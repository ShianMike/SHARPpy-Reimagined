"""Reusable plotting canvas for synchronized sounding-comparison charts.

Feature views configure its Matplotlib surface while the comparison workspace retains
profile selection, timeline alignment, and export ownership."""

from __future__ import annotations

from qtpy.QtCore import QPointF
from qtpy.QtCore import QRectF
from qtpy.QtCore import Qt
from qtpy.QtCore import Signal
from qtpy.QtGui import QColor
from qtpy.QtGui import QFontMetricsF
from qtpy.QtGui import QPainter
from qtpy.QtGui import QPainterPath
from qtpy.QtGui import QPen
from qtpy.QtWidgets import QWidget
from sharpmod.viz.colors import semantic_palette
from sharpmod.ui.features.gui_theme import current_theme
from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.features.gui_theme import ui_font
from typing import Mapping
import math
import numpy as np
from sharpmod.ui.features.gui_visual_comparison import (
    CANVAS_DESCRIPTION,
    DIFFERENCE_DIRECTION,
    _FAMILY_NAMES,
    _MIN_DIFFERENCE_SPAN,
    _MIN_STRIP_WIDTH,
    _PLOT_LEFT_INSET,
    _PLOT_RIGHT_INSET,
    _THERMODYNAMIC_FIELDS,
    _WIND_FIELDS,
    _array
)


class _ComparisonCanvas(QWidget):
    cursorHeightChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisVisualComparisonCanvas")
        self.setAccessibleName("Synchronized sounding comparison plots")
        self.setMinimumSize(0, 360)
        self.setMouseTracking(True)
        self._panels = ()
        self._layout_count = 2
        self._cursor_height = None
        self._height_range = (0.0, 12_000.0)
        self._temperature_range = (-50.0, 40.0)
        self._panel_rects = ()
        self._difference_spans = {}
        self._reference_label = ""
        self._locked = False
        self._locked_scales = None
        self._clipped_families = ()
        self.setAccessibleDescription(CANVAS_DESCRIPTION)

    def set_data(self, panels, layout_count):
        self._panels = tuple(panels)[: int(layout_count)]
        self._layout_count = int(layout_count)
        self._resolve_difference_scales()
        if self._locked:
            # A locked comparison keeps the ranges it was locked at, so the same
            # visual displacement means the same quantity across every slot and
            # every valid time the reader steps through.
            self.update()
            return
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

    def panels(self):
        """Return the semantic panel assignments for focused UI tests."""
        return self._panels

    def _resolve_difference_scales(self):
        """Derive the difference scales, automatic per slot or locked and shared.

        Computed here rather than while painting so the scales are queryable,
        stable across repaints, and describable in the accessible text.
        """
        self._difference_spans = {}
        self._reference_label = ""
        clipped = set()
        locked = self._locked_scales if self._locked else None
        for index, panel in enumerate(self._panels):
            if panel.profile is None:
                continue
            levels = tuple(getattr(panel.difference, "levels", ()) or ())
            automatic = {
                "thermodynamic_c": self._family_span(levels, _THERMODYNAMIC_FIELDS),
                "wind_kt": self._family_span(levels, _WIND_FIELDS),
            }
            if locked is None:
                self._difference_spans[index] = automatic
            else:
                # One shared scale per family is the point of locking: two slots
                # on different scales cannot be compared by eye.
                self._difference_spans[index] = {
                    "thermodynamic_c": locked["thermodynamic_c"],
                    "wind_kt": locked["wind_kt"],
                }
                for family, span in self._difference_spans[index].items():
                    if automatic[family] > span:
                        clipped.add(family)
            if not self._reference_label:
                self._reference_label = str(
                    getattr(panel.difference, "reference_label", "") or ""
                )
        self._clipped_families = tuple(sorted(clipped))
        self.setAccessibleDescription(self._describe())

    # -- deliberate scale locking ------------------------------------------- #

    def scale_lock(self):
        """Whether the axis ranges are currently fixed rather than automatic."""
        return self._locked

    def locked_scales(self):
        """Return the frozen ranges, or ``None`` while ranges are automatic."""
        return dict(self._locked_scales) if self._locked_scales else None

    def clipped_families(self):
        """Return the unit families whose data exceeds the locked scale."""
        return self._clipped_families

    def automatic_scales(self):
        """Return the ranges automatic fitting would choose for current data."""
        thermodynamic = _MIN_DIFFERENCE_SPAN
        wind = _MIN_DIFFERENCE_SPAN
        for panel in self._panels:
            if panel.profile is None:
                continue
            levels = tuple(getattr(panel.difference, "levels", ()) or ())
            thermodynamic = max(
                thermodynamic, self._family_span(levels, _THERMODYNAMIC_FIELDS)
            )
            wind = max(wind, self._family_span(levels, _WIND_FIELDS))
        return {
            "thermodynamic_c": thermodynamic,
            "wind_kt": wind,
            "height_m": tuple(self._height_range),
            "temperature_c": tuple(self._temperature_range),
        }

    def set_scale_lock(self, locked):
        """Fix the current ranges, or return every axis to automatic fitting.

        Locking freezes the widest scale the current data needs, so nothing is
        clipped at the moment of locking; later data that exceeds it is clamped
        and reported rather than silently truncated.
        """
        locked = bool(locked)
        if locked and self._locked_scales is None:
            self._locked_scales = self.automatic_scales()
        self._locked = locked
        if not locked:
            self._locked_scales = None
        self._resolve_difference_scales()
        self.update()

    def refit_scales(self):
        """Re-fit the frozen ranges to the data now loaded, staying locked."""
        if not self._locked:
            return
        self._locked_scales = self.automatic_scales()
        self._resolve_difference_scales()
        self.update()

    def scale_state(self):
        """Return JSON-safe lock state for the analysis session."""
        return {
            "locked": bool(self._locked),
            "scales": {
                key: (list(value) if isinstance(value, tuple) else value)
                for key, value in (self._locked_scales or {}).items()
            },
        }

    def restore_scale_state(self, state):
        """Restore a saved lock, ignoring anything malformed or unknown."""
        if not isinstance(state, Mapping):
            return
        scales = state.get("scales")
        restored = None
        if isinstance(scales, Mapping):
            try:
                restored = {
                    "thermodynamic_c": float(scales["thermodynamic_c"]),
                    "wind_kt": float(scales["wind_kt"]),
                    "height_m": tuple(float(value) for value in scales["height_m"]),
                    "temperature_c": tuple(
                        float(value) for value in scales["temperature_c"]
                    ),
                }
            except (KeyError, TypeError, ValueError):
                restored = None
        if restored is not None and (
            restored["thermodynamic_c"] <= 0.0
            or restored["wind_kt"] <= 0.0
            or len(restored["height_m"]) != 2
            or len(restored["temperature_c"]) != 2
        ):
            restored = None
        if not bool(state.get("locked")) or restored is None:
            # A saved lock without usable ranges must not fix the axes at
            # nothing; fall back to automatic rather than to a broken scale.
            self._locked = False
            self._locked_scales = None
        else:
            self._locked = True
            self._locked_scales = restored
            self._height_range = restored["height_m"]
            self._temperature_range = restored["temperature_c"]
        self._resolve_difference_scales()
        self.update()

    def _describe(self):
        """State the units, vertical coordinate, direction, and actual scales."""
        parts = [CANVAS_DESCRIPTION]
        if self._reference_label:
            parts.append(f"{DIFFERENCE_DIRECTION} ({self._reference_label}).")
        elif self._difference_spans:
            parts.append(f"{DIFFERENCE_DIRECTION}.")
        parts.append(
            "Axis ranges are fixed for comparison across slots and times."
            if self._locked
            else "Axis ranges fit each slot automatically."
        )
        for index in sorted(self._difference_spans):
            spans = self._difference_spans[index]
            parts.append(
                f"Slot {index + 1} axes: ΔT and ΔTd ±{spans['thermodynamic_c']:g} °C, "
                f"Δu and Δv ±{spans['wind_kt']:g} kt."
            )
        if self._clipped_families:
            names = ", ".join(
                _FAMILY_NAMES.get(family, family) for family in self._clipped_families
            )
            parts.append(
                f"Values beyond the fixed range are clamped to the axis edge: {names}. "
                "Refit or unlock to see their full extent."
            )
        return " ".join(parts)

    @staticmethod
    def _trace(qp, points, colour, width=1.7, *, clip):
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
        qp.save()
        qp.setClipRect(clip)
        qp.setPen(QPen(colour, width))
        qp.drawPath(path)
        qp.restore()

    def _layout(self):
        margin = 8.0
        gap = 8.0
        columns = 2
        rows = 1 if self._layout_count == 2 else 2
        width = (self.width() - margin * 2 - gap * (columns - 1)) / columns
        footer_height = QFontMetricsF(mono_font("small")).height() + 6.0
        height = (self.height() - margin * 2 - gap * (rows - 1) - footer_height) / rows
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
        """Map one difference onto its axis, clamped to the plotted range.

        A fixed range can be narrower than the data. Clamping keeps the trace
        inside its own axis instead of drawing over a neighbouring plot, and the
        clamped families are named in the readout rather than hidden.
        """
        half = plot.width() / 2.0
        offset = value / span * half
        return plot.center().x() + max(-half, min(half, offset))

    def _panel_boxes(self, outer):
        """Return the profile plot and the two separate difference axes.

        One shared helper, because the hover hit-test needs exactly the same
        geometry as the painter; they previously repeated the same offsets.
        """
        title_font = ui_font("small")
        title_font.setBold(True)
        title_metrics = QFontMetricsF(title_font)
        labels = QFontMetricsF(mono_font("small"))
        top = outer.top() + 8.0 + max(20.0, title_metrics.height()) + labels.height() / 2.0
        height = max(1.0, outer.bottom() - labels.height() * 2.0 - 8.0 - top)
        gutter = max(_PLOT_LEFT_INSET, *(labels.horizontalAdvance(f"{z / 1000:g}") + 8.0
                                      for z in self._height_range))
        left = outer.left() + gutter
        available = max(60.0, outer.right() - _PLOT_RIGHT_INSET - left)
        gap = 12.0
        plot_width = max(60.0, available * 0.46)
        strip_width = max(
            _MIN_STRIP_WIDTH, (available - plot_width - gap * 2.0) / 2.0
        )
        plot = QRectF(left, top, plot_width, height)
        thermodynamic = QRectF(plot.right() + gap, top, strip_width, height)
        wind = QRectF(thermodynamic.right() + gap, top, strip_width, height)
        return plot, thermodynamic, wind

    def difference_spans(self):
        """Return each slot's independent difference half-ranges.

        Keyed by slot position, with ``thermodynamic_c`` in °C and ``wind_kt`` in
        knots. Available without painting so callers and tests can read the
        actual scales rather than infer them from pixels.
        """
        return {index: dict(spans) for index, spans in self._difference_spans.items()}

    @staticmethod
    def _family_span(levels, fields):
        """Round one unit family's largest absolute difference up to a scale."""
        magnitudes = [
            abs(value)
            for level in levels
            for value in (getattr(level, field, None) for field in fields)
            if value is not None and math.isfinite(value)
        ]
        return max(
            _MIN_DIFFERENCE_SPAN,
            math.ceil(max(magnitudes, default=0.0) / 5.0) * 5.0,
        )

    def _caption(self, metrics, width, full, short):
        """Prefer the fully labelled caption; fall back before it would clip."""
        if metrics.horizontalAdvance(full) <= width - 2:
            return full
        return short if metrics.horizontalAdvance(short) <= width - 2 else "\n".join(short.rsplit(" ", 1))

    @staticmethod
    def _height_step(low, high, pixels, metrics):
        """Choose a height tick interval whose labels cannot collide.

        A fixed 2 km interval crowds into an unreadable column once the
        interface text is enlarged or the slot is short, so the interval is
        derived from the actual font height and the actual plot height.
        """
        extent = float(high) - float(low)
        needed = metrics.height() + 3.0
        if extent <= 0.0 or pixels <= 0.0:
            return 2000.0
        for candidate in (1000.0, 2000.0, 4000.0, 5000.0, 10000.0):
            if candidate / extent * float(pixels) >= needed:
                return max(2000.0, candidate)
        return 20000.0

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
                qp.drawText(outer, Qt.AlignCenter, f"Slot {index + 1}: not assigned")
                continue
            panel = self._panels[index]
            title = f"Slot {panel.slot_number}: {panel.label}"
            if panel.reference:
                title += " · reference"
            if panel.modified:
                title += " · modified"
            qp.setPen(QColor(theme.text_primary))
            font = ui_font("small")
            font.setBold(True)
            qp.setFont(font)
            title_metrics = qp.fontMetrics()
            title_width = max(20.0, outer.width() - 16)
            title_box = QRectF(
                outer.left() + 8,
                outer.top() + 4,
                title_width,
                max(20.0, float(title_metrics.height())),
            )
            # Elide rather than clip: a truncated slot title still has to say
            # which slot it is and, where it fits, that it is the reference.
            qp.drawText(
                title_box,
                Qt.AlignLeft | Qt.AlignVCenter,
                title_metrics.elidedText(title, Qt.ElideRight, int(title_width)),
            )
            if panel.profile is None:
                qp.setPen(QColor(theme.text_tertiary))
                qp.setFont(ui_font("small"))
                qp.drawText(
                    QRectF(
                        outer.left() + 18,
                        outer.top() + 34,
                        outer.width() - 36,
                        outer.height() - 48,
                    ),
                    Qt.AlignCenter | Qt.TextWordWrap,
                    panel.unavailable_reason,
                )
                continue
            plot, thermo_box, wind_box = self._panel_boxes(outer)
            qp.setFont(mono_font("small"))
            metrics = qp.fontMetrics()
            low_z, high_z = self._height_range
            step = self._height_step(low_z, high_z, plot.height(), metrics)
            label_height = metrics.height() + 2
            label_box_width = max(12.0, plot.left() - outer.left() - 5.0)
            qp.setPen(QPen(grid, 1.0, Qt.DotLine))
            z = math.ceil(low_z / step) * step
            while z <= high_z:
                y = self._z_to_y(z, plot)
                qp.drawLine(QPointF(plot.left(), y), QPointF(wind_box.right(), y))
                qp.setPen(QColor(theme.text_tertiary))
                qp.drawText(
                    QRectF(
                        outer.left() + 2,
                        y - label_height / 2.0,
                        label_box_width,
                        label_height,
                    ),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{z / 1000:g}",
                )
                qp.setPen(QPen(grid, 1.0, Qt.DotLine))
                z += step
            qp.setPen(QPen(frame, 1.0))
            qp.drawRect(plot)
            for box in (thermo_box, wind_box):
                qp.drawRect(box)
                qp.drawLine(
                    QPointF(box.center().x(), box.top()),
                    QPointF(box.center().x(), box.bottom()),
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
                self._trace(qp, points, colour, clip=plot)

            difference_levels = tuple(getattr(panel.difference, "levels", ()) or ())
            spans = self._difference_spans.get(
                index,
                {
                    "thermodynamic_c": _MIN_DIFFERENCE_SPAN,
                    "wind_kt": _MIN_DIFFERENCE_SPAN,
                },
            )
            # Two families, two scales. Sharing one span let a 30 kt wind change
            # squash a 2 °C temperature change to a few percent of the axis.
            families = (
                (
                    "thermodynamic_c",
                    thermo_box,
                    spans["thermodynamic_c"],
                    (
                        ("temperature_c", palette["red"]),
                        ("dewpoint_c", palette["green"]),
                    ),
                    "ΔT ΔTd (°C)",
                    "Δ °C",
                ),
                (
                    "wind_kt",
                    wind_box,
                    spans["wind_kt"],
                    (
                        ("u_wind_kt", palette["blue"]),
                        ("v_wind_kt", palette["magenta"]),
                    ),
                    "Δu Δv (kt)",
                    "Δ kt",
                ),
            )
            for family_key, box, span, fields, full_caption, short_caption in families:
                for field, colour in fields:
                    points = []
                    for level in difference_levels:
                        value = getattr(level, field)
                        if value is None or not math.isfinite(value):
                            # A missing difference stays a gap; _trace breaks the
                            # path rather than joining across it.
                            points.append(None)
                        else:
                            points.append(
                                QPointF(
                                    self._difference_x(value, box, span),
                                    self._z_to_y(level.height_msl_m, box),
                                )
                            )
                    self._trace(qp, points, QColor(colour), 1.25, clip=box)
                qp.setPen(QColor(theme.text_tertiary))
                metrics = qp.fontMetrics()
                extent = f"{span:g}"
                # Name the scale magnitude, so the axis is readable rather than
                # merely centred on zero. Requires a clear gap between the two
                # ends; otherwise the accessible text carries the magnitude and
                # nothing is drawn touching its neighbour.
                end_labels = f"-{extent}"
                gap_width = metrics.horizontalAdvance("  ")
                fits = (
                    metrics.horizontalAdvance(f"-{extent}+{extent}") + gap_width
                    <= box.width() - 6
                    and metrics.height() * 2 + 4 <= box.height()
                )
                if fits:
                    label_height = metrics.height()
                    baseline = box.bottom() - label_height - 2
                    half = box.width() / 2.0 - 3.0
                    qp.drawText(
                        QRectF(box.left() + 2, baseline, half, label_height),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        end_labels,
                    )
                    qp.drawText(
                        QRectF(box.center().x() + 1, baseline, half, label_height),
                        Qt.AlignRight | Qt.AlignVCenter,
                        f"+{extent}",
                    )
                qp.drawText(
                    QRectF(box.left(), box.bottom() + 4, box.width(), metrics.height() * 2),
                    Qt.AlignHCenter | Qt.AlignTop,
                    self._caption(metrics, box.width(), full_caption, short_caption),
                )
                if family_key in self._clipped_families:
                    # Mark the edge a clamped trace is resting against, so a
                    # fixed range never reads as the real extent of the data.
                    qp.setPen(QPen(QColor(theme.warning), 1.6))
                    for edge in (box.left(), box.right()):
                        qp.drawLine(
                            QPointF(edge, box.top()),
                            QPointF(edge, box.bottom()),
                        )
                    qp.setPen(QColor(theme.text_tertiary))
            if not difference_levels:
                # The typed model already explains why; say it instead of drawing
                # two empty axes.
                reason = str(getattr(panel.difference, "reason", "") or "")
                qp.setPen(QColor(theme.text_tertiary))
                qp.setFont(ui_font("small"))
                qp.drawText(
                    QRectF(
                        thermo_box.left(),
                        thermo_box.top(),
                        wind_box.right() - thermo_box.left(),
                        thermo_box.height(),
                    ),
                    Qt.AlignCenter | Qt.TextWordWrap,
                    f"No differences: {reason}" if reason else "No differences",
                )
                qp.setFont(mono_font("small"))
            qp.setPen(QColor(theme.text_tertiary))
            qp.drawText(
                QRectF(plot.left(), plot.bottom() + 4, plot.width(), qp.fontMetrics().height() * 2),
                Qt.AlignHCenter | Qt.AlignTop,
                self._caption(qp.fontMetrics(), plot.width(), "T / Td (°C)", "T/Td °C"),
            )
            if self._cursor_height is not None:
                y = self._z_to_y(self._cursor_height, plot)
                if plot.top() <= y <= plot.bottom():
                    cursor = QColor(theme.accent)
                    qp.setPen(QPen(cursor, 1.2, Qt.DashLine))
                    qp.drawLine(QPointF(plot.left(), y), QPointF(wind_box.right(), y))

        if self._panels:
            # An exported image loses the status line, so the vertical coordinate
            # and the subtraction direction have to be on the canvas itself.
            qp.setPen(QColor(theme.text_tertiary))
            qp.setFont(mono_font("small"))
            footer = f"height: km MSL · {DIFFERENCE_DIRECTION}"
            metrics = qp.fontMetrics()
            available = max(0.0, self.width() - 8)
            if self._reference_label:
                # Name the exact reference when it fits; never let it push the
                # units and the subtraction direction off the edge.
                extended = f"{footer} ({self._reference_label})"
                if metrics.horizontalAdvance(extended) <= available:
                    footer = extended
            qp.drawText(
                4,
                self.height() - 3,
                metrics.elidedText(footer, Qt.ElideRight, int(available)),
            )
        qp.end()

    @staticmethod
    def _position(event):
        return event.position() if hasattr(event, "position") else event.posF()

    def mouseMoveEvent(self, event):  # noqa: N802
        position = self._position(event)
        for outer in self._panel_rects:
            if not outer.contains(position):
                continue
            plot, _thermo, _wind = self._panel_boxes(outer)
            fraction = (position.y() - plot.top()) / max(1.0, plot.height())
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
