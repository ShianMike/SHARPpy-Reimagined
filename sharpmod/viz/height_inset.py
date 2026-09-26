"""Shared Qt widget for compact value-versus-height profile charts.

Subclasses declare their series and calculations; this base handles common geometry,
axes, palette, legend, fills, and compatibility with SHARPpy panel composition."""

from __future__ import annotations

from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from sharpmod.viz import colors
import numpy as np
from sharpmod.viz import height_charts as _api


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
            _api._LOGGER.debug(
                "height_charts.compute_failed chart=%s",
                type(self).__name__, exc_info=True)
            return None

    # -- palette --------------------------------------------------------- #
    def _apply_palette(self):
        palette = colors.semantic_palette(
            self.bg_color.name(), self.fg_color.name())
        self._palette = palette
        self.text_color = QtGui.QColor(palette["neutral"])
        # Match the canonical Skew-T plot frame; reserve the semantic blue for
        # grid accents rather than the outside edge of a plot box.
        self.border_color = QtGui.QColor(self.fg_color)
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
        step = _api._nice_step(high - low)
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
        step = _api._nice_step(high - low)
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
        painter.setPen(QtGui.QPen(
            self.border_color, colors.PLOT_FRAME_WIDTH))
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

        step_km = _api._nice_step(self.MAX_HEIGHT_KM, target=6)
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
