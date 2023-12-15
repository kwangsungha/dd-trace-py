#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX < 0x030c0000
#undef _PyGC_FINALIZED
#endif

#include <atomic>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <vector>
#include <chrono>
#include <thread>
#include <iostream>

#include "echion/config.h"
#include "echion/interp.h"
#include "echion/signals.h"
#include "echion/stacks.h"
#include "echion/state.h"
#include "echion/threads.h"
#include "echion/timing.h"

struct DDFrame
{
    std::string filename;
    std::string name;
    uint64_t line;

    DDFrame(std::string_view filename, std::string_view name, uint64_t line)
      : filename{ std::string(filename) }
      , name{ std::string(name) }
      , line{ line }
    {
    }
};

using StackTrace = std::vector<DDFrame>;

struct StackSampleEvent
{
    unsigned long thread_id;
    unsigned long native_id;
    std::string thread_name;
    microsecond_t cpu_time;
    microsecond_t wall_time;
    unsigned long task_id;
    std::string task_name;
    StackTrace frames;
};

struct CachedThread
{
    std::string name;
    microsecond_t wall_time;
    microsecond_t cpu_time;
    uintptr_t thread_id;
    unsigned long native_id;

    void set(std::string_view name, microsecond_t cpu_time, microsecond_t wall_time, uintptr_t thread_id, unsigned long native_id)
    {
        this->name = name;
        this->cpu_time = cpu_time;
        this->wall_time = wall_time;
        this->thread_id = thread_id;
        this->native_id = native_id;
    }
};

class StackRenderer : public RendererInterface
{
    StackSampleEvent current_event;
    std::array<std::vector<StackSampleEvent>, 2> event_buffers;
    std::vector<StackSampleEvent>* events_in = &event_buffers[0]; // input buffer
    std::vector<StackSampleEvent>* events_out = &event_buffers[1]; // output buffer
    CachedThread current_thread;
    static PyObject* stack_sample_event_type;
    static PyObject* ddframe_type;

    void render_message(std::string_view msg) override { (void)msg; }

    void render_thread_begin(PyThreadState *tstate,
                             std::string_view name,
                             microsecond_t wall_time,
                             uintptr_t thread_id,
                             unsigned long native_id) override
    {
        current_thread.set(name, 0, wall_time, thread_id, native_id);
    }

    void render_stack_begin() override { current_event.frames.clear(); }

    void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        current_event.frames.emplace_back(file, name, line);
    }

    void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        current_event.frames.emplace_back(file, name, line);
    }

    void render_cpu_time(uint64_t cpu_time) override {
      // TODO weird that we have an unused cpu_time in the CachedThread now
      current_event.cpu_time = cpu_time;
    }

    void render_stack_end() override
    {
        current_event.thread_id = current_thread.thread_id;
        current_event.native_id = current_thread.native_id;
        current_event.thread_name = current_thread.name;
        current_event.wall_time = current_thread.wall_time;

        events_in->emplace_back(std::move(current_event));

        current_event = StackSampleEvent();
    }

    bool is_valid() override { return true; }

  public:
    PyObject* pop()
    {
        std::vector<StackSampleEvent> &events = *events_out;
        if (events.empty()) {
            Py_INCREF(Py_None);
            return Py_None;
        }
        if (!stack_sample_event_type) {
            set_type();
        }

        auto event = std::move(events.back());
        events.pop_back();

        PyObject* py_event = PyObject_CallObject(stack_sample_event_type, NULL);
        if (py_event == NULL) {
            std::cout << "Failed to create event" << std::endl;
            PyErr_Print();
            return NULL;
        }

        // Helper function to safely set attribute and check for errors
        auto safe_set_attr = [&](const char* attr_name, PyObject* value) {
            if (PyObject_SetAttrString(py_event, attr_name, value) < 0) {
                PyErr_Print();
                Py_XDECREF(value);
                Py_XDECREF(py_event);
                return false;
            }
            Py_XDECREF(value);
            return true;
        };

        // Set the scalar attributes of the event
        // NB the times are in microseconds, so we multiply by 1000
        if (!safe_set_attr("thread_id", PyLong_FromUnsignedLong(event.thread_id)) ||
            !safe_set_attr("thread_name", PyUnicode_FromString(event.thread_name.c_str())) ||
            !safe_set_attr("thread_native_id", PyLong_FromUnsignedLong(event.native_id)) ||
            !safe_set_attr("wall_time_ns", PyLong_FromUnsignedLong(1000*event.wall_time)) ||
            !safe_set_attr("cpu_time_ns", PyLong_FromUnsignedLong(1000*event.cpu_time)) ||
            !safe_set_attr("sampling_period", PyLong_FromUnsignedLong(1000)) ||
            !safe_set_attr("nframes", PyLong_FromUnsignedLong(event.frames.size()))) {
            std::cout << "Failed to populate scalar attributes" << std::endl;
            return NULL;
        }

        PyObject* py_frames = PyList_New(0);
        if (py_frames == NULL) {
            PyErr_Print();
            Py_XDECREF(py_event);
            return NULL;
        }

        for (auto &frame : event.frames) {
            PyObject *py_frame = PyObject_CallFunction(ddframe_type, "sLss", frame.filename.c_str(), static_cast<long>(frame.line),
                                                       frame.name.c_str(), "");

            if (py_frame == NULL) {
                PyErr_Print();
                Py_XDECREF(py_frame);
                Py_XDECREF(py_frames);
                break;
            }

            if (PyList_Append(py_frames, py_frame) < 0) {
                PyErr_Print();
                Py_XDECREF(py_frame);
                Py_XDECREF(py_frames);
                // TODO manage nframes properly
                break;
            }

            Py_XDECREF(py_frame);
        }

        // If the number of frames we got is different than the number we expected, we have an error
        // TODO this is maybe more conservative than necessary; it wouldn't be bad to
        // add a virtual frame indicating the situation
        if (PyList_Size(py_frames) != event.frames.size()) {
            PyErr_Print();
            Py_XDECREF(py_frames);
            Py_XDECREF(py_event);
            Py_INCREF(Py_None);
            return Py_None;
        }

        // Otherwise, set the frames attribute
        if (PyObject_SetAttrString(py_event, "frames", py_frames) < 0) {
            PyErr_Print();
            Py_XDECREF(py_frames);
            Py_XDECREF(py_event);
            return NULL;
        }

        Py_XDECREF(py_frames);
        return py_event;
    }

    void
    set_type()
    {
        PyObject* mod_name = PyUnicode_FromString("ddtrace.profiling.event");
        PyObject* mod = PyImport_Import(mod_name);
        Py_XDECREF(mod_name);
        if (mod == NULL) {
            PyErr_Print();
            exit(1);
        }
        ddframe_type = PyObject_GetAttrString(mod, "DDFrame");
        Py_XDECREF(mod);

        mod_name = PyUnicode_FromString("ddtrace.profiling.collector.stack_event");
        mod = PyImport_Import(mod_name);
        Py_XDECREF(mod_name);
        if (mod == NULL) {
            PyErr_Print();
            exit(1);
        }
        stack_sample_event_type = PyObject_GetAttrString(mod, "StackSampleEvent");
        Py_XDECREF(mod);

        // Check for errors
        if (stack_sample_event_type == NULL) {
            PyErr_Print();
            exit(1);
        }
        if (ddframe_type == NULL) {
            PyErr_Print();
            exit(1);
        }
    }

    void
    swap_buffers()
    {
        std::swap(events_in, events_out);
    }

    void normalize_cpu_time() {
      // Goes through the cached threads and counts the number of running threads (nonzero cpu time)
      // then normalizes those times by the number of running threads
      auto num_running_threads = std::count_if(events_in->begin(), events_in->end(), [](const StackSampleEvent& event) {
        return event.thread_id != 0;
      });
    }
};

// Initialize static members
PyObject* StackRenderer::stack_sample_event_type = nullptr;
PyObject* StackRenderer::ddframe_type = nullptr;


// Accepts fractional seconds, saves variable as integral us
std::atomic<unsigned long> sample_interval = 10000; // in us
static void
_set_v2_interval(double new_interval)
{
  unsigned int new_interval_us = static_cast<unsigned int>(new_interval*1e6);
  sample_interval.store(new_interval_us);
}

void
_stack_sampler_v2()
{
    using namespace std::chrono;
    unsigned long samples = 0;
    auto last_time = steady_clock::now();

    while (true) {
        auto now = steady_clock::now();
        auto wall_time = duration_cast<microseconds>(now - last_time).count();
        last_time = now;

        // Perform the sample
        int num_threads = 0;
        int num_interps = 0;
        for_each_interp([&](PyInterpreterState* interp) -> void {
            num_interps++;
            for_each_thread(
              interp, [&](PyThreadState* tstate, ThreadInfo& thread) {
                num_threads++;
                thread.sample(interp->id, tstate, wall_time);
              });
        });

        // Sleep for the remainder of the interval, get it atomically
        std::this_thread::sleep_until(now + microseconds(sample_interval.load()));
    }
}

void
stack_sampler_v2()
{
    // TODO lifetime?
    std::thread(_stack_sampler_v2).detach();
}

void make_it_abort()
{
    std::abort();
}

// All interfaces use the same global instance of the renderer, since
// it keeps persistent state
std::shared_ptr<StackRenderer> _renderer = std::make_shared<StackRenderer>();

static PyObject*
start(PyObject* self, PyObject* args, PyObject* kwargs)
{
    static char *kwlist[] = {"min_interval", "max_frames", NULL};
    double min_interval_f = 0.010; // Default 10ms period (100hz)
    double max_frames_f = 128;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|dd", kwlist,
                                     &min_interval_f, &max_frames_f)) {
        return NULL; // If an error occurs during argument parsing
    }

    // Set options
    _set_v2_interval(min_interval_f); // fractional seconds to us
    _set_cpu(true);                   // enable CPU profiling in echion
    init_frame_cache(1024);           // TODO don't hardcode this?

    _set_pid(getpid()); // TODO follow forks
    Renderer::get().set_renderer(_renderer);

    Py_BEGIN_ALLOW_THREADS;
    stack_sampler_v2();
    Py_END_ALLOW_THREADS;

    // DEBUGGING:  ensure uncaught exceptions dump core
    std::set_terminate(make_it_abort);
    return PyLong_FromLong(1);
}

static PyObject*
collect(PyObject* self, PyObject* args)
{
    return _renderer->pop();
}

static PyObject*
swap_buffers(PyObject* self, PyObject* args)
{
    _renderer->swap_buffers();
    Py_INCREF(Py_None);
    return Py_None;
}

// This is a function for using std::cout to print a number passed from Python,
// except it prepends the given string
static PyObject*
print_number(PyObject* self, PyObject* args)
{
    int num;
    char* str;
    if (!PyArg_ParseTuple(args, "si", &str, &num)) {
        return NULL; // If an error occurs during argument parsing
    }
    std::cout << str << ": " << num << std::endl;
    return PyLong_FromLong(1);
}

static PyObject*
set_v2_interval(PyObject* self, PyObject* args)
{
    double new_interval;
    if (!PyArg_ParseTuple(args, "d", &new_interval)) {
        return NULL; // If an error occurs during argument parsing
    }
    _set_v2_interval(new_interval);
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef _stack_v2_methods[] = { { "start", (PyCFunction)start, METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
                                           { "collect", (PyCFunction)collect, METH_VARARGS, "Get an event" },
                                           { "print_number", (PyCFunction)print_number, METH_VARARGS, "Print a number" },
                                           { "set_interval", (PyCFunction)set_v2_interval, METH_VARARGS, "Set the sampling interval" },
                                           { "swap_buffers", (PyCFunction)swap_buffers, METH_VARARGS, "Swap buffers" },
                                           { NULL, NULL, 0, NULL } };

PyMODINIT_FUNC
PyInit_stack_v2(void)
{
    PyObject* m;
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT, "_stack_v2", NULL, -1, _stack_v2_methods, NULL, NULL, NULL, NULL
    };

    m = PyModule_Create(&moduledef);
    if (!m)
        return NULL;

    return m;
}
