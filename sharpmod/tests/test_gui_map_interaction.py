"""Regression coverage for responsive picker-map interaction."""

import os

# Select the headless Qt platform before importing ``sharpmod.gui``.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QPoint, QPointF, Qt
from qtpy.QtGui import QPainter, QPixmap
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from sharpmod.gui import PointMapWidget, StationMapWidget


class _WheelEvent:
    def angleDelta(self):  # noqa: N802 - mirrors the Qt event API
        return QPoint(0, 120)

    def position(self):
        return QPointF(320.0, 240.0)


class _MouseEvent:
    def __init__(self, x: float, y: float):
        self._position = QPointF(x, y)

    def button(self):
        return Qt.LeftButton

    def buttons(self):
        return Qt.LeftButton

    def position(self):
        return self._position


def _primed_map() -> StationMapWidget:
    QApplication.instance() or QApplication([])
    widget = StationMapWidget([])
    widget.resize(640, 480)
    widget._basemap_pixmap()
    return widget


def test_wheel_zoom_keeps_cached_basemap_until_input_settles():
    widget = _primed_map()
    cached = widget._basemap_cache
    cached_key = widget._cache_key

    widget.wheelEvent(_WheelEvent())

    assert widget._basemap_cache is cached
    assert widget._cache_key == cached_key
    assert widget._basemap_refresh_timer.isActive()


def test_wheel_zoom_paints_preview_without_rerasterizing(monkeypatch):
    widget = _primed_map()
    widget.wheelEvent(_WheelEvent())

    def unexpected_rasterization():
        raise AssertionError("wheel preview rerasterized the vector basemap")

    monkeypatch.setattr(widget, "_basemap_pixmap", unexpected_rasterization)
    target = QPixmap(widget.size())
    painter = QPainter(target)
    try:
        widget._draw_basemap(painter)
    finally:
        painter.end()


def test_forecast_map_keeps_preview_across_notched_wheel_cadence():
    QApplication.instance() or QApplication([])
    widget = PointMapWidget()
    widget.resize(640, 480)
    widget._basemap_pixmap()
    cached = widget._basemap_cache

    widget.wheelEvent(_WheelEvent())
    QTest.qWait(100)

    # Consecutive physical mouse-wheel notches commonly arrive about 100 ms
    # apart.  The expensive vector redraw must not run between those notches.
    assert widget._basemap_refresh_timer.isActive()
    assert widget._basemap_cache is cached


def test_forecast_map_drag_keeps_cached_basemap_during_mouse_moves():
    QApplication.instance() or QApplication([])
    widget = PointMapWidget()
    widget.resize(640, 480)
    widget._basemap_pixmap()
    cached = widget._basemap_cache

    widget.mousePressEvent(_MouseEvent(300.0, 220.0))
    widget.mouseMoveEvent(_MouseEvent(340.0, 245.0))

    assert widget._basemap_refresh_timer.isActive()
    assert widget._basemap_cache is cached
