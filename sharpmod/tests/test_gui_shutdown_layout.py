"""GUI shutdown, minimum-size, and viewer-fit regressions."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHARPMOD_GEOCODER_URL", "off")

from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import QApplication, QMainWindow, QWidget

from sharpmod import gui_picker, gui_viewer
from sharpmod.gui_settings import _build_settings


class _Timer:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _Worker:
    def __init__(self, *, cooperative=True):
        self.cooperative = cooperative
        self.running = True
        self.interrupted = 0
        self.terminated = 0
        self.waits = []

    def requestInterruption(self):  # noqa: N802 - mirrors QThread
        self.interrupted += 1

    def isRunning(self):  # noqa: N802 - mirrors QThread
        return self.running

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.cooperative or not self.running:
            self.running = False
            return True
        return False

    def terminate(self):
        self.terminated += 1
        self.running = False


class _Cache:
    def __init__(self):
        self.calls = 0

    def clear(self):
        self.calls += 1


class _DiskCache:
    def __init__(self):
        self.calls = 0

    def prune(self):
        self.calls += 1


def test_shutdown_stops_every_owned_gui_worker():
    timers = [_Timer() for _ in range(4)]
    catalog = _Worker(cooperative=False)
    availability = _Worker()
    model_availability = _Worker()
    observed = _Worker()
    model = _Worker()
    cache = _Cache()
    disk_cache = _DiskCache()
    owner = SimpleNamespace(
        _shutdown_started=False,
        _avail_timer=timers[0],
        _catalog_timer=timers[1],
        _model_availability_timer=timers[2],
        _model_progress_timer=timers[3],
        _avail_request=object(),
        _catalog_request=object(),
        _model_availability_request=object(),
        _avail_token=1,
        _catalog_token=2,
        _model_availability_token=3,
        _catalog_worker=catalog,
        _avail_workers=[availability],
        _model_availability_workers=[model_availability],
        _worker=observed,
        _model_worker=model,
        _model_timeline_worker=None,
        _model_prefetch_worker=None,
        _era5_worker=None,
        _wrf_inspect_worker=None,
        _wrf_extract_worker=None,
        _model_hour_cache=cache,
        _model_disk_cache=disk_cache,
    )

    gui_picker.PickerWindow._shutdown_model_cache(owner)

    assert all(timer.stopped for timer in timers)
    assert owner._avail_request is None
    assert owner._catalog_request is None
    assert owner._model_availability_request is None
    assert (owner._avail_token, owner._catalog_token,
            owner._model_availability_token) == (2, 3, 4)
    assert all(
        worker.interrupted == 1
        for worker in (catalog, availability, model_availability, observed, model)
    )
    assert catalog.terminated == 1
    assert not any(
        worker.running
        for worker in (catalog, availability, model_availability, observed, model)
    )
    assert owner._avail_workers == []
    assert owner._model_availability_workers == []
    assert owner._catalog_worker is None
    assert owner._worker is None
    assert owner._model_worker is None
    assert cache.calls == 1
    assert disk_cache.calls == 1

    gui_picker.PickerWindow._shutdown_model_cache(owner)
    assert cache.calls == 1
    assert disk_cache.calls == 1


def test_minimum_picker_size_scrolls_instead_of_collapsing_controls(
        monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "settings.ini"
    monkeypatch.setattr(
        gui_picker,
        "_build_settings",
        lambda: _build_settings(path=settings_path),
    )
    monkeypatch.setattr(
        gui_picker.PickerWindow,
        "_refresh_station_catalog",
        lambda *_args: None,
    )

    picker = gui_picker.PickerWindow()
    picker._avail_timer.stop()
    picker._catalog_timer.stop()
    picker._model_availability_timer.stop()
    picker.resize(900, 620)
    picker.show()
    app.processEvents()

    rails = (
        (0, picker._map_controls_scroll),
        (2, picker._model_controls_scroll),
        (3, picker._era5_controls_scroll),
    )
    for tab, rail in rails:
        picker._tabs.setCurrentIndex(tab)
        app.processEvents()
        assert rail.verticalScrollBar().maximum() > 0
        assert rail.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    picker._tabs.setCurrentIndex(4)
    picker._file_modes.setCurrentIndex(1)
    app.processEvents()
    assert picker._wrf_controls_scroll.verticalScrollBar().maximum() > 0
    assert picker._model_fxx_combo.minimumHeight() >= 30
    assert picker._model_fxx_combo.sizeHint().height() >= 30

    picker._shutdown_model_cache()
    picker.close()
    picker.deleteLater()
    app.processEvents()


def test_native_sounding_fit_removes_transient_scroll_ranges():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.menuBar().addMenu("File")
    sounding = QWidget()
    natural = QSize(600, 400)
    host = gui_viewer._FixedSoundingScrollArea(
        sounding, natural, window)
    window.setCentralWidget(host)

    estimated_menu = window.menuBar().sizeHint().height()
    window.resize(natural.width(), natural.height() + estimated_menu - 12)
    window.show()
    app.processEvents()
    assert host.verticalScrollBar().maximum() > 0

    gui_viewer._finalize_scaled_fit(app, window)
    app.processEvents()
    app.processEvents()

    assert host.horizontalScrollBar().maximum() == 0
    assert host.verticalScrollBar().maximum() == 0
    window.close()
    window.deleteLater()
    app.processEvents()
