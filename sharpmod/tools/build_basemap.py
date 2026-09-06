"""Build the bundled HD basemap resource for the GUI station map.

Downloads the public-domain Natural Earth 1:50m vector layers -- coastline,
lake shorelines, country boundary lines, and state/province boundary lines --
and writes a compact multi-layer JSON resource
``sharpmod/resources/basemap.json`` consumed by
:class:`sharpmod.gui.StationMapWidget`.

Lakes are a separate layer because ``ne_50m_coastline`` is strictly the
ocean/land boundary: it does not contain a single inland shoreline. Without
``ne_50m_lakes`` the Great Lakes are simply absent, so Chicago and Milwaukee sit
on featureless ground and a sounding beside Lake Michigan has nothing to locate
it against. Lakes ship as polygons rather than lines, which is why the geometry
reader below accepts rings as well as line strings.

The output schema is a dict of layer-name -> list of polylines, each polyline a
list of ``[lon, lat]`` pairs rounded to two decimals (~1 km, plenty for a
picker) to keep the file small::

    {"coastline": [[[lon, lat], ...], ...],
     "lakes":     [...],
     "countries": [...],
     "states":    [...]}

Run offline once (or when refreshing the basemap)::

    python -m sharpmod.tools.build_basemap

Natural Earth is public domain (credit: https://www.naturalearthdata.com).
"""

from __future__ import annotations

import json
import os
import ssl
from urllib.request import urlopen

import certifi

_BASE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
         "master/geojson/")

# layer name -> Natural Earth 1:50m GeoJSON file.
LAYERS = {
    "coastline": "ne_50m_coastline.geojson",
    # Polygons, not lines. A lake shore is a coastline in every sense that
    # matters to a map reader, and this is the only file that has them.
    "lakes": "ne_50m_lakes.geojson",
    "countries": "ne_50m_admin_0_boundary_lines_land.geojson",
    "states": "ne_50m_admin_1_states_provinces_lines.geojson",
}

#: Smallest lake worth an outline, in square degrees of its bounding box. At the
#: extents a picker map shows, anything under this is a speck that costs points
#: and adds no orientation. The Great Lakes, Great Salt Lake, Winnipeg, and the
#: Caspian all clear it comfortably.
MIN_LAKE_BBOX_SQ_DEG = 0.05

_RESOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "resources", "basemap.json")


def _iter_linestrings(geometry):
    """Yield every coordinate run in a GeoJSON geometry as a polyline.

    Polygon rings are yielded alongside line strings so a polygon layer -- the
    lakes -- can be drawn as outlines by the same painter that draws the
    boundary-line layers. Interior rings (islands in a lake) are included: they
    are real shoreline.
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        yield from coords
    elif gtype == "Polygon":
        yield from coords
    elif gtype == "MultiPolygon":
        for polygon in coords:
            yield from polygon


def _bbox_area(points) -> float:
    """Return the area of a ring's bounding box in square degrees."""
    if not points:
        return 0.0
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return (max(lons) - min(lons)) * (max(lats) - min(lats))


def _ring_bounds(points) -> tuple:
    """Return ``(lon0, lon1, lat0, lat1)`` for a ring."""
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return (min(lons), max(lons), min(lats), max(lats))


def _point_in_ring(lon: float, lat: float, points) -> bool:
    """Whether ``(lon, lat)`` falls inside a closed ring, by ray casting."""
    inside = False
    count = len(points)
    previous = count - 1
    for current in range(count):
        x_current, y_current = points[current]
        x_previous, y_previous = points[previous]
        if ((y_current > lat) != (y_previous > lat)
                and y_previous != y_current):
            crossing = (x_current + (lat - y_current)
                        * (x_previous - x_current)
                        / (y_previous - y_current))
            if lon < crossing:
                inside = not inside
        previous = current
    return inside


def _water_test(lake_rings):
    """Return a predicate for "this point is inside a lake".

    Bounding boxes are checked first, which is what keeps a whole-planet ring
    list cheap enough to test tens of thousands of boundary segments against.
    """
    indexed = [(_ring_bounds(ring), ring)
               for ring in lake_rings if len(ring) >= 4]

    def in_water(lon: float, lat: float) -> bool:
        for (lon0, lon1, lat0, lat1), ring in indexed:
            if (lon0 <= lon <= lon1 and lat0 <= lat <= lat1
                    and _point_in_ring(lon, lat, ring)):
                return True
        return False

    return in_water


def _split_at_water(lines, in_water) -> tuple[list, int]:
    """Break boundary polylines wherever a segment runs across a lake.

    Natural Earth publishes these as ``boundary_lines_land``, but the data still
    bridges from one shore to the other with a single straight segment -- the
    US/Canada line, for instance, crosses Lake Superior as one 100 km chord.
    With no lake drawn that was invisible. With the lakes drawn it reads as a
    ruled line slashing across the water, which is the one thing on the map that
    looks like a rendering fault rather than geography.

    Segment length cannot be the test: real political borders run dead straight
    for hundreds of kilometres, and this layer has thousands of legitimate long
    segments. Whether the segment lies *over water* is the test, so a straight
    border across a prairie survives and a chord across a lake does not.

    Three interior samples rather than one, so a long chord is not spared by a
    midpoint that happens to land on an island.
    """
    kept: list = []
    dropped = 0
    for points in lines:
        if len(points) < 2:
            continue
        run = [points[0]]
        for start, end in zip(points, points[1:]):
            wet = 0
            for step in (0.25, 0.5, 0.75):
                lon = start[0] + (end[0] - start[0]) * step
                lat = start[1] + (end[1] - start[1]) * step
                if in_water(lon, lat):
                    wet += 1
            if wet >= 2:
                dropped += 1
                if len(run) >= 2:
                    kept.append(run)
                run = [end]
            else:
                run.append(end)
        if len(run) >= 2:
            kept.append(run)
    return kept, dropped


def _fetch_layer(url: str, ctx, *, min_bbox=0.0) -> list:
    print(f"Downloading {url} ...")
    with urlopen(url, timeout=90, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    polylines = []
    dropped = 0
    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        for line in _iter_linestrings(geom):
            # Indexed rather than unpacked: a GeoJSON position is allowed a third
            # elevation element, and unpacking would raise on it.
            pts = [[round(float(pos[0]), 2), round(float(pos[1]), 2)]
                   for pos in line]
            # Drop consecutive duplicate points created by rounding.
            dedup = [pts[0]] if pts else []
            for p in pts[1:]:
                if p != dedup[-1]:
                    dedup.append(p)
            if len(dedup) < 2:
                continue
            if min_bbox and _bbox_area(dedup) < min_bbox:
                dropped += 1
                continue
            polylines.append(dedup)
    if dropped:
        print(f"  (skipped {dropped} ring(s) smaller than "
              f"{min_bbox} sq deg)")
    return polylines


#: Layers whose lines are political rather than physical, and so belong on land.
#: The coastline is excluded deliberately: it *is* a water boundary, and it
#: contains no segment crossing a lake anyway.
LAND_ONLY_LAYERS = ("countries", "states")


def clip_boundaries_to_land(payload: dict) -> dict:
    """Drop the political-boundary segments that run across lake water.

    Returns a new payload; the input is left alone. A payload without a lakes
    layer is returned unchanged, since there is then nothing to clip against.
    """
    lakes = payload.get("lakes") or []
    if not lakes:
        return dict(payload)
    in_water = _water_test(lakes)
    result = dict(payload)
    for name in LAND_ONLY_LAYERS:
        lines = payload.get(name)
        if not lines:
            continue
        kept, dropped = _split_at_water(lines, in_water)
        result[name] = kept
        if dropped:
            print(f"  {name}: dropped {dropped} segment(s) crossing water "
                  f"({len(lines)} -> {len(kept)} lines)")
    return result


def build(out_path: str = _RESOURCE) -> str:
    """Download every layer and write the resource, or write nothing at all.

    The all-or-nothing rule matters because ``out_path`` is a resource committed
    to the repository and shipped in the wheel. Writing the layers that happened
    to succeed would let one refused connection replace a good basemap with an
    empty one -- a blank map, silently, with the only evidence a line of console
    output that has already scrolled past.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    payload = {}
    failures = []
    for name, fname in LAYERS.items():
        try:
            layer = _fetch_layer(
                _BASE + fname, ctx,
                min_bbox=MIN_LAKE_BBOX_SQ_DEG if name == "lakes" else 0.0)
        except Exception as exc:
            print(f"  ! {name} failed: {exc}")
            failures.append(f"{name}: {exc}")
            continue
        if not layer:
            print(f"  ! {name} returned no geometry")
            failures.append(f"{name}: no geometry")
            continue
        payload[name] = layer

    if failures:
        raise RuntimeError(
            "refusing to overwrite " + out_path + " from an incomplete "
            "download; nothing was written. Failed layers: "
            + "; ".join(failures))

    payload = clip_boundaries_to_land(payload)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    total = sum(len(v) for v in payload.values())
    size_kb = os.path.getsize(out_path) / 1024.0
    print(f"Wrote {out_path}: "
          + ", ".join(f"{k}={len(v)}" for k, v in payload.items())
          + f" ({total} polylines, {size_kb:.0f} KB)")
    return out_path


if __name__ == "__main__":
    build()
