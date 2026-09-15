"""UI workflow checks for forecast verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time

import numpy as np
from qtpy.QtCore import Qt

from sharpmod.gui_verification import VerificationWorkspace


VALID = datetime(2026, 5, 20, 18, tzinfo=timezone.utc)
RUN = datetime(2026, 5, 20, 12, tzinfo=timezone.utc)


class _Collection:
    def __init__(self, sounding, **metadata):
        self._dates = [VALID]
        self._prof_idx = 0
        self._highlight = ""
        self._profs = {"": [sounding]}
        self._meta = metadata

    def getMeta(self, key):  # noqa: N802 - profile collection API
        return self._meta[key]


def _profile(error=0.0):
    return SimpleNamespace(
        pres=np.array([1000.0, 900.0, 800.0]),
        hght=np.array([100.0, 600.0, 1100.0]),
        tmpc=np.array([20.0, 16.0, 12.0]) + error,
        dwpc=np.array([15.0, 11.0, 7.0]) + error,
        u=np.array([10.0, 20.0, 30.0]) + error,
        v=np.array([5.0, 10.0, 15.0]) + error,
        metrics={"mlcape": 1000.0 + 100.0 * error, "shear_6km": 35.0 + error},
        latitude=35.22,
    )


def _forecast(**metadata):
    return _Collection(
        _profile(2.0),
        model="HRRR",
        run=RUN,
        fxx=6,
        selected_lat=35.22,
        selected_lon=-97.44,
        terrain_elevation_m=100.0,
        **metadata,
    )


def _observation(**metadata):
    return _Collection(
        _profile(),
        model="Observed",
        observed=True,
        source_station="KOUN",
        selected_lat=35.22,
        selected_lon=-97.44,
        terrain_elevation_m=100.0,
        **metadata,
    )


class _Engine:
    def values(self, sounding, keys):
        return {key: sounding.metrics.get(key) for key in keys}


def _wait(qt_app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert predicate()


def test_verification_pair_renders_vertical_diagnostics_and_aggregate(qt_app):
    collections = [_forecast(), _observation(), _forecast(analysis=True)]
    requested = []
    widget = VerificationWorkspace(
        _Engine(),
        collections_provider=lambda: tuple(collections),
        request_observation=lambda: requested.append(True),
    )
    try:
        widget.refresh()
        assert widget.forecast.count() == 1
        assert widget.observation.count() == 1
        assert "analysis 1" in widget.classifications.text()
        widget.station.setText("KOUN")
        widget._verify()
        _wait(qt_app, lambda: widget._worker is None)

        assert len(widget.pairs) == 1
        assert widget.pairs[0].matched
        assert widget.vertical.rowCount() == 3
        assert widget.vertical.item(0, 3).text() == "2.0"
        assert widget.diagnostics.rowCount() == 2
        assert widget.aggregates.rowCount() == 6
        assert widget.aggregates.item(0, 2).text() == "3"
        assert widget.cases.item(0, 0).checkState() == Qt.Checked

        widget._request_observed()
        assert requested == [True]
    finally:
        widget.shutdown()
        widget.close()


def test_rejected_case_is_retained_and_session_round_trips(qt_app):
    observation = _observation()
    observation._dates[0] = VALID + timedelta(hours=1)
    collections = [_forecast(), observation]
    first = VerificationWorkspace(
        _Engine(), collections_provider=lambda: tuple(collections)
    )
    second = VerificationWorkspace(
        _Engine(), collections_provider=lambda: tuple(collections)
    )
    try:
        first.refresh()
        first._verify()
        _wait(qt_app, lambda: first._worker is None)
        assert not first.pairs[0].matched
        assert first.cases.item(0, 6).text() == "Rejected"
        assert first.aggregates.item(0, 7).text() == "1"

        second.restore_session_state(first.session_state())
        assert len(second.pairs) == 1
        assert second.cases.item(0, 6).text() == "Rejected"
        assert "valid times" in second.pairs[0].rejection_reasons[0]
    finally:
        first.shutdown()
        second.shutdown()
        first.close()
        second.close()
