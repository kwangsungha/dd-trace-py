import json
import struct

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.fnv import fnv1_64

from ddtrace.internal.accupath.node_info import NodeInfo
import logging
import uuid
import uuid


UPSTREAM_PATHWAY_ID = "accupath_upstream_pathway_id"


log = get_logger(f"accupath.{__name__}")
log.setLevel(logging.ERROR)


def get_bytes(s):
    return bytes(s, encoding="utf-8")


class EdgeInfo:
    def __init__(self, type, name):
        self.type = type  # HTTP or GRPC for now
        self.name = name


def generate_request_pathway_id(*args, request_path_info=None, current_node_info=None, **kwargs):
    try:
        request_path_info = request_path_info or core.get_item("accupath.service.request_path_info") or 0
        current_node_info = current_node_info or core.get_item("accupath.service.current_node_info") or NodeInfo.from_local_env()

        log.debug(f"accupath - generating request pathway id with {request_path_info} and {current_node_info.to_bytes()}")

        current_node_bytes = current_node_info.to_bytes()# + request_path_info.to_bytes(8, 'big')
        current_node_hash = fnv1_64(current_node_bytes)
        #curre = fnv1_64(current_node_bytes)
        result = fnv1_64(struct.pack("<Q", current_node_hash) + struct.pack("<Q", request_path_info))


        return result
    except Exception as e:
        log.debug("failed to generate request pathway id", exc_info=True)


def generate_pathway_uid():
    uid = str(uuid.uuid4())
    return uid

uid = str(uuid.uuid4())
print(uid)
def generate_response_pathway_id(*args, **kwargs):
    response_path_info = core.get_item("accupath.service.response_path_info") or core.get_item("accupath.service.request_path_info")
    current_node_info = core.get_item("accupath.service.current_node_info") or NodeInfo.from_local_env()

    current_node_bytes = current_node_info.to_bytes()
    current_node_hash = fnv1_64(current_node_bytes)
    result = fnv1_64(struct.pack("<Q", current_node_hash) + struct.pack("<Q", response_path_info))

    return result



class PathKey:
    def __init__(self, request_pathway_id, response_pathway_id, root_node_info, request_id):
        self.request_pathway_id = request_pathway_id
        self.response_pathway_id = response_pathway_id
        self.root_node_info = root_node_info
        self.request_id = request_id

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, PathKey):
            return False

        return all(
            [
                self.request_pathway_id == __value.request_pathway_id,
                self.response_pathway_id == __value.response_pathway_id
            ]
        )
    
    def __hash__(self):
        return hash((
            self.request_pathway_id,
            self.response_pathway_id
            ))


class PathInfo:
    def __init__(self, request_pathway_hash=None, response_pathway_hash=None):
        # Keys
        self.request_pathway_hash = request_pathway_hash  # Type Hash/Int
        self.response_pathway_hash = response_pathway_hash  # Type hash/int

        # Additional information
        self.root_node_info = root_node_info  # Type: NodeInfo
        self.current_node_info = current_node_info # Type NodeInfo
        self.request_parent_node_info = parent_node_info  # Type NodeInfo
        self.response_parent_node_info = response_node_info  # Type NodeInfo
        self.request_edge_info = request_edge_info  # Type EdgeInfo
        self.response_edge_info = response_edge_info  # Type EdgeInfo


    def to_bytes(self):
        b = self.current_node_info.to_bytes()

        return b

    def to_hash(self):
        b = self.to_bytes()
        node_hash = fnv1_64(b)
        result = fnv1_64(struct.pack("<Q", node_hash) + struct.pack("<Q", self.request_pathway_hash))

        return result

    @classmethod
    def from_local_env(cls):
        root_pathway_id = NodeInfo.get_root_node_info()
        upstream_path = cls.get_upstream_pathway_id()
        current_node = NodeInfo.from_local_env()
        return cls(
            root_pathway_id = root_pathway_id,
            upstream_pathway_id = upstream_path,
            current_node_info = current_node,
        )

    @classmethod
    def get_upstream_pathway_id(cls):
        return core.get_item(UPSTREAM_PATHWAY_ID) or 0

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, PathInfo):
            return False

        return all(
            [
                self.current_node_info == __value.current_node_info,
                self.downstream_path_id == __value.downstream_path_id,
                self.root_pathway_id == __value.root_pathway_id,
                self.upstream_pathway_id == __value.upstream_pathway_id,
                self.last_edge_info == __value.last_edge_info,
                self.current_node_info == __value.current_node_info,
                self.downstream_path_id == __value.downstream_path_id,
            ]
        )

    def __hash__(self):
        return hash((
            #self.current_node_info,
            self.downstream_path_id,
            self.root_pathway_id,
            self.upstream_pathway_id,
            #self.last_edge_info,
            #self.current_node_info,
            self.downstream_path_id
            ))