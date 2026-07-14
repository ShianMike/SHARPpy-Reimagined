"""Availability-aware forecast-model picker behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QApplication, QComboBox, QDateEdit, QPushButton

from sharpmod import gui, gui_picker


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_hourly_model_candidates_walk_back_one_cycle_at_a_time():
    requested = datetime(2026, 7, 14, 5, tzinfo=timezone.utc)

    assert gui._model_probe_candidates("hrrr", requested, limit=4) == [
        datetime(2026, 7, 14, 5, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 4, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 3, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 2, tzinfo=timezone.utc),
    ]


def test_six_hourly_model_candidates_cross_midnight():
    requested = datetime(2026, 7, 14, 0, tzinfo=timezone.utc)

    assert gui._model_probe_candidates("gfs", requested, limit=4) == [
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 18, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 6, tzinfo=timezone.utc),
    ]


def test_model_availability_worker_offers_first_earlier_cycle(
        qt_app, monkeypatch):
    requested = datetime(2026, 7, 14, 5, tzinfo=timezone.utc)
    calls = []

    def fake_probe(model, run_time, fxx, member, open_subset=False):
        calls.append(run_time)
        return {"available": len(calls) == 2}

    from sharpmod.tools import model_extract
    monkeypatch.setattr(model_extract, "probe", fake_probe)
    results = []
    worker = gui._ModelAvailabilityWorker(
        "hrrr", requested, 3, None, token=7)
    worker.checked.connect(lambda *args: results.append(args))

    worker.run()

    assert calls == [requested, requested.replace(hour=4)]
    assert len(results) == 1
    token, model, selected, fxx, member, status, message, available = results[0]
    assert (token, model, selected, fxx, member) == (
        7, "hrrr", requested, 3, None)
    assert status == gui.AVAIL_FALLBACK
    assert available == requested.replace(hour=4)
    assert "04Z" in message


def test_model_availability_worker_keeps_manual_fetch_when_probe_is_uncertain(
        qt_app, monkeypatch):
    requested = datetime(2026, 7, 14, 0, tzinfo=timezone.utc)

    def fake_probe(*_args, **_kwargs):
        return {"available": False, "error": "catalog timeout"}

    from sharpmod.tools import model_extract
    monkeypatch.setattr(model_extract, "probe", fake_probe)
    results = []
    worker = gui._ModelAvailabilityWorker(
        "gfs", requested, 0, None, token=8)
    worker.checked.connect(lambda *args: results.append(args))

    worker.run()

    assert results[0][5] == gui.AVAIL_UNKNOWN
    assert results[0][7] is None
    assert "Fetch remains available" in results[0][6]


class _RecordingIndicator:
    def __init__(self):
        self.calls = []

    def set_status(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _FakeSignal:
    def connect(self, _slot):
        pass


def test_availability_preflights_native_runtime_before_worker_start(
        monkeypatch):
    requested = datetime(2026, 7, 14, 6, tzinfo=timezone.utc)
    events = []

    from sharpmod.tools import model_extract
    monkeypatch.setattr(
        model_extract, "require_runtime_dependencies",
        lambda: events.append("runtime"))

    class FakeWorker:
        def __init__(self, *_args, **_kwargs):
            events.append("construct")
            self.checked = _FakeSignal()
            self.finished = _FakeSignal()

        def start(self):
            events.append("start")

    monkeypatch.setattr(gui_picker, "_ModelAvailabilityWorker", FakeWorker)
    owner = SimpleNamespace(
        _model_availability_request=("gfs", requested, 0, None),
        _model_availability_token=9,
        _model_availability_workers=[],
        _on_model_availability_checked=lambda *_args: None,
        _on_model_availability_finished=lambda: None,
    )

    gui.PickerWindow._run_model_availability(owner)

    assert events == ["runtime", "construct", "start"]


def test_availability_runtime_failure_stays_advisory(monkeypatch):
    requested = datetime(2026, 7, 14, 6, tzinfo=timezone.utc)
    indicator = _RecordingIndicator()

    from sharpmod.tools import model_extract

    def fail_preflight():
        raise model_extract.RetrievalError("missing native runtime")

    monkeypatch.setattr(
        model_extract, "require_runtime_dependencies", fail_preflight)

    def unexpected_worker(*_args, **_kwargs):
        pytest.fail("availability worker started after failed preflight")

    monkeypatch.setattr(
        gui_picker, "_ModelAvailabilityWorker", unexpected_worker)
    owner = SimpleNamespace(
        _model_availability_request=("gfs", requested, 0, None),
        _model_availability_token=10,
        _model_availability_workers=[],
        _model_availability=indicator,
    )

    gui.PickerWindow._run_model_availability(owner)

    assert indicator.calls[-1][0][0] == gui.AVAIL_UNKNOWN
    assert "Fetch remains available" in indicator.calls[-1][0][1]
    assert owner._model_availability_workers == []


def test_stale_model_availability_result_is_ignored(qt_app):
    requested = datetime(2026, 7, 14, 6, tzinfo=timezone.utc)
    indicator = _RecordingIndicator()
    button = QPushButton()
    owner = SimpleNamespace(
        _model_availability_token=12,
        _model_availability_request=("gfs", requested, 0, None),
        _model_availability=indicator,
        _model_use_available_btn=button,
        _model_available_run=None,
    )

    gui.PickerWindow._on_model_availability_checked(
        owner, 11, "gfs", requested, 0, None, gui.AVAIL_FALLBACK,
        "Earlier cycle available", requested.replace(day=13, hour=18))

    assert indicator.calls == []
    assert owner._model_available_run is None
    assert not button.isVisible()


def test_matching_fallback_result_exposes_explicit_cycle_button(qt_app):
    requested = datetime(2026, 7, 14, 6, tzinfo=timezone.utc)
    fallback = requested.replace(hour=0)
    indicator = _RecordingIndicator()
    button = QPushButton()
    owner = SimpleNamespace(
        _model_availability_token=12,
        _model_availability_request=("gfs", requested, 0, None),
        _model_availability=indicator,
        _model_use_available_btn=button,
        _model_available_run=None,
    )

    gui.PickerWindow._on_model_availability_checked(
        owner, 12, "gfs", requested, 0, None, gui.AVAIL_FALLBACK,
        "Earlier cycle available", fallback)

    assert indicator.calls[-1][0][:2] == (
        gui.AVAIL_FALLBACK, "Earlier cycle available")
    assert owner._model_available_run == fallback
    assert not button.isHidden()
    assert "00Z" in button.text()


def test_use_available_cycle_changes_date_and_cycle_explicitly(qt_app):
    date = QDateEdit()
    cycle = QComboBox()
    for hour in (0, 6, 12, 18):
        cycle.addItem(f"{hour:02d}Z", hour)
    fallback = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)
    owner = SimpleNamespace(
        _model_available_run=fallback,
        _model_date=date,
        _model_cycle=cycle,
    )

    gui.PickerWindow._use_model_available_run(owner)

    selected = date.date()
    assert (selected.year(), selected.month(), selected.day()) == (2026, 7, 13)
    assert cycle.currentData() == 18


def test_picker_rechecks_inventory_inputs_but_not_point_coordinates(qt_app):
    picker = gui.PickerWindow()
    try:
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()

        token = picker._model_availability_token
        picker._model_date.setDate(picker._model_date.date().addDays(-1))
        assert picker._model_availability_token > token

        token = picker._model_availability_token
        if picker._model_fxx_combo.count() > 1:
            picker._model_fxx_combo.setCurrentIndex(1)
            assert picker._model_availability_token > token

        token = picker._model_availability_token
        picker._model_member.setText("p01")
        assert picker._model_availability_token > token

        picker._model_availability_timer.stop()
        token = picker._model_availability_token
        picker._model_lat.setValue(picker._model_lat.value() + 0.25)
        assert picker._model_availability_token == token
    finally:
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()
        picker.close()
