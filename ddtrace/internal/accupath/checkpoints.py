import time

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
import logging

log = get_logger(f"accupath.{__name__}")
log.setLevel(logging.ERROR)

def _time_checkpoint(checkpoint_record_id, dispatch_id, *args, **kwargs):
    now = int(time.time()*1e9)
    core.set_item(checkpoint_record_id, now)
    log.debug(f"accupath - recorded timestamp {now} for record name {checkpoint_record_id} and calling {dispatch_id}")
    if dispatch_id is not None:
        core.dispatch(dispatch_id, [])


def _status_checkpoint(checkpoint_record_id, dispatch_id, *args, **kwargs):
    log.debug(f"accupath - args are {args}")
    if len(args) < 2:
        return
    status_code = args[1]
    log.debug(f"accupath - status code is {status_code}")

    success =  status_code < 400
    core.set_item(checkpoint_record_id, success)
    log.debug(f"accupath - recorded success status {success} for record name {checkpoint_record_id} and calling {dispatch_id}")
    if dispatch_id is not None:
        core.dispatch(dispatch_id, [])
