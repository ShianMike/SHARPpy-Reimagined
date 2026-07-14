"""Zoomed worldwide locator drawn over the hodograph pixmap.

The locator always draws bundled Natural Earth coastlines and administrative
boundaries.  Inside the United States it adds the existing live TIGERweb
county outlines on top for greater local detail.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import math
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_COUNTY_QUERY_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/State_County/MapServer/1/query"
)
_MAP_FILL = "#05090b"
_MAP_BORDER = "#ffffff"
_GLOBAL_STATE_OUTLINE = "#60778c"
_GLOBAL_COUNTRY_OUTLINE = "#8ca2b8"
_GLOBAL_COASTLINE = "#b8cada"
_COUNTY_OUTLINE = "#ffffff"
_POINT_COLOR = "#ffda00"
_GLOBAL_LAYER_NAMES = ("coastline", "countries", "states")


def _as_float(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _collection_meta(widget: Any, key: str) -> Any:
    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        return None
    try:
        return collection.getMeta(key)
    except (AttributeError, KeyError, TypeError):
        return getattr(collection, "_meta", {}).get(key)


def point_from_widget(widget: Any) -> tuple[float, float] | None:
    """Return the active sounding latitude/longitude when both are available."""
    profile = getattr(widget, "prof", None)
    lat = _as_float(getattr(profile, "latitude", None))
    lon = _as_float(getattr(profile, "longitude", None))

    if lat is None:
        lat = _as_float(getattr(profile, "lat", None))
    if lon is None:
        lon = _as_float(getattr(profile, "lon", None))
    if lat is None:
        lat = _as_float(_collection_meta(widget, "lat"))
    if lon is None:
        lon = _as_float(_collection_meta(widget, "lon"))

    if lat is None or lon is None:
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def zoom_bounds(lat: float, lon: float) -> tuple[float, float, float, float]:
    """Return a local, near-square geographic extent centered on ``lat/lon``."""
    half_lat = 0.70
    cos_lat = max(0.35, math.cos(math.radians(lat)))
    half_lon = half_lat * 1.35 / cos_lat
    return lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat


@lru_cache(maxsize=64)
def _county_features_cached(lat_key: float, lon_key: float) -> tuple[dict[str, Any], ...]:
    west, south, east, north = zoom_bounds(lat_key, lon_key)
    params = {
        "where": "1=1",
        "geometry": f"{west:.5f},{south:.5f},{east:.5f},{north:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "250",
        "f": "geojson",
    }
    request = Request(
        f"{_COUNTY_QUERY_URL}?{urlencode(params)}",
        headers={"User-Agent": "SHARPpy-Reimagined/1.0"},
    )
    try:
        with urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ()

    features = payload.get("features", []) if isinstance(payload, dict) else []
    return tuple(feature for feature in features if isinstance(feature, dict))


def county_features_for_point(lat: float, lon: float) -> tuple[dict[str, Any], ...]:
    """Load county outlines near a sounding, caching by approximately 1 km."""
    return _county_features_cached(round(lat, 2), round(lon, 2))


@lru_cache(maxsize=1)
def _global_layers_cached() -> dict[
        str, tuple[tuple[tuple[float, float], ...], ...]]:
    """Load validated worldwide boundary polylines from package resources."""
    empty = {name: () for name in _GLOBAL_LAYER_NAMES}
    try:
        resource = files("sharpmod.resources").joinpath("basemap.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty

    layers = {}
    for name in _GLOBAL_LAYER_NAMES:
        lines = []
        raw_lines = payload.get(name, ())
        if not isinstance(raw_lines, list):
            layers[name] = ()
            continue
        for raw_line in raw_lines:
            if not isinstance(raw_line, list):
                continue
            line = []
            for coordinate in raw_line:
                if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                    continue
                lon = _as_float(coordinate[0])
                lat = _as_float(coordinate[1])
                if lon is None or lat is None:
                    continue
                if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
                    continue
                line.append((lon, lat))
            if len(line) >= 2:
                lines.append(tuple(line))
        layers[name] = tuple(lines)
    return layers


def _longitude_near(lon: float, center: float) -> float:
    """Wrap ``lon`` onto the copy of the world closest to ``center``."""
    return center + ((lon - center + 180.0) % 360.0) - 180.0


@lru_cache(maxsize=64)
def _global_lines_for_bounds_cached(
        west: float, south: float, east: float, north: float) -> dict[
            str, tuple[tuple[tuple[float, float], ...], ...]]:
    center = (west + east) / 2.0
    selected = {}
    for name, lines in _global_layers_cached().items():
        matches = []
        for line in lines:
            adjusted = tuple(
                (_longitude_near(lon, center), lat) for lon, lat in line)
            line_west = min(coordinate[0] for coordinate in adjusted)
            line_east = max(coordinate[0] for coordinate in adjusted)
            line_south = min(coordinate[1] for coordinate in adjusted)
            line_north = max(coordinate[1] for coordinate in adjusted)
            if (
                line_east >= west
                and line_west <= east
                and line_north >= south
                and line_south <= north
            ):
                matches.append(adjusted)
        selected[name] = tuple(matches)
    return selected


def global_lines_for_bounds(bounds: tuple[float, float, float, float]) -> dict[
        str, tuple[tuple[tuple[float, float], ...], ...]]:
    """Return bundled worldwide polylines intersecting ``bounds``."""
    try:
        west, south, east, north = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return {name: () for name in _GLOBAL_LAYER_NAMES}
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return {name: () for name in _GLOBAL_LAYER_NAMES}
    if west >= east or south >= north:
        return {name: () for name in _GLOBAL_LAYER_NAMES}
    return _global_lines_for_bounds_cached(
        round(west, 5), round(south, 5), round(east, 5), round(north, 5))


def _rings(geometry: Any):
    if not isinstance(geometry, dict):
        return
    kind = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if kind == "Polygon":
        for ring in coords:
            yield ring
    elif kind == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                yield ring


def _inset_rect(widget: Any, qtcore: Any):
    bitmap = widget.plotBitMap
    # Share the hodograph's upper-left corner rather than floating inward.
    frame_left = int(getattr(widget, "tlx", 0)) + 1
    frame_top = int(getattr(widget, "tly", 0)) + 1
    available_width = max(0, bitmap.width() - frame_left - 8)
    available_height = max(0, bitmap.height() - frame_top - 8)
    width = min(max(150, int(bitmap.width() * 0.29)), 250, available_width)
    height = min(max(96, int(width * 0.64)), available_height)
    if width < 110 or height < 72:
        return None
    return qtcore.QRectF(frame_left, frame_top, width, height)


def _map_point(rect: Any, bounds: tuple[float, float, float, float], lat: float, lon: float):
    west, south, east, north = bounds
    x = rect.left() + (lon - west) / (east - west) * rect.width()
    y = rect.top() + (north - lat) / (north - south) * rect.height()
    return x, y


def _draw_global_lines(
        painter: Any,
        lines: tuple[tuple[tuple[float, float], ...], ...],
        color: str,
        width: float,
        rect: Any,
        bounds: tuple[float, float, float, float],
        qtcore: Any,
        qtgui: Any) -> None:
    pen = qtgui.QPen(qtgui.QColor(color), width)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(qtcore.Qt.NoBrush)
    for line in lines:
        path = qtgui.QPainterPath()
        for index, (lon, lat) in enumerate(line):
            x, y = _map_point(rect, bounds, lat, lon)
            if index:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
        painter.drawPath(path)


def draw_hodo_locator(widget: Any) -> bool:
    """Draw a worldwide locator, optional U.S. counties, and selected point."""
    point = point_from_widget(widget)
    if point is None or not hasattr(widget, "plotBitMap"):
        return False

    try:
        from qtpy import QtCore, QtGui
    except Exception:
        return False

    rect = _inset_rect(widget, QtCore)
    if rect is None:
        return False

    lat, lon = point
    bounds = zoom_bounds(lat, lon)
    try:
        features = county_features_for_point(lat, lon)
    except Exception:
        features = ()
    try:
        global_layers = global_lines_for_bounds(bounds)
    except Exception:
        global_layers = {name: () for name in _GLOBAL_LAYER_NAMES}

    painter = QtGui.QPainter(widget.plotBitMap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor(_MAP_BORDER), 1.25))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(_MAP_FILL)))
        painter.drawRect(rect)

        padding = 5.0
        interior = rect.adjusted(padding, padding, -padding, -padding)
        painter.save()
        painter.setClipRect(interior)
        _draw_global_lines(
            painter, global_layers.get("states", ()),
            _GLOBAL_STATE_OUTLINE, 0.8, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, global_layers.get("countries", ()),
            _GLOBAL_COUNTRY_OUTLINE, 1.0, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, global_layers.get("coastline", ()),
            _GLOBAL_COASTLINE, 1.15, interior, bounds, QtCore, QtGui)
        county_pen = QtGui.QPen(QtGui.QColor(_COUNTY_OUTLINE), 1.0)
        county_pen.setCosmetic(True)
        painter.setPen(county_pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        for feature in features:
            for ring in _rings(feature.get("geometry")):
                if not isinstance(ring, list) or len(ring) < 2:
                    continue
                path = QtGui.QPainterPath()
                started = False
                for coordinate in ring:
                    if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                        continue
                    ring_lon = _as_float(coordinate[0])
                    ring_lat = _as_float(coordinate[1])
                    if ring_lat is None or ring_lon is None:
                        continue
                    x, y = _map_point(interior, bounds, ring_lat, ring_lon)
                    if started:
                        path.lineTo(x, y)
                    else:
                        path.moveTo(x, y)
                        started = True
                if started:
                    painter.drawPath(path)

        point_x, point_y = _map_point(interior, bounds, lat, lon)
        marker = QtGui.QColor(_POINT_COLOR)
        painter.setPen(QtGui.QPen(marker, 1.4))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(_MAP_FILL)))
        painter.drawEllipse(QtCore.QPointF(point_x, point_y), 4.0, 4.0)
        painter.drawLine(point_x - 7.0, point_y, point_x + 7.0, point_y)
        painter.drawLine(point_x, point_y - 7.0, point_x, point_y + 7.0)
        painter.restore()
        return True
    finally:
        painter.end()
