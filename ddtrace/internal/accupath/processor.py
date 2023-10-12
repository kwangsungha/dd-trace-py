# coding: utf-8
import gzip
import os
import struct
import time
import typing
from typing import DefaultDict
from typing import NamedTuple

from ddsketch import LogCollapsingLowestDenseDDSketch
from ddsketch.pb.proto import DDSketchProto
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..periodic import PeriodicService
from ..writer import _human_size
from ddtrace.internal.utils.fnv import fnv1_64


log = get_logger(__name__)

PROPAGATION_KEY = "dd-pathway-ctx"
PROPAGATION_KEY_BASE_64 = "dd-pathway-ctx-base64"


"""
We need to pass:
* root hash
* parent path hash (including this node)
* 
payload = 
{
    node: NodeId : {
        service: ""
        env: ""
        host: ""
    },
    paths: PathwayInfo : {
        start: 0
        duration: 5
        stats: [ PathwayStats: {
            edge:  EdgeId: {
                type: EdgeType: {
                    UNKNOWN|HTTP|GRPC
                },
                name: ""
            },
            info: PathayInfo: {
            },
            request_latency: 0,
            response_latency: 0
        }]
    }
}
"""

"""
We need to pass:
* Path information

Metrics are generated
"""

def inject_context(headers):
    log.debug("teague.bick - attempting to inject accupath headers into %r", headers)
    headers[HTTP_HEADER_ACCUPATH_PATH_ID] = generate_current_path_id_v0(tags=[])

    current_id, current_time = generate_current_service_node_id_v0()
    headers[HTTP_HEADER_ACCUPATH_PARENT_ID] = current_id
    headers[HTTP_HEADER_ACCUPATH_PARENT_TIME] = current_time 

    root_id, root_time = generate_root_node_id_v0()
    headers[HTTP_HEADER_ACCUPATH_ROOT_ID] = root_id 
    headers[HTTP_HEADER_ACCUPATH_ROOT_TIME] = root_time

    log.debug("teague.bick - injected accupath headers into %r", headers)


def generate_current_service_node_id_v0():
    import json
    service = os.environ.get("DD_SERVICE", "unnamed-python-service")
    env = os.environ.get("DD_ENV", "none")
    host = os.environ.get("DD_HOSTNAME", "")

    service_node = dict(
            service=service,
            env=env,
            host=host,
        )

    return (json.dumps(service_node), str(time.time()))


def generate_node_hash_v0(node_info):
    import json
    def get_bytes(s):
        return bytes(s, encoding="utf-8")
    
    if isinstance(node_info, str):
        node_info = json.loads(node_info)

    b = get_bytes(node_info['service']) + get_bytes(node_info['env']) + get_bytes(node_info['host'])
    node_hash = fnv1_64(b)
    return fnv1_64(struct.pack("<Q", node_hash))


def generate_current_path_id_v0(tags=[]):
    from ddtrace.internal import core
    import json
    log.debug("teague.bick - a")
    parent_pathway_hash = int(core.get_item(PARENT_PATHWAY_ID) or 0)
    log.debug("teague.bick - b")

    current_node = generate_current_service_node_id_v0()[0]
    log.debug("teague.bick - c")

    def get_bytes(s):
        return bytes(s, encoding="utf-8")

    b = get_bytes(json.loads(current_node)['service']) + get_bytes(json.loads(current_node)['env']) + get_bytes(json.loads(current_node)['host'])
    log.debug("teague.bick - d")
    for t in tags:
        b += get_bytes(t)
    log.debug("teague.bick - e")
    node_hash = fnv1_64(b)
    log.debug("teague.bick - f")
    result = fnv1_64(struct.pack("<Q", node_hash) + struct.pack("<Q", parent_pathway_hash))
    log.debug("teague.bick - g")
    return result


def generate_root_node_id_v0():
    from ddtrace.internal import core
    return (str(core.get_item(ROOT_NODE_ID) or generate_current_service_node_id_v0()[0]), str(core.get_item(ROOT_NODE_TIME) or time.time()))


def extract_accupath_information(headers):
    extract_root_node_id_v0(headers)
    extract_parent_service_node_id_v0(headers)
    extract_path_info_v0(headers)


def extract_root_node_id_v0(headers):
    from ddtrace.internal import core
    root_node_id_header = headers[HTTP_HEADER_ACCUPATH_ROOT_ID]
    root_node_time = headers[HTTP_HEADER_ACCUPATH_ROOT_TIME]
    core.set_item(ROOT_NODE_TIME, float(root_node_time))
    core.set_item(ROOT_NODE_ID, root_node_id_header)
    log.debug("teague.bick - extracted root id header value: %s", root_node_id_header)
    log.debug("teague.bick - extracted root time header value: %s", root_node_time)


def extract_parent_service_node_id_v0(headers):
    from ddtrace.internal import core
    parent_node_id = headers[HTTP_HEADER_ACCUPATH_PARENT_ID]
    parent_time = headers[HTTP_HEADER_ACCUPATH_PARENT_TIME]
    core.set_item(PARENT_NODE_ID, parent_node_id)
    core.set_item(PARENT_NODE_TIME, float(parent_time))
    log.debug("teague.bick - extracted parent service info of: %r", parent_node_id)
    log.debug("teague.bick - extracted parent service time info of: %r", parent_time)

def extract_path_info_v0(headers):
    from ddtrace.internal import core
    parent_path_id = headers[HTTP_HEADER_ACCUPATH_PATH_ID]
    core.set_item(PARENT_PATHWAY_ID, parent_path_id)

def generate_parent_service_node_info_v0():
    from ddtrace.internal import core
    return (str(core.get_item(PARENT_NODE_ID) or ""), int(core.get_item(PARENT_NODE_TIME) or 0))


def generate_payload_v0():
    # Protobuf 
    # https://protobuf.dev/getting-started/pythontutorial/
    """
    to regenerate the proto:
    protoc -I=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/ \
        --python_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        --pyi_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
        /Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/payload.proto 
    """
    log.debug("teague.bick - starting to generate payload")
    from ddtrace.internal.accupath.payload_pb2 import DataPathAPIPayload
    from ddtrace.internal.accupath.payload_pb2 import NodeID
    from ddtrace.internal.accupath.payload_pb2 import EdgeType
    from ddtrace.internal.accupath.payload_pb2 import EdgeID
    from ddtrace.internal.accupath.payload_pb2 import PathwayInfo
    from ddtrace.internal.accupath.payload_pb2 import PathwayStats
    from ddtrace.internal.accupath.payload_pb2 import Paths
    import json
    log.debug("teague.bick - payload 0")
    now = time.time()
    log.debug("teague.bick - payload 1")
    root_info, root_time = generate_root_node_id_v0()
    log.debug("teague.bick - payload 2")
    node_info = json.loads(generate_current_service_node_id_v0()[0])
    log.debug("teague.bick - payload 3")
    current_node_hash = generate_node_hash_v0(node_info)
    log.debug("teague.bick - payload 4")
    root_node_hash = generate_node_hash_v0(root_info)
    log.debug("teague.bick - payload 5")
    pathway_hash = generate_current_path_id_v0()
    log.debug("teague.bick - payload 6")
    parent_hash, parent_time = generate_parent_service_node_info_v0()

    # ADD THIS NODE TO THE PAYLOAD
    log.debug("teague.bick - payload a")
    node = NodeID()
    node.service = node_info['service']
    node.env = node_info['env']
    node.host = node_info['host']

    # REPRESENT THIS EDGE
    log.debug("teague.bick - payload b")
    edge = EdgeID()
    edge.type = EdgeType.HTTP
    edge.name = "foo"  # What are the requirements for the name?


    # REPRESENT PATHWAY
    log.debug("teague.bick - payload c")
    pathway = PathwayInfo()
    pathway.root_service_hash =root_node_hash 
    pathway.node_hash = current_node_hash 
    pathway.upstream_pathway_hash = pathway_hash
    pathway.downstream_pathway_hash = 0

    # PATHWAY STATS
    log.debug("teague.bick - payload d")
    pathway_stats = PathwayStats()
    pathway_stats.info.CopyFrom(pathway)
    pathway_stats.edge.CopyFrom(edge)
    full_pathway_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
    full_pathway_latency.add(now - int(float(root_time)* 1e3))
    edge_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
    edge_latency.add(now - int(float(parent_time)*1e3))
    from_root_latency = DDSketchProto.to_proto(full_pathway_latency).SerializeToString()
    from_downstream_latency = DDSketchProto.to_proto(edge_latency).SerializeToString()
    pathway_stats.request_latency = from_root_latency
    pathway_stats.response_latency = from_downstream_latency

    # PATHS info
    log.debug("teague.bick - payload e")
    paths = Paths()
    paths.start = int(now - (now%ACCUPATH_COLLECTION_DURATION))
    paths.duration = ACCUPATH_COLLECTION_DURATION
    paths.stats.append(pathway_stats)

    # PAYLOAD
    log.debug("teague.bick - payload f and g")
    payload = DataPathAPIPayload()
    payload.node.CopyFrom(node)
    payload.paths.CopyFrom(paths)

    msg = "teague.bick - checking initialized: %s" % payload.IsInitialized()
    log.debug(msg)
    #log.debug("teague.bick - serializing payload: %s" % payload.__str__())
    return payload.SerializeToString()

def report_information_to_backend():
    log.debug("teague.bick - beginning to report to backend")
    payload = generate_payload_v0()
    log.debug("teague.bick - payload generated")
    headers = {"DD-API-KEY": os.environ.get("DD_API_KEY")}
    log.debug("teague.bick - headers are: %s" % headers)
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


# Quick Fix Constants
ACCUPATH_BASE_URL = "https://trace-internal.agent.datad0g.com"
ACCUPATH_TIMEOUT = 10
ACCUPATH_ENDPOINT = "/api/v0.2/datapaths"
ACCUPATH_COLLECTION_DURATION = 10  # 10 seconds?



# Core Constants
ROOT_NODE_ID = "accupath_root_node_id"
ROOT_NODE_TIME = "accupath_root_node_time"
PARENT_NODE_ID = "accupath_parent_node_id"
PARENT_NODE_TIME = "accupath_parent_node_time"
PARENT_PATHWAY_ID = "accupath_parent_pathway_id"

# Headers
HTTP_HEADER_ACCUPATH_PARENT_ID = "x-datadog-accupath-parent-id"  # Current node hash
HTTP_HEADER_ACCUPATH_PARENT_TIME = "x-datadog-accupath-parent-time"  # Current node hash
HTTP_HEADER_ACCUPATH_PATH_ID = "x-datadog-accupath-path-id"  # Pathway up to (and incuding) current node hash
HTTP_HEADER_ACCUPATH_ROOT_ID = "x-datadog-accupath-root-id"  # Hash for first node in pathway
HTTP_HEADER_ACCUPATH_ROOT_TIME = "x-datadog-accupath-root-time"  # Hash for first node in pathway

# Assumptions we need to fix/validate eventually
"""
* Every tracer supports these headers (especially upstream)
* Efficiency of data transmitted (hashing/unhashing)
* Context propagation only woks through the 'datadog' propagator (others not supported -_-)
* core is implemented and will always find the right items
* How is information back-propagated?
* Make the metrics a periodic service (instead of synchronous)
* Actually aggregate metrics
* Add testing
"""
