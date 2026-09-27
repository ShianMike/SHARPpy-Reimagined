"""Bundled basemap loading and shared immutable geometry."""

from __future__ import annotations

from types import MappingProxyType

#: Basemap layers the station map prepares and draws, in one place so a new
#: layer cannot be loaded but silently dropped during preparation.
BASEMAP_LAYER_NAMES = ("coastline", "lakes", "countries", "states")


def _load_basemap() -> dict:
    """Load the bundled HD basemap layers for the station map.

    Returns a dict ``{"coastline": [...], "countries": [...], "states": [...]}``
    where each value is a list of ``[lon, lat]`` polylines. Resolved
    package-relative via :mod:`importlib.resources`; prefers the multi-layer
    ``basemap.json`` and falls back to the older single-layer
    ``coastlines.json`` (or empty layers) so the map always renders.
    """
    import json

    try:
        from importlib.resources import files

        pkg = files("sharpmod.resources")
        res = pkg.joinpath("basemap.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {
            "coastline": data.get("coastline", []),
            # Absent from an older bundled basemap, which simply means no lake
            # outlines rather than a failure to load the map at all.
            "lakes": data.get("lakes", []),
            "countries": data.get("countries", []),
            "states": data.get("states", []),
        }
    except Exception:
        pass
    try:
        from importlib.resources import files

        res = files("sharpmod.resources").joinpath("coastlines.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {
            "coastline": data.get("polylines", []),
            "lakes": [],
            "countries": [],
            "states": [],
        }
    except Exception:
        return {"coastline": [], "lakes": [], "countries": [], "states": []}


def _prepare_basemap_layers(basemap: dict) -> MappingProxyType:
    """Freeze basemap polylines and precompute their clipping bounds.

    Map widgets only read this geometry.  Freezing every container makes it
    safe for all picker tabs to share one prepared copy while their view,
    raster, timer, station, and selection state remains widget-local.
    """
    prepared = {}
    for name in BASEMAP_LAYER_NAMES:
        lines = []
        for points in basemap.get(name, ()):
            if len(points) < 2:
                continue

            frozen_points = []
            min_lon = min_lat = float("inf")
            max_lon = max_lat = float("-inf")
            for point in points:
                lon, lat = point[0], point[1]
                frozen_points.append((lon, lat))
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)
                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)

            lines.append(
                (
                    (min_lon, max_lon, min_lat, max_lat),
                    tuple(frozen_points),
                )
            )
        prepared[name] = tuple(lines)
    return MappingProxyType(prepared)


def _shared_basemap_layers(
    loader=None, preparer=None,
) -> MappingProxyType:
    """Return process-wide geometry through the caller's loading hooks.

    Optional hooks let the public map module preserve its loading hooks.
    """
    load = loader or _load_basemap
    prepare = preparer or _prepare_basemap_layers
    return prepare(load())
