import time
from collections import defaultdict
from typing import NamedTuple
from typing import Any, DefaultDict
from ddtrace.internal.logger import get_logger

from ddtrace.internal import core
from ddtrace.internal.accupath.path_info import PathKey, PathInfo, generate_response_pathway_id, generate_request_pathway_id
from ddtrace.internal.accupath.processor import _processor_singleton as _accupath_processor

from ddsketch import LogCollapsingLowestDenseDDSketch

log = get_logger(__name__)


class PathwayStats:
    """Aggregated pathway statistics."""

    __slots__ = (
        "request_latency",
        "response_latency",
    )

    def __init__(self):
        self.request_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.response_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)


Bucket = NamedTuple(
    "Bucket",
    [
        ("pathway_stats", DefaultDict[PathKey, PathwayStats]),
    ],
)

import numbers
def _checkpoint_diff(metric_record_id, observation_coordinates, dispatch_event_id, *args, **kwargs):
    val1 = core.get_item(observation_coordinates[0])
    val2 = core.get_item(observation_coordinates[1])

    log.debug(f"accupath - _checkpoint_diff called with metric id: {metric_record_id} and coordinates with name/values of: {observation_coordinates[0]}/{val1} and {observation_coordinates[1]}/{val2}")
    if isinstance(val1, numbers.Number) and isinstance(val2, numbers.Number):
        core.set_item(metric_record_id, val2-val1)
    
    if dispatch_event_id:
        core.dispatch(dispatch_event_id, [])


def _submit_service_metrics(*args, **kwargs):
    log.debug("accupath - _submit_service_metrics called")
    root_request_out_time = core.get_item("accupath.service.root_out")

    request_in_time = core.get_item("accupath.service.request_in")
    request_out_time = core.get_item("accupath.service.request_out")
    response_in_time = core.get_item("accupath.service.response_in")
    # response_out_time = core.get_item("accupath.service.response_out")

    request_pathway_id = core.get_item("accupath.service.request_path_info")
    response_pathway_id = core.get_item("accupath.service.response_path_info")

    path_key = PathKey(request_pathway_id=request_pathway_id, response_pathway_id=response_pathway_id)
    to_submit = [
        (request_in_time, path_key, "request_latency", (request_in_time - root_request_out_time)),
        (request_in_time, path_key, "response_latency", (response_in_time - request_out_time)),
    ]
    _accupath_processor.add_bucket_data(to_submit)


_buckets = defaultdict(
    lambda: Bucket(defaultdict(PathwayStats))
)

_accupath_processor.set_metrics_bucket(_buckets)