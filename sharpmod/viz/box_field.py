"""Colour scales and painters for box-sounding fields and cross-sections.

Two things are drawn from a :class:`~sharpmod.box_analysis.BoxAnalysis`:

*A parameter field*, painted as one filled cell per sampled grid point. Cells
are centred on their node and sized to the sample spacing, so what is on screen
is exactly the set of columns that were extracted -- nothing is smoothed or
interpolated into a resolution the model never had. The map widget supplies the
projection, so the field lands in the same geography as the coastlines.

*An ingredient-screen overlay*, hatched over the cells where every threshold of a
mode holds at once, in the same visual grammar as the outlook overlay so the
field underneath stays readable.

A pressure-versus-distance cross-section and a per-level spread band used to live
here too. Both were vertical plots on a plain linear axis, which read as broken
beside the application's own Skew-T, and neither answered a question the field
map and the averaged sounding do not answer better.
:meth:`~sharpmod.box_analysis.BoxAnalysis.vertical_transect` and
:meth:`~sharpmod.box_analysis.BoxAnalysis.envelope` still return the numbers for
a caller that wants to plot them its own way.

The colour ramp is deliberately the conventional meteorological cool-to-hot
sequence. It is not a theme token: like radar reflectivity, a field ramp encodes
magnitude and must keep the same reading under every chrome theme.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QBrush, QColor, QPainter, QPen

from sharpmod.box_analysis import BoxAnalysis, BoxFieldStats, parameter


__all__ = [
    "DIVERGING_STOPS",
    "MASK_COLOR",
    "MASK_EDGE_COLOR",
    "SEQUENTIAL_STOPS",
    "FieldScale",
    "draw_cell_values",
    "draw_color_bar",
    "draw_field_cells",
    "draw_mask_overlay",
    "ramp_color",
    "scale_for",
]


#: Cool-to-hot sequential ramp, ``(position, colour)`` from low to high.
SEQUENTIAL_STOPS: tuple[tuple[float, str], ...] = (
    (0.00, "#2B3A8F"),
    (0.20, "#1E88C7"),
    (0.40, "#3FB878"),
    (0.60, "#E8D44D"),
    (0.80, "#E8873A"),
    (1.00, "#C8353A"),
)

#: Blue-white-red ramp for fields that genuinely straddle zero, such as omega
#: or a signed helicity. Using the sequential ramp for these would hide the sign
#: change, which is usually the most important thing in the field.
DIVERGING_STOPS: tuple[tuple[float, str], ...] = (
    (0.00, "#2166AC"),
    (0.25, "#67A9CF"),
    (0.50, "#F2F0EC"),
    (0.75, "#EF8A62"),
    (1.00, "#B2182B"),
)

#: Text drawn over filled cells needs a fixed pair rather than a theme colour:
#: the cell beneath it is a data colour, not the chrome background.
_ON_FILL_TEXT = "#0B0B0A"
_ON_FILL_SHADOW = "#FFFFFF"


def ramp_color(fraction, stops=SEQUENTIAL_STOPS) -> QColor:
    """Return the ramp colour at ``fraction`` in ``[0, 1]``."""
    try:
        position = float(fraction)
    except (TypeError, ValueError):
        position = 0.0
    if not math.isfinite(position):
        position = 0.0
    position = max(0.0, min(1.0, position))
    previous_stop, previous_color = stops[0]
    for stop, color in stops:
        if position <= stop:
            if stop <= previous_stop:
                return QColor(color)
            span = stop - previous_stop
            weight = (position - previous_stop) / span
            start = QColor(previous_color)
            end = QColor(color)
            return QColor(
                round(start.red() + (end.red() - start.red()) * weight),
                round(start.green() + (end.green() - start.green()) * weight),
                round(start.blue() + (end.blue() - start.blue()) * weight),
            )
        previous_stop, previous_color = stop, color
    return QColor(stops[-1][1])


@dataclass(frozen=True)
class FieldScale:
    """A value range mapped onto a colour ramp."""

    minimum: float
    maximum: float
    stops: tuple[tuple[float, str], ...] = SEQUENTIAL_STOPS

    @property
    def span(self) -> float:
        return self.maximum - self.minimum

    @property
    def flat(self) -> bool:
        """Whether the field has no range to colour.

        A uniform field is worth saying so about: colouring it would invent a
        gradient that is not in the data.
        """
        return self.span <= 0.0

    def normalize(self, value) -> float | None:
        """Return ``value``'s position in ``[0, 1]``, or ``None`` if absent."""
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if self.flat:
            return 0.5
        return max(0.0, min(1.0, (number - self.minimum) / self.span))

    def color(self, value, *, alpha=255) -> QColor | None:
        """Return the colour for ``value``, or ``None`` when it is absent."""
        fraction = self.normalize(value)
        if fraction is None:
            return None
        color = ramp_color(fraction, self.stops)
        color.setAlpha(int(alpha))
        return color


def scale_for(stats: BoxFieldStats, *, robust=True) -> FieldScale:
    """Build a colour scale from a field's area statistics.

    ``robust`` clips to the 10th-90th percentile band so one extreme cell cannot
    flatten the rest of the box into a single colour, which is the usual failure
    of auto-scaled fields. The full range is used when that band is degenerate.
    """
    if not isinstance(stats, BoxFieldStats):
        raise TypeError("stats must be a BoxFieldStats")
    low, high = float(stats.minimum), float(stats.maximum)
    if robust and stats.p90 > stats.p10:
        low, high = float(stats.p10), float(stats.p90)
    stops = SEQUENTIAL_STOPS
    if low < 0.0 < high:
        # Straddles zero, so put the neutral tone on zero and make the ramp
        # symmetric; otherwise the colour of "zero" would drift with the data.
        limit = max(abs(low), abs(high))
        low, high = -limit, limit
        stops = DIVERGING_STOPS
    return FieldScale(minimum=low, maximum=high, stops=stops)


def _cell_steps(analysis: BoxAnalysis) -> tuple[float, float]:
    """Return the ``(dlat, dlon)`` extent of one sample cell, in degrees."""
    region = analysis.plan.region
    dlat = (
        region.lat_span / float(analysis.rows - 1)
        if analysis.rows > 1 else region.lat_span
    )
    dlon = (
        region.lon_span / float(analysis.cols - 1)
        if analysis.cols > 1 else region.lon_span
    )
    return dlat, dlon


def draw_field_cells(
    qp: QPainter,
    to_px,
    analysis: BoxAnalysis,
    key,
    scale: FieldScale,
    *,
    alpha=170,
    outline=False,
) -> int:
    """Fill one cell per sampled node and return how many were drawn.

    ``to_px`` maps ``(lon, lat)`` to a ``QPointF``, so the caller's projection
    decides the geometry. Cells are centred on their node: the colour at a
    location is the value actually extracted there, not a blend of neighbours.
    """
    item = parameter(key)
    dlat, dlon = _cell_steps(analysis)
    half_lat = dlat / 2.0
    half_lon = dlon / 2.0
    drawn = 0
    qp.setPen(Qt.NoPen)
    for point in analysis.points:
        value = point.value(item.key)
        color = scale.color(value, alpha=alpha)
        if color is None:
            continue
        top_left = to_px(point.lon - half_lon, point.lat + half_lat)
        bottom_right = to_px(point.lon + half_lon, point.lat - half_lat)
        rect = QRectF(top_left, bottom_right).normalized()
        # Grow hairline cells by half a pixel so a coarse box does not leave
        # seams between neighbouring fills.
        if rect.width() < 1.0 or rect.height() < 1.0:
            rect = rect.adjusted(-0.5, -0.5, 0.5, 0.5)
        qp.setBrush(QBrush(color))
        qp.drawRect(rect)
        drawn += 1
    if outline and drawn:
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(0, 0, 0, 60), 0.6))
        for point in analysis.points:
            if scale.normalize(point.value(item.key)) is None:
                continue
            top_left = to_px(point.lon - half_lon, point.lat + half_lat)
            bottom_right = to_px(point.lon + half_lon, point.lat - half_lat)
            qp.drawRect(QRectF(top_left, bottom_right).normalized())
    return drawn


#: Colour of the ingredient-screen overlay. A single high-contrast neutral
#: rather than a hue: the cells beneath it already carry a data colour, and any
#: hue here would be read as another magnitude.
MASK_COLOR = "#FFFFFF"
MASK_EDGE_COLOR = "#101010"


def draw_mask_overlay(
    qp: QPainter,
    to_px,
    analysis: BoxAnalysis,
    mask,
    *,
    color=MASK_COLOR,
    edge_color=MASK_EDGE_COLOR,
) -> int:
    """Hatch the cells that satisfy a criteria set. Returns how many.

    Hatching, rather than a fill, is deliberate: the qualifying region has to be
    readable *without* hiding the parameter field underneath it, since the useful
    question is usually "how strong is it where the ingredients overlap". This is
    the same visual grammar the SPC outlook overlay on this map already uses for
    a qualifier area, so the two read as the same kind of statement.

    ``mask`` is the tri-state grid from :meth:`BoxAnalysis.mask`. Only ``True``
    cells are drawn; ``None`` (unjudgeable) is left bare rather than shaded,
    which would imply a verdict that was never reached.
    """
    from qtpy import QtCore, QtGui

    from sharpmod.overlay_hatch import hatch_brush

    dlat, dlon = _cell_steps(analysis)
    half_lat = dlat / 2.0
    half_lon = dlon / 2.0
    brush = hatch_brush(QtCore, QtGui, QColor(color), 2)
    drawn = 0
    for point in analysis.points:
        try:
            verdict = mask[point.row][point.col]
        except (IndexError, TypeError):
            continue
        if verdict is not True:
            continue
        top_left = to_px(point.lon - half_lon, point.lat + half_lat)
        bottom_right = to_px(point.lon + half_lon, point.lat - half_lat)
        rect = QRectF(top_left, bottom_right).normalized()
        qp.setPen(Qt.NoPen)
        qp.setBrush(brush)
        qp.drawRect(rect)
        drawn += 1
    if drawn:
        # A thin dark keyline under the hatch keeps the qualifying region's
        # boundary legible over a light cell as well as a dark one.
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(edge_color), 1.0))
        for point in analysis.points:
            try:
                verdict = mask[point.row][point.col]
            except (IndexError, TypeError):
                continue
            if verdict is not True:
                continue
            top_left = to_px(point.lon - half_lon, point.lat + half_lat)
            bottom_right = to_px(point.lon + half_lon, point.lat - half_lat)
            qp.drawRect(QRectF(top_left, bottom_right).normalized())
    return drawn


def draw_cell_values(
    qp: QPainter,
    to_px,
    analysis: BoxAnalysis,
    key,
    *,
    font=None,
    min_cell_px=34.0,
) -> int:
    """Label each cell with its value when the cells are large enough.

    Returns the number of labels drawn. Below ``min_cell_px`` the numbers would
    overlap into unreadable mush, so nothing is drawn and the colour carries the
    field on its own.
    """
    item = parameter(key)
    dlat, dlon = _cell_steps(analysis)
    if font is not None:
        qp.setFont(font)
    probe_a = to_px(0.0, 0.0)
    probe_b = to_px(dlon, dlat)
    if abs(probe_b.x() - probe_a.x()) < min_cell_px:
        return 0
    drawn = 0
    for point in analysis.points:
        value = point.value(item.key)
        if value is None:
            continue
        center = to_px(point.lon, point.lat)
        rect = QRectF(
            center.x() - 26.0, center.y() - 8.0, 52.0, 16.0)
        text = item.format(value)
        qp.setPen(QPen(QColor(_ON_FILL_SHADOW)))
        qp.drawText(rect.translated(0.7, 0.7), Qt.AlignCenter, text)
        qp.setPen(QPen(QColor(_ON_FILL_TEXT)))
        qp.drawText(rect, Qt.AlignCenter, text)
        drawn += 1
    return drawn


def draw_color_bar(
    qp: QPainter,
    rect: QRectF,
    scale: FieldScale,
    key,
    *,
    text_color="#F1EFEB",
    shadow_color="#000000",
    font=None,
) -> None:
    """Draw a horizontal colour bar with end labels and a title."""
    item = parameter(key)
    if font is not None:
        qp.setFont(font)
    bar = QRectF(
        rect.left(), rect.top() + 14.0, rect.width(), max(8.0, rect.height() - 28.0))
    steps = max(2, int(bar.width()))
    qp.setPen(Qt.NoPen)
    for step in range(steps):
        fraction = step / float(steps - 1)
        qp.setBrush(QBrush(ramp_color(fraction, scale.stops)))
        qp.drawRect(QRectF(
            bar.left() + fraction * (bar.width() - 1.0), bar.top(),
            2.0, bar.height()))
    qp.setBrush(Qt.NoBrush)
    qp.setPen(QPen(QColor(text_color), 1.0))
    qp.drawRect(bar)

    title = item.label if not item.units else f"{item.label} ({item.units})"
    low = item.format(scale.minimum)
    high = item.format(scale.maximum)
    if scale.flat:
        low = high = item.format(scale.minimum)
        title = f"{title} \u2014 uniform"

    def label(text, target, alignment):
        qp.setPen(QPen(QColor(shadow_color)))
        qp.drawText(target.translated(0.8, 0.8), alignment, text)
        qp.setPen(QPen(QColor(text_color)))
        qp.drawText(target, alignment, text)

    label(title, QRectF(rect.left(), rect.top() - 2.0, rect.width(), 14.0),
          Qt.AlignHCenter | Qt.AlignTop)
    below = QRectF(rect.left(), bar.bottom() + 1.0, rect.width(), 14.0)
    label(low, below, Qt.AlignLeft | Qt.AlignVCenter)
    label(high, below, Qt.AlignRight | Qt.AlignVCenter)
