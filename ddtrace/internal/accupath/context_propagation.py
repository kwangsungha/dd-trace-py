import time

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger

from ddtrace.internal.accupath.node_info import NodeInfo, ROOT_NODE_ID, ROOT_NODE_REQUEST_OUT_TIME, PARENT_NODE_ID, PARENT_NODE_REQUEST_OUT_TIME
from ddtrace.internal.accupath.path_info import PathInfo, UPSTREAM_PATHWAY_ID


log = get_logger(f"accupath.{__name__}")


def _generate_header(var_name):
    return f"x-datadog-{var_name.replace('_', '-').replace('.', '-')}"


def inject_context(var_name, default_value_generator, use_existing, headers):
    log.debug(f"accupath - inject starting for {var_name} with {use_existing}")
    HEADER = _generate_header(var_name)
    value = None
    if use_existing:
        value = core.get_item(var_name) or default_value_generator()
    else:
        value = default_value_generator()

    if value is not None:
        value = str(value)
        if isinstance(headers, list):
            headers.append((HEADER.encode('utf-8'), value.encode('utf-8')))
        else:
            headers[HEADER] = value

    log.debug(f"accupath - injected value {value} into header {HEADER} for full headers {headers}")



def extract_context(var_name, default_value_generator, extraction_cast_func, headers):
    HEADER = _generate_header(var_name)
    log.debug(f"accupath - extracting value {var_name} from header {HEADER} with headers {headers}")
    if isinstance(headers, dict):
        value = headers.get(HEADER, default_value_generator())
    elif isinstance(headers, list):
        value = default_value_generator()
        for (k, v) in headers:
            if k == value:
                value = v.decode('utf-8')
                break

    if value is not None:
        value = extraction_cast_func(value)
        core.set_item(var_name, value)
        log.debug(f"accupath - extracted value {value} from header {HEADER} and put it into {var_name}")
