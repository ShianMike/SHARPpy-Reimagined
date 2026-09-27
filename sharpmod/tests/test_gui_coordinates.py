"""Coordinate preview, cancellation, precision, and authoritative point updates."""

import gc
import weakref

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QDoubleSpinBox, QMainWindow, QVBoxLayout, QWidget

from sharpmod.gui_coordinates import coordinate_paste_button


class _PointWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1000, 650)
        body = QWidget(self)
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        self.lat, self.lon = QDoubleSpinBox(body), QDoubleSpinBox(body)
        self.lat.setRange(-90, 90)
        self.lon.setRange(-180, 180)
        for spin in (self.lat, self.lon):
            spin.setDecimals(4)
            layout.addWidget(spin)
        self.lat.setValue(35.63)
        self.lon.setValue(-97.44)
        self.changes = []
        self.lat.valueChanged.connect(self.point_changed)
        self.lon.valueChanged.connect(self.point_changed)
        self.paste = coordinate_paste_button(self, self.lat, self.lon, self.point_changed,
                                            source="Test point")
        layout.addWidget(self.paste)

    def point_changed(self, *_args, center=False):
        self.changes.append((self.lat.value(), self.lon.value(), center))


@pytest.fixture
def point_window(qt_app):
    window = _PointWindow()
    window.show()
    qt_app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()


def _open(window):
    window.paste.click()
    dialog = window.paste._sharpmod_coordinate_controller.dialog
    assert dialog.isVisible()
    return dialog


def test_preview_is_precise_and_cancel_does_not_update_any_field(point_window):
    window = point_window
    dialog = _open(window)
    assert not dialog.apply_button.isEnabled()
    dialog.input.setText("35.123456N 80.654321W")
    assert "35.1235° N" in dialog.preview.text()
    assert "80.6543° W" in dialog.preview.text()
    assert "Rounded" in dialog.status.text()
    assert "grid point may differ" in dialog.preview.text()
    assert (window.lat.value(), window.lon.value()) == (35.63, -97.44)
    assert window.changes == []
    dialog.reject()
    assert window.changes == []


def test_ambiguous_input_requires_visible_correction_then_enter_updates_once(point_window, qt_app):
    window = point_window
    dialog = _open(window)
    dialog.input.setText("35, -80")
    assert "Both values" in dialog.status.text()
    assert not dialog.apply_button.isEnabled()
    dialog.use_point()
    assert window.changes == [] and dialog.isVisible()
    dialog.order.setCurrentIndex(dialog.order.findData("lat_lon"))
    assert "Explicit order" in dialog.status.text()
    dialog.activateWindow()
    dialog.input.setFocus()
    qt_app.processEvents()
    QTest.keyClick(dialog.input, Qt.Key_Return)
    assert window.changes == [(35, -80, True)]
    assert not dialog.isVisible()


def test_invalid_busy_and_changed_range_never_clamp_or_commit(point_window):
    window = point_window
    dialog = _open(window)
    dialog.input.setText("lat:91 lon:-80")
    assert not dialog.apply_button.isEnabled()
    assert "Latitude" in dialog.status.text()
    dialog.input.setText("lat:35 lon:-80")
    window.lat.setEnabled(False)
    dialog.use_point()
    assert "busy or unavailable" in dialog.status.text()
    assert dialog.isVisible() and window.changes == []
    window.lat.setEnabled(True)
    window.lat.setMaximum(30)
    window.changes.clear()  # Qt range change itself moves the original value.
    previous = (window.lat.value(), window.lon.value())
    dialog.use_point()
    assert "exceeds" in dialog.status.text()
    assert (window.lat.value(), window.lon.value()) == previous
    assert window.changes == []


def test_live_precision_and_blocked_signal_state_are_preserved(point_window):
    window = point_window
    dialog = _open(window)
    dialog.input.setText("35.123456N 80.654321W")
    window.lat.setDecimals(2)
    window.changes.clear()
    window.lon.blockSignals(True)
    dialog.use_point()
    assert "35.12° N" in dialog.preview.text()
    assert window.changes == [(35.12, -80.6543, True)]
    assert not window.lat.signalsBlocked() and window.lon.signalsBlocked()


def test_enlarged_preview_scrolls_without_clipping_its_text_or_warning(point_window, qt_app):
    from sharpmod import gui_theme

    try:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
        dialog = _open(point_window)
        dialog.input.setText("35, -80")
        qt_app.processEvents()
        assert dialog.width() <= point_window.width()
        assert dialog.height() <= point_window.height()
        assert dialog.preview.height() >= dialog.preview.heightForWidth(dialog.preview.width())
        assert dialog.status.parentWidget() is dialog, "corrective warning stays outside the scroll viewport"
        assert dialog.status.height() >= dialog.status.heightForWidth(dialog.status.width())
        assert dialog.apply_button.isVisibleTo(dialog)
        assert not dialog.status.geometry().intersects(dialog.apply_button.parentWidget().geometry())
        assert "Both values" in dialog.status.text()
        assert not dialog.apply_button.isEnabled()
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_repeated_preview_and_window_deletion_release_wrappers(qt_app):
    window = _PointWindow()
    window.show()
    controller = window.paste._sharpmod_coordinate_controller
    for _ in range(3):
        controller.open()
        controller.dialog.reject()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    from sharpmod.gui_coordinates import CoordinateDialog

    assert len(window.findChildren(CoordinateDialog)) == 1
    window_ref, dialog_ref = weakref.ref(window), weakref.ref(controller.dialog)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    del controller, window
    gc.collect()
    assert window_ref() is None and dialog_ref() is None
