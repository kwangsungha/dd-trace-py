# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._taint_tracking import OriginType
from ddtrace.appsec.iast._taint_tracking import Source


def setup():
    oce._enabled = True


@pytest.mark.parametrize(
    "obj, kwargs",
    [
        (3.5, {}),
        ("Hi", {}),
        ("🙀", {}),
        (b"Hi", {}),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}),
        ({"a": "b", "c": "d"}, {}),
        ({"a", "b", "c", "d"}, {}),
        (("a", "b", "c", "d"), {}),
        (["a", "b", "c", "d"], {}),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_str_aspect(obj, kwargs):
    import ddtrace.appsec.iast._taint_tracking.aspects as ddtrace_aspects

    assert ddtrace_aspects.str_aspect(obj, **kwargs) == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, kwargs, should_be_tainted",
    [
        (3.5, {}, False),
        ("Hi", {}, True),
        ("🙀", {}, True),
        (b"Hi", {}, True),
        (bytearray(b"Hi"), {}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}, True),
        ({"a": "b", "c": "d"}, {}, False),
        ({"a", "b", "c", "d"}, {}, False),
        (("a", "b", "c", "d"), {}, False),
        (["a", "b", "c", "d"], {}, False),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_str_aspect_tainting(obj, kwargs, should_be_tainted):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
    import ddtrace.appsec.iast._taint_tracking.aspects as ddtrace_aspects

    setup(bytes.join, bytearray.join)
    clear_taint_mapping()
    if should_be_tainted:
        obj = taint_pyobject(obj, Source("test_str_aspect_tainting", repr(obj), OriginType.PARAMETER))

    result = ddtrace_aspects.str_aspect(obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)
