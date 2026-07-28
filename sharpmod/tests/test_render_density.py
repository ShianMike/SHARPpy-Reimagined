"""Regression tests for target-density scientific-panel bitmap caches."""

from __future__ import annotations

import pytest
from qtpy import QtCore, QtGui, QtWidgets

from sharpmod import render


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app


@pytest.mark.parametrize(
    ("scale", "logical", "physical"),
    [
        (2.0, (13, 7), (26, 14)),
        (2.8, (13, 7), (36, 20)),
    ],
)
def test_density_pixmap_preserves_logical_geometry(
        qapp, scale, logical, physical):
    del qapp
    native = render._NATIVE_QPIXMAP

    with render._target_density_pixmaps(scale):
        pixmap = QtGui.QPixmap(*logical)
        sized = QtGui.QPixmap(QtCore.QSize(*logical))

        for candidate in (pixmap, sized):
            assert candidate.devicePixelRatioF() == pytest.approx(scale)
            assert (candidate.width(), candidate.height()) == logical
            assert candidate.size() == QtCore.QSize(*logical)
            assert candidate.rect() == QtCore.QRect(
                0, 0, logical[0], logical[1])
            assert (
                native.width(candidate),
                native.height(candidate),
            ) == physical
            assert (
                candidate.toImage().width(),
                candidate.toImage().height(),
            ) == physical


def test_density_pixmap_copy_maps_fractional_dpr_edges(qapp):
    del qapp
    native = render._NATIVE_QPIXMAP
    scale = 2.8

    with render._target_density_pixmaps(scale):
        pixmap = QtGui.QPixmap(11, 9)
        pixmap.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(pixmap)
        try:
            painter.fillRect(
                QtCore.QRect(5, 4, 6, 5), QtGui.QColor("black"))
        finally:
            painter.end()

        copied = pixmap.copy(5, 4, 6, 5)
        rect_copied = pixmap.copy(QtCore.QRect(5, 4, 6, 5))
        full_copy = pixmap.copy()

        expected_physical = (
            round(11 * scale) - round(5 * scale),
            round(9 * scale) - round(4 * scale),
        )
        for candidate in (copied, rect_copied):
            assert isinstance(candidate, QtGui.QPixmap)
            assert candidate.devicePixelRatioF() == pytest.approx(scale)
            assert (candidate.width(), candidate.height()) == (6, 5)
            assert (
                native.width(candidate),
                native.height(candidate),
            ) == expected_physical
            image = candidate.toImage()
            assert image.pixelColor(
                image.width() - 2, image.height() - 2
            ).name() == "#000000"

        assert isinstance(full_copy, QtGui.QPixmap)
        assert (full_copy.width(), full_copy.height()) == (11, 9)
        assert (
            native.width(full_copy),
            native.height(full_copy),
        ) == (round(11 * scale), round(9 * scale))

        through_edge = pixmap.copy(5, 4, -1, -1)
        assert (through_edge.width(), through_edge.height()) == (6, 5)
        assert (
            native.width(through_edge),
            native.height(through_edge),
        ) == expected_physical


def test_density_pixmap_preserves_null_constructor_semantics(qapp):
    del qapp

    with render._target_density_pixmaps(2.0):
        pixmap = QtGui.QPixmap(0, 0)

        assert pixmap.isNull()
        assert (pixmap.width(), pixmap.height()) == (0, 0)


def test_density_pixmap_context_restores_qt_class_after_failure(qapp):
    del qapp
    native = QtGui.QPixmap

    with pytest.raises(RuntimeError, match="deliberate failure"):
        with render._target_density_pixmaps(2.0):
            assert QtGui.QPixmap is not native
            raise RuntimeError("deliberate failure")

    assert QtGui.QPixmap is native


def test_density_pixmap_context_rejects_nested_global_override(qapp):
    del qapp
    native = QtGui.QPixmap

    with render._target_density_pixmaps(2.0):
        with pytest.raises(RuntimeError, match="already temporarily"):
            with render._target_density_pixmaps(2.0):
                pass

    assert QtGui.QPixmap is native


def test_final_hd_capture_is_not_scaled_twice(qapp):
    app = qapp

    class PatternWidget(QtWidgets.QWidget):
        def paintEvent(self, event):  # noqa: N802 - Qt override
            del event
            painter = QtGui.QPainter(self)
            try:
                painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))
                painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Crisp")
            finally:
                painter.end()

    with render._target_density_pixmaps(2.0):
        widget = PatternWidget()
        widget.resize(80, 50)
        widget.show()
        app.processEvents()
        captured = render.grab_widget_pixmap(widget, scale=2.0)

        assert type(captured) is render._NATIVE_QPIXMAP
        assert (captured.width(), captured.height()) == (160, 100)
        widget.close()
        widget.deleteLater()
