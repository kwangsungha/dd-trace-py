from collections import defaultdict
from ddsketch import LogCollapsingLowestDenseDDSketch
from typing import DefaultDict
import json
import uuid
import os
import time
from typing import NamedTuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.accupath.processor import _processor_singleton as _accupath_processor
from ddtrace.internal import core
from ddtrace.internal.utils.fnv import fnv1_64
import struct

log = get_logger('accupath')

def _enabled():
    # Can later read from an env var
    return True

NAMESPACE = "accupath"

OBSERVATION_STORAGE_COORDINATE_FORMAT = "{NAMESPACE}.{schema_name}.{observation_name}"
OBSERVATION_GENERATED_EVENT_FORMAT = "{NAMESPACE}.{schema_name}.{observation_generator_name}.{event_observation_name}.generated"
METRIC_GENERATED_EVENT_FORMAT = "{NAMESPACE}.{schema_name}.{metric_generator_name}.{dispatch_event_id}.generated"

def get_bytes(s):
    return bytes(s, encoding="utf-8")


class AccuPathPathwayContext:
    def __init__(self, tag):
        self.uid = str(uuid.uuid4())
        self.tag = tag
        self.root_node_info = AccuPathServiceContext.from_local_env()
        self.root_checkpoint_time = time.time_ns()
        self.checkpoints = [AccuPathCheckpointContext("root_request_in", 0, self.root_checkpoint_time)]
        self.upstream_node_hash = 0
        self.node_hash = self.checkpoints[-1].checkpoint_hash
        self.downstream_node_hash = -1

    def add_checkpoint(self, checkpoint):
        self.checkpoints.append(checkpoint)

    def __repr__(self):
        output = json.dumps({
            "service": str(os.environ.get("DD_SERVICE")),
            "uid": self.uid,
            "tag": self.tag,
            "root_node_info": str(self.root_node_info),
            "root_checkpoint_time": self.root_checkpoint_time,
            "upstream_node_hash": self.upstream_node_hash,
            "node_hash": self.node_hash,
            "downstream_node_hash": self.downstream_node_hash,
            #"checkpoints": [str(chk) for chk in self.checkpoints]
        })
        return output

    @classmethod
    def from_headers(cls, headers, direction="response"):
        tag = _extract_single_header_value("accupath.pathway.tag", headers)
        pathway_uid = _extract_single_header_value("accupath.pathway.uid", headers)
        root_node_info = AccuPathServiceContext.from_string_dict(_extract_single_header_value("accupath.pathway.root_node_info", headers))
        root_checkpoint_time = _extract_single_header_value("accupath.pathway.root_checkpoint_time", headers)
        last_checkpoint_info = AccuPathCheckpointContext.from_string_dict(_extract_single_header_value("accupath.pathway.last_checkpoint_info", headers))
        last_node_hash = int(_extract_single_header_value("accupath.pathway.last_node_hash", headers))

        to_return = cls(tag)
        to_return.root_node_info = root_node_info
        to_return.checkpoints = [last_checkpoint_info]
        to_return.uid = pathway_uid
        to_return.root_checkpoint_time = root_checkpoint_time
        to_return.upstream_node_hash = last_node_hash  # not used by response
        to_return.node_hash = cls._calc_checkpoint_hash(last_node_hash)
        if direction == "response":
            to_return.node_hash = last_node_hash

        return to_return

    @classmethod
    def from_request_pathway(cls, tag):
        to_return = cls(tag)
        request = core.get_item("accupath.request.context")
        to_return.uid = request.uid
        to_return.root_node_info = request.root_node_info
        to_return.root_checkpoint_time = request.root_checkpoint_time
        to_return.checkpoints = [request.checkpoints[-1]]
        to_return.downstream_node_hash = 0

        return to_return

    @classmethod
    def _calc_checkpoint_hash(cls, last_node_hash):
        current_node_hash = AccuPathServiceContext.from_local_env().to_hash()
        result = fnv1_64(struct.pack("<Q", current_node_hash) + struct.pack("<Q", last_node_hash))
        return result



class AccuPathCheckpointContext:
    def __init__(self, checkpoint_label, parent_checkpoint_hash, checkpoint_time, success=True):
        self.label = checkpoint_label
        self.parent_checkpoint_hash = parent_checkpoint_hash
        self.checkpoint_success = success
        self.checkpoint_time = checkpoint_time
        self.checkpoint_hash = self._calc_checkpoint_hash()

    @classmethod
    def from_string_dict(cls, string_dict):
        info = json.loads(string_dict)
        to_return = cls(info["label"], info["parent_checkpoint_hash"], info["checkpoint_time"])
        to_return.checkpoint_hash = info["checkpoint_hash"]
        to_return.checkpoint_success = bool(info["checkpoint_success"])

        return to_return

    def to_string_dict(self):
        return json.dumps({
            "label": self.label,
            "parent_checkpoint_hash": self.parent_checkpoint_hash,
            "checkpoint_hash": self.checkpoint_hash,
            "checkpoint_time": self.checkpoint_time,
            "checkpoint_success": str(self.checkpoint_success),
        })

    def _calc_checkpoint_hash(self):
        current_node_hash = AccuPathServiceContext.from_local_env().to_hash()
        result = fnv1_64(struct.pack("<Q", current_node_hash) + struct.pack("<Q", self.parent_checkpoint_hash))
        return result
    
    def __repr__(self):
        return f"AccuPathCheckpointContext(label={self.label}, parent_checkpoint_hash={self.parent_checkpoint_hash} -> checkpoint_hash={self.checkpoint_hash}, checkpoint_time={self.checkpoint_time}, checkpoint_success={self.checkpoint_success})"


class AccuPathServiceContext:
    def __init__(self, service, env, hostname):
        self.service = service
        self.env = env
        self.hostname = hostname

    @classmethod
    def from_string_dict(cls, string_dict):
        info = json.loads(string_dict)
        return cls(info["service"], info["env"], info["hostname"])

    def to_string_dict(self):
        return json.dumps({
            "service": self.service,
            "env": self.env,
            "hostname": self.hostname,
        })

    def __repr__(self):
        return f"AccuPathServiceContext(service={self.service}, env={self.env}, hostname={self.hostname})"

    def to_hash(self):
        b = self.to_bytes()
        node_hash = fnv1_64(b)
        return node_hash

    def to_bytes(self):
        return get_bytes(self.service)

    @classmethod
    def from_local_env(cls):
        service = os.environ.get("DD_SERVICE", "unnamed-python-service")
        env = os.environ.get("DD_ENV", "none")
        hostname = os.environ.get("DD_HOSTNAME", "")

        return cls(service, env, hostname)


def new_pathway_checkpoint(pathway_tag="default", *args, **kwargs):
    checkpoint_label = "accupath.request.context"
    if core.get_item(checkpoint_label):
        return
    #log.debug(f"starting new pathway in context {core._CURRENT_CONTEXT.get().identifier}")
    current_context = AccuPathPathwayContext(pathway_tag)
    core.set_item(checkpoint_label, current_context)
    #log.debug(f"started new pathway {checkpoint_label} - {current_context}")


def request_out_checkpoint(*args, **kwargs):
    #log.debug(f"Resuming pathway in context {core._CURRENT_CONTEXT.get().identifier}")
    checkpoint_label = "accupath.request.context"
    current_pathway_context = core.get_item(checkpoint_label)
    new_checkpoint = AccuPathCheckpointContext("request_out", current_pathway_context.checkpoints[-1].checkpoint_hash, time.time_ns())
    current_pathway_context.add_checkpoint(new_checkpoint)
    #log.debug(f"Resumed pathway {current_pathway_context}")


def request_in_checkpoint(*args, **kwargs):
    #log.debug(f"Resuming pathway in context {core._CURRENT_CONTEXT.get().identifier}")
    checkpoint_label = "accupath.request.context"
    current_pathway_context = core.get_item(checkpoint_label)
    new_checkpoint = AccuPathCheckpointContext("request_in", current_pathway_context.checkpoints[-1].checkpoint_hash, time.time_ns())
    current_pathway_context.add_checkpoint(new_checkpoint)
    #log.debug(f"Resumed pathway {current_pathway_context}")
    

def _generate_header(var_name):
    return f"x-datadog-{var_name.replace('_', '-').replace('.', '-')}"

def inject_response_pathway_context(headers):
    try:
        checkpoint_label = "accupath.request.context"
        current_pathway_context = core.get_item(checkpoint_label)
        #log.debug(f"inject starting for {current_pathway_context}")

        to_inject = [
            ("accupath.pathway.tag", current_pathway_context.tag),
            ("accupath.pathway.uid", current_pathway_context.uid),
            ("accupath.pathway.root_node_info", current_pathway_context.root_node_info.to_string_dict()),
            ("accupath.pathway.root_checkpoint_time", current_pathway_context.root_checkpoint_time),
            ("accupath.pathway.last_node_hash", current_pathway_context.node_hash),
            ("accupath.pathway.last_checkpoint_info", current_pathway_context.checkpoints[-1].to_string_dict()),
        ]

        for var_name, value in to_inject:
            #log.debug(f"Starting to inject header for {var_name}")
            HEADER = _generate_header(var_name)

            if value is not None:
                value = str(value)
                if isinstance(headers, list):
                    headers.append((HEADER.encode('utf-8'), value.encode('utf-8')))
                else:
                    headers[HEADER] = value

            #log.debug(f"accupath - injected value {value} into header {HEADER}")
        #log.debug(f"Full headers are: {headers}")
    except:
        log.debug("Error in inject_response_pathway_context", exc_info=True)

def inject_request_pathway_context(headers):
    try:
        checkpoint_label = "accupath.request.context"
        current_pathway_context = core.get_item(checkpoint_label)
        #log.debug(f"inject starting for {current_pathway_context}")

        to_inject = [
            ("accupath.pathway.tag", current_pathway_context.tag),
            ("accupath.pathway.uid", current_pathway_context.uid),
            ("accupath.pathway.root_node_info", current_pathway_context.root_node_info.to_string_dict()),
            ("accupath.pathway.root_checkpoint_time", current_pathway_context.root_checkpoint_time),
            ("accupath.pathway.last_checkpoint_info", current_pathway_context.checkpoints[-1].to_string_dict()),
            ("accupath.pathway.last_node_hash", current_pathway_context.node_hash),
        ]

        for var_name, value in to_inject:
            #log.debug(f"Starting to inject header for {var_name}")
            HEADER = _generate_header(var_name)

            if value is not None:
                value = str(value)
                if isinstance(headers, list):
                    headers.append((HEADER.encode('utf-8'), value.encode('utf-8')))
                else:
                    headers[HEADER] = value

            #log.debug(f"accupath - injected value {value} into header {HEADER}")
        #log.debug(f"Full headers are: {headers}")
    except:
        log.debug("Error in inject_request_pathway_context", exc_info=True)



def _extract_single_header_value(var_name, headers):
    HEADER = _generate_header(var_name)
    #log.debug(f"extracting value {var_name} from header {HEADER}")

    if isinstance(headers, dict):
        value = headers[HEADER]
    elif isinstance(headers, list):
        for (k, v) in headers:
            if k == value:
                value = v.decode('utf-8')
                break
    #log.debug(f"accupath - extracted value {value} from header {HEADER} and put it into {var_name}")
    return value


def extract_request_pathway_context(headers, *args, **kwargs):
    try:
        #log.debug("request pathway extract started")
        #log.debug(f"Extracting from headers: {headers}")

        checkpoint_label = "accupath.request.context"
        current_context = AccuPathPathwayContext.from_headers(headers, direction="request")
        core.set_item(checkpoint_label, current_context)
        #log.debug(f"resuming distributed pathway {checkpoint_label} - {current_context}")
    except:
        log.debug("Error in extract_request_pathway_context", exc_info=True)


def response_in_checkpoint(headers, status_code, *args, **kwargs):
    try:
        log.debug("Starting response_in context pathway")
        checkpoint_label = "accupath.request.context"
        current_context = core.get_item(checkpoint_label)
        success = True
        if not headers:
            # Assume there were no propagation requests made, this is the last service in the chain
            return_pathway = AccuPathPathwayContext.from_request_pathway("default")
            current_context.checkpoints.append(return_pathway.checkpoints[-1])
            current_context.downstream_node_hash = 0
        else:
            log.debug("here in response_in_checkpoint")
            success = (status_code < 400)
            return_pathway = AccuPathPathwayContext.from_headers(headers, direction="response")
            current_context.checkpoints.append(return_pathway.checkpoints[-1])
            current_context.downstream_node_hash = return_pathway.node_hash
            current_context.checkpoints[-1].success = success

        new_checkpoint = AccuPathCheckpointContext("response_in", current_context.checkpoints[-1].checkpoint_hash, time.time_ns(), success=success)
        current_context.checkpoints.append(new_checkpoint)
        #log.debug(f"Finished response pathway resumption: {core._CURRENT_CONTEXT.get().identifier} - {current_context}")

        if os.environ.get("DD_SERVICE") == current_context.root_node_info.service:
            response_out_checkpoint(headers=None)
    except:
        log.debug("Error in response_in_checkpoint", exc_info=True)

def submit_metrics():
    try:
        checkpoint_label = "accupath.request.context"
        current_context = core.get_item(checkpoint_label)

        request_in_time = 0
        if current_context.checkpoints[0].label != "root_request_in":
            request_in_time = int(current_context.checkpoints[1].checkpoint_time)
        else:
            request_in_time = int(current_context.checkpoints[0].checkpoint_time)


        path_key = PathKey(
            request_pathway_id=current_context.upstream_node_hash,
            response_pathway_id=current_context.downstream_node_hash,
            root_node_info=current_context.root_node_info,
            node_hash=current_context.node_hash,
            request_id=current_context.uid
        )


        response_in_time = int(current_context.checkpoints[-2].checkpoint_time)
        root_request_out_time = int(current_context.root_checkpoint_time)
        request_out_time = int(current_context.checkpoints[2].checkpoint_time)
        response_in_status = bool(current_context.checkpoints[-2].checkpoint_success)

        to_submit = [
            #(request_in_time, path_key, "request_latency", max(0, (request_in_time - root_request_out_time))),
            #(response_in_time, path_key, "response_latency", max(0, (response_in_time - request_out_time))),
            #(request_in_time, path_key, "root_to_request_in_latency", max(0, (request_in_time - root_request_out_time))),
            #(request_in_time, path_key, "root_to_request_in_latency_errors", (request_in_time - root_request_out_time)),
            #(request_in_time, path_key, "root_to_request_out_latency", max(0, (request_out_time - root_request_out_time))),
            #(request_in_time, path_key, "root_to_request_out_latency_errors", (request_out_time - root_request_out_time)),
        ]
        if response_in_status:
            to_submit.extend([
                #(response_in_time, path_key, "root_to_response_in_latency", max(0, (response_in_time - root_request_out_time))),
                #(response_in_time, path_key, "root_to_response_out_latency", max(0, (response_in_time - root_request_out_time))),
                (request_out_time, path_key, "root_to_request_out_latency", max(0, (request_out_time - root_request_out_time))),
            ])
        else:
            to_submit.extend([
                #(response_in_time, path_key, "root_to_response_in_latency_errors", max(0, (response_in_time - root_request_out_time))),
                #(response_in_time, path_key, "root_to_response_out_latency_errors", max(0, (response_in_time - root_request_out_time))),
                (request_out_time, path_key, "root_to_request_out_latency_errors", max(0, (request_out_time - root_request_out_time))),
            ])
        _accupath_processor.add_bucket_data(to_submit)
    except:
        log.debug("Error in submit_metrics", exc_info=True)


def response_out_checkpoint(headers, *args, **kwargs):
    try:
        log.debug(f"response out checkpoint started from {core._CURRENT_CONTEXT.get().identifier}")
        checkpoint_label = "accupath.request.context"
        current_context = core.get_item(checkpoint_label)
        if len(current_context.checkpoints) <= 3:
            # Handle the last link in a chain of requests
            response_in_checkpoint(headers=None, status_code=200)
            current_context = core.get_item(checkpoint_label)
        else:
            log.debug(f"Resuming response context")

        new_checkpoint = AccuPathCheckpointContext("response_out", current_context.checkpoints[-1].checkpoint_hash, time.time_ns())
        current_context.add_checkpoint(new_checkpoint)
        log.debug(f"finished response out {checkpoint_label} - {current_context}")
        submit_metrics()
    except:
        log.error("Error in response_out_checkpoint", exc_info=True)


if _enabled():
    core.on("http.request.start", new_pathway_checkpoint)
    core.on("http.request.header.injection", request_out_checkpoint)
    core.on("http.request.header.injection", inject_request_pathway_context)
    core.on("http.request.header.extraction", extract_request_pathway_context)
    core.on("http.request.header.extraction", request_in_checkpoint)
    core.on("http.response.header.injection", response_out_checkpoint)
    core.on("http.response.header.extraction", response_in_checkpoint)
    core.on("http.response.header.injection", inject_response_pathway_context)

class PathwayStats:
    """Aggregated pathway statistics."""

    __slots__ = (
        "request_latency",
        "response_latency",
        "root_to_request_in_latency",
        "root_to_request_in_latency_errors",
        "root_to_request_out_latency",
        "root_to_request_out_latency_errors",
        "root_to_response_in_latency",
        "root_to_response_in_latency_errors",
        "root_to_response_out_latency_errors",
        "root_to_response_out_latency",
    )

    def __init__(self):
        self.request_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.response_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_request_in_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_request_in_latency_errors = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_request_out_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_request_out_latency_errors = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_response_in_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_response_in_latency_errors = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_response_out_latency = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)
        self.root_to_response_out_latency_errors = LogCollapsingLowestDenseDDSketch(0.00775, bin_limit=2048)

class PathKey:
    def __init__(self, request_pathway_id, response_pathway_id, root_node_info, node_hash, request_id):
        self.request_pathway_id = request_pathway_id
        self.response_pathway_id = response_pathway_id
        self.root_node_info = root_node_info
        self.request_id = request_id
        self.node_hash = node_hash

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
Bucket = NamedTuple(
    "Bucket",
    [
        ("pathway_stats", DefaultDict[PathKey, PathwayStats]),
    ],
)

_buckets = defaultdict(
    lambda: Bucket(defaultdict(PathwayStats))
)

_accupath_processor.set_metrics_bucket(_buckets)