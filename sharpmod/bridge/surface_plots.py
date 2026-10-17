"""Nearby surface observations as station-plot records.

One handler wraps ``providers.surface_observations`` and passes its typed
observations on as display records; the frontend draws the station models.
This is the audited seam: observation values travel as typed point records and
the frontend draws the plots, barbs, and marks.

Two presentation facts are computed here rather than in TypeScript. The staff
mark is a screen-pixel spec (direction in screen degrees, pennant/barb/half
counts) in the same convention ``ui/maps/vector.py`` paints with -- barbs point
toward the direction the wind comes from, so the staff walks the ``-u, +v`` of
the stored components -- and the reading pair is formatted at the plot's own
whole-degree precision. Hover text mirrors ``surface_overlay_layer``'s own
wording. Records reach the frontend un-decluttered: plot spacing is screen
space and the screen is the frontend's (``vector.py`` says as much), so the
set only carries the provider's own fetch caps.
"""

from __future__ import annotations

import math
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

#: Matches the fetch's own station cap (24 matched of 32 discovered); a set
#: larger than this cannot arrive.
MAX_PLOTS = 64


def _short(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else type(exc).__name__


def _staff_spec(u: float | None, v: float | None) -> dict[str, Any] | None:
    """The wind staff as the paint path's screen spec, or None for no staff."""
    if u is None or v is None:
        return None
    speed = math.hypot(u, v)
    if speed < 0.5:
        return None
    remaining = int(round(speed / 5.0) * 5)
    pennants, remaining = divmod(remaining, 50)
    barbs, remaining = divmod(remaining, 10)
    angle = math.degrees(math.atan2(v, -u)) % 360.0
    return {
        # Screen degrees of the staff tip from east, y down, matching
        # ``vector.py``: the staff walks (-u, +v) because the meteorological
        # convention points a barb toward the direction the wind comes from.
        # A tiny negative angle can round to 360 during modulo.
        "angle_deg": 0.0 if angle >= 360.0 else angle,
        "pennants": int(pennants),
        "barbs": int(barbs),
        "halves": 1 if remaining >= 5 else 0,
    }


def _reading(value: float | None) -> str:
    """One plot reading, at the station model's whole-degree precision."""
    return "\u2014" if value is None else f"{value:.0f}"


def _sample(value: float | None) -> str:
    """One hover reading, in ``surface_overlay_layer``'s own wording."""
    return "missing" if value is None else f"{value:.1f} \u00b0C"


def _wind_text(obs: Any) -> str:
    """One hover line, in ``surface_overlay_layer``'s own wording."""
    state = obs.wind_state
    if state == "calm":
        text = "calm (0 kt)"
    elif state == "direction-missing":
        text = f"{obs.wind_speed_kt:.0f} kt; direction missing"
    elif state == "available":
        text = f"{obs.wind_direction_deg:.0f}\u00b0 at {obs.wind_speed_kt:.0f} kt"
    else:
        text = "missing (not calm)"
    if obs.wind_gust_kt is not None:
        text += f", gusting {obs.wind_gust_kt:.0f} kt"
    return text


def plots(params: dict[str, Any]) -> dict[str, Any]:
    """The station models around one point at one time."""
    p.keys(params, ("latitude", "longitude", "valid_time"))
    lat = p.number(params, "latitude", -90.0, 90.0)
    lon = p.number(params, "longitude", -180.0, 180.0)
    when = p.time(params, "valid_time")
    from sharpmod.providers import surface_observations as surface

    try:
        dataset = surface.fetch_surface_observations(lat, lon, when)
    except surface.SurfaceObservationError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The surface observations could not be loaded: {_short(exc)}.",
            True,
        ) from None
    requested = f"{dataset.requested_time:%Y-%m-%d %H:%MZ}"
    out: list[dict[str, Any]] = []
    for obs in dataset.observations:
        u, v = obs.wind_uv_kt
        offset = obs.offset_seconds(dataset.requested_time) / 60.0
        stale = obs.is_stale(dataset.requested_time)
        body = [
            f"Observed {obs.observed_at:%Y-%m-%d %H:%M:%SZ} ({offset:+.0f} min from requested)",
            f"Temperature {_sample(obs.temperature_c)}; "
            f"dewpoint {_sample(obs.dewpoint_c)}; wind {_wind_text(obs)}",
            "Source: NOAA/NWS API station observation",
        ]
        if stale:
            body.append(
                "Stale/mismatched: more than 30 minutes from requested time"
            )
        out.append(
            {
                "id": obs.station.station_id,
                "longitude": obs.station.longitude,
                "latitude": obs.station.latitude,
                "valid": p.iso(obs.observed_at),
                "offset_min": round(offset, 1),
                "stale": stale,
                "availability": "available",
                "wind_state": obs.wind_state,
                "staff": _staff_spec(u, v) if obs.wind_state == "available" else None,
                "upper_text": _reading(obs.temperature_c),
                "lower_text": _reading(obs.dewpoint_c),
                "temperature_c": obs.temperature_c,
                "dewpoint_c": obs.dewpoint_c,
                "label": f"{obs.station.station_id} \u2014 {obs.station.name}",
                "description": "\n".join(body),
            }
        )
    failed = set(dataset.failed_station_ids)
    for station_id in dataset.unmatched_station_ids:
        station = dataset.station_for_id(station_id)
        if station is None:
            continue
        # Failed stations sit in the unmatched list too (the provider's own
        # bookkeeping), so one pass classifies each exactly once, in the words
        # ``surface_overlay_layer`` distinguishes them.
        no_match = station_id not in failed
        state = "unavailable" if no_match else "unknown"
        body = [
            (
                "No observation was available within \u00b160 minutes of the "
                f"requested {requested}."
                if no_match
                else "The station query failed; observation availability is unknown."
            ),
            "Source: NOAA/NWS API station catalogue",
        ]
        out.append(
            {
                "id": station.station_id,
                "longitude": station.longitude,
                "latitude": station.latitude,
                "valid": None,
                "offset_min": None,
                "stale": False,
                "availability": state,
                "wind_state": "missing",
                "staff": None,
                "upper_text": "\u2014",
                "lower_text": "\u2014",
                "temperature_c": None,
                "dewpoint_c": None,
                "label": f"{station.station_id} \u2014 "
                + ("no observation" if no_match else "query failed"),
                "description": "\n".join(body),
            }
        )
    if len(out) > MAX_PLOTS:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            "The surface set has more station plots than the map draws.",
            False,
        )
    return {
        "valid_time": p.iso(dataset.requested_time),
        "counts": {
            "matched": len(dataset.observations),
            "stale": int(dataset.stale_count),
            "no_observation": len(set(dataset.unmatched_station_ids) - failed),
            "failed": len(failed),
            "nearby": int(dataset.discovered_station_count),
            "queried": int(dataset.queried_station_count),
            "unqueried": int(dataset.unqueried_station_count),
        },
        "plots": out,
    }


HANDLERS = {"surface.plots": plots}
