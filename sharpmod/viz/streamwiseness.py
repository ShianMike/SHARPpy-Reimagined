"""SHARPpy-style streamwiseness profile calculation and inset widget.

The calculation is intentionally profile/decoder agnostic.  It consumes the
standard SHARPpy height and wind-component arrays plus the shared Bunkers storm
motion, interpolates them onto a 100 m AGL grid, and resolves the fraction of
horizontal vorticity aligned with the storm-relative wind through 6 km.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.ma as ma

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.constants import is_missing
from sharpmod.sharptab import winds as sm_winds

__all__ = [
    "StreamwisenessData",
    "streamwiseness_profile",
    "plotStreamwiseness",
]


KTS_TO_MS = 0.5144444444444445


@dataclass(frozen=True)
class StreamwisenessData:
    """Streamwiseness samples on an evenly spaced height grid."""

    height_km: np.ndarray
    percent: np.ndarray
    signed_percent: np.ndarray


def _finite_vector(value, minimum_size=1):
    if value is None:
        return None
    try:
        array = ma.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.ndim != 1 or array.size < minimum_size:
        return None
    return np.asarray(array.filled(np.nan), dtype=float)


def _wind_components(prof):
    u = _finite_vector(getattr(prof, "u", None), minimum_size=2)
    v = _finite_vector(getattr(prof, "v", None), minimum_size=2)
    if u is not None and v is not None and u.size == v.size:
        return u, v

    wdir = _finite_vector(getattr(prof, "wdir", None), minimum_size=2)
    wspd = _finite_vector(getattr(prof, "wspd", None), minimum_size=2)
    if wdir is None or wspd is None or wdir.size != wspd.size:
        return None, None
    radians = np.deg2rad(wdir)
    return -wspd * np.sin(radians), -wspd * np.cos(radians)


def _storm_motion(prof, use_left):
    try:
        motion = tuple(sm_winds.storm_motion(prof))
    except Exception:
        return None
    indexes = (2, 3) if use_left else (0, 1)
    if len(motion) <= max(indexes):
        return None
    values = []
    for index in indexes:
        value = motion[index]
        if is_missing(value):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(value):
            return None
        values.append(value * KTS_TO_MS)
    return tuple(values)


def streamwiseness_profile(
        prof, *, use_left=False, max_height_m=6000.0, step_m=100.0):
    """Return the 0-6 km streamwiseness profile, or ``None`` when unavailable.

    Horizontal vorticity is the horizontal part of ``curl(V)`` for a wind that
    varies with height: ``(-dv/dz, du/dz)``.  Streamwiseness is the magnitude
    of its projection onto the storm-relative wind unit vector divided by the
    total horizontal-vorticity magnitude.  The sign is retained separately for
    cyclonic/anticyclonic shading.
    """
    if prof is None or step_m <= 0 or max_height_m <= 0:
        return None

    height = _finite_vector(getattr(prof, "hght", None), minimum_size=2)
    u, v = _wind_components(prof)
    if height is None or u is None or v is None:
        return None
    if not (height.size == u.size == v.size):
        return None

    try:
        sfc = int(getattr(prof, "sfc", 0) or 0)
    except (TypeError, ValueError):
        sfc = 0
    if sfc < 0 or sfc >= height.size or not np.isfinite(height[sfc]):
        return None

    height = height[sfc:] - height[sfc]
    u = u[sfc:]
    v = v[sfc:]
    valid = np.isfinite(height) & np.isfinite(u) & np.isfinite(v)
    height, u, v = height[valid], u[valid], v[valid]
    if height.size < 2:
        return None

    order = np.argsort(height, kind="stable")
    height, u, v = height[order], u[order], v[order]
    height, unique = np.unique(height, return_index=True)
    u, v = u[unique], v[unique]
    top = min(float(max_height_m), float(height[-1]))
    if top < step_m:
        return None
    grid_top = np.floor(top / step_m) * step_m
    grid = np.arange(0.0, grid_top + step_m * 0.5, step_m)
    if grid.size < 2:
        return None

    motion = _storm_motion(prof, bool(use_left))
    if motion is None:
        return None
    storm_u, storm_v = motion

    u_ms = np.interp(grid, height, u) * KTS_TO_MS
    v_ms = np.interp(grid, height, v) * KTS_TO_MS
    u_sr = u_ms - storm_u
    v_sr = v_ms - storm_v

    dudz = np.gradient(u_ms, step_m)
    dvdz = np.gradient(v_ms, step_m)
    omega_u = -dvdz
    omega_v = dudz
    omega_mag = np.hypot(omega_u, omega_v)
    sr_speed = np.hypot(u_sr, v_sr)
    usable = (omega_mag > 1.0e-6) & (sr_speed > 0.1)

    percent = np.full(grid.shape, np.nan, dtype=float)
    signed = np.full(grid.shape, np.nan, dtype=float)
    if not np.any(usable):
        return None
    omega_streamwise = np.full(grid.shape, np.nan, dtype=float)
    omega_streamwise[usable] = (
        omega_u[usable] * (u_sr[usable] / sr_speed[usable])
        + omega_v[usable] * (v_sr[usable] / sr_speed[usable])
    )
    percent[usable] = np.clip(
        np.abs(omega_streamwise[usable]) / omega_mag[usable] * 100.0,
        0.0,
        100.0,
    )
    signed[usable] = np.sign(omega_streamwise[usable]) * percent[usable]

    return StreamwisenessData(
        height_km=grid / 1000.0,
        percent=percent,
        signed_percent=signed,
    )


class plotStreamwiseness(QtWidgets.QFrame):
    """Native Qt inset for streamwiseness versus height through 6 km AGL."""

    TITLE = "Streamwiseness"
    MAX_HEIGHT_KM = 6.0
    RIGHT_INSET = 25
    TEXT_COLOR = "#ffffff"
    PROFILE_COLOR = "#44ddaa"
    CYCLONIC_COLOR = "#ff3333"
    ANTICYCLONIC_COLOR = "#4488ff"
    BORDER_COLOR = "#3399cc"
    GRID_COLOR = "#33506a"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prof = None
        self.data = None
        self.use_left = False
        self.bg_color = QtGui.QColor(colors.BG_COLOR)
        self.fg_color = QtGui.QColor(colors.FG_COLOR)
        self.text_color = QtGui.QColor(self.TEXT_COLOR)
        self._legend_rect = QtCore.QRectF()
        self._border_lines = ()
        self.setObjectName("sharpmod_streamwiseness")
        self.setMinimumSize(150, 220)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.setStyleSheet("QFrame { border: 0px; margin: 0px; }")
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self._redraw()

    # ------------------------------------------------------------------
    # SHARPpy widget contract
    # ------------------------------------------------------------------
    def setProf(self, prof):
        self.prof = prof
        self.data = streamwiseness_profile(prof, use_left=self.use_left)
        self._redraw()
        self.update()

    def setPreferences(self, update_gui=True, **prefs):
        if "bg_color" in prefs:
            self.bg_color = QtGui.QColor(prefs["bg_color"])
        if "fg_color" in prefs:
            self.fg_color = QtGui.QColor(prefs["fg_color"])
        if update_gui:
            self._redraw()
            self.update()

    def setDeviant(self, deviant):
        self.use_left = deviant == "left"
        self.data = streamwiseness_profile(
            self.prof, use_left=self.use_left) if self.prof is not None else None
        self._redraw()
        self.update()

    def clearData(self):
        self.plotBitMap = QtGui.QPixmap(max(1, self.width()), max(1, self.height()))
        self.plotBitMap.fill(self.bg_color)

    def plotData(self):
        self._redraw()
        self.update()

    # ------------------------------------------------------------------
    # Geometry and paint lifecycle
    # ------------------------------------------------------------------
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
        side_inset = max(27, int(width * 0.14))
        left = side_inset
        right = width - self.RIGHT_INSET
        top = max(22, int(height * 0.07))
        bottom = height - max(30, int(height * 0.09))
        if right <= left:
            right = left + 1
        if bottom <= top:
            bottom = top + 1
        return QtCore.QRectF(left, top, right - left, bottom - top)

    @staticmethod
    def _x_to_pix(plot, value):
        return plot.left() + np.clip(float(value), 0.0, 100.0) / 100.0 * plot.width()

    @classmethod
    def _y_to_pix(cls, plot, height_km):
        fraction = np.clip(float(height_km), 0.0, cls.MAX_HEIGHT_KM) / cls.MAX_HEIGHT_KM
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

    def _draw_text(
            self, painter, rect, text, color=None,
            align=QtCore.Qt.AlignmentFlag.AlignCenter):
        painter.setPen(QtGui.QPen(
            self.text_color if color is None else color))
        painter.drawText(rect, int(align), str(text))

    # ------------------------------------------------------------------
    # Chart drawing
    # ------------------------------------------------------------------
    def _redraw(self):
        self.clearData()
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
            QtCore.QRectF(
                plot.left(), 2,
                plot.width(),
                plot.top() - 3,
            ),
            self.TITLE,
        )

        self._draw_grid(painter, plot, axis_size)
        if self.data is None:
            painter.setFont(self._font(max(12, title_size + 2), bold=True))
            self._draw_text(painter, plot, "--", self.text_color)
        else:
            self._draw_fills(painter, plot)
            self._draw_profile(painter, plot)
            self._draw_markers(painter, plot, tiny_size)
            self._draw_legend(painter, plot, tiny_size)

        painter.setFont(self._font(axis_size, bold=True))
        self._draw_text(
            painter,
            QtCore.QRectF(plot.left(), plot.bottom() + 13,
                          plot.width(), max(10, self.height() - plot.bottom() - 13)),
            "Streamwiseness (%)",
            self.text_color,
        )
        painter.save()
        painter.translate(8, plot.center().y())
        painter.rotate(-90)
        self._draw_text(
            painter,
            QtCore.QRectF(-plot.height() / 2.0, -7, plot.height(), 14),
            "Height AGL (km)",
            self.text_color,
        )
        painter.restore()

        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(self.BORDER_COLOR), 1))
        self._border_lines = (
            QtCore.QLineF(0.5, 0.5, 0.5, max(0.5, self.height() - 0.5)),
        )
        for line in self._border_lines:
            painter.drawLine(line)
        painter.end()

    def _draw_grid(self, painter, plot, font_size):
        grid = QtGui.QColor(self.GRID_COLOR)
        grid.setAlpha(130)
        pen = QtGui.QPen(grid, 1, QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setFont(self._font(font_size))

        for tick in (0, 25, 50, 75, 100):
            x = self._x_to_pix(plot, tick)
            painter.setPen(
                QtGui.QPen(self.text_color, 1) if tick == 100 else pen)
            painter.drawLine(QtCore.QPointF(x, plot.top()),
                             QtCore.QPointF(x, plot.bottom()))
            if tick != 0:
                self._draw_text(
                    painter,
                    QtCore.QRectF(x - 12, plot.bottom() + 1, 24, 12),
                    tick,
                    self.text_color,
                )
        for tick in range(0, 7):
            y = self._y_to_pix(plot, tick)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(plot.left(), y),
                             QtCore.QPointF(plot.right(), y))
            self._draw_text(
                painter,
                QtCore.QRectF(11, y - 6, max(14, plot.left() - 13), 12),
                tick,
                self.text_color,
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
            )

    def _draw_fills(self, painter, plot):
        data = self.data
        for index in range(len(data.height_km) - 1):
            p0, p1 = data.percent[index:index + 2]
            s0, s1 = data.signed_percent[index:index + 2]
            if not np.all(np.isfinite((p0, p1, s0, s1))):
                continue
            y0 = self._y_to_pix(plot, data.height_km[index])
            y1 = self._y_to_pix(plot, data.height_km[index + 1])
            x0 = self._x_to_pix(plot, p0)
            x1 = self._x_to_pix(plot, p1)
            color = QtGui.QColor(
                self.CYCLONIC_COLOR if (s0 + s1) >= 0.0
                else self.ANTICYCLONIC_COLOR)
            color.setAlpha(52)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(color))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(plot.left(), y0),
                QtCore.QPointF(x0, y0),
                QtCore.QPointF(x1, y1),
                QtCore.QPointF(plot.left(), y1),
            ]))

    def _draw_profile(self, painter, plot):
        data = self.data
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(self.PROFILE_COLOR), 2))
        path = QtGui.QPainterPath()
        active = False
        for value, height_km in zip(data.percent, data.height_km):
            if not np.isfinite(value) or height_km > self.MAX_HEIGHT_KM:
                active = False
                continue
            point = QtCore.QPointF(
                self._x_to_pix(plot, value), self._y_to_pix(plot, height_km))
            if active:
                path.lineTo(point)
            else:
                path.moveTo(point)
                active = True
        painter.drawPath(path)

    def _draw_markers(self, painter, plot, font_size):
        painter.setFont(self._font(font_size, bold=True))
        for depth, color_hex in (
                (0.5, "#b8bcc2"), (1.0, "#ff8800"), (3.0, "#ffcc00")):
            valid = np.isfinite(self.data.percent)
            if not np.any(valid):
                continue
            distances = np.where(valid,
                                 np.abs(self.data.height_km - depth), np.inf)
            index = int(np.argmin(distances))
            value = float(self.data.percent[index])
            if not np.isfinite(value):
                continue
            color = QtGui.QColor(color_hex)
            color_dim = QtGui.QColor(color)
            color_dim.setAlpha(115)
            y = self._y_to_pix(plot, depth)
            x = self._x_to_pix(plot, value)
            painter.setPen(QtGui.QPen(
                color_dim, 1, QtCore.Qt.PenStyle.DashLine))
            painter.drawLine(QtCore.QPointF(plot.left(), y),
                             QtCore.QPointF(plot.right(), y))
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
            painter.setBrush(QtGui.QBrush(color))
            painter.drawEllipse(QtCore.QPointF(x, y), 2.8, 2.8)
            label = f"{value:.0f}%"
            if x > plot.right() - 35:
                rect = QtCore.QRectF(x - 39, y - 13, 36, 12)
                align = (QtCore.Qt.AlignmentFlag.AlignRight
                         | QtCore.Qt.AlignmentFlag.AlignVCenter)
            else:
                rect = QtCore.QRectF(x + 4, y - 13, 36, 12)
                align = (QtCore.Qt.AlignmentFlag.AlignLeft
                         | QtCore.Qt.AlignmentFlag.AlignVCenter)
            self._draw_text(painter, rect, label, self.text_color, align)

    def _draw_legend(self, painter, plot, font_size):
        painter.setFont(self._font(font_size))
        metrics = QtGui.QFontMetrics(painter.font())
        labels = (
            ("Cyclonic", QtGui.QColor(self.CYCLONIC_COLOR)),
            ("Anticyclonic", QtGui.QColor(self.ANTICYCLONIC_COLOR)),
        )
        row_h = max(9, metrics.height())
        width = min(plot.width() - 4, max(
            metrics.horizontalAdvance(label) + 18 for label, _ in labels) + 4)
        height = row_h * len(labels) + 4
        left = plot.right() - width - 2
        top = plot.top() + 2
        self._legend_rect = QtCore.QRectF(left, top, width, height)
        background = QtGui.QColor(self.bg_color)
        background.setAlpha(220)
        painter.setPen(QtGui.QPen(QtGui.QColor("#555b62"), 1))
        painter.setBrush(QtGui.QBrush(background))
        painter.drawRect(self._legend_rect)
        for row, (label, color) in enumerate(labels):
            y = top + 2 + row * row_h
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            painter.setPen(QtGui.QPen(color, 1))
            painter.setBrush(QtGui.QBrush(fill))
            painter.drawRect(QtCore.QRectF(left + 3, y + 2, 10, row_h - 4))
            self._draw_text(
                painter,
                QtCore.QRectF(left + 16, y, width - 18, row_h),
                label,
                self.text_color,
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
            )
