"""Synchronized two/four-panel thermodynamic and vertical-difference views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

import numpy as np
from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from sharpmod.colors import semantic_palette
from sharpmod.comparison import exact_profile, vertical_profile_difference
from sharpmod.gui_theme import current_theme, mono_font, ui_font
from sharpmod.theme import OBJ_HINT, OBJ_SECTION_LABEL, SPACE


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except (AttributeError, KeyError, TypeError, ValueError):
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value is None else value


def _current_time(collection):
    try:
        return collection.getCurrentDate()
    except (AttributeError, IndexError, TypeError, ValueError):
        dates = tuple(getattr(collection, "_dates", ()) or ())
        index = getattr(collection, "_prof_idx", 0)
        return dates[index] if 0 <= int(index) < len(dates) else None


def _label(collection, index):
    location = str(_meta(collection, "loc", f"Sounding {index + 1}"))
    model = str(_meta(collection, "model", "") or "").upper()
    run = _meta(collection, "run")
    parts = [location]
    if model:
        parts.append(model)
    if isinstance(run, datetime):
        parts.append(run.strftime("%d %b %H%MZ"))
    return " · ".join(parts)


def _array(profile, field):
    try:
        values = np.ma.asarray(getattr(profile, field), dtype=float).reshape(-1)
    except (AttributeError, TypeError, ValueError):
        return np.asarray([], dtype=float)
    result = np.asarray(values.filled(np.nan), dtype=float)
    result[~np.isfinite(result) | (result <= -9998.0)] = np.nan
    return result


@dataclass(frozen=True)
class _Panel:
    label: str
    profile: object
    difference: object
    reference: bool
    modified: bool


class _ComparisonCanvas(QWidget):
    cursorHeightChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisVisualComparisonCanvas")
        self.setAccessibleName("Synchronized sounding comparison plots")
        self.setMinimumSize(560, 360)
        self.setMouseTracking(True)
        self._panels = ()
        self._layout_count = 2
        self._cursor_height = None
        self._height_range = (0.0, 12_000.0)
        self._temperature_range = (-50.0, 40.0)
        self._panel_rects = ()

    def set_data(self, panels, layout_count):
        self._panels = tuple(panels)[: int(layout_count)]
        self._layout_count = int(layout_count)
        heights = []
        temperatures = []
        for panel in self._panels:
            height = _array(panel.profile, "hght")
            temp = np.concatenate(
                (_array(panel.profile, "tmpc"), _array(panel.profile, "dwpc"))
            )
            heights.extend(height[np.isfinite(height)].tolist())
            temperatures.extend(temp[np.isfinite(temp)].tolist())
        if heights:
            low = math.floor(min(heights) / 500.0) * 500.0
            high = math.ceil(min(max(heights), low + 18_000.0) / 500.0) * 500.0
            self._height_range = (low, max(low + 1000.0, high))
        if temperatures:
            low = math.floor((min(temperatures) - 4.0) / 10.0) * 10.0
            high = math.ceil((max(temperatures) + 4.0) / 10.0) * 10.0
            self._temperature_range = (low, max(low + 20.0, high))
        self.update()

    @staticmethod
    def _trace(qp, points, colour, width=1.7):
        path = QPainterPath()
        active = False
        for point in points:
            if point is None:
                active = False
                continue
            if active:
                path.lineTo(point)
            else:
                path.moveTo(point)
                active = True
        qp.setPen(QPen(colour, width))
        qp.drawPath(path)

    def _layout(self):
        margin = 8.0
        gap = 8.0
        columns = 2
        rows = 1 if self._layout_count == 2 else 2
        width = (self.width() - margin * 2 - gap * (columns - 1)) / columns
        height = (self.height() - margin * 2 - gap * (rows - 1)) / rows
        return tuple(
            QRectF(
                margin + (index % columns) * (width + gap),
                margin + (index // columns) * (height + gap),
                width,
                height,
            )
            for index in range(self._layout_count)
        )

    def _z_to_y(self, value, plot):
        low, high = self._height_range
        return plot.bottom() - (value - low) / max(1.0, high - low) * plot.height()

    def _temperature_x(self, value, plot):
        low, high = self._temperature_range
        return plot.left() + (value - low) / max(1.0, high - low) * plot.width()

    def _difference_x(self, value, plot, span):
        return plot.center().x() + value / span * plot.width() / 2.0

    def paintEvent(self, _event):  # noqa: N802
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        theme = current_theme()
        qp.fillRect(self.rect(), QColor(theme.surface_sunken))
        palette = semantic_palette(theme.surface_sunken, theme.text_primary)
        frame = QColor(theme.border)
        grid = QColor(theme.border)
        grid.setAlpha(130)
        self._panel_rects = self._layout()
        for index, outer in enumerate(self._panel_rects):
            qp.setPen(QPen(frame, 1.0))
            qp.setBrush(Qt.NoBrush)
            qp.drawRoundedRect(outer, 4.0, 4.0)
            if index >= len(self._panels):
                qp.setPen(QColor(theme.text_tertiary))
                qp.setFont(ui_font("small"))
                qp.drawText(outer, Qt.AlignCenter, "No sounding loaded")
                continue
            panel = self._panels[index]
            title = panel.label
            if panel.reference:
                title += " · reference"
            if panel.modified:
                title += " · modified"
            qp.setPen(QColor(theme.text_primary))
            font = ui_font("small")
            font.setBold(True)
            qp.setFont(font)
            qp.drawText(
                QRectF(outer.left() + 8, outer.top() + 4, outer.width() - 16, 20),
                Qt.AlignLeft | Qt.AlignVCenter,
                title,
            )
            plot = QRectF(
                outer.left() + 34,
                outer.top() + 28,
                outer.width() * 0.61,
                outer.height() - 46,
            )
            difference = QRectF(
                plot.right() + 16,
                plot.top(),
                max(25.0, outer.right() - plot.right() - 25),
                plot.height(),
            )
            qp.setFont(mono_font("small"))
            qp.setPen(QPen(grid, 1.0, Qt.DotLine))
            low_z, high_z = self._height_range
            step = 2000.0
            z = math.ceil(low_z / step) * step
            while z <= high_z:
                y = self._z_to_y(z, plot)
                qp.drawLine(QPointF(plot.left(), y), QPointF(difference.right(), y))
                qp.setPen(QColor(theme.text_tertiary))
                qp.drawText(
                    QRectF(outer.left() + 2, y - 8, 29, 16),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{z / 1000:g}",
                )
                qp.setPen(QPen(grid, 1.0, Qt.DotLine))
                z += step
            qp.setPen(QPen(frame, 1.0))
            qp.drawRect(plot)
            qp.drawRect(difference)
            qp.drawLine(
                QPointF(difference.center().x(), difference.top()),
                QPointF(difference.center().x(), difference.bottom()),
            )

            height = _array(panel.profile, "hght")
            for field, colour in (
                ("tmpc", QColor(palette["red"])),
                ("dwpc", QColor(palette["green"])),
            ):
                values = _array(panel.profile, field)
                size = min(height.size, values.size)
                points = []
                for z_value, value in zip(height[:size], values[:size]):
                    if not (math.isfinite(z_value) and math.isfinite(value)):
                        points.append(None)
                    else:
                        points.append(
                            QPointF(
                                self._temperature_x(value, plot),
                                self._z_to_y(z_value, plot),
                            )
                        )
                self._trace(qp, points, colour)

            difference_levels = tuple(getattr(panel.difference, "levels", ()) or ())
            finite_differences = [
                abs(value)
                for level in difference_levels
                for value in (
                    level.temperature_c,
                    level.dewpoint_c,
                    level.u_wind_kt,
                    level.v_wind_kt,
                )
                if value is not None and math.isfinite(value)
            ]
            span = max(5.0, math.ceil(max(finite_differences, default=0.0) / 5.0) * 5.0)
            diff_fields = (
                ("temperature_c", palette["red"]),
                ("dewpoint_c", palette["green"]),
                ("u_wind_kt", palette["blue"]),
                ("v_wind_kt", palette["magenta"]),
            )
            for field, colour in diff_fields:
                points = []
                for level in difference_levels:
                    value = getattr(level, field)
                    if value is None or not math.isfinite(value):
                        points.append(None)
                    else:
                        points.append(
                            QPointF(
                                self._difference_x(value, difference, span),
                                self._z_to_y(level.height_msl_m, difference),
                            )
                        )
                self._trace(qp, points, QColor(colour), 1.25)
            qp.setPen(QColor(theme.text_tertiary))
            qp.drawText(
                QRectF(plot.left(), plot.bottom() + 1, plot.width(), 16),
                Qt.AlignCenter,
                "T / Td (°C)",
            )
            qp.drawText(
                QRectF(difference.left(), difference.bottom() + 1, difference.width(), 16),
                Qt.AlignCenter,
                "ΔT ΔTd Δu Δv",
            )
            if self._cursor_height is not None:
                y = self._z_to_y(self._cursor_height, plot)
                if plot.top() <= y <= plot.bottom():
                    cursor = QColor(theme.accent)
                    qp.setPen(QPen(cursor, 1.2, Qt.DashLine))
                    qp.drawLine(QPointF(plot.left(), y), QPointF(difference.right(), y))

        if self._panels:
            qp.setPen(QColor(theme.text_tertiary))
            qp.setFont(mono_font("small"))
            qp.drawText(4, self.height() - 3, "height: km MSL")
        qp.end()

    @staticmethod
    def _position(event):
        return event.position() if hasattr(event, "position") else event.posF()

    def mouseMoveEvent(self, event):  # noqa: N802
        position = self._position(event)
        for outer in self._panel_rects:
            if not outer.contains(position):
                continue
            plot_top = outer.top() + 28
            plot_height = outer.height() - 46
            fraction = (position.y() - plot_top) / max(1.0, plot_height)
            fraction = min(1.0, max(0.0, fraction))
            low, high = self._height_range
            self._cursor_height = high - fraction * (high - low)
            self.cursorHeightChanged.emit(self._cursor_height)
            self.update()
            return
        if self._cursor_height is not None:
            self._cursor_height = None
            self.cursorHeightChanged.emit(None)
            self.update()

    def leaveEvent(self, _event):  # noqa: N802
        self._cursor_height = None
        self.cursorHeightChanged.emit(None)
        self.update()


class VisualComparisonWidget(QWidget):
    """Controls and synchronized canvas embedded in the Compare workspace."""

    layoutChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisVisualComparison")
        self._collections = ()
        self._reference = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["xs"])
        row = QHBoxLayout()
        label = QLabel("Synchronized profiles")
        label.setObjectName(OBJ_SECTION_LABEL)
        row.addWidget(label)
        row.addStretch(1)
        self.layout_choice = QComboBox()
        self.layout_choice.setObjectName("analysisVisualLayout")
        self.layout_choice.setAccessibleName("Visual comparison panel count")
        self.layout_choice.addItem("2 panels", 2)
        self.layout_choice.addItem("4 panels", 4)
        self.layout_choice.currentIndexChanged.connect(self._layout_changed)
        row.addWidget(self.layout_choice)
        outer.addLayout(row)
        self.canvas = _ComparisonCanvas(self)
        self.canvas.cursorHeightChanged.connect(self._cursor_changed)
        outer.addWidget(self.canvas, 1)
        self.status = QLabel(
            "Solid traces: temperature/dewpoint. Difference strip: candidate minus reference."
        )
        self.status.setObjectName(OBJ_HINT)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def layout_count(self):
        return int(self.layout_choice.currentData() or 2)

    def set_layout_count(self, count):
        index = self.layout_choice.findData(4 if int(count) == 4 else 2)
        if index >= 0:
            self.layout_choice.setCurrentIndex(index)

    def set_collections(self, collections, reference_index=0, valid_time=None):
        self._collections = tuple(collections)
        self._reference = max(
            0, min(int(reference_index), max(0, len(self._collections) - 1))
        )
        if not self._collections:
            self.canvas.set_data((), self.layout_count())
            self.status.setText("Load at least two soundings for visual comparison.")
            return
        reference_collection = self._collections[self._reference]
        reference_time = valid_time or _current_time(reference_collection)
        reference_profile = exact_profile(reference_collection, reference_time)
        panels = []
        # Reference first so it is always visible in both layouts; the remaining
        # soundings retain their acquisition order.
        order = (self._reference,) + tuple(
            index for index in range(len(self._collections)) if index != self._reference
        )
        for index in order[: self.layout_count()]:
            collection = self._collections[index]
            profile = exact_profile(collection, reference_time)
            if profile is None:
                # No nearby-time substitution. A blank panel is less misleading
                # than a beautiful trace for the wrong hour.
                continue
            label = _label(collection, index)
            difference = vertical_profile_difference(
                reference_profile,
                profile,
                reference_label=_label(reference_collection, self._reference),
                candidate_label=label,
            )
            modified = bool(
                _meta(collection, "profile_edited", False)
                or _meta(collection, "scenario_hypothetical", False)
                or any(getattr(collection, "_mod_therm", ()) or ())
                or any(getattr(collection, "_mod_wind", ()) or ())
            )
            panels.append(_Panel(label, profile, difference, index == self._reference, modified))
        self.canvas.set_data(panels, self.layout_count())
        missing = min(self.layout_count(), len(self._collections)) - len(panels)
        message = (
            f"Reference: {_label(reference_collection, self._reference)} · exact valid "
            f"{reference_time:%Y-%m-%d %H:%MZ}"
            if isinstance(reference_time, datetime)
            else f"Reference: {_label(reference_collection, self._reference)} · valid time unknown"
        )
        if missing:
            message += f" · {missing} panel(s) have no profile at that exact time"
        self.status.setText(message)

    def _layout_changed(self, *_args):
        self.layoutChanged.emit(self.layout_count())
        self.set_collections(self._collections, self._reference)

    def _cursor_changed(self, height):
        if height is None:
            return
        self.status.setText(
            f"Shared cursor: {float(height):,.0f} m MSL · gaps remain missing; "
            "plot interpolation is not used for scalar diagnostics."
        )


__all__ = ["VisualComparisonWidget"]
