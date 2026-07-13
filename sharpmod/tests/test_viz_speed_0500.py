"""Regression tests for the wind-speed strip 0-500 m color split."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import sharpmod.render as render_mod


class _Painter:
    def __init__(self):
        self.pen = None
        self.lines = []

    def setPen(self, pen):
        self.pen = pen

    def drawLine(self, *args):
        self.lines.append((self.pen.color().name().lower(), args))


def test_speed_strip_draws_sfc_500m_in_hodograph_pink():
    render_mod._install_speed_0500()

    import sharppy.viz.speed as speed_mod

    hght = np.array([0.0, 300.0, 800.0, 3500.0])
    widget = SimpleNamespace(
        prof=SimpleNamespace(hght=hght, sfc=0),
        u=np.array([10.0, 20.0, 30.0, 40.0]),
        v=np.array([0.0, 0.0, 0.0, 0.0]),
        hght=hght,
        pres=np.array([1000.0, 950.0, 900.0, 700.0]),
        wind_units="knots",
        low_level_color=speed_mod.QtGui.QColor("#ff0000"),
        mid_level_color=speed_mod.QtGui.QColor("#00ff00"),
        upper_level_color=speed_mod.QtGui.QColor("#ffff00"),
        trop_level_color=speed_mod.QtGui.QColor("#00ffff"),
        speed_to_pix=lambda s: float(s),
        pres_to_pix=lambda p: float(p),
    )
    painter = _Painter()

    speed_mod.plotSpeed.draw_profile(widget, painter)

    colors = [color for color, _args in painter.lines]
    assert colors[:2] == ["#ff00ff", "#ff00ff"]
    assert colors[2] == "#ff0000"
    assert colors[3] == "#00ff00"
