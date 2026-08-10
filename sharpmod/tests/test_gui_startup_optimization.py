"""Regressions for picker first-paint and observed-fetch optimizations."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

from sharpmod import gui_picker, gui_workers
from sharpmod.gui_workers import AVAIL_AVAILABLE


def _close_picker(picker, app):
    picker._catalog_timer.stop()
    picker._avail_timer.stop()
    picker._model_availability_timer.stop()
    picker._shutdown_model_cache()
    picker.close()
    picker.deleteLater()
    app.processEvents()


def test_picker_import_does_not_load_heavy_analysis_modules():
    root = Path(__file__).resolve().parents[2]
    code = r"""
import json
import sys
import sharpmod.gui_picker
names = (
    "numpy",
    "sharppy.sharptab.prof_collection",
    "sharpmod.gui_viewer",
    "sharpmod.gui_timeline",
    "sharpmod.model_disk_cache",
    "sharpmod.portable_sounding",
)
print(json.dumps({name: name in sys.modules for name in names}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(result.stdout.strip())
    assert not any(loaded.values()), loaded


def test_picker_builds_only_station_map_before_first_tab_use(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    picker = gui_picker.PickerWindow()
    try:
        assert set(picker._lazy_tab_builders) == {
            "Station List",
            "Forecast Model",
            "Reanalysis (ERA5)",
            "Open File",
        }
        assert not hasattr(picker, "_station_list")
        assert not hasattr(picker, "_model_map")
        assert not hasattr(picker, "_era5_map")
        assert not hasattr(picker, "_file_modes")
        assert picker._model_disk_cache is None
        assert picker._model_hour_cache is None
        assert picker._catalog_worker is None
        assert picker._avail_workers == []
        assert picker._model_availability_workers == []

        picker._select_tab("Station List")
        assert hasattr(picker, "_station_list")
        assert "Station List" not in picker._lazy_tab_builders
        assert not hasattr(picker, "_model_map")
    finally:
        _close_picker(picker, qt_app)


def test_model_cache_prune_runs_in_background(qt_app, tmp_path, monkeypatch):
    from sharpmod import model_disk_cache

    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("SHARPMOD_MODEL_CACHE", str(tmp_path / "cache"))
    started = threading.Event()
    release = threading.Event()

    def slow_prune(_cache):
        started.set()
        release.wait(2.0)
        return []

    monkeypatch.setattr(model_disk_cache.ModelDiskCache, "prune", slow_prune)
    picker = gui_picker.PickerWindow()
    try:
        before = time.perf_counter()
        picker._ensure_model_cache()
        elapsed = time.perf_counter() - before
        assert elapsed < 1.0
        assert started.wait(1.0)
        assert picker._model_cache_prune_worker.isRunning()
        release.set()
        assert picker._model_cache_prune_worker.wait(2000)
        qt_app.processEvents()
    finally:
        release.set()
        _close_picker(picker, qt_app)


def test_available_preflight_profile_is_cached_and_skips_second_fetch(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    picker = gui_picker.PickerWindow()
    when = datetime(2026, 8, 10, 0)
    fetched = SimpleNamespace(
        profile=object(), station_id="72357", provider="uwyo"
    )
    key = picker._observed_cache_key("72357", when)
    picker._observed_profile_cache[key] = (
        fetched, "Available (80 levels)", "72357 — Norman"
    )
    displayed = []
    monkeypatch.setattr(
        picker,
        "_display_prefetched_observation",
        lambda payload, sid, valid: displayed.append((payload, sid, valid)),
    )

    class UnexpectedFetchWorker:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("availability cache should avoid a second fetch")

    monkeypatch.setattr(gui_picker, "_FetchWorker", UnexpectedFetchWorker)
    try:
        picker._start_fetch("72357", when)
        assert displayed == [(fetched, "72357", when)]
        assert picker._worker is None
    finally:
        _close_picker(picker, qt_app)


def test_availability_worker_emits_the_decoded_profile(qt_app, monkeypatch):
    when = datetime(2026, 8, 10, 0)
    profile = SimpleNamespace(
        pres=[1000.0 - 50.0 * index for index in range(18)],
        tmpc=[20.0 - index for index in range(18)],
        dwpc=[15.0 - index for index in range(18)],
        wspd=[10.0 + index for index in range(18)],
    )
    decoder = SimpleNamespace(
        resolve_station=lambda _sid: SimpleNamespace(id="72357", name="Norman"),
        fetch=lambda _sid, _when: profile,
    )

    class LookupError(Exception):
        pass

    class UWyoError(Exception):
        pass

    monkeypatch.setattr(
        gui_workers,
        "_uwyo_decoder_classes",
        lambda: (LookupError, object, UWyoError),
    )
    monkeypatch.setattr(
        gui_workers,
        "_decoder_for_station",
        lambda _station: (decoder, "72357"),
    )
    worker = gui_workers._AvailabilityWorker("72357", when, 1)
    results = []
    worker.checked.connect(lambda *args: results.append(args))

    worker.run()

    assert len(results) == 1
    query, valid, status, _message, _label, fetched = results[0]
    assert (query, valid, status) == ("72357", when, AVAIL_AVAILABLE)
    assert fetched.profile is profile
    assert fetched.station_id == "72357"


def test_availability_result_handler_retains_only_current_usable_profile():
    when = datetime(2026, 8, 10, 0)
    indicator = SimpleNamespace(calls=[])
    indicator.set_status = lambda *args: indicator.calls.append(args)
    worker = SimpleNamespace(token=9)
    fetched = SimpleNamespace(profile=object(), station_id="72357")
    owner = SimpleNamespace(
        sender=lambda: worker,
        _avail_pending={9: indicator},
        _avail_workers=[worker],
        _avail_latest={id(indicator): 9},
        _observed_profile_cache={},
        _observed_cache_key=gui_picker.PickerWindow._observed_cache_key,
    )

    gui_picker.PickerWindow._on_availability_checked(
        owner,
        "72357",
        when,
        AVAIL_AVAILABLE,
        "Available (80 levels)",
        "72357 — Norman",
        fetched,
    )

    key = gui_picker.PickerWindow._observed_cache_key("72357", when)
    assert owner._observed_profile_cache[key][0] is fetched
    assert indicator.calls == [
        (AVAIL_AVAILABLE, "Available (80 levels)", "72357 — Norman")
    ]
