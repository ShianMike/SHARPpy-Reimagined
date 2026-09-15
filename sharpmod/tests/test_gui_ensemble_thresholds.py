"""UI contract checks for the ensemble threshold explorer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import time

from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
from sharpmod.gui_ensemble_thresholds import EnsembleThresholdExplorer


VALID = datetime(2026, 5, 20, 18, tzinfo=timezone.utc)


class _Collection:
    def __init__(self):
        self._dates = [VALID]
        self._profs = {
            "c00": [SimpleNamespace(values={"mlcape": 1800.0})],
            "p01": [SimpleNamespace(values={"mlcape": 600.0})],
            "p02": [SimpleNamespace(values={"mlcape": None})],
        }
        self._meta = {}

    def getCurrentDate(self):  # noqa: N802 - profile collection API
        return VALID

    def getMeta(self, key):  # noqa: N802 - profile collection API
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802 - profile collection API
        self._meta[key] = value


class _Engine:
    def values(self, profile, keys):
        return {key: profile.values.get(key) for key in keys}


def _wait(qt_app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    qt_app.processEvents()
    assert predicate()


def test_explorer_renders_member_outcomes_and_requested_denominator(qt_app):
    collection = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "not found"),),
    ).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)

        assert widget.timeline.rowCount() == 1
        assert widget.timeline.item(0, 1).text() == "1"
        assert widget.timeline.item(0, 2).text() == "2"
        assert widget.timeline.item(0, 3).text() == "3"
        assert widget.timeline.item(0, 4).text() == "4"
        outcomes = [widget.members.item(row, 1).text() for row in range(4)]
        assert outcomes == [
            "Qualifies",
            "Does not qualify",
            "Missing / unusable",
            "Missing / unusable",
        ]
        assert "1/2 usable members qualify" in widget.status.text()
        assert "3/4 loaded" in widget.status.text()

        activated = []
        widget.memberActivated.connect(lambda member, when: activated.append((member, when)))
        widget._member_double_clicked(1, 0)
        assert activated == [("p01", VALID)]
    finally:
        widget.shutdown()
        widget.close()


def test_explorer_definition_session_round_trip_and_nonensemble_disable(qt_app):
    first = EnsembleThresholdExplorer(_Engine())
    second = EnsembleThresholdExplorer(_Engine())
    try:
        first.name.setText("CAPE gate")
        first.condition_rows[0][3].setValue(1250.0)
        state = first.session_state()
        second.restore_session_state(state)
        assert second.name.text() == "CAPE gate"
        assert second.condition_rows[0][3].value() == 1250.0

        second.refresh(SimpleNamespace(_profs={"only": [object()]}, _meta={}))
        assert not second.evaluate_current.isEnabled()
        assert "not a multi-member ensemble" in second.status.text()
    finally:
        first.close()
        second.close()


def test_joint_threshold_defaults_are_distinct_supported_diagnostics(qt_app):
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        assert [row[1].currentData() for row in widget.condition_rows] == [
            "mlcape",
            "shear_6km",
            "srh_3km",
        ]
        for enabled, _metric, _operator, _value, _upper in widget.condition_rows:
            enabled.setChecked(True)

        assert widget.definition().metrics == (
            "mlcape",
            "shear_6km",
            "srh_3km",
        )
    finally:
        widget.close()
