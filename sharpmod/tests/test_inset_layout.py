"""Regression tests for vendored bottom-inset label layout shims."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtGui

from sharpmod.viz import inset_layout


class _ThetaeStub:
    tlx = 0
    tly = 0
    lpad = 0
    rpad = 0
    brx = 240
    bry = 100
    tpad = 0
    fg_color = QtGui.QColor("#ffffff")
    label_font = QtGui.QFont("Helvetica", 12)

    def theta_to_pix(self, t):
        return 35 + (float(t) - 300.0) * 3.0


class _PainterRecorder:
    def __init__(self):
        self.text_rects = []

    def setPen(self, *_args):
        pass

    def setFont(self, *_args):
        pass

    def drawLine(self, *_args):
        pass

    def drawText(self, x, y, w, h, _flags, text):
        self.text_rects.append((int(x), int(x) + int(w), str(text)))


def test_thetae_bottom_labels_do_not_overlap_when_inset_is_narrow(qt_app):
    assert inset_layout.apply()

    from sharppy.viz.thetae import backgroundThetae

    widget = _ThetaeStub()
    painter = _PainterRecorder()

    for tick in range(200, 361, 10):
        backgroundThetae.draw_thetae(widget, tick, painter)

    labels = painter.text_rects
    assert len(labels) >= 2
    assert len(labels) < len(range(300, 361, 10))
    assert any(text == "300" for _left, _right, text in labels)
    for (_left_a, right_a, _text_a), (left_b, _right_b, _text_b) in zip(
            labels, labels[1:]):
        assert left_b >= right_a + 3
