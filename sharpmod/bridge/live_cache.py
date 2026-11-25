"""Bounded live responses under the bridge's existing, exit-cleaned session root."""

from __future__ import annotations

import logging
from pathlib import Path

from sharpmod.bridge.sounding_cache import cache

MAX_ENTRIES = 128
MAX_BYTES = 128 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
METHODS = {"radar.mosaic": "radar", "radar.site": "radar", "satellite.goes": "satellite",
           "surface.plots": "observations", "alerts.weather": "alerts"}
log = logging.getLogger(__name__)
_ORIGINAL_PATH_UNLINK = Path.unlink


def _ttl(method: str, params: dict) -> float:
    import sharpmod_rs

    return sharpmod_rs.live_cache_ttl(method, params)


def _read(path: Path, ttl: float) -> dict | None:
    import sharpmod_rs

    return sharpmod_rs.live_cache_read(path, float(ttl), max_bytes=MAX_RESPONSE_BYTES)


def _write(path: Path, result: dict) -> None:
    import sharpmod_rs

    unlink_cb = Path.unlink if Path.unlink is not _ORIGINAL_PATH_UNLINK else None
    sharpmod_rs.live_cache_write(
        path,
        result,
        max_entries=MAX_ENTRIES,
        max_bytes=MAX_BYTES,
        max_response_bytes=MAX_RESPONSE_BYTES,
        unlink_cb=unlink_cb,
    )


def call(method: str, params: dict, handler):
    """Share successful replies between desktop and browser, without caching errors."""
    if method not in METHODS:
        return handler(params)
    import sharpmod_rs

    digest = sharpmod_rs.live_cache_digest(method, params)
    try:
        path = cache().root / "live" / METHODS[method] / f"{digest}.json.gz"
    except OSError:
        log.debug("live session cache is unavailable", exc_info=True)
        return handler(params)
    result = _read(path, _ttl(method, params))
    if result is not None:
        return result
    result = handler(params)
    _write(path, result)
    return result
