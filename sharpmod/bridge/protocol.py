"""Wire protocol version 1: one UTF-8 JSON object per ``\\n``-terminated line.

Requests are ``{"id", "method", "params"}``; responses are ``{"id", "result"}``
or ``{"id", "error"}``. Frames are at most :data:`MAX_FRAME_BYTES` bytes,
excluding the terminator, and never contain ``NaN`` or ``Infinity`` tokens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sharpmod.bridge.errors import BridgeError

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_REQUEST_ID = 2**53 - 1

_ENVELOPE_KEYS = frozenset({"id", "method", "params"})


class ProtocolError(Exception):
    """A request frame that cannot be served, with the id to answer it under.

    ``request_id`` is ``None`` when the frame could not be parsed far enough to
    find a valid id; the reply then carries ``"id": null``.
    """

    def __init__(self, error: BridgeError, request_id: int | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.request_id = request_id


@dataclass(frozen=True)
class Request:
    id: int
    method: str
    params: dict[str, Any]


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON token {token}")


def _protocol(message: str, request_id: int | None = None) -> ProtocolError:
    return ProtocolError(BridgeError("BRIDGE_PROTOCOL", message, False), request_id)


def _invalid(what: str, request_id: int) -> ProtocolError:
    return ProtocolError(
        BridgeError("INVALID_ARGUMENT", f"The request was not valid: {what}.", False),
        request_id,
    )


def encode_frame(obj: Any) -> bytes:
    """Serialize ``obj`` as one frame, including the ``\\n`` terminator.

    Raises ``ValueError`` for a non-finite number or a frame over the cap.
    """
    text = json.dumps(obj, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    data = text.encode("utf-8")
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError(f"frame of {len(data)} bytes exceeds {MAX_FRAME_BYTES}")
    return data + b"\n"


def _valid_id(value: Any) -> bool:
    return type(value) is int and 1 <= value <= MAX_REQUEST_ID


def decode_request(line: bytes) -> Request:
    """Decode one request line (with or without its terminator).

    Accepts a trailing ``\\r\\n``. Raises :class:`ProtocolError` for anything
    that is not a well-formed request envelope.
    """
    if line.endswith(b"\n"):
        line = line[:-1]
    if line.endswith(b"\r"):
        line = line[:-1]
    if len(line) > MAX_FRAME_BYTES:
        raise _protocol("Request frame exceeds the 8 MiB limit.")
    try:
        obj = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise _protocol("Request frame is not valid JSON.") from None
    if not isinstance(obj, dict):
        raise _protocol("Request frame is not a JSON object.")
    request_id = obj.get("id")
    if not _valid_id(request_id):
        raise _protocol("Request frame has no valid id.")
    keys = set(obj)
    if keys != _ENVELOPE_KEYS:
        missing = sorted(_ENVELOPE_KEYS - keys)
        extra = sorted(keys - _ENVELOPE_KEYS)
        raise _protocol(
            f"Request envelope keys are wrong (missing {missing}, unexpected {extra}).",
            request_id,
        )
    method = obj["method"]
    if not isinstance(method, str) or not method:
        raise _protocol("Request method must be a non-empty string.", request_id)
    params = obj["params"]
    if not isinstance(params, dict):
        raise _invalid("params must be an object", request_id)
    return Request(request_id, method, params)


#: Methods whose handler validates its own parameters (bridge/soundings.py,
#: bridge/fields.py, bridge/map_layers.py).
HANDLER_VALIDATED = frozenset({
    "models.catalog",
    "models.status",
    "soundings.open_observed",
    "soundings.open_model",
    "soundings.open_era5",
    "soundings.open_file",
    "soundings.inspect_wrf",
    "soundings.open_wrf",
    "soundings.analysis",
    "soundings.trends",
    "soundings.render",
    "soundings.view",
    "map.catalog",
    "fields.grid",
    "outlooks.spc",
    "radar.mosaic",
    "radar.site",
    "radar.sites",
    "satellite.goes",
    "reports.storms",
    "reports.options",
    "fields.contours",
    "surface.plots",
    "ensemble.summary",
    "thresholds.metrics",
    "thresholds.evaluate",
    "thresholds.export_csv",
    "places.labels",
    "stations.availability",
})


def check_params(request: Request) -> None:
    """Validate ``request.params`` for its method.

    ``hello`` takes exactly ``{"protocol_version": 1}``; the methods in
    :data:`HANDLER_VALIDATED` check their own parameters; every other method
    takes no parameters.
    """
    params = request.params
    if request.method in HANDLER_VALIDATED:
        return
    if request.method == "hello":
        version = params.get("protocol_version")
        if set(params) != {"protocol_version"} or type(version) is not int:
            raise _invalid(
                "hello takes exactly an integer protocol_version", request.id
            )
        if version != PROTOCOL_VERSION:
            raise _invalid(
                f"protocol_version {version} is not supported "
                f"(this bridge speaks {PROTOCOL_VERSION})",
                request.id,
            )
        return
    if params:
        raise _invalid(f"{request.method} takes no parameters", request.id)
