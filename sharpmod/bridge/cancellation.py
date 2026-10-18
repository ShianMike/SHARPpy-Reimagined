"""Request-owned cancellation shared by the stdio and browser bridges."""

from collections import OrderedDict
from contextlib import contextmanager
import threading
import time

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

_LOCK = threading.RLock()
_ACTIVE = {}
_EARLY = OrderedDict()
_LOCAL = threading.local()


class Cancelled(BridgeError):
    def __init__(self):
        super().__init__("INTERNAL", "The request was cancelled.", False, {"cancelled": True})


def token(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or not value.isascii() or not value.isalnum():
        raise p.invalid("request_id must be 1 to 64 ASCII letters or digits")
    return value


def register(request_id):
    request_id = token(request_id)
    with _LOCK:
        if request_id in _ACTIVE:
            raise p.invalid("request_id is already in use")
        event = threading.Event()
        if time.monotonic() - _EARLY.pop(request_id, float("-inf")) < 30:
            event.set()
        _ACTIVE[request_id] = event
        return event


def release(request_id):
    with _LOCK:
        _ACTIVE.pop(request_id, None)


@contextmanager
def scope(event):
    previous = getattr(_LOCAL, "event", None)
    _LOCAL.event = event
    try:
        check()
        yield
    finally:
        _LOCAL.event = previous


def cancelled():
    current = event()
    return current is not None and current.is_set()


def event():
    return getattr(_LOCAL, "event", None)


def check():
    if cancelled():
        raise Cancelled()


def cancel(params):
    p.keys(params, ("request_id",))
    request_id = token(params["request_id"])
    with _LOCK:
        event = _ACTIVE.get(request_id)
        if event is not None:
            event.set()
        else:
            _EARLY[request_id] = time.monotonic()
            while len(_EARLY) > 64:
                _EARLY.popitem(last=False)
    return {"ok": True}
