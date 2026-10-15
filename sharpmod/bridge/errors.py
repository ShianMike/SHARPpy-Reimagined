"""Structured errors carried by bridge error frames.

An error frame may carry exactly the codes in :data:`ERROR_CODES`. Every other
public error code is raised by the Rust bridge or the frontend, and
``BACKEND_UNAVAILABLE`` / ``BACKEND_CONFIG_INVALID`` travel inside the
``health`` result instead of an error frame.
"""

from __future__ import annotations

from typing import Any

ERROR_CODES = frozenset({
    "METHOD_NOT_FOUND",
    "INVALID_ARGUMENT",
    "BRIDGE_PROTOCOL",
    "CATALOG_UNAVAILABLE",
    "STATION_LIST_UNAVAILABLE",
    "INTERNAL",
    # Soundings (bridge/soundings.py).
    "SOUNDING_UNAVAILABLE",
    "SOUNDING_FETCH_FAILED",
    "SOUNDING_INVALID",
    "SOUNDING_NOT_FOUND",
    "RENDER_FAILED",
    # Map data (bridge/fields.py, bridge/map_layers.py).
    "FIELD_UNAVAILABLE",
    "FIELD_FETCH_FAILED",
    "OVERLAY_FETCH_FAILED",
})


class BridgeError(Exception):
    """A failure reported to Rust as an error frame.

    ``message`` is one user-facing sentence; tracebacks never go in it or in
    ``details``, which holds small structured facts for the developer log.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown bridge error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = bool(retryable)
        self.details = details


def error_frame(request_id: int | None, err: BridgeError) -> dict[str, Any]:
    """Return the response envelope for ``err``."""
    error: dict[str, Any] = {
        "code": err.code,
        "message": err.message,
        "retryable": err.retryable,
    }
    if err.details:
        error["details"] = dict(err.details)
    return {"id": request_id, "error": error}


def internal_error(method: str) -> BridgeError:
    """Return the ``INTERNAL`` error for an unexpected failure in ``method``."""
    return BridgeError(
        "INTERNAL",
        f"Unexpected error in {method}; see the log.",
        False,
        {"method": method},
    )
