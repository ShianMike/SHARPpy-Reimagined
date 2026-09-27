"""Selectable time-series chart for sounding trends."""

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
    _TrendPoint,
    _TrendSeries,
    _chart_ink,
    _format_metric,
    _format_time,
    _get,
    _inset,
    _metric_title,
    _metric_value,
    _nice_ticks,
    _series_colours,
    _tick_decimals
)


class _TrendChart(QWidget):
    """Small dependency-free time-series chart with selectable samples.

    Reachable by mouse *and* keyboard: the chart takes focus, Left/Right walk
    the plotted samples, and Enter opens the highlighted valid time. Without
    that a click was the only way to reach the feature the chart advertises.
    """

    pointActivated = Signal(int)

    #: Pixel radius within which a click counts as hitting a sample.
    HIT_RADIUS = 15.0
    SERIES_STYLES = (
        Qt.SolidLine,
        Qt.DashLine,
        Qt.DotLine,
        Qt.DashDotLine,
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisTrendChart")
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Keyboard reachable, and hover-tracked so the nearest sample can be
        # highlighted before the user commits to a click.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Parameter trend chart")
        # Built here, not in paintEvent: ``render.install_font`` replaces QFont
        # process-wide when the first sounding opens, and ui_font/mono_font set
        # the family after construction to defeat that. Painting with the
        # inherited widget font instead would silently restyle mid-session.
        self._label_font = ui_font("caption")
        self._value_font = mono_font("caption")
        self._series = ()
        self._metric = ""
        #: One entry per stacked track. A single-metric chart has exactly one, and
        #: then ``_metric``/``_y_bounds`` are that track, which is what keeps the
        #: pre-existing single-metric geometry and tests unchanged.
        self._tracks = ()
        self._track_bounds = ()
        self._automatic_bounds = ()
        self._prepared = ()
        self._x_bounds = (0.0, 1.0)
        self._y_bounds = (0.0, 1.0)
        self._multiday = False
        self._hover_index = None
        self._locked = False
        self._locked_bounds = None
        self._clipped_tracks = ()
        self._show_local = False
        self._accessible_summary = "No timeline data yet."
        self.setAccessibleDescription(self._accessible_summary)

    # -- data --------------------------------------------------------------- #

    @property
    def series(self):
        """The plotted series, in legend order."""
        return self._series

    def set_samples(self, samples, metric):
        """Plot a single unlabelled timeline."""
        self.set_series(
            (
                _TrendSeries(0, "", "", _series_colours(1)[0], tuple(samples or ())),
            )
            if samples
            else (),
            metric,
        )

    def set_series(self, series, metric):
        """Plot one metric. Kept as the single-metric entry point."""
        self.set_tracks(series, (metric,))

    def set_tracks(self, series, metrics):
        """Prepare numeric plot inputs once; :meth:`paintEvent` stays cheap.

        Every sounding is plotted together on shared axes, so the comparison the
        chart is for -- how the runs disagree about the same parameter over the
        same hours -- is one read rather than a sequence of remembered ones.
        Points are flattened into :attr:`_prepared` in track-then-series-then-
        sample order, which is what lets one integer identify a point.

        Several metrics become several stacked tracks rather than several lines on
        one axis. CAPE in J/kg and a shear value in knots share no scale: putting
        them on one axis makes the smaller quantity a flat line at the bottom, so
        each track keeps its own value axis, its own units, and its own ticks, and
        they share only the time axis -- which is the axis the comparison is
        actually across.
        """
        selected = None
        if self._hover_index is not None and self._hover_index < len(self._prepared):
            point = self._prepared[self._hover_index]
            item = self._series[point.series]
            selected = (
                item.collection_index, _get(item.samples[point.sample], "valid_time"),
                self._tracks[point.track],
            )
        self._series = tuple(series or ())
        tracks = tuple(str(metric) for metric in (metrics or ()) if str(metric))
        self._tracks = tracks or ("",)
        # The first track stays ``_metric``: the tooltip, the accessible summary,
        # the CSV export and the chart both describe the primary parameter.
        self._metric = self._tracks[0]
        self._hover_index = None
        self.setToolTip("")
        prepared = []
        finite_x = []
        per_track = [[] for _ in self._tracks]
        days = set()
        for track_index, metric in enumerate(self._tracks):
            for series_index, item in enumerate(self._series):
                for sample_index, sample in enumerate(item.samples):
                    valid = _get(sample, "valid_time")
                    if isinstance(valid, datetime):
                        try:
                            x_value = float(valid.timestamp())
                        except (OverflowError, OSError, ValueError):
                            x_value = float(sample_index)
                        days.add(valid.date())
                    else:
                        x_value = float(sample_index)
                    y_value = _metric_value(sample, metric)
                    if not bool(_get(sample, "available", True)):
                        y_value = None
                    prepared.append(
                        _TrendPoint(
                            x_value,
                            y_value,
                            len(prepared),
                            series_index,
                            sample_index,
                            track_index,
                        )
                    )
                    if track_index == 0:
                        finite_x.append(x_value)
                    if y_value is not None:
                        per_track[track_index].append(y_value)
        self._prepared = tuple(prepared)
        self._multiday = len(days) > 1
        if finite_x:
            x_min, x_max = min(finite_x), max(finite_x)
            if x_min == x_max:
                x_min -= 0.5
                x_max += 0.5
            self._x_bounds = (x_min, x_max)
        else:
            self._x_bounds = (0.0, 1.0)
        # Held separately from the drawn bounds. ``_apply_lock`` used to overwrite
        # the only copy, so Refit re-locked to the *clamped* range it was already
        # showing instead of to the data that had arrived since.
        self._automatic_bounds = tuple(
            self._bounds_for(values) for values in per_track
        )
        self._apply_lock()
        self._update_accessible_description()
        if selected is not None:
            for point in self._prepared:
                item = self._series[point.series]
                if selected == (
                    item.collection_index, _get(item.samples[point.sample], "valid_time"),
                    self._tracks[point.track],
                ):
                    self._highlight(point.index)
                    break
        self.update()

    @staticmethod
    def _bounds_for(values):
        """Pad one track's value range, or fall back to a unit range."""
        if not values:
            return (0.0, 1.0)
        low, high = min(values), max(values)
        pad = max((high - low) * 0.08, 1.0 if low == high else 0.0)
        return (low - pad, high + pad)

    # -- deliberate range locking ------------------------------------------- #

    def _apply_lock(self):
        """Resolve which bounds each track is drawn at, and what that clips.

        Locking exists so a track means the same displacement across a refresh: an
        automatically refitted axis makes two runs look alike when their values
        differ, and makes one run look volatile when it is not. Data beyond a
        locked range is clamped at the edge and the track is named as clipped,
        never silently truncated.
        """
        clipped = []
        automatic = self._automatic_bounds
        if not self._locked or not self._locked_bounds:
            self._track_bounds = automatic
        else:
            resolved = []
            for index, metric in enumerate(self._tracks):
                fitted = automatic[index] if index < len(automatic) else (0.0, 1.0)
                locked = self._locked_bounds.get(metric)
                if locked is None:
                    resolved.append(fitted)
                    continue
                resolved.append(locked)
                if fitted[0] < locked[0] or fitted[1] > locked[1]:
                    clipped.append(metric)
            self._track_bounds = tuple(resolved)
        self._clipped_tracks = tuple(clipped)
        self._y_bounds = (
            self._track_bounds[0] if self._track_bounds else (0.0, 1.0)
        )

    def scale_lock(self):
        """Whether the value axes are fixed rather than refitted per update."""
        return self._locked

    def clipped_tracks(self):
        """Metrics whose data exceeds the fixed range and is clamped."""
        return self._clipped_tracks

    def set_scale_lock(self, locked):
        """Fix the current value ranges, or return every track to automatic."""
        locked = bool(locked)
        if locked and not self._locked_bounds:
            # Snapshotted from the automatic fit, never from what is currently
            # drawn, so Refit lands on the data rather than on the old lock.
            self._locked_bounds = {
                metric: tuple(
                    self._automatic_bounds[index]
                    if index < len(self._automatic_bounds)
                    else (0.0, 1.0)
                )
                for index, metric in enumerate(self._tracks)
            }
        self._locked = locked
        if not locked:
            self._locked_bounds = None
        self._apply_lock()
        self._update_accessible_description()
        self.update()

    def refit_scales(self):
        """Re-fit the fixed ranges to the data now loaded, staying fixed."""
        if not self._locked:
            return
        self._locked_bounds = None
        self.set_scale_lock(True)

    def locked_scales(self):
        return dict(self._locked_bounds) if self._locked_bounds else None

    def scale_state(self):
        """Return JSON-safe lock state for the analysis session."""
        return {
            "locked": bool(self._locked),
            "tracks": {
                str(metric): [float(low), float(high)]
                for metric, (low, high) in (self._locked_bounds or {}).items()
            },
        }

    def restore_scale_state(self, state):
        """Restore a saved lock, ignoring anything malformed or unusable."""
        if not isinstance(state, Mapping):
            return
        restored = {}
        raw = state.get("tracks")
        if isinstance(raw, Mapping):
            for metric, bounds in raw.items():
                try:
                    low, high = (float(value) for value in bounds)
                except (TypeError, ValueError):
                    continue
                if high > low:
                    restored[str(metric)] = (low, high)
        if not bool(state.get("locked")) or not restored:
            # A saved lock with no usable ranges must not pin the axes at nothing.
            self._locked = False
            self._locked_bounds = None
        else:
            self._locked = True
            self._locked_bounds = restored
        self._apply_lock()
        self._update_accessible_description()
        self.update()

    @classmethod
    def _series_style(cls, index):
        """Cycle line patterns so series identity never depends on hue alone."""
        return cls.SERIES_STYLES[int(index) % len(cls.SERIES_STYLES)]

    def _track_title(self, track):
        """Name one track's parameter and its units."""
        try:
            metric = self._tracks[int(track)]
        except (IndexError, TypeError, ValueError):
            metric = self._metric
        label, units, _decimals = _metric_title(metric)
        return f"{label}{f' ({units})' if units else ''}"

    def tracks(self):
        """The metric keys currently stacked, in top-to-bottom order."""
        return tuple(self._tracks)

    def show_local_time(self):
        return self._show_local

    def set_show_local_time(self, enabled):
        """Add local time to the readouts. UTC is never removed."""
        enabled = bool(enabled)
        if enabled == self._show_local:
            return
        self._show_local = enabled
        self._update_accessible_description(self._hover_index)
        self.update()

    def _update_accessible_description(self, focused=None):
        label, units, _decimals = _metric_title(self._metric)
        primary = [
            point.y
            for point in self._prepared
            if point.y is not None and point.track == 0
        ]
        available = [point.y for point in self._prepared if point.y is not None]
        missing = len(self._prepared) - len(available)
        series_count = len(self._series)
        if not self._prepared:
            summary = f"{label} trend. No timeline data yet."
        else:
            unit_text = f" {units}" if units else ""
            summary = (
                f"{label} trend with {len(available)} available point(s) across "
                f"{series_count} sounding series and {missing} missing point(s)."
            )
            if primary:
                summary += (
                    f" Values range from {_format_metric(self._metric, min(primary))}"
                    f"{unit_text} to {_format_metric(self._metric, max(primary))}"
                    f"{unit_text}."
                )
            if self._track_count() > 1:
                # Say that the tracks do not share a value scale, because reading
                # a stacked chart as one scale is the mistake it invites.
                summary += (
                    f" {self._track_count()} separate tracks share the time axis "
                    "only; each has its own units and value scale: "
                    + "; ".join(
                        self._track_title(index)
                        for index in range(self._track_count())
                    )
                    + "."
                )
            summary += (
                " Axis ranges are fixed for comparison across updates."
                if self._locked
                else " Axis ranges fit the loaded data."
            )
            summary += (
                " Times are UTC; the local equivalent is given beside each "
                "reading with its zone, offset, and daylight-saving state."
                if self._show_local
                else " Times are UTC."
            )
            if self._clipped_tracks:
                summary += (
                    " Values beyond the fixed range are clamped to the axis edge: "
                    + ", ".join(
                        _metric_title(metric)[0] for metric in self._clipped_tracks
                    )
                    + "."
                )
            summary += (
                " Missing values are marked with an X; series use distinct line "
                "patterns as well as colour. Use Left and Right to read points "
                "and Enter to open one."
            )
        self._accessible_summary = summary
        description = summary
        if focused is not None:
            reading = self._sample_tooltip(focused).replace("\n", ". ")
            if reading:
                description += f" Focused point: {reading}."
        self.setAccessibleDescription(description)

    def point_at(self, index):
        """Return the flat point ``index`` identifies, or ``None``."""
        try:
            return self._prepared[int(index)]
        except (IndexError, TypeError, ValueError):
            return None

    def sample_at(self, index):
        """Return ``(series, sample)`` for a flat index, or ``(None, None)``."""
        point = self.point_at(index)
        if point is None:
            return None, None
        try:
            item = self._series[point.series]
            return item, item.samples[point.sample]
        except (IndexError, TypeError):
            return None, None

    # -- geometry ----------------------------------------------------------- #

    def _y_ticks(self, track=0):
        """Return labelled value gridlines and the decimals to print them at.

        Ticks whose label would repeat the one below are dropped: label precision
        is capped, so a series that barely varies would otherwise draw a stack of
        gridlines all reading ``0.000``. Deduplicating here rather than in
        :meth:`paintEvent` keeps the gutter measurement and the drawing in step.
        """
        y_min, y_max = self._bounds_of(track)
        ticks = _nice_ticks(y_min, y_max, target=4 if self._track_count() < 3 else 3)
        step = ticks[1] - ticks[0] if len(ticks) > 1 else max(abs(y_max), 1.0)
        decimals = _tick_decimals(step)
        unique = []
        seen = set()
        for tick in ticks:
            text = f"{tick:.{decimals}f}"
            if text in seen:
                continue
            seen.add(text)
            unique.append(tick)
        return tuple(unique), decimals

    def _needs_legend(self):
        """A legend earns its line only once colour distinguishes something."""
        return sum(1 for item in self._series if item.short_label) > 1

    def _track_count(self):
        return max(1, len(self._tracks))

    def _bounds_of(self, track):
        try:
            return self._track_bounds[int(track)]
        except (IndexError, TypeError, ValueError):
            return self._y_bounds

    def _plot_area(self):
        """Reserve gutters sized to the text that actually has to fit in them.

        The previous fixed 46px left gutter clipped four-digit CAPE labels and
        wasted half its width on two-digit ones.
        """
        values = QFontMetricsF(self._value_font)
        labels = QFontMetricsF(self._label_font)
        widest = 0.0
        for track in range(self._track_count()):
            ticks, decimals = self._y_ticks(track)
            widest = max(
                widest,
                max(
                    (values.horizontalAdvance(f"{tick:.{decimals}f}") for tick in ticks),
                    default=0.0,
                ),
            )
        left = SPACE["sm"] + widest + SPACE["xs"]
        top = SPACE["xs"] + labels.height() + SPACE["sm"]
        if self._needs_legend():
            # Its own line rather than the title's: sounding names beside a
            # parameter name overran the width and collided.
            top += labels.height() + SPACE["xxs"]
        bottom = SPACE["xs"] + values.height() + SPACE["xs"]
        return _inset(self, left, top, SPACE["md"], bottom)

    def _track_rect(self, track=0):
        """Return one stacked track's band of the plot area.

        With a single track this is the whole plot area, unchanged, so the
        single-metric chart keeps its existing geometry to the pixel.
        """
        rect = self._plot_area()
        count = self._track_count()
        if count == 1:
            return rect
        # Room for each band's own caption, since a stacked track cannot rely on
        # the single title at the top of the widget to say what it is.
        caption = QFontMetricsF(self._label_font).height() + SPACE["xxs"]
        gap = caption + SPACE["sm"]
        height = (rect.height() - gap * (count - 1)) / count
        index = max(0, min(count - 1, int(track)))
        top = rect.top() + index * (height + gap)
        return QRectF(rect.left(), top, rect.width(), max(1.0, height))

    def _screen_point(self, x_value, y_value, track=0):
        rect = self._track_rect(track)
        x_min, x_max = self._x_bounds
        y_min, y_max = self._bounds_of(track)
        x = rect.left() + (x_value - x_min) / (x_max - x_min) * rect.width()
        # Clamped so a value beyond a fixed range rests on the axis edge instead
        # of being drawn over a neighbouring track.
        fraction = (y_value - y_min) / (y_max - y_min) if y_max > y_min else 0.0
        y = rect.bottom() - max(0.0, min(1.0, fraction)) * rect.height()
        return QPointF(x, y)

    def _hit_points(self):
        return tuple(
            (self._screen_point(point.x, point.y, point.track), point.index)
            for point in self._prepared
            if point.y is not None
        )

    def _plotted_indices(self):
        return tuple(
            point.index for point in self._prepared if point.y is not None
        )

    def _nearest_index(self, position, *, maximum_distance=None):
        """Return the sample index closest to ``position``, or ``None``."""
        limit = self.HIT_RADIUS if maximum_distance is None else float(maximum_distance)
        closest = None
        for point, index in self._hit_points():
            distance = math.hypot(point.x() - position.x(), point.y() - position.y())
            if closest is None or distance < closest[0]:
                closest = distance, index
        if closest is None or closest[0] > limit:
            return None
        return int(closest[1])

    # -- interaction -------------------------------------------------------- #

    def activate_nearest(self, position, *, maximum_distance=None):
        """Activate the closest plotted point; useful to mouse and keyboard tests."""
        index = self._nearest_index(position, maximum_distance=maximum_distance)
        if index is None:
            return False
        self.pointActivated.emit(index)
        return True

    def _sample_tooltip(self, index):
        item, sample = self.sample_at(index)
        if sample is None:
            return ""
        label, units, _decimals = _metric_title(self._metric)
        value = _metric_value(sample, self._metric)
        reading = _format_metric(self._metric, value)
        suffix = f" {units}" if units and value is not None else ""
        # The sounding first: with several series on one axis, which run a point
        # belongs to is the thing a hovered point cannot say for itself.
        heading = f"{item.label}\n" if item is not None and item.label else ""
        valid = _get(sample, "valid_time")
        # UTC first and always. The local line is added below it, never in place
        # of it, and it carries its zone, offset, and daylight-saving state.
        when = _format_time(valid)
        if self._show_local:
            local = local_time.local_reading(valid)
            if local is not None:
                when += f"\n{local.describe()}"
        return (
            f"{heading}{when}\n"
            f"{label}: {reading}{suffix}\n"
            "Click or press Enter to open this valid time"
        )

    def _highlight(self, index):
        if index == self._hover_index:
            return
        self._hover_index = index
        self.setToolTip("" if index is None else self._sample_tooltip(index))
        self._update_accessible_description(index)
        self.update()

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            if self.activate_nearest(event.position()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        index = self._nearest_index(event.position())
        self.setCursor(Qt.ArrowCursor if index is None else Qt.PointingHandCursor)
        self._highlight(index)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setCursor(Qt.ArrowCursor)
        self._highlight(None)
        super().leaveEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        indices = self._plotted_indices()
        key = event.key()
        if indices and key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End):
            if key == Qt.Key_Home:
                position = 0
            elif key == Qt.Key_End:
                position = len(indices) - 1
            else:
                step = -1 if key == Qt.Key_Left else 1
                if self._hover_index in indices:
                    position = indices.index(self._hover_index) + step
                else:
                    position = 0 if step > 0 else len(indices) - 1
                position = max(0, min(len(indices) - 1, position))
            self._highlight(indices[position])
            event.accept()
            return
        if self._hover_index is not None and key in (
            Qt.Key_Return,
            Qt.Key_Enter,
            Qt.Key_Space,
        ):
            self.pointActivated.emit(int(self._hover_index))
            event.accept()
            return
        super().keyPressEvent(event)

    # -- painting ----------------------------------------------------------- #

    def _axis_time(self, value):
        """Short axis form: the date only matters once the series crosses one."""
        if not isinstance(value, datetime):
            return ""
        if self._multiday:
            return value.strftime("%d %HZ")
        return value.strftime("%HZ") if value.minute == 0 else value.strftime("%H:%MZ")

    def _time_axis_points(self):
        """One point per distinct valid time, so ticks are not drawn per series.

        Several series share the same hours; ticking every plotted point would
        stack every label N times over.
        """
        seen = {}
        for point in self._prepared:
            seen.setdefault(point.x, point)
        return tuple(seen[key] for key in sorted(seen))

    def _time_ticks(self, rect, metrics):
        """Pick evenly spaced times whose labels still fit side by side."""
        axis = self._time_axis_points()
        if not axis:
            return ()
        widest = max(
            (
                metrics.horizontalAdvance(self._axis_time(self._point_time(point)))
                for point in axis
            ),
            default=0.0,
        )
        slots = max(1, int(rect.width() // max(1.0, widest + SPACE["lg"])))
        count = max(1, min(len(axis), slots))
        if count == 1:
            return (axis[0],)
        stride = (len(axis) - 1) / (count - 1)
        chosen = sorted({int(round(slot * stride)) for slot in range(count)})
        return tuple(axis[index] for index in chosen)

    def _point_time(self, point):
        _item, sample = self.sample_at(point.index)
        return _get(sample, "valid_time") if sample is not None else None

    def _draw_series_legend(self, painter, rect, ink, metrics, baseline):
        """Name each colour/pattern, dropping entries before they overrun."""
        swatch = metrics.ascent()
        gap = SPACE["xs"]
        x = rect.left()
        limit = rect.right()
        for position, item in enumerate(self._series):
            text = item.short_label or f"#{item.collection_index + 1}"
            width = swatch + gap + metrics.horizontalAdvance(text)
            remaining = len(self._series) - position
            if x + width > limit:
                more = f"+{remaining}"
                if x + metrics.horizontalAdvance(more) <= limit:
                    painter.setPen(ink.axis)
                    painter.drawText(QPointF(x, baseline), more)
                return
            y = baseline - swatch / 2.0
            painter.setPen(
                QPen(QColor(item.colour), 2.2, self._series_style(position))
            )
            painter.drawLine(QPointF(x, y), QPointF(x + swatch, y))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(ink.title)
            painter.drawText(QPointF(x + swatch + gap, baseline), text)
            x += width + SPACE["md"]

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        # Re-resolve explicit painter fonts on every paint. Application text
        # scaling can change while this widget is open, whereas chart zoom is a
        # separate data/canvas transform and never reaches these fonts.
        self._label_font = ui_font("caption")
        self._value_font = mono_font("caption")
        colours = _series_colours(len(self._series))
        self._series = tuple(
            item._replace(colour=colour) for item, colour in zip(self._series, colours)
        )
        ink = _chart_ink()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), ink.background)
        rect = self._plot_area()
        label_metrics = QFontMetricsF(self._label_font)
        value_metrics = QFontMetricsF(self._value_font)

        painter.setFont(self._label_font)
        painter.setPen(ink.title)
        title = self._track_title(0)
        if self._track_count() > 1:
            # The heading names the stack; each band names itself below.
            title = f"{title} + {self._track_count() - 1} more"
        if self._locked:
            title += "  \u00b7  fixed ranges"
            if self._clipped_tracks:
                title += " (clamped)"
        baseline = SPACE["xs"] + label_metrics.ascent()
        painter.drawText(QPointF(SPACE["sm"], baseline), title)
        if self._needs_legend():
            self._draw_series_legend(
                painter,
                rect,
                ink,
                label_metrics,
                baseline + label_metrics.height() + SPACE["xxs"],
            )

        if not self._prepared:
            painter.setPen(ink.axis)
            painter.drawText(rect, Qt.AlignCenter, "No timeline data yet")
            painter.setPen(QPen(ink.frame, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if self.hasFocus():
                painter.setPen(QPen(ink.focus, 1.0, Qt.DashLine))
                painter.drawRect(rect.adjusted(-2.0, -2.0, 2.0, 2.0))
            return

        by_track = {}
        for point in self._prepared:
            by_track.setdefault(point.track, []).append(point)

        for track in range(self._track_count()):
            band = self._track_rect(track)
            ticks, decimals = self._y_ticks(track)
            y_min, y_max = self._bounds_of(track)
            if self._track_count() > 1:
                # Each band says which parameter and unit it is; without this the
                # stack is several unlabelled plots.
                painter.setFont(self._label_font)
                painter.setPen(ink.title)
                painter.drawText(
                    QPointF(band.left(), band.top() - SPACE["xxs"]),
                    self._track_title(track),
                )
            painter.setFont(self._value_font)
            for tick in ticks:
                if not y_min <= tick <= y_max:
                    continue
                y = self._screen_point(self._x_bounds[0], tick, track).y()
                painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
                painter.drawLine(QPointF(band.left(), y), QPointF(band.right(), y))
                painter.setPen(ink.axis)
                painter.drawText(
                    QRectF(
                        SPACE["sm"],
                        y - value_metrics.height() / 2.0,
                        band.left() - SPACE["sm"] - SPACE["xs"],
                        value_metrics.height(),
                    ),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{tick:.{decimals}f}",
                )

            # One shared time axis: gridlines on every band, labels only under
            # the last, because the tracks are read against the same hours.
            for point in self._time_ticks(band, value_metrics):
                x = self._screen_point(point.x, y_min, track).x()
                painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
                painter.drawLine(QPointF(x, band.top()), QPointF(x, band.bottom()))
                if track != self._track_count() - 1:
                    continue
                text = self._axis_time(self._point_time(point))
                width = value_metrics.horizontalAdvance(text) + SPACE["sm"]
                painter.setPen(ink.axis)
                painter.drawText(
                    QRectF(
                        min(
                            max(band.left() - SPACE["sm"], x - width / 2.0),
                            self.width() - width,
                        ),
                        band.bottom() + SPACE["xs"],
                        width,
                        value_metrics.height(),
                    ),
                    Qt.AlignHCenter | Qt.AlignTop,
                    text,
                )

            painter.setPen(QPen(ink.frame, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(band)
            if self._tracks and self._tracks[track] in self._clipped_tracks:
                # Mark the edges a clamped trace rests against, so a fixed range
                # never reads as the real extent of the data.
                painter.setPen(QPen(QColor(current_theme().warning), 1.6))
                painter.drawLine(band.topLeft(), band.topRight())
                painter.drawLine(band.bottomLeft(), band.bottomRight())

            painter.save()
            painter.setClipRect(band.adjusted(-3.0, -3.0, 3.0, 3.0))
            by_series = {}
            for point in by_track.get(track, ()):
                by_series.setdefault(point.series, []).append(point)
            for series_index, points in by_series.items():
                try:
                    colour = QColor(self._series[series_index].colour)
                except IndexError:
                    colour = ink.series
                path = QPainterPath()
                path_open = False
                for point in points:
                    if point.y is None:
                        # A visible marker plus a path break distinguishes a true
                        # gap from a meteorological jump between adjacent valid
                        # times. Drawn in the series colour, so with several
                        # soundings on one axis it is clear whose hour is missing.
                        x = self._screen_point(point.x, y_min, track).x()
                        painter.setPen(QPen(colour, 1.5))
                        painter.drawLine(
                            QPointF(x - 4, band.bottom() - 4),
                            QPointF(x + 4, band.bottom() + 4),
                        )
                        painter.drawLine(
                            QPointF(x - 4, band.bottom() + 4),
                            QPointF(x + 4, band.bottom() - 4),
                        )
                        path_open = False
                        continue
                    position = self._screen_point(point.x, point.y, track)
                    if path_open:
                        path.lineTo(position)
                    else:
                        path.moveTo(position)
                        path_open = True
                painter.setPen(QPen(colour, 2.0, self._series_style(series_index)))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path)
                painter.setPen(QPen(ink.background, 1.0))
                painter.setBrush(QBrush(colour))
                for point in points:
                    if point.y is None:
                        continue
                    radius = 5.0 if point.index == self._hover_index else 3.0
                    painter.drawEllipse(
                        self._screen_point(point.x, point.y, track), radius, radius
                    )
            painter.restore()

        painter.save()
        painter.setClipRect(self.rect())
        # Ring the highlighted sample so hover and keyboard focus read the same.
        if self._hover_index is not None:
            for point, index in self._hit_points():
                if index != self._hover_index:
                    continue
                painter.setPen(QPen(ink.focus, 1.5))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(point, 8.0, 8.0)
        painter.restore()

        if self.hasFocus():
            painter.setPen(QPen(ink.focus, 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(-2.0, -2.0, 2.0, 2.0))
