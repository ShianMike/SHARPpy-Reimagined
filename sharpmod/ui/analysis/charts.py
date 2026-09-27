"""Presentation helpers and charts for the sounding analysis workspace.

The workspace controller owns jobs and tabs; this module owns chart geometry,
formatting, drawing, and the two chart widgets.
"""

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

_MISSING = "\u2014"
_THERMO_RANGE_C = (-50.0, 50.0)

#: Isobars a forecaster reads a profile against.
_STANDARD_ISOBARS = (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100)

#: Temperature gridlines keep the zero isotherm labelled.
_THERMO_STEP_C = 25.0

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
    return item.label, item.display_units, item.decimals


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


def _time_scope(values):
    times = sorted({_format_time(value) for value in values})
    return ", ".join(times) if len(times) <= 3 else f"{len(times)} exact times, {times[0]} to {times[-1]}"


def _comparison_basis_text(sample) -> tuple[str, str]:
    """Return a compact label and full provenance tooltip for one row."""
    context = _get(sample, "context")
    basis = _get(sample, "basis")
    relation = str(_get(basis, "location_relation", "unknown"))
    relation_label = {
        "same-location": "same location",
        "intentional-spatial": "spatial",
        "unknown": "location unknown",
    }.get(relation, relation.replace("-", " "))

    def shown(name, suffix=""):
        value = _get(context, name)
        return _MISSING if value is None else f"{value}{suffix}"

    lines = [
        f"Comparison basis: {relation_label}",
        (
            "Requested point: "
            f"{shown('requested_lat')}, {shown('requested_lon')}"
        ),
        f"Selected source point: {shown('selected_lat')}, {shown('selected_lon')}",
        f"Grid spacing: {shown('grid_spacing_km', ' km')}",
        f"Terrain: {shown('terrain_elevation_m', ' m MSL')}",
        f"Run / lead: {_format_time(_get(context, 'run_time'))} / {shown('lead_hours', ' h')}",
        f"Parcel convention: {shown('parcel_convention')}",
        f"Storm motion: {shown('storm_motion_convention')}",
        f"Modified profile: {shown('profile_edited')}",
    ]
    issues = tuple(_get(basis, "issues", ()) or ())
    if issues:
        lines.append("Compatibility:")
        lines.extend(
            f"• {str(_get(issue, 'severity', 'info')).upper()}: "
            f"{_get(issue, 'message', '')}"
            for issue in issues
        )
    return relation_label, "\n".join(lines)


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
    :mod:`sharpmod.ui.styles.theme` documents rejecting, so the panel read as belonging to
    a different application than the dock around it. Chrome roles come from the
    theme; the two thermodynamic traces come from
    :func:`sharpmod.viz.colors.semantic_palette` so temperature and dewpoint are the
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

    ``track`` is which stacked metric band the point belongs to. It trails with a
    default so a single-metric chart flattens to exactly the same tuples, in the
    same order, as before stacked tracks existed.
    """

    x: float
    y: float | None
    index: int
    series: int
    sample: int
    track: int = 0


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


from sharpmod.ui.analysis.trend_chart import _TrendChart  # noqa: E402
from sharpmod.ui.analysis.envelope_chart import _EnvelopeChart  # noqa: E402
