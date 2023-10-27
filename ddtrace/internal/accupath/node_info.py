import json
import os
import struct

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.fnv import fnv1_64


ROOT_NODE_ID = "accupath_root_node_id"
ROOT_NODE_REQUEST_OUT_TIME = "accupath.service.root_out"
PARENT_NODE_ID = "accupath_parent_node_id"
PARENT_NODE_REQUEST_OUT_TIME = "accupath_parent_node_request_out_time"


log = get_logger(__name__)


def get_bytes(s):
    return bytes(s, encoding="utf-8")


class NodeInfo:
    def __init__(self, service, env, hostname):
        self.service = service
        self.env = env
        self.hostname = hostname
        self.checkpoint_info = dict(
            request_out_ns = None,
            request_in_ns = None,
            response_in_ns = None,
            resopnse_out_ns = None,
        )

    @classmethod
    def get_root_node_request_out_time(cls):
        return core.get_item(ROOT_NODE_REQUEST_OUT_TIME)

    @classmethod
    def parent_request_out_time(cls):
        return core.get_item(PARENT_NODE_REQUEST_OUT_TIME)

    @classmethod
    def from_local_env(cls):
        service = os.environ.get("DD_SERVICE", "unnamed-python-service")
        env = os.environ.get("DD_ENV", "none")
        hostname = os.environ.get("DD_HOSTNAME", "")

        return cls(service, env, hostname)
    
    @classmethod
    def from_string_dict(cls, string_dict):
        info = json.loads(string_dict)
        return cls(info["service"], info["env"], info["hostname"])

    @classmethod
    def get_root_node_info(cls):
        return core.get_item(ROOT_NODE_ID) or cls.from_local_env()
    
    @classmethod
    def get_parent_node_info(cls):
        return core.get_item(PARENT_NODE_ID)
    
    @classmethod
    def get_parent_request_out_time(cls):
        return core.get_item(PARENT_NODE_REQUEST_OUT_TIME)

    def to_hash(self):
        b = self.to_bytes()
        node_hash = fnv1_64(b)
        return fnv1_64(struct.pack("<Q", node_hash))
    
    def to_bytes(self):
        return get_bytes(self.service) + get_bytes(self.env) + get_bytes(self.hostname)

    def to_string_dict(self):
        return json.dumps({"service": self.service, "env": self.env, "hostname": self.hostname})

    def __eq__(self, __value: object) -> bool:
        if isinstance(__value, NodeInfo):
            return self.service == __value.service and self.env == __value.env and self.hostname == __value.hostname
        else:
            return False
