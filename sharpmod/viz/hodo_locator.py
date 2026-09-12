"""Zoomed worldwide locator drawn over the hodograph pixmap.

All map geometry used by the paint path is bundled with the application.
Keeping painting offline is important for the interactive GUI: a slow or
unavailable map service must never stall a hodograph repaint.
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

from sharpmod import colors


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
#: pixels, so it hides nothing and has no reason to be translucent. Matches
#: ``gui_maps.OVERLAY_MARKER_FILL_ALPHA`` so a storm report looks the same on
#: the inset as it did on the map it was chosen from.
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

    if lat is None or lon is None:
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
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
    raw = _collection_meta(widget, "box_mean_bounds")
    if raw is None or isinstance(raw, (str, bytes)):
        return None
    try:
        south, west, north, east = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    for value in (south, west, north, east):
        if not math.isfinite(value):
            return None
    if not -90.0 <= south <= 90.0 or not -90.0 <= north <= 90.0:
        return None
    if north <= south:
        return None
    # A box drawn across the antimeridian comes back with its east edge wrapped
    # below its west edge. Unwrapping keeps the span positive so the inset sizes
    # itself to the real width instead of to the 340-degree complement.
    if east <= west:
        east += 360.0
    if east - west > 360.0:
        return None
    return west, south, east, north


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
        from sharpmod.map_overlays import locator_overlays, overlays_covering
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
        from sharpmod.map_overlays import locator_rasters
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
    try:
        lon0, lon1, lat0, lat1 = (float(value) for value in raster.bounds)
    except (AttributeError, TypeError, ValueError):
        return None
    west, south, east, north = bounds
    span_lon = lon1 - lon0
    span_lat = lat1 - lat0
    if span_lon <= 0 or span_lat <= 0:
        return None
    if east <= lon0 or west >= lon1 or north <= lat0 or south >= lat1:
        return None
    left = (west - lon0) / span_lon * width
    right = (east - lon0) / span_lon * width
    # Row 0 is the north edge, so latitude runs the other way.
    top = (lat1 - north) / span_lat * height
    bottom = (lat1 - south) / span_lat * height
    return (left, top, max(1.0, right - left), max(1.0, bottom - top))


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
    full = raster_source_rect(raster, bounds, width, height)
    if full is None:
        return None
    x, y, w, h = full
    if w <= 0 or h <= 0:
        return None
    left = max(0.0, x)
    top = max(0.0, y)
    right = min(float(width), x + w)
    bottom = min(float(height), y + h)
    if right <= left or bottom <= top:
        return None
    # The same fraction removed from the source comes off the destination.
    target_left = target.left() + (left - x) / w * target.width()
    target_top = target.top() + (top - y) / h * target.height()
    target_width = (right - left) / w * target.width()
    target_height = (bottom - top) / h * target.height()
    if target_width <= 0.0 or target_height <= 0.0:
        return None
    return ((left, top, right - left, bottom - top),
            (target_left, target_top, target_width, target_height))


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
            from sharpmod.hrrr_products import PRODUCTS
            product = PRODUCTS.get(key)
            if product is not None and product.short_label:
                return product.short_label
        except Exception:  # noqa: BLE001 - a label is not worth a failed render
            pass
        return key.upper()
    return str(getattr(raster, "title", "") or "").strip()


def _draw_field_chip(painter, text, rect, fill, border, qtcore, qtgui) -> None:
    """Name the attached field in the inset's bottom-right corner.

    Bottom *right* because the outlook category chip already owns bottom-left and
    the two can be showing at once -- the field is the airmass and the outlook is
    a judgement about it, so a reader wants both at the same time.

    Styled from the locator's own frame colours rather than the field's palette:
    the chip says which quantity is drawn, and giving it a colour from that
    quantity's own scale would read as a value.
    """
    if not text:
        return
    font = qtgui.QFont("Helvetica", 7)
    font.setBold(True)
    painter.setFont(font)
    metrics = qtgui.QFontMetrics(font)
    padding = 3.0
    # Half the inset at most: the chip names the field, it does not become the
    # inset. Elided rather than overflowing, so a long name loses its tail
    # instead of running off the frame or over the outlook chip.
    room = max(18.0, rect.width() / 2.0 - 6.0)
    shown = metrics.elidedText(text, qtcore.Qt.ElideRight,
                               int(room - padding * 2.0))
    width = metrics.horizontalAdvance(shown) + padding * 2.0
    height = metrics.height() + 1.0
    chip = qtcore.QRectF(
        rect.right() - width - 4.0,
        rect.bottom() - height - 4.0,
        width,
        height,
    )
    background = qtgui.QColor(fill)
    if not background.isValid():
        return
    background.setAlpha(235)
    painter.setBrush(qtgui.QBrush(background))
    edge = qtgui.QColor(border)
    painter.setPen(qtgui.QPen(edge if edge.isValid() else background, 1.0))
    painter.drawRect(chip)
    painter.setPen(qtgui.QPen(
        qtgui.QColor("#000000") if background.lightnessF() >= 0.5
        else qtgui.QColor("#FFFFFF")))
    painter.drawText(chip, qtcore.Qt.AlignCenter, shown)


def _draw_overlay_rasters(painter, rasters, rect, bounds, qtcore, qtgui) -> None:
    """Blit each attached field image into the inset.

    Drawn beneath every line and the marker: the field is areal context and the
    geography locating the point has to stay readable through it.
    """
    for raster in rasters:
        payload = getattr(raster, "image_bytes", None)
        if not payload:
            continue
        image = qtgui.QImage()
        if not image.loadFromData(payload):
            continue
        rects = raster_draw_rects(
            raster, bounds, image.width(), image.height(), rect)
        if rects is None:
            continue
        source, destination = rects
        opacity = getattr(raster, "opacity", 1.0)
        try:
            opacity = min(1.0, max(0.0, float(opacity)))
        except (TypeError, ValueError):
            opacity = 1.0
        painter.save()
        try:
            painter.setOpacity(opacity)
            # Smooth, because the inset magnifies about two source pixels per
            # degree into a hundred: nearest-neighbour would show the field's own
            # grid as blocks and invite reading them as structure.
            painter.setRenderHint(qtgui.QPainter.SmoothPixmapTransform, True)
            painter.drawImage(
                qtcore.QRectF(*destination),
                image,
                qtcore.QRectF(*source),
            )
        finally:
            painter.restore()


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
        from sharpmod.map_overlays import shape_at
        # The graded band, not the hatched qualifier drawn over it: the
        # significant-severe area outranks every band so that it paints on top,
        # and answering with it would report "SIGN" while discarding the
        # probability the point actually sits in.
        shape = shape_at(layers, lon, lat, hatch=False)
        qualifier = shape_at(layers, lon, lat, hatch=True)
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


def _draw_overlay_badge(
        painter: Any,
        label: tuple[str, str, str],
        rect: Any,
        qtcore: Any,
        qtgui: Any) -> None:
    """Draw a small chip naming the risk category at the sounding's point."""
    text, stroke, fill = label
    font = qtgui.QFont("Helvetica", 7)
    font.setBold(True)
    painter.setFont(font)
    metrics = qtgui.QFontMetrics(font)
    padding = 3.0
    width = metrics.horizontalAdvance(text) + padding * 2.0
    height = metrics.height() + 1.0
    chip = qtcore.QRectF(
        rect.left() + 4.0,
        rect.bottom() - height - 4.0,
        width,
        height,
    )
    background = qtgui.QColor(fill)
    if not background.isValid():
        return
    background.setAlpha(235)
    painter.setBrush(qtgui.QBrush(background))
    edge = qtgui.QColor(stroke)
    painter.setPen(qtgui.QPen(edge if edge.isValid() else background, 1.0))
    painter.drawRect(chip)
    # Chosen against the chip's own fill rather than the map background, since
    # the chip is opaque and the categories run from pale green to deep magenta.
    painter.setPen(qtgui.QPen(
        qtgui.QColor("#000000") if background.lightnessF() >= 0.5
        else qtgui.QColor("#FFFFFF")))
    painter.drawText(chip, qtcore.Qt.AlignCenter, text)


def _hatch_brush(qtcore: Any, qtgui: Any, colour: Any, level: int) -> Any:
    """Return the graded hatch brush shared with the picker maps.

    Falls back to the plain diagonal rather than losing the area entirely: this
    is a paint path, and an overlay detail is never worth failing a render for.
    """
    try:
        from sharpmod.overlay_hatch import hatch_brush
    except Exception:  # noqa: BLE001 - keep painting without the graded form
        return qtgui.QBrush(colour, qtcore.Qt.BDiagPattern)
    return hatch_brush(qtcore, qtgui, colour, level)


def _draw_overlay_layers(
        painter: Any,
        layers: tuple[Any, ...],
        rect: Any,
        bounds: tuple[float, float, float, float],
        qtcore: Any,
        qtgui: Any) -> None:
    """Fill and outline overlay polygons inside the locator's interior.

    Shapes whose own bounding box misses the view are skipped: the inset spans
    well under two degrees while a convective outlook spans the continent, so
    almost every shape in a layer is irrelevant to a given sounding.
    """
    west, south, east, north = bounds
    for layer in layers:
        for shape in getattr(layer, "shapes", ()):
            try:
                min_lon, max_lon, min_lat, max_lat = shape.bounds
            except (AttributeError, TypeError, ValueError):
                continue
            if max_lon < west or min_lon > east \
                    or max_lat < south or min_lat > north:
                continue

            path = qtgui.QPainterPath()
            # Odd-even filling makes an interior ring a hole whichever way it is
            # wound, which is what keeps each risk category filled exactly once.
            path.setFillRule(qtcore.Qt.OddEvenFill)
            for ring in shape.rings:
                if len(ring) < 3:
                    continue
                started = False
                for ring_lon, ring_lat in ring:
                    x, y = _map_point(rect, bounds, ring_lat, ring_lon)
                    if started:
                        path.lineTo(x, y)
                    else:
                        path.moveTo(x, y)
                        started = True
                if started:
                    path.closeSubpath()
            if path.isEmpty():
                continue

            fill = getattr(shape, "fill", None)
            if fill:
                colour = qtgui.QColor(fill)
                if colour.isValid():
                    if getattr(shape, "hatch", False):
                        colour.setAlpha(_OVERLAY_HATCH_ALPHA)
                        painter.fillPath(path, _hatch_brush(
                            qtcore, qtgui, colour,
                            getattr(shape, "hatch_level", 0)))
                    else:
                        colour.setAlpha(
                            _OVERLAY_MARKER_FILL_ALPHA
                            if getattr(shape, "marker", False)
                            else _OVERLAY_FILL_ALPHA)
                        painter.fillPath(path, qtgui.QBrush(colour))
            stroke = getattr(shape, "stroke", None)
            if stroke:
                colour = qtgui.QColor(stroke)
                if colour.isValid():
                    painter.strokePath(path, qtgui.QPen(
                        colour, _OVERLAY_STROKE_WIDTH))


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
LOCATOR_HALF_LAT_DEGREES = 0.98

#: Longitude is widened by this before the cosine-of-latitude correction, so the
#: extent matches the inset's landscape aspect and reads as near-square.
LOCATOR_LON_ASPECT = 1.35

#: Room left around an averaged area's outline, as a multiplier on the distance
#: from the sounding to the furthest edge of that area. Enough that the rectangle
#: reads as sitting inside a region rather than being cropped by the frame.
LOCATOR_BOX_MARGIN = 1.18

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
    half_lat = LOCATOR_HALF_LAT_DEGREES
    cos_lat = max(0.35, math.cos(math.radians(lat)))
    if box is not None:
        west, south, east, north = box
        # Measured from the sounding rather than from the box centre, so the
        # rectangle still fits when the point sits off-centre inside it.
        reach_lat = max(abs(north - lat), abs(lat - south))
        reach_lon = max(abs(east - lon), abs(lon - west))
        half_lat = max(
            half_lat,
            reach_lat * LOCATOR_BOX_MARGIN,
            # Convert the longitude requirement into the latitude half-height
            # that produces it, so widening cannot distort the aspect.
            reach_lon * LOCATOR_BOX_MARGIN * cos_lat / LOCATOR_LON_ASPECT,
        )
        half_lat = min(half_lat, LOCATOR_MAX_HALF_LAT_DEGREES)
    half_lon = half_lat * LOCATOR_LON_ASPECT / cos_lat
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


def _draw_box_outline(
        painter: Any,
        box: tuple[float, float, float, float],
        rect: Any,
        bounds: tuple[float, float, float, float],
        color: str,
        qtcore: Any,
        qtgui: Any) -> None:
    """Outline the averaged area inside the locator.

    Dashed and unfilled, in the marker's own colour: it has to read as the extent
    the sounding represents without competing with the boundary linework or
    hiding whatever overlay is washed underneath it.
    """
    west, south, east, north = box
    top_left = _map_point(rect, bounds, north, west)
    bottom_right = _map_point(rect, bounds, south, east)
    outline = qtcore.QRectF(
        qtcore.QPointF(*top_left), qtcore.QPointF(*bottom_right)).normalized()
    pen = qtgui.QPen(qtgui.QColor(color), 1.3)
    pen.setCosmetic(True)
    pen.setStyle(qtcore.Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(qtcore.Qt.NoBrush)
    painter.drawRect(outline)


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
    try:
        box = box_from_widget(widget)
    except Exception:  # noqa: BLE001 - never lose the locator to metadata
        box = None
    bounds = zoom_bounds(lat, lon, box)
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
    overlay_layers = overlay_layers_for_widget(widget)
    overlay_rasters = overlay_rasters_for_widget(widget)

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
        # A one-pixel antialiased rectangle on integer coordinates is split
        # between two pixels, making the nominal white outline look gray. Fill
        # the surface independently, then put the shared plot-frame stroke on
        # half-pixel centers so it resolves to solid foreground white.
        painter.fillRect(rect, QtGui.QBrush(QtGui.QColor(map_fill)))
        # The locator is intentionally anchored in the hodograph's upper-left
        # corner. Reuse the hodograph's crisp top/left frame there, and draw only
        # the locator's right/bottom edges; drawing all four would make that
        # shared corner two pixels thick.
        map_frame = QtCore.QRectF(rect).adjusted(-0.5, -0.5, -0.5, -0.5)
        painter.setPen(QtGui.QPen(
            QtGui.QColor(map_border), colors.PLOT_FRAME_WIDTH))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawLine(map_frame.topRight(), map_frame.bottomRight())
        painter.drawLine(map_frame.bottomRight(), map_frame.bottomLeft())

        padding = 5.0
        interior = rect.adjusted(padding, padding, -padding, -padding)
        painter.save()
        painter.setClipRect(interior)
        # Overlays go under the boundary linework and the marker: they are areal
        # context, and the point being analysed plus the geography locating it
        # must stay readable through them. The gridded field goes under the
        # vector overlays for the same reason it does on the picker map -- the
        # field is the airmass and the risk area is a judgement about it.
        try:
            _draw_overlay_rasters(
                painter, overlay_rasters, interior, bounds, QtCore, QtGui)
        except Exception:  # noqa: BLE001 - never lose the locator to an overlay
            pass
        try:
            _draw_overlay_layers(
                painter, overlay_layers, interior, bounds, QtCore, QtGui)
        except Exception:  # noqa: BLE001 - never lose the locator to an overlay
            pass
        _draw_global_lines(
            painter, global_layers.get("states", ()),
            state_outline, 0.8, interior, bounds, QtCore, QtGui)
        _draw_global_lines(
            painter, global_layers.get("countries", ()),
            country_outline, 1.0, interior, bounds, QtCore, QtGui)
        # A lake shore is the same kind of boundary as a coast, so it takes the
        # same colour. Without this layer the Great Lakes are absent entirely and
        # a sounding beside one has nothing to locate it against.
        _draw_global_lines(
            painter, global_layers.get("lakes", ()),
            coastline, 0.95, interior, bounds, QtCore, QtGui)
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

        if box is not None:
            # An averaged sounding has no single point, so the outline replaces
            # the marker rather than joining it. Drawing both would assert a
            # location the numbers do not have -- the crosshair would name the
            # box's centre as the place this sounding came from.
            try:
                _draw_box_outline(
                    painter, box, interior, bounds, point_color,
                    QtCore, QtGui)
            except Exception:  # noqa: BLE001 - never lose the locator to this
                pass
        else:
            point_x, point_y = _map_point(interior, bounds, lat, lon)
            marker = QtGui.QColor(point_color)
            painter.setPen(QtGui.QPen(marker, 1.4))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(map_fill)))
            painter.drawEllipse(QtCore.QPointF(point_x, point_y), 4.0, 4.0)
            painter.drawLine(point_x - 7.0, point_y, point_x + 7.0, point_y)
            painter.drawLine(point_x, point_y - 7.0, point_x, point_y + 7.0)
        painter.restore()
        if overlay_layers:
            badge = overlay_label_at_point(overlay_layers, lat, lon)
            if badge is not None:
                try:
                    _draw_overlay_badge(painter, badge, rect, QtCore, QtGui)
                except Exception:  # noqa: BLE001
                    pass
        if overlay_rasters:
            # Whichever field is drawn last is the one on top, so that is the one
            # the chip has to name.
            try:
                _draw_field_chip(
                    painter, raster_chip_text(overlay_rasters[-1]), rect,
                    map_fill, map_border, QtCore, QtGui)
            except Exception:  # noqa: BLE001 - never lose the locator to a label
                pass
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
