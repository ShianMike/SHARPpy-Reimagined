"""Lightweight cursor layer for synchronized field maps.

Mouse motion changes guides and text, not the weather image. Painting them in a
transparent child keeps four raster maps out of the hover repaint path.
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from qtpy.QtGui import QColor, QPainter, QPen, QRegion
from qtpy.QtWidgets import QWidget

from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.maps.presentation import _map
from sharpmod.ui.styles.theme import OBJ_PLAIN


class CrosshairOverlay(QWidget):
    """Draw only the moving guide and one elided readout over a point map."""

    def __init__(self, map_widget) -> None:
        super().__init__(map_widget)
        self._map_widget = map_widget
        self._guide: tuple[int, int] | None = None
        self._readout_key: tuple | None = None
        self.setObjectName(OBJ_PLAIN)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setGeometry(map_widget.rect())
        map_widget.installEventFilter(self)
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self._map_widget and event.type() == QEvent.Resize:
            self.setGeometry(self._map_widget.rect())
            self.refresh(force=True)
        return super().eventFilter(watched, event)

    def _guide_position(self) -> tuple[int, int] | None:
        crosshair = self._map_widget.crosshair()
        if crosshair is None:
            return None
        try:
            point = self._map_widget._to_px(
                crosshair[1], crosshair[0], self._map_widget._proj())
            return round(point.x()), round(point.y())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _readout(self) -> str:
        widget = self._map_widget
        if widget.crosshair() is not None:
            return widget.crosshair_text()
        hover = getattr(widget, "_hover_lonlat", None)
        if hover is None:
            return ""
        detail = getattr(widget, "_hover_inspection_text", "")
        if detail and getattr(widget, "_map_tool", "select") == "inspect":
            return str(detail)
        return f"Cursor  {hover[1]:.3f}, {hover[0]:.3f}"

    def _readout_state(self) -> tuple | None:
        """Compare cheap cursor state here; sample the grid only while painting."""
        widget = self._map_widget
        position = widget.crosshair()
        if position is None:
            position = getattr(widget, "_hover_lonlat", None)
        if position is None:
            return None
        raster = getattr(widget, "_rasters", {}).get("hrrr_field")
        return (round(position[0], 3), round(position[1], 3),
                getattr(widget, "_hover_inspection_text", ""),
                getattr(widget, "_inspection_source", None), id(raster),
                self._readout_y())

    def _readout_y(self) -> int:
        base = getattr(self._map_widget, "_readout_rect", None)
        return max(8, round(base[1] + base[3] + 2)) if base else 8

    def refresh(self, *, force: bool = False) -> None:
        guide = self._guide_position()
        readout_key = self._readout_state()
        if not force and guide == self._guide and readout_key == self._readout_key:
            return
        old_readout_y = self._readout_key[-1] if self._readout_key else 8
        dirty = QRegion(QRect(
            0, 0, self.width(),
            min(self.height(), max(old_readout_y, self._readout_y()) + 48)))
        for point in (self._guide, guide):
            if point is None:
                continue
            x, y = point
            dirty = dirty.united(QRegion(QRect(x - 2, 0, 5, self.height())))
            dirty = dirty.united(QRegion(QRect(0, y - 2, self.width(), 5)))
        self._guide = guide
        self._readout_key = readout_key
        self.update(self.rect() if force else dirty)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        if self._guide is not None:
            x, y = self._guide
            painter.setPen(QPen(QColor(_map().selected_edge), 1.2, Qt.DashLine))
            painter.drawLine(0, y, self.width(), y)
            painter.drawLine(x, 0, x, self.height())
        self._draw_field_marker(painter)
        try:
            text = self._readout() if self._readout_key is not None else ""
        except Exception:  # noqa: BLE001 - the hover readout is advisory
            text = ""
        if text:
            painter.setFont(mono_font("body"))
            metrics = painter.fontMetrics()
            line = metrics.elidedText(
                text, Qt.ElideRight, max(0, self.width() - 16))
            bounds = QRectF(8, self._readout_y(), max(0, self.width() - 16),
                            max(18, metrics.height() + 2))
            painter.setPen(QColor(_map().readout_shadow))
            painter.drawText(bounds.translated(1, 1),
                             Qt.AlignLeft | Qt.AlignVCenter, line)
            painter.setPen(QColor(_map().readout_text))
            painter.drawText(bounds, Qt.AlignLeft | Qt.AlignVCenter, line)
        painter.end()

    def _draw_field_marker(self, painter: QPainter) -> None:
        """Draw the hovered field value over its cached colour-bar frame."""
        widget = self._map_widget
        bar = getattr(widget, "_legend_bar", None)
        scale = getattr(widget, "_legend_bar_range", None)
        if bar is None or scale is None:
            return
        position = widget.crosshair()
        if position is None:
            hover = getattr(widget, "_hover_lonlat", None)
            if hover is None:
                return
            lat, lon = float(hover[1]), float(hover[0])
        else:
            lat, lon = position
        try:
            sample = widget.sample_field_at(lat, lon)
            if not getattr(sample, "has_value", False):
                return
            from sharpmod.maps.map_legends import colorbar_fraction

            fraction = colorbar_fraction(float(sample.value), *scale)
            if fraction is None:
                return
            x, y, width, height = bar
            marker_x = x + width * float(fraction)
            for pen, offset in (
                (QPen(QColor(_map().readout_shadow), 1.6), 1.0),
                (QPen(QColor(_map().selected_edge), 1.4), 0.0),
            ):
                painter.setPen(pen)
                painter.drawLine(
                    QPointF(marker_x + offset, y - 2.0),
                    QPointF(marker_x + offset, y + height + 2.0),
                )
        except (AttributeError, RuntimeError, TypeError, ValueError,
                OverflowError):
            return
