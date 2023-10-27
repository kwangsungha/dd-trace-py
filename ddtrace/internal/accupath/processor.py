# coding: utf-8
import os
from ddtrace.internal.accupath.node_info import NodeInfo, ROOT_NODE_ID, ROOT_NODE_REQUEST_OUT_TIME, PARENT_NODE_ID, PARENT_NODE_REQUEST_OUT_TIME
from ddtrace.internal.accupath.path_info import EdgeInfo, PathInfo

from ddsketch import LogCollapsingLowestDenseDDSketch
from ddsketch.pb.proto import DDSketchProto
from ddtrace.internal import core
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.forksafe import Lock

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..writer import _human_size
from ddtrace.internal.utils.fnv import fnv1_64

# Protobuff
from ddtrace.internal.accupath.payload_pb2 import DataPathAPIPayload
from ddtrace.internal.accupath.payload_pb2 import NodeID
from ddtrace.internal.accupath.payload_pb2 import EdgeType
from ddtrace.internal.accupath.payload_pb2 import EdgeID
from ddtrace.internal.accupath.payload_pb2 import PathwayInfo
from ddtrace.internal.accupath.payload_pb2 import PathwayStats
from ddtrace.internal.accupath.payload_pb2 import Paths


log = get_logger(__name__)


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
        with self._lock:
            for time_index, path_key, metric_name, metric_value in items:
                bucket_time_ns = time_index - (time_index % self._bucket_size_ns)
                stats = self._buckets[bucket_time_ns].pathway_stats[path_key]
                if hasattr(stats, metric_name):
                    getattr(stats, metric_name).add(metric_value)

    def periodic(self):
        self._flush_stats()

    def _flush_stats(self):
        headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
        with self._lock:
            for bucket_time, bucket in self._buckets.items():
                for path_info_key, bucket in bucket.pathway_stats.items():
                    payload = generate_payload_v0(
                        bucket_start_time=bucket_time,
                        bucket_duration=ACCUPATH_COLLECTION_DURATION,
                        current_node_info = NodeInfo.from_local_env(),
                        root_node_info = NodeInfo.get_root_node_info(),
                        path_key_info = path_info_key,
                        pathway_stat_bucket = bucket
                        )
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


    # ADD THIS NODE TO THE PAYLOAD
    node = NodeID()
    node.service = current_node_info.service
    node.env = current_node_info.env
    node.host = current_node_info.hostname

    # REPRESENT THIS EDGE
    edge = EdgeID()
    edge.type = EdgeType.HTTP
    edge.name = "foo"  # What are the requirements for the name?

    # REPRESENT PATHWAY
    pathway = PathwayInfo()
    pathway.root_service_hash = root_node_hash
    pathway.node_hash = current_node_hash
    pathway.upstream_pathway_hash = path_key_info.request_pathway_id
    pathway.downstream_pathway_hash = path_key_info.response_pathway_id

    # PATHWAY STATS
    pathway_stats = PathwayStats()
    pathway_stats.info.CopyFrom(pathway)
    pathway_stats.edge.CopyFrom(edge)
    request_latency_sketch = pathway_stat_bucket.request_latency
    response_latency_sketch = pathway_stat_bucket.response_latency
    request_latency = DDSketchProto.to_proto(request_latency_sketch).SerializeToString()
    response_latency = DDSketchProto.to_proto(response_latency_sketch).SerializeToString()
    pathway_stats.request_latency = request_latency
    pathway_stats.response_latency = response_latency

    # PATHS info
    paths = Paths()
    paths.start = bucket_start_time
    paths.duration = bucket_duration
    paths.stats.append(pathway_stats)

    # PAYLOAD
    payload = DataPathAPIPayload()
    payload.node.CopyFrom(node)
    payload.paths.CopyFrom(paths)

    return payload.SerializeToString()


def report_information_to_backend():
    payload = generate_payload_v0()
    headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
    try:
        conn = get_connection(ACCUPATH_BASE_URL, ACCUPATH_TIMEOUT)
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
            log.debug("sent %s to %s", _human_size(len(payload)), ACCUPATH_BASE_URL)


_processor_singleton = _AccuPathProcessor()
