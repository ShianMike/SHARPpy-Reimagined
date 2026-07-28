"""Numbered height markers for the active hodograph profile."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from sharpmod import colors
from sharpmod.viz.hodo_locator import locator_rect_for_widget


HEIGHT_LEVELS_M = (500.0, 1000.0, 3000.0, 6000.0, 9000.0, 12000.0)
MARKER_RADIUS_PX = 6.0
_MARKER_GAP_PX = 2.0


def _label_for_height(height_m: float) -> str:
    height_km = float(height_m) / 1000.0
    return f"{height_km:.1f}".rstrip("0").rstrip(".")


def _marker_font_pixel_size(label: str) -> int:
    """Leave extra circle-edge clearance for multi-character labels."""

    return 7 if str(label) == "0.5" else 8


def _marker_font_letter_spacing(label: str) -> float:
    """Tighten only ``0.5`` without horizontally deforming its glyphs."""

    return -0.5 if str(label) == "0.5" else 0.0


def height_level_points(
    widget: Any,
    levels: Iterable[float] = HEIGHT_LEVELS_M,
) -> tuple[tuple[str, float, float, float], ...]:
    """Return ``(label, height_m, x, y)`` points for available AGL levels."""

    profile = getattr(widget, "prof", None)
    transform = getattr(widget, "uv_to_pix", None)
    if profile is None or not callable(transform):
        return ()
    try:
        from sharppy.sharptab import interp

        height = np.ma.asarray(
            interp.to_agl(profile, profile.hght), dtype=float)
        u_wind = np.ma.asarray(profile.u, dtype=float)
        v_wind = np.ma.asarray(profile.v, dtype=float)
        mask = (
            np.ma.getmaskarray(height)
            | np.ma.getmaskarray(u_wind)
            | np.ma.getmaskarray(v_wind)
        )
        height = np.asarray(height.filled(np.nan), dtype=float)
        u_wind = np.asarray(u_wind.filled(np.nan), dtype=float)
        v_wind = np.asarray(v_wind.filled(np.nan), dtype=float)
        valid = ~mask & np.isfinite(height) & np.isfinite(u_wind) \
            & np.isfinite(v_wind)
        height = height[valid]
        u_wind = u_wind[valid]
        v_wind = v_wind[valid]
    except (AttributeError, TypeError, ValueError):
        return ()
    if height.size < 2:
        return ()

    order = np.argsort(height, kind="stable")
    height = height[order]
    u_wind = u_wind[order]
    v_wind = v_wind[order]
    unique_height, unique_indices = np.unique(height, return_index=True)
    height = unique_height
    u_wind = u_wind[unique_indices]
    v_wind = v_wind[unique_indices]
    if height.size < 2:
        return ()

    points = []
    for level in levels:
        level = float(level)
        if not math.isfinite(level) or level < height[0] or level > height[-1]:
            continue
        u_value = float(np.interp(level, height, u_wind))
        v_value = float(np.interp(level, height, v_wind))
        try:
            x, y = transform(u_value, v_value)
            x = float(np.asarray(x).reshape(-1)[0])
            y = float(np.asarray(y).reshape(-1)[0])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((_label_for_height(level), level, x, y))
    return tuple(points)


def _marker_color(widget: Any, height_m: float, qtgui: Any):
    if height_m <= 500.0:
        return qtgui.QColor("#ff00ff")
    colors = tuple(getattr(widget, "colors", ()) or ())
    index = 0 if height_m <= 3000.0 else (
        1 if height_m <= 6000.0 else (
            2 if height_m <= 9000.0 else 3
        )
    )
    if index < len(colors):
        candidate = qtgui.QColor(colors[index])
        if candidate.isValid():
            return candidate
    return qtgui.QColor(getattr(widget, "fg_color", "#ffffff"))


def _marker_text_color(marker_color: Any, qtgui: Any):
    """Use one crisp black numeral style for every marker and palette."""

    del marker_color
    return qtgui.QColor(colors.BLACK)


def _rect_bounds(rect: Any) -> tuple[float, float, float, float]:
    """Normalize a QRect-like object or four-item tuple."""
    if all(callable(getattr(rect, name, None)) for name in (
            "left", "top", "right", "bottom")):
        return (
            float(rect.left()),
            float(rect.top()),
            float(rect.right()),
            float(rect.bottom()),
        )
    left, top, right, bottom = rect
    return float(left), float(top), float(right), float(bottom)


def _inside_marker_bounds(
        x: float,
        y: float,
        bounds: tuple[float, float, float, float],
        radius: float) -> bool:
    left, top, right, bottom = bounds
    return (
        left + radius <= x <= right - radius
        and top + radius <= y <= bottom - radius
    )


def _overlaps_exclusion(
        x: float,
        y: float,
        exclusion: tuple[float, float, float, float],
        radius: float) -> bool:
    left, top, right, bottom = exclusion
    return (
        x + radius > left
        and x - radius < right
        and y + radius > top
        and y - radius < bottom
    )


def _projection_candidates(
        x: float,
        y: float,
        exclusion: tuple[float, float, float, float],
        radius: float) -> tuple[tuple[float, float], ...]:
    """Return deterministic nearest positions immediately outside a rectangle."""
    left, top, right, bottom = exclusion
    clearance = radius + _MARKER_GAP_PX
    candidates = (
        (right + clearance, y),
        (x, bottom + clearance),
        (left - clearance, y),
        (x, top - clearance),
    )
    return tuple(sorted(
        candidates,
        key=lambda point: (
            (point[0] - x) ** 2 + (point[1] - y) ** 2,
            candidates.index(point),
        ),
    ))


def _candidate_centers(
        x: float,
        y: float,
        exclusions: tuple[tuple[float, float, float, float], ...],
        minimum_separation: float,
        radius: float) -> tuple[tuple[float, float], ...]:
    """Generate stable nearby marker centers, including exclusion projections."""
    seeds = [(x, y)]
    for exclusion in exclusions:
        if _overlaps_exclusion(x, y, exclusion, radius):
            seeds.extend(_projection_candidates(
                x, y, exclusion, radius))

    # Cardinal directions come first so tightly packed markers remain easy to
    # scan; diagonal and 30-degree slots provide enough positions for all six
    # standard levels even when their winds are identical.
    angles = (
        0.0, 90.0, 180.0, 270.0,
        45.0, 135.0, 225.0, 315.0,
        30.0, 60.0, 120.0, 150.0,
        210.0, 240.0, 300.0, 330.0,
    )
    candidates: list[tuple[float, float]] = list(seeds)
    for seed_x, seed_y in seeds:
        for ring in range(1, 6):
            distance = minimum_separation * ring
            for angle in angles:
                radians = math.radians(angle)
                candidates.append((
                    seed_x + math.cos(radians) * distance,
                    seed_y + math.sin(radians) * distance,
                ))
    # Preserve order while removing candidates repeated by multiple seeds.
    return tuple(dict.fromkeys(
        (round(cx, 6), round(cy, 6)) for cx, cy in candidates))


def layout_height_level_markers(
        points: Iterable[tuple[str, float, float, float]],
        bounds: tuple[float, float, float, float],
        exclusions: Iterable[Any] = (),
        radius: float = MARKER_RADIUS_PX,
) -> tuple[tuple[str, float, float, float, float, float], ...]:
    """Lay out marker centers without marker or annotation overlap.

    Each result is ``(label, height_m, source_x, source_y, center_x,
    center_y)``.  Source coordinates preserve the exact wind location so the
    painter can add a short leader when a dot needs to move.
    """
    radius = float(radius)
    if not math.isfinite(radius) or radius <= 0.0:
        return ()
    try:
        normalized_bounds = tuple(float(value) for value in bounds)
    except (TypeError, ValueError):
        return ()
    if (
        len(normalized_bounds) != 4
        or not all(math.isfinite(value) for value in normalized_bounds)
        or normalized_bounds[0] >= normalized_bounds[2]
        or normalized_bounds[1] >= normalized_bounds[3]
    ):
        return ()

    normalized_exclusions = []
    for exclusion in exclusions:
        try:
            candidate = _rect_bounds(exclusion)
        except (TypeError, ValueError):
            continue
        if (
            all(math.isfinite(value) for value in candidate)
            and candidate[0] < candidate[2]
            and candidate[1] < candidate[3]
        ):
            normalized_exclusions.append(candidate)
    normalized_exclusions = tuple(normalized_exclusions)

    minimum_separation = radius * 2.0 + _MARKER_GAP_PX
    placed: list[tuple[float, float]] = []
    layout = []
    for label, height_m, source_x, source_y in points:
        try:
            source_x = float(source_x)
            source_y = float(source_y)
        except (TypeError, ValueError):
            continue
        if (
            not math.isfinite(source_x)
            or not math.isfinite(source_y)
            or not _inside_marker_bounds(
                source_x, source_y, normalized_bounds, radius)
        ):
            continue

        center = None
        for candidate_x, candidate_y in _candidate_centers(
                source_x, source_y, normalized_exclusions,
                minimum_separation, radius):
            if not _inside_marker_bounds(
                    candidate_x, candidate_y, normalized_bounds, radius):
                continue
            if any(_overlaps_exclusion(
                    candidate_x, candidate_y, exclusion, radius)
                    for exclusion in normalized_exclusions):
                continue
            if any(math.hypot(
                    candidate_x - placed_x, candidate_y - placed_y)
                    < minimum_separation
                    for placed_x, placed_y in placed):
                continue
            center = candidate_x, candidate_y
            break

        if center is None:
            continue
        placed.append(center)
        layout.append((
            str(label),
            float(height_m),
            source_x,
            source_y,
            center[0],
            center[1],
        ))
    return tuple(layout)


def _height_level_exclusions(widget: Any, qtcore: Any) -> tuple[Any, ...]:
    """Return locator and scientific-annotation rectangles to avoid."""
    exclusions = []
    locator_rect = locator_rect_for_widget(widget, qtcore)
    if locator_rect is not None:
        exclusions.append(locator_rect)
    annotations = getattr(
        widget, "_sharpmod_hodo_annotation_rects", ()) or ()
    for rect in tuple(annotations):
        try:
            exclusions.append(qtcore.QRectF(rect))
        except (TypeError, ValueError):
            continue
    return tuple(exclusions)


def draw_hodo_height_levels(
        widget: Any,
        *,
        painter: Any | None = None,
) -> bool:
    """Draw each AGL height number inside a compact colored dot.

    When ``painter`` is supplied, coordinates remain in widget-logical pixels
    but Qt rasterizes the overlay at the painter's target scale. The live
    hodograph paint path uses this form so HD/UHD exports do not enlarge tiny
    numerals from the one-pixel-density ``plotBitMap`` cache.
    """

    points = height_level_points(widget)
    if not points or not hasattr(widget, "plotBitMap"):
        return False
    try:
        from qtpy import QtCore, QtGui
    except Exception:
        return False

    left = float(getattr(widget, "tlx", 0)) + 2.0
    top = float(getattr(widget, "tly", 0)) + 2.0
    right = float(getattr(
        widget, "brx", widget.plotBitMap.width())) - 2.0
    bottom = float(getattr(
        widget, "bry", widget.plotBitMap.height())) - 2.0
    if right <= left or bottom <= top:
        return False
    exclusions = _height_level_exclusions(widget, QtCore)
    layout = layout_height_level_markers(
        points, (left, top, right, bottom), exclusions=exclusions)
    if not layout:
        return False

    owns_painter = painter is None
    if owns_painter:
        painter = QtGui.QPainter(widget.plotBitMap)
    elif not painter.isActive():
        return False
    drawn = 0
    try:
        if not owns_painter:
            painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        base_font = QtGui.QFont(getattr(
            widget, "label_font", QtGui.QFont("Helvetica", 7)))
        base_font.setBold(True)
        radius = MARKER_RADIUS_PX

        for label, height_m, source_x, source_y, x, y in layout:
            label_font = QtGui.QFont(base_font)
            label_font.setPixelSize(_marker_font_pixel_size(label))
            label_font.setStretch(100)
            letter_spacing = _marker_font_letter_spacing(label)
            if letter_spacing:
                label_font.setLetterSpacing(
                    QtGui.QFont.AbsoluteSpacing, letter_spacing)
            painter.setFont(label_font)
            marker_color = _marker_color(widget, height_m, QtGui)
            border_color = QtGui.QColor(
                getattr(widget, "fg_color", "#ffffff"))
            if math.hypot(x - source_x, y - source_y) > 1.0:
                leader = QtGui.QColor(border_color)
                leader.setAlpha(190)
                leader_pen = QtGui.QPen(leader, 0.75)
                leader_pen.setCosmetic(True)
                painter.setPen(leader_pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawLine(
                    QtCore.QPointF(source_x, source_y),
                    QtCore.QPointF(x, y),
                )
            painter.setPen(QtGui.QPen(border_color, 0.8))
            painter.setBrush(QtGui.QBrush(marker_color))
            marker_rect = QtCore.QRectF(
                x - radius, y - radius, radius * 2.0, radius * 2.0
            )
            painter.drawEllipse(marker_rect)
            text_color = _marker_text_color(marker_color, QtGui)
            painter.setPen(QtGui.QPen(text_color, 1.0))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawText(marker_rect, QtCore.Qt.AlignCenter, label)
            drawn += 1
    finally:
        if owns_painter:
            painter.end()
        else:
            painter.restore()
    return drawn > 0
