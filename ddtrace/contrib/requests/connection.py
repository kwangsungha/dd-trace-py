from typing import Optional  # noqa:F401

import ddtrace
from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.settings.asm import config as asm_config
from ddtrace.internal import core

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...internal.compat import parse
from ...internal.logger import get_logger
from ...internal.schema import schematize_url_operation
from ...internal.utils import get_argument_value
from ...propagation.http import HTTPPropagator
from .. import trace_utils


log = get_logger(__name__)


def _extract_hostname_and_path(uri):
    # type: (str) -> str
    parsed_uri = parse.urlparse(uri)
    hostname = parsed_uri.hostname
    try:
        if parsed_uri.port is not None:
            hostname = "%s:%s" % (hostname, str(parsed_uri.port))
    except ValueError:
        # ValueError is raised in PY>3.5 when parsed_uri.port < 0 or parsed_uri.port > 65535
        hostname = "%s:?" % (hostname,)
    return hostname, parsed_uri.path


def _extract_query_string(uri):
    # type: (str) -> Optional[str]
    start = uri.find("?") + 1
    if start == 0:
        return None

    end = len(uri)
    j = uri.rfind("#", 0, end)
    if j != -1:
        end = j

    if end <= start:
        return None

    return uri[start:end]


def _wrap_request(func, instance, args, kwargs):
    """This function wraps `request.request`, which includes all request verbs like `request.get` and `request.post`.
    IAST needs to wrap this function because `Session.send` is too late, as we require the raw arguments of the request.
    """
    if asm_config._iast_enabled:
        from ddtrace.appsec._iast.taint_sinks.ssrf import _iast_report_ssrf

        _iast_report_ssrf(func, *args, **kwargs)
    return func(*args, **kwargs)


def _wrap_send(func, instance, args, kwargs):
    """Trace the `Session.send` instance method"""
    # TODO[manu]: we already offer a way to provide the Global Tracer
    # and is ddtrace.tracer; it's used only inside our tests and can
    # be easily changed by providing a TracingTestCase that sets common
    # tracing functionalities.
    tracer = getattr(instance, "datadog_tracer", ddtrace.tracer)

    # skip if tracing is not enabled
    if not tracer.enabled:
        return func(*args, **kwargs)

    request = get_argument_value(args, kwargs, 0, "request")
    if not request:
        return func(*args, **kwargs)

    url = trace_utils._sanitized_url(request.url)
    method = ""
    if request.method is not None:
        method = request.method.upper()
    hostname, path = _extract_hostname_and_path(url)
    host_without_port = hostname.split(":")[0] if hostname is not None else None

    cfg = config.get_from(instance)
    service = None
    if cfg["split_by_domain"] and hostname:
        service = hostname
    if service is None:
        service = cfg.get("service", None)
    if service is None:
        service = cfg.get("service_name", None)
    if service is None:
        service = trace_utils.ext_service(None, config.requests)

    operation_name = schematize_url_operation("requests.request", protocol="http", direction=SpanDirection.OUTBOUND)
    with tracer.trace(operation_name, service=service, resource=f"{method} {path}", span_type=SpanTypes.HTTP) as span:
        span.set_tag_str(COMPONENT, config.requests.integration_name)

        # set span.kind to the type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(SPAN_MEASURED_KEY)

        # Configure trace search sample rate
        # DEV: analytics enabled on per-session basis
        cfg = config.get_from(instance)
        analytics_enabled = cfg.get("analytics_enabled")
        if analytics_enabled:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, cfg.get("analytics_sample_rate", True))

        # propagate distributed tracing headers
        if cfg.get("distributed_tracing"):
            HTTPPropagator.inject(span.context, request.headers)

        response = response_headers = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            try:
                status = None
                if response is not None:
                    status = response.status_code
                    # Storing response headers in the span.
                    # Note that response.headers is not a dict, but an iterable
                    # requests custom structure, that we convert to a dict
                    response_headers = dict(getattr(response, "headers", {}))
                    core.dispatch("http.response.header.extraction", [response_headers, response.status_code])

                trace_utils.set_http_meta(
                    span,
                    config.requests,
                    request_headers=request.headers,
                    response_headers=response_headers,
                    method=method,
                    url=request.url,
                    target_host=host_without_port,
                    status_code=status,
                    query=_extract_query_string(url),
                )
            except Exception:
                log.debug("requests: error adding tags", exc_info=True)
