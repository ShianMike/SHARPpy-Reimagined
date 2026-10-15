"""Semantic operations served by the bridge.

Each handler takes the validated ``params`` object and returns a JSON-ready
result dict (snake_case, mirroring sharpmod's own keys). Handlers import their
sharpmod modules inside the function, so importing this module stays cheap and
never pulls in Qt.
"""

from __future__ import annotations

import logging
import math
import os
import platform
import sys
from collections.abc import Callable, Iterable
from typing import Any

from sharpmod.bridge import contours, ensemble, fields, map_layers, reports, satellite, soundings, surface_plots, thresholds
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.protocol import PROTOCOL_VERSION

log = logging.getLogger("sharpmod.bridge")

#: The ``backend_info()`` keys copied into the health result, and only these.
BACKEND_INFO_KEYS = (
    "requested_backend",
    "active_backend",
    "rust_installed",
    "rust_version",
    "fallback_reason",
)

MAX_ID_CHARS = 16
MAX_NAME_CHARS = 200
MAX_SOURCE_CHARS = 16


def hello(params: dict[str, Any]) -> dict[str, Any]:
    """Version facts for the handshake and the liveness ping."""
    import sharpmod

    return {
        "protocol_version": PROTOCOL_VERSION,
        "sharpmod_version": sharpmod.__version__,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "pid": os.getpid(),
    }


def health(params: dict[str, Any]) -> dict[str, Any]:
    """The scientific backend selection as ``backend_info()`` reports it."""
    from sharpmod.backends import BackendUnavailableError, backend_info

    try:
        info = backend_info()
    except BackendUnavailableError as exc:
        return {"scientific_backend": _unavailable("BACKEND_UNAVAILABLE", exc)}
    except ValueError as exc:
        return {"scientific_backend": _unavailable("BACKEND_CONFIG_INVALID", exc)}
    backend: dict[str, Any] = {"status": "ok"}
    for key in BACKEND_INFO_KEYS:
        backend[key] = info[key]
    return {"scientific_backend": backend}


def _unavailable(code: str, exc: Exception) -> dict[str, Any]:
    return {"status": "unavailable", "code": code, "message": str(exc)}


def stations_catalog(params: dict[str, Any]) -> dict[str, Any]:
    """The bundled UWyo station catalogue, filtered by the record rule."""
    from sharpmod.io import uwyo_catalog

    try:
        records = uwyo_catalog.all_stations()
        stations, dropped = filter_records(records)
        seed = is_seed_catalog(records, uwyo_catalog.SEED_STATIONS)
    except uwyo_catalog.StationListError as exc:
        log.exception("stations.catalog: station list unavailable")
        raise BridgeError(
            "STATION_LIST_UNAVAILABLE",
            f"The live UWyo station list could not be loaded ({exc}). "
            "The bundled list is still shown.",
            True,
        ) from None
    except Exception:
        log.exception("stations.catalog failed")
        raise BridgeError(
            "CATALOG_UNAVAILABLE", "The station catalogue could not be loaded.", True
        ) from None
    if seed:
        log.warning("stations.catalog: the bundled catalogue failed to load; "
                    "serving the %d seed stations", len(stations))
    return {
        "stations": stations,
        "origin": "seed_fallback" if seed else "bundled",
        "dropped_count": dropped,
    }


def _text(value: Any, low: int, high: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value if low <= len(value) <= high else None


def _coordinate(value: Any, limit: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not -limit <= number <= limit:
        return None
    return number


def _clean_record(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    raw_id = record.get("id")
    sid = _text(raw_id.strip() if isinstance(raw_id, str) else raw_id, 1, MAX_ID_CHARS)
    if sid is None:
        return None, "id must be 1 to 16 characters"
    lat = _coordinate(record.get("lat"), 90.0)
    if lat is None:
        return None, "latitude must be finite and within [-90, 90]"
    lon = _coordinate(record.get("lon"), 180.0)
    if lon is None:
        return None, "longitude must be finite and within [-180, 180]"
    name = _text(record.get("name"), 0, MAX_NAME_CHARS)
    if name is None:
        return None, "name must be text of at most 200 characters"
    src = _text(record.get("src"), 1, MAX_SOURCE_CHARS)
    if src is None:
        return None, "source must be 1 to 16 characters"
    return {"id": sid, "name": name, "lat": lat, "lon": lon, "src": src}, ""


def filter_records(
    records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Apply the record rule: keep well-formed records with unique ids.

    Every rejected record is logged with its id and reason and counted;
    nothing is passed on as ``null`` or ``NaN``. A repeated id keeps the first.
    """
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for record in records:
        clean, reason = _clean_record(record)
        if clean is not None and clean["id"] in seen:
            clean, reason = None, "duplicate id"
        if clean is None:
            dropped += 1
            log.warning(
                "stations.catalog: dropped record %r: %s", record.get("id"), reason
            )
            continue
        seen.add(clean["id"])
        kept.append(clean)
    return kept, dropped


def is_seed_catalog(records: list[dict[str, Any]], seed: dict[str, tuple]) -> bool:
    """True when ``records`` are exactly the embedded seed stations.

    ``load_catalog()`` silently falls back to ``SEED_STATIONS``; the bundled
    file shares ids with the seed but not names, so id set plus names decide.
    """
    if {r.get("id") for r in records} != set(seed) or len(records) != len(seed):
        return False
    return all(r.get("name") == seed[r["id"]][0] for r in records)


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "hello": hello,
    "health": health,
    "stations.catalog": stations_catalog,
    **soundings.HANDLERS,
    **fields.HANDLERS,
    **map_layers.HANDLERS,
    **satellite.HANDLERS,
    **reports.HANDLERS,
    **contours.HANDLERS,
    **surface_plots.HANDLERS,
    **ensemble.HANDLERS,
    **thresholds.HANDLERS,
}
