"""Build the bundled, spatially tiled CONUS county-outline resource.

The source is the U.S. Census Bureau's generalized national County
Cartographic Boundary File.  The output is a deterministic ZIP whose entries
are independently compressed one-degree tiles, allowing the hodograph locator
to decode only nearby linework instead of loading a national geometry file.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

try:
    from scripts.build_conus_place_index import CONUS_STATE_FIPS
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from build_conus_place_index import CONUS_STATE_FIPS


DEFAULT_YEAR = 2025
SOURCE_PAGE_TEMPLATE = (
    "https://www.census.gov/geographies/mapping-files/time-series/geo/"
    "cartographic-boundary.{year}.html"
)
URL_TEMPLATE = (
    "https://www2.census.gov/geo/tiger/GENZ{year}/kml/"
    "cb_{year}_us_county_500k.zip"
)
TILE_DEGREES = 1
COORDINATE_PRECISION = 5
FORMAT_VERSION = 1
_QUANTIZATION = 10 ** COORDINATE_PRECISION
_KML_NAMESPACE = {"kml": "http://www.opengis.net/kml/2.2"}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

Point = tuple[int, int]
Edge = tuple[Point, Point]
TileKey = tuple[int, int]


def _source_url(year: int) -> str:
    return URL_TEMPLATE.format(year=year)


def _source_page(year: int) -> str:
    return SOURCE_PAGE_TEMPLATE.format(year=year)


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "SHARPpy-Reimagined county-tile builder"},
    )
    with urlopen(request, timeout=90) as response:
        return response.read()


def _load_zip(path: str | None, url: str) -> bytes:
    if path:
        return Path(path).expanduser().read_bytes()
    return _download(url)


def _county_rings(payload: bytes):
    """Yield ``(state_fips, geoid, coordinates)`` from the Census KML."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.casefold().endswith(".kml")
        ]
        if len(names) != 1:
            raise ValueError(
                f"expected one Census KML file in archive, found {names!r}"
            )
        with archive.open(names[0]) as source:
            root = ET.parse(source).getroot()

    for placemark in root.findall(".//kml:Placemark", _KML_NAMESPACE):
        fields = {
            str(item.attrib.get("name", "")): str(item.text or "").strip()
            for item in placemark.findall(
                ".//kml:SimpleData", _KML_NAMESPACE)
        }
        state_fips = fields.get("STATEFP", "")
        if state_fips not in CONUS_STATE_FIPS:
            continue
        geoid = fields.get("GEOID", "")
        for coordinate_node in placemark.findall(
                ".//kml:LinearRing/kml:coordinates", _KML_NAMESPACE):
            points = []
            for coordinate in str(coordinate_node.text or "").split():
                values = coordinate.split(",")
                if len(values) < 2:
                    continue
                lon = float(values[0])
                lat = float(values[1])
                if (
                    math.isfinite(lon)
                    and math.isfinite(lat)
                    and -130.0 <= lon <= -60.0
                    and 20.0 <= lat <= 55.0
                ):
                    points.append((lon, lat))
            if len(points) >= 2:
                yield state_fips, geoid, tuple(points)


def _clip_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Clip one segment to a rectangle with the Liang-Barsky algorithm."""
    x0, y0 = start
    x1, y1 = end
    left, bottom, right, top = bounds
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, x0 - left),
        (dx, right - x0),
        (-dy, y0 - bottom),
        (dy, top - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > upper:
                return None
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return None
            upper = min(upper, ratio)
    if lower > upper:
        return None
    return (
        (x0 + lower * dx, y0 + lower * dy),
        (x0 + upper * dx, y0 + upper * dy),
    )


def _quantize(point: tuple[float, float]) -> Point:
    return (
        int(round(point[0] * _QUANTIZATION)),
        int(round(point[1] * _QUANTIZATION)),
    )


def _canonical_edge(start: Point, end: Point) -> Edge | None:
    if start == end:
        return None
    return (start, end) if start < end else (end, start)


def _segment_tiles(
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[TileKey, ...]:
    epsilon = 1e-10
    west = math.floor(min(start[0], end[0]) - epsilon)
    east = math.floor(max(start[0], end[0]) + epsilon)
    south = math.floor(min(start[1], end[1]) - epsilon)
    north = math.floor(max(start[1], end[1]) + epsilon)
    return tuple(
        (tile_lon, tile_lat)
        for tile_lon in range(west, east + 1)
        for tile_lat in range(south, north + 1)
    )


def _tile_edges(payload: bytes) -> tuple[
        dict[TileKey, set[Edge]], dict[str, object]]:
    tiles: dict[TileKey, set[Edge]] = defaultdict(set)
    counties = set()
    states = set()
    ring_count = 0
    source_point_count = 0
    for state_fips, geoid, ring in _county_rings(payload):
        states.add(state_fips)
        counties.add(geoid)
        ring_count += 1
        source_point_count += len(ring)
        for start, end in zip(ring, ring[1:]):
            for tile_lon, tile_lat in _segment_tiles(start, end):
                clipped = _clip_segment(
                    start,
                    end,
                    (
                        float(tile_lon),
                        float(tile_lat),
                        float(tile_lon + TILE_DEGREES),
                        float(tile_lat + TILE_DEGREES),
                    ),
                )
                if clipped is None:
                    continue
                edge = _canonical_edge(
                    _quantize(clipped[0]), _quantize(clipped[1]))
                if edge is not None:
                    tiles[(tile_lon, tile_lat)].add(edge)

    if states != CONUS_STATE_FIPS:
        raise ValueError(
            "county input did not contain exactly the lower 48 states plus "
            f"D.C.: {sorted(states)!r}"
        )
    if len(counties) < 3_000 or ring_count < 3_000:
        raise ValueError(
            "unexpectedly sparse Census county input: "
            f"{len(counties)} counties, {ring_count} rings"
        )
    return dict(tiles), {
        "county_count": len(counties),
        "state_fips": sorted(states),
        "state_or_district_count": len(states),
        "source_ring_count": ring_count,
        "source_point_count": source_point_count,
    }


def _edge_key(start: Point, end: Point) -> Edge:
    return (start, end) if start < end else (end, start)


def _tile_lines(edges: set[Edge]) -> tuple[tuple[Point, ...], ...]:
    """Merge a tile's unique segments into deterministic maximal paths."""
    adjacency: dict[Point, set[Point]] = defaultdict(set)
    for start, end in edges:
        adjacency[start].add(end)
        adjacency[end].add(start)

    visited: set[Edge] = set()

    def follow(start: Point, neighbor: Point) -> tuple[Point, ...]:
        path = [start, neighbor]
        visited.add(_edge_key(start, neighbor))
        previous = start
        current = neighbor
        while len(adjacency[current]) == 2:
            candidates = [
                candidate
                for candidate in sorted(adjacency[current])
                if candidate != previous
                and _edge_key(current, candidate) not in visited
            ]
            if not candidates:
                break
            following = candidates[0]
            path.append(following)
            visited.add(_edge_key(current, following))
            previous, current = current, following
        forward = tuple(path)
        reverse = tuple(reversed(forward))
        return min(forward, reverse)

    lines = []
    for start in sorted(adjacency):
        if len(adjacency[start]) == 2:
            continue
        for neighbor in sorted(adjacency[start]):
            if _edge_key(start, neighbor) not in visited:
                lines.append(follow(start, neighbor))
    for start, end in sorted(edges):
        if _edge_key(start, end) not in visited:
            lines.append(follow(start, end))
    return tuple(sorted(lines))


def _encode_lines(lines: tuple[tuple[Point, ...], ...]) -> bytes:
    encoded = []
    for line in lines:
        values = [line[0][0], line[0][1]]
        previous_lon, previous_lat = line[0]
        for lon, lat in line[1:]:
            values.extend((lon - previous_lon, lat - previous_lat))
            previous_lon, previous_lat = lon, lat
        encoded.append(values)
    return json.dumps(
        encoded, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _write_entry(
        archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(
        info,
        payload,
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def build(
    *,
    year: int,
    county_boundary_zip: str | None,
    output: Path,
    metadata_output: Path,
) -> dict[str, object]:
    source_url = _source_url(year)
    source_payload = _load_zip(county_boundary_zip, source_url)
    tiles, source_stats = _tile_edges(source_payload)

    tile_payloads = {}
    line_count = 0
    output_point_count = 0
    for (tile_lon, tile_lat), edges in sorted(tiles.items()):
        lines = _tile_lines(edges)
        tile_payloads[f"tiles/{tile_lat}/{tile_lon}.json"] = \
            _encode_lines(lines)
        line_count += len(lines)
        output_point_count += sum(len(line) for line in lines)

    manifest = {
        "format_version": FORMAT_VERSION,
        "coverage": "48 contiguous U.S. states and District of Columbia",
        "coordinate_precision": COORDINATE_PRECISION,
        "tile_degrees": TILE_DEGREES,
        "tile_count": len(tile_payloads),
        "line_count": line_count,
        "point_count": output_point_count,
        "source": "U.S. Census Bureau County Cartographic Boundary File",
        "source_page": _source_page(year),
        "source_scale": "1:500,000",
        "source_year": int(year),
        "source_url": source_url,
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        **source_stats,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        _write_entry(
            archive,
            "manifest.json",
            json.dumps(
                manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        for name, payload in sorted(tile_payloads.items()):
            _write_entry(archive, name, payload)
    archive_payload = buffer.getvalue()
    output.write_bytes(archive_payload)

    metadata = {
        **manifest,
        "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
        "archive_bytes": len(archive_payload),
        "encoding": (
            "independently DEFLATE-compressed one-degree ZIP tiles; "
            "1e-5-degree integer coordinates with delta-encoded paths"
        ),
        "usage": (
            "Locator map outlines only; no place names or map-label layer. "
            "Runtime reads only tiles intersecting the local locator bounds."
        ),
    }
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def _parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    resources = repo_root / "sharpmod" / "resources"
    parser = argparse.ArgumentParser(
        description="Build bundled offline CONUS county-outline tiles."
    )
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument(
        "--county-boundary-zip",
        help="optional downloaded national County boundary KML ZIP",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=resources / "conus-counties.zip",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=resources / "conus-counties.metadata.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    metadata = build(
        year=args.year,
        county_boundary_zip=args.county_boundary_zip,
        output=args.output,
        metadata_output=args.metadata_output,
    )
    print(
        "[ok] wrote "
        f"{metadata['county_count']} county outlines across "
        f"{metadata['tile_count']} tiles to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
