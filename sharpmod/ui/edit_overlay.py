"""Edit ghost traces, change markers, and drag-coordinate overlays."""

from __future__ import annotations

from sharpmod.ui.features.gui_edit_feedback import (
    _GHOST_ALPHA, _VALUE_TOLERANCE_C, _PRESSURE_TOLERANCE_HPA,
    _MARKER_LIMIT, _VALUE_TOLERANCE_KT, _MARKER_RADIUS,
    _GHOST_TEMP_STYLE, _GHOST_DEWP_STYLE, _GHOST_CAPTION,
    _GHOST_WIND_PATTERN, _GHOST_MAX_AGL_M, _LOGGER,
)

from functools import wraps
from qtpy import QtCore
from qtpy import QtGui
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from sharpmod.ui.features.gui_sounding_readout import finite_number
from sharpmod.sharptab import interp
import math
import numpy.ma as ma
import weakref
from sharpmod.ui.features.gui_edit_feedback import _is_dragging


def _ghost_color(value, fallback=None) -> QColor:
    color = QColor(value if value is not None else fallback)
    if not color.isValid():
        color = QColor("#FFFFFF")
    color.setAlpha(_GHOST_ALPHA)
    return color


def _float_array(source, name):
    try:
        array = ma.asarray(getattr(source, name), dtype=float)
    except (AttributeError, TypeError, ValueError):
        return None
    return array if array.ndim == 1 else None


def wind_component_arrays(profile):
    """Return ``(u, v)`` arrays from whichever wind representation exists.

    A profile restored from a history or session snapshot is a raw SHARPpy
    ``Profile`` whose ``u``/``v`` are ``None`` while ``wdir``/``wspd`` are
    populated -- the upstream ``Profile.copy`` already branches on exactly that.
    A components-only reader would therefore stop drawing and comparing wind
    after any undo or redo, silently and without an error. The inverse below
    matches the direction convention in :func:`wind_from_components`.
    """
    u = _float_array(profile, "u")
    v = _float_array(profile, "v")
    if u is not None and v is not None:
        return u, v
    direction = _float_array(profile, "wdir")
    speed = _float_array(profile, "wspd")
    if direction is None or speed is None:
        return None, None
    radians = direction * (math.pi / 180.0)
    return -speed * ma.sin(radians), -speed * ma.cos(radians)


def level_wind_components(profile, index) -> tuple[float | None, float | None]:
    """Return ``(u, v)`` at one level from whichever wind fields actually exist."""
    u, v = wind_component_arrays(profile)
    if u is None or v is None:
        return None, None
    return _reported(u, index), _reported(v, index)


def _reported(array, index) -> float | None:
    try:
        if ma.getmaskarray(array)[index]:
            return None
        return finite_number(array[index])
    except (IndexError, TypeError, ValueError):
        return None


def edited_levels(ghost, current, fields, *, tolerance=_VALUE_TOLERANCE_C):
    """Return ``(index, field)`` pairs whose reported value actually moved.

    Both sides must share the vertical grid at that index, so an interpolated
    profile yields nothing rather than a fabricated list of differences.
    """
    ghost_pressure = _float_array(ghost, "pres")
    current_pressure = _float_array(current, "pres")
    if ghost_pressure is None or current_pressure is None:
        return []
    if len(ghost_pressure) != len(current_pressure):
        return []
    found = []
    for field in fields:
        before = _float_array(ghost, field)
        after = _float_array(current, field)
        if before is None or after is None:
            continue
        if len(before) != len(ghost_pressure) or len(after) != len(current_pressure):
            continue
        for index in range(len(ghost_pressure)):
            ghost_level = _reported(ghost_pressure, index)
            current_level = _reported(current_pressure, index)
            if ghost_level is None or current_level is None:
                continue
            if abs(ghost_level - current_level) > _PRESSURE_TOLERANCE_HPA:
                continue
            first, second = _reported(before, index), _reported(after, index)
            if first is None or second is None:
                continue
            if abs(first - second) > tolerance:
                found.append((index, field))
                if len(found) >= _MARKER_LIMIT:
                    return found
    return found


def edited_wind_levels(ghost, current, *, tolerance=_VALUE_TOLERANCE_KT) -> list[int]:
    """Return levels whose reported wind moved, on a shared vertical grid."""
    ghost_pressure = _float_array(ghost, "pres")
    current_pressure = _float_array(current, "pres")
    if ghost_pressure is None or current_pressure is None:
        return []
    if len(ghost_pressure) != len(current_pressure):
        return []
    found = []
    for index in range(len(ghost_pressure)):
        before = _reported(ghost_pressure, index)
        after = _reported(current_pressure, index)
        if before is None or after is None:
            continue
        if abs(before - after) > _PRESSURE_TOLERANCE_HPA:
            continue
        ghost_u, ghost_v = level_wind_components(ghost, index)
        current_u, current_v = level_wind_components(current, index)
        if None in (ghost_u, ghost_v, current_u, current_v):
            continue
        if math.hypot(current_u - ghost_u, current_v - ghost_v) > tolerance:
            found.append(index)
            if len(found) >= _MARKER_LIMIT:
                break
    return found


def _draw_change_markers(painter, color, points) -> None:
    """Ring each original value and connect it to the value replacing it."""
    for origin, target in points:
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(color, 2, Qt.SolidLine))
        painter.drawEllipse(
            QtCore.QPointF(origin[0], origin[1]), _MARKER_RADIUS, _MARKER_RADIUS
        )
        if target is None:
            continue
        connector = QtGui.QPen(color, 1, Qt.DotLine)
        painter.setPen(connector)
        painter.drawLine(
            QtCore.QPointF(origin[0], origin[1]),
            QtCore.QPointF(target[0], target[1]),
        )


def _draw_skew_ghost(widget, ghost) -> None:
    """Draw the retained original temperature/dewpoint traces, dashed."""
    bitmap = getattr(widget, "plotBitMap", None)
    if bitmap is None or not callable(getattr(widget, "drawTrace", None)):
        return
    painter = QtGui.QPainter()
    if not painter.begin(bitmap):
        return
    try:
        clip = getattr(widget, "clip", None)
        if clip is not None:
            painter.setClipRect(clip)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        fallback = getattr(widget, "fg_color", "#FFFFFF")
        temp = _ghost_color(getattr(widget, "temp_color", None), fallback)
        dewp = _ghost_color(getattr(widget, "dewp_color", None), fallback)
        colors = {"tmpc": temp, "dwpc": dewp}
        for field, style in (
            ("tmpc", _GHOST_TEMP_STYLE),
            ("dwpc", _GHOST_DEWP_STYLE),
        ):
            data = getattr(ghost, field, None)
            pressure = getattr(ghost, "pres", None)
            if data is None or pressure is None:
                continue
            try:
                widget.drawTrace(
                    data,
                    colors[field],
                    painter,
                    width=1,
                    style=style,
                    p=pressure,
                    label=False,
                )
            except Exception:
                _LOGGER.debug("edit_feedback.skew_ghost_trace_failed", exc_info=True)
        _draw_skew_change_markers(widget, painter, ghost, colors)
        _draw_ghost_caption(widget, painter, temp)
    finally:
        painter.end()


def _draw_skew_change_markers(widget, painter, ghost, colors) -> None:
    """Ring the original temperature/dewpoint at each level an edit moved."""
    try:
        painter.setClipping(True)
        for field in ("tmpc", "dwpc"):
            points = []
            for index, moved in edited_levels(ghost, widget, (field,)):
                if moved != field:
                    continue
                origin = _skew_point(
                    widget,
                    _reported(_float_array(ghost, field), index),
                    _reported(_float_array(ghost, "pres"), index),
                )
                target = _skew_point(
                    widget,
                    _reported(_float_array(widget, field), index),
                    _reported(_float_array(widget, "pres"), index),
                )
                if origin is not None:
                    points.append((origin, target))
            if points:
                _draw_change_markers(painter, colors[field], points)
    except Exception:
        _LOGGER.debug("edit_feedback.skew_marker_failed", exc_info=True)


def _skew_point(widget, value, pressure):
    """Convert one (value, pressure) pair into the widget's pixel space."""
    if value is None or pressure is None:
        return None
    try:
        scale = float(widget.scale) or 1.0
        x = float(widget.originx) + float(widget.tmpc_to_pix(value, pressure)) / scale
        y = float(widget.originy) + float(widget.pres_to_pix(pressure)) / scale
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _draw_ghost_caption(widget, painter, color) -> None:
    """Name the dashed curve where the vendored main title already sits."""
    try:
        height = int(getattr(widget, "title_height", 0) or 0)
        if height <= 0:
            height = max(12, painter.fontMetrics().height())
        painter.setClipping(False)
        font = getattr(widget, "title_font", None)
        if font is not None:
            painter.setFont(font)
        painter.setPen(QtGui.QPen(color, 1, Qt.SolidLine))
        left = int(getattr(widget, "lpad", 0) or 0)
        width = max(160, painter.fontMetrics().horizontalAdvance(_GHOST_CAPTION) + 8)
        painter.drawText(
            QtCore.QRect(left, 2 + height, width, height),
            Qt.TextDontClip | Qt.AlignLeft,
            _GHOST_CAPTION,
        )
        painter.setClipping(True)
    except Exception:
        _LOGGER.debug("edit_feedback.skew_ghost_caption_failed", exc_info=True)


def _draw_hodo_ghost(widget, ghost) -> None:
    """Draw the retained original hodograph trace with a distinct dash pattern."""
    bitmap = getattr(widget, "plotBitMap", None)
    if bitmap is None or not callable(getattr(widget, "uv_to_pix", None)):
        return
    painter = QtGui.QPainter()
    if not painter.begin(bitmap):
        return
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        _draw_hodo_ghost_trace(widget, painter, ghost)
        _draw_hodo_change_markers(widget, painter, ghost)
    except Exception:
        _LOGGER.debug("edit_feedback.hodo_ghost_failed", exc_info=True)
    finally:
        painter.end()


def _draw_hodo_ghost_trace(widget, painter, ghost) -> None:
    """Trace the retained original wind profile with a distinct dash pattern."""
    u, v = wind_component_arrays(ghost)
    height = _float_array(ghost, "hght")
    if u is None or v is None or height is None:
        return
    if not (len(u) == len(v) == len(height)) or len(u) < 2:
        return
    try:
        agl = ma.asarray(interp.to_agl(ghost, height), dtype=float)
    except (AttributeError, IndexError, TypeError, ValueError):
        return
    mask = (
        ma.getmaskarray(u)
        | ma.getmaskarray(v)
        | ma.getmaskarray(height)
        | ma.getmaskarray(agl)
    )
    pen = QtGui.QPen(
        _ghost_color(getattr(widget, "fg_color", "#FFFFFF")), 2, Qt.CustomDashLine
    )
    pen.setDashPattern(list(_GHOST_WIND_PATTERN))
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    path = QtGui.QPainterPath()
    started = False
    for index in range(len(u)):
        if mask[index] or float(agl[index]) > _GHOST_MAX_AGL_M:
            started = False
            continue
        x, y = widget.uv_to_pix(float(u[index]), float(v[index]))
        if not (math.isfinite(float(x)) and math.isfinite(float(y))):
            started = False
            continue
        if started:
            path.lineTo(float(x), float(y))
        else:
            path.moveTo(float(x), float(y))
            started = True
    if not path.isEmpty():
        painter.drawPath(path)


def _draw_hodo_change_markers(widget, painter, ghost) -> None:
    """Ring the original wind vector at each level an edit moved.

    The current values come from ``widget.prof`` rather than ``widget.u``/``v``,
    because the hodograph inserts a synthetic 12 km point into its own arrays
    and their indices therefore no longer match the profile levels the edit
    actually changed.
    """
    current = getattr(widget, "prof", None)
    if current is None:
        return
    try:
        moved = edited_wind_levels(ghost, current)
        if not moved:
            return
        color = _ghost_color(getattr(widget, "fg_color", "#FFFFFF"))
        points = []
        for index in moved:
            origin = _hodo_point(widget, ghost, index)
            target = _hodo_point(widget, current, index)
            if origin is not None:
                points.append((origin, target))
        if points:
            _draw_change_markers(painter, color, points)
    except Exception:
        _LOGGER.debug("edit_feedback.hodo_marker_failed", exc_info=True)


def _hodo_point(widget, profile, index):
    u, v = level_wind_components(profile, index)
    if u is None or v is None:
        return None
    try:
        x, y = widget.uv_to_pix(u, v)
        x, y = float(x), float(y)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def install_surface_overlay(surface, draw) -> bool:
    """Wrap whichever ``plotData`` this surface actually renders through.

    A class-level wrapper is not sufficient. The fork attaches several
    *instance-level* ``plotData`` callables to the composed Skew-T -- the
    dendritic-growth band, the thermal levels, the CAPE fill, and later the
    hidden-profile paint filter -- and an instance attribute shadows the class
    method entirely, so a class wrapper is simply never called. This wraps the
    live attribute the same way those existing mounts do. It runs from the
    controller, which is created after every mount, so the overlay draws last.
    The base callable is reached weakly, adding no new reference cycle on a
    widget whose C++ side Qt owns.
    """
    if surface is None:
        return False
    if getattr(surface, "_sharpmod_original_overlay_attached", False):
        return False
    original = getattr(surface, "plotData", None)
    if not callable(original):
        return False
    surface_ref = weakref.ref(surface)
    if getattr(original, "__self__", None) is not None:
        original_ref = weakref.WeakMethod(original)
    else:
        # Renderer/product mounts install plain instance callbacks. Keep them on
        # their own canvas rather than in a closure that outlives it.
        surface._sharpmod_original_overlay_base = original

        def original_ref():
            live = surface_ref()
            return getattr(live, "_sharpmod_original_overlay_base", None)

    def plot_data(*args, **kwargs):
        base, live = original_ref(), surface_ref()
        if base is None:
            return None
        result = base(*args, **kwargs)
        if live is None:
            return result
        ghost = getattr(live, "_sharpmod_original_profile", None)
        if ghost is not None:
            draw(live, ghost)
        return result

    surface.plotData = plot_data
    surface._sharpmod_original_overlay_attached = True
    return True


def install_original_overlay(sound=None, hodo=None) -> None:
    """Attach the dashed original-profile overlay to both scientific surfaces."""
    install_surface_overlay(sound, _draw_skew_ghost)
    install_surface_overlay(hodo, _draw_hodo_ghost)


def _refresh_drag_coordinates(widget) -> None:
    """Rebuild the Skew-T drag hit targets from the current transform."""
    pressure = getattr(widget, "pres", None)
    if pressure is None:
        return
    try:
        origin_x = float(widget.originx)
        origin_y = float(widget.originy)
        scale = float(widget.scale)
        y = origin_y + widget.pres_to_pix(pressure) / scale
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return
    for name, field in (("drag_tmpc", "tmpc"), ("drag_dwpc", "dwpc")):
        drag = getattr(widget, name, None)
        data = getattr(widget, field, None)
        if drag is None or data is None or _is_dragging(drag):
            continue
        try:
            x = origin_x + widget.tmpc_to_pix(data, pressure) / scale
            drag.setCoords(x, y)
        except Exception:
            _LOGGER.debug("edit_feedback.drag_refresh_failed", exc_info=True)


def install_drag_coordinate_refresh(skew_class=None) -> None:
    """Keep Skew-T edit hit-testing aligned with the widget's current size.

    ``plotSkewT.wheelEvent`` already rebuilds ``drag_tmpc``/``drag_dwpc`` from
    the live transform, but ``resizeEvent`` only replots. Every later click is
    then hit-tested against pixel coordinates from the previous widget size, so
    grabbing the visible temperature curve can miss entirely or start a drag on
    a different level. The viewer resizes this canvas during startup and users
    resize the window freely, so the very first edit of a session is affected.
    """
    if skew_class is None:
        return
    if getattr(skew_class, "_sharpmod_drag_refresh_hook", False):
        return
    original = getattr(skew_class, "resizeEvent", None)
    if not callable(original):
        return

    @wraps(original)
    def resize_event(self, event, __original=original):
        result = __original(self, event)
        _refresh_drag_coordinates(self)
        return result

    skew_class.resizeEvent = resize_event
    skew_class._sharpmod_drag_refresh_hook = True
