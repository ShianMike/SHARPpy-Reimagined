"""Height-profile chart insets in the streamwiseness visual language.

The 0-6 km streamwiseness inset established a look for a native Qt chart that
sits in the bottom scientific band: a titled panel, a dashed grid, height in km
AGL up the left edge with a rotated axis label, the quantity along the bottom, a
translucent fill under the trace, and a small legend box in the top-right
corner. Three more charts are wanted in that same slot, so the parts of that
look which are not specific to streamwiseness live here as
:class:`HeightChartInset` and each chart supplies only its data and its series.

The charts implemented on it are:

* :class:`plotStormRelativeWind` -- storm-relative wind speed against height,
  using the same storm motion the streamwiseness chart uses, so the two never
  disagree about which vector "storm relative" means.
* :class:`plotThetaProfile` -- potential temperature against equivalent
  potential temperature. Their separation *is* the moisture, so the gap between
  the two traces is shaded rather than left for the eye to estimate.
* :class:`plotStepwiseCape` -- CAPE and CIN for a parcel lifted from each level
  in turn, which is the profile that shows where the unstable air actually is
  rather than reporting one parcel's value.

All three take a vertical scale that adapts to the sounding rather than a fixed
one, because a chart that clips its own trace is worse than a chart with an
unfamiliar axis.

Importing this module must not require a running ``QApplication``; only
instantiating a widget does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

import numpy as np
from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import colors
from sharpmod.viz.streamwiseness import (
    KTS_TO_MS,
    _finite_vector,
    _storm_motion,
    _wind_components,
)

__all__ = [
    "HeightChartInset",
    "HeightSeries",
    "plotStepwiseCape",
    "plotStormRelativeWind",
    "plotThetaProfile",
    "storm_relative_wind_profile",
    "stepwise_cape_profile",
    "theta_profile",
]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeightSeries:
    """One or more value series sampled on a shared height grid."""

    height_km: np.ndarray
    values: dict = field(default_factory=dict)

    def series(self, key):
        return self.values.get(key)

    @property
    def finite_span(self):
        """Return ``(min, max)`` across every series, or ``None`` if empty."""
        lows, highs = [], []
        for array in self.values.values():
            finite = np.asarray(array, dtype=float)
            finite = finite[np.isfinite(finite)]
            if finite.size:
                lows.append(float(finite.min()))
                highs.append(float(finite.max()))
        if not lows:
            return None
        return min(lows), max(highs)


# ---------------------------------------------------------------------------
# Height grid helper
# ---------------------------------------------------------------------------

def _agl_grid(prof, *, max_height_m, step_m):
    """Return ``(grid_m, height_agl_m, order_index)`` for interpolation.

    Shares the streamwiseness chart's conventions: heights are taken relative to
    the reported surface level, non-finite samples are dropped, duplicates are
    collapsed, and the grid stops at the sounding top rather than extrapolating
    past it.
    """
    height = _finite_vector(getattr(prof, "hght", None), minimum_size=2)
    if height is None:
        return None, None, None
    try:
        sfc = int(getattr(prof, "sfc", 0) or 0)
    except (TypeError, ValueError):
        sfc = 0
    if sfc < 0 or sfc >= height.size or not np.isfinite(height[sfc]):
        return None, None, None
    agl = height - height[sfc]
    valid = np.isfinite(agl) & (agl >= 0.0)
    if np.count_nonzero(valid) < 2:
        return None, None, None
    order = np.argsort(agl[valid], kind="stable")
    top = min(float(max_height_m), float(agl[valid][order][-1]))
    if top < step_m:
        return None, None, None
    grid = np.arange(0.0, np.floor(top / step_m) * step_m + step_m * 0.5,
                     step_m)
    if grid.size < 2:
        return None, None, None
    return grid, agl, valid


def _interp_to_grid(grid, agl, valid, source):
    """Interpolate ``source`` onto ``grid``, or return ``None``."""
    array = _finite_vector(source, minimum_size=2)
    if array is None or array.size != valid.size:
        return None
    usable = valid & np.isfinite(array)
    if np.count_nonzero(usable) < 2:
        return None
    heights = agl[usable]
    values = array[usable]
    order = np.argsort(heights, kind="stable")
    heights, values = heights[order], values[order]
    heights, unique = np.unique(heights, return_index=True)
    values = values[unique]
    if heights.size < 2:
        return None
    out = np.interp(grid, heights, values, left=np.nan, right=np.nan)
    out[grid > heights[-1]] = np.nan
    return out


# ---------------------------------------------------------------------------
# Data: storm-relative wind
# ---------------------------------------------------------------------------

def storm_relative_wind_profile(
        prof, *, use_left=False, max_height_m=12000.0, step_m=250.0):
    """Return storm-relative wind speed (kt) against height, or ``None``.

    The storm motion is the same vector the streamwiseness chart subtracts, so
    switching between the two charts cannot silently change the reference frame.
    """
    if prof is None or step_m <= 0 or max_height_m <= 0:
        return None
    grid, agl, valid = _agl_grid(
        prof, max_height_m=max_height_m, step_m=step_m)
    if grid is None:
        return None
    u, v = _wind_components(prof)
    if u is None or v is None:
        return None
    u_grid = _interp_to_grid(grid, agl, valid, u)
    v_grid = _interp_to_grid(grid, agl, valid, v)
    if u_grid is None or v_grid is None:
        return None
    motion = _storm_motion(prof, bool(use_left))
    if motion is None:
        return None
    storm_u, storm_v = motion
    u_sr = u_grid * KTS_TO_MS - storm_u
    v_sr = v_grid * KTS_TO_MS - storm_v
    speed_kt = np.hypot(u_sr, v_sr) / KTS_TO_MS
    if not np.any(np.isfinite(speed_kt)):
        return None
    return HeightSeries(height_km=grid / 1000.0, values={"srw": speed_kt})


# ---------------------------------------------------------------------------
# Data: theta / theta-e
# ---------------------------------------------------------------------------

def theta_profile(prof, *, max_height_m=12000.0, step_m=250.0):
    """Return potential and equivalent potential temperature (K) vs height."""
    if prof is None or step_m <= 0 or max_height_m <= 0:
        return None
    grid, agl, valid = _agl_grid(
        prof, max_height_m=max_height_m, step_m=step_m)
    if grid is None:
        return None
    theta = _profile_theta(prof)
    thetae = _profile_thetae(prof)
    values = {}
    if theta is not None:
        sampled = _interp_to_grid(grid, agl, valid, theta)
        if sampled is not None and np.any(np.isfinite(sampled)):
            values["theta"] = sampled
    if thetae is not None:
        sampled = _interp_to_grid(grid, agl, valid, thetae)
        if sampled is not None and np.any(np.isfinite(sampled)):
            values["thetae"] = sampled
    if not values:
        return None
    return HeightSeries(height_km=grid / 1000.0, values=values)


def _profile_theta(prof):
    """Return potential temperature (K), computing it only if not published."""
    published = getattr(prof, "theta", None)
    array = _finite_vector(published, minimum_size=2)
    if array is not None:
        return _as_kelvin(array)
    try:
        from sharppy.sharptab import thermo
    except Exception:  # noqa: BLE001 - the chart is optional, not the app
        return None
    pres = _finite_vector(getattr(prof, "pres", None), minimum_size=2)
    tmpc = _finite_vector(getattr(prof, "tmpc", None), minimum_size=2)
    if pres is None or tmpc is None or pres.size != tmpc.size:
        return None
    try:
        return _as_kelvin(np.asarray(thermo.theta(pres, tmpc), dtype=float))
    except Exception:  # noqa: BLE001
        _LOGGER.debug("height_charts.theta_failed", exc_info=True)
        return None


def _profile_thetae(prof):
    """Return equivalent potential temperature (K), or ``None``.

    ``thermo.thetae`` upstream takes scalars only -- handed arrays it fails deep
    inside with ``'float' object is not subscriptable`` -- so it is applied level
    by level. The sounding has tens of levels, not thousands, so the loop costs
    nothing worth optimizing away.
    """
    published = getattr(prof, "thetae", None)
    array = _finite_vector(published, minimum_size=2)
    if array is not None:
        return _as_kelvin(array)
    try:
        from sharppy.sharptab import thermo
    except Exception:  # noqa: BLE001
        return None
    pres = _finite_vector(getattr(prof, "pres", None), minimum_size=2)
    tmpc = _finite_vector(getattr(prof, "tmpc", None), minimum_size=2)
    dwpc = _finite_vector(getattr(prof, "dwpc", None), minimum_size=2)
    if pres is None or tmpc is None or dwpc is None:
        return None
    if not (pres.size == tmpc.size == dwpc.size):
        return None
    out = np.full(pres.shape, np.nan, dtype=float)
    for index in range(pres.size):
        p, t, d = pres[index], tmpc[index], dwpc[index]
        if not np.all(np.isfinite((p, t, d))):
            continue
        try:
            out[index] = float(thermo.thetae(float(p), float(t), float(d)))
        except Exception:  # noqa: BLE001 - one bad level is not a failed chart
            continue
    if not np.any(np.isfinite(out)):
        _LOGGER.debug("height_charts.thetae_unavailable")
        return None
    return _as_kelvin(out)


def _parcel_oracle(prof):
    """Return a parcel-capable upstream Profile for ``prof``, or ``None``.

    Falls back to ``prof`` itself when it already carries virtual temperature,
    which is the case when the caller handed us an upstream Profile directly.
    """
    if getattr(prof, "vtmp", None) is not None:
        return prof
    try:
        from sharpmod.sharptab.derived import _convective_oracle_profile
    except Exception:  # noqa: BLE001
        return None
    try:
        return _convective_oracle_profile(prof)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("height_charts.oracle_failed", exc_info=True)
        return None


def _as_kelvin(array):
    """Return ``array`` in kelvin, accepting a Celsius-scaled input.

    Potential temperature is conventionally kelvin, but some decoders publish it
    in Celsius. A sounding whose values sit near 300 is already kelvin; one near
    30 is not, and plotting the two on one axis would be meaningless.
    """
    finite = array[np.isfinite(array)]
    if finite.size and float(np.nanmedian(finite)) < 150.0:
        return array + 273.15
    return array


# ---------------------------------------------------------------------------
# Data: stepwise CAPE / CIN
# ---------------------------------------------------------------------------

def stepwise_cape_profile(
        prof, *, max_height_m=4000.0, step_m=250.0):
    """Return CAPE and CIN (J/kg) for a parcel lifted from each level.

    One parcel ascent per level is expensive, so the grid is deliberately coarse
    and shallow: parcels originating above roughly 4 km are not what this chart
    is read for, and a finer grid bought detail nobody uses at several times the
    cost.
    """
    if prof is None or step_m <= 0 or max_height_m <= 0:
        return None
    try:
        from sharppy.sharptab import params
    except Exception:  # noqa: BLE001
        return None

    # ``parcelx`` needs virtual temperature, which this project's Profile does
    # not publish, so lifting against it fails on every level with a bare
    # ``AttributeError: 'Profile' object has no attribute 'vtmp'``. The derived
    # module already builds and caches an upstream ConvectiveProfile for exactly
    # this reason; reuse it rather than opening a second parcel path that could
    # disagree with the parcel values shown elsewhere in the window.
    oracle = _parcel_oracle(prof)
    if oracle is None:
        return None

    grid, agl, valid = _agl_grid(
        prof, max_height_m=max_height_m, step_m=step_m)
    if grid is None:
        return None
    pres = _interp_to_grid(grid, agl, valid, getattr(prof, "pres", None))
    tmpc = _interp_to_grid(grid, agl, valid, getattr(prof, "tmpc", None))
    dwpc = _interp_to_grid(grid, agl, valid, getattr(prof, "dwpc", None))
    if pres is None or tmpc is None or dwpc is None:
        return None

    cape = np.full(grid.shape, np.nan, dtype=float)
    cin = np.full(grid.shape, np.nan, dtype=float)
    for index in range(grid.size):
        p, t, d = pres[index], tmpc[index], dwpc[index]
        if not np.all(np.isfinite((p, t, d))):
            continue
        try:
            parcel = params.parcelx(
                oracle, pres=float(p), tmpc=float(t), dwpc=float(d))
        except Exception:  # noqa: BLE001 - one bad level must not kill the chart
            continue
        bplus, bminus = getattr(parcel, "bplus", None), \
            getattr(parcel, "bminus", None)
        if bplus is not None and np.isfinite(float(bplus)):
            cape[index] = float(bplus)
        if bminus is not None and np.isfinite(float(bminus)):
            cin[index] = float(bminus)
    if not np.any(np.isfinite(cape)) and not np.any(np.isfinite(cin)):
        return None
    return HeightSeries(
        height_km=grid / 1000.0, values={"cape": cape, "cin": cin})


# ---------------------------------------------------------------------------
# Shared chart scaffolding
# ---------------------------------------------------------------------------

def _nice_step(span, target=5):
    """Return a human-readable tick interval covering ``span`` in ~``target``."""
    if not np.isfinite(span) or span <= 0:
        return 1.0
    raw = span / max(1, int(target))
    magnitude = 10.0 ** np.floor(np.log10(raw))
    for multiple in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= magnitude * multiple:
            return magnitude * multiple
    return magnitude * 10.0


class HeightChartInset(QtWidgets.QFrame):
    """Base for a value-versus-height chart drawn in the streamwiseness style.

    A subclass supplies :attr:`TITLE`, :attr:`X_LABEL`, :attr:`SERIES`, and a
    :meth:`_compute` that returns a :class:`HeightSeries`. Everything else --
    palette, geometry, grid, axis labels, legend, fill, and the SHARPpy widget
    contract -- is handled here so the charts cannot drift apart visually.
    """

    TITLE = ""
    X_LABEL = ""
    Y_LABEL = "Height AGL (km)"
    MAX_HEIGHT_KM = 12.0
    RIGHT_INSET = 25
    #: ``((key, legend label, palette role), ...)`` in draw order.
    SERIES: tuple = ()
    #: Draw a translucent band between these two series, when both exist.
    FILL_BETWEEN: tuple = ()
    #: Series keys to shade from the zero line outward, in draw order.
    FILL_SIGNED: tuple = ()
    #: Fill opacity. Deliberately a tint: a fill that reaches most of the way
    #: across the panel stops reading as a shape and starts reading as a
    #: background, which is what made the first storm-relative wind draft look
    #: like an olive block with a line on it.
    FILL_ALPHA = 34
    #: Force an axis floor/ceiling; ``None`` autoscales from the data.
    X_MIN = None
    X_MAX = None
    #: Draw an emphasized vertical rule at zero.
    ZERO_LINE = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prof = None
        self.data = None
        self.use_left = False
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)
        self._apply_palette()
        self._legend_rect = QtCore.QRectF()
        self._bounds = (0.0, 1.0)
        self.setObjectName("sharpmod_%s" % type(self).__name__.lower())
        self.setMinimumSize(150, 220)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.plotBitMap = QtGui.QPixmap(
            max(1, self.width()), max(1, self.height()))
        self._redraw()

    # -- SHARPpy widget contract ---------------------------------------- #
    def _compute(self, prof):
        raise NotImplementedError

    def setProf(self, prof):
        self.prof = prof
        self.data = self._safe_compute(prof)
        self._redraw()
        self.update()

    def setDeviant(self, deviant):
        self.use_left = deviant == "left"
        self.data = self._safe_compute(self.prof)
        self._redraw()
        self.update()

    def setPreferences(self, update_gui=True, **prefs):
        if "bg_color" in prefs:
            self.bg_color = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg_color = QtGui.QColor(prefs["fg_color"])
        self._apply_palette()
        if update_gui:
            self._redraw()
            self.update()

    def clearData(self):
        self.plotBitMap = QtGui.QPixmap(
            max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)

    def plotData(self):
        self._redraw()
        self.update()

    def _safe_compute(self, prof):
        """Compute chart data, treating any failure as "no data to draw".

        A chart is an inset on a scientific window; one that raises would take
        the whole sounding down with it, so an unexpected failure degrades to
        the same "--" state as genuinely absent data and is logged.
        """
        if prof is None:
            return None
        try:
            return self._compute(prof)
        except Exception:  # noqa: BLE001 - see docstring
            _LOGGER.debug(
                "height_charts.compute_failed chart=%s",
                type(self).__name__, exc_info=True)
            return None

    # -- palette --------------------------------------------------------- #
    def _apply_palette(self):
        palette = colors.semantic_palette(
            self.bg_color.name(), self.fg_color.name())
        self._palette = palette
        self.text_color = QtGui.QColor(palette["neutral"])
        self.border_color = QtGui.QColor(palette["border"])
        self.grid_color = QtGui.QColor(palette["grid"])
        self.setStyleSheet(
            "QFrame { background-color: %s; border: 0px; margin: 0px; }"
            % self.bg_color.name())

    def _series_color(self, role):
        return QtGui.QColor(self._palette[role])

    # -- geometry and paint ---------------------------------------------- #
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._redraw()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self.plotBitMap)
        painter.end()

    def _geometry(self):
        width = max(1, self.width())
        height = max(1, self.height())
        left = max(27, int(width * 0.14))
        right = width - self.RIGHT_INSET
        top = max(22, int(height * 0.07))
        bottom = height - max(30, int(height * 0.09))
        if right <= left:
            right = left + 1
        if bottom <= top:
            bottom = top + 1
        return QtCore.QRectF(left, top, right - left, bottom - top)

    def _x_to_pix(self, plot, value):
        low, high = self._bounds
        if high <= low:
            return plot.left()
        fraction = (float(value) - low) / (high - low)
        return plot.left() + np.clip(fraction, 0.0, 1.0) * plot.width()

    def _y_to_pix(self, plot, height_km):
        fraction = np.clip(
            float(height_km), 0.0, self.MAX_HEIGHT_KM) / self.MAX_HEIGHT_KM
        return plot.bottom() - fraction * plot.height()

    def _font(self, pixel_size, *, bold=False):
        font = QtGui.QFont("Helvetica")
        font.setPixelSize(max(6, int(pixel_size)))
        font.setBold(bool(bold))
        font.setStyleStrategy(
            QtGui.QFont.StyleStrategy.PreferAntialias
            | QtGui.QFont.StyleStrategy.PreferQuality
        )
        return font

    def _draw_text(self, painter, rect, text, color=None,
                   align=QtCore.Qt.AlignmentFlag.AlignCenter):
        painter.setPen(QtGui.QPen(
            self.text_color if color is None else color))
        painter.drawText(rect, int(align), str(text))

    # -- axis scaling ---------------------------------------------------- #
    def _resolve_bounds(self):
        """Return the x-axis range, adapting to the data unless pinned."""
        low, high = self.X_MIN, self.X_MAX
        span = self.data.finite_span if self.data is not None else None
        if span is not None:
            data_low, data_high = span
            if low is None:
                low = data_low
            if high is None:
                high = data_high
        if low is None or high is None or not np.isfinite((low, high)).all():
            return 0.0, 1.0
        if high <= low:
            high = low + 1.0
        step = _nice_step(high - low)
        low = float(np.floor(low / step) * step)
        high = float(np.ceil(high / step) * step)
        if self.X_MIN is not None:
            low = float(self.X_MIN)
        if self.X_MAX is not None:
            high = float(self.X_MAX)
        if high <= low:
            high = low + step
        return low, high

    def _x_ticks(self):
        low, high = self._bounds
        step = _nice_step(high - low)
        first = np.ceil(low / step) * step
        ticks = np.arange(first, high + step * 0.5, step)
        return [float(tick) for tick in ticks]

    @staticmethod
    def _format_tick(value):
        magnitude = abs(value)
        if magnitude >= 1000:
            trimmed = value / 1000.0
            return f"{trimmed:.0f}K" if trimmed == int(trimmed) \
                else f"{trimmed:.1f}K"
        if magnitude >= 10 or value == int(value):
            return f"{value:.0f}"
        return f"{value:.1f}"

    # -- drawing --------------------------------------------------------- #
    def _redraw(self):
        self.clearData()
        self._bounds = self._resolve_bounds()
        painter = QtGui.QPainter(self.plotBitMap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        plot = self._geometry()
        height = max(1, self.height())

        title_size = max(8, min(11, round(height * 0.027)))
        axis_size = max(7, min(9, round(height * 0.022)))
        tiny_size = max(6, min(8, round(height * 0.019)))

        painter.setFont(self._font(title_size, bold=True))
        self._draw_text(
            painter,
            QtCore.QRectF(plot.left(), 2, plot.width(), plot.top() - 3),
            self.TITLE,
        )

        self._draw_grid(painter, plot, axis_size)
        if self.data is None:
            painter.setFont(self._font(max(12, title_size + 2), bold=True))
            self._draw_text(painter, plot, "--", self.text_color)
        else:
            self._draw_fill(painter, plot)
            self._draw_series(painter, plot)
            if len(self.SERIES) > 1:
                self._draw_legend(painter, plot, tiny_size)

        painter.setFont(self._font(axis_size, bold=True))
        self._draw_text(
            painter,
            QtCore.QRectF(plot.left(), plot.bottom() + 13, plot.width(),
                          max(10, self.height() - plot.bottom() - 13)),
            self.X_LABEL,
            self.text_color,
        )
        painter.save()
        painter.translate(8, plot.center().y())
        painter.rotate(-90)
        self._draw_text(
            painter,
            QtCore.QRectF(-plot.height() / 2.0, -7, plot.height(), 14),
            self.Y_LABEL,
            self.text_color,
        )
        painter.restore()

        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(self.border_color, 1))
        painter.drawLine(QtCore.QLineF(
            0.5, 0.5, 0.5, max(0.5, self.height() - 0.5)))
        painter.end()

    def _draw_grid(self, painter, plot, font_size):
        grid = QtGui.QColor(self.grid_color)
        grid.setAlpha(130)
        pen = QtGui.QPen(grid, 1, QtCore.Qt.PenStyle.DashLine)
        painter.setFont(self._font(font_size))

        for tick in self._x_ticks():
            x = self._x_to_pix(plot, tick)
            emphasize = self.ZERO_LINE and abs(tick) < 1e-9
            painter.setPen(
                QtGui.QPen(self.text_color, 1) if emphasize else pen)
            painter.drawLine(QtCore.QPointF(x, plot.top()),
                             QtCore.QPointF(x, plot.bottom()))
            self._draw_text(
                painter,
                QtCore.QRectF(x - 18, plot.bottom() + 1, 36, 12),
                self._format_tick(tick),
                self.text_color,
            )

        step_km = _nice_step(self.MAX_HEIGHT_KM, target=6)
        tick = 0.0
        while tick <= self.MAX_HEIGHT_KM + 1e-9:
            y = self._y_to_pix(plot, tick)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(plot.left(), y),
                             QtCore.QPointF(plot.right(), y))
            self._draw_text(
                painter,
                QtCore.QRectF(11, y - 6, max(14, plot.left() - 13), 12),
                self._format_tick(tick),
                self.text_color,
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
            )
            tick += step_km

    def _draw_fill(self, painter, plot):
        """Shade the band that carries this chart's meaning, if it has one."""
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for key in self.FILL_SIGNED:
            self._fill_signed(painter, plot, key)
        if len(self.FILL_BETWEEN) == 2:
            self._fill_between(painter, plot, *self.FILL_BETWEEN)

    def _fill_signed(self, painter, plot, key):
        values = self.data.series(key)
        if values is None:
            return
        role = next((r for k, _l, r in self.SERIES if k == key), "profile")
        base = self._x_to_pix(plot, 0.0)
        heights = self.data.height_km
        for index in range(len(heights) - 1):
            v0, v1 = values[index], values[index + 1]
            if not np.all(np.isfinite((v0, v1))):
                continue
            color = self._series_color(role)
            color.setAlpha(self.FILL_ALPHA)
            painter.setBrush(QtGui.QBrush(color))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(base, self._y_to_pix(plot, heights[index])),
                QtCore.QPointF(self._x_to_pix(plot, v0),
                               self._y_to_pix(plot, heights[index])),
                QtCore.QPointF(self._x_to_pix(plot, v1),
                               self._y_to_pix(plot, heights[index + 1])),
                QtCore.QPointF(base, self._y_to_pix(plot, heights[index + 1])),
            ]))

    def _fill_between(self, painter, plot, low_key, high_key):
        low = self.data.series(low_key)
        high = self.data.series(high_key)
        if low is None or high is None:
            return
        heights = self.data.height_km
        color = self._series_color("cyan")
        color.setAlpha(46)
        painter.setBrush(QtGui.QBrush(color))
        for index in range(len(heights) - 1):
            a0, a1 = low[index], low[index + 1]
            b0, b1 = high[index], high[index + 1]
            if not np.all(np.isfinite((a0, a1, b0, b1))):
                continue
            y0 = self._y_to_pix(plot, heights[index])
            y1 = self._y_to_pix(plot, heights[index + 1])
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(self._x_to_pix(plot, a0), y0),
                QtCore.QPointF(self._x_to_pix(plot, b0), y0),
                QtCore.QPointF(self._x_to_pix(plot, b1), y1),
                QtCore.QPointF(self._x_to_pix(plot, a1), y1),
            ]))

    def _draw_series(self, painter, plot):
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        for key, _label, role in self.SERIES:
            values = self.data.series(key)
            if values is None:
                continue
            painter.setPen(QtGui.QPen(self._series_color(role), 2))
            path = QtGui.QPainterPath()
            active = False
            for value, height_km in zip(values, self.data.height_km):
                if not np.isfinite(value) or height_km > self.MAX_HEIGHT_KM:
                    active = False
                    continue
                point = QtCore.QPointF(self._x_to_pix(plot, value),
                                       self._y_to_pix(plot, height_km))
                if active:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    active = True
            painter.drawPath(path)

    def _draw_legend(self, painter, plot, font_size):
        entries = [(label, self._series_color(role))
                   for key, label, role in self.SERIES
                   if self.data.series(key) is not None and label]
        if len(entries) < 2:
            return
        painter.setFont(self._font(font_size))
        metrics = QtGui.QFontMetrics(painter.font())
        row_h = max(9, metrics.height())
        width = min(plot.width() - 4, max(
            metrics.horizontalAdvance(label) + 18 for label, _ in entries) + 4)
        height = row_h * len(entries) + 4
        left = plot.right() - width - 2
        top = plot.top() + 2
        self._legend_rect = QtCore.QRectF(left, top, width, height)
        background = QtGui.QColor(self.bg_color)
        background.setAlpha(220)
        painter.setPen(QtGui.QPen(QtGui.QColor(colors.resolve_theme_color(
            "#555b62", self.bg_color.name(), self.fg_color.name())), 1))
        painter.setBrush(QtGui.QBrush(background))
        painter.drawRect(self._legend_rect)
        for row, (label, color) in enumerate(entries):
            y = top + 2 + row * row_h
            painter.setPen(QtGui.QPen(color, 2))
            painter.drawLine(QtCore.QPointF(left + 3, y + row_h / 2.0),
                             QtCore.QPointF(left + 13, y + row_h / 2.0))
            self._draw_text(
                painter,
                QtCore.QRectF(left + 16, y, width - 18, row_h),
                label,
                self.text_color,
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
            )


# ---------------------------------------------------------------------------
# The charts
# ---------------------------------------------------------------------------

class plotStormRelativeWind(HeightChartInset):  # noqa: N801 - upstream style
    """Storm-relative wind speed against height AGL."""

    TITLE = "Storm-Relative Wind"
    X_LABEL = "SR Wind (kt)"
    SERIES = (("srw", "SR wind", "marker_orange"),)
    #: No fill. Storm-relative speed never approaches the axis floor, so
    #: shading from zero tinted most of the panel without conveying anything --
    #: it just meant "left of the line". The trace alone carries the profile.
    X_MIN = 0.0

    def _compute(self, prof):
        return storm_relative_wind_profile(prof, use_left=self.use_left)


class plotThetaProfile(HeightChartInset):  # noqa: N801 - upstream style
    """Potential temperature and equivalent potential temperature vs height."""

    TITLE = "\u03b8 / \u03b8e Profile"
    X_LABEL = "\u03b8 / \u03b8e (K)"
    SERIES = (
        ("theta", "\u03b8", "marker_orange"),
        ("thetae", "\u03b8e", "cyan"),
    )
    #: The gap between the two curves is the moisture, so shade it.
    FILL_BETWEEN = ("theta", "thetae")

    def _compute(self, prof):
        return theta_profile(prof)


class plotStepwiseCape(HeightChartInset):  # noqa: N801 - upstream style
    """CAPE and CIN for a parcel lifted from each level in turn."""

    TITLE = "Stepwise CIN & CAPE"
    X_LABEL = "CIN / CAPE (J/kg)"
    MAX_HEIGHT_KM = 4.0
    SERIES = (
        ("cin", "CIN", "cyan"),
        ("cape", "CAPE", "marker_orange"),
    )
    FILL_SIGNED = ("cin", "cape")
    ZERO_LINE = True

    def _compute(self, prof):
        return stepwise_cape_profile(prof)


# ---------------------------------------------------------------------------
# The switchable slot
# ---------------------------------------------------------------------------

#: The charts offered in the swappable slot, in menu order. Streamwiseness is
#: first because it is what the slot showed before it became switchable, so the
#: default view does not change for anyone who never opens the menu.
def _chart_catalogue():
    """Return ``((key, label, factory), ...)`` for the switchable slot.

    Built lazily so importing this module never pulls in the streamwiseness
    widget, which keeps the import side of the two modules one-directional.
    """
    from sharpmod.viz.streamwiseness import plotStreamwiseness

    return (
        ("streamwiseness", "Streamwiseness", plotStreamwiseness),
        ("srw", "Storm-Relative Wind", plotStormRelativeWind),
        ("theta", "\u03b8 / \u03b8e Profile", plotThetaProfile),
        ("cape", "Stepwise CIN && CAPE", plotStepwiseCape),
    )


class SwappableHeightChart(QtWidgets.QFrame):
    """Hosts the height charts and shows one, chosen from a context menu.

    The slot previously held the streamwiseness chart alone. Rather than
    replacing that chart with a different one, this frame keeps every chart
    constructed and simply raises the selected one, so switching is immediate and
    the profile does not have to be recomputed each time the user looks at
    something else. Each chart is only *populated* when it is first shown, which
    matters because the stepwise CAPE chart lifts a parcel per level and nobody
    should pay for that unless they ask to see it.

    The whole SHARPpy inset contract is forwarded, so the surrounding window can
    keep treating this as the single widget it used to be.
    """

    DEFAULT_CHART = "streamwiseness"

    #: Emitted with the chart key whenever the visible chart changes.
    chartChanged = QtCore.Signal(str)

    def __init__(self, parent=None, *, chart=None):
        super().__init__(parent)
        self.setObjectName("sharpmod_swappable_height_chart")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self._prof = None
        self._deviant = "right"
        self._prefs = {}
        self._populated = set()
        # The slot is a real painted surface as far as the theme controller is
        # concerned: it inspects every mounted widget for this palette pair to
        # verify a light/dark switch reached everything. Holding it here also
        # keeps the frame behind the charts from flashing the old background
        # during a theme change.
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)
        self._apply_palette()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._stack = QtWidgets.QStackedWidget(self)
        layout.addWidget(self._stack)

        self._charts = {}
        self._order = []
        for key, label, factory in _chart_catalogue():
            try:
                widget = factory()
            except Exception:  # noqa: BLE001 - one bad chart must not break the slot
                _LOGGER.debug(
                    "height_charts.chart_unavailable key=%s", key, exc_info=True)
                continue
            self._charts[key] = (label, widget)
            self._order.append(key)
            self._stack.addWidget(widget)

        self._current = None
        self.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        requested = chart if chart in self._charts else self.DEFAULT_CHART
        self.setChart(requested if requested in self._charts
                      else (self._order[0] if self._order else None))

    # -- selection ------------------------------------------------------- #
    def availableCharts(self):
        """Return ``((key, label), ...)`` for every chart the slot can show."""
        return tuple((key, self._charts[key][0]) for key in self._order)

    def currentChart(self):
        return self._current

    def setChart(self, key):
        """Show the chart named ``key``, populating it on first display."""
        if key is None or key not in self._charts:
            return False
        _label, widget = self._charts[key]
        if key not in self._populated:
            self._apply_state(widget)
            self._populated.add(key)
        self._stack.setCurrentWidget(widget)
        if key != self._current:
            self._current = key
            self.chartChanged.emit(key)
        return True

    def _show_menu(self, position):
        menu = QtWidgets.QMenu(self)
        group = QtGui.QActionGroup(menu)
        group.setExclusive(True)
        for key in self._order:
            label, _widget = self._charts[key]
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self._current)
            action.triggered.connect(
                lambda _checked=False, chart=key: self.setChart(chart))
            group.addAction(action)
        menu.exec(self.mapToGlobal(position))

    # -- forwarded inset contract ---------------------------------------- #
    def _apply_state(self, widget):
        """Push the current profile, deviant, and preferences into one chart."""
        if self._prefs:
            try:
                widget.setPreferences(update_gui=False, **self._prefs)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("height_charts.prefs_failed", exc_info=True)
        try:
            widget.setDeviant(self._deviant)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("height_charts.deviant_failed", exc_info=True)
        if self._prof is not None:
            try:
                widget.setProf(self._prof)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("height_charts.setprof_failed", exc_info=True)

    def _invalidate(self):
        """Drop populated state so charts refresh lazily on next display."""
        self._populated.clear()
        current = self._current
        if current is not None and current in self._charts:
            self._apply_state(self._charts[current][1])
            self._populated.add(current)

    def setProf(self, prof):
        self._prof = prof
        self._invalidate()

    def setDeviant(self, deviant):
        self._deviant = deviant
        self._invalidate()

    def _apply_palette(self):
        """Derive the same semantic colours the charts use.

        The theme controller walks every mounted surface and reads these to
        confirm a light/dark switch actually landed, so the slot resolves the
        palette itself rather than only passing preferences through.
        """
        palette = colors.semantic_palette(
            self.bg_color.name(), self.fg_color.name())
        self.text_color = QtGui.QColor(palette["neutral"])
        self.border_color = QtGui.QColor(palette["border"])
        self.grid_color = QtGui.QColor(palette["grid"])
        self.setStyleSheet(
            "QFrame#sharpmod_swappable_height_chart { background-color: %s; "
            "border: 0px; margin: 0px; }" % self.bg_color.name())

    def setPreferences(self, update_gui=True, **prefs):
        self._prefs.update(prefs)
        if "bg_color" in prefs:
            self.bg_color = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg_color = QtGui.QColor(prefs["fg_color"])
        self._apply_palette()
        # Preferences are cheap and affect the frame's own background, so every
        # chart takes them immediately rather than lazily.
        for _label, widget in self._charts.values():
            try:
                widget.setPreferences(update_gui=update_gui, **prefs)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("height_charts.prefs_failed", exc_info=True)

    def plotData(self):
        current = self._current
        if current is not None and current in self._charts:
            self._charts[current][1].plotData()

    def clearData(self):
        for _label, widget in self._charts.values():
            try:
                widget.clearData()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("height_charts.cleardata_failed", exc_info=True)

    # -- convenience for the mounting code -------------------------------- #
    @property
    def chart(self):
        """The visible chart widget, for callers that need the real inset."""
        current = self._current
        if current is None:
            return None
        return self._charts[current][1]

    @property
    def data(self):
        """The visible chart's data, so the slot reads like a single inset.

        Callers that used to hold the streamwiseness chart directly check this
        to decide whether anything was plotted, and that has to keep working
        without them knowing a stack appeared underneath.
        """
        chart = self.chart
        return None if chart is None else getattr(chart, "data", None)

    @property
    def use_left(self):
        """The deviant currently applied to every chart in the slot."""
        return self._deviant == "left"
