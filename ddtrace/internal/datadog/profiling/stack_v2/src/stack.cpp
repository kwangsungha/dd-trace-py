#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX < 0x030c0000
#undef _PyGC_FINALIZED
#endif

#include <atomic>
#include <chrono>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

#include "echion/config.h"
#include "echion/interp.h"
#include "echion/signals.h"
#include "echion/stacks.h"
#include "echion/state.h"
#include "echion/threads.h"
#include "echion/timing.h"

#include "interface.hpp"

class StackRenderer : public RendererInterface
{
    static PyObject* stack_sample_event_type;
    static PyObject* ddframe_type;
    void render_message(std::string_view msg) override { (void)msg; }

    void render_thread_begin(PyThreadState* tstate,
                             std::string_view name,
                             microsecond_t wall_time,
                             uintptr_t thread_id,
                             unsigned long native_id) override
    {
        ddup_push_threadinfo(static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name.data());
        ddup_push_walltime(wall_time, 1);
    }

    void render_stack_begin() override
    {
        ddup_start_sample(512); // TODO magic number
    }

    void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        ddup_push_frame(name.data(), file.data(), 0, line);
    }

    void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        ddup_push_frame(name.data(), file.data(), 0, line);
    }

    void render_cpu_time(uint64_t cpu_time) override { ddup_push_cputime(cpu_time, 1); }

    void render_stack_end() override { ddup_flush_sample(); }

    bool is_valid() override { return true; }

  public:
    void set_type()
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
};

// Initialize static members
PyObject* StackRenderer::stack_sample_event_type = nullptr;
PyObject* StackRenderer::ddframe_type = nullptr;

// Accepts fractional seconds, saves variable as integral us
std::atomic<unsigned long> sample_interval = 10000; // in us
static void
_set_v2_interval(double new_interval)
{
    unsigned int new_interval_us = static_cast<unsigned int>(new_interval * 1e6);
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
            for_each_thread(interp, [&](PyThreadState* tstate, ThreadInfo& thread) {
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

void
make_it_abort()
{
    std::abort();
}

// All interfaces use the same global instance of the renderer, since
// it keeps persistent state
std::shared_ptr<StackRenderer> _renderer = std::make_shared<StackRenderer>();

static PyObject*
start_stack_v2(PyObject* self, PyObject* args, PyObject* kwargs)
{
    static char* kwlist[] = { "min_interval", "max_frames", NULL };
    double min_interval_f = 0.010; // Default 10ms period (100hz)
    double max_frames_f = 128;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|dd", kwlist, &min_interval_f, &max_frames_f)) {
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

static PyMethodDef _stack_v2_methods[] = {
    { "start", (PyCFunction)start_stack_v2, METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "print_number", (PyCFunction)print_number, METH_VARARGS, "Print a number" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC
PyInit__stack_v2(void)
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
