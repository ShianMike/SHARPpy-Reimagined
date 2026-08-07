"""Regression tests for patched Theta-E / SR-Wind inset labels."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtCore, QtGui


class _RecordingPainter:
    def __init__(self):
        self.fonts = []
        self.texts = []

    def setPen(self, *_args):
        pass

    def setFont(self, font):
        self.fonts.append(QtGui.QFont(font))

    def drawLine(self, *_args):
        pass

    def drawText(self, *args):
        text = str(args[-1])
        if text == "SR Wind\nv.\nHeight":
            self.texts.append(SimpleNamespace(
                x=args[0],
                y=args[1],
                width=args[2],
                height=args[3],
                flags=args[4],
                text=text,
            ))


class _SRWindWidget:
    tlx = 0
    tly = 0
    brx = 140
    bry = 120
    fg_color = QtGui.QColor("#ffffff")
    clsc_color = QtGui.QColor("#ff0000")

    def __init__(self):
        self.label_font = QtGui.QFont("Helvetica", 16)

    def speed_to_pix(self, speed):
        return {15.0: 30.0, 40.0: 82.0, 70.0: 122.0}[float(speed)]

    def hgt_to_pix(self, height):
        return {8.0: 82.0, 16.0: 22.0}[float(height)]


def test_srwind_title_uses_normal_inset_label_font(qt_app):
    pytest.importorskip("sharppy.viz.srwinds")

    from sharpmod.viz import inset_layout
    from sharppy.viz.srwinds import backgroundWinds

    assert inset_layout.apply()

    widget = _SRWindWidget()
    painter = _RecordingPainter()

    backgroundWinds.draw_frame(widget, painter)

    assert painter.fonts
    assert painter.fonts[0].pointSize() == widget.label_font.pointSize()

    title = painter.texts[0]
    assert title.x == 35
    assert title.y == 15
    assert title.width == 50
    assert title.height == 50
    assert title.flags & QtCore.Qt.TextDontClip
    assert title.flags & QtCore.Qt.AlignVCenter
    assert title.flags & QtCore.Qt.AlignHCenter
