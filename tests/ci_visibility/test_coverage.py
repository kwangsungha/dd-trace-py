#!/usr/bin/env python3
from ast import literal_eval
from os import getcwd

import pytest

from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.coverage import Coverage
from ddtrace.internal.ci_visibility.coverage import _coverage_end
from ddtrace.internal.ci_visibility.coverage import _coverage_start
from ddtrace.internal.ci_visibility.coverage import _initialize
from ddtrace.internal.ci_visibility.coverage import segments
from ddtrace.internal.ci_visibility.recorder import CITracer


EXPECTED_COVERED_FILES = (
    "ddtrace/internal/module.py",
    "ddtrace/contrib/pytest/plugin.py",
    "ddtrace/span.py",
    "ddtrace/tracer.py",
    "ddtrace/provider.py",
    "ddtrace/_hooks.py",
    "ddtrace/internal/processor/trace.py",
    "ddtrace/internal/processor/endpoint_call_counter.py",
    "ddtrace/internal/ci_visibility/recorder.py",
    "ddtrace/internal/service.py",
    "ddtrace/internal/processor/__init__.py",
    "ddtrace/internal/telemetry/writer.py",
    "ddtrace/internal/periodic.py",
    "ddtrace/internal/forksafe.py",
    "ddtrace/internal/telemetry/metrics_namespaces.py",
    "ddtrace/internal/telemetry/metrics.py",
    "ddtrace/internal/runtime/__init__.py",
    "ddtrace/internal/telemetry/data.py",
    "ddtrace/internal/utils/cache.py",
    "ddtrace/internal/hostname.py",
    "ddtrace/internal/runtime/container.py",
    "ddtrace/internal/utils/time.py",
    "ddtrace/internal/utils/http.py",
    "ddtrace/internal/http.py",
    "ddtrace/internal/compat.py",
    "ddtrace/internal/ci_visibility/writer.py",
    "ddtrace/internal/ci_visibility/coverage.py",
)


@pytest.mark.parametrize(
    "lines,expected_segments",
    [
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 10, 0, -1)]),
        ([1, 2, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 2, 0, -1), (4, 0, 10, 0, -1)]),
        ([1, 3, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 1, 0, -1), (3, 0, 10, 0, -1)]),
        ([1, 2, 3, 4, 5, 6, 7, 8, 10], [(1, 0, 8, 0, -1), (10, 0, 10, 0, -1)]),
        ([1, 2, 3, 4, 10, 5, 6, 7, 8], [(1, 0, 8, 0, -1), (10, 0, 10, 0, -1)]),
    ],
)
def test_segments(lines, expected_segments):
    assert segments(lines) == expected_segments


@pytest.mark.skipif(Coverage is None, reason="Coverage not available")
def test_cover():
    tracer = CITracer()
    span = tracer.start_span("cover_span")

    _initialize(getcwd())
    _coverage_start()
    pytest.main(["tests/utils.py"])
    _coverage_end(span)

    res = literal_eval(span.get_tag(COVERAGE_TAG_NAME))

    assert "files" in res
    assert len(res["files"]) >= 27

    covered_files = [x["filename"] for x in res["files"]]
    for filename in EXPECTED_COVERED_FILES:
        assert filename in covered_files
