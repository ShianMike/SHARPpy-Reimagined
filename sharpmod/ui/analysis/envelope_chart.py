"""Ensemble thermodynamic envelope chart."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
import math
from typing import NamedTuple

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF,
)
from qtpy.QtWidgets import QSizePolicy, QWidget

from sharpmod.core import local_time
from sharpmod.analysis.box_analysis import parameter
from sharpmod.viz.colors import semantic_palette
from sharpmod.ui.features.gui_theme import current_theme, mono_font, ui_font
from sharpmod.ui.styles.theme import SPACE

from sharpmod.ui.analysis.charts import (
    _STANDARD_ISOBARS,
    _THERMO_RANGE_C,
    _THERMO_STEP_C,
    _chart_ink,
    _get,
    _inset,
    _number
)


class _EnvelopeChart(QWidget):
    """Temperature/dewpoint p10-p90 ribbons from pre-computed ensemble bands."""

    TEMPERATURE_STYLE = Qt.SolidLine
    DEWPOINT_STYLE = Qt.DashLine

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisEnsembleBandChart")
        self.setMinimumHeight(235)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAccessibleName("Ensemble thermodynamic envelope")
        self.setToolTip(
            "Shaded ribbons span the 10th to 90th member percentile. Median "
            "temperature is solid; median dewpoint (Td) is dashed. The "
            "temperature scale is fixed to "
            "\u221250 \u00b0C \u2013 +50 \u00b0C so runs stay comparable. "
            "Focus the chart and use Up/Down or Home/End to read reported levels."
        )
        self._label_font = ui_font("caption")
        self._value_font = mono_font("caption")
        self._temperature = ()
        self._dewpoint = ()
        self._pressure_bounds = (100.0, 1000.0)
        self._temperature_bounds = _THERMO_RANGE_C
        self._level_index = None
        self._pressures = ()
        self._help_text = self.toolTip()
        self.setAccessibleDescription("No ensemble envelope data yet.")

    @staticmethod
    def _curve(levels, distributions):
        points = []
        for pressure_value, distribution in zip(levels, distributions):
            pressure = _number(pressure_value)
            low = _number(_get(distribution, "p10"))
            median = _number(_get(distribution, "median"))
            high = _number(_get(distribution, "p90"))
            if pressure is None or pressure <= 0 or None in (low, median, high):
                continue
            points.append((pressure, low, median, high))
        return tuple(points)

    def set_band(self, bands):
        selected = self._pressures[self._level_index] if self._level_index is not None else None
        # ``profile_metrics`` returns one immutable band record per pressure.
        # Accept the former column-shaped form too, so a saved/plugin-provided
        # result can still be displayed without teaching paintEvent about it.
        records = tuple(bands or ())
        if records and _number(_get(records[0], "pressure_hpa")) is not None:
            levels = tuple(_get(item, "pressure_hpa") for item in records)
            temperatures = tuple(_get(item, "temperature") for item in records)
            dewpoints = tuple(_get(item, "dewpoint") for item in records)
        elif bands:
            levels = tuple(_get(bands, "pressure_hpa", ()) or ())
            temperatures = tuple(_get(bands, "temperature", ()) or ())
            dewpoints = tuple(_get(bands, "dewpoint", ()) or ())
        else:
            levels = temperatures = dewpoints = ()
        self._temperature = self._curve(levels, temperatures)
        self._dewpoint = self._curve(levels, dewpoints)
        all_points = self._temperature + self._dewpoint
        if all_points:
            pressures = [item[0] for item in all_points]
            self._pressure_bounds = min(pressures), max(pressures)
        else:
            self._pressure_bounds = (100.0, 1000.0)
        self._temperature_bounds = _THERMO_RANGE_C
        self._pressures = tuple(sorted({point[0] for point in all_points}, reverse=True))
        self._level_index = self._pressures.index(selected) if selected in self._pressures else None
        if all_points:
            pressures = [point[0] for point in all_points]
            medians = [point[2] for point in all_points]
            self.setAccessibleDescription(
                "Ensemble thermodynamic envelope. Shaded ribbons show the "
                "10th to 90th member percentiles. Median temperature is solid; "
                "median dewpoint is dashed. "
                f"{len(self._temperature)} temperature level(s) and "
                f"{len(self._dewpoint)} dewpoint level(s) span "
                f"{min(pressures):g} to {max(pressures):g} hPa; median values "
                f"span {min(medians):g} to {max(medians):g} degrees Celsius."
            )
        else:
            self.setAccessibleDescription(
                "Ensemble thermodynamic envelope. No ensemble members are "
                "available at this valid time."
            )
        self._accessible_summary = self.accessibleDescription()
        # Every reported value is available as text, not just an aggregate
        # range. There is no interpolation or substitution for a missing level.
        if self._pressures:
            self._accessible_summary += " " + " ".join(
                self._level_readout(pressure) for pressure in self._pressures
            )
        reading = self._level_readout(selected) if self._level_index is not None else ""
        self.setAccessibleDescription(self._accessible_summary + (f" Focused level: {reading}" if reading else ""))
        self.setToolTip(reading or self._help_text)
        self.update()

    def _level_readout(self, pressure):
        readings = [f"{pressure:g} hPa."]
        for name, points in (("Temperature", self._temperature), ("Dewpoint", self._dewpoint)):
            record = next((point for point in points if point[0] == pressure), None)
            if record is None:
                readings.append(f"{name}: not reported at this level.")
            else:
                _pressure, low, median, high = record
                readings.append(
                    f"{name}: p10 {low:.1f} \u00b0C, median {median:.1f} \u00b0C, "
                    f"p90 {high:.1f} \u00b0C."
                )
        return " ".join(readings)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if self._pressures and key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
            if key == Qt.Key_Home:
                index = 0
            elif key == Qt.Key_End:
                index = len(self._pressures) - 1
            elif self._level_index is None:
                index = 0
            else:
                index = self._level_index + (1 if key == Qt.Key_Up else -1)
            self._level_index = max(0, min(index, len(self._pressures) - 1))
            reading = self._level_readout(self._pressures[self._level_index])
            self.setAccessibleDescription(f"{self._accessible_summary} Focused level: {reading}")
            self.setToolTip(f"{self._help_text}\n{reading}")
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def _plot_area(self):
        """Reserve gutters for the pressure labels and the temperature axis title."""
        values = QFontMetricsF(self._value_font)
        labels = QFontMetricsF(self._label_font)
        widest = max(
            (values.horizontalAdvance(f"{level}") for level in _STANDARD_ISOBARS),
            default=0.0,
        )
        left = SPACE["sm"] + widest + SPACE["xs"]
        top = SPACE["xs"] + labels.height() + SPACE["sm"]
        # Two stacked rows below the plot: the tick values, then the axis title.
        bottom = SPACE["xs"] + values.height() + labels.height() + SPACE["xs"]
        return _inset(self, left, top, SPACE["md"], bottom)

    def _point(self, pressure, value):
        rect = self._plot_area()
        x_min, x_max = self._temperature_bounds
        p_min, p_max = self._pressure_bounds
        x = rect.left() + (value - x_min) / (x_max - x_min) * rect.width()
        if p_min == p_max:
            y = rect.center().y()
        else:
            log_min, log_max = math.log(p_min), math.log(p_max)
            y = (
                rect.top()
                + ((math.log(pressure) - log_min) / (log_max - log_min)) * rect.height()
            )
        return QPointF(x, y)

    def _isobars(self):
        """Standard levels inside the plotted band, plus the band's own edges."""
        p_min, p_max = self._pressure_bounds
        if p_min >= p_max:
            return (p_min,)
        inside = [
            float(level) for level in _STANDARD_ISOBARS if p_min < level < p_max
        ]
        return tuple(sorted({float(p_min), *inside, float(p_max)}))

    def _isobar_labels(self, metrics):
        """Label isobars bottom-up, dropping any that would overprint a neighbour.

        Pressure is plotted logarithmically, so 1000/925/850 crowd into a few
        pixels at the bottom of the axis and would otherwise render as one
        illegible smear. Gridlines stay for all of them; only labels thin out.
        """
        gap = metrics.height() * 0.95
        labelled = []
        last_y = None
        for level in reversed(self._isobars()):
            y = self._point(level, self._temperature_bounds[0]).y()
            if last_y is not None and abs(last_y - y) < gap:
                continue
            labelled.append((level, y))
            last_y = y
        return tuple(labelled)

    def _draw_curve(self, painter, points, color, style):
        if not points:
            return
        low = [self._point(item[0], item[1]) for item in points]
        high = [self._point(item[0], item[3]) for item in points]
        polygon = QPolygonF(low + list(reversed(high)))
        fill = QColor(color)
        fill.setAlpha(58)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(polygon)
        median_path = QPainterPath()
        for index, item in enumerate(points):
            point = self._point(item[0], item[2])
            median_path.moveTo(point) if index == 0 else median_path.lineTo(point)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(color), 2.0, style))
        painter.drawPath(median_path)

    def _draw_legend(self, painter, rect, ink, metrics):
        """Caption plus right-aligned swatch chips, thinned when space runs out.

        The previous legend hand-positioned three strings on one baseline with a
        literal 14px gap, so in a narrow dock the caption and the series names
        overlapped. Here the caption is dropped first, then the chip labels
        shorten, and the widget tooltip carries the meaning either way.
        """
        swatch = metrics.ascent() * 1.8
        gap = SPACE["xs"]
        baseline = SPACE["xs"] + metrics.ascent()
        available = rect.right()
        # Starts at the plot edge, clear of the pressure-axis gutter the unit
        # label occupies.
        caption_x = rect.left()

        for labels in (("Temperature", "Td"), ("Temp", "Td")):
            entries = tuple(zip(
                labels, (ink.temperature, ink.dewpoint),
                (self.TEMPERATURE_STYLE, self.DEWPOINT_STYLE),
            ))
            widths = [
                swatch + gap + metrics.horizontalAdvance(text)
                for text, _colour, _style in entries
            ]
            needed = sum(widths) + SPACE["md"] * (len(entries) - 1)
            if caption_x + needed > available:
                continue
            caption = "p10\u2013p90 band, median line"
            if caption_x + metrics.horizontalAdvance(caption) + SPACE["lg"] + needed \
                    <= available:
                painter.setPen(ink.title)
                painter.drawText(QPointF(caption_x, baseline), caption)
            x = available - needed
            for (text, colour, style), width in zip(entries, widths):
                painter.setPen(QPen(QColor(colour), 2.0, style))
                painter.drawLine(
                    QPointF(x, baseline - metrics.ascent() / 2.0),
                    QPointF(x + swatch, baseline - metrics.ascent() / 2.0),
                )
                painter.setBrush(Qt.NoBrush)
                painter.setPen(ink.title)
                painter.drawText(QPointF(x + swatch + gap, baseline), text)
                x += width + SPACE["md"]
            return

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        self._label_font = ui_font("caption")
        self._value_font = mono_font("caption")
        ink = _chart_ink()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), ink.background)
        rect = self._plot_area()
        label_metrics = QFontMetricsF(self._label_font)
        value_metrics = QFontMetricsF(self._value_font)

        if not self._temperature and not self._dewpoint:
            painter.setFont(self._label_font)
            painter.setPen(QPen(ink.frame, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            painter.setPen(ink.axis)
            painter.drawText(
                rect, Qt.AlignCenter, "No ensemble members at this valid time"
            )
            self._draw_focus(painter, rect, ink)
            return

        # Isobars. Without these the pressure axis printed two numbers and the
        # profile floated in an unreadable void.
        painter.setFont(self._value_font)
        painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
        for level in self._isobars():
            y = self._point(level, self._temperature_bounds[0]).y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(ink.axis)
        for level, y in self._isobar_labels(value_metrics):
            painter.drawText(
                QRectF(
                    0.0,
                    y - value_metrics.height() / 2.0,
                    rect.left() - SPACE["xs"],
                    value_metrics.height(),
                ),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{level:.0f}",
            )

        # Isotherms every 25 C, so a labelled line always lands on 0 C.
        x_min, x_max = self._temperature_bounds
        value = x_min
        while value <= x_max + 1e-9:
            x = self._point(self._pressure_bounds[0], value).x()
            if value == 0.0:
                painter.setPen(QPen(ink.freezing, 1.2, Qt.DashLine))
            else:
                painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            text = f"{value:.0f}"
            width = value_metrics.horizontalAdvance(text) + SPACE["sm"]
            painter.setPen(ink.freezing if value == 0.0 else ink.axis)
            painter.drawText(
                QRectF(
                    min(max(0.0, x - width / 2.0), self.width() - width),
                    rect.bottom() + SPACE["xs"],
                    width,
                    value_metrics.height(),
                ),
                Qt.AlignHCenter | Qt.AlignTop,
                text,
            )
            value += _THERMO_STEP_C

        painter.save()
        painter.setClipRect(rect)
        self._draw_curve(painter, self._temperature, ink.temperature,
                         self.TEMPERATURE_STYLE)
        self._draw_curve(painter, self._dewpoint, ink.dewpoint, self.DEWPOINT_STYLE)
        painter.restore()

        painter.setPen(QPen(ink.frame, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        painter.setFont(self._label_font)
        self._draw_legend(painter, rect, ink, label_metrics)
        # Axis titles carry the units, so no tick label has to hang a stray
        # " C" off the rightmost number the way the old axis read "-50 0 50 C".
        painter.setPen(ink.axis)
        painter.drawText(
            QRectF(
                rect.left(),
                rect.bottom() + SPACE["xs"] + value_metrics.height(),
                rect.width(),
                label_metrics.height(),
            ),
            Qt.AlignHCenter | Qt.AlignTop,
            "Temperature (\u00b0C)",
        )
        # Sits in the pressure gutter on the legend's baseline. Drawn over the
        # plot it collided with the topmost isobar label.
        painter.drawText(
            QRectF(0.0, SPACE["xs"], rect.left() - SPACE["xs"], label_metrics.height()),
            Qt.AlignRight | Qt.AlignTop,
            "hPa",
        )
        self._draw_focus(painter, rect, ink)

    def _draw_focus(self, painter, rect, ink):
        if self.hasFocus():
            painter.setPen(QPen(ink.focus, 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(-2.0, -2.0, 2.0, 2.0))
            if self._level_index is not None:
                pressure = self._pressures[self._level_index]
                y = self._point(pressure, 0.0).y()
                painter.setPen(QPen(ink.focus, 1.2, Qt.DashDotLine))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
