"""Zoomed worldwide locator drawn over the hodograph pixmap.

All map geometry used by the paint path is bundled with the application.
Keeping painting offline prevents a slow or unavailable map service from
stalling a sounding render.
"""

from __future__ import annotations

from datetime import datetime, timezone
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

from sharpmod.viz import colors
from sharpmod.maps import locator_presentation


_MAP_FILL = "#05090b"
_MAP_BORDER = "#ffffff"
_GLOBAL_STATE_OUTLINE = "#60778c"
_GLOBAL_COUNTRY_OUTLINE = "#8ca2b8"
_GLOBAL_COASTLINE = "#b8cada"
_COUNTY_OUTLINE = "#ffffff"
_POINT_COLOR = "#ffda00"
_GLOBAL_LAYER_NAMES = ("coastline", "lakes", "countries", "states")
_COUNTY_ARCHIVE_NAME = "conus-counties.zip"
_COUNTY_FORMAT_VERSION = 1
_COUNTY_TILE_DEGREES = 1
_COUNTY_COORDINATE_PRECISION = 5
_COUNTY_MAX_QUERY_TILES = 64
_COUNTY_ARCHIVE_LOCK = threading.RLock()
#: Overlay fills are a little more opaque here than on the picker maps: the
#: inset is small, so a wash faint enough to read well at full size disappears.
_OVERLAY_FILL_ALPHA = 90
_OVERLAY_HATCH_ALPHA = 200
#: A shape standing in for a point is a symbol, not a wash: it covers a few
#: pixels, so it hides nothing and has no reason to be translucent.
_OVERLAY_MARKER_FILL_ALPHA = 255
_OVERLAY_STROKE_WIDTH = 1.2
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

    import sharpmod_rs

    if not sharpmod_rs.hodo_locator_point_valid(lat, lon):
        return None
    return lat, lon


def requested_point_from_widget(widget: Any) -> tuple[float, float] | None:
    """Return a distinct requested point when a source snapped to another one.

    Point-model and reanalysis sources retain both ``requested_lat/lon`` and
    the selected grid coordinate in ``lat/lon``.  Showing only the latter makes
    a locator look as though the user requested a place they did not.  A request
    indistinguishable at map precision is omitted so observed soundings and
    exact grid hits keep one unambiguous marker.
    """
    import sharpmod_rs

    lat = _as_float(_collection_meta(widget, "requested_lat"))
    lon = _as_float(_collection_meta(widget, "requested_lon"))
    if lat is None or lon is None:
        return None
    selected = point_from_widget(widget)
    selected_lat, selected_lon = (
        selected if selected is not None else (None, None)
    )
    if not sharpmod_rs.hodo_locator_requested_distinct(
        selected_lat, selected_lon, lat, lon
    ):
        return None
    return lat, lon


def box_from_widget(widget: Any) -> tuple[float, float, float, float] | None:
    """Return the averaged area as ``(west, south, east, north)``, if there is one.

    A box mean sounding records the rectangle it averaged, and the JSON sidecar's
    keys reach the profile collection's metadata, so the inset can show the area
    the numbers actually came from. Without it the locator marks a single point
    that was never sampled on its own, which is the most misleading thing it
    could draw for an average.

    The stored order is the region's own ``(lat0, lon0, lat1, lon1)``.
    """
    import sharpmod_rs

    return sharpmod_rs.hodo_locator_normalise_box(
        _collection_meta(widget, "box_mean_bounds")
    )


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
        import sharpmod_rs

        return sharpmod_rs.hodo_overlay_strip_coordinate_label(
            coordinate_fallback
        )
    return ""


def valid_time_from_widget(widget: Any) -> datetime | None:
    """Return the focused sounding's valid time as tz-aware UTC, or ``None``.

    Tries the collection's own accessor first, then the metadata mirrors, then
    the profile attribute, because the loaders do not all populate the same one.

    A naive result is treated as UTC. Every cycle, forecast hour, and analysis
    time in this application is UTC, but not every decoder attaches a timezone,
    and the consumers of this value reject naive input outright rather than
    guess. Resolving the assumption here keeps that strictness meaningful.
    """
    candidates = []
    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        collection = None
    if collection is not None:
        try:
            candidates.append(collection.getCurrentDate())
        except (AttributeError, KeyError, TypeError, IndexError):
            pass
    candidates.append(_collection_meta(widget, "valid"))
    candidates.append(_collection_meta(widget, "date"))
    candidates.append(getattr(getattr(widget, "prof", None), "date", None))

    for value in candidates:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
    return None


def overlay_layers_for_widget(widget: Any) -> tuple[Any, ...]:
    """Return locator overlays attached to the focused sounding.

    Only layers whose validity window contains the sounding's valid time are
    returned, so switching focus between profiles at different times cannot
    leave the previous one's overlay on screen.
    """
    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        return ()
    try:
        from sharpmod.maps.map_overlays import locator_overlays, overlays_covering
        layers = locator_overlays(collection)
        if not layers:
            return ()
        return overlays_covering(layers, valid_time_from_widget(widget))
    except Exception:  # noqa: BLE001 - an overlay must never break the render
        return ()


def overlay_rasters_for_widget(widget: Any) -> tuple[Any, ...]:
    """Return locator image overlays attached to the focused sounding.

    The gridded-field counterpart of :func:`overlay_layers_for_widget`. A raster
    carries one valid time rather than a window, so it is matched to the hour the
    sounding depicts: a field an hour away from the profile beside it would be a
    different forecast presented as the same one.
    """
    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        return ()
    try:
        from sharpmod.maps.map_overlays import locator_rasters
        rasters = locator_rasters(collection)
        if not rasters:
            return ()
        when = valid_time_from_widget(widget)
        if when is None:
            return rasters
        matched = []
        for raster in rasters:
            valid = getattr(raster, "valid_time", None)
            if valid is None:
                matched.append(raster)
                continue
            try:
                if abs((valid - when).total_seconds()) < 3600.0:
                    matched.append(raster)
            except TypeError:
                # A naive/aware mismatch is not worth losing the field over.
                matched.append(raster)
        return tuple(matched)
    except Exception:  # noqa: BLE001 - an overlay must never break the render
        return ()


def raster_source_rect(raster: Any, bounds, width: int, height: int):
    """Return ``(x, y, w, h)`` of the image covering ``bounds``, or ``None``.

    Both the field image and the inset are plate carree with north at the top, so
    the crop is a straight rectangle: no resampling of our own, and Qt scales the
    result into the inset. ``None`` means the field does not reach this sounding.

    The rectangle is *not* clipped to the image. A sounding within a degree or two
    of the field's own edge legitimately asks for ground the field does not cover,
    and clipping here would lose the information needed to place what does
    survive. :func:`raster_draw_rects` does that clipping in step with the
    destination.
    """
    import sharpmod_rs

    try:
        lon0, lon1, lat0, lat1 = (float(value) for value in raster.bounds)
    except (AttributeError, TypeError, ValueError):
        return None
    west, south, east, north = (float(value) for value in bounds)
    return sharpmod_rs.hodo_locator_raster_source_rect(
        lon0, lon1, lat0, lat1, (west, south, east, north),
        float(width), float(height),
    )


def raster_draw_rects(raster: Any, bounds, width: int, height: int, target):
    """Return ``(source, target)`` rectangles for one field, or ``None``.

    Both are clipped together. A sounding near the edge of the model's domain asks
    for ground the field does not cover, and the surviving pixels have to be drawn
    on the ground they actually describe: shrinking only the source and letting Qt
    stretch it across the whole inset shifted the field by up to 0.8 degrees --
    about seventy kilometres -- while the marker stayed in the middle, so the
    values under the point were not the values at the point. The uncovered part of
    the inset is left unpainted instead, which is the truthful answer.
    """
    import sharpmod_rs

    try:
        lon0, lon1, lat0, lat1 = (float(value) for value in raster.bounds)
    except (AttributeError, TypeError, ValueError):
        return None
    west, south, east, north = (float(value) for value in bounds)
    rects = sharpmod_rs.hodo_locator_raster_draw_rects(
        lon0,
        lon1,
        lat0,
        lat1,
        (
            float(target.left()),
            float(target.top()),
            float(target.width()),
            float(target.height()),
        ),
        (west, south, east, north),
        float(width),
        float(height),
    )
    return rects[0] if rects else None


def raster_chip_text(raster: Any) -> str:
    """The shortest name for an attached field that still identifies it.

    A field drawn with nothing naming it is a wash of colour: the reader can see
    that *something* is shaded around the point and cannot tell whether they are
    looking at CAPE, helicity or a tornado parameter, which are the same reds and
    blues in the same places. The picker map has a legend and a colour bar for
    this; the inset has room for about ten characters.

    The product's own compact name is preferred, resolved through the catalogue
    the same way the picker's colour bar resolves its palette, so the naming
    lives with the product definition rather than being reinvented here. Falls
    back to the raster's identifier and then its title, because an ugly label
    beats an unlabelled field.
    """
    key = str(getattr(raster, "short_name", "") or "").strip()
    if key:
        try:
            # The catalogue is read directly rather than through
            # ``get_product``, which resolves an unknown key to the default
            # product so a stale setting still selects something. That is right
            # for choosing a field and wrong for naming one: it would label an
            # unrecognised raster "REFC", which is worse than not labelling it.
            from sharpmod.providers.hrrr_products import PRODUCTS
            product = PRODUCTS.get(key)
            if product is not None and product.short_label:
                return product.short_label
        except Exception:  # noqa: BLE001 - a label is not worth a failed render
            pass
        return key.upper()
    return str(getattr(raster, "title", "") or "").strip()






def overlay_label_at_point(
        layers: tuple[Any, ...],
        lat: float,
        lon: float,
) -> tuple[str, str, str] | None:
    """Return ``(label, stroke, fill)`` for the category covering the point.

    The inset spans under two degrees, so it is usually filled entirely by one
    category and the wash alone cannot say which. Naming the category at the
    sounding's own position is the part that actually reports the risk.
    """
    try:
        from sharpmod.maps.map_overlays import shape_at
        # The graded band, not the hatched qualifier drawn over it: the
        # significant-severe area outranks every band so that it paints on top,
        # and answering with it would report "SIGN" while discarding the
        # probability the point actually sits in.
        # The badge names an area category. Report points have their own fixed
        # pixel symbols and must not turn the outlook badge into a report label.
        shape = shape_at(layers, lon, lat, hatch=False, marker=False)
        qualifier = shape_at(layers, lon, lat, hatch=True, marker=False)
    except Exception:  # noqa: BLE001 - never break the render for a label
        return None
    if shape is None or not shape.label:
        return None

    # A probability label is a bare quantity: "5%" does not say whether it is
    # tornado, wind, or hail, and the badge is the only text the inset shows for
    # the overlay. Prefix the hazard when the product supplies one.
    text = shape.label
    for layer in layers:
        if shape in getattr(layer, "shapes", ()):
            short = getattr(layer, "short_name", "")
            if short:
                text = f"{short} {shape.label}"
            break
    if qualifier is not None:
        # SPC's Conditional Intensity Groups grade how strong the hazard could
        # become if it occurs, so the level is reported rather than the bare
        # presence of a qualifier. Pre-2026 outlooks carry the ungraded "SIGN"
        # area instead, which has no level to name.
        level = getattr(qualifier, "hatch_level", 0)
        text = f"{text} CIG{level}" if level else f"{text} SIG"
    return text, shape.stroke, (shape.fill or shape.stroke)




def _hatch_brush(qtcore: Any, qtgui: Any, colour: Any, level: int) -> Any:
    """Return the graded hatch brush shared with the picker maps.

    Falls back to the plain diagonal rather than losing the area entirely: this
    is a paint path, and an overlay detail is never worth failing a render for.
    """
    try:
        from sharpmod.maps.overlay_hatch import hatch_brush
    except Exception:  # noqa: BLE001 - keep painting without the graded form
        return qtgui.QBrush(colour, qtcore.Qt.BDiagPattern)
    return hatch_brush(qtcore, qtgui, colour, level)




#: Half-height of the locator's geographic extent, in degrees of latitude, so
#: the inset spans twice this from north to south -- about 218 km at 0.98.
#:
#: This is the inset's only zoom control; the longitude half-width and every
#: drawn layer derive from it.
#:
#: Raised from 0.70 (1.40 degrees, ~156 km), which was tight enough that a
#: sounding's surroundings gave little sense of where it was. Measured across
#: four sites, 0.98 puts about 47% more boundary linework in the inset while
#: leaving roughly 90% of it empty, so it reads as more context rather than as
#: clutter.
#:
#: It is also the ceiling. ``zoom_bounds`` is contracted to stay local -- under
#: two degrees of latitude -- so 0.98 is the largest value that still satisfies
#: it, with very little to spare. Going further is a deliberate decision to
#: widen that contract, and past roughly 1.4 the fixed-size marker grows
#: comparable to the county it sits in and starts hiding the thing it points at.
LOCATOR_HALF_LAT_DEGREES = locator_presentation.LOCAL_HALF_LAT_DEGREES

#: Longitude is widened by this before the cosine-of-latitude correction, so the
#: extent matches the inset's landscape aspect and reads as near-square.
LOCATOR_LON_ASPECT = locator_presentation.LON_ASPECT

#: Room left around an averaged area's outline, as a multiplier on the distance
#: from the sounding to the furthest edge of that area. Enough that the rectangle
#: reads as sitting inside a region rather than being cropped by the frame.
LOCATOR_BOX_MARGIN = locator_presentation.AREA_MARGIN

#: Ceiling on the box-driven zoom-out, in degrees of latitude. This is the one
#: sanctioned way past :data:`LOCATOR_HALF_LAT_DEGREES`: an averaged area has to
#: be shown whole or the inset misrepresents where the numbers came from, and
#: that outranks the local-detail contract. Beyond this a box is pathological
#: rather than a forecast area, and the inset would have stopped being a locator.
LOCATOR_MAX_HALF_LAT_DEGREES = 25.0


def zoom_bounds(
    lat: float,
    lon: float,
    box: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Return a local, near-square geographic extent centered on ``lat/lon``.

    Given ``box`` as ``(west, south, east, north)``, the extent is widened until
    that whole rectangle fits with a margin, so an averaged area is never drawn
    larger than the inset showing it. The aspect is preserved while widening, so
    the inset keeps its shape at every zoom.
    """
    import sharpmod_rs

    return sharpmod_rs.hodo_locator_zoom_bounds(
        float(lat),
        float(lon),
        None if box is None else tuple(float(value) for value in box),
    )


def presentation_from_widget(widget: Any) -> locator_presentation.LocatorPresentation:
    """Return the focused collection's portable locator presentation."""

    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        return locator_presentation.LocatorPresentation()
    return locator_presentation.collection_presentation(collection)


def bounds_from_widget(widget: Any) -> tuple[float, float, float, float] | None:
    """Resolve the extent shared by inset, expanded view, and PNG export."""

    point = point_from_widget(widget)
    if point is None:
        return None
    try:
        represented_area = box_from_widget(widget)
    except Exception:  # noqa: BLE001 - bad metadata falls back to a point
        represented_area = None
    presentation = presentation_from_widget(widget)
    try:
        return presentation.effective_bounds(
            point, represented_area=represented_area)
    except (TypeError, ValueError, OverflowError):
        # Preserve the pre-T25 renderer as a total fallback for old or damaged
        # collection metadata.
        return zoom_bounds(point[0], point[1], represented_area)


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
    import sharpmod_rs

    _county_manifest_cached()
    name = f"tiles/{tile_lat}/{tile_lon}.json"
    try:
        with _COUNTY_ARCHIVE_LOCK:
            payload = _county_archive_cached().read(name)
    except KeyError:
        return ()
    decoded = sharpmod_rs.hodo_locator_decode_county_tile(
        payload, int(tile_lon), int(tile_lat)
    )
    return tuple(tuple(tuple(pt) for pt in line) for line in decoded)


def county_lines_for_bounds(
        bounds: tuple[float, float, float, float],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return only bundled county linework intersecting local map tiles."""
    import sharpmod_rs

    coords = sharpmod_rs.hodo_locator_county_tile_coords(bounds)
    if not coords:
        return ()

    lines = []
    try:
        for tile_lon, tile_lat in coords:
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
    import sharpmod_rs

    selected = {}
    for name, lines in _global_layers_cached().items():
        filtered = sharpmod_rs.hodo_locator_filter_global_lines(
            [list(line) for line in lines], west, south, east, north
        )
        selected[name] = tuple(
            tuple(tuple(point) for point in line) for line in filtered
        )
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


_GEOGRAPHY_ATTRIBUTION = "U.S. Census Bureau boundaries"


def _format_utc(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%MZ")


def _overlay_actual_time(entry: Any) -> tuple[datetime | None, str]:
    actual_times = tuple(getattr(entry, "actual_times", ()) or ())
    actual_times = tuple(item for item in actual_times if isinstance(item, datetime))
    if actual_times:
        return max(actual_times), "observed"
    for key, basis in (
            ("valid_time", "valid"),
            ("valid_from", "valid from"),
            ("issued", "issued"),
            ("retrieved_at", "retrieved")):
        value = getattr(entry, key, None)
        if isinstance(value, datetime):
            return value, basis
        if isinstance(entry, dict) and isinstance(entry.get(key), datetime):
            return entry[key], basis
    return None, ""


def overlay_timing_text(context: dict[str, Any]) -> str:
    """Name the provider timestamp's meaning instead of guessing validity."""
    import sharpmod_rs

    return sharpmod_rs.hodo_overlay_timing_text(context)




def _scale_bar_spec(bounds, width_px: float) -> tuple[float, str, float] | None:
    """Return ``(pixel_width, label, km)`` for the current locator extent."""

    try:
        from sharpmod.maps.map_geography import (
            format_scale_length,
            km_per_pixel_at_latitude,
            pick_scale_length,
        )

        west, south, east, north = bounds
        latitude = (south + north) / 2.0
        km_per_px = km_per_pixel_at_latitude(
            east - west, max(1.0, float(width_px)), latitude)
        km, pixel_width, _unit = pick_scale_length(
            km_per_px, target_px=min(90.0, width_px * 0.3))
        if not math.isfinite(km_per_px) or km_per_px <= 0.0 or km <= 0.0:
            return None
        return (pixel_width, format_scale_length(km, "km"), km)
    except Exception:  # noqa: BLE001 - scale context must not break the map
        return None




def _draw_mode_chip(painter, presentation, rect, fill, border, qtcore, qtgui):
    text = (
        "FOLLOW"
        if presentation.link_mode == locator_presentation.LINK_FOLLOW
        else locator_presentation.EXTENT_LABELS[presentation.extent_mode].upper()
    )
    font = qtgui.QFont("Helvetica", max(6, min(15, round(rect.width() / 100))))
    font.setBold(True)
    painter.setFont(font)
    metrics = qtgui.QFontMetrics(font)
    width = metrics.horizontalAdvance(text) + 8.0
    chip = qtcore.QRectF(
        rect.right() - width - 4.0, rect.top() + 3.0, width, metrics.height() + 1.0)
    background = qtgui.QColor(fill)
    background.setAlpha(225)
    painter.fillRect(chip, background)
    painter.setPen(qtgui.QPen(qtgui.QColor(border), 0.8))
    painter.drawRect(chip)
    painter.drawText(chip, qtcore.Qt.AlignCenter, text)




def _inset_rect(widget: Any, qtcore: Any):
    explicit = getattr(widget, "_sharpmod_locator_rect", None)
    if explicit is not None:
        try:
            return qtcore.QRectF(explicit)
        except (TypeError, ValueError):
            pass
    bitmap = widget.plotBitMap
    # Share the hodograph's upper-left corner rather than floating inward.
    frame_left = int(getattr(widget, "tlx", 0)) + 1
    frame_top = int(getattr(widget, "tly", 0)) + 1
    import sharpmod_rs

    rect = sharpmod_rs.hodo_locator_inset_rect(
        float(bitmap.width()),
        float(bitmap.height()),
        float(frame_left),
        float(frame_top),
    )
    return None if rect is None else qtcore.QRectF(*rect)


def locator_rect_for_widget(widget: Any, qtcore: Any):
    """Reserve space only for an enabled locator with a map point."""
    if _collection_meta(widget, "show_locator") is False or point_from_widget(widget) is None:
        return None
    return _inset_rect(widget, qtcore)


def _map_point(rect: Any, bounds: tuple[float, float, float, float], lat: float, lon: float):
    west, south, east, north = bounds
    x = rect.left() + (lon - west) / (east - west) * rect.width()
    y = rect.top() + (north - lat) / (north - south) * rect.height()
    return x, y






def _marker_position(rect, bounds, lat: float, lon: float):
    """Map a point, clamping an off-view point to a visible edge indicator."""
    import sharpmod_rs

    return sharpmod_rs.hodo_locator_marker_position(
        float(lat),
        float(lon),
        tuple(float(value) for value in bounds),
        (
            float(rect.left()),
            float(rect.top()),
            float(rect.width()),
            float(rect.height()),
        ),
    )


def _draw_requested_point(
        painter, point, rect, bounds, color, qtcore, qtgui) -> None:
    """Draw the user's request as a diamond, distinct from the sampled crosshair."""

    lat, lon = point
    x, y, _inside = _marker_position(rect, bounds, lat, lon)
    marker = qtgui.QColor(color)
    pen = qtgui.QPen(marker, 1.1)
    pen.setCosmetic(True)
    pen.setStyle(qtcore.Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(qtcore.Qt.NoBrush)
    diamond = qtgui.QPolygonF([
        qtcore.QPointF(x, y - 5.0),
        qtcore.QPointF(x + 5.0, y),
        qtcore.QPointF(x, y + 5.0),
        qtcore.QPointF(x - 5.0, y),
    ])
    painter.drawPolygon(diamond)


from sharpmod.viz.hodo_overlay_drawing import (  # noqa: E402
    _draw_field_chip,
    _draw_overlay_rasters,
    _draw_overlay_badge,
    _draw_overlay_layers,
    overlay_context_for_widget,
    _draw_context_footer
)


from sharpmod.viz.hodo_render import (  # noqa: E402
    _draw_scale_bar,
    _draw_global_lines,
    _draw_box_outline,
    draw_hodo_locator,
    render_locator_pixmap
)
