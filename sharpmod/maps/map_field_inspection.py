"""Qt-free numeric contracts for T18 map selection and inspection.

This module owns the plain-data side of "inspect a map freely while keeping
the intended sounding location stable":

* :class:`FieldSample` -- one synchronously sampled numeric field value with
  its units, source, and actual time, plus the requested-versus-sampled
  geometry (both coordinates and the distance between them).
* Text builders for the pinned inspection card, the hover readout, and the
  copy-to-clipboard actions. Text lives here rather than on the widgets so it
  is exercised without a ``QApplication`` and so every map subclass words the
  same state identically.
* The explicit tool vocabulary (``select``/``inspect``/``box``) and its
  validation, shared by the maps and the rail helper.

No Qt import. The paint path and the clipboard stay in ``gui_maps``; the
gridded sampling itself stays in ``hrrr_field`` (which owns the cached
frames). Anything this module cannot know -- for example terrain height, which
none of the current raster sources supplies -- is represented as ``None`` and
omitted from the card rather than guessed at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Explicit map tools (T18.1). ``select`` places the sounding point,
#: ``inspect`` reads values without moving anything, ``box`` draws an area.
MAP_TOOLS = ("select", "inspect", "box")

#: Tools a map without box support (station map, panels) accepts.
POINTLESS_TOOLS = ("select", "inspect")

#: Nominal HRRR grid spacing, for labelling the sampled resolution.
HRRR_RESOLUTION_KM = 3.0

#: ERA5 CDS grid spacing, for labelling the snapped resolution.
ERA5_RESOLUTION_KM = 27.8


def normalise_tool(tool, *, allow_box: bool = True) -> str:
    """Return a valid tool name, falling back to ``"select"``.

    Unknown, empty, or disallowed values degrade to ``select`` rather than
    raising, because the tool can arrive from persisted settings written by a
    different version.
    """
    wanted = str(tool or "").strip().lower()
    allowed = MAP_TOOLS if allow_box else POINTLESS_TOOLS
    return wanted if wanted in allowed else "select"


def haversine_km(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    """Return the great-circle distance between two points, in kilometres."""
    radius = 6371.0088
    phi0, phi1 = math.radians(lat0), math.radians(lat1)
    delta_phi = math.radians(lat1 - lat0)
    delta_lambda = math.radians(lon1 - lon0)
    half = (math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi0) * math.cos(phi1) * math.sin(delta_lambda / 2.0) ** 2)
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(max(0.0, half))))


def format_field_value(value: float | None) -> str:
    """Format a sampled value at a precision suited to its magnitude."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if not math.isfinite(number):
        return "—"
    magnitude = abs(number)
    if magnitude >= 100.0:
        return f"{number:.0f}"
    if magnitude >= 1.0:
        return f"{number:.1f}"
    return f"{number:.2f}"


def coordinates_text(lat: float, lon: float) -> str:
    """Return ``"lat, lon"`` at picker spin precision for copy actions."""
    return f"{float(lat):.4f}, {float(lon):.4f}"


@dataclass(frozen=True)
class FieldSample:
    """One synchronously sampled numeric field value (T18.4).

    ``value`` is read from the underlying numeric field in display units, or
    ``None`` when the field holds nothing usable at the sampled cell.
    ``covered`` is ``False`` only when the point falls outside the model's
    domain at all. ``imagery_only`` marks snapshots taken while no numeric
    frame was cached: the card must then name the limitation instead of
    presenting a colour-derived number.
    """

    product_key: str = ""
    label: str = ""
    value: float | None = None
    units: str = ""
    run_iso: str = ""
    fxx: int | None = None
    valid_iso: str = ""
    requested_lat: float = 0.0
    requested_lon: float = 0.0
    sampled_lat: float = 0.0
    sampled_lon: float = 0.0
    distance_km: float = 0.0
    covered: bool = True
    #: Why there is no value, when ``value`` is ``None`` and the point is
    #: covered: ``"no-frame"``, ``"no-data"``, ``"unknown-product"``.
    reason: str = ""
    #: The value lies outside the palette's drawn range. The number is still
    #: reported -- it was read, not inferred -- with this naming the limit.
    display_note: str = ""
    #: Nominal grid spacing in kilometres, for the resolution caveat.
    resolution_km: float = HRRR_RESOLUTION_KM
    #: Terrain height in metres, when the source supplies it. ``None`` omits
    #: the row rather than guessing.
    elevation_m: float | None = None
    imagery_only: bool = False

    @property
    def has_value(self) -> bool:
        """Whether a finite numeric value was read."""
        return (
            self.value is not None
            and math.isfinite(self.value)
        )


def imagery_only_sample(requested_lat: float, requested_lon: float, *,
                        rasters_visible: bool = False) -> FieldSample:
    """Build a snapshot stating that no numeric value is available (T18.4).

    Used when no cached field frame covers the point. The card names the
    limitation -- values are never reverse-engineered from a rendered colour.
    """
    return FieldSample(
        requested_lat=float(requested_lat),
        requested_lon=float(requested_lon),
        sampled_lat=float(requested_lat),
        sampled_lon=float(requested_lon),
        distance_km=0.0,
        covered=bool(rasters_visible),
        reason="imagery-only" if rasters_visible else "no-field",
        imagery_only=True,
    )


def hover_text(sample: FieldSample | None) -> str:
    """Return the one-line hover readout for an inspect hover (T18.2)."""
    if sample is None:
        return ""
    if sample.imagery_only:
        return (f"{sample.requested_lat:.3f}, {sample.requested_lon:.3f} "
                "— imagery only, no numeric value")
    if not sample.covered:
        return (f"{sample.requested_lat:.3f}, {sample.requested_lon:.3f} "
                "— outside model coverage")
    if not sample.has_value:
        name = sample.label or sample.product_key or "field"
        return (f"{sample.requested_lat:.3f}, {sample.requested_lon:.3f} "
                f"— {name}: no value at this grid cell")
    units = f" {sample.units}" if sample.units else ""
    return (f"{sample.label or sample.product_key} "
            f"{format_field_value(sample.value)}{units}")


def card_lines(snapshot: dict) -> list[str]:
    """Return the pinned-card body lines for a snapshot dict (T18.2/3/4).

    The snapshot is the plain-data dict the map stores (field, station, or
    point kind). Every line stays short enough for the map's top-right card.
    """
    kind = str(snapshot.get("kind") or "point")
    if kind == "station":
        return _station_card_lines(snapshot)
    if kind == "field":
        return _field_card_lines(snapshot)
    return _point_card_lines(snapshot)


def _field_card_lines(snapshot: dict) -> list[str]:
    lines = []
    label = str(snapshot.get("label") or snapshot.get("product") or "field")
    lines.append(f"{label} (pinned)")
    value = snapshot.get("value")
    units = str(snapshot.get("units") or "")
    if value is None:
        reason = str(snapshot.get("reason") or "")
        if str(snapshot.get("imagery_only") or "") == "True" or snapshot.get(
                "imagery_only") is True:
            lines.append("Imagery only — no numeric value.")
            lines.append("Values cannot be read from colour.")
        elif reason == "outside-domain":
            lines.append("Outside model coverage.")
        else:
            lines.append("No value at this grid cell.")
    else:
        units_text = f" {units}" if units else ""
        try:
            lines.append(f"{format_field_value(float(value))}{units_text}")
        except (TypeError, ValueError, OverflowError):
            lines.append("No value at this grid cell.")
    note = str(snapshot.get("display_note") or "")
    if note:
        lines.append(note)
    sampled = _format_latlon(snapshot.get("sampled_lat"),
                             snapshot.get("sampled_lon"))
    requested = _format_latlon(snapshot.get("requested_lat"),
                               snapshot.get("requested_lon"))
    if sampled and requested and sampled != requested:
        lines.append(f"Sampled {sampled}")
        lines.append(f"Requested {requested}")
        try:
            distance = float(snapshot.get("distance_km") or 0.0)
        except (TypeError, ValueError, OverflowError):
            distance = 0.0
        lines.append(f"{distance:.1f} km apart "
                     f"(≈{snapshot.get('resolution_km', HRRR_RESOLUTION_KM):.0f} km grid)")
    elif sampled:
        lines.append(sampled)
    elevation = snapshot.get("elevation_m")
    try:
        elevation_text = (f"Terrain {float(elevation):.0f} m"
                          if elevation is not None else "")
    except (TypeError, ValueError, OverflowError):
        elevation_text = ""
    if elevation_text:
        lines.append(elevation_text)
    valid = str(snapshot.get("valid_label") or "")
    if valid:
        lines.append(valid)
    return lines


def _station_card_lines(snapshot: dict) -> list[str]:
    sid = str(snapshot.get("station_id") or "")
    name = str(snapshot.get("station_name") or "")
    heading = f"{sid} — {name}".strip(" —") if (sid or name) else "Station (pinned)"
    lines = [heading]
    coords = _format_latlon(snapshot.get("lat"), snapshot.get("lon"))
    if coords:
        lines.append(coords)
    when = str(snapshot.get("when_label") or "")
    if when:
        lines.append(when)
    return lines


def _point_card_lines(snapshot: dict) -> list[str]:
    lines = ["Sounding point (pinned)"]
    coords = _format_latlon(snapshot.get("lat"), snapshot.get("lon"))
    if coords:
        lines.append(coords)
    if snapshot.get("rasters_visible"):
        lines.append("Imagery only — no numeric value.")
    else:
        lines.append("No numeric field at this point.")
    return lines


def _format_latlon(lat, lon) -> str:
    try:
        return f"{float(lat):.4f}, {float(lon):.4f}"
    except (TypeError, ValueError, OverflowError):
        return ""


def value_copy_text(snapshot: dict) -> str:
    """Return clipboard text for "copy value" on a pinned field snapshot."""
    label = str(snapshot.get("label") or snapshot.get("product") or "value")
    value = snapshot.get("value")
    units = str(snapshot.get("units") or "")
    coords = _format_latlon(snapshot.get("sampled_lat"),
                            snapshot.get("sampled_lon"))
    if value is None:
        return f"{label}: no value at {coords}"
    units_text = f" {units}" if units else ""
    try:
        number = format_field_value(float(value))
    except (TypeError, ValueError, OverflowError):
        return f"{label}: no value at {coords}"
    return f"{label} {number}{units_text} at {coords}"
