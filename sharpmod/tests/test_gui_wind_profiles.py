"""UI checks for wind-only VAD/VWP analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np

from sharpmod.gui_wind_profiles import WindProfileWorkspace
from sharpmod.wind_profiles import ObservedWindProfile, read_wind_profile_csv


FIXTURE = Path(__file__).parent / "data" / "klot_vwp_20260913_0146.csv"


class _Collection:
    def __init__(self, sounding):
        self._profs = {"": [sounding]}
        self._highlight = ""
        self._prof_idx = 0
        self._dates = [datetime(2026, 9, 13, 2, tzinfo=timezone.utc)]
        self._meta = {"model": "HRRR"}


def _model(observed):
    heights = np.asarray([item.height_msl_m for item in observed.levels])
    return SimpleNamespace(
        hght=heights,
        u=np.asarray([item.u_kt for item in observed.levels]) + 2.0,
        v=np.asarray([item.v_kt for item in observed.levels]) - 3.0,
    )


def _wait(qt_app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert predicate()


def test_wind_profile_overlay_differences_layer_and_session(qt_app):
    observed = read_wind_profile_csv(FIXTURE)
    collection = _Collection(_model(observed))
    first = WindProfileWorkspace(collections_provider=lambda: (collection,))
    second = WindProfileWorkspace(collections_provider=lambda: (collection,))
    try:
        first._append((observed,))
        first.refresh()
        assert first.differences.rowCount() == 20
        assert first.differences.item(0, 6).text() == "+2.0"
        assert first.differences.item(0, 7).text() == "-3.0"
        assert "coverage" in first.info.text()

        first.top.setValue(2800.0)
        first.use_storm.setChecked(True)
        first.storm_u.setValue(20.0)
        first.storm_v.setValue(10.0)
        first.storm_source.setText("Bunkers RM from HRRR")
        first._layer()
        assert "storm motion source: Bunkers RM from HRRR" in first.layer_result.text()
        assert "SRH —" not in first.layer_result.text()

        second.restore_session_state(first.session_state())
        second.refresh()
        assert len(second.profiles) == 1
        assert second.profile_combo.currentText().startswith("KLOT")
        assert second.storm_source.text() == "Bunkers RM from HRRR"
    finally:
        first.shutdown()
        second.shutdown()
        first.close()
        second.close()


def test_latest_retrieval_runs_off_thread_and_keeps_actual_timestamp(
    qt_app, monkeypatch
):
    observed = read_wind_profile_csv(FIXTURE)
    monkeypatch.setattr(
        "sharpmod.gui_wind_profiles.fetch_recent_vwp",
        lambda _radar, *, cancel: observed,
    )
    widget = WindProfileWorkspace(collections_provider=lambda: ())
    try:
        widget.radar.setText("KLOT")
        widget._retrieve()
        _wait(qt_app, lambda: widget._worker is None)
        assert widget.profiles == (observed,)
        assert "2026-09-13 01:46:22Z" in widget.info.text()
        assert widget.export_button.isEnabled()
    finally:
        widget.shutdown()
        widget.close()


def test_latest_retrieval_exposes_cooperative_cancel(qt_app, monkeypatch):
    from sharpmod.wind_profiles import WindProfileCancelled

    def _cancelled(_radar, *, cancel):
        while not cancel():
            QThread.msleep(1)
        raise WindProfileCancelled("cancelled")

    from qtpy.QtCore import QThread

    monkeypatch.setattr("sharpmod.gui_wind_profiles.fetch_recent_vwp", _cancelled)
    widget = WindProfileWorkspace(collections_provider=lambda: ())
    try:
        widget._retrieve()
        _wait(qt_app, lambda: widget.cancel_retrieve.isEnabled())
        widget.cancel_retrieve.click()
        _wait(qt_app, lambda: widget._worker is None)
        assert "cancelled" in widget.info.text().lower()
        assert widget.retrieve.isEnabled()
    finally:
        widget.shutdown()
        widget.close()


def test_sparse_coverage_cannot_render_complete_layer_diagnostics(qt_app):
    source = read_wind_profile_csv(FIXTURE)
    sparse = ObservedWindProfile.from_dict(
        {**source.as_dict(), "levels": [item.as_dict() for item in source.levels[:2]]}
    )
    widget = WindProfileWorkspace(collections_provider=lambda: ())
    try:
        widget._append((sparse,))
        widget.bottom.setValue(0.0)
        widget.top.setValue(6000.0)
        widget._layer()
        assert "shear — kt" in widget.layer_result.text()
        assert "coverage or quality" in widget.layer_result.text()
    finally:
        widget.close()
