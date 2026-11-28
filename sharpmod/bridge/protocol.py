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


def encode_frame(obj: Any) -> bytes:
    """Serialize ``obj`` as one frame, including the ``\\n`` terminator.

    Raises ``ValueError`` for a non-finite number or a frame over the cap.
    """
    import sharpmod_rs

    text = json.dumps(obj, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    return sharpmod_rs.bridge_encode_frame_check(text.encode("utf-8"))


def decode_request(line: bytes) -> Request:
    """Decode one request line (with or without its terminator).

    Accepts a trailing ``\\r\\n``. Raises :class:`ProtocolError` for anything
    that is not a well-formed request envelope.
    """
    import sharpmod_rs

    request_id, method, params = sharpmod_rs.bridge_decode_request_frame(line)
    return Request(request_id, method, params)


#: Methods whose handler validates its own parameters (bridge/soundings.py,
#: bridge/fields.py, bridge/map_layers.py).
HANDLER_VALIDATED = frozenset({
    "soundings.snapshot",
    "soundings.retain",
    "soundings.restore",
    "animation.webp_frame",
    "soundings.progress",
    "storage.clear",
    "models.catalog",
    "models.status",
    "soundings.open_observed",
    "soundings.open_model",
    "soundings.open_era5",
    "soundings.open_file",
    "soundings.inspect_wrf",
    "soundings.open_wrf",
    "soundings.analysis",
    "soundings.render",
    "soundings.view",
    "map.catalog",
    "fields.grid",
    "fields.cached",
    "outlooks.spc",
    "outlooks.wpc",
    "outlooks.build_forecast",
    "alerts.weather",
    "radar.mosaic",
    "radar.site",
    "radar.sites",
    "radar.tilts",
    "satellite.goes",
    "reports.storms",
    "reports.options",
    "fields.contours",
    "surface.plots",
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
    import sharpmod_rs

    sharpmod_rs.bridge_check_request_params(request.id, request.method, request.params)
