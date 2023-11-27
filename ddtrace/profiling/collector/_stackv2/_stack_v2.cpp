#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX < 0x030c0000
#undef _PyGC_FINALIZED
#endif

#include <fcntl.h>
#include <fstream>
#include <queue>

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

struct StackBasedEvent
{
    unsigned long thread_id;
    unsigned long native_id;
    std::string thread_name;
    unsigned long task_id;
    std::string task_name;
    StackTrace frames;
};

struct CachedThread
{
    std::string name;
    microsecond_t cpu_time;
    uintptr_t thread_id;
    unsigned long native_id;

    void set(std::string_view name, microsecond_t cpu_time, uintptr_t thread_id, unsigned long native_id)
    {
        this->name = name;
        this->cpu_time = cpu_time;
        this->thread_id = thread_id;
        this->native_id = native_id;
    }
};

class StackRenderer : public RendererInterface
{
    StackBasedEvent current_event;
    std::queue<StackBasedEvent> events;
    CachedThread current_thread;
    static PyObject* stack_based_event_type;
    static PyObject* ddframe_type;

    void render_message(std::string_view msg) override { (void)msg; }

    void render_thread_begin(std::string_view name,
                             microsecond_t cpu_time,
                             uintptr_t thread_id,
                             unsigned long native_id) override
    {
        current_thread.set(name, cpu_time, thread_id, native_id);
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

    void render_cpu_time(uint64_t cpu_time) override { current_thread.cpu_time = cpu_time; }

    void render_stack_end() override
    {
        current_event.thread_id = current_thread.thread_id;
        current_event.native_id = current_thread.native_id;
        current_event.thread_name = current_thread.name;

        // std::move current event into the queue
        events.push(std::move(current_event));
        current_event = StackBasedEvent();
    }

    bool is_valid() override { return true; }

  public:
    PyObject* pop()
    {
        std::cout << "Popping (internal)" << std::endl;
        if (events.empty()) {
            std::cout << "No events" << std::endl;
            Py_INCREF(Py_None);
            return Py_None;
        }
        if (!stack_based_event_type) {
            set_type();
        }

        auto event = events.front();
        events.pop();

        PyObject* py_event = PyObject_CallObject(stack_based_event_type, NULL);
        if (py_event == NULL) {
            std::cout << "Failed to create event" << std::endl;
            PyErr_Print();
            return NULL;
        }

        // Helper function to safely set attribute and check for errors
        auto safe_set_attr = [&](const char* attr_name, PyObject* value) {
            if (PyObject_SetAttrString(py_event, attr_name, value) < 0) {
                PyErr_Print();
                Py_DECREF(value);
                Py_DECREF(py_event);
                return false;
            }
            Py_DECREF(value);
            return true;
        };

        // Set the scalar attributes of the event
        if (!safe_set_attr("thread_id", PyLong_FromUnsignedLong(event.thread_id)) ||
            !safe_set_attr("thread_name", PyUnicode_FromString(event.thread_name.c_str())) ||
            !safe_set_attr("thread_native_id", PyLong_FromUnsignedLong(event.native_id)) ||
            !safe_set_attr("cpu_time", PyLong_FromUnsignedLong(current_thread.cpu_time)) ||
            !safe_set_attr("nframes", PyLong_FromUnsignedLong(event.frames.size()))) {
            std::cout << "Failed to populate scalar attributes" << std::endl;
            return NULL;
        }

        PyObject* frames = PyList_New(event.frames.size());
        if (frames == NULL) {
            PyErr_Print();
            Py_DECREF(py_event);
            return NULL;
        }

        for (size_t i = 0; i < event.frames.size(); i++) {
            auto& frame = event.frames[i];
            PyObject* py_frame = PyObject_CallObject(ddframe_type, NULL);
            if (py_frame == NULL) {
                PyErr_Print();
                Py_DECREF(frames);
                Py_DECREF(py_event);
                return NULL;
            }
            // Similar attribute setting for py_frame
            PyList_SetItem(frames, i, py_frame); // PyList_SetItem steals the reference
        }

        if (PyObject_SetAttrString(py_event, "frames", frames) < 0) {
            PyErr_Print();
            Py_DECREF(frames);
            Py_DECREF(py_event);
            return NULL;
        }

        Py_DECREF(frames);
        return py_event;
    }

    void set_type()
    {
        PyObject* mod_name = PyUnicode_FromString("ddtrace.profiling.event");
        PyObject* mod = PyImport_Import(mod_name);
        Py_DECREF(mod_name);
        if (mod == NULL) {
            PyErr_Print();
            exit(1);
        }

        stack_based_event_type = PyObject_GetAttrString(mod, "StackBasedEvent");

        // Now get the DDFrame
        ddframe_type = PyObject_GetAttrString(mod, "DDFrame");
        Py_DECREF(mod);
        if (stack_based_event_type == NULL) {
            PyErr_Print();
            exit(1);
        }
        if (ddframe_type == NULL) {
            PyErr_Print();
            exit(1);
        }
    }
};

// Initialize static members
PyObject* StackRenderer::stack_based_event_type = nullptr;
PyObject* StackRenderer::ddframe_type = nullptr;

void
_stack_sampler_v2()
{

    auto last_time = gettime();
    while (true) {
        auto now = gettime();
        auto end_time = now + interval; // TODO interval is set in echion, just take it for now
        auto wall_time = now - last_time;
        for_each_interp([=](PyInterpreterState* interp) -> void {
            for_each_thread(
              interp, [=](PyThreadState* tstate, ThreadInfo& thread) { thread.sample(interp->id, tstate, wall_time); });
        });

        while (now < end_time) {
            auto sleep_duration = std::chrono::microseconds(end_time - now);
            std::this_thread::sleep_for(sleep_duration);
            now = gettime();
        }
        last_time = now;
    }
}

void
stack_sampler_v2()
{
    // TODO lifetime?
    std::thread(_stack_sampler_v2).detach();
}

// All interfaces use the same global instance of the renderer, since
// it keeps persistent state
std::shared_ptr<StackRenderer> _renderer = std::make_shared<StackRenderer>();

static PyObject*
start(PyObject* self, PyObject* args)
{
    std::cout << "Starting stack sampler" << std::endl;
    Renderer::get().set_renderer(_renderer);

    Py_BEGIN_ALLOW_THREADS;
    stack_sampler_v2();
    Py_END_ALLOW_THREADS;
    return PyLong_FromLong(1);
}

static PyObject*
collect(PyObject* self, PyObject* args)
{
    std::cout << "Popping (external)" << std::endl;
    return _renderer->pop();
}

static PyMethodDef _stack_v2_methods[] = { { "start", (PyCFunction)start, METH_VARARGS, "Start the sampler" },
                                           { "collect", (PyCFunction)collect, METH_VARARGS, "Get an event" },
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
