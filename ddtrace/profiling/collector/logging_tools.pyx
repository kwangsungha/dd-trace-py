# distutils: extra_compile_args = -std=c11


from cpython cimport PyObject, PyBytes_AsString, PyBytes_FromString

cdef extern from "stdio.h":
    ctypedef struct FILE
    int fprintf(FILE* stream, const char* format, ...)
    int fflush(FILE* stream)
    FILE *stdout
    FILE *stderr

cdef extern from "stdatomic.h":
    unsigned long atomic_fetch_add_explicit(unsigned long* atom, unsigned long val, int memorder) nogil

# Define the memory orders
cdef enum memory_order:
    memory_order_relaxed

cdef unsigned long display_counter = 0
cdef inline void _display_str(char* s):
    cdef unsigned long current = atomic_fetch_add_explicit(&display_counter, 1, memory_order_relaxed)
    fprintf(stderr, "{%lu}%s\n", current, s)
    fflush(stderr)

def display_str(str p_s):
    cdef bytes p_b = p_s.encode('utf-8')
    cdef char* c_s = PyBytes_AsString(p_b)
    _display_str(c_s)
