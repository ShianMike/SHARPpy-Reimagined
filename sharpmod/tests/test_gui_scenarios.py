"""Focused UI checks for saved sensitivity scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from sharppy.sharptab import prof_collection, profile

from sharpmod.gui_scenarios import ScenarioWorkspace


VALID = datetime(2026, 9, 13, 0, tzinfo=timezone.utc)


def _collection(location="KOUN"):
    sounding = profile.create_profile(
        profile="raw",
        pres=[1000.0, 950.0, 900.0, 800.0],
        hght=[100.0, 500.0, 1000.0, 2000.0],
        tmpc=[25.0, 21.0, 17.0, 9.0],
        dwpc=[18.0, 15.0, 11.0, 1.0],
        wdir=[180.0, 190.0, 210.0, 230.0],
        wspd=[10.0, 15.0, 25.0, 35.0],
        location=location,
        date=VALID,
        latitude=35.2,
        missing=-9999.0,
    )
    sounding.srwind = (20.0, 5.0, -10.0, 8.0)
    collection = prof_collection.ProfCollection({"": [sounding]}, [VALID])
    collection.setMeta("loc", location)
    collection.setMeta("model", "Observed")
    return collection


class _Engine:
    def values(self, sounding, keys):
        known = {
            "mlcape": float(sounding.tmpc[0]) * 100.0,
            "mlcin": -25.0,
            "ml_lcl": 900.0,
            "shear_6km": 35.0,
            "srh_1km": 100.0,
            "stp_cin": 1.0,
        }
        return {key: known.get(key) for key in keys}


def _wait(qt_app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert predicate()


def test_named_scenario_edits_open_separately_and_preserve_baseline(qt_app):
    focused = [_collection()]
    opened = []
    widget = ScenarioWorkspace(
        _Engine(),
        collection_provider=lambda: focused[0],
        open_collection=opened.append,
    )
    try:
        widget.refresh()
        original = widget.book.baseline
        widget._new()
        widget.name.setText("Warm surface")
        widget.surface_temperature[0].setChecked(True)
        widget.surface_temperature[1].setValue(2.0)
        widget._apply()
        _wait(qt_app, lambda: widget._metric_worker is None)

        assert widget.book.baseline == original
        assert widget.book.scenarios[0].name == "Warm surface"
        assert "surface temperature +2" in widget.perturbations.item(0, 0).text()
        assert widget.metrics.item(0, 3).text() == "+200.0"

        widget._open_selected()
        assert len(opened) == 1
        assert opened[0].getMeta("scenario_hypothetical") is True
        assert float(opened[0]._profs["scenario"][0].tmpc[0]) == 27.0

        focused[0] = _collection("KTLX")
        widget.refresh()
        assert widget.book.baseline == original
        assert "differs from this scenario book" in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()


def test_sensitivity_cell_creates_and_opens_hypothetical(qt_app):
    collection = _collection()
    opened = []
    widget = ScenarioWorkspace(
        _Engine(),
        collection_provider=lambda: collection,
        open_collection=opened.append,
    )
    try:
        widget.refresh()
        for spin in (widget.t_min, widget.t_max, widget.td_min, widget.td_max):
            spin.setValue(1.0)
        widget._run_sensitivity()
        _wait(qt_app, lambda: widget._sensitivity_worker is None)
        assert widget.grid_table.rowCount() == 1
        assert widget.grid_table.columnCount() == 1
        assert widget.grid_table.item(0, 0).text() == "+100.0"

        widget._grid_clicked(0, 0)
        _wait(qt_app, lambda: widget._metric_worker is None)
        assert len(widget.book.scenarios) == 1
        assert len(opened) == 1
        assert "hypothetical" in opened[0].getMeta("loc")
    finally:
        widget.shutdown()
        widget.close()


def test_scenario_book_survives_workspace_session_round_trip(qt_app):
    collection = _collection()
    first = ScenarioWorkspace(
        _Engine(), collection_provider=lambda: collection, open_collection=lambda _value: None
    )
    second = ScenarioWorkspace(
        _Engine(), collection_provider=lambda: collection, open_collection=lambda _value: None
    )
    try:
        first.refresh()
        first._new()
        first.name.setText("Session scenario")
        first._apply()
        state = first.session_state()
        second.restore_session_state(state)
        _wait(qt_app, lambda: second._metric_worker is None)
        assert [item.name for item in second.book.scenarios] == ["Session scenario"]
        assert second.scenario.currentText() == "Session scenario"
    finally:
        first.shutdown()
        second.shutdown()
        first.close()
        second.close()
