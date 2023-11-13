#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX < 0x030c0000
#undef _PyGC_FINALIZED
#endif

#include <fcntl.h>
#include <fstream>

#include "echion/config.h"
#include "echion/interp.h"
#include "echion/signals.h"
#include "echion/stacks.h"
#include "echion/state.h"
#include "echion/threads.h"
#include "echion/timing.h"

#include "interface.hpp"

class StackRenderer : public RendererInterface {
  void render_message(std::string_view msg) override {
  }

  void render_stack_begin() override {
    ddup_start_sample(512); // TODO magic numbers
  }

  void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override {
    ddup_push_frame(name.data(), file.data(), 0, line);
  }

  void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override {
    ddup_push_frame(name.data(), file.data(), 0, line);
  }

  void render_cpu_time(uint64_t cpu_time) override {
    ddup_push_cputime(cpu_time, 1);
  }

  void render_stack_end() override {
    ddup_flush_sample();
  }

  bool is_valid() override { return true; }
};

void _stack_sampler_v2() {

  auto last_time = gettime();
  while (true) {
      auto now = gettime();
      auto end_time = now + interval; // TODO interval is set in echion, just take it for now
      auto wall_time = now - last_time;
      for_each_interp(
          [=](PyInterpreterState *interp) -> void {
              for_each_thread(interp,
                  [=](PyThreadState *tstate, ThreadInfo &thread) {
                      thread.sample(interp->id, tstate, wall_time);
                  });
          });

      while (now < end_time) {
          auto sleep_duration = std::chrono::microseconds(end_time - now);
          std::this_thread::sleep_for(sleep_duration);
          now = gettime();
      }
      last_time = now;
  }
}

void stack_sampler_v2() {
    // TODO lifetime?
    std::thread(_stack_sampler_v2).detach();
}

static PyObject *start(PyObject* self, PyObject* args) {
  // Sets the renderer and then schedules a native thread to run the sampler
  Renderer::get().set_renderer(std::make_shared<StackRenderer>());

  Py_BEGIN_ALLOW_THREADS;
  stack_sampler_v2();
  Py_END_ALLOW_THREADS;
  return PyLong_FromLong(1);
}

static PyMethodDef _stack_v2_methods[] = {
    {"start", (PyCFunction)start, METH_VARARGS, "Start the sampler"},
    {NULL, NULL, 0, NULL}};

PyMODINIT_FUNC PyInit__stack_v2(void) {
  PyObject *m;
  static struct PyModuleDef moduledef = {
      PyModuleDef_HEAD_INIT, "_stack_v2", NULL, -1, _stack_v2_methods, NULL,
      NULL, NULL, NULL};

  m = PyModule_Create(&moduledef);
  if (!m)
    return NULL;

  return m;
}
