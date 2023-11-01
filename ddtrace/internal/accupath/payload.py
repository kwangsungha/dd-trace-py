import json
import time

from ddtrace.internal.accupath.node_info import NodeInfo
from ddtrace.internal.accupath.path_info import PathInfo
from ddtrace.internal.accupath.payload_pb2 import DataPathAPIPayload
from ddtrace.internal.accupath.payload_pb2 import NodeID
from ddtrace.internal.accupath.payload_pb2 import EdgeType
from ddtrace.internal.accupath.payload_pb2 import EdgeID
from ddtrace.internal.accupath.payload_pb2 import PathwayInfo
from ddtrace.internal.accupath.payload_pb2 import PathwayStats
from ddtrace.internal.accupath.payload_pb2 import Paths

from ..logger import get_logger


log = get_logger(__name__)

"""
to regenerate the proto:
protoc -I=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/ \
    --python_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
    --pyi_out=/Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-trace-py/ddtrace/internal/accupath/ \
    /Users/teague.bick/Workspace/experimental/users/ani.saraf/accupath/architectures/services/dd-go/pb/proto/trace/datapaths/payload.proto 
"""

class Payload:
    # Protobuf 
    # https://protobuf.dev/getting-started/pythontutorial/
    def _init__(self):
        pass

    @classmethod
    def generate_from_context(cls):
        root_time = NodeInfo.root_request_out_time()
        parent_info = NodeInfo.get_parent_node_info()
        parent_time = NodeInfo.get_parent_request_out_time()
        current_time = time.time()

        payload = DataPathAPIPayload()

        # ADD THIS NODE TO THE PAYLOAD
        payload.node.CopyFrom(cls._current_node_to_proto())


    @classmethod
    def _current_node_to_proto(cls):
        current_info = NodeInfo.from_local_env()
        node = NodeID()
        node.service = current_info.service
        node.env = current_info.env
        node.host = current_info.hostname

        return node

    @classmethod
    def _current_edge_to_proto(cls):
        edge = EdgeID()
        edge.type = EdgeType.HTTP
        edge.name = "foo"  # What are the requirements for the name?

        return edge

    @classmethod
    def _pathway_stats_to_proto(cls):
        pathway_stats = PathwayStats()
        pathway_stats.edge.CopyFrom(cls._current_edge_to_proto())
        pathway_stats.info.CopyFrom(cls._current_pathway_to_proto())

        return pathway_stats

    @classmethod
    def _current_pathway_to_proto(cls):
        root_info = NodeInfo.get_root_node_info()
        current_info = NodeInfo.from_local_env()
        path_info = PathInfo.from_local_env()
        pathway = PathwayInfo()
        pathway.root_service_hash = root_info.to_hash()
        pathway.node_hash = current_info.to_hash()
        pathway.upstream_pathway_hash = path_info.to_hash()
        pathway.downstream_pathway_hash = 0  # TODO

        return pathway

    @classmethod
    def _paths_to_proto(cls):
        1