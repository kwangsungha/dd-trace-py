"""
This custom pytest plugin implements tracing for pytest by using pytest hooks. The plugin registers tracing code
to be run at specific points during pytest execution. The most important hooks used are:

    * pytest_sessionstart: during pytest session startup, a custom trace filter is configured to the global tracer to
        only send test spans, which are generated by the plugin.
    * pytest_runtest_protocol: this wraps around the execution of a pytest test function, which we trace. Most span
        tags are generated and added in this function. We also store the span on the underlying pytest test item to
        retrieve later when we need to report test status/result.
    * pytest_runtest_makereport: this hook is used to set the test status/result tag, including skipped tests and
        expected failures.

"""
from doctest import DocTest
import json
import os
import re
from typing import Dict

from _pytest.nodes import get_fslocation_from_item
import pytest

import ddtrace
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import HELP_MSG
from ddtrace.contrib.pytest.constants import KIND
from ddtrace.contrib.pytest.constants import XFAIL_REASON
from ddtrace.contrib.unittest import unpatch as unpatch_unittest
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE as _EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import ITR_UNSKIPPABLE_REASON
from ddtrace.internal.ci_visibility.constants import MODULE_ID as _MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE as _MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import SESSION_ID as _SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE as _SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import SUITE_ID as _SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE as _SUITE_TYPE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.coverage import _initialize_coverage
from ddtrace.internal.ci_visibility.coverage import build_payload as build_coverage_payload
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger


PATCH_ALL_HELP_MSG = "Call ddtrace.patch_all before running tests."

log = get_logger(__name__)

_global_skipped_elements = 0


def encode_test_parameter(parameter):
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def is_enabled(config):
    """Check if the ddtrace plugin is enabled."""
    return (config.getoption("ddtrace") or config.getini("ddtrace")) and not config.getoption("no-ddtrace")


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `pytest.Item` instance."""
    item._datadog_span = span


def _attach_coverage(item):
    coverage = _initialize_coverage(str(item.config.rootdir))
    item._coverage = coverage
    coverage.start()


def _detach_coverage(item, span):
    if not hasattr(item, "_coverage"):
        log.warning("No coverage object found for item")
        return
    span_id = str(span.trace_id)
    item._coverage.stop()
    if not item._coverage._collector or len(item._coverage._collector.data) == 0:
        log.warning("No coverage collector or data found for item")
    span.set_tag(COVERAGE_TAG_NAME, build_coverage_payload(item._coverage, item.config.rootdir, test_id=span_id))
    item._coverage.erase()
    del item._coverage


def _extract_module_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span_module", None)


def _extract_ancestor_module_span(item):
    """Return the first ancestor module span found"""
    while item:
        module_span = _extract_module_span(item) or _extract_span(item)
        if module_span is not None and module_span.name == "pytest.test_module":
            return module_span
        item = item.parent


def _store_module_span(item, span):
    """Store span at `pytest.Item` instance."""
    item._datadog_span_module = span


def _mark_failed(item):
    """Store test failed status at `pytest.Item` instance."""
    if item.parent:
        _mark_failed(item.parent)
    item._failed = True


def _check_failed(item):
    """Extract test failed status from `pytest.Item` instance."""
    return getattr(item, "_failed", False)


def _mark_not_skipped(item):
    """Mark test suite/module/session `pytest.Item` as not skipped."""
    if item.parent:
        _mark_not_skipped(item.parent)
    item._fully_skipped = False


def _mark_test_forced(test_item):
    # type: (pytest.Test) -> None
    test_span = _extract_span(test_item)
    test_span.set_tag_str(test.ITR_FORCED_RUN, "true")

    suite_span = _extract_span(test_item.parent)
    suite_span.set_tag_str(test.ITR_FORCED_RUN, "true")

    module_span = _extract_ancestor_module_span(test_item)
    module_span.set_tag_str(test.ITR_FORCED_RUN, "true")

    session_span = _extract_span(test_item.session)
    session_span.set_tag_str(test.ITR_FORCED_RUN, "true")


def _mark_test_unskippable(test_item):
    # type: (pytest.Test) -> None
    test_span = _extract_span(test_item)
    test_span.set_tag_str(test.ITR_UNSKIPPABLE, "true")

    suite_span = _extract_span(test_item.parent)
    suite_span.set_tag_str(test.ITR_UNSKIPPABLE, "true")

    module_span = _extract_ancestor_module_span(test_item)
    module_span.set_tag_str(test.ITR_UNSKIPPABLE, "true")

    session_span = _extract_span(test_item.session)
    session_span.set_tag_str(test.ITR_UNSKIPPABLE, "true")


def _check_fully_skipped(item):
    """Check if test suite/module/session `pytest.Item` has `_fully_skipped` marker."""
    return getattr(item, "_fully_skipped", True)


def _mark_test_status(item, span):
    """
    Given a `pytest.Item`, determine and set the test status of the corresponding span.
    """
    # If any child has failed, mark span as failed.
    if _check_failed(item):
        status = test.Status.FAIL.value
        if item.parent:
            _mark_failed(item.parent)
            _mark_not_skipped(item.parent)
    # If all children have been skipped, mark span as skipped.
    elif _check_fully_skipped(item):
        status = test.Status.SKIP.value
    else:
        status = test.Status.PASS.value
        if item.parent:
            _mark_not_skipped(item.parent)
    span.set_tag_str(test.STATUS, status)


def _extract_reason(call):
    if call.excinfo is not None:
        return call.excinfo.value


def _get_pytest_command(config):
    """Extract and re-create pytest session command from pytest config."""
    command = "pytest"
    if getattr(config, "invocation_params", None):
        command += " {}".format(" ".join(config.invocation_params.args))
    return command


def _get_module_path(item):
    """Extract module path from a `pytest.Item` instance."""
    if not isinstance(item, (pytest.Package, pytest.Module)):
        return None
    return item.nodeid.rpartition("/")[0]


def _get_module_name(item, is_package=True):
    """Extract module name (fully qualified) from a `pytest.Item` instance."""
    if is_package:
        return item.module.__name__
    return item.nodeid.rpartition("/")[0].replace("/", ".")


def _get_suite_name(item, test_module_path=None):
    """
    Extract suite name from a `pytest.Item` instance.
    If the module path doesn't exist, the suite path will be reported in full.
    """
    if test_module_path:
        if not item.nodeid.startswith(test_module_path):
            log.warning("Suite path is not under module path: '%s' '%s'", item.nodeid, test_module_path)
        suite_path = os.path.relpath(item.nodeid, start=test_module_path)
        return suite_path
    return item.nodeid


def _is_test_unskippable(item):
    return any(
        [
            True
            for marker in item.iter_markers(name="skipif")
            if marker.args[0] is False
            and "reason" in marker.kwargs
            and marker.kwargs["reason"] is ITR_UNSKIPPABLE_REASON
        ]
    )


def _start_test_module_span(pytest_package_item=None, pytest_module_item=None):
    """
    Starts a test module span at the start of a new pytest test package.
    Note that ``item`` is a ``pytest.Package`` object referencing the test module being run.
    """
    is_package = True
    item = pytest_package_item

    if pytest_package_item is None and pytest_module_item is not None:
        item = pytest_module_item
        is_package = False

    test_session_span = _extract_span(item.session)
    test_module_span = _CIVisibility._instance.tracer._start_span(
        "pytest.test_module",
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=test_session_span,
    )
    test_module_span.set_tag_str(COMPONENT, "pytest")
    test_module_span.set_tag_str(SPAN_KIND, KIND)
    test_module_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_module_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
    test_module_span.set_tag_str(test.COMMAND, _get_pytest_command(item.config))
    test_module_span.set_tag_str(_EVENT_TYPE, _MODULE_TYPE)
    if test_session_span:
        test_module_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
    test_module_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
    test_module_span.set_tag_str(test.MODULE, _get_module_name(item, is_package))
    test_module_span.set_tag_str(test.MODULE_PATH, _get_module_path(item))
    if is_package:
        _store_span(item, test_module_span)
    else:
        _store_module_span(item, test_module_span)

    test_module_span.set_tag_str(
        test.ITR_TEST_CODE_COVERAGE_ENABLED,
        "true" if _CIVisibility._instance._collect_coverage_enabled else "false",
    )

    if _CIVisibility.test_skipping_enabled():
        test_module_span.set_tag_str(test.ITR_TEST_SKIPPING_ENABLED, "true")
        test_module_span.set_tag(
            test.ITR_TEST_SKIPPING_TYPE, SUITE if _CIVisibility._instance._suite_skipping_mode else TEST
        )
        test_module_span.set_tag_str(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "false")
        test_module_span.set_tag_str(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "false")
        test_module_span.set_tag_str(test.ITR_FORCED_RUN, "false")
        test_module_span.set_tag_str(test.ITR_UNSKIPPABLE, "false")
    else:
        test_module_span.set_tag(test.ITR_TEST_SKIPPING_ENABLED, "false")

    return test_module_span, is_package


def _start_test_suite_span(item, test_module_span, should_enable_coverage=False):
    """
    Starts a test suite span at the start of a new pytest test module.
    Note that ``item`` is a ``pytest.Module`` object referencing the test file being run.
    """
    test_session_span = _extract_span(item.session)
    if test_module_span is None and isinstance(item.parent, pytest.Package):
        test_module_span = _extract_span(item.parent)
    parent_span = test_module_span
    if parent_span is None:
        parent_span = test_session_span

    test_suite_span = _CIVisibility._instance.tracer._start_span(
        "pytest.test_suite",
        service=_CIVisibility._instance._service,
        span_type=SpanTypes.TEST,
        activate=True,
        child_of=parent_span,
    )
    test_suite_span.set_tag_str(COMPONENT, "pytest")
    test_suite_span.set_tag_str(SPAN_KIND, KIND)
    test_suite_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
    test_suite_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
    test_suite_span.set_tag_str(test.COMMAND, _get_pytest_command(item.config))
    test_suite_span.set_tag_str(_EVENT_TYPE, _SUITE_TYPE)
    if test_session_span:
        test_suite_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
    test_suite_span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
    test_module_path = None
    if test_module_span is not None:
        test_suite_span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
        test_suite_span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
        test_module_path = test_module_span.get_tag(test.MODULE_PATH)
        test_suite_span.set_tag_str(test.MODULE_PATH, test_module_path)
    test_suite_span.set_tag_str(test.SUITE, _get_suite_name(item, test_module_path))
    _store_span(item, test_suite_span)

    if should_enable_coverage:
        _attach_coverage(item)
    return test_suite_span


def pytest_addoption(parser):
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group._addoption(
        "--ddtrace",
        action="store_true",
        dest="ddtrace",
        default=False,
        help=HELP_MSG,
    )

    group._addoption(
        "--no-ddtrace",
        action="store_true",
        dest="no-ddtrace",
        default=False,
        help=HELP_MSG,
    )

    group._addoption(
        "--ddtrace-patch-all",
        action="store_true",
        dest="ddtrace-patch-all",
        default=False,
        help=PATCH_ALL_HELP_MSG,
    )

    parser.addini("ddtrace", HELP_MSG, type="bool")
    parser.addini("no-ddtrace", HELP_MSG, type="bool")
    parser.addini("ddtrace-patch-all", PATCH_ALL_HELP_MSG, type="bool")


def pytest_configure(config):
    unpatch_unittest()
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
    if is_enabled(config):
        _CIVisibility.enable(config=ddtrace.config.pytest)


def pytest_sessionstart(session):
    if _CIVisibility.enabled:
        log.debug("CI Visibility enabled - starting test session")
        global _global_skipped_elements
        _global_skipped_elements = 0
        test_session_span = _CIVisibility._instance.tracer.trace(
            "pytest.test_session",
            service=_CIVisibility._instance._service,
            span_type=SpanTypes.TEST,
        )
        test_session_span.set_tag_str(COMPONENT, "pytest")
        test_session_span.set_tag_str(SPAN_KIND, KIND)
        test_session_span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        test_session_span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)
        test_session_span.set_tag_str(_EVENT_TYPE, _SESSION_TYPE)
        test_session_span.set_tag_str(test.COMMAND, _get_pytest_command(session.config))
        test_session_span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))
        if _CIVisibility.test_skipping_enabled():
            test_session_span.set_tag_str(test.ITR_TEST_SKIPPING_ENABLED, "true")
            test_session_span.set_tag(
                test.ITR_TEST_SKIPPING_TYPE, SUITE if _CIVisibility._instance._suite_skipping_mode else TEST
            )
            test_session_span.set_tag(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "false")
            test_session_span.set_tag(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "false")
            test_session_span.set_tag_str(test.ITR_FORCED_RUN, "false")
            test_session_span.set_tag_str(test.ITR_UNSKIPPABLE, "false")
        else:
            test_session_span.set_tag_str(test.ITR_TEST_SKIPPING_ENABLED, "false")
        test_session_span.set_tag_str(
            test.ITR_TEST_CODE_COVERAGE_ENABLED,
            "true" if _CIVisibility._instance._collect_coverage_enabled else "false",
        )

        _store_span(session, test_session_span)


def pytest_sessionfinish(session, exitstatus):
    if _CIVisibility.enabled:
        log.debug("CI Visibility enabled - finishing test session")
        test_session_span = _extract_span(session)
        if test_session_span is not None:
            if _CIVisibility.test_skipping_enabled():
                test_session_span.set_metric(test.ITR_TEST_SKIPPING_COUNT, _global_skipped_elements)
            _mark_test_status(session, test_session_span)
            test_session_span.finish()
        _CIVisibility.disable()


@pytest.fixture(scope="function")
def ddspan(request):
    """Return the :class:`ddtrace.span.Span` instance associated with the
    current test when Datadog CI Visibility is enabled.
    """
    if _CIVisibility.enabled:
        return _extract_span(request.node)


@pytest.fixture(scope="session")
def ddtracer():
    """Return the :class:`ddtrace.tracer.Tracer` instance for Datadog CI
    visibility if it is enabled, otherwise return the default Datadog tracer.
    """
    if _CIVisibility.enabled:
        return _CIVisibility._instance.tracer
    return ddtrace.tracer


@pytest.fixture(scope="session", autouse=True)
def patch_all(request):
    """Patch all available modules for Datadog tracing when ddtrace-patch-all
    is specified in command or .ini.
    """
    if request.config.getoption("ddtrace-patch-all") or request.config.getini("ddtrace-patch-all"):
        ddtrace.patch_all()


def _find_pytest_item(item, pytest_item_type):
    """
    Given a `pytest.Item`, traverse upwards until we find a specified `pytest.Package` or `pytest.Module` item,
    or return None.
    """
    if item is None:
        return None
    if pytest_item_type not in [pytest.Package, pytest.Module]:
        return None
    parent = item.parent
    while not isinstance(parent, pytest_item_type) and parent is not None:
        parent = parent.parent
    return parent


def _get_test_class_hierarchy(item):
    """
    Given a `pytest.Item` function item, traverse upwards to collect and return a string listing the
    test class hierarchy, or an empty string if there are no test classes.
    """
    parent = item.parent
    test_class_hierarchy = []
    while parent is not None:
        if isinstance(parent, pytest.Class):
            test_class_hierarchy.insert(0, parent.name)
        parent = parent.parent
    return ".".join(test_class_hierarchy)


def pytest_collection_modifyitems(session, config, items):
    if _CIVisibility.test_skipping_enabled():
        skip = pytest.mark.skip(reason=SKIPPED_BY_ITR_REASON)

        items_to_skip_by_module = {}
        current_suite_has_unskippable_test = False

        for item in items:
            test_is_unskippable = _is_test_unskippable(item)

            if test_is_unskippable:
                log.debug("Test %s in module %s is marked as unskippable", (item.name, item.module))
                item._dd_itr_test_unskippable = True

            # Due to suite skipping mode, defer adding ITR skip marker until unskippable status of the suite has been
            # fully resolved because Pytest markers cannot be dynamically removed
            if _CIVisibility._instance._suite_skipping_mode:
                if item.module not in items_to_skip_by_module:
                    items_to_skip_by_module[item.module] = []
                    current_suite_has_unskippable_test = False

                if test_is_unskippable and not current_suite_has_unskippable_test:
                    current_suite_has_unskippable_test = True
                    # Retroactively mark collected tests as forced:
                    for item_to_skip in items_to_skip_by_module[item.module]:
                        item_to_skip._dd_itr_forced = True
                    items_to_skip_by_module[item.module] = []

            if _CIVisibility._instance._should_skip_path(str(get_fslocation_from_item(item)[0]), item.name):
                if test_is_unskippable or (
                    _CIVisibility._instance._suite_skipping_mode and current_suite_has_unskippable_test
                ):
                    item._dd_itr_forced = True
                else:
                    items_to_skip_by_module.setdefault(item.module, []).append(item)

        # Mark remaining tests that should be skipped
        for items_to_skip in items_to_skip_by_module.values():
            for item_to_skip in items_to_skip:
                item_to_skip.add_marker(skip)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if not _CIVisibility.enabled:
        yield
        return

    is_skipped = bool(
        item.get_closest_marker("skip")
        or any([marker for marker in item.iter_markers(name="skipif") if marker.args[0] is True])
    )
    is_skipped_by_itr = bool(
        is_skipped
        and any(
            [
                marker
                for marker in item.iter_markers(name="skip")
                if "reason" in marker.kwargs and marker.kwargs["reason"] == SKIPPED_BY_ITR_REASON
            ]
        )
    )

    test_session_span = _extract_span(item.session)

    pytest_module_item = _find_pytest_item(item, pytest.Module)
    pytest_package_item = _find_pytest_item(pytest_module_item, pytest.Package)

    module_is_package = True

    test_module_span = _extract_span(pytest_package_item)
    if not test_module_span:
        test_module_span = _extract_module_span(pytest_module_item)
        if test_module_span:
            module_is_package = False

    if test_module_span is None:
        test_module_span, module_is_package = _start_test_module_span(pytest_package_item, pytest_module_item)

    if _CIVisibility.test_skipping_enabled() and test_module_span.get_metric(test.ITR_TEST_SKIPPING_COUNT) is None:
        test_module_span.set_tag(
            test.ITR_TEST_SKIPPING_TYPE, SUITE if _CIVisibility._instance._suite_skipping_mode else TEST
        )
        test_module_span.set_metric(test.ITR_TEST_SKIPPING_COUNT, 0)

    test_suite_span = _extract_span(pytest_module_item)
    if pytest_module_item is not None and test_suite_span is None:
        # Start coverage for the test suite if coverage is enabled
        # In ITR suite skipping mode, all tests in a skipped suite should be marked
        # as skipped
        test_suite_span = _start_test_suite_span(
            pytest_module_item,
            test_module_span,
            should_enable_coverage=(
                _CIVisibility._instance._suite_skipping_mode
                and _CIVisibility._instance._collect_coverage_enabled
                and not is_skipped_by_itr
            ),
        )

    if is_skipped_by_itr:
        test_module_span._metrics[test.ITR_TEST_SKIPPING_COUNT] += 1
        global _global_skipped_elements
        _global_skipped_elements += 1
        test_module_span.set_tag_str(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "true")
        test_module_span.set_tag_str(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "true")

        test_session_span.set_tag_str(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "true")
        test_session_span.set_tag_str(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "true")

    with _CIVisibility._instance.tracer._start_span(
        ddtrace.config.pytest.operation_name,
        service=_CIVisibility._instance._service,
        resource=item.nodeid,
        span_type=SpanTypes.TEST,
        activate=True,
    ) as span:
        span.set_tag_str(COMPONENT, "pytest")
        span.set_tag_str(SPAN_KIND, KIND)
        span.set_tag_str(test.FRAMEWORK, FRAMEWORK)
        span.set_tag_str(_EVENT_TYPE, SpanTypes.TEST)
        span.set_tag_str(test.NAME, item.name)
        span.set_tag_str(test.COMMAND, _get_pytest_command(item.config))
        if test_session_span:
            span.set_tag_str(_SESSION_ID, str(test_session_span.span_id))

        span.set_tag_str(_MODULE_ID, str(test_module_span.span_id))
        span.set_tag_str(test.MODULE, test_module_span.get_tag(test.MODULE))
        span.set_tag_str(test.MODULE_PATH, test_module_span.get_tag(test.MODULE_PATH))

        span.set_tag_str(_SUITE_ID, str(test_suite_span.span_id))
        test_class_hierarchy = _get_test_class_hierarchy(item)
        if test_class_hierarchy:
            span.set_tag_str(test.CLASS_HIERARCHY, test_class_hierarchy)
        if hasattr(item, "dtest") and isinstance(item.dtest, DocTest):
            span.set_tag_str(test.SUITE, "{}.py".format(item.dtest.globs["__name__"]))
        else:
            span.set_tag_str(test.SUITE, test_suite_span.get_tag(test.SUITE))

        span.set_tag_str(test.TYPE, SpanTypes.TEST)
        span.set_tag_str(test.FRAMEWORK_VERSION, pytest.__version__)

        if item.location and item.location[0]:
            _CIVisibility.set_codeowners_of(item.location[0], span=span)

        # We preemptively set FAIL as a status, because if pytest_runtest_makereport is not called
        # (where the actual test status is set), it means there was a pytest error
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)

        # Parameterized test cases will have a `callspec` attribute attached to the pytest Item object.
        # Pytest docs: https://docs.pytest.org/en/6.2.x/reference.html#pytest.Function
        if getattr(item, "callspec", None):
            parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
            for param_name, param_val in item.callspec.params.items():
                try:
                    parameters["arguments"][param_name] = encode_test_parameter(param_val)
                except Exception:
                    parameters["arguments"][param_name] = "Could not encode"
                    log.warning("Failed to encode %r", param_name, exc_info=True)
            span.set_tag_str(test.PARAMETERS, json.dumps(parameters))

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            span.set_tags(tags)
        _store_span(item, span)

        # Items are marked ITR-unskippable regardless of other unrelateed skipping status
        if getattr(item, "_dd_itr_test_unskippable", False) or getattr(item, "_dd_itr_suite_unskippable", False):
            _mark_test_unskippable(item)
        if not is_skipped:
            if getattr(item, "_dd_itr_forced", False):
                _mark_test_forced(item)

        coverage_per_test = (
            not _CIVisibility._instance._suite_skipping_mode
            and _CIVisibility._instance._collect_coverage_enabled
            and not is_skipped
        )
        if coverage_per_test:
            _attach_coverage(item)
        # Run the actual test
        yield

        # Finish coverage for the test suite if coverage is enabled
        if coverage_per_test:
            _detach_coverage(item, span)

        nextitem_pytest_module_item = _find_pytest_item(nextitem, pytest.Module)
        if nextitem is None or nextitem_pytest_module_item != pytest_module_item and not test_suite_span.finished:
            _mark_test_status(pytest_module_item, test_suite_span)
            # Finish coverage for the test suite if coverage is enabled
            # In ITR suite skipping mode, all tests in a skipped suite should be marked
            # as skipped
            if (
                _CIVisibility._instance._suite_skipping_mode
                and _CIVisibility._instance._collect_coverage_enabled
                and not is_skipped_by_itr
            ):
                _detach_coverage(pytest_module_item, test_suite_span)
            test_suite_span.finish()

            if not module_is_package:
                test_module_span.set_tag_str(test.STATUS, test_suite_span.get_tag(test.STATUS))
                test_module_span.finish()
            else:
                nextitem_pytest_package_item = _find_pytest_item(nextitem, pytest.Package)
                if (
                    nextitem is None
                    or nextitem_pytest_package_item != pytest_package_item
                    and not test_module_span.finished
                ):
                    _mark_test_status(pytest_package_item, test_module_span)
                    test_module_span.finish()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    if not _CIVisibility.enabled:
        return

    span = _extract_span(item)
    if span is None:
        return

    is_setup_or_teardown = call.when == "setup" or call.when == "teardown"
    has_exception = call.excinfo is not None

    if is_setup_or_teardown and not has_exception:
        return

    result = outcome.get_result()
    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            span.set_tag_str(test.STATUS, test.Status.PASS.value)
            _mark_not_skipped(item.parent)
            if not item.config.option.runxfail:
                span.set_tag_str(test.RESULT, test.Status.XFAIL.value)
                span.set_tag_str(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
        else:
            span.set_tag_str(test.STATUS, test.Status.SKIP.value)
        reason = _extract_reason(call)
        if reason is not None:
            span.set_tag_str(test.SKIP_REASON, str(reason))
            if reason == SKIPPED_BY_ITR_REASON:
                span.set_tag_str(test.ITR_SKIPPED, "true")
    elif result.passed:
        _mark_not_skipped(item.parent)
        span.set_tag_str(test.STATUS, test.Status.PASS.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            span.set_tag_str(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
            span.set_tag_str(test.RESULT, test.Status.XPASS.value)
    else:
        # Store failure in test suite `pytest.Item` to propagate to test suite spans
        _mark_failed(item.parent)
        _mark_not_skipped(item.parent)
        span.set_tag_str(test.STATUS, test.Status.FAIL.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
            span.set_tag_str(XFAIL_REASON, getattr(result, "longrepr", "XFail"))
            span.set_tag_str(test.RESULT, test.Status.XPASS.value)
        if call.excinfo:
            span.set_exc_info(call.excinfo.type, call.excinfo.value, call.excinfo.tb)
