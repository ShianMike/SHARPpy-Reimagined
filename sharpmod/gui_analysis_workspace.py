"""Interactive comparison, trend, and ensemble workspace for sounding viewers.

The expensive meteorological work lives in :mod:`sharpmod.profile_metrics`.
This module is deliberately a presentation layer: it submits one lazy job for
the visible tab, rejects stale results, and only paints pre-computed numbers.
That split keeps both the viewer and its focused GUI tests responsive.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
import logging
import math
from pathlib import Path
from typing import NamedTuple
import weakref

from qtpy.QtCore import QObject, QPointF, QRectF, QRunnable, QThreadPool, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.box_analysis import PARAMETERS, parameter
from sharpmod.colors import semantic_palette
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.gui_shell import dock_title_bar
from sharpmod.gui_theme import current_theme, mono_font, ui_font
from sharpmod.profile_metrics import (
    DEFAULT_METRIC_KEYS,
    ProfileMetricsEngine,
    export_comparison_csv,
    export_timeline_csv,
    export_timeline_series_csv,
)
from sharpmod.theme import (
    CONTROL_H,
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_SECTION_LABEL,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)


_LOGGER = logging.getLogger(__name__)
_MISSING = "\u2014"
_THERMO_RANGE_C = (-50.0, 50.0)
_SHARED_POOL = None

#: Isobars a forecaster reads a profile against. Only those inside the plotted
#: band are drawn, so a shallow band does not gain gridlines it cannot support.
_STANDARD_ISOBARS = (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100)

#: Temperature gridlines. 25 degree steps land on 0 C -- the one isotherm that
#: has to be labelled -- while staying sparse enough to read in a narrow dock.
_THERMO_STEP_C = 25.0


def _analysis_pool():
    """Return one process-wide, single-worker analysis queue.

    The pool outlives individual windows. Closing a sounding therefore never
    waits for an already-running composite calculation, while every analysis
    workspace together still has a hard concurrency bound of one.
    """
    global _SHARED_POOL
    if _SHARED_POOL is None:
        _SHARED_POOL = QThreadPool()
        _SHARED_POOL.setMaxThreadCount(1)
        _SHARED_POOL.setExpiryTimeout(15_000)
    return _SHARED_POOL


def _get(value, name, default=None):
    """Read a dataclass attribute or mapping item."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _number(value):
    """Return a finite float, otherwise ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metric_value(sample, key):
    values = _get(sample, "values", {}) or {}
    return _number(values.get(key))


def _metric_title(key):
    try:
        item = parameter(key)
    except Exception:
        return str(key).replace("_", " ").upper(), "", 2
    return item.label, item.units, item.decimals


def _format_metric(key, value):
    number = _number(value)
    if number is None:
        return _MISSING
    try:
        return parameter(key).format(number)
    except Exception:
        return f"{number:.2f}"


def _format_range(key, minimum, maximum):
    """Spell a span out in words.

    The bare hyphen this replaces rendered a negative CIN range as
    ``-172--134``, which reads as neither a range nor two numbers.
    """
    if minimum is None or maximum is None:
        return _MISSING
    return f"{_format_metric(key, minimum)} to {_format_metric(key, maximum)}"


def _delta_ink(delta):
    """Colour a delta by direction.

    Green above the reference and red below is the convention forecasters
    already read in model-comparison tables; it means *higher* and *lower*, not
    better and worse, which is why every delta cell also carries a tooltip
    naming the reference explicitly.
    """
    theme = current_theme()
    if delta is None or delta == 0:
        return QColor(theme.text_tertiary)
    return QColor(theme.success if delta > 0 else theme.danger)


def _format_time(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%MZ")
    return str(value) if value is not None else _MISSING


def _meta(collection, name, default=None):
    try:
        value = collection.getMeta(name)
    except (AttributeError, KeyError, TypeError, ValueError):
        value = default
    return default if value is None else value


def _collection_label(collection, index):
    loc = _meta(collection, "loc", f"Sounding {index + 1}")
    model = _meta(collection, "model", "")
    run = _meta(collection, "run")
    bits = [str(loc)]
    if model:
        bits.append(str(model).upper())
    if isinstance(run, datetime):
        bits.append(run.strftime("%d %b %H%MZ"))
    elif run:
        bits.append(str(run))
    return " \u00b7 ".join(bits)


def _current_date(collection):
    try:
        return collection.getCurrentDate()
    except (AttributeError, IndexError, TypeError, ValueError):
        dates = tuple(getattr(collection, "_dates", ()))
        return dates[0] if dates else None


def _inset(widget, left, top, right, bottom):
    """Return the drawable rectangle inside a widget's axis gutters."""
    return QRectF(
        left,
        top,
        max(1.0, widget.width() - left - right),
        max(1.0, widget.height() - top - bottom),
    )


class _ChartInk(NamedTuple):
    """Colours for one chart repaint, resolved from the live chrome theme.

    These charts previously hardcoded a blue-grey ramp (``#101820`` /
    ``#283848`` / ``#52606d``) -- which is precisely the generic dark palette
    :mod:`sharpmod.theme` documents rejecting, so the panel read as belonging to
    a different application than the dock around it. Chrome roles come from the
    theme; the two thermodynamic traces come from
    :func:`sharpmod.colors.semantic_palette` so temperature and dewpoint are the
    same red and green the user is already reading on the skew-T.
    """

    background: QColor
    frame: QColor
    grid: QColor
    axis: QColor
    title: QColor
    focus: QColor
    #: Fallback for a trend series with no colour of its own; the per-sounding
    #: colours come from :func:`_series_colours`.
    series: QColor
    temperature: QColor
    dewpoint: QColor
    freezing: QColor


@lru_cache(maxsize=8)
def _trace_inks(background, foreground):
    """Cache the contrast-resolved trace colours for one chrome surface."""
    palette = semantic_palette(background, foreground)
    return palette["red"], palette["green"]


#: Qualitative cycle for the trend chart's soundings. Hues, not shades: the
#: series are unordered categories, so a sequential ramp would imply a ranking
#: that does not exist. Taken from the renderer's palette so they are the same
#: colours -- and the same contrast resolution -- as the scientific canvas.
_SERIES_ROLES = ("blue", "orange", "green", "magenta", "cyan", "yellow")


@lru_cache(maxsize=8)
def _series_inks(background, foreground):
    palette = semantic_palette(background, foreground)
    return tuple(palette[role] for role in _SERIES_ROLES)


def _series_colours(count):
    """Return ``count`` series colours, cycling once the palette runs out."""
    theme = current_theme()
    inks = _series_inks(theme.surface_sunken, theme.text_primary)
    return tuple(inks[index % len(inks)] for index in range(max(0, int(count))))


def _short_labels(collections):
    """Legend-sized names, made unique so a colour can be resolved to a run.

    The full ``loc - MODEL - run`` label is far too wide for a legend, but a
    bare model name is ambiguous the moment two runs of the same model are
    loaded -- which is exactly the comparison this chart exists for.
    """
    names = []
    for index, collection in enumerate(collections):
        model = _meta(collection, "model", "")
        loc = _meta(collection, "loc", "")
        names.append(str(model).upper() or str(loc) or f"#{index + 1}")
    counts = Counter(names)
    resolved = []
    for index, (collection, name) in enumerate(zip(collections, names)):
        if counts[name] > 1:
            run = _meta(collection, "run")
            name = (
                f"{name} {run:%H}Z" if isinstance(run, datetime) else f"{name} #{index + 1}"
            )
        resolved.append(name)
    # A model and run pair can still repeat -- two locations, one run -- so any
    # name that is still shared falls back to its position.
    repeats = Counter(resolved)
    return tuple(
        f"{name} #{index + 1}" if repeats[name] > 1 else name
        for index, name in enumerate(resolved)
    )


class _TrendSeries(NamedTuple):
    """One sounding's timeline, ready to plot."""

    collection_index: int
    label: str
    short_label: str
    colour: str
    samples: tuple


class _TrendPoint(NamedTuple):
    """One plotted sample, addressable by a single flat index.

    ``index`` is what :attr:`_TrendChart.pointActivated` carries, so a receiver
    needs no knowledge of how the series are ordered to resolve a click back to
    a collection and a valid time.
    """

    x: float
    y: float | None
    index: int
    series: int
    sample: int


def _chart_ink():
    """Resolve chart colours against whichever theme is currently applied."""
    theme = current_theme()
    warm, cool = _trace_inks(theme.surface_sunken, theme.text_primary)
    return _ChartInk(
        background=QColor(theme.surface_sunken),
        # border_strong bounds the data region; plain border is the interior
        # rule, which must stay quieter than the data drawn over it.
        frame=QColor(theme.border_strong),
        grid=QColor(theme.border),
        axis=QColor(theme.text_tertiary),
        title=QColor(theme.text_secondary),
        focus=QColor(theme.focus_ring),
        series=QColor(theme.info),
        temperature=QColor(warm),
        dewpoint=QColor(cool),
        freezing=QColor(theme.info),
    )


def _nice_ticks(low, high, target=4):
    """Return rounded gridline values spanning ``low``..``high``.

    Snapping to 1/2/2.5/5/10 times a power of ten keeps axis labels readable
    (``500``, ``1000``) instead of echoing the padded data bounds (``486.3``).
    """
    span = float(high) - float(low)
    if not math.isfinite(span) or span <= 0:
        return (float(low),)
    rough = span / max(1, int(target))
    magnitude = 10.0 ** math.floor(math.log10(rough))
    step = magnitude * 10.0
    for factor in (1.0, 2.0, 2.5, 5.0):
        candidate = magnitude * factor
        if candidate >= rough:
            step = candidate
            break
    ticks = []
    value = math.ceil(low / step) * step
    # Tolerance absorbs the float error that would otherwise drop the top tick.
    while value <= high + step * 1e-9:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return tuple(ticks)


def _tick_decimals(step):
    """Decimals needed to print a tick step without a misleading rounding."""
    if step <= 0 or not math.isfinite(step):
        return 0
    return max(0, min(3, int(math.ceil(-math.log10(step))) + 1)) if step < 1 else 0


class _TrendChart(QWidget):
    """Small dependency-free time-series chart with selectable samples.

    Reachable by mouse *and* keyboard: the chart takes focus, Left/Right walk
    the plotted samples, and Enter opens the highlighted valid time. Without
    that a click was the only way to reach the feature the chart advertises.
    """

    pointActivated = Signal(int)

    #: Pixel radius within which a click counts as hitting a sample.
    HIT_RADIUS = 15.0

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
        self._prepared = ()
        self._x_bounds = (0.0, 1.0)
        self._y_bounds = (0.0, 1.0)
        self._multiday = False
        self._hover_index = None

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
        """Prepare numeric plot inputs once; :meth:`paintEvent` stays cheap.

        Every sounding is plotted together on shared axes, so the comparison the
        chart is for -- how the runs disagree about the same parameter over the
        same hours -- is one read rather than a sequence of remembered ones.
        Points are flattened into :attr:`_prepared` in series-then-sample order,
        which is what lets one integer identify a point.
        """
        self._series = tuple(series or ())
        self._metric = str(metric)
        self._hover_index = None
        self.setToolTip("")
        prepared = []
        finite_x = []
        finite_y = []
        days = set()
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
                y_value = _metric_value(sample, self._metric)
                if not bool(_get(sample, "available", True)):
                    y_value = None
                prepared.append(
                    _TrendPoint(
                        x_value, y_value, len(prepared), series_index, sample_index
                    )
                )
                finite_x.append(x_value)
                if y_value is not None:
                    finite_y.append(y_value)
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
        if finite_y:
            y_min, y_max = min(finite_y), max(finite_y)
            pad = max((y_max - y_min) * 0.08, 1.0 if y_min == y_max else 0.0)
            self._y_bounds = (y_min - pad, y_max + pad)
        else:
            self._y_bounds = (0.0, 1.0)
        self.update()

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

    def _y_ticks(self):
        """Return labelled value gridlines and the decimals to print them at.

        Ticks whose label would repeat the one below are dropped: label precision
        is capped, so a series that barely varies would otherwise draw a stack of
        gridlines all reading ``0.000``. Deduplicating here rather than in
        :meth:`paintEvent` keeps the gutter measurement and the drawing in step.
        """
        y_min, y_max = self._y_bounds
        ticks = _nice_ticks(y_min, y_max)
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

    def _plot_area(self):
        """Reserve gutters sized to the text that actually has to fit in them.

        The previous fixed 46px left gutter clipped four-digit CAPE labels and
        wasted half its width on two-digit ones.
        """
        values = QFontMetricsF(self._value_font)
        labels = QFontMetricsF(self._label_font)
        ticks, decimals = self._y_ticks()
        widest = max(
            (values.horizontalAdvance(f"{tick:.{decimals}f}") for tick in ticks),
            default=0.0,
        )
        left = SPACE["sm"] + widest + SPACE["xs"]
        top = SPACE["xs"] + labels.height() + SPACE["sm"]
        if self._needs_legend():
            # Its own line rather than the title's: sounding names beside a
            # parameter name overran the width and collided.
            top += labels.height() + SPACE["xxs"]
        bottom = SPACE["xs"] + values.height() + SPACE["xs"]
        return _inset(self, left, top, SPACE["md"], bottom)

    def _screen_point(self, x_value, y_value):
        rect = self._plot_area()
        x_min, x_max = self._x_bounds
        y_min, y_max = self._y_bounds
        x = rect.left() + (x_value - x_min) / (x_max - x_min) * rect.width()
        y = rect.bottom() - (y_value - y_min) / (y_max - y_min) * rect.height()
        return QPointF(x, y)

    def _hit_points(self):
        return tuple(
            (self._screen_point(point.x, point.y), point.index)
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
        return (
            f"{heading}{_format_time(_get(sample, 'valid_time'))}\n"
            f"{label}: {reading}{suffix}\n"
            "Click or press Enter to open this valid time"
        )

    def _highlight(self, index):
        if index == self._hover_index:
            return
        self._hover_index = index
        self.setToolTip("" if index is None else self._sample_tooltip(index))
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
        """Name each colour, dropping entries rather than overrunning the width."""
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
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(item.colour)))
            painter.drawRoundedRect(
                QRectF(x, baseline - swatch, swatch, swatch), 2.0, 2.0
            )
            painter.setBrush(Qt.NoBrush)
            painter.setPen(ink.title)
            painter.drawText(QPointF(x + swatch + gap, baseline), text)
            x += width + SPACE["md"]

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        ink = _chart_ink()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), ink.background)
        rect = self._plot_area()
        label_metrics = QFontMetricsF(self._label_font)
        value_metrics = QFontMetricsF(self._value_font)

        painter.setFont(self._label_font)
        painter.setPen(ink.title)
        label, units, _decimals = _metric_title(self._metric)
        title = f"{label}{f' ({units})' if units else ''}"
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
            return

        # Value gridlines, labelled. Three unlabelled lines used to imply a
        # scale the axis never actually printed.
        ticks, decimals = self._y_ticks()
        y_min, y_max = self._y_bounds
        painter.setFont(self._value_font)
        for tick in ticks:
            if not y_min <= tick <= y_max:
                continue
            y = self._screen_point(self._x_bounds[0], tick).y()
            painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(ink.axis)
            painter.drawText(
                QRectF(
                    SPACE["sm"],
                    y - value_metrics.height() / 2.0,
                    rect.left() - SPACE["sm"] - SPACE["xs"],
                    value_metrics.height(),
                ),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{tick:.{decimals}f}",
            )

        # Time gridlines and labels.
        for point in self._time_ticks(rect, value_metrics):
            x = self._screen_point(point.x, y_min).x()
            painter.setPen(QPen(ink.grid, 1.0, Qt.DotLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            text = self._axis_time(self._point_time(point))
            width = value_metrics.horizontalAdvance(text) + SPACE["sm"]
            painter.setPen(ink.axis)
            painter.drawText(
                QRectF(
                    min(
                        max(rect.left() - SPACE["sm"], x - width / 2.0),
                        self.width() - width,
                    ),
                    rect.bottom() + SPACE["xs"],
                    width,
                    value_metrics.height(),
                ),
                Qt.AlignHCenter | Qt.AlignTop,
                text,
            )

        painter.setPen(QPen(ink.frame, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        painter.save()
        painter.setClipRect(rect.adjusted(-3.0, -3.0, 3.0, 3.0))
        by_series = {}
        for point in self._prepared:
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
                    # A visible marker plus a path break distinguishes a true gap
                    # from a meteorological jump between adjacent valid times.
                    # Drawn in the series colour, so with several soundings on
                    # one axis it is clear whose hour is missing.
                    x = self._screen_point(point.x, y_min).x()
                    painter.setPen(QPen(colour, 1.5))
                    painter.drawLine(
                        QPointF(x - 4, rect.bottom() - 4),
                        QPointF(x + 4, rect.bottom() + 4),
                    )
                    painter.drawLine(
                        QPointF(x - 4, rect.bottom() + 4),
                        QPointF(x + 4, rect.bottom() - 4),
                    )
                    path_open = False
                    continue
                position = self._screen_point(point.x, point.y)
                if path_open:
                    path.lineTo(position)
                else:
                    path.moveTo(position)
                    path_open = True
            painter.setPen(QPen(colour, 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            painter.setPen(QPen(ink.background, 1.0))
            painter.setBrush(QBrush(colour))
            for point in points:
                if point.y is None:
                    continue
                radius = 5.0 if point.index == self._hover_index else 3.0
                painter.drawEllipse(
                    self._screen_point(point.x, point.y), radius, radius
                )
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


class _EnvelopeChart(QWidget):
    """Temperature/dewpoint p10-p90 ribbons from pre-computed ensemble bands."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisEnsembleBandChart")
        self.setMinimumHeight(235)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Ensemble thermodynamic envelope")
        self.setToolTip(
            "Shaded ribbon spans the 10th to 90th member percentile; the solid "
            "line is the member median. Temperature scale is fixed to "
            "\u221250 \u00b0C \u2013 +50 \u00b0C so runs stay comparable."
        )
        self._label_font = ui_font("caption")
        self._value_font = mono_font("caption")
        self._temperature = ()
        self._dewpoint = ()
        self._pressure_bounds = (100.0, 1000.0)
        self._temperature_bounds = _THERMO_RANGE_C

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
        self.update()

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

    def _draw_curve(self, painter, points, color):
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
        painter.setPen(QPen(QColor(color), 2.0))
        painter.drawPath(median_path)

    def _draw_legend(self, painter, rect, ink, metrics):
        """Caption plus right-aligned swatch chips, thinned when space runs out.

        The previous legend hand-positioned three strings on one baseline with a
        literal 14px gap, so in a narrow dock the caption and the series names
        overlapped. Here the caption is dropped first, then the chip labels
        shorten, and the widget tooltip carries the meaning either way.
        """
        swatch = metrics.ascent()
        gap = SPACE["xs"]
        baseline = SPACE["xs"] + metrics.ascent()
        available = rect.right()
        # Starts at the plot edge, clear of the pressure-axis gutter the unit
        # label occupies.
        caption_x = rect.left()

        for labels in (("Temperature", "Td"), ("Temp", "Td")):
            entries = tuple(zip(labels, (ink.temperature, ink.dewpoint)))
            widths = [
                swatch + gap + metrics.horizontalAdvance(text)
                for text, _colour in entries
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
            for (text, colour), width in zip(entries, widths):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(colour))
                painter.drawRoundedRect(
                    QRectF(x, baseline - swatch, swatch, swatch), 2.0, 2.0
                )
                painter.setBrush(Qt.NoBrush)
                painter.setPen(ink.title)
                painter.drawText(QPointF(x + swatch + gap, baseline), text)
                x += width + SPACE["md"]
            return

    def paintEvent(self, _event):  # noqa: N802 - Qt override
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
        self._draw_curve(painter, self._temperature, ink.temperature)
        self._draw_curve(painter, self._dewpoint, ink.dewpoint)
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


class _TaskSignals(QObject):
    completed = Signal(int, str, object)
    failed = Signal(int, str, str)


class _ComputeTask(QRunnable):
    """A single bounded calculation submitted to the workspace pool."""

    def __init__(self, generation, kind, function):
        super().__init__()
        self.generation = int(generation)
        self.kind = str(kind)
        self.function = function
        self.signals = _TaskSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.function()
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.signals.failed.emit(self.generation, self.kind, str(exc))
            return
        self.signals.completed.emit(self.generation, self.kind, result)


class AnalysisWorkspace(QWidget):
    """Controller and widget for all cross-sounding analysis features."""

    TAB_TRENDS = 0
    TAB_COMPARE = 1
    TAB_ENSEMBLE = 2
    TAB_NOTES = 3

    def __init__(self, win, *, engine=None, async_compute=True, parent=None):
        super().__init__(parent or win)
        self.setObjectName("sharpmodAnalysisWorkspace")
        self._win_ref = weakref.ref(win)
        self.engine = engine or ProfileMetricsEngine()
        self._async_compute = bool(async_compute)
        self._generation = {"trends": 0, "compare": 0, "ensemble": 0}
        self._trend_samples = ()
        self._trend_series = ()
        self._comparison_samples = ()
        self._trend_collection_index = 0
        self._pool = _analysis_pool()
        self._tasks = {}
        # One monospace font for every column of figures, so digits share an
        # advance width and a reader can compare rows down the column.
        self._figure_font = mono_font("small")
        self._build_ui()
        self._populate_controls()

    # -- construction ------------------------------------------------------- #

    @staticmethod
    def _section(text, parent=None):
        label = QLabel(text, parent)
        label.setObjectName(OBJ_SECTION_LABEL)
        return label

    @staticmethod
    def _new_status(parent):
        label = QLabel(parent)
        label.setObjectName(OBJ_STATUS)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _new_action(text, tooltip, parent):
        """Tertiary action styling: these sit beside status prose, not below it."""
        button = QPushButton(text, parent)
        button.setObjectName(OBJ_GHOST)
        button.setToolTip(tooltip)
        return button

    @staticmethod
    def _new_page(parent):
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"]
        )
        layout.setSpacing(SPACE["sm"])
        return page, layout

    @staticmethod
    def _new_row(spacing="sm"):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE[spacing])
        return row

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        outer.setSpacing(SPACE["sm"])
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("analysisWorkspaceTabs")
        outer.addWidget(self.tabs)
        self._build_trends_tab()
        self._build_compare_tab()
        self._build_ensemble_tab()
        self._build_notes_tab()
        self._connect()

    def _build_trends_tab(self):
        self.trends_tab, layout = self._new_page(self.tabs)
        selectors = self._new_row()
        self.trend_metric = QComboBox(self.trends_tab)
        self.trend_metric.setObjectName("analysisTrendMetric")
        self.trend_metric.setToolTip("Parameter plotted against valid time")
        self.trend_metric.setAccessibleName("Trend parameter")
        for item in PARAMETERS:
            suffix = f" ({item.units})" if item.units else ""
            self.trend_metric.addItem(f"{item.label}{suffix}", item.key)
        default_metric = (
            str(DEFAULT_METRIC_KEYS[0]) if DEFAULT_METRIC_KEYS else "mlcape"
        )
        index = self.trend_metric.findData(default_metric)
        self.trend_metric.setCurrentIndex(max(0, index))
        # One parameter across every loaded sounding, rather than one sounding at
        # a time: there is no sounding picker here because the chart plots them
        # all, and the legend names the colours.
        selectors.addWidget(self.trend_metric, 1)
        layout.addLayout(selectors)

        actions = self._new_row()
        self.trend_status = self._new_status(self.trends_tab)
        actions.addWidget(self.trend_status, 1)
        self.trend_refresh = self._new_action(
            "Refresh", "Recompute this timeline", self.trends_tab
        )
        self.trend_export = self._new_action(
            "Export CSV\u2026", "Save the plotted series as CSV", self.trends_tab
        )
        self.trend_export.setEnabled(False)
        actions.addWidget(self.trend_refresh)
        actions.addWidget(self.trend_export)
        layout.addLayout(actions)

        self.trend_chart = _TrendChart(self.trends_tab)
        layout.addWidget(self.trend_chart, 1)
        self.tabs.addTab(self.trends_tab, "Trends")

    def _build_compare_tab(self):
        self.compare_tab, layout = self._new_page(self.tabs)
        header = self._new_row()
        header.addWidget(self._section("Reference", self.compare_tab))
        self.compare_reference = QComboBox(self.compare_tab)
        self.compare_reference.setObjectName("analysisComparisonReference")
        self.compare_reference.setToolTip(
            "Every \u0394 column is measured against this sounding"
        )
        self.compare_reference.setAccessibleName("Comparison reference sounding")
        header.addWidget(self.compare_reference, 1)
        layout.addLayout(header)

        actions = self._new_row()
        self.compare_status = self._new_status(self.compare_tab)
        actions.addWidget(self.compare_status, 1)
        self.compare_refresh = self._new_action(
            "Refresh", "Realign and recompute the comparison", self.compare_tab
        )
        self.compare_export = self._new_action(
            "Export CSV\u2026", "Save the aligned comparison as CSV", self.compare_tab
        )
        self.compare_export.setEnabled(False)
        actions.addWidget(self.compare_refresh)
        actions.addWidget(self.compare_export)
        layout.addLayout(actions)

        self.compare_table = self._new_table("analysisComparisonTable")
        layout.addWidget(self.compare_table, 1)
        self.tabs.addTab(self.compare_tab, "Compare")

    def _build_ensemble_tab(self):
        self.ensemble_tab, layout = self._new_page(self.tabs)
        header = self._new_row()
        self.ensemble_status = self._new_status(self.ensemble_tab)
        header.addWidget(self.ensemble_status, 1)
        self.ensemble_refresh = self._new_action(
            "Refresh", "Resummarize the ensemble members", self.ensemble_tab
        )
        header.addWidget(self.ensemble_refresh)
        layout.addLayout(header)

        self.ensemble_table = self._new_table("analysisEnsembleTable")
        self.ensemble_chart = _EnvelopeChart(self.ensemble_tab)
        # A splitter rather than a hard 50/50 stretch: which half matters
        # depends on the question, and a fixed split left both halves cramped.
        self.ensemble_split = QSplitter(Qt.Vertical, self.ensemble_tab)
        self.ensemble_split.setObjectName("analysisEnsembleSplitter")
        self.ensemble_split.setChildrenCollapsible(False)
        self.ensemble_split.setHandleWidth(SPACE["sm"])
        for title, widget, tip in (
            (
                "Member spread",
                self.ensemble_table,
                "Percentiles across the members available at this valid time",
            ),
            (
                "Vertical envelope",
                self.ensemble_chart,
                "Temperature and dewpoint spread by pressure level",
            ),
        ):
            pane = QWidget(self.ensemble_split)
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.setSpacing(SPACE["xs"])
            heading = self._section(title, pane)
            heading.setToolTip(tip)
            pane_layout.addWidget(heading)
            pane_layout.addWidget(widget, 1)
            self.ensemble_split.addWidget(pane)
        self.ensemble_split.setStretchFactor(0, 3)
        self.ensemble_split.setStretchFactor(1, 4)
        layout.addWidget(self.ensemble_split, 1)
        self.tabs.addTab(self.ensemble_tab, "Ensemble")

    def _build_notes_tab(self):
        self.notes_tab, layout = self._new_page(self.tabs)
        note_help = QLabel(
            "Saved with the analysis session, so it travels with the soundings, "
            "overlays, and valid times you are looking at.",
            self.notes_tab,
        )
        note_help.setObjectName(OBJ_HINT)
        note_help.setWordWrap(True)
        layout.addWidget(note_help)
        self.notes = QPlainTextEdit(self.notes_tab)
        self.notes.setObjectName("analysisWorkspaceNotes")
        self.notes.setPlaceholderText(
            "Record decisions, uncertainty, or handoff notes\u2026"
        )
        layout.addWidget(self.notes, 1)
        self.tabs.addTab(self.notes_tab, "Notes")

    def _connect(self):
        self.tabs.currentChanged.connect(self._refresh_active_tab)
        self.trend_metric.currentIndexChanged.connect(self._refresh_trends)
        self.trend_refresh.clicked.connect(self._refresh_trends)
        self.trend_export.clicked.connect(self._choose_trend_export)
        self.trend_chart.pointActivated.connect(self._activate_trend_point)
        self.compare_reference.currentIndexChanged.connect(self._refresh_compare)
        self.compare_refresh.clicked.connect(self._refresh_compare)
        self.compare_export.clicked.connect(self._choose_comparison_export)
        self.ensemble_refresh.clicked.connect(self._refresh_ensemble)

    @staticmethod
    def _new_table(name):
        table = QTableWidget()
        table.setObjectName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        # Banded rows already separate records; grid lines on top of them add
        # ink without adding information.
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setCornerButtonEnabled(False)
        table.setTextElideMode(Qt.ElideRight)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(CONTROL_H["sm"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        # The first column absorbs slack instead of the last. Stretching the last
        # one stranded the right-aligned figures far from their header.
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        return table

    @staticmethod
    def _finish_table(table, figure_from, *, stretch=0, capped=()):
        """Align figure headers with their cells and place any leftover width.

        ``stretch`` is the column that absorbs slack, or ``None`` to leave it
        unclaimed. A wide table must pass ``None``: a stretched column is also
        the first one Qt shrinks when the sections overflow, which squeezed the
        sounding name -- the row's identity -- down to an ellipsis.

        Rows are deliberately left at the uniform default section height rather
        than resized to content: a per-row height fights the row token and, in
        the ensemble pane, inflated five rows past the height the splitter had
        given them, so the table opened already scrolling.
        """
        header = table.horizontalHeader()
        if stretch is not None and table.columnCount() > int(stretch):
            header.setSectionResizeMode(int(stretch), QHeaderView.Stretch)
        # Prose columns are capped so the figures stay on screen; the full text
        # remains reachable as a tooltip.
        for column, width in capped:
            if column < table.columnCount():
                header.setSectionResizeMode(column, QHeaderView.Interactive)
                header.resizeSection(column, int(width))
        for column in range(int(figure_from), table.columnCount()):
            item = table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    # -- cell factories ----------------------------------------------------- #

    @staticmethod
    def _text_cell(text, *, tooltip=None, emphasis=False):
        item = QTableWidgetItem(str(text))
        if tooltip:
            item.setToolTip(str(tooltip))
        if emphasis:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def _figure_cell(self, text, *, tooltip=None, colour=None):
        """A right-aligned monospace figure.

        Colour arrives as a resolved theme role rather than a literal: per-item
        foregrounds cannot be expressed in the style sheet without an item
        delegate, so this is the one place values are set in code.
        """
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFont(self._figure_font)
        if tooltip:
            item.setToolTip(str(tooltip))
        if colour is not None:
            item.setForeground(QBrush(colour))
        return item

    # -- status ------------------------------------------------------------- #

    #: Severity of a status line mapped onto a semantic text role. Swapping the
    #: object name keeps the colour in the style sheet, where a theme change can
    #: still reach it, instead of baking a literal into a setStyleSheet call.
    _STATUS_ROLES = {
        "info": OBJ_STATUS,
        "warn": OBJ_WARNING_TEXT,
        "error": OBJ_ERROR_TEXT,
    }

    @classmethod
    def _set_status(cls, label, text, *, level="info"):
        label.setText(str(text))
        wanted = cls._STATUS_ROLES.get(level, OBJ_STATUS)
        if label.objectName() != wanted:
            label.setObjectName(wanted)
            style = label.style()
            style.unpolish(label)
            style.polish(label)

    def _set_busy(self, kind, busy):
        """Disable the matching Refresh while its calculation is outstanding."""
        button = {
            "trends": getattr(self, "trend_refresh", None),
            "compare": getattr(self, "compare_refresh", None),
            "ensemble": getattr(self, "ensemble_refresh", None),
        }.get(str(kind))
        if button is not None:
            button.setEnabled(not busy)

    def _window(self):
        return self._win_ref()

    def _collections(self):
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        return tuple(getattr(widget, "prof_collections", ()) or ())

    def _focused_index(self):
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        try:
            return max(0, int(widget.pc_idx))
        except (AttributeError, TypeError, ValueError):
            return 0

    def _populate_controls(self):
        collections = self._collections()
        previous_reference = self.compare_reference.currentData()
        focused = min(self._focused_index(), max(0, len(collections) - 1))
        combo = self.compare_reference
        blocked = combo.blockSignals(True)
        combo.clear()
        for index, collection in enumerate(collections):
            combo.addItem(_collection_label(collection, index), index)
        old_index = combo.findData(previous_reference)
        combo.setCurrentIndex(old_index if old_index >= 0 else focused)
        combo.blockSignals(blocked)
        self._trend_collection_index = min(
            max(0, self._trend_collection_index), max(0, len(collections) - 1)
        )

    def refresh(self):
        """Refresh selectors and lazily recompute only the visible tab."""
        dock = getattr(self, "dock", None)
        if dock is not None and dock.isHidden():
            return
        self._populate_controls()
        self._refresh_active_tab(self.tabs.currentIndex())

    def open(self, tab=None):
        """Show the dock and optionally select a tab by index or label."""
        if isinstance(tab, str):
            wanted = tab.strip().casefold()
            for index in range(self.tabs.count()):
                if self.tabs.tabText(index).casefold() == wanted:
                    tab = index
                    break
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)
        dock = getattr(self, "dock", None)
        if dock is not None:
            dock.show()
            dock.raise_()
        self.refresh()

    def show_compare(self):
        """Open the comparison tab for picker-side acquisition workflows."""
        self.open(self.TAB_COMPARE)

    def show_ensemble(self):
        """Open the ensemble tab after a picker assembles fetched members."""
        self.open(self.TAB_ENSEMBLE)

    def cancel_pending(self):
        """Forget queued work without waiting for an in-flight calculation."""
        for kind in self._generation:
            self._generation[kind] += 1
            # Results already in flight are now stale and will never reach
            # _accept_result, so clear the busy latch here or Refresh stays dead.
            self._set_busy(kind, False)
        self._take_queued_tasks()

    def _take_queued_tasks(self):
        """Remove this workspace's not-yet-running jobs from the shared pool."""
        for key, task in tuple(self._tasks.items()):
            try:
                removed = self._pool.tryTake(task)
            except RuntimeError:
                removed = False
            if removed:
                self._tasks.pop(key, None)

    def shutdown(self):
        """Make outstanding results inert; closing a window remains instant."""
        self.cancel_pending()

    def _refresh_active_tab(self, index=None):
        index = self.tabs.currentIndex() if index is None else int(index)
        if index == self.TAB_TRENDS:
            self._refresh_trends()
        elif index == self.TAB_COMPARE:
            self._refresh_compare()
        elif index == self.TAB_ENSEMBLE:
            self._refresh_ensemble()

    def _request(self, kind, function):
        self._generation[kind] += 1
        generation = self._generation[kind]
        self._set_busy(kind, True)
        if not self._async_compute:
            try:
                result = function()
            except Exception as exc:  # noqa: BLE001 - presentation boundary
                self._accept_failure(generation, kind, str(exc))
            else:
                self._accept_result(generation, kind, result)
            return
        # Keep at most one queued analysis behind the running one. Rapid slider
        # or selector changes discard work which has not begun yet; the token
        # below discards a result from any task already in flight.
        self._take_queued_tasks()
        task = _ComputeTask(generation, kind, function)
        task.signals.completed.connect(self._accept_result)
        task.signals.failed.connect(self._accept_failure)
        self._tasks[(kind, generation)] = task
        self._pool.start(task)

    def _accept_result(self, generation, kind, result):
        self._tasks.pop((str(kind), int(generation)), None)
        if int(generation) != self._generation.get(str(kind)):
            return
        self._set_busy(kind, False)
        if kind == "trends":
            self._render_trends(result)
        elif kind == "compare":
            self._render_compare(result)
        elif kind == "ensemble":
            self._render_ensemble(result)

    def _accept_failure(self, generation, kind, message):
        self._tasks.pop((str(kind), int(generation)), None)
        if int(generation) != self._generation.get(str(kind)):
            return
        self._set_busy(kind, False)
        label = {
            "trends": self.trend_status,
            "compare": self.compare_status,
            "ensemble": self.ensemble_status,
        }.get(str(kind))
        if label is not None:
            self._set_status(
                label, f"Analysis unavailable: {message}", level="error"
            )
        _LOGGER.warning("analysis_workspace.%s_failed: %s", kind, message)

    def _refresh_trends(self, *_args):
        if self.tabs.currentIndex() != self.TAB_TRENDS:
            return
        collections = self._collections()
        if not collections:
            self._trend_samples = ()
            self._trend_series = ()
            self.trend_chart.set_series((), "")
            self.trend_export.setEnabled(False)
            self._set_status(
                self.trend_status, "Load a forecast timeline to plot trends."
            )
            return
        key = str(self.trend_metric.currentData() or "")
        labels = tuple(
            _collection_label(collection, index)
            for index, collection in enumerate(collections)
        )
        shorts = _short_labels(collections)
        self._set_status(
            self.trend_status,
            f"Computing {len(collections)} timeline(s)\u2026",
        )

        def compute(collections=collections, key=key, labels=labels, shorts=shorts):
            # One engine call per sounding, all on the single analysis worker, so
            # plotting several does not multiply the concurrency bound.
            results = []
            for index, collection in enumerate(collections):
                results.append(
                    (
                        index,
                        labels[index],
                        shorts[index],
                        tuple(self.engine.timeline(collection, (key,)) or ()),
                    )
                )
            return tuple(results)

        self._request("trends", compute)

    def _render_trends(self, results):
        key = str(self.trend_metric.currentData() or "")
        colours = _series_colours(len(tuple(results or ())))
        series = []
        for position, entry in enumerate(tuple(results or ())):
            index, label, short, samples = entry
            series.append(
                _TrendSeries(
                    int(index), str(label), str(short), colours[position], samples
                )
            )
        self._trend_series = tuple(series)
        # Kept pointing at one series' samples: the CSV export and the session
        # both describe a focused sounding, and a flat tuple cannot say which
        # sounding a row came from.
        focused = self._focused_series()
        self._trend_samples = focused.samples if focused is not None else ()
        self.trend_chart.set_series(self._trend_series, key)

        plotted = sum(len(item.samples) for item in self._trend_series)
        available = sum(
            _metric_value(sample, key) is not None
            and bool(_get(sample, "available", True))
            for item in self._trend_series
            for sample in item.samples
        )
        missing = plotted - available
        if not plotted:
            text = "No forecast times are available for these soundings."
        else:
            count = len(self._trend_series)
            text = f"{available} available time(s)"
            if count > 1:
                text += f" across {count} soundings"
            if missing:
                text += f"; {missing} missing gap(s) are marked \u00d7"
            text += ". Click a point, or focus the chart and use \u2190 \u2192 Enter."
        self._set_status(self.trend_status, text)
        self.trend_export.setEnabled(bool(plotted))

    def _focused_series(self):
        """The series for the sounding the viewer currently has in front."""
        if not self._trend_series:
            return None
        wanted = self._trend_collection_index
        for item in self._trend_series:
            if item.collection_index == wanted:
                return item
        return self._trend_series[0]

    def _activate_trend_point(self, point_index):
        """Open the valid time a plotted point stands for, in its own sounding.

        With every sounding on one axis the point has to carry its collection:
        activating a RAP point must not move the HRRR timeline.
        """
        item, sample = self.trend_chart.sample_at(point_index)
        if item is None or sample is None:
            return
        collections = self._collections()
        target = int(item.collection_index)
        try:
            collections[target].setCurrentDate(_get(sample, "valid_time"))
        except (AttributeError, IndexError, TypeError, ValueError):
            return
        self._trend_collection_index = target
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        if widget is None:
            return
        try:
            prof_ids = tuple(getattr(widget, "prof_ids", ()))
            if target < len(prof_ids) and hasattr(widget, "setProfileCollection"):
                widget.setProfileCollection(prof_ids[target])
            else:
                widget.pc_idx = target
                widget.updateProfs()
        except Exception:
            _LOGGER.exception("analysis_workspace.trend_activation_failed")

    def _choose_trend_export(self):
        if not self._trend_samples:
            return
        try:
            suggested = export_file_path("sounding-trend.csv")
        except ExportDirectoryError as exc:
            self._set_status(self.trend_status, str(exc), level="error")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export parameter trend",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            try:
                saved = self.export_trend(path)
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self._set_status(
                    self.trend_status, f"CSV export failed: {exc}", level="error"
                )
            else:
                self._set_status(self.trend_status, f"Saved {saved}")

    def export_trend(self, path):
        """Export what is plotted, without re-running any analysis.

        A single sounding writes the plain timeline columns; several add a
        leading ``sounding`` column, because otherwise the rows could not be told
        apart. The header describes the data, which is the same rule the metric
        columns already follow.
        """
        key = str(self.trend_metric.currentData() or "")
        if len(self._trend_series) > 1:
            return export_timeline_series_csv(
                Path(path),
                ((item.label, item.samples) for item in self._trend_series),
                keys=(key,),
            )
        return export_timeline_csv(Path(path), self._trend_samples, keys=(key,))

    def _refresh_compare(self, *_args):
        if self.tabs.currentIndex() != self.TAB_COMPARE:
            return
        collections = self._collections()
        if len(collections) < 2:
            self._comparison_samples = ()
            self.compare_table.clear()
            self.compare_table.setRowCount(0)
            self.compare_table.setColumnCount(0)
            self.compare_export.setEnabled(False)
            self._set_status(
                self.compare_status,
                "Load 2 or more soundings into the same viewer to compare "
                "models or runs at one valid time.",
            )
            return
        reference = self.compare_reference.currentData()
        try:
            reference = int(reference)
            valid_time = _current_date(collections[reference])
        except (IndexError, TypeError, ValueError):
            reference = 0
            valid_time = _current_date(collections[0])
        keys = tuple(DEFAULT_METRIC_KEYS)
        self._set_status(
            self.compare_status,
            f"Aligning {len(collections)} soundings at {_format_time(valid_time)}\u2026",
        )
        self._request(
            "compare",
            lambda collections=collections, keys=keys, valid_time=valid_time: (
                self.engine.compare(collections, keys, valid_time=valid_time)
            ),
        )

    def _render_compare(self, samples):
        samples = tuple(samples or ())
        self._comparison_samples = samples
        keys = tuple(DEFAULT_METRIC_KEYS)
        headers = ["Sounding", "Valid time", "Alignment"]
        for key in keys:
            label, units, _decimals = _metric_title(key)
            headers.extend((f"{label}{f' ({units})' if units else ''}", "\u0394"))
        self.compare_table.clear()
        self.compare_table.setColumnCount(len(headers))
        self.compare_table.setHorizontalHeaderLabels(headers)
        self.compare_table.setRowCount(len(samples))

        reference_index = self.compare_reference.currentData()
        try:
            reference_index = int(reference_index)
        except (TypeError, ValueError):
            reference_index = 0
        reference = next(
            (
                sample
                for sample in samples
                if int(_get(sample, "collection_index", -1)) == reference_index
            ),
            None,
        )
        reference_label = str(_get(reference, "label", "the reference")) if reference else ""
        for row, sample in enumerate(samples):
            available = bool(_get(sample, "available", False))
            aligned = bool(_get(sample, "aligned", False))
            reason = _get(sample, "reason")
            is_reference = sample is reference
            alignment = (
                "Exact" if available and aligned else str(reason or "Unavailable")
            )
            if is_reference:
                # Name the row the deltas are measured from; otherwise the one
                # column of zeroes is the only hint, and it looks like a result.
                alignment = (
                    "Reference"
                    if available and aligned
                    else f"Reference \u00b7 {alignment}"
                )
            label = str(_get(sample, "label", f"Sounding {row + 1}"))
            self.compare_table.setItem(
                row, 0, self._text_cell(label, emphasis=is_reference)
            )
            self.compare_table.setItem(
                row,
                1,
                self._text_cell(
                    _format_time(_get(sample, "valid_time")),
                    tooltip=_format_time(_get(sample, "requested_time")),
                ),
            )
            self.compare_table.setItem(
                row,
                2,
                self._text_cell(
                    alignment,
                    # Always tooltipped: this column is width-capped, so the
                    # reason a row is unusable is often elided in the cell.
                    tooltip=(
                        alignment
                        if available and aligned
                        else f"{alignment}\nNo value is shown because the sounding "
                        "could not be aligned to the requested valid time."
                    ),
                ),
            )
            column = 3
            for key in keys:
                metric_label, units, _decimals = _metric_title(key)
                unit_suffix = f" {units}" if units else ""
                value = _metric_value(sample, key) if available and aligned else None
                reference_value = _metric_value(reference, key) if reference else None
                delta = (
                    value - reference_value
                    if value is not None and reference_value is not None
                    else None
                )
                self.compare_table.setItem(
                    row,
                    column,
                    self._figure_cell(
                        _format_metric(key, value),
                        tooltip=f"{label} \u00b7 {metric_label}{unit_suffix}",
                    ),
                )
                if is_reference:
                    # A sounding cannot differ from itself; a printed 0 implies
                    # a measured agreement that was never computed.
                    self.compare_table.setItem(
                        row,
                        column + 1,
                        self._figure_cell(
                            _MISSING, tooltip="This row is the reference"
                        ),
                    )
                else:
                    if delta is None:
                        delta_tip = f"No {metric_label} delta available"
                    else:
                        direction = (
                            "same as" if delta == 0
                            else ("above" if delta > 0 else "below")
                        )
                        delta_tip = (
                            f"{metric_label} is {_format_metric(key, abs(delta))}"
                            f"{unit_suffix} {direction} {reference_label}"
                            if delta
                            else f"{metric_label} matches {reference_label}"
                        )
                    self.compare_table.setItem(
                        row,
                        column + 1,
                        self._figure_cell(
                            _format_metric(key, delta),
                            tooltip=delta_tip,
                            colour=_delta_ink(delta),
                        ),
                    )
                column += 2
        unavailable = sum(
            not bool(_get(sample, "available", False))
            or not bool(_get(sample, "aligned", False))
            for sample in samples
        )
        target = _get(samples[0], "requested_time") if samples else None
        status = f"Compared {len(samples) - unavailable} at {_format_time(target)}"
        if unavailable:
            status += f"; {unavailable} mismatch/unavailable row(s) are explicit"
        self._set_status(self.compare_status, status + ".")
        self.compare_export.setEnabled(bool(samples))
        # 3 prose columns plus two per metric overruns any dock, so the reason
        # text is capped rather than allowed to push the figures off screen.
        self._finish_table(
            self.compare_table, 3, stretch=None, capped=((1, 132), (2, 148))
        )

    def _choose_comparison_export(self):
        if not self._comparison_samples:
            return
        try:
            suggested = export_file_path("sounding-comparison.csv")
        except ExportDirectoryError as exc:
            self._set_status(self.compare_status, str(exc), level="error")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export sounding comparison",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            try:
                saved = self.export_comparison(path)
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self._set_status(
                    self.compare_status, f"CSV export failed: {exc}", level="error"
                )
            else:
                self._set_status(self.compare_status, f"Saved {saved}")

    def export_comparison(self, path):
        """Export the rendered aligned comparison without recomputing it."""
        return export_comparison_csv(
            Path(path), self._comparison_samples, keys=tuple(DEFAULT_METRIC_KEYS)
        )

    def _refresh_ensemble(self, *_args):
        if self.tabs.currentIndex() != self.TAB_ENSEMBLE:
            return
        collections = self._collections()
        if not collections:
            self._set_status(
                self.ensemble_status, "Load an ensemble sounding to summarize it."
            )
            self.ensemble_table.clear()
            self.ensemble_table.setRowCount(0)
            self.ensemble_chart.set_band(None)
            return
        index = min(self._focused_index(), len(collections) - 1)
        collection = collections[index]
        valid_time = _current_date(collection)
        keys = tuple(DEFAULT_METRIC_KEYS)
        self._set_status(
            self.ensemble_status,
            f"Summarizing members at {_format_time(valid_time)}\u2026",
        )
        self._request(
            "ensemble",
            lambda collection=collection, keys=keys, valid_time=valid_time: (
                self.engine.ensemble(collection, keys, valid_time=valid_time)
            ),
        )

    def _render_ensemble(self, summary):
        available = bool(_get(summary, "available", False)) if summary else False
        scalar = (_get(summary, "scalar", {}) or {}) if summary else {}
        keys = tuple(DEFAULT_METRIC_KEYS)
        self.ensemble_table.clear()
        self.ensemble_table.setColumnCount(6)
        self.ensemble_table.setHorizontalHeaderLabels(
            ("Parameter", "Count", "p10", "Median", "p90", "Range")
        )
        self.ensemble_table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            distribution = scalar.get(key)
            label, units, _decimals = _metric_title(key)
            if units:
                label = f"{label} ({units})"
            count = _get(distribution, "count", 0) if distribution else 0
            minimum = _number(_get(distribution, "minimum")) if distribution else None
            maximum = _number(_get(distribution, "maximum")) if distribution else None
            self.ensemble_table.setItem(row, 0, self._text_cell(label))
            self.ensemble_table.setItem(
                row,
                1,
                self._figure_cell(
                    str(count), tooltip=f"{count} member(s) reported this parameter"
                ),
            )
            figures = (
                (_format_metric(key, _get(distribution, "p10")), "10th percentile"),
                (_format_metric(key, _get(distribution, "median")), "Median member"),
                (_format_metric(key, _get(distribution, "p90")), "90th percentile"),
                (_format_range(key, minimum, maximum), "Lowest to highest member"),
            )
            for offset, (text, tip) in enumerate(figures):
                self.ensemble_table.setItem(
                    row, 2 + offset, self._figure_cell(text, tooltip=tip)
                )
        band = _get(summary, "bands") if summary else None
        self.ensemble_chart.set_band(band)
        member_count = int(_get(summary, "member_count", 0) or 0) if summary else 0
        used = int(_get(summary, "available_member_count", 0) or 0) if summary else 0
        missing = tuple(_get(summary, "missing_members", ()) or ()) if summary else ()
        if available:
            text = f"{used} of {member_count} members available"
            if missing:
                text += "; missing: " + ", ".join(str(item) for item in missing)
            text += f" at {_format_time(_get(summary, 'valid_time'))}."
        else:
            text = str(
                _get(summary, "reason", None)
                or "This sounding does not contain a usable ensemble."
            )
        # A partial ensemble still renders, but the percentiles rest on fewer
        # members than the run advertises -- flag it rather than reporting it as
        # an ordinary result.
        self._set_status(
            self.ensemble_status,
            text,
            level="warn" if available and missing else "info",
        )
        self._finish_table(self.ensemble_table, 1)

    def session_state(self):
        """Return JSON-safe workspace controls and notes."""
        dock = getattr(self, "dock", None)
        return {
            "version": 1,
            "visible": bool(dock is not None and not dock.isHidden()),
            "tab": int(self.tabs.currentIndex()),
            # Every sounding is plotted now, so this is no longer a filter: it
            # records which series the reader last opened, which is what the
            # chart highlights and what the single-sounding CSV exports.
            "trend_collection": int(self._trend_collection_index),
            "trend_metric": self.trend_metric.currentData(),
            "comparison_reference": self.compare_reference.currentData(),
            # How the reader chose to divide table against chart is part of how
            # they were reading the ensemble, so it travels with the session.
            "ensemble_split": [int(size) for size in self.ensemble_split.sizes()],
            "notes": self.notes.toPlainText(),
        }

    def restore_session_state(self, state):
        """Restore a prior JSON-safe state, ignoring unknown future fields."""
        if not isinstance(state, Mapping):
            return
        self._populate_controls()
        metric = state.get("trend_metric")
        metric_index = self.trend_metric.findData(metric)
        ref_index = self.compare_reference.findData(state.get("comparison_reference"))
        tab = state.get("tab", self.TAB_TRENDS)
        try:
            self._trend_collection_index = max(0, int(state.get("trend_collection")))
        except (TypeError, ValueError):
            self._trend_collection_index = 0
        for combo, index in (
            (self.trend_metric, metric_index),
            (self.compare_reference, ref_index),
        ):
            if index >= 0:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(blocked)
        try:
            tab = max(0, min(self.tabs.count() - 1, int(tab)))
        except (TypeError, ValueError):
            tab = self.TAB_TRENDS
        blocked = self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(tab)
        self.tabs.blockSignals(blocked)
        sizes = state.get("ensemble_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.ensemble_split.setSizes([int(size) for size in sizes])
            except (TypeError, ValueError):
                # A hand-edited or future session file must not stop a restore.
                _LOGGER.debug("analysis_workspace.split_restore_skipped")
        self.notes.setPlainText(str(state.get("notes") or ""))
        dock = getattr(self, "dock", None)
        if dock is not None and "visible" in state:
            dock.setVisible(bool(state["visible"]))
        if bool(state.get("visible", False)):
            self._refresh_active_tab(tab)


def install_analysis_workspace(win, *, engine=None, async_compute=True):
    """Install the dock once and return its :class:`AnalysisWorkspace`."""
    existing = getattr(win, "_sharpmod_analysis_workspace", None)
    if existing is not None:
        return existing

    workspace = AnalysisWorkspace(
        win, engine=engine, async_compute=async_compute, parent=win
    )
    dock = QDockWidget("Analysis Workspace", win)
    dock.setObjectName("analysisWorkspaceDock")
    dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
    dock.setFeatures(QDockWidget.DockWidgetClosable)
    # Qt's own title bar leaves a ~16x9px close target that ignores the style
    # sheet -- and it is the only affordance for dismissing this panel. The
    # sounding sidebar beside it already uses the themed replacement.
    dock.setTitleBarWidget(
        dock_title_bar(dock, dock.windowTitle(), shortcut_hint="Ctrl+Shift+A")
    )
    dock.setWidget(workspace)
    win.addDockWidget(Qt.RightDockWidgetArea, dock)
    sidebar = getattr(win, "_sharpmod_sidebar_dock", None)
    if isinstance(sidebar, QDockWidget):
        win.tabifyDockWidget(sidebar, dock)
    dock.hide()
    workspace.dock = dock

    toggle = dock.toggleViewAction()
    toggle.setShortcut("Ctrl+Shift+A")
    toggle.setToolTip(
        "Open parameter trends, model/run comparison, ensemble spread, and notes"
    )
    view_menu = getattr(win, "_sharpmod_view_menu", None)
    if view_menu is not None:
        view_menu.addSeparator()
        view_menu.addAction(toggle)
    workspace_ref = weakref.ref(workspace)

    def toggled(checked):
        live = workspace_ref()
        if checked and live is not None:
            live.refresh()

    toggle.triggered.connect(toggled)

    def visibility_changed(visible):
        live = workspace_ref()
        if live is not None and not visible:
            live.cancel_pending()

    dock.visibilityChanged.connect(visibility_changed)
    win.destroyed.connect(
        lambda *_args, ref=workspace_ref: (
            ref().shutdown() if ref() is not None else None
        )
    )

    win._sharpmod_analysis_workspace = workspace
    win._sharpmod_analysis_workspace_dock = dock
    win._sharpmod_analysis_workspace_action = toggle
    return workspace


def refresh_analysis_workspace(win):
    """Cheap integration hook for the viewer's existing ``updateProfs`` path."""
    workspace = getattr(win, "_sharpmod_analysis_workspace", None)
    if workspace is not None:
        workspace.refresh()


__all__ = [
    "AnalysisWorkspace",
    "install_analysis_workspace",
    "refresh_analysis_workspace",
]
