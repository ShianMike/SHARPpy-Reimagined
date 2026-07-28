"""Zoomed worldwide locator drawn over the hodograph pixmap.

All map geometry used by the paint path is bundled with the application.
Keeping painting offline is important for the interactive GUI: a slow or
unavailable map service must never stall a hodograph repaint.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import io
import json
import math
from pathlib import Path
import re
import threading
from typing import Any
import zipfile

from sharpmod import colors


_MAP_FILL = "#05090b"
_MAP_BORDER = "#ffffff"
_GLOBAL_STATE_OUTLINE = "#60778c"
_GLOBAL_COUNTRY_OUTLINE = "#8ca2b8"
_GLOBAL_COASTLINE = "#b8cada"
_COUNTY_OUTLINE = "#ffffff"
_POINT_COLOR = "#ffda00"
_GLOBAL_LAYER_NAMES = ("coastline", "countries", "states")
_COUNTY_ARCHIVE_NAME = "conus-counties.zip"
_COUNTY_FORMAT_VERSION = 1
_COUNTY_TILE_DEGREES = 1
_COUNTY_COORDINATE_PRECISION = 5
_COUNTY_MAX_QUERY_TILES = 64
_COUNTY_ARCHIVE_LOCK = threading.RLock()
_COORDINATE_LABEL_RE = re.compile(
    r"(?ix)"
    r"(?:\b\d{1,2}(?:\.\d+)?\s*[NS]\s*[,/ ]+\s*"
    r"\d{1,3}(?:\.\d+)?\s*[EW]\b)"
    r"|(?:[-+]?\d{1,2}(?:\.\d+)?\s*,\s*"
    r"[-+]?\d{1,3}(?:\.\d+)?)"
)


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


def location_name_from_widget(widget: Any) -> str:
    """Return the active sounding's human-readable location label."""

    coordinate_fallback = ""
    for key in (
        "town", "place_name", "location_name", "location",
        "station_name", "stn_id", "loc",
    ):
        value = _collection_meta(widget, key)
        if value is None:
            continue
        label = " ".join(str(value).split())
        if label:
            if _COORDINATE_LABEL_RE.search(label):
                coordinate_fallback = label
                continue
            return label
    profile = getattr(widget, "prof", None)
    for key in (
        "town", "place_name", "location_name", "location",
        "station_name", "stn_id", "loc",
    ):
        value = getattr(profile, key, None)
        if value is None:
            continue
        label = " ".join(str(value).split())
        if label:
            if _COORDINATE_LABEL_RE.search(label):
                coordinate_fallback = label
                continue
            return label
    # Legacy model soundings may only have labels such as
    # ``HRRR 45.76N 91.60W``. Never put those raw coordinates back over the
    # locator: new fetches receive a resolved town, while old files fall back
    # to the model name.
    model = _collection_meta(widget, "model")
    if model:
        return " ".join(str(model).split())
    if coordinate_fallback:
        prefix = _COORDINATE_LABEL_RE.split(coordinate_fallback, maxsplit=1)[0]
        return prefix.strip(" ,-")
    return ""


def zoom_bounds(lat: float, lon: float) -> tuple[float, float, float, float]:
    """Return a local, near-square geographic extent centered on ``lat/lon``."""
    half_lat = 0.70
    cos_lat = max(0.35, math.cos(math.radians(lat)))
    half_lon = half_lat * 1.35 / cos_lat
    return lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat


def county_features_for_point(lat: float, lon: float) -> tuple[dict[str, Any], ...]:
    """Return optional preloaded county features without performing I/O.

    County geometry used to be downloaded synchronously from TIGERweb here.
    This function intentionally remains as a small compatibility seam for
    callers that inject already-loaded features, while the production paint
    path relies on the bundled state, country, and coastline layers.
    """
    del lat, lon
    return ()


@lru_cache(maxsize=1)
def _county_archive_cached() -> zipfile.ZipFile:
    """Open the bundled tiled archive without inflating its national payload."""
    resource = files("sharpmod.resources").joinpath(_COUNTY_ARCHIVE_NAME)
    resource_path = Path(str(resource))
    if resource_path.is_file():
        return zipfile.ZipFile(resource_path, mode="r")
    # Supports uncommon zip-import package layouts. Normal wheel installs and
    # PyInstaller builds take the path branch above and retain only the ZIP
    # central directory plus requested tile data in memory.
    return zipfile.ZipFile(io.BytesIO(resource.read_bytes()), mode="r")


@lru_cache(maxsize=1)
def _county_manifest_cached() -> dict[str, Any]:
    with _COUNTY_ARCHIVE_LOCK:
        payload = _county_archive_cached().read("manifest.json")
    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("county archive manifest must be an object")
    expected = {
        "format_version": _COUNTY_FORMAT_VERSION,
        "tile_degrees": _COUNTY_TILE_DEGREES,
        "coordinate_precision": _COUNTY_COORDINATE_PRECISION,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(
                f"unsupported county archive {key}: {manifest.get(key)!r}")
    return manifest


@lru_cache(maxsize=64)
def _county_tile_lines_cached(
        tile_lon: int,
        tile_lat: int,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Decode one independently compressed one-degree county-outline tile."""
    _county_manifest_cached()
    name = f"tiles/{tile_lat}/{tile_lon}.json"
    try:
        with _COUNTY_ARCHIVE_LOCK:
            payload = _county_archive_cached().read(name)
    except KeyError:
        return ()
    raw_lines = json.loads(payload.decode("ascii"))
    if not isinstance(raw_lines, list) or len(raw_lines) > 20_000:
        return ()

    scale = float(10 ** _COUNTY_COORDINATE_PRECISION)
    lines = []
    for raw_line in raw_lines:
        if (
            not isinstance(raw_line, list)
            or len(raw_line) < 4
            or len(raw_line) % 2
            or len(raw_line) > 200_000
            or not all(isinstance(value, int) for value in raw_line)
        ):
            continue
        lon = raw_line[0]
        lat = raw_line[1]
        points = [(lon / scale, lat / scale)]
        for index in range(2, len(raw_line), 2):
            lon += raw_line[index]
            lat += raw_line[index + 1]
            points.append((lon / scale, lat / scale))
        if all(
            tile_lon - 1e-4 <= point_lon <= tile_lon + 1.0001
            and tile_lat - 1e-4 <= point_lat <= tile_lat + 1.0001
            for point_lon, point_lat in points
        ):
            lines.append(tuple(points))
    return tuple(lines)


def county_lines_for_bounds(
        bounds: tuple[float, float, float, float],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return only bundled county linework intersecting local map tiles."""
    try:
        west, south, east, north = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return ()
    if (
        not all(math.isfinite(value) for value in (
            west, south, east, north))
        or west >= east
        or south >= north
    ):
        return ()

    tile_west = math.floor(west)
    tile_east = math.floor(east)
    tile_south = math.floor(south)
    tile_north = math.floor(north)
    tile_count = (
        (tile_east - tile_west + 1)
        * (tile_north - tile_south + 1)
    )
    if tile_count <= 0 or tile_count > _COUNTY_MAX_QUERY_TILES:
        return ()

    lines = []
    try:
        for tile_lon in range(tile_west, tile_east + 1):
            for tile_lat in range(tile_south, tile_north + 1):
                lines.extend(_county_tile_lines_cached(tile_lon, tile_lat))
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            zipfile.BadZipFile):
        return ()
    return tuple(lines)


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


def locator_rect_for_widget(widget: Any, qtcore: Any):
    """Return the locator rectangle only when the widget has a map point."""
    if point_from_widget(widget) is None:
        return None
    return _inset_rect(widget, qtcore)


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
    """Draw an offline worldwide locator and the selected sounding point."""
    point = point_from_widget(widget)
    if point is None or not hasattr(widget, "plotBitMap"):
        return False

    try:
        from qtpy import QtCore, QtGui
    except Exception:
        return False

    rect = locator_rect_for_widget(widget, QtCore)
    if rect is None:
        return False

    lat, lon = point
    location_name = location_name_from_widget(widget)
    bounds = zoom_bounds(lat, lon)
    try:
        features = county_features_for_point(lat, lon)
    except Exception:
        features = ()
    try:
        county_lines = county_lines_for_bounds(bounds)
    except Exception:
        county_lines = ()
    try:
        global_layers = global_lines_for_bounds(bounds)
    except Exception:
        global_layers = {name: () for name in _GLOBAL_LAYER_NAMES}

    bg_color = QtGui.QColor(getattr(widget, "bg_color", _MAP_FILL))
    fg_color = QtGui.QColor(getattr(widget, "fg_color", _MAP_BORDER))
    if not bg_color.isValid():
        bg_color = QtGui.QColor(_MAP_FILL)
    if not fg_color.isValid():
        fg_color = QtGui.QColor(_MAP_BORDER)
    if bg_color.lightnessF() >= 0.5:
        background = bg_color.name()
        foreground = fg_color.name()
        semantic = colors.semantic_palette(background, foreground)
        map_fill = background
        map_border = foreground
        state_outline = colors.resolve_theme_color(
            _GLOBAL_STATE_OUTLINE, background, foreground, minimum=3.0)
        country_outline = colors.resolve_theme_color(
            _GLOBAL_COUNTRY_OUTLINE, background, foreground, minimum=3.0)
        coastline = colors.resolve_theme_color(
            _GLOBAL_COASTLINE, background, foreground, minimum=3.0)
        county_outline = foreground
        point_color = semantic["marker_yellow"]
    else:
        # Preserve the established standard/protanopia locator byte-for-byte.
        map_fill = _MAP_FILL
        map_border = _MAP_BORDER
        state_outline = _GLOBAL_STATE_OUTLINE
        country_outline = _GLOBAL_COUNTRY_OUTLINE
        coastline = _GLOBAL_COASTLINE
        county_outline = _COUNTY_OUTLINE
        point_color = _POINT_COLOR

    painter = QtGui.QPainter(widget.plotBitMap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor(map_border), 1.25))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(map_fill)))
        painter.drawRect(rect)

        padding = 5.0
        interior = rect.adjusted(padding, padding, -padding, -padding)
        painter.save()
        painter.setClipRect(interior)
        _draw_global_lines(
            painter, global_layers.get("states", ()),
            state_outline, 0.8, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, global_layers.get("countries", ()),
            country_outline, 1.0, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, global_layers.get("coastline", ()),
            coastline, 1.15, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, county_lines,
            county_outline, 0.75, interior, bounds, QtCore, QtGui)
        county_pen = QtGui.QPen(QtGui.QColor(county_outline), 1.0)
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
        marker = QtGui.QColor(point_color)
        painter.setPen(QtGui.QPen(marker, 1.4))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(map_fill)))
        painter.drawEllipse(QtCore.QPointF(point_x, point_y), 4.0, 4.0)
        painter.drawLine(point_x - 7.0, point_y, point_x + 7.0, point_y)
        painter.drawLine(point_x, point_y - 7.0, point_x, point_y + 7.0)
        painter.restore()
        if location_name:
            font = QtGui.QFont("Helvetica", 8)
            font.setBold(True)
            painter.setFont(font)
            metrics = QtGui.QFontMetrics(font)
            title = metrics.elidedText(
                location_name,
                QtCore.Qt.ElideRight,
                max(1, int(rect.width()) - 12),
            )
            title_rect = QtCore.QRectF(
                rect.left() + 4.0,
                rect.top() + 3.0,
                rect.width() - 8.0,
                metrics.height() + 3.0,
            )
            title_background = QtGui.QColor(map_fill)
            title_background.setAlpha(220)
            painter.fillRect(title_rect, title_background)
            painter.setPen(QtGui.QPen(QtGui.QColor(map_border), 1.0))
            painter.drawText(
                title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
        return True
    finally:
        painter.end()
