"""Hodograph locator geometry and pixmap-render helpers.

The routines paint shared scale/global lines and box outlines for interactive and
headless rendering; overlay-specific drawing remains in ``hodo_overlay_drawing``."""

from __future__ import annotations

from sharpmod.viz import colors
from typing import Any
from sharpmod.viz import hodo_locator as _api


def _draw_scale_bar(painter, rect, bounds, color, qtcore, qtgui, *, units="metric"):
    spec = _api._scale_bar_spec(bounds, rect.width())
    if spec is None:
        return
    pixel_width, metric_label, km = spec
    if units == "imperial":
        from sharpmod.maps.map_geography import KM_PER_MILE, format_scale_length

        label = format_scale_length(km / KM_PER_MILE, "mi")
    else:
        label = metric_label
    left = rect.center().x() - pixel_width / 2.0
    baseline = rect.bottom() - 7.0
    pen = qtgui.QPen(qtgui.QColor(color), 1.2)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.drawLine(left, baseline, left + pixel_width, baseline)
    painter.drawLine(left, baseline - 3.0, left, baseline + 1.0)
    painter.drawLine(
        left + pixel_width, baseline - 3.0, left + pixel_width, baseline + 1.0)
    font = qtgui.QFont("Helvetica", max(6, min(15, round(rect.width() / 100))))
    painter.setFont(font)
    label_rect = qtcore.QRectF(
        left - 20.0, baseline - 14.0, pixel_width + 40.0, 10.0)
    painter.drawText(label_rect, qtcore.Qt.AlignCenter, label)


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
            x, y = _api._map_point(rect, bounds, lat, lon)
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
    # A panned map may display the 530°–550° copy of a 170°–190° box.
    # Keep the outline on that same visible longitude copy rather than drawing
    # it 360° away (where the painter would silently clip it).
    view_center = (bounds[0] + bounds[2]) / 2.0
    box_center = (west + east) / 2.0
    shift = _api._longitude_near(box_center, view_center) - box_center
    west += shift
    east += shift
    top_left = _api._map_point(rect, bounds, north, west)
    bottom_right = _api._map_point(rect, bounds, south, east)
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
    point = _api.point_from_widget(widget)
    if point is None or not hasattr(widget, "plotBitMap"):
        return False

    try:
        from qtpy import QtCore, QtGui
    except Exception:
        return False

    outer_rect = _api.locator_rect_for_widget(widget, QtCore)
    if outer_rect is None:
        return False

    expanded = bool(getattr(widget, "_sharpmod_locator_expanded", False))
    rect = QtCore.QRectF(outer_rect)
    if expanded:
        footer_height = min(94.0, max(70.0, rect.height() * 0.18))
        rect.setBottom(max(rect.top() + 120.0, rect.bottom() - footer_height))

    lat, lon = point
    location_name = _api.location_name_from_widget(widget)
    try:
        box = _api.box_from_widget(widget)
    except Exception:  # noqa: BLE001 - never lose the locator to metadata
        box = None
    presentation = _api.presentation_from_widget(widget)
    bounds = _api.bounds_from_widget(widget) or _api.zoom_bounds(lat, lon, box)
    try:
        features = _api.county_features_for_point(lat, lon)
    except Exception:
        features = ()
    try:
        county_lines = _api.county_lines_for_bounds(bounds)
    except Exception:
        county_lines = ()
    try:
        global_layers = _api.global_lines_for_bounds(bounds)
    except Exception:
        global_layers = {name: () for name in _api._GLOBAL_LAYER_NAMES}
    overlay_layers = _api.overlay_layers_for_widget(widget)
    overlay_rasters = _api.overlay_rasters_for_widget(widget)
    overlay_context = _api.overlay_context_for_widget(widget)

    bg_color = QtGui.QColor(getattr(widget, "bg_color", _api._MAP_FILL))
    fg_color = QtGui.QColor(getattr(widget, "fg_color", _api._MAP_BORDER))
    if not bg_color.isValid():
        bg_color = QtGui.QColor(_api._MAP_FILL)
    if not fg_color.isValid():
        fg_color = QtGui.QColor(_api._MAP_BORDER)
    if bg_color.lightnessF() >= 0.5:
        background = bg_color.name()
        foreground = fg_color.name()
        semantic = colors.semantic_palette(background, foreground)
        map_fill = background
        map_border = foreground
        state_outline = colors.resolve_theme_color(
            _api._GLOBAL_STATE_OUTLINE, background, foreground, minimum=3.0)
        country_outline = colors.resolve_theme_color(
            _api._GLOBAL_COUNTRY_OUTLINE, background, foreground, minimum=3.0)
        coastline = colors.resolve_theme_color(
            _api._GLOBAL_COASTLINE, background, foreground, minimum=3.0)
        county_outline = foreground
        point_color = semantic["marker_yellow"]
    else:
        # Preserve the established standard/protanopia locator byte-for-byte.
        map_fill = _api._MAP_FILL
        map_border = _api._MAP_BORDER
        state_outline = _api._GLOBAL_STATE_OUTLINE
        country_outline = _api._GLOBAL_COUNTRY_OUTLINE
        coastline = _api._GLOBAL_COASTLINE
        county_outline = _api._COUNTY_OUTLINE
        point_color = _api._POINT_COLOR

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
            _api._draw_overlay_rasters(
                painter, overlay_rasters, interior, bounds, QtCore, QtGui)
        except Exception:  # noqa: BLE001 - never lose the locator to an overlay
            pass
        try:
            _api._draw_overlay_layers(
                painter, overlay_layers, interior, bounds, QtCore, QtGui)
        except Exception:  # noqa: BLE001 - never lose the locator to an overlay
            pass
        _api._draw_global_lines(
            painter, global_layers.get("states", ()),
            state_outline, 0.8, interior, bounds, QtCore, QtGui)
        _api._draw_global_lines(
            painter, global_layers.get("countries", ()),
            country_outline, 1.0, interior, bounds, QtCore, QtGui)
        # A lake shore is the same kind of boundary as a coast, so it takes the
        # same colour. Without this layer the Great Lakes are absent entirely and
        # a sounding beside one has nothing to locate it against.
        _api._draw_global_lines(
            painter, global_layers.get("lakes", ()),
            coastline, 0.95, interior, bounds, QtCore, QtGui)
        _api._draw_global_lines(
            painter, global_layers.get("coastline", ()),
            coastline, 1.15, interior, bounds, QtCore, QtGui)
        _api._draw_global_lines(
            painter, county_lines,
            county_outline, 0.75, interior, bounds, QtCore, QtGui)
        county_pen = QtGui.QPen(QtGui.QColor(county_outline), 1.0)
        county_pen.setCosmetic(True)
        painter.setPen(county_pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        for feature in features:
            for ring in _api._rings(feature.get("geometry")):
                if not isinstance(ring, list) or len(ring) < 2:
                    continue
                path = QtGui.QPainterPath()
                started = False
                for coordinate in ring:
                    if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                        continue
                    ring_lon = _api._as_float(coordinate[0])
                    ring_lat = _api._as_float(coordinate[1])
                    if ring_lat is None or ring_lon is None:
                        continue
                    x, y = _api._map_point(interior, bounds, ring_lat, ring_lon)
                    if started:
                        path.lineTo(x, y)
                    else:
                        path.moveTo(x, y)
                        started = True
                if started:
                    painter.drawPath(path)

        selected_area = presentation.selected_area
        if selected_area is not None and selected_area != box:
            # The active picker box is context around a point sounding, not the
            # area that profile itself represents. Draw it in the same dashed
            # geographic language but keep the point marker as well.
            try:
                _api._draw_box_outline(
                    painter, selected_area, interior, bounds, coastline,
                    QtCore, QtGui)
            except Exception:  # noqa: BLE001 - never lose the locator to this
                pass
        if box is not None:
            # An averaged sounding has no single point, so the outline replaces
            # the marker rather than joining it. Drawing both would assert a
            # location the numbers do not have -- the crosshair would name the
            # box's centre as the place this sounding came from.
            try:
                _api._draw_box_outline(
                    painter, box, interior, bounds, point_color,
                    QtCore, QtGui)
            except Exception:  # noqa: BLE001 - never lose the locator to this
                pass
        else:
            requested = _api.requested_point_from_widget(widget)
            if requested is not None:
                try:
                    _api._draw_requested_point(
                        painter, requested, interior, bounds, point_color,
                        QtCore, QtGui)
                except Exception:  # noqa: BLE001 - a secondary marker is optional
                    pass
            point_x, point_y, point_inside = _api._marker_position(
                interior, bounds, lat, lon)
            marker = QtGui.QColor(point_color)
            point_pen = QtGui.QPen(marker, 1.4)
            if not point_inside:
                point_pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(point_pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(map_fill)))
            painter.drawEllipse(QtCore.QPointF(point_x, point_y), 4.0, 4.0)
            painter.drawLine(point_x - 7.0, point_y, point_x + 7.0, point_y)
            painter.drawLine(point_x, point_y - 7.0, point_x, point_y + 7.0)
        try:
            _api._draw_scale_bar(
                painter, interior, bounds, map_border, QtCore, QtGui,
                units=presentation.scale_units)
        except Exception:  # noqa: BLE001 - context must not block the locator
            pass
        painter.restore()
        if overlay_layers:
            badge = _api.overlay_label_at_point(overlay_layers, lat, lon)
            if badge is not None:
                try:
                    _api._draw_overlay_badge(painter, badge, rect, QtCore, QtGui)
                except Exception:  # noqa: BLE001
                    pass
        if overlay_rasters:
            # Whichever field is drawn last is the one on top, so that is the one
            # the chip has to name.
            try:
                _api._draw_field_chip(
                    painter, _api.raster_chip_text(overlay_rasters[-1]), rect,
                    map_fill, map_border, QtCore, QtGui)
            except Exception:  # noqa: BLE001 - never lose the locator to a label
                pass
        if location_name or expanded:
            font = QtGui.QFont(
                "Helvetica", 8 if not expanded
                else max(10, min(18, round(rect.width() / 100))))
            font.setBold(True)
            painter.setFont(font)
            metrics = QtGui.QFontMetrics(font)
            full_title = (
                f"Locator · {location_name}" if location_name else "Sounding locator"
            )
            title_room = max(40.0, rect.width() * (0.64 if not expanded else 0.72))
            title = metrics.elidedText(
                full_title,
                QtCore.Qt.ElideRight,
                max(1, int(title_room) - 8),
            )
            title_rect = QtCore.QRectF(
                rect.left() + 4.0,
                rect.top() + 3.0,
                title_room,
                metrics.height() + 3.0,
            )
            title_background = QtGui.QColor(map_fill)
            title_background.setAlpha(220)
            painter.fillRect(title_rect, title_background)
            painter.setPen(QtGui.QPen(QtGui.QColor(map_border), 1.0))
            painter.drawText(
                title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
        try:
            _api._draw_mode_chip(
                painter, presentation, rect, map_fill, map_border,
                QtCore, QtGui)
        except Exception:  # noqa: BLE001 - optional state label
            pass
        if expanded:
            try:
                _api._draw_context_footer(
                    painter, outer_rect, rect, widget, presentation,
                    overlay_context, map_fill, map_border, QtCore, QtGui)
            except Exception:  # noqa: BLE001 - map remains useful without footer
                pass
        return True
    finally:
        painter.end()


def render_locator_pixmap(
        widget: Any,
        width: int = 1200,
        height: int = 760,
        *,
        expanded: bool = True):
    """Render a detached locator using the focused inset's exact state.

    No live widget is resized and no selection signal exists on the detached
    proxy.  The profile collection, presentation, overlay payloads, theme, and
    focus index are shared read-only, which makes this the export-facing seam
    rather than a second map implementation.
    """

    from qtpy import QtCore, QtGui

    width = max(320, int(width))
    height = max(220, int(height))
    pixmap = QtGui.QPixmap(width, height)
    background = QtGui.QColor(getattr(widget, "bg_color", _api._MAP_FILL))
    if not background.isValid():
        background = QtGui.QColor(_api._MAP_FILL)
    pixmap.fill(background)

    class _RenderProxy:
        pass

    proxy = _RenderProxy()
    proxy.plotBitMap = pixmap
    proxy.prof_collections = getattr(widget, "prof_collections", ())
    proxy.pc_idx = int(getattr(widget, "pc_idx", 0) or 0)
    proxy.prof = getattr(widget, "prof", None)
    proxy.bg_color = background
    proxy.fg_color = getattr(widget, "fg_color", QtGui.QColor(_api._MAP_BORDER))
    proxy.tlx = 0
    proxy.tly = 0
    proxy._sharpmod_locator_rect = QtCore.QRectF(
        0.5, 0.5, width - 1.0, height - 1.0)
    proxy._sharpmod_locator_expanded = bool(expanded)
    _api.draw_hodo_locator(proxy)
    return pixmap
