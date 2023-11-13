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
  }

  void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override {
  }

  void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override {
  }

  void render_cpu_time(uint64_t cpu_time) override {
  }

  void render_stack_end() override {
  }

  bool is_valid() override { return true; }
};

static PyObject *start(PyObject* self, PyObject* args) {
  Renderer::get().setRenderer(std::make_shared<StackRenderer>());
  // Return true
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
