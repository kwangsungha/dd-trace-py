import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401

import botocore.client
import botocore.exceptions

from ddtrace import config
from ddtrace import Span  # noqa:F401
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ....ext import SpanTypes
from ....ext import http
from ....internal.compat import time_ns
from ....internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ....internal.logger import get_logger
from ....pin import Pin  # noqa:F401
from ....propagation.http import HTTPPropagator
from ..patch import extract_DD_context
from ..patch import extract_trace_context_json
from ..patch import get_pathway
from ..patch import get_queue_name
from ..patch import schematize_cloud_messaging_operation
from ..patch import schematize_service_name
from ..patch import set_patched_api_call_span_tags
from ..patch import set_response_metadata_tags


log = get_logger(__name__)


def _encode_data(trace_data):
    """
    This method exists solely to enable us to patch the value in tests, since
    moto doesn't support auto-encoded SNS -> SQS as binary with RawDelivery enabled
    """
    return json.dumps(trace_data)


def get_topic_arn(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the topic given the params
    """
    sns_arn = params["TopicArn"]
    return sns_arn


def inject_trace_data_to_message_attributes(trace_data, entry, endpoint_service=None):
    # type: (Dict[str, str], Dict[str, Any], Optional[str]) -> None
    """
    :trace_data: trace headers and DSM pathway to be stored in the entry's MessageAttributes
    :entry: an SQS or SNS record
    :endpoint_service: endpoint of message, "sqs" or "sns"

    Inject trace headers and DSM info into the SQS or SNS record's MessageAttributes
    """
    if "MessageAttributes" not in entry:
        entry["MessageAttributes"] = {}
    # Max of 10 message attributes.
    if len(entry["MessageAttributes"]) < 10:
        if endpoint_service == "sqs":
            # Use String since changing this to Binary would be a breaking
            # change as other tracers expect this to be a String.
            entry["MessageAttributes"]["_datadog"] = {"DataType": "String", "StringValue": _encode_data(trace_data)}
        elif endpoint_service == "sns":
            # Use Binary since SNS subscription filter policies fail silently
            # with JSON strings https://github.com/DataDog/datadog-lambda-js/pull/269
            # AWS will encode our value if it sees "Binary"
            entry["MessageAttributes"]["_datadog"] = {"DataType": "Binary", "BinaryValue": _encode_data(trace_data)}
        else:
            log.warning("skipping trace injection, endpoint is not SNS or SQS")
    else:
        # In the event a record has 10 or more msg attributes we cannot add our _datadog msg attribute
        log.warning("skipping trace injection, max number (10) of MessageAttributes exceeded")


def inject_trace_to_sqs_or_sns_batch_message(params, span, endpoint_service=None, pin=None, data_streams_enabled=False):
    # type: (Any, Span, Optional[str], Optional[Pin], Optional[bool]) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    :pin: patch info for the botocore client
    :data_streams_enabled: boolean for whether data streams monitoring is enabled

    Inject trace headers and DSM info into MessageAttributes for all SQS or SNS records inside a batch
    """

    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    # An entry here is an SNS or SQS record, and depending on how it was published,
    # it could either show up under Entries (in case of PutRecords),
    # or PublishBatchRequestEntries (in case of PublishBatch).
    entries = params.get("Entries", params.get("PublishBatchRequestEntries", []))
    for entry in entries:
        if data_streams_enabled:
            dsm_identifier = None
            if endpoint_service == "sqs":
                dsm_identifier = get_queue_name(params)
            elif endpoint_service == "sns":
                dsm_identifier = get_topic_arn(params)
            trace_data[PROPAGATION_KEY_BASE_64] = get_pathway(pin, endpoint_service, dsm_identifier, span)
        inject_trace_data_to_message_attributes(trace_data, entry, endpoint_service)


def inject_trace_to_sqs_or_sns_message(params, span, endpoint_service=None, pin=None, data_streams_enabled=False):
    # type: (Any, Span, Optional[str], Optional[Pin], Optional[bool]) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    :pin: patch info for the botocore client
    :data_streams_enabled: boolean for whether data streams monitoring is enabled

    Inject trace headers and DSM info into MessageAttributes for the SQS or SNS record
    """
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    if data_streams_enabled:
        dsm_identifier = None
        if endpoint_service == "sqs":
            dsm_identifier = get_queue_name(params)
        elif endpoint_service == "sns":
            dsm_identifier = get_topic_arn(params)

        trace_data[PROPAGATION_KEY_BASE_64] = get_pathway(pin, endpoint_service, dsm_identifier, span)

    inject_trace_data_to_message_attributes(trace_data, params, endpoint_service)


def patched_sqs_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    message_received = False
    func_run = False
    func_run_err = None

    if config.botocore["distributed_tracing"]:
        if endpoint_name == "sqs" and operation == "ReceiveMessage":
            # Ensure we have Datadog MessageAttribute enabled
            if "MessageAttributeNames" not in params:
                params.update({"MessageAttributeNames": ["_datadog"]})
            elif "_datadog" not in params["MessageAttributeNames"]:
                params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})

            # Ensure we have AWS attribute enabl
            # ed for interoperability with dd-trace-java aws propagation
            if "AttributeNames" not in params:
                params.update({"AttributeNames": ["AWSTraceHeader"]})
            elif "AWSTraceHeader" not in params["AttributeNames"]:
                params.update({"AttributeNames": list(params["AttributeNames"]) + ["AWSTraceHeader"]})

            try:
                start_ns = time_ns()
                func_run = True
                # run the function before in order to extract possible parent context before starting span
                result = original_func(*args, **kwargs)
            except Exception as e:
                func_run_err = e
            if result is not None and "Messages" in result and len(result["Messages"]) >= 1:
                message_received = True
                ctx = extract_DD_context(result["Messages"])

    # we don't want to create a span for empty poll consumer operations
    if (func_run and message_received) or config.empty_poll_enabled or not func_run:
        with pin.tracer.start_span(
            trace_operation,
            service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
            span_type=SpanTypes.HTTP,
            child_of=ctx if ctx is not None else pin.tracer.context_provider.active(),
            activate=True,
        ) as span:
            set_patched_api_call_span_tags(span, instance, args, params, endpoint_name, operation)

            if start_ns is not None:
                span.start_ns = start_ns

            if args:
                if config.botocore["distributed_tracing"]:
                    try:
                        if endpoint_name == "sqs" and operation == "SendMessage":
                            inject_trace_to_sqs_or_sns_message(
                                params,
                                span,
                                endpoint_service=endpoint_name,
                                pin=pin,
                                data_streams_enabled=config._data_streams_enabled,
                            )
                            span.name = schematize_cloud_messaging_operation(
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="sqs",
                                direction=SpanDirection.OUTBOUND,
                            )
                        if endpoint_name == "sqs" and operation == "SendMessageBatch":
                            inject_trace_to_sqs_or_sns_batch_message(
                                params,
                                span,
                                endpoint_service=endpoint_name,
                                pin=pin,
                                data_streams_enabled=config._data_streams_enabled,
                            )
                            span.name = schematize_cloud_messaging_operation(
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="sqs",
                                direction=SpanDirection.OUTBOUND,
                            )
                        if endpoint_name == "sqs" and operation == "ReceiveMessage":
                            span.name = schematize_cloud_messaging_operation(
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="sqs",
                                direction=SpanDirection.INBOUND,
                            )
                    except Exception:
                        log.warning("Unable to inject trace context", exc_info=True)
            try:
                if endpoint_name == "sqs" and operation == "ReceiveMessage" and config._data_streams_enabled:
                    queue_name = get_queue_name(params)

                    if "MessageAttributeNames" not in params:
                        params.update({"MessageAttributeNames": ["_datadog"]})
                    elif "_datadog" not in params["MessageAttributeNames"]:
                        params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})

                    if not func_run:
                        result = original_func(*args, **kwargs)

                    set_response_metadata_tags(span, result)

                    try:
                        if "Messages" in result:
                            for message in result["Messages"]:
                                message_body = None
                                context_json = None

                                if "Body" in message:
                                    try:
                                        message_body = json.loads(message["Body"])
                                    except ValueError:
                                        log.debug("Unable to parse message body, treat as non-json")

                                # try to extract trace context from the request
                                context_json = extract_trace_context_json(message_body)
                                if context_json is None:
                                    log.debug("DataStreams did not handle message: %r", message)

                                pathway = context_json.get(PROPAGATION_KEY_BASE_64, None) if context_json else None
                                ctx = pin.tracer.data_streams_processor.decode_pathway_b64(pathway)
                                ctx.set_checkpoint(["direction:in", "topic:" + queue_name, "type:sqs"], span=span)

                    except Exception:
                        log.debug("Error receiving SQS message with data streams monitoring enabled", exc_info=True)

                    # raise error if it was encountered before the span was started
                    if func_run_err:
                        raise func_run_err

                    return result
            except botocore.exceptions.ClientError as e:
                # `ClientError.response` contains the result, so we can still grab response metadata
                set_response_metadata_tags(span, e.response)

                # If we have a status code, and the status code is not an error,
                #   then ignore the exception being raised
                status_code = span.get_tag(http.STATUS_CODE)
                if status_code and not config.botocore.operations[span.resource].is_error_code(int(status_code)):
                    span._ignore_exception(botocore.exceptions.ClientError)
                raise
