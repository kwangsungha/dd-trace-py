from collections import defaultdict
from ctypes import c_int
import json
import multiprocessing
import textwrap
import time

import mock
import pytest

import ddtrace
from ddtrace.constants import AUTO_KEEP
from ddtrace.ext import ci
from ddtrace.ext.git import _build_git_packfiles_with_details
from ddtrace.ext.git import _GitSubprocessDetails
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.constants import SUITE
from ddtrace.internal.ci_visibility.constants import TEST
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClientSerializerV1
from ddtrace.internal.ci_visibility.recorder import _extract_repository_name_from_url
from ddtrace.internal.utils.http import Response
from ddtrace.span import Span
from tests.ci_visibility.util import _patch_dummy_writer
from tests.utils import DummyCIVisibilityWriter
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


TEST_SHA_1 = "b3672ea5cbc584124728c48a443825d2940e0ddd"
TEST_SHA_2 = "b3672ea5cbc584124728c48a443825d2940e0eee"


def test_filters_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_test_span = Span(name="span1", span_type="test")
    root_test_span._local_root = root_test_span
    # Root span in trace is a test
    trace = [root_test_span]
    assert trace_filter.process_trace(trace) == trace
    assert root_test_span.service == "test-service"
    assert root_test_span.get_tag(ci.LIBRARY_VERSION) == ddtrace.__version__
    assert root_test_span.get_tag("hello") == "world"
    assert root_test_span.context.dd_origin == ci.CI_APP_TEST_ORIGIN
    assert root_test_span.context.sampling_priority == AUTO_KEEP


def test_filters_non_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_span = Span(name="span1")
    root_span._local_root = root_span
    # Root span in trace is not a test
    trace = [root_span]
    assert trace_filter.process_trace(trace) is None


def test_ci_visibility_service_enable():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            ci_visibility_instance = CIVisibility._instance
            assert ci_visibility_instance is not None
            assert CIVisibility.enabled
            assert ci_visibility_instance.tracer == dummy_tracer
            assert ci_visibility_instance._service == "test-service"
            assert ci_visibility_instance._code_coverage_enabled_by_api is False
            assert ci_visibility_instance._test_skipping_enabled_by_api is False
            assert any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in dummy_tracer._filters)
            CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_ci_visibility_service_enable_with_app_key_and_itr_disabled(_do_request):
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        with _patch_dummy_writer():
            _do_request.return_value = Response(
                status=200,
                body='{"data":{"id":"1234","type":"ci_app_tracers_test_service_settings","attributes":'
                '{"code_coverage":true,"tests_skipping":true}}}',
            )
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            assert CIVisibility._instance._code_coverage_enabled_by_api is False
            assert CIVisibility._instance._test_skipping_enabled_by_api is False
            CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request", side_effect=TimeoutError)
def test_ci_visibility_service_settings_timeout(_do_request):
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_ITR_ENABLED="1",
        )
    ):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._code_coverage_enabled_by_api is False
        assert CIVisibility._instance._test_skipping_enabled_by_api is False
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features", return_value=(True, True))
@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request", side_effect=TimeoutError)
def test_ci_visibility_service_skippable_timeout(_do_request, _check_enabled_features):
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_ITR_ENABLED="1",
        )
    ):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._test_suites_to_skip == []
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_ci_visibility_service_enable_with_itr_enabled(_do_request):
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_ITR_ENABLED="1",
        )
    ), mock.patch("ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        _do_request.return_value = Response(
            status=200,
            body='{"data":{"id":"1234","type":"ci_app_tracers_test_service_settings","attributes":'
            '{"code_coverage":true,"tests_skipping":true, "require_git": false}}}',
        )
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._code_coverage_enabled_by_api is True
        assert CIVisibility._instance._test_skipping_enabled_by_api is True
        CIVisibility.disable()


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_ci_visibility_service_enable_with_app_key_and_error_response(_do_request):
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APP_KEY="foobar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        _do_request.return_value = Response(
            status=404,
            body='{"errors": ["Not found"]}',
        )
        CIVisibility.enable(service="test-service")
        assert CIVisibility._instance._code_coverage_enabled_by_api is False
        assert CIVisibility._instance._test_skipping_enabled_by_api is False
        CIVisibility.disable()


def test_ci_visibility_service_disable():
    with override_env(dict(DD_API_KEY="foobar.baz")):
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            CIVisibility.enable(tracer=dummy_tracer, service="test-service")
            CIVisibility.disable()
            ci_visibility_instance = CIVisibility._instance
            assert ci_visibility_instance is None
            assert not CIVisibility.enabled


@pytest.mark.parametrize(
    "repository_url,repository_name",
    [
        ("https://github.com/DataDog/dd-trace-py.git", "dd-trace-py"),
        ("https://github.com/DataDog/dd-trace-py", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py.git", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py", "dd-trace-py"),
        ("dd-trace-py", "dd-trace-py"),
        ("git@hostname.com:org/repo-name.git", "repo-name"),
        ("git@hostname.com:org/repo-name", "repo-name"),
        ("ssh://git@hostname.com:org/repo-name", "repo-name"),
        ("git+git://github.com/org/repo-name.git", "repo-name"),
        ("git+ssh://github.com/org/repo-name.git", "repo-name"),
        ("git+https://github.com/org/repo-name.git", "repo-name"),
    ],
)
def test_repository_name_extracted(repository_url, repository_name):
    assert _extract_repository_name_from_url(repository_url) == repository_name


def test_repository_name_not_extracted_warning():
    """If provided an invalid repository url, should raise warning and return original repository url"""
    repository_url = "https://github.com:organ[ization/repository-name"
    with mock.patch("ddtrace.internal.ci_visibility.recorder.log") as mock_log:
        extracted_repository_name = _extract_repository_name_from_url(repository_url)
        assert extracted_repository_name == repository_url
    mock_log.warning.assert_called_once_with("Repository name cannot be parsed from repository_url: %s", repository_url)


DUMMY_RESPONSE = Response(status=200, body='{"data": [{"type": "commit", "id": "%s", "attributes": {}}]}' % TEST_SHA_1)


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_git_client_worker_agentless(_do_request, git_repo):
    _do_request.return_value = Response(
        status=200,
        body='{"data":{"id":"1234","type":"ci_app_tracers_test_service_settings","attributes":'
        '{"code_coverage":true,"tests_skipping":true}}}',
    )
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APPLICATION_KEY="banana",
            DD_CIVISIBILITY_ITR_ENABLED="1",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_SITE="datadoghq.com",
        )
    ):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            start_time = time.time()
            with mock.patch("ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"), mock.patch(
                "ddtrace.internal.ci_visibility.recorder._get_git_repo"
            ) as ggr:
                original = ddtrace.internal.ci_visibility.git_client.RESPONSE
                ddtrace.internal.ci_visibility.git_client.RESPONSE = DUMMY_RESPONSE
                ggr.return_value = git_repo
                CIVisibility.enable(tracer=dummy_tracer, service="test-service")
                assert CIVisibility._instance._git_client is not None
                assert CIVisibility._instance._git_client._worker is not None
                assert CIVisibility._instance._git_client._base_url == "https://api.datadoghq.com/api/v2/git"
                CIVisibility.disable()
    shutdown_timeout = dummy_tracer.SHUTDOWN_TIMEOUT
    assert (
        time.time() - start_time <= shutdown_timeout + 0.1
    ), "CIVisibility.disable() should not block for longer than tracer timeout"
    ddtrace.internal.ci_visibility.git_client.RESPONSE = original


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_git_client_worker_evp_proxy(_do_request, git_repo):
    _do_request.return_value = Response(
        status=200,
        body='{"data":{"id":"1234","type":"ci_app_tracers_test_service_settings","attributes":'
        '{"code_coverage":true,"tests_skipping":true}}}',
    )
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_APPLICATION_KEY="banana",
            DD_CIVISIBILITY_ITR_ENABLED="1",
        )
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_is_available", return_value=True
    ):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        with _patch_dummy_writer():
            dummy_tracer = DummyTracer()
            start_time = time.time()
            with mock.patch("ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"), mock.patch(
                "ddtrace.internal.ci_visibility.recorder._get_git_repo"
            ) as ggr:
                original = ddtrace.internal.ci_visibility.git_client.RESPONSE
                ddtrace.internal.ci_visibility.git_client.RESPONSE = DUMMY_RESPONSE
                ggr.return_value = git_repo
                CIVisibility.enable(tracer=dummy_tracer, service="test-service")
                assert CIVisibility._instance._git_client is not None
                assert CIVisibility._instance._git_client._worker is not None
                assert CIVisibility._instance._git_client._base_url == "http://localhost:8126/evp_proxy/v2/api/v2/git"
                CIVisibility.disable()
    shutdown_timeout = dummy_tracer.SHUTDOWN_TIMEOUT
    assert (
        time.time() - start_time <= shutdown_timeout + 0.1
    ), "CIVisibility.disable() should not block for longer than tracer timeout"
    ddtrace.internal.ci_visibility.git_client.RESPONSE = original


def test_git_client_get_repository_url(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(cwd=git_repo)
    assert remote_url == "git@github.com:test-repo-url.git"


def test_git_client_get_repository_url_env_var_precedence(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(
        tags={ci.git.REPOSITORY_URL: "https://github.com/Datadog/dd-trace-py"}, cwd=git_repo
    )
    assert remote_url == "https://github.com/Datadog/dd-trace-py"


def test_git_client_get_repository_url_env_var_precedence_empty_tags(git_repo):
    remote_url = CIVisibilityGitClient._get_repository_url(tags={}, cwd=git_repo)
    assert remote_url == "git@github.com:test-repo-url.git"


def test_git_client_get_latest_commits(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details",
        return_value=_GitSubprocessDetails(
            "b3672ea5cbc584124728c48a443825d2940e0ddd\nb3672ea5cbc584124728c48a443825d2940e0eee", "", 10, 0
        ),
    ) as mock_git_subprocess_cmd_with_details:
        latest_commits = CIVisibilityGitClient._get_latest_commits(cwd=git_repo)
        assert latest_commits == [TEST_SHA_1, TEST_SHA_2]
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "log", "--format=%H", "-n", "1000", '--since="1 month ago"', cwd=git_repo
        )


def test_git_client_search_commits():
    remote_url = "git@github.com:test-repo-url.git"
    latest_commits = [TEST_SHA_1]
    serializer = CIVisibilityGitClientSerializerV1("foo")
    backend_commits = CIVisibilityGitClient._search_commits(
        REQUESTS_MODE.AGENTLESS_EVENTS, "", remote_url, latest_commits, serializer, DUMMY_RESPONSE
    )
    assert latest_commits[0] in backend_commits


def test_get_client_do_request_agentless_headers():
    serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    response.status = 200

    with mock.patch("ddtrace.internal.http.HTTPConnection.request") as _request, mock.patch(
        "ddtrace.internal.compat.get_connection_response", return_value=response
    ):
        CIVisibilityGitClient._do_request(
            REQUESTS_MODE.AGENTLESS_EVENTS, "http://base_url", "/endpoint", "payload", serializer, {}
        )

    _request.assert_called_once_with("POST", "/repository/endpoint", "payload", {"dd-api-key": "foo"})


def test_get_client_do_request_evp_proxy_headers():
    serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    response.status = 200

    with mock.patch("ddtrace.internal.http.HTTPConnection.request") as _request, mock.patch(
        "ddtrace.internal.compat.get_connection_response", return_value=response
    ):
        CIVisibilityGitClient._do_request(
            REQUESTS_MODE.EVP_PROXY_EVENTS, "http://base_url", "/endpoint", "payload", serializer, {}
        )

    _request.assert_called_once_with(
        "POST",
        "/repository/endpoint",
        "payload",
        {"X-Datadog-EVP-Subdomain": "api"},
    )


def test_git_client_get_filtered_revisions(git_repo):
    excluded_commits = [TEST_SHA_1]
    filtered_revisions = CIVisibilityGitClient._get_filtered_revisions(excluded_commits, cwd=git_repo)
    assert filtered_revisions == ""


@pytest.mark.parametrize("requests_mode", [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS])
def test_git_client_upload_packfiles(git_repo, requests_mode):
    serializer = CIVisibilityGitClientSerializerV1("foo")
    remote_url = "git@github.com:test-repo-url.git"
    with _build_git_packfiles_with_details("%s\n" % TEST_SHA_1, cwd=git_repo) as (packfiles_path, packfiles_details):
        with mock.patch("ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._do_request") as dr:
            dr.return_value = Response(
                status=200,
            )
            CIVisibilityGitClient._upload_packfiles(
                requests_mode, "", remote_url, packfiles_path, serializer, None, cwd=git_repo
            )
            assert dr.call_count == 1
            call_args = dr.call_args_list[0][0]
            call_kwargs = dr.call_args.kwargs
            assert call_args[0] == requests_mode
            assert call_args[1] == ""
            assert call_args[2] == "/packfile"
            assert call_args[3].startswith(b"------------boundary------\r\nContent-Disposition: form-data;")
            assert call_kwargs["headers"] == {"Content-Type": "multipart/form-data; boundary=----------boundary------"}


def test_git_do_request_agentless(git_repo):
    mock_serializer = CIVisibilityGitClientSerializerV1("fakeapikey")
    response = mock.MagicMock()
    setattr(response, "status", 200)  # noqa: B010

    with mock.patch("ddtrace.internal.ci_visibility.git_client.get_connection") as mock_get_connection:
        with mock.patch("ddtrace.internal.compat.get_connection_response", return_value=response):
            mock_http_connection = mock.Mock()
            mock_http_connection.request = mock.Mock()
            mock_http_connection.close = mock.Mock()
            mock_get_connection.return_value = mock_http_connection

            CIVisibilityGitClient._do_request(
                REQUESTS_MODE.AGENTLESS_EVENTS,
                "http://base_url",
                "/endpoint",
                '{"payload": "payload"}',
                mock_serializer,
                {"mock_header_name": "mock_header_value"},
            )

            mock_get_connection.assert_called_once_with("http://base_url/repository/endpoint", timeout=20)
            mock_http_connection.request.assert_called_once_with(
                "POST",
                "/repository/endpoint",
                '{"payload": "payload"}',
                {
                    "dd-api-key": "fakeapikey",
                    "mock_header_name": "mock_header_value",
                },
            )


def test_git_do_request_evp(git_repo):
    mock_serializer = CIVisibilityGitClientSerializerV1("foo")
    response = mock.MagicMock()
    setattr(response, "status", 200)  # noqa: B010

    with mock.patch("ddtrace.internal.ci_visibility.git_client.get_connection") as mock_get_connection:
        with mock.patch("ddtrace.internal.compat.get_connection_response", return_value=response):
            mock_http_connection = mock.Mock()
            mock_http_connection.request = mock.Mock()
            mock_http_connection.close = mock.Mock()
            mock_get_connection.return_value = mock_http_connection

            CIVisibilityGitClient._do_request(
                REQUESTS_MODE.EVP_PROXY_EVENTS,
                "https://base_url",
                "/endpoint",
                '{"payload": "payload"}',
                mock_serializer,
                {"mock_header_name": "mock_header_value"},
            )

            mock_get_connection.assert_called_once_with("https://base_url/repository/endpoint", timeout=20)
            mock_http_connection.request.assert_called_once_with(
                "POST",
                "/repository/endpoint",
                '{"payload": "payload"}',
                {
                    "X-Datadog-EVP-Subdomain": "api",
                    "mock_header_name": "mock_header_value",
                },
            )


def test_civisibilitywriter_agentless_url():
    with override_env(dict(DD_API_KEY="foobar.baz")):
        with override_global_config({"_ci_visibility_agentless_url": "https://foo.bar"}):
            ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url = (
                ddtrace.config._ci_visibility_agentless_url
            )  # this is what override_global_config is supposed to do, but in this case it doesn't work
            dummy_writer = DummyCIVisibilityWriter()
            assert dummy_writer.intake_url == "https://foo.bar"


def test_civisibilitywriter_coverage_agentless_url():
    ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url = ""
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        dummy_writer = DummyCIVisibilityWriter(coverage_enabled=True)
        assert dummy_writer.intake_url == "https://citestcycle-intake.datadoghq.com"

        cov_client = dummy_writer._clients[1]
        assert cov_client._intake_url == "https://citestcov-intake.datadoghq.com"

        with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection:
            dummy_writer._put("", {}, cov_client, no_trace=True)
            _get_connection.assert_called_once_with("https://citestcov-intake.datadoghq.com", 2.0)


def test_civisibilitywriter_coverage_agentless_with_intake_url_param():
    ddtrace.internal.ci_visibility.writer.config._ci_visibility_agentless_url = ""
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        dummy_writer = DummyCIVisibilityWriter(intake_url="https://some-url.com", coverage_enabled=True)
        assert dummy_writer.intake_url == "https://some-url.com"

        cov_client = dummy_writer._clients[1]
        assert cov_client._intake_url == "https://citestcov-intake.datadoghq.com"

        with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection:
            dummy_writer._put("", {}, cov_client, no_trace=True)
            _get_connection.assert_called_once_with("https://citestcov-intake.datadoghq.com", 2.0)


def test_civisibilitywriter_coverage_evp_proxy_url():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
        )
    ):
        dummy_writer = DummyCIVisibilityWriter(use_evp=True, coverage_enabled=True)

        test_client = dummy_writer._clients[0]
        assert test_client.ENDPOINT == "/evp_proxy/v2/api/v2/citestcycle"
        cov_client = dummy_writer._clients[1]
        assert cov_client.ENDPOINT == "/evp_proxy/v2/api/v2/citestcov"

        with mock.patch("ddtrace.internal.writer.writer.get_connection") as _get_connection:
            dummy_writer._put("", {}, cov_client, no_trace=True)
            _get_connection.assert_called_once_with("http://localhost:8126", 2.0)


def test_civisibilitywriter_agentless_url_envvar():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_URL="https://foo.bar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        ddtrace.internal.ci_visibility.writer.config = ddtrace.settings.Config()
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable()
        assert CIVisibility._instance._requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS
        assert CIVisibility._instance.tracer._writer.intake_url == "https://foo.bar"
        CIVisibility.disable()


def test_civisibilitywriter_evp_proxy_url():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
        )
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_is_available", return_value=True
    ):
        ddtrace.internal.ci_visibility.writer.config = ddtrace.settings.Config()
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable()
        assert CIVisibility._instance._requests_mode == REQUESTS_MODE.EVP_PROXY_EVENTS
        assert CIVisibility._instance.tracer._writer.intake_url == "http://localhost:8126"
        CIVisibility.disable()


def test_civisibilitywriter_only_traces():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
        )
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_is_available", return_value=False
    ):
        ddtrace.internal.ci_visibility.writer.config = ddtrace.settings.Config()
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable()
        assert CIVisibility._instance._requests_mode == REQUESTS_MODE.TRACES
        assert CIVisibility._instance.tracer._writer.intake_url == "http://localhost:8126"
        CIVisibility.disable()


class TestCheckEnabledFeatures:
    """Test whether CIVisibility._check_enabled_features properly
    - properly calls _do_request (eg: payloads are correct)
    - waits for git metadata upload as necessary

    Across a "matrix" of:
    - whether the settings API returns {... "require_git": true ...}
    - call failures
    """

    requests_mode_parameters = [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS]

    # All requests to setting endpoint are the same within a call of _check_enabled_features()
    expected_do_request_method = "POST"
    expected_do_request_urls = {
        REQUESTS_MODE.AGENTLESS_EVENTS: "https://api.datad0g.com/api/v2/libraries/tests/services/setting",
        REQUESTS_MODE.EVP_PROXY_EVENTS: "http://localhost:8126/evp_proxy/v2/api/v2/libraries/tests/services/setting",
    }
    expected_do_request_headers = {
        REQUESTS_MODE.AGENTLESS_EVENTS: {
            "dd-api-key": "myfakeapikey",
            "Content-Type": "application/json",
        },
        REQUESTS_MODE.EVP_PROXY_EVENTS: {
            "X-Datadog-EVP-Subdomain": "api",
        },
    }
    expected_do_request_payload = {
        "data": {
            "id": "checkoutmyuuid4",
            "type": "ci_app_test_service_libraries_settings",
            "attributes": {
                "test_level": "test",
                "service": "service",
                "env": None,
                "repository_url": "my_repo_url",
                "sha": "mycommitshaaaaaaalalala",
                "branch": "notmain",
                "configurations": {
                    "os.architecture": "arm64",
                    "os.platform": "PlatForm",
                    "os.version": "9.8.a.b",
                    "runtime.name": "RPython",
                    "runtime.version": "11.5.2",
                },
            },
        }
    }

    @staticmethod
    def _get_mock_civisibility(requests_mode, suite_skipping_mode):
        with mock.patch.object(CIVisibility, "__init__", return_value=None):
            mock_civisibility = CIVisibility()

            # User-configurable values
            mock_civisibility._requests_mode = requests_mode
            mock_civisibility._suite_skipping_mode = suite_skipping_mode

            # Defaults
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._service = "service"
            mock_civisibility._api_key = "myfakeapikey"
            mock_civisibility._dd_site = "datad0g.com"
            mock_civisibility._tags = {
                ci.git.REPOSITORY_URL: "my_repo_url",
                ci.git.COMMIT_SHA: "mycommitshaaaaaaalalala",
                ci.git.BRANCH: "notmain",
            }
            mock_civisibility._configurations = {
                "os.architecture": "arm64",
                "os.platform": "PlatForm",
                "os.version": "9.8.a.b",
                "runtime.name": "RPython",
                "runtime.version": "11.5.2",
            }
            mock_civisibility._git_client = mock.MagicMock(spec=CIVisibilityGitClient)

        return mock_civisibility

    @staticmethod
    def _get_settings_api_response(status_code, code_coverage, tests_skipping, require_git):
        return Response(
            status=status_code,
            body=json.dumps(
                {
                    "data": {
                        "id": "1234",
                        "type": "ci_app_tracers_test_service_settings",
                        "attributes": {
                            "code_coverage": code_coverage,
                            "tests_skipping": tests_skipping,
                            "require_git": require_git,
                        },
                    }
                }
            ),
        )

    def _check_mock_do_request_calls(self, mock_do_request, count, requests_mode):
        assert mock_do_request.call_count == count

        for c in range(count):
            call_args = mock_do_request.call_args_list[c][0]
            assert call_args[0] == self.expected_do_request_method
            assert call_args[1] == self.expected_do_request_urls[requests_mode]
            assert call_args[3] == self.expected_do_request_headers[requests_mode]

            payload = json.loads(call_args[2])
            assert payload == self.expected_do_request_payload

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch("ddtrace.internal.ci_visibility.recorder.uuid4", return_value="checkoutmyuuid4"):
            yield

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "setting_response, expected_result",
        [
            ((True, True), (True, True)),
            ((True, False), (True, False)),
            ((False, True), (False, True)),
            ((False, False), (False, False)),
        ],
    )
    def test_civisibility_check_enabled_features_require_git_false(
        self, requests_mode, setting_response, expected_result
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            side_effect=[self._get_settings_api_response(200, setting_response[0], setting_response[1], False)],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(requests_mode, False)
            enabled_features = mock_civisibility._check_enabled_features()

            self._check_mock_do_request_calls(mock_do_request, 1, requests_mode)

            assert enabled_features == (expected_result[0], expected_result[1])

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "wait_for_upload_side_effect",
        [[METADATA_UPLOAD_STATUS.SUCCESS], [METADATA_UPLOAD_STATUS.FAILED], ValueError, TimeoutError],
    )
    @pytest.mark.parametrize(
        "setting_response, expected_result",
        [
            ([(True, True), (True, True)], (True, True)),
            ([(True, False), (True, False)], (True, False)),
            ([(True, False), (False, False)], (False, False)),
            ([(False, False), (False, False)], (False, False)),
        ],
    )
    @pytest.mark.parametrize("second_setting_require_git", [False, True])
    def test_civisibility_check_enabled_features_require_git_true(
        self, requests_mode, wait_for_upload_side_effect, setting_response, expected_result, second_setting_require_git
    ):
        """Simulates the scenario where we would run coverage because we don't have git metadata, but after the upload
        finishes, we see we don't need to run coverage.

        Along with the requests mode dimension, the test matrix covers the possible cases where:
        - coverage starts off true, then gets disabled after metadata upload (in which case skipping cannot be enabled)
        - coverage starts off true, then stays true
        - coverage starts off false, and stays false

        Finally, require_git on the second attempt is tested both as True and False, but the response should not affect
        the final settings
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            side_effect=[
                self._get_settings_api_response(200, setting_response[0][0], setting_response[0][1], True),
                self._get_settings_api_response(
                    200, setting_response[1][0], setting_response[1][1], second_setting_require_git
                ),
            ],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(requests_mode, False)
            mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = wait_for_upload_side_effect
            enabled_features = mock_civisibility._check_enabled_features()

            mock_civisibility._git_client.wait_for_metadata_upload_status.assert_called_once()

            self._check_mock_do_request_calls(mock_do_request, 2, requests_mode)

            assert enabled_features == (expected_result[0], expected_result[1])

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "first_do_request_side_effect",
        [
            TimeoutError,
            Response(status=200, body="} this is bad JSON"),
            Response(status=200, body='{"not correct key": "not correct value"}'),
            Response(status=600, body="Only status code matters here"),
            Response(
                status=200,
                body='{"errors":["Not found"]}',
            ),
            (200, True, True, True),
            (200, True, False, True),
            (200, False, False, True),
        ],
    )
    @pytest.mark.parametrize(
        "second_do_request_side_effect",
        [
            [TimeoutError],
            [Response(status=200, body="} this is bad JSON")],
            [Response(status=200, body='{"not correct key": "not correct value"}')],
            [Response(status=600, body="Only status code matters here")],
        ],
    )
    def test_civisibility_check_enabled_features_any_api_error_disables_itr(
        self, requests_mode, first_do_request_side_effect, second_do_request_side_effect
    ):
        """Tests that any error encountered while querying the setting endpoint results in ITR
        being disabled, whether on the first or second request
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
        ) as mock_do_request:
            if isinstance(first_do_request_side_effect, tuple):
                expected_call_count = 2
                mock_do_request.side_effect = [
                    self._get_settings_api_response(*first_do_request_side_effect),
                    second_do_request_side_effect,
                ]
            else:
                expected_call_count = 1
                mock_do_request.side_effect = [first_do_request_side_effect]

            mock_civisibility = self._get_mock_civisibility(requests_mode, False)
            mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = [METADATA_UPLOAD_STATUS.SUCCESS]

            enabled_features = mock_civisibility._check_enabled_features()

            assert mock_do_request.call_count == expected_call_count
            assert enabled_features == (False, False)


@mock.patch("ddtrace.internal.ci_visibility.recorder._do_request")
def test_civisibility_check_enabled_features_itr_disabled_request_not_called(_do_request):
    with override_env(
        dict(
            DD_API_KEY="foo.bar",
            DD_APP_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_URL="https://foo.bar",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
        )
    ):
        ddtrace.internal.ci_visibility.writer.config = ddtrace.settings.Config()
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        CIVisibility.enable()

        _do_request.assert_not_called()
        assert CIVisibility._instance._code_coverage_enabled_by_api is False
        assert CIVisibility._instance._test_skipping_enabled_by_api is False

        CIVisibility.disable()


def test_run_protocol_unshallow_git_ge_227():
    with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_git_version", return_value=(2, 27, 0)):
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _get_repository_url=mock.DEFAULT,
            _is_shallow_repository=classmethod(lambda *args, **kwargs: True),
            _get_latest_commits=classmethod(lambda *args, **kwwargs: ["latest1", "latest2"]),
            _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
            _get_filtered_revisions=mock.DEFAULT,
            _upload_packfiles=mock.DEFAULT,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            with mock.patch.object(CIVisibilityGitClient, "_unshallow_repository") as mock_unshallow_repository:
                CIVisibilityGitClient._run_protocol(None, None, None, multiprocessing.Value(c_int, 0))

            mock_unshallow_repository.assert_called_once_with(cwd=None)


def test_run_protocol_does_not_unshallow_git_lt_227():
    with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_git_version", return_value=(2, 26, 0)):
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _get_repository_url=mock.DEFAULT,
            _is_shallow_repository=classmethod(lambda *args, **kwargs: True),
            _get_latest_commits=classmethod(lambda *args, **kwargs: ["latest1", "latest2"]),
            _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
            _get_filtered_revisions=mock.DEFAULT,
            _upload_packfiles=mock.DEFAULT,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            with mock.patch.object(CIVisibilityGitClient, "_unshallow_repository") as mock_unshallow_repository:
                CIVisibilityGitClient._run_protocol(None, None, None, multiprocessing.Value(c_int, 0))

            mock_unshallow_repository.assert_not_called()


class TestUploadGitMetadata:
    """Exercises the multprocessed use of CIVisibilityGitClient.upload_git_metadata to make sure that the caller gets
    the expected METADATA_UPLOAD_STATUS value

    This uses CIVisibilityGitClient.wait_for_metadata_upload_status() since it returns the given status, and allows for
    exercising the various exception/error scenarios.
    """

    api_key_requests_mode_parameters = [
        ("myfakeapikey", REQUESTS_MODE.AGENTLESS_EVENTS),
        ("", REQUESTS_MODE.EVP_PROXY_EVENTS),
    ]

    @pytest.fixture(scope="function", autouse=True)
    def mock_git_client(self):
        """NotImplementedError mocks should never be reached unless tests are set up improperly"""
        with mock.patch.multiple(
            CIVisibilityGitClient,
            _is_shallow_repository=mock.Mock(return_value=True),
            _get_latest_commits=mock.Mock(
                side_effect=[["latest1", "latest2"], ["latest1", "latest2", "latest3", "latest4"]]
            ),
            _search_commits=mock.Mock(side_effect=[["latest1"], ["latest1", "latest2"]]),
            _unshallow_repository=mock.Mock(),
            _get_filtered_revisions=mock.Mock(return_value="latest3\nlatest4"),
            _upload_packfiles=mock.Mock(side_effec=NotImplementedError),
            _do_request=mock.Mock(side_effect=NotImplementedError),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            yield

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch(
            "ddtrace.ext.ci.tags",
            return_value={
                "git.repository_url": "git@github.com:TestDog/dd-test-py.git",
                "git.commit.sha": "mytestcommitsha1234",
            },
        ):
            yield

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_no_backend_commits_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_search_commits", return_value=None):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_no_revisions_succeeds(self, api_key, requests_mode):
        with mock.patch.object(
            CIVisibilityGitClient, "_get_filtered_revisions", classmethod(lambda *args, **kwargs: "")
        ):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_packfiles_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=False):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.FAILED

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_packfiles_succeeds(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_nonzero_return_code_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.SUCCESS

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_value_error_fails(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "_upload_packfiles", return_value=True):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client._worker = mock.MagicMock(spec=multiprocessing.Process)
            git_client._worker.exitcode = 0
            git_client.upload_git_metadata()
            with pytest.raises(ValueError):
                git_client.wait_for_metadata_upload_status()

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_timeout_raises_timeout(self, api_key, requests_mode):
        with mock.patch.object(CIVisibilityGitClient, "upload_git_metadata", lambda *args, **kwargs: time.sleep(3)):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client._worker = mock.MagicMock(spec=multiprocessing.Process)
            git_client._worker.exitcode = None
            git_client.upload_git_metadata()
            with pytest.raises(TimeoutError):
                git_client._wait_for_metadata_upload(0)

    @pytest.mark.parametrize("api_key, requests_mode", api_key_requests_mode_parameters)
    def test_upload_git_metadata_upload_unnecessary(self, api_key, requests_mode):
        with mock.patch.object(
            CIVisibilityGitClient, "_get_latest_commits", mock.Mock(side_effect=[["latest1", "latest2"]])
        ), mock.patch.object(CIVisibilityGitClient, "_search_commits", mock.Mock(side_effect=[["latest1", "latest2"]])):
            git_client = CIVisibilityGitClient(api_key, requests_mode)
            git_client.upload_git_metadata()
            assert git_client.wait_for_metadata_upload_status() == METADATA_UPLOAD_STATUS.UNNECESSARY


def test_get_filtered_revisions():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._get_rev_list_with_details",
        return_value=_GitSubprocessDetails("rev1\nrev2", "", 100, 0),
    ) as mock_get_rev_list:
        assert (
            CIVisibilityGitClient._get_filtered_revisions(
                ["excluded1", "excluded2"], included_commits=["included1", "included2"], cwd="/path/to/repo"
            )
            == "rev1\nrev2"
        )
        mock_get_rev_list.assert_called_once_with(
            ["excluded1", "excluded2"], ["included1", "included2"], cwd="/path/to/repo"
        )


def test_is_shallow_repository_true():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._is_shallow_repository_with_details", return_value=(True, 10.0, 0)
    ) as mock_is_shallow_repository_with_details:
        assert CIVisibilityGitClient._is_shallow_repository(cwd="/path/to/repo") is True
        mock_is_shallow_repository_with_details.assert_called_once_with(cwd="/path/to/repo")


def test_is_shallow_repository_false():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._is_shallow_repository_with_details", return_value=(False, 10.0, 128)
    ) as mock_is_shallow_repository_with_details:
        assert CIVisibilityGitClient._is_shallow_repository(cwd="/path/to/repo") is False
        mock_is_shallow_repository_with_details.assert_called_once_with(cwd="/path/to/repo")


def test_unshallow_repository_local_head():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch("ddtrace.internal.ci_visibility.git_client.extract_commit_sha", return_value="myfakesha"):
            with mock.patch("ddtrace.ext.git._git_subprocess_cmd_with_details") as mock_git_subprocess_cmd_with_details:
                CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                mock_git_subprocess_cmd_with_details.assert_called_once_with(
                    "fetch",
                    '--shallow-since="1 month ago"',
                    "--update-shallow",
                    "--filter=blob:none",
                    "--recurse-submodules=no",
                    "origin",
                    "myfakesha",
                    cwd="/path/to/repo",
                )


def test_unshallow_repository_upstream():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_local_head",
            side_effect=ValueError,
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.git_client._extract_upstream_sha", return_value="myupstreamsha"
            ):
                with mock.patch(
                    "ddtrace.ext.git._git_subprocess_cmd_with_details"
                ) as mock_git_subprocess_cmd_with_details:
                    CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                    mock_git_subprocess_cmd_with_details.assert_called_once_with(
                        "fetch",
                        '--shallow-since="1 month ago"',
                        "--update-shallow",
                        "--filter=blob:none",
                        "--recurse-submodules=no",
                        "origin",
                        "myupstreamsha",
                        cwd="/path/to/repo",
                    )


def test_unshallow_repository_full():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("origin", "", 100, 0),
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_local_head",
            side_effect=ValueError,
        ):
            with mock.patch(
                "ddtrace.internal.ci_visibility.git_client.CIVisibilityGitClient._unshallow_repository_to_upstream",
                side_effect=ValueError,
            ):
                with mock.patch(
                    "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=_GitSubprocessDetails("", "", 0, 0)
                ) as mock_git_subprocess_cmd_with_details:
                    CIVisibilityGitClient._unshallow_repository(cwd="/path/to/repo")
                    mock_git_subprocess_cmd_with_details.assert_called_once_with(
                        "fetch",
                        '--shallow-since="1 month ago"',
                        "--update-shallow",
                        "--filter=blob:none",
                        "--recurse-submodules=no",
                        "origin",
                        cwd="/path/to/repo",
                    )


def test_unshallow_respository_cant_get_remote():
    with mock.patch(
        "ddtrace.internal.ci_visibility.git_client._extract_clone_defaultremotename_with_details",
        return_value=_GitSubprocessDetails("", "", 10, 125),
    ):
        with mock.patch("ddtrace.ext.git._git_subprocess_cmd") as mock_git_subprocess_command:
            CIVisibilityGitClient._unshallow_repository()
            mock_git_subprocess_command.assert_not_called()


def test_encoder_pack_payload():
    packed_payload = CIVisibilityEncoderV01._pack_payload(
        {"string_key": [1, {"unicode_key": "string_value"}, "unicode_value", {"string_key": "unicode_value"}]}
    )
    assert (
        packed_payload == b"\x81\xaastring_key\x94\x01\x81\xabunicode_key\xacstring_value"
        b"\xadunicode_value\x81\xaastring_key\xadunicode_value"
    )


class TestFetchTestsToSkip:
    @pytest.fixture(scope="function")
    def mock_civisibility(self):
        with mock.patch.object(CIVisibility, "__init__", return_value=None):
            _civisibility = CIVisibility()
            _civisibility._api_key = "notanapikey"
            _civisibility._dd_site = "notdatadog.notcom"
            _civisibility._service = "test-service"
            _civisibility._git_client = None
            _civisibility._requests_mode = REQUESTS_MODE.AGENTLESS_EVENTS
            _civisibility._tags = {
                ci.git.REPOSITORY_URL: "test_repo_url",
                ci.git.COMMIT_SHA: "testcommitsssshhhaaaa1234",
            }
            _civisibility._configurations = {
                "os.architecture": "arm64",
                "os.platform": "PlatForm",
                "os.version": "9.8.a.b",
                "runtime.name": "RPython",
                "runtime.version": "11.5.2",
            }
            _civisibility._git_client = mock.MagicMock(spec=CIVisibilityGitClient)
            _civisibility._git_client.wait_for_metadata_upload_status.return_value = METADATA_UPLOAD_STATUS.SUCCESS

            yield _civisibility
            CIVisibility._test_suites_to_skip = None
            CIVisibility._tests_to_skip = defaultdict(list)

    @pytest.fixture(scope="class", autouse=True)
    def _test_context_manager(self):
        with mock.patch("ddtrace.ext.ci._get_runtime_and_os_metadata"), mock.patch("json.dumps", return_value=""):
            yield

    def test_fetch_tests_to_skip_test_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body=textwrap.dedent(
                    """{
                        "data": [
                            {
                                "id": "123456789",
                                "type": "test",
                                "attributes": {
                                    "configurations": {
                                        "test.bundle": "testbundle"
                                    },
                                    "name": "test_name_1",
                                    "suite": "test_suite_1.py"
                                }
                            },
                            {
                                "id": "987654321",
                                "type": "test",
                                "attributes": {
                                    "configurations": {
                                        "test.bundle": "testpackage/testbundle"
                                    },
                                    "name": "test_name_2",
                                    "suite": "test_suite_2.py"
                                }
                            }
                        ]
                    }"""
                ),
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(TEST)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {
                "testbundle/test_suite_1.py": ["test_name_1"],
                "testpackage/testbundle/test_suite_2.py": ["test_name_2"],
            }

    def test_fetch_tests_to_skip_suite_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body=textwrap.dedent(
                    """{
                        "data": [
                            {
                                "id": "34640cc7ce80c01e",
                                "type": "suite",
                                "attributes": {
                                    "configurations": {
                                        "test.bundle": "testbundle"
                                    },
                                    "suite": "test_module_1.py"
                                }
                            },
                            {
                                "id": "239fa7de754db779",
                                "type": "suite",
                                "attributes": {
                                    "configurations": {
                                        "test.bundle": "testpackage/testbundle"
                                    },
                                    "suite": "test_suite_2.py"
                                }
                            }
                        ]
                    }"""
                ),
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == [
                "testbundle/test_module_1.py",
                "testpackage/testbundle/test_suite_2.py",
            ]
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_no_data_test_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body="{}",
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(TEST)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_no_data_suite_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body="{}",
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_data_is_none_test_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body='{"data": null}',
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(TEST)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_data_is_none_suite_level(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body='{"data": null}',
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_bad_json(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body="{ this is not valid JSON { ",
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_tests_to_skip_bad_response(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=500,
                body="Internal server error",
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_test_to_skip_invalid_data_missing_key(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body='{"data": [{"somekey": "someval"}]}',
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_test_to_skip_invalid_data_type_error(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body=textwrap.dedent(
                    """{
                    "data": [                            {
                        "id": "12345",
                        "type": "suite",
                        "attributes": {
                            "configurations": {
                                "test.bundle": "2"
                            },
                            "suite": 1
                        }
                    }]}""",
                ),
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}

    def test_fetch_test_to_skip_invalid_data_attribute_error(self, mock_civisibility):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            return_value=Response(
                status=200,
                body=textwrap.dedent(
                    """{
                    "data": [                            {
                        "id": "12345",
                        "type": "suite",
                        "attributes": {
                            "configurations": {
                                "test.bundle": 2
                            },
                            "suite": "1"
                        }
                    }]}""",
                ),
            ),
        ):
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            mock_civisibility._fetch_tests_to_skip(SUITE)
            assert mock_civisibility._test_suites_to_skip == []
            assert mock_civisibility._tests_to_skip == {}


def test_fetch_tests_to_skip_custom_configurations():
    with override_env(
        dict(
            DD_API_KEY="foobar.baz",
            DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            DD_CIVISIBILITY_ITR_ENABLED="1",
            DD_TAGS="test.configuration.disk:slow,test.configuration.memory:low",
            DD_SERVICE="test-service",
            DD_ENV="test-env",
        )
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features", return_value=(True, True)
    ), mock.patch.multiple(
        CIVisibilityGitClient,
        _get_repository_url=classmethod(lambda *args, **kwargs: "git@github.com:TestDog/dd-test-py.git"),
        _is_shallow_repository=classmethod(lambda *args, **kwargs: False),
        _get_latest_commits=classmethod(lambda *args, **kwwargs: ["latest1", "latest2"]),
        _search_commits=classmethod(lambda *args: ["latest1", "searched1", "searched2"]),
        _get_filtered_revisions=classmethod(lambda *args, **kwargs: "revision1\nrevision2"),
        _upload_packfiles=classmethod(lambda *args, **kwargs: None),
    ), mock.patch(
        "ddtrace.ext.ci._get_runtime_and_os_metadata",
        return_value={
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        },
    ), mock.patch(
        "ddtrace.ext.ci.tags",
        return_value={
            "git.repository_url": "git@github.com:TestDog/dd-test-py.git",
            "git.commit.sha": "mytestcommitsha1234",
        },
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder._do_request",
        return_value=Response(
            status=200,
            body='{"data": []}',
        ),
    ) as mock_do_request:
        with mock.patch(
            "ddtrace.internal.ci_visibility.git_client._build_git_packfiles_with_details"
        ) as mock_build_packfiles:
            mock_build_packfiles.return_value.__enter__.return_value = "myprefix", _GitSubprocessDetails("", "", 10, 0)
            ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
            CIVisibility.enable(service="test-service")

            expected_data_arg = json.dumps(
                {
                    "data": {
                        "type": "test_params",
                        "attributes": {
                            "service": "test-service",
                            "env": "test-env",
                            "repository_url": "git@github.com:TestDog/dd-test-py.git",
                            "sha": "mytestcommitsha1234",
                            "configurations": {
                                "os.architecture": "testarch64",
                                "os.platform": "Not Actually Linux",
                                "os.version": "1.2.3-test",
                                "runtime.name": "CPythonTest",
                                "runtime.version": "1.2.3",
                                "custom": {"disk": "slow", "memory": "low"},
                            },
                            "test_level": "test",
                        },
                    }
                }
            )

            mock_do_request.assert_called_once_with(
                "POST",
                "https://api.datadoghq.com/api/v2/ci/tests/skippable",
                expected_data_arg,
                {"dd-api-key": "foobar.baz", "Content-Type": "application/json"},
            )
            CIVisibility.disable()
