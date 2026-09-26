"""Painter routines for raster and metadata overlays on the hodograph locator.

They draw products from the context assembled by ``hodo_locator``; this module does not
own acquisition, selection, or the locator widget lifecycle."""

from __future__ import annotations

from sharpmod.maps import locator_presentation
from sharpmod.rendering.map_point_symbols import draw_map_point_symbol
from typing import Any
import math
from sharpmod.viz import hodo_locator as _api


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
        try:
            raster_west, raster_east = raster.bounds[:2]
            west, _south, east, _north = bounds
            first_copy = math.floor((west - raster_east) / 360.0) + 1
            last_copy = math.ceil((east - raster_west) / 360.0) - 1
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
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
            for copy in range(first_copy, last_copy + 1):
                shift = 360.0 * copy
                shifted_view = (
                    bounds[0] - shift, bounds[1],
                    bounds[2] - shift, bounds[3],
                )
                rects = _api.raster_draw_rects(
                    raster, shifted_view, image.width(), image.height(), rect)
                if rects is None:
                    continue
                source, destination = rects
                painter.drawImage(
                    qtcore.QRectF(*destination),
                    image,
                    qtcore.QRectF(*source),
                )
        finally:
            painter.restore()


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
    center = (west + east) / 2.0
    point_symbols = []
    for layer in layers:
        for shape in getattr(layer, "shapes", ()):
            radius = getattr(shape, "marker_radius_px", 0.0) or 0.0
            marker_lon = getattr(shape, "marker_lon", None)
            marker_lat = getattr(shape, "marker_lat", None)
            if radius > 0.0 and marker_lon is not None and marker_lat is not None:
                near_lon = _api._longitude_near(marker_lon, center)
                if west <= near_lon <= east and south <= marker_lat <= north:
                    x, y = _api._map_point(rect, bounds, marker_lat, near_lon)
                    point_symbols.append((x, y, radius, shape))
                continue
            try:
                min_lon, max_lon, min_lat, max_lat = shape.bounds
            except (AttributeError, TypeError, ValueError):
                continue
            if max_lat < south or min_lat > north:
                continue

            # Ordinary shapes need one whole-world shift. A polygon straddling
            # the antimeridian instead needs each vertex wrapped onto the
            # displayed copy, otherwise its 178°E→178°W edge crosses 356°.
            if max_lon - min_lon > 180.0:
                rings = tuple(tuple(
                    (_api._longitude_near(ring_lon, center), ring_lat)
                    for ring_lon, ring_lat in ring
                ) for ring in shape.rings)
                longitudes = [ring_lon for ring in rings
                              for ring_lon, _ring_lat in ring]
                if not longitudes or max(longitudes) < west \
                        or min(longitudes) > east:
                    continue
            else:
                shape_center = (min_lon + max_lon) / 2.0
                shift = _api._longitude_near(shape_center, center) - shape_center
                if max_lon + shift < west or min_lon + shift > east:
                    continue
                rings = tuple(tuple(
                    (ring_lon + shift, ring_lat)
                    for ring_lon, ring_lat in ring
                ) for ring in shape.rings)

            path = qtgui.QPainterPath()
            # Odd-even filling makes an interior ring a hole whichever way it is
            # wound, which is what keeps each risk category filled exactly once.
            path.setFillRule(qtcore.Qt.OddEvenFill)
            for ring in rings:
                if len(ring) < 3:
                    continue
                started = False
                for ring_lon, ring_lat in ring:
                    x, y = _api._map_point(rect, bounds, ring_lat, ring_lon)
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
                        colour.setAlpha(_api._OVERLAY_HATCH_ALPHA)
                        painter.fillPath(path, _api._hatch_brush(
                            qtcore, qtgui, colour,
                            getattr(shape, "hatch_level", 0)))
                    else:
                        colour.setAlpha(
                            _api._OVERLAY_MARKER_FILL_ALPHA
                            if getattr(shape, "marker", False)
                            else _api._OVERLAY_FILL_ALPHA)
                        painter.fillPath(path, qtgui.QBrush(colour))
            stroke = getattr(shape, "stroke", None)
            if stroke:
                colour = qtgui.QColor(stroke)
                if colour.isValid():
                    painter.strokePath(path, qtgui.QPen(
                        colour, _api._OVERLAY_STROKE_WIDTH))
    for x, y, radius, shape in point_symbols:
        draw_map_point_symbol(
            painter, x, y, radius,
            getattr(shape, "marker_symbol", "circle"),
            shape.fill, shape.stroke, qtcore, qtgui,
        )


def overlay_context_for_widget(widget: Any) -> tuple[dict[str, Any], ...]:
    """Describe every selected/live locator overlay with source and status.

    Live payloads provide the actual timestamps and attribution.  Lightweight
    status records fill the gap while a requested layer is loading or when a
    provider has nothing usable, and session descriptors keep source context
    intelligible after heavy geometry/image bytes were deliberately omitted.
    """

    try:
        collection = widget.prof_collections[widget.pc_idx]
    except (AttributeError, IndexError, TypeError):
        return ()
    live = tuple(_api.overlay_rasters_for_widget(widget)) + tuple(
        _api.overlay_layers_for_widget(widget))
    by_key: dict[str, dict[str, Any]] = {}
    for entry in live:
        key = str(getattr(entry, "key", "") or "")
        if not key:
            continue
        actual_time, time_basis = _api._overlay_actual_time(entry)
        by_key[key] = {
            "key": key,
            "title": str(getattr(entry, "title", "") or key),
            "source": str(getattr(entry, "attribution", "") or "Source not stated"),
            "status": "available",
            "actual_time": actual_time,
            "time_basis": time_basis,
            "hazard": key in {"spc_outlook", "storm_reports"},
        }

    # Restored sessions retain these descriptors without the expensive payload.
    try:
        descriptors = collection.getMeta("sharpmod_locator_overlay_descriptors")
    except (AttributeError, KeyError, TypeError, RuntimeError):
        descriptors = getattr(collection, "_meta", {}).get(
            "sharpmod_locator_overlay_descriptors", ())
    if isinstance(descriptors, (list, tuple)):
        for entry in descriptors:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            if not key or key in by_key:
                continue
            actual_time, time_basis = _api._overlay_actual_time(entry)
            by_key[key] = {
                "key": key,
                "title": str(entry.get("title") or key),
                "source": str(entry.get("attribution") or "Source recorded in session"),
                "status": "saved context; reload required",
                "actual_time": actual_time,
                "time_basis": time_basis,
                "hazard": key in {"spc_outlook", "storm_reports"},
            }

    statuses = locator_presentation.overlay_statuses(collection)
    for key, status in statuses.items():
        state = str(status.get("state") or "unavailable")
        if state == "not-selected":
            # A restored descriptor is historical metadata, not permission to
            # continue presenting a layer the user explicitly deselected.
            by_key.pop(key, None)
            continue
        current = by_key.get(key)
        product = str(status.get("product") or "").strip()
        family = str(status.get("family") or "").strip()
        title = product.upper() if product else family.replace("-", " ").title()
        if current is None:
            current = {
                "key": key,
                "title": title or key.replace("_", " ").title(),
                "source": (
                    "NOAA/NWS Storm Prediction Center"
                    if key in {"spc_outlook", "storm_reports"}
                    else "Provider source"
                ),
                "actual_time": None,
                "time_basis": "",
                "hazard": key in {"spc_outlook", "storm_reports"},
            }
            by_key[key] = current
        if current.get("status") != "available" and state != "available":
            detail = str(status.get("detail") or "").strip()
            current["status"] = detail or state.replace("-", " ")
        elif current.get("status") is None:
            current["status"] = "not loaded; reload required"
    # The compact footer can elide later context.  A selected hazard must be
    # first, so its source/status/time do not disappear behind two field rows.
    return tuple(sorted(by_key.values(), key=lambda item: not item["hazard"]))


def _draw_context_footer(
        painter, outer_rect, map_rect, widget, presentation, overlay_context,
        fill, border, qtcore, qtgui):
    """Draw expanded/export provenance below the shared map surface."""

    footer = qtcore.QRectF(
        outer_rect.left(), map_rect.bottom(), outer_rect.width(),
        outer_rect.bottom() - map_rect.bottom())
    painter.fillRect(footer, qtgui.QColor(fill))
    painter.setPen(qtgui.QPen(qtgui.QColor(border), 1.0))
    painter.drawLine(footer.topLeft(), footer.topRight())
    location = _api.location_name_from_widget(widget) or "Unnamed sounding"
    valid = _api._format_utc(_api.valid_time_from_widget(widget)) or "valid time unavailable"
    requested = _api.requested_point_from_widget(widget)
    selected = _api.point_from_widget(widget)
    coordinate_note = ""
    if requested is not None and selected is not None:
        coordinate_note = (
            f" · requested {requested[0]:.3f}, {requested[1]:.3f}; "
            f"sampled {selected[0]:.3f}, {selected[1]:.3f}"
        )
    font = qtgui.QFont("Helvetica", max(8, min(17, round(outer_rect.width() / 110))))
    painter.setFont(font)
    line_height = qtgui.QFontMetrics(font).height() + 2.0
    x = footer.left() + 8.0
    y = footer.top() + 5.0
    width = footer.width() - 16.0
    first = f"{location} · {valid} · {presentation.summary()}{coordinate_note}"
    painter.drawText(
        qtcore.QRectF(x, y, width, line_height),
        qtcore.Qt.AlignLeft | qtcore.Qt.AlignVCenter,
        first,
    )
    y += line_height
    if overlay_context:
        parts = []
        visible_context = overlay_context[:1] if overlay_context[0].get("hazard") \
            else overlay_context[:3]
        for item in visible_context:
            timing_text = _api.overlay_timing_text(item)
            timing = f" · {timing_text}" if timing_text else ""
            hazard = "Hazard: " if item.get("hazard") else ""
            parts.append(
                f"{hazard}{item.get('title')} — {item.get('status')}"
                f"{timing} · {item.get('source')}"
            )
        overlay_line = "   |   ".join(parts)
    else:
        overlay_line = "Overlays: none selected or available"
    metrics = qtgui.QFontMetrics(font)
    overlay_line = metrics.elidedText(overlay_line, qtcore.Qt.ElideRight, int(width))
    painter.drawText(
        qtcore.QRectF(x, y, width, line_height),
        qtcore.Qt.AlignLeft | qtcore.Qt.AlignVCenter,
        overlay_line,
    )
    y += line_height
    attribution = f"Geography: {_api._GEOGRAPHY_ATTRIBUTION}"
    painter.drawText(
        qtcore.QRectF(x, y, width, line_height),
        qtcore.Qt.AlignLeft | qtcore.Qt.AlignVCenter,
        attribution,
    )
