import ctypes
import ddtrace.internal.datadog.profiling._ddup as ddup

ddup.init_crashtracker()
ctypes.string_at(0)
