"""Select real NOAA WMS scans; never substitute a latest image for history."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.etree import ElementTree

HISTORY = timedelta(minutes=60)
TOLERANCE = timedelta(minutes=10)
CAPABILITIES_TTL_S = 45.0
MAX_CAPABILITIES_BYTES = 2 * 1024 * 1024
_CACHE: dict[str, tuple[float, dict[str, tuple[datetime, ...]]]] = {}
_LOCK = threading.Lock()


class RadarTimeError(ValueError):
    """The advertised service cannot supply a recent scan near this time."""


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_capabilities(payload: bytes) -> dict[str, tuple[datetime, ...]]:
    """Read each layer's discrete TIME values, including inherited dimensions."""
    if len(payload) > MAX_CAPABILITIES_BYTES or b"<!DOCTYPE" in payload.upper():
        raise RadarTimeError("radar capabilities exceed the safety limits")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise RadarTimeError("radar service returned invalid capabilities") from exc
    result = {}

    def visit(element, inherited=()):
        times = inherited
        for child in element:
            if (
                child.tag.rsplit("}", 1)[-1] in ("Dimension", "Extent")
                and child.get("name", "").lower() == "time"
            ):
                parsed = set()
                for token in (child.text or "").split(","):
                    # shortcut: NOAA supplies discrete scans; add interval dimensions if NOAA publishes them.
                    if "/" in token:
                        continue
                    try:
                        parsed.add(
                            _utc(
                                datetime.fromisoformat(
                                    token.strip().replace("Z", "+00:00")
                                )
                            )
                        )
                    except ValueError:
                        continue
                times = tuple(sorted(parsed))
        for child in element:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "Name" and child.text:
                result[child.text.strip().split(":")[-1]] = times
            elif name in ("Capability", "Layer"):
                visit(child, times)

    visit(root)
    return result


def select_time(
    endpoint: str,
    layer: str,
    requested: datetime,
    *,
    opener,
    timeout: float,
    now: datetime | None = None,
) -> datetime:
    """Nearest real scan; current views can show a delayed latest scan honestly."""
    reference = _utc(now or datetime.now(timezone.utc))
    requested = _utc(requested)
    # Minute-rounded history targets can wait behind other preload downloads.
    if requested < reference - HISTORY - TOLERANCE or requested > reference + timedelta(
        minutes=1
    ):
        raise RadarTimeError("radar history is available only for the past 60 minutes")
    with _LOCK:
        entry = _CACHE.get(endpoint)
    if entry is None or entry[0] <= time.monotonic():
        url = (
            endpoint
            + "?"
            + urlencode(
                {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"}
            )
        )
        layers = parse_capabilities(opener(url, timeout, MAX_CAPABILITIES_BYTES))
        with _LOCK:
            if len(_CACHE) >= 24:
                _CACHE.pop(min(_CACHE, key=lambda key: _CACHE[key][0]))
            _CACHE[endpoint] = (time.monotonic() + CAPABILITIES_TTL_S, layers)
    else:
        layers = entry[1]
    times = layers.get(layer.split(":")[-1], ())
    candidates = [scan for scan in times if reference - HISTORY <= scan <= reference]
    if not candidates:
        raise RadarTimeError(
            f"{layer} advertises no historical scans in the past 60 minutes"
        )
    selected = min(candidates, key=lambda scan: (abs(scan - requested), scan))
    current = abs(reference - requested) <= timedelta(minutes=2)
    if not current and abs(selected - requested) > TOLERANCE:
        raise RadarTimeError(
            f"{layer} has no advertised scan within 10 minutes of the requested time"
        )
    return selected


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
