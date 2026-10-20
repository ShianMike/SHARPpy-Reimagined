"""Summaries of opened soundings, and the decode step every open shares.

A summary is what the frontend lists for an open sounding: its title, place,
times, source, and size. It is read from the decoded collection's metadata,
never computed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_cache import (
    Entry,
    SoundingDecodeError,
    cache,
    decode_collection,
)

log = logging.getLogger("sharpmod.bridge")


def meta(collection: Any, key: str) -> Any:
    meta = getattr(collection, "_meta", None)
    return meta.get(key) if isinstance(meta, dict) else None


def _level_count(collection: Any) -> int:
    profiles = getattr(collection, "_profs", {}) or {}
    member = getattr(collection, "_highlight", None)
    series = (
        profiles.get(member)
        if member in profiles
        else next(iter(profiles.values()), None)
    )
    try:
        return int(len(series[getattr(collection, "_prof_idx", 0) or 0].pres))
    except (AttributeError, TypeError, IndexError, KeyError):
        return 0


def _latlon(collection: Any) -> tuple[float | None, float | None]:
    lat, lon = p.num(meta(collection, "lat")), p.num(meta(collection, "lon"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None, None
    return lat, lon


def summary(entry_id: str, kind: str, collection: Any, **fields: Any) -> dict[str, Any]:
    lat, lon = _latlon(collection)
    current = (
        collection.getCurrentDate() if hasattr(collection, "getCurrentDate") else None
    )
    summary: dict[str, Any] = {
        "sounding_id": entry_id,
        "kind": kind,
        "title": "",
        "location": (str(meta(collection, "loc") or "") or None),
        "station_id": None,
        "station_name": None,
        "latitude": lat,
        "longitude": lon,
        "valid_time": p.iso(current),
        "run_time": None,
        "forecast_hour": None,
        "model": None,
        "provider": None,
        "provider_name": None,
        "source_url": None,
        "file_name": None,
        "level_count": _level_count(collection),
        "time_count": len(getattr(collection, "_dates", ()) or ()),
    }
    summary.update(fields)
    if summary["time_count"] > 1:
        summary["times"] = [p.iso(value) for value in collection._dates]
    if not summary["title"]:
        summary["title"] = summary["location"] or summary["file_name"] or "Sounding"
    return summary


def register(
    entry_id: str, path: Path, summary: dict[str, Any], collection: Any
) -> dict[str, Any]:
    cache().add(Entry(entry_id, path, summary, collection))
    log.info("opened sounding %s (%s): %s", entry_id, summary["kind"], summary["title"])
    return summary


def entry_collection(sounding_id: Any) -> tuple[Entry, Any]:
    """An open sounding and its decoded collection (decoded again if it was dropped)."""
    entry = cache().get(sounding_id)
    with entry.lock:
        if entry.collection is None:
            entry.collection, _ = decode(entry.path)
        return entry, entry.collection


def decode(path: Path, display: str | None = None) -> tuple[Any, str]:
    try:
        return decode_collection(path)
    except (SoundingDecodeError, ValueError) as exc:
        raise BridgeError(
            "SOUNDING_INVALID",
            f"{display or path.name} is not a sounding sharpmod can read "
            f"({str(exc)[:200]}).",
            False,
        ) from None
