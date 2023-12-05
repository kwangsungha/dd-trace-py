# coding: utf-8
import os
import logging
from ddtrace.internal.accupath.node_info import NodeInfo, ROOT_NODE_ID, ROOT_NODE_REQUEST_OUT_TIME, PARENT_NODE_ID, PARENT_NODE_REQUEST_OUT_TIME

from ddsketch import LogCollapsingLowestDenseDDSketch
from ddsketch.pb.proto import DDSketchProto
from ddtrace.internal import core
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.forksafe import Lock

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..writer import _human_size

# Protobuff
from ddtrace.internal.accupath.payload_pb2 import DataPathAPIPayload
from ddtrace.internal.accupath.payload_pb2 import NodeID
from ddtrace.internal.accupath.payload_pb2 import EdgeType
from ddtrace.internal.accupath.payload_pb2 import EdgeID
from ddtrace.internal.accupath.payload_pb2 import PathwayInfo
from ddtrace.internal.accupath.payload_pb2 import PathwayStats
from ddtrace.internal.accupath.payload_pb2 import Paths
from ddtrace.internal.accupath.payload_pb2 import Latencies
from ddtrace.internal.accupath.payload_pb2 import PathDirection


log = get_logger(f"accupath.{__name__}")
log.setLevel(logging.DEBUG)


# Quick Fix Constants
ACCUPATH_BASE_URL = "https://trace-internal.agent.datad0g.com"
ACCUPATH_TIMEOUT = 10
ACCUPATH_ENDPOINT = "/api/v0.2/datapaths"
ACCUPATH_COLLECTION_DURATION = 10  # 10 seconds?

class _AccuPathProcessor(PeriodicService):
    def __init__(self, interval=10):
        super().__init__(interval=interval)
        self._interval = interval
        self._bucket_size_ns = int(interval * 1e9)  # type: int
        self._lock = Lock()
        self._counter = 0
        self.start()

    def set_metrics_bucket(self, buckets):
        self._buckets = buckets

    def add_bucket_data(
            self,
            items
        ):
        """
        Add the data into buckets
        """
        try:
            with self._lock:
                for time_index, path_key, metric_name, metric_value in items:
                    bucket_time_ns = time_index - (time_index % self._bucket_size_ns)
                    stats = self._buckets[bucket_time_ns].pathway_stats[path_key]
                    if hasattr(stats, metric_name):
                        getattr(stats, metric_name).add(metric_value)
                    log.debug("Added bucket entry")
        except Exception as e:
            log.debug("accupath - error", exc_info=True)
        log.debug("Added bucket data")

    def periodic(self):
        try:
            log.debug("flushing stats")
            self._flush_stats()
            log.debug("flushed stats")
        except Exception as e:
            log.debug("accupath - error _flush_stats", exc_info=True)

    def _flush_stats(self):
        headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
        to_del = set()
        with self._lock:
            for bucket_time, bucket in self._buckets.items():
                for path_info_key, actual_bucket in bucket.pathway_stats.items():
                    payload=None
                    try:
                        payload = generate_payload_v0(
                            bucket_start_time=bucket_time,
                            bucket_duration=ACCUPATH_COLLECTION_DURATION,
                            current_node_info = NodeInfo.from_local_env(),
                            root_node_info = path_info_key.root_node_info,
                            path_key_info = path_info_key,
                            pathway_stat_bucket = actual_bucket
                            )
                    except Exception as e:
                        log.debug("Ran into an issue creating payloads", exc_info=True)
                        return 
                    try:
                        conn = self._conn()
                        conn.request("POST", ACCUPATH_ENDPOINT, payload, headers)
                        resp = get_connection_response(conn)
                    except Exception:
                        raise
                    else:
                        if resp.status == 404:
                            log.error("Error sending data, response is: %s and conn is: %s" % (resp.__dict__, conn.__dict__))
                            return
                        elif resp.status >= 400:
                            log.error(
                                "failed to send data stream stats payload, %s (%s) (%s) response from Datadog agent at %s",
                                resp.status,
                                resp.reason,
                                resp.read(),
                                ACCUPATH_BASE_URL
                            )
                        else:
                            log.debug("accupath sent %s to %s", _human_size(len(payload)), ACCUPATH_BASE_URL)
                            to_del.add(bucket_time)
            for b_t in to_del:
                if b_t in self._buckets:
                    del self._buckets[b_t]

    def _conn(self):
        conn = get_connection(ACCUPATH_BASE_URL, ACCUPATH_TIMEOUT)
        return conn


def generate_parent_service_node_info_v0():
    return (str(core.get_item(PARENT_NODE_ID) or ""), int(core.get_item(PARENT_NODE_REQUEST_OUT_TIME) or 0))


def generate_payload_v0(
        bucket_start_time=None,
        bucket_duration=None,
        root_node_info=None,
        current_node_info=None,
        path_key_info=None,
        pathway_stat_bucket=None
    ):
    # Protobuf
    # https://protobuf.dev/getting-started/pythontutorial/
    """
    to regenerate the proto:
    protoc -I=/Users/accupath/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/ \
        --python_out=/Users/accupath/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        --pyi_out=/Users/accupath/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        /Users/accupath/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/payload.proto
    """
    root_node_hash = root_node_info.to_hash()
    current_node_hash = current_node_info.to_hash()

    log.info("accupath - generating payload")

    # ADD THIS NODE TO THE PAYLOAD
    node = NodeID()
    node.service = current_node_info.service
    node.env = current_node_info.env
    node.host = current_node_info.hostname

    # REPRESENT THIS EDGE
    edge = EdgeID()
    edge.type = EdgeType.HTTP
    edge.name = path_key_info.resource_name

    # REPRESENT PATHWAY
    pathway = PathwayInfo()
    pathway.root_service_hash = root_node_hash
    pathway.node_hash = path_key_info.node_hash
    pathway.upstream_pathway_hash = path_key_info.request_pathway_id
    pathway.downstream_pathway_hash = path_key_info.response_pathway_id
    pathway_string = " -> ".join([
        f"({root_node_info.service}, {root_node_info.env})",
        f"Time Bucket: {bucket_start_time}",
        f"upstream: {path_key_info.request_pathway_id}",
        f"current: {path_key_info.node_hash} - {current_node_info.service}, {current_node_info.env}",
        f"downstream: {path_key_info.response_pathway_id}",
        f"resource: {path_key_info.resource_name}"
    ])
    log.debug(f"accupath payload -  {pathway_string}")

    #  LATENCIES
    response_latencies = Latencies()
    request_latencies = Latencies()

    root_to_request_in_latency_sketch = pathway_stat_bucket.root_to_request_in_latency
    root_to_request_in_latency_proto = DDSketchProto.to_proto(root_to_request_in_latency_sketch)

    root_to_request_in_latency_errors_sketch = pathway_stat_bucket.root_to_request_in_latency_errors
    root_to_request_in_latency_errors_proto = DDSketchProto.to_proto(root_to_request_in_latency_errors_sketch)

    root_to_request_out_latency_sketch = pathway_stat_bucket.root_to_request_out_latency
    root_to_request_out_latency_proto = DDSketchProto.to_proto(root_to_request_out_latency_sketch)

    root_to_request_out_latency_errors_sketch = pathway_stat_bucket.root_to_request_out_latency_errors
    root_to_request_out_latency_errors_proto = DDSketchProto.to_proto(root_to_request_out_latency_errors_sketch)

    root_to_response_in_latency_sketch = pathway_stat_bucket.root_to_response_in_latency
    root_to_response_in_latency_proto = DDSketchProto.to_proto(root_to_response_in_latency_sketch)

    root_to_response_in_latency_errors_sketch = pathway_stat_bucket.root_to_response_in_latency_errors
    root_to_response_in_latency_errors_proto = DDSketchProto.to_proto(root_to_response_in_latency_errors_sketch)

    root_to_response_out_latency_sketch = pathway_stat_bucket.root_to_response_out_latency
    root_to_response_out_latency_proto = DDSketchProto.to_proto(root_to_response_out_latency_sketch)

    root_to_response_out_latency_errors_sketch = pathway_stat_bucket.root_to_response_out_latency_errors
    root_to_response_out_latency_errors_proto = DDSketchProto.to_proto(root_to_response_out_latency_errors_sketch)

    response_latencies.latency_in = root_to_response_in_latency_proto.SerializeToString()
    response_latencies.error_latency_in = root_to_response_in_latency_errors_proto.SerializeToString()
    response_latencies.latency_out = root_to_response_out_latency_proto.SerializeToString()
    response_latencies.error_latency_out = root_to_response_out_latency_errors_proto.SerializeToString()
    request_latencies.latency_in = root_to_request_in_latency_proto.SerializeToString()
    request_latencies.error_latency_in = root_to_request_in_latency_errors_proto.SerializeToString()
    request_latencies.latency_out = root_to_request_out_latency_proto.SerializeToString()
    request_latencies.error_latency_out = root_to_request_out_latency_errors_proto.SerializeToString()


    # PATHWAY STATS
    request_pathway_stats = PathwayStats()
    request_pathway_stats.edge.CopyFrom(edge)
    request_pathway_stats.info.CopyFrom(pathway)
    request_pathway_stats.latencies.CopyFrom(request_latencies)

    response_pathway_stats = PathwayStats()
    response_pathway_stats.edge.CopyFrom(edge)
    response_pathway_stats.info.CopyFrom(pathway)
    response_pathway_stats.latencies.CopyFrom(response_latencies)

    # PATH DIRECTIONS
    request_path_direction = PathDirection.REQUEST
    response_path_direction = PathDirection.RESPONSE

    """
    # very first attempt
    request_latency_sketch = pathway_stat_bucket.request_latency
    response_latency_sketch = pathway_stat_bucket.response_latency
    request_latency = DDSketchProto.to_proto(request_latency_sketch).SerializeToString()
    response_latency = DDSketchProto.to_proto(response_latency_sketch).SerializeToString()
    pathway_stats.request_latency = request_latency
    pathway_stats.response_latency = response_latency
    """

    # PATHS info
    request_path = Paths()
    request_path.start = bucket_start_time
    request_path.duration = bucket_duration
    request_path.direction = request_path_direction
    request_path.stats.append(request_pathway_stats)

    response_path = Paths()
    response_path.start = bucket_start_time
    response_path.duration = bucket_duration
    response_path.direction = response_path_direction
    response_path.stats.append(response_pathway_stats)

    # PAYLOAD
    payload = DataPathAPIPayload()
    payload.node.CopyFrom(node)
    payload.paths.append(request_path)
    payload.paths.append(response_path)

    return payload.SerializeToString()


_processor_singleton = _AccuPathProcessor()
