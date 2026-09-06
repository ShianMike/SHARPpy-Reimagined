"""Qt-independent export of box soundings to CSV and GeoJSON.

A box is only useful if it can leave the application: into a spreadsheet, into
GIS alongside radar and warning polygons, or into a briefing. Both writers are
here rather than in the interface layers so the desktop workspace and the
command line produce byte-identical files.

Two decisions are worth stating.

*Cells, not points.* GeoJSON features are the sampled **cell** each sounding
represents, not a bare marker at its centre. A point would let a reader place the
value anywhere, when what the model actually asserts is a value over an area of
one grid spacing. The cell says which area.

*Absence is written as absence.* A field a node does not have is an empty CSV
value and an explicit ``null`` in GeoJSON, never a zero and never a ``-9999``.
Anything else would arrive downstream as data.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Iterable

from sharpmod.box_analysis import BoxAnalysis, parameter


__all__ = [
    "GEOJSON_GENERATOR",
    "box_csv_rows",
    "box_geojson",
    "summarize_export",
    "write_box_csv",
    "write_box_geojson",
]


#: Recorded in the exported metadata so a stray file can be traced back.
GEOJSON_GENERATOR = "SHARPpy Reimagined box sounding"

#: Columns that identify a node, before the field values.
_IDENTITY_COLUMNS = ("request_id", "row", "col", "lat", "lon", "status")


def _normalize_lon180(lon) -> float:
    return ((float(lon) + 180.0) % 360.0) - 180.0


def _as_sequence(source) -> tuple[tuple[int | None, BoxAnalysis], ...]:
    """Return ``((hour, analysis), ...)`` for an analysis or a sequence.

    Accepting both is what lets one writer serve a single-hour box and a
    multi-hour one without the caller branching.
    """
    if isinstance(source, BoxAnalysis):
        return ((None, source),)
    hours = getattr(source, "hours", None)
    if hours is None:
        raise TypeError("expected a BoxAnalysis or BoxSequence")
    pairs = []
    for hour in hours:
        analysis = source.at(hour)
        if analysis is not None:
            pairs.append((int(hour), analysis))
    if not pairs:
        raise ValueError("this sequence contains no analyzed forecast hour")
    return tuple(pairs)


def _shared_keys(pairs, keys=None) -> tuple[str, ...]:
    """Return the field keys to export, in registry order."""
    if keys is not None:
        return tuple(parameter(key).key for key in keys)
    present: set[str] = set()
    for _hour, analysis in pairs:
        present.update(
            item.key for item in analysis.available_parameters()
        )
    from sharpmod.box_analysis import PARAMETERS

    return tuple(item.key for item in PARAMETERS if item.key in present)


def _status(point) -> str:
    if point.error:
        return str(point.error)
    return "ok" if point.values else "no data"


def _number(value) -> str:
    """Format one value for CSV, or an empty field when it is absent."""
    if value is None:
        return ""
    return f"{float(value):.6g}"


def box_csv_rows(source, *, keys=None, criteria=None) -> Iterable[list]:
    """Yield CSV rows for a box analysis or sequence, header first.

    A sequence gains a leading ``fxx`` column so every hour lands in one file
    that can be pivoted, rather than one file per hour.
    """
    pairs = _as_sequence(source)
    field_keys = _shared_keys(pairs, keys)
    sequence = len(pairs) > 1 or pairs[0][0] is not None
    header = list(_IDENTITY_COLUMNS) + list(field_keys)
    if sequence:
        header.insert(0, "fxx")
    if criteria is not None:
        header.append("matches")
    yield header

    for hour, analysis in pairs:
        verdicts = None
        if criteria is not None:
            verdicts = analysis.mask(criteria)
        for point in analysis.points:
            row = [
                point.request_id,
                point.row,
                point.col,
                f"{point.lat:.6f}",
                f"{_normalize_lon180(point.lon):.6f}",
                _status(point),
            ]
            row.extend(_number(point.value(key)) for key in field_keys)
            if sequence:
                row.insert(0, hour if hour is not None else "")
            if verdicts is not None:
                verdict = verdicts[point.row][point.col]
                row.append(
                    "" if verdict is None else ("yes" if verdict else "no"))
            yield row


def write_box_csv(source, path, *, keys=None, criteria=None) -> int:
    """Write a box analysis or sequence to CSV. Returns the data row count."""
    target = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    written = 0
    with open(target, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for index, row in enumerate(
            box_csv_rows(source, keys=keys, criteria=criteria)
        ):
            writer.writerow(row)
            if index:
                written += 1
    return written


def _ring(west, east, south, north) -> list[list[float]]:
    """Return a closed, counter-clockwise GeoJSON linear ring."""
    return [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
    ]


def _cell_geometry(west, east, south, north) -> dict:
    """Return a Polygon, or a MultiPolygon split at the antimeridian.

    RFC 7946 asks that geometries not cross the antimeridian, so a cell that
    would is emitted as its two halves instead of a polygon that appears to wrap
    the wrong way round the world.
    """
    south = max(-90.0, min(90.0, float(south)))
    north = max(-90.0, min(90.0, float(north)))
    west = _normalize_lon180(west)
    east = _normalize_lon180(east)
    if west <= east:
        return {"type": "Polygon", "coordinates": [_ring(
            west, east, south, north)]}
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [_ring(west, 180.0, south, north)],
            [_ring(-180.0, east, south, north)],
        ],
    }


def _cell_steps(analysis: BoxAnalysis) -> tuple[float, float]:
    region = analysis.plan.region
    dlat = (
        region.lat_span / float(analysis.rows - 1)
        if analysis.rows > 1 else region.lat_span
    )
    dlon = (
        region.lon_span / float(analysis.cols - 1)
        if analysis.cols > 1 else region.lon_span
    )
    return dlat, dlon


def _stamp(value) -> str | None:
    """Render a datetime as ISO 8601 with a Z suffix, or ``None``."""
    if value is None:
        return None
    try:
        return value.isoformat().replace("+00:00", "Z")
    except AttributeError:
        return str(value)


def box_geojson(source, *, keys=None, criteria=None) -> dict:
    """Return a GeoJSON ``FeatureCollection`` for a box analysis or sequence.

    Each sampled node becomes one cell-shaped feature carrying its field values.
    A final feature outlines the requested box itself, so a reader can see the
    area that was asked for as well as the grid that answered it.
    """
    pairs = _as_sequence(source)
    field_keys = _shared_keys(pairs, keys)
    first = pairs[0][1]
    plan = first.plan
    region = plan.region

    features = []
    for hour, analysis in pairs:
        dlat, dlon = _cell_steps(analysis)
        half_lat = dlat / 2.0
        half_lon = dlon / 2.0
        verdicts = None if criteria is None else analysis.mask(criteria)
        for point in analysis.points:
            properties = {
                "request_id": point.request_id,
                "row": point.row,
                "col": point.col,
                "lat": round(float(point.lat), 6),
                "lon": round(_normalize_lon180(point.lon), 6),
                "status": _status(point),
            }
            if hour is not None:
                properties["fxx"] = int(hour)
            for key in field_keys:
                value = point.value(key)
                properties[key] = (
                    None if value is None else float(value)
                )
            if verdicts is not None:
                properties["matches"] = verdicts[point.row][point.col]
            features.append({
                "type": "Feature",
                "geometry": _cell_geometry(
                    point.lon - half_lon, point.lon + half_lon,
                    point.lat - half_lat, point.lat + half_lat,
                ),
                "properties": properties,
            })

    features.append({
        "type": "Feature",
        "geometry": _cell_geometry(
            region.lon0, region.lon0 + region.lon_span,
            region.lat0, region.lat1,
        ),
        "properties": {
            "kind": "box",
            "label": region.label(),
            "width_km": round(region.width_km, 3),
            "height_km": round(region.height_km, 3),
        },
    })

    hours = [hour for hour, _analysis in pairs if hour is not None]
    metadata = {
        "generator": GEOJSON_GENERATOR,
        "model": plan.model_label,
        "model_key": plan.model_key,
        "run": _stamp(first.run_time),
        "rows": first.rows,
        "cols": first.cols,
        "spacing_km": round(plan.spacing_km, 3),
        "native_spacing_km": round(plan.native_spacing_km, 3),
        "cell_area_km2": round(first.cell_area_km2, 3),
        "tiers": list(first.tiers),
        "fields": list(field_keys),
    }
    if hours:
        metadata["hours"] = hours
    else:
        metadata["fxx"] = int(first.fxx)
        metadata["valid"] = _stamp(first.valid_time)
    if criteria is not None:
        from sharpmod.box_analysis import describe_criteria

        resolved = first._resolve_criteria(criteria)
        metadata["criteria"] = describe_criteria(resolved)

    return {
        "type": "FeatureCollection",
        "metadata": metadata,
        "features": features,
    }


def write_box_geojson(source, path, *, keys=None, criteria=None) -> int:
    """Write a box analysis or sequence as GeoJSON. Returns the feature count."""
    document = box_geojson(source, keys=keys, criteria=criteria)
    target = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1, allow_nan=False)
        handle.write("\n")
    return len(document["features"])


def summarize_export(source) -> str:
    """Return a one-line description of what an export will contain."""
    pairs = _as_sequence(source)
    field_keys = _shared_keys(pairs, None)
    nodes = sum(len(analysis.points) for _hour, analysis in pairs)
    if len(pairs) > 1:
        return (
            f"{nodes} rows ({len(pairs)} forecast hours x "
            f"{pairs[0][1].rows * pairs[0][1].cols} points), "
            f"{len(field_keys)} fields"
        )
    return f"{nodes} points, {len(field_keys)} fields"
