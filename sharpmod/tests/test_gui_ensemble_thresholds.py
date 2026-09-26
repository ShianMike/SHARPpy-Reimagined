"""UI contract checks for the ensemble threshold explorer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import threading
import time

from qtpy.QtWidgets import QScrollArea

from sharpmod import gui_theme
from sharpmod import gui_ensemble_thresholds as threshold_ui
from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
from sharpmod.ensemble_thresholds import evaluate_threshold
from sharpmod.gui_common import action_label
from sharpmod.gui_ensemble_thresholds import EnsembleThresholdExplorer
from sharpmod.gui_jobs import JobStatus
from sharpmod.theme import OBJ_WARNING_TEXT


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
        # Funnel order, widest denominator first: each count is a subset of the one
        # before it, and the previous order read outwards from the narrowest.
        assert [
            widget.timeline.horizontalHeaderItem(column).text()
            for column in range(widget.timeline.columnCount())
        ] == [
            "Valid time",
            "Requested",
            "Loaded",
            "Usable",
            "Qualifying",
            "Nonqualifying",
            "Agreement (qualifying / usable)",
            "Coverage (usable / requested)",
        ]
        assert widget.timeline.item(0, 1).text() == "4"
        assert widget.timeline.item(0, 2).text() == "3"
        assert widget.timeline.item(0, 3).text() == "2"
        assert widget.timeline.item(0, 4).text() == "1"
        # Nonqualifying was previously only reachable by subtracting.
        assert widget.timeline.item(0, 5).text() == "1"
        # No cell on this row is a percentage without its own denominator.
        assert widget.timeline.item(0, 6).text() == "1/2 (50%)"
        assert widget.timeline.item(0, 7).text() == "2/4 (50%)"

        outcomes = [widget.members.item(row, 1).text() for row in range(4)]
        # Four different problems with four different remedies, no longer collapsed
        # into one "Missing / unusable".
        assert outcomes == [
            "Qualifies",
            "Does not qualify",
            "Diagnostic unavailable",
            "Not loaded",
        ]
        assert widget.members.horizontalHeaderItem(2).text() == "Reason"
        assert widget.members.item(2, 2).text() == "no value for mlcape"
        assert widget.members.item(3, 2).text() == "member was not loaded"
        assert widget.members.horizontalHeaderItem(3).text() == "MLCAPE (J/kg)"
        assert widget.members.item(0, 3).text() == "1800"

        text = widget.status.text()
        assert "1/2 (50%) usable members qualify" in text
        assert "2/4 (50%) of the requested ensemble was usable" in text
        assert "3/4 (75%) loaded" in text
        assert "1 never loaded" in text
        assert "1 are missing a required diagnostic" in text
        assert text.startswith("Warning: ")
        assert widget.status.objectName() == OBJ_WARNING_TEXT
        assert widget.status.accessibleName() == "Warning message"
        assert widget.status.property("statusLevel") == "warn"

        activated = []
        widget.memberActivated.connect(lambda member, when: activated.append((member, when)))
        widget._member_double_clicked(1, 0)
        assert activated == [("p01", VALID)]
    finally:
        widget.shutdown()
        widget.close()


def test_counts_reconcile_and_each_one_filters_the_member_list(qt_app):
    """A count a reader cannot act on is only trivia."""
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
        result = widget._selected_result()
        assert result.reconciles
        assert widget.members.rowCount() == 4
        assert "4 of 4 requested members" in widget.member_count.text()

        # Selecting the Qualifying count narrows the list to the members behind it.
        widget._timeline_cell_clicked(0, 4)
        assert widget.member_filter.currentText() == "Qualifying"
        assert widget.members.rowCount() == 1
        assert widget.members.item(0, 0).text() == "c00"
        assert "1 of 4 requested members" in widget.member_count.text()

        widget._timeline_cell_clicked(0, 5)
        assert widget.member_filter.currentText() == "Does not qualify"
        assert [widget.members.item(0, 0).text()] == ["p01"]

        widget._timeline_cell_clicked(0, 3)
        assert widget.member_filter.currentText() == "Usable"
        assert widget.members.rowCount() == 2

        widget._timeline_cell_clicked(0, 2)
        assert widget.member_filter.currentText() == "Loaded"
        assert widget.members.rowCount() == 3

        widget._timeline_cell_clicked(0, 1)
        assert widget.member_filter.currentText() == "All requested members"
        assert widget.members.rowCount() == 4

        # Every filtered subtotal adds back up to the requested ensemble, so the
        # list under a count and the count itself cannot disagree.
        totals = {}
        for index in range(widget.member_filter.count()):
            widget.member_filter.setCurrentIndex(index)
            totals[widget.member_filter.currentText()] = widget.members.rowCount()
        assert (
            totals["Qualifying"]
            + totals["Does not qualify"]
            + totals["Loaded but unusable"]
            + totals["Not loaded"]
        ) == totals["All requested members"] == 4
    finally:
        widget.shutdown()
        widget.close()


def test_a_member_can_be_opened_without_a_mouse_and_never_when_unloaded(qt_app):
    collection = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "not found"),),
    ).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    activated = []
    widget.memberActivated.connect(lambda member, when: activated.append((member, when)))
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)
        # Double-click was the only way in, which no keyboard reader can reach.
        assert not widget.open_member.isEnabled()
        widget.members.selectRow(0)
        assert widget.open_member.isEnabled()
        widget._open_selected_member()
        assert activated == [("c00", VALID)]

        # A member that never downloaded has no profile to show, so the action
        # explains that instead of failing after the fact.
        widget.members.selectRow(3)
        widget._open_selected_member()
        assert activated == [("c00", VALID)]
        assert "cannot be opened" in widget.status.text()
        assert "not loaded" in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()


def test_threshold_controls_follow_the_chosen_diagnostic(qt_app):
    """Bounds, step, and precision come from the diagnostic, not one shared range."""
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        _enabled, metric, _operator, value, upper = widget.condition_rows[0]
        assert metric.currentData() == "mlcape"
        assert value.suffix() == " J/kg"
        assert value.decimals() == 0
        assert value.singleStep() == 100.0
        assert (value.minimum(), value.maximum()) == (-1000.0, 8000.0)
        assert value.value() == 1000.0

        value.setValue(2500.0)
        # Switching diagnostic used to leave the number in place, so 2500 J/kg
        # silently became 2500 kt in a control still accepting +/-100000.
        metric.setCurrentIndex(metric.findData("stp_cin"))
        assert value.suffix() == ""
        assert value.decimals() == 2
        assert value.singleStep() == 0.25
        assert (value.minimum(), value.maximum()) == (-5.0, 20.0)
        assert value.value() == 2.5

        # The upper bound of a between rule scales with the diagnostic too; a
        # fixed +500 was meaningless for an index that tops out near ten.
        assert upper.value() == 5.0

        value.setValue(3.0)
        metric.setCurrentIndex(metric.findData("mlcape"))
        # The CAPE threshold comes back, rather than 3 J/kg.
        assert value.value() == 2500.0
        metric.setCurrentIndex(metric.findData("stp_cin"))
        assert value.value() == 3.0
    finally:
        widget.close()


def test_remembered_thresholds_and_filter_survive_a_session_round_trip(qt_app):
    first = EnsembleThresholdExplorer(_Engine())
    second = EnsembleThresholdExplorer(_Engine())
    try:
        _enabled, metric, _operator, value, _upper = first.condition_rows[0]
        value.setValue(2500.0)
        metric.setCurrentIndex(metric.findData("srh_1km"))
        value.setValue(275.0)
        first.member_filter.setCurrentIndex(
            first.member_filter.findText("Not loaded")
        )
        state = first.session_state()
        assert state["metric_values"]["mlcape"] == 2500.0
        assert state["metric_values"]["srh_1km"] == 275.0

        second.restore_session_state(state)
        assert second.member_filter.currentText() == "Not loaded"
        _enabled, metric, _operator, value, _upper = second.condition_rows[0]
        assert metric.currentData() == "srh_1km"
        assert value.value() == 275.0
        # The diagnostic not in the restored definition kept its threshold too.
        metric.setCurrentIndex(metric.findData("mlcape"))
        assert value.value() == 2500.0
    finally:
        first.close()
        second.close()


def test_the_rule_is_restated_in_words(qt_app):
    """A definition was identified only by the name someone typed."""
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        assert widget.definition_summary.text() == (
            "This rule tests: MLCAPE \u2265 1000 J/kg"
        )
        _enabled, metric, operator, value, _upper = widget.condition_rows[0]
        operator.setCurrentIndex(operator.findData("<"))
        value.setValue(500.0)
        assert widget.definition_summary.text() == (
            "This rule tests: MLCAPE < 500 J/kg"
        )
        widget.condition_rows[1][0].setChecked(True)
        assert " and 0-6 km shear \u2265 35 kt" in widget.definition_summary.text()
    finally:
        widget.close()


def test_status_describes_the_selected_valid_time(qt_app):
    """The status only ever described the first result."""
    collection = _Collection()
    collection._dates = [VALID, VALID.replace(hour=19)]
    for member, values in (
        ("c00", [1800.0, 400.0]),
        ("p01", [600.0, 500.0]),
        ("p02", [None, 2000.0]),
    ):
        collection._profs[member] = [
            SimpleNamespace(values={"mlcape": item}) for item in values
        ]
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.refresh(collection)
        widget._evaluate(True)
        _wait(qt_app, lambda: widget._worker is None)
        assert widget.timeline.rowCount() == 2
        assert "1/2 (50%) usable members qualify" in widget.status.text()

        widget.timeline.selectRow(1)
        # Every member is usable at the second time, and one of three qualifies.
        assert "1/3 (33%) usable members qualify" in widget.status.text()
        assert "3/3 (100%) of the requested ensemble was usable" in widget.status.text()
        assert widget.members.rowCount() == 3
    finally:
        widget.shutdown()
        widget.close()


def test_a_second_evaluation_replaces_the_in_progress_message(qt_app):
    """Row zero is already selected the second time, so no signal fires.

    The status was only refreshed from ``itemSelectionChanged``, so re-evaluating
    left "Evaluating members…" on screen above a finished result.
    """
    collection = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.refresh(collection)
        for _pass in range(2):
            widget._evaluate(False)
            _wait(qt_app, lambda: widget._worker is None)
            assert "Evaluating" not in widget.status.text()
            assert "usable members qualify" in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()


def test_a_retry_is_offered_only_for_members_that_could_be_refetched(qt_app):
    collection = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "not found"),),
    ).attach(collection)
    asked = []
    widget = EnsembleThresholdExplorer(
        _Engine(),
        request_retry=lambda coll, members: asked.append(tuple(members)) or True,
    )
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)
        # p02 is loaded but missing a diagnostic; refetching it would change
        # nothing, so only the member that never arrived is offered.
        assert widget._selected_result().retryable_members == ("p03",)
        assert widget.retry.isEnabled()
        widget._retry_unavailable()
        assert asked == [("p03",)]
        assert not widget.retry.isEnabled()
        assert "Retrying p03" in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()


def test_refresh_preserves_same_ensemble_but_clears_a_different_context(qt_app):
    first = _Collection()
    second = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(first)
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(second)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.refresh(first)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)
        assert widget.timeline.rowCount() == 1
        assert widget._selected_result() is not None

        widget.refresh(first)
        assert widget.timeline.rowCount() == 1

        widget.refresh(second)
        assert widget.timeline.rowCount() == 0
        assert widget.members.rowCount() == 0
        assert widget._selected_result() is None
        assert not widget.export.isEnabled()
        assert "Ready to evaluate" in widget.status.text()
        assert "3/3" in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()


def test_cancelling_one_evaluation_keeps_and_labels_the_partial_result(qt_app):
    collection = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)

    class _Slow(_Engine):
        def __init__(self):
            self.calls = 0

        def values(self, profile, keys):
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.05)
            return super().values(profile, keys)

    widget = EnsembleThresholdExplorer(_Slow())
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        widget.cancel_pending()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)
        result = widget._selected_result()
        assert result is not None
        assert result.cancelled
        assert result.reconciles
        assert "cancelled after" in widget.status.text()
        assert widget.status.property("statusLevel") in {"warn", "error"}
    finally:
        widget.shutdown()
        widget.close()


def test_cancel_rejects_a_queued_complete_result_and_reports_exact_job_counts(
    qt_app, monkeypatch
):
    """A success queued before the GUI handles Cancel must not replace retained data."""
    collection = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    entered = threading.Event()
    release = threading.Event()
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)
        retained = widget._results
        assert retained[0].qualifying_member_count == 1

        # The same live collection and rule can represent updated profile bytes.
        # A new Evaluate action therefore needs a new request identity even when
        # its user-facing key looks identical.
        collection._profs["p01"][0].values["mlcape"] = 1600.0
        queued_result = evaluate_threshold(collection, widget.definition(), _Engine())
        assert queued_result.qualifying_member_count == 2
        queued_calls = []

        def queued_complete(*_args, **_kwargs):
            queued_calls.append(True)
            entered.set()
            release.wait(2.0)
            return queued_result

        monkeypatch.setattr(threshold_ui, "evaluate_threshold", queued_complete)
        widget._evaluate(False)
        _wait(qt_app, entered.is_set)

        assert isinstance(widget.job, JobStatus)
        assert widget.job.snapshot.state == "running"
        assert widget.job.snapshot.total == 3
        assert "2026-05-20 18:00Z" in widget.job.snapshot.affected_input
        assert "MLCAPE" in widget.job.snapshot.affected_input
        assert "2026-05-20 18:00Z" in widget.job.retained_label.text()
        assert widget.timeline.rowCount() == 1

        widget.cancel_pending()
        release.set()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)

        assert widget._results == retained
        assert widget._selected_result().qualifying_member_count == 1
        assert widget.job.snapshot.state == "cancelled"
        assert widget.job.snapshot.counts.cancelled == 3
        assert widget.job.snapshot.counts.requested == 3
        assert widget.job.snapshot.retryable

        widget.job.retry_button.click()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)
        assert len(queued_calls) == 2
        assert widget._selected_result().qualifying_member_count == 2
    finally:
        release.set()
        widget.shutdown()
        widget.close()


def test_evaluation_retry_uses_saved_rule_and_only_missing_or_failed_members(qt_app):
    collection = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "not found"),),
    ).attach(collection)

    class RetryEngine(_Engine):
        def __init__(self):
            self.phase = 1
            self.calls = []
            self.failed_member_entered = threading.Event()
            self.release_failure = threading.Event()

        def values(self, profile, keys):
            value = profile.values["mlcape"]
            self.calls.append((self.phase, value))
            if self.phase == 1 and value == 600.0:
                self.failed_member_entered.set()
                self.release_failure.wait(2.0)
                raise RuntimeError("temporary metric failure")
            return super().values(profile, keys)

    engine = RetryEngine()
    widget = EnsembleThresholdExplorer(engine)
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, engine.failed_member_entered.is_set)
        widget.cancel_pending()
        engine.release_failure.set()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)

        first = widget._selected_result()
        assert [item.state for item in first.members] == [
            "qualifying",
            "failed",
            "not_evaluated",
            "not_loaded",
        ]
        assert widget.job.snapshot.counts.completed == 1
        assert widget.job.snapshot.counts.failed == 1
        assert widget.job.snapshot.counts.cancelled == 1
        assert widget.job.snapshot.counts.unavailable == 1
        assert widget.job.snapshot.retryable

        # Change the editor after the request. Retry must use the saved >= 1000 rule.
        widget.condition_rows[0][3].setValue(500.0)
        engine.phase = 2
        assert widget.job.retry_button is not widget.retry
        widget.job.retry_button.click()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)

        assert [value for phase, value in engine.calls if phase == 2] == [600.0, None]
        repaired = widget._selected_result()
        assert repaired.definition.conditions[0].value == 1000.0
        assert repaired.nonqualifying_members == ("p01",)
        assert repaired.cancelled is False
        assert widget.job.snapshot.counts.completed == 2
        assert widget.job.snapshot.counts.failed == 0
        assert widget.job.snapshot.counts.cancelled == 0
        assert widget.job.snapshot.counts.unavailable == 2
    finally:
        engine.release_failure.set()
        widget.shutdown()
        widget.close()


def test_new_collection_supersedes_a_running_threshold_request(qt_app, monkeypatch):
    first = _Collection()
    second = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(first)
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(second)
    widget = EnsembleThresholdExplorer(_Engine())
    result = evaluate_threshold(first, widget.definition(), _Engine())
    entered = threading.Event()
    release = threading.Event()

    def queued_complete(*_args, **_kwargs):
        entered.set()
        release.wait(2.0)
        return result

    monkeypatch.setattr(threshold_ui, "evaluate_threshold", queued_complete)
    try:
        widget.refresh(first)
        widget._evaluate(False)
        _wait(qt_app, entered.is_set)
        widget.refresh(second)
        release.set()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)

        assert widget._collection is second
        assert widget._results == ()
        assert widget.timeline.rowCount() == 0
        assert widget.job.snapshot.state == "superseded"
    finally:
        release.set()
        widget.shutdown()
        widget.close()


def test_worker_freezes_collection_containers_and_rule_at_start(qt_app, monkeypatch):
    collection = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    real_evaluate = threshold_ui.evaluate_threshold
    entered = threading.Event()
    release = threading.Event()
    captured = {}

    def delayed(snapshot, definition, engine, **kwargs):
        captured["snapshot"] = snapshot
        captured["definition"] = definition
        entered.set()
        release.wait(2.0)
        return real_evaluate(snapshot, definition, engine, **kwargs)

    monkeypatch.setattr(threshold_ui, "evaluate_threshold", delayed)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, entered.is_set)

        # Live collection containers and editor controls may change while the
        # worker runs; they are not the saved request.
        collection._dates.clear()
        collection._profs.pop("p01")
        collection._meta["loc"] = "changed after start"
        widget.condition_rows[0][3].setValue(500.0)
        release.set()
        _wait(qt_app, lambda: widget._worker is None, timeout=5.0)

        snapshot = captured["snapshot"]
        assert snapshot is not collection
        assert snapshot._dates == (VALID,)
        assert tuple(snapshot._profs) == ("c00", "p01", "p02")
        assert all(isinstance(items, tuple) for items in snapshot._profs.values())
        assert captured["definition"].conditions[0].value == 1000.0
        result = widget._selected_result()
        assert result.requested_member_count == 3
        assert result.valid_time == VALID
        assert result.definition.conditions[0].value == 1000.0
    finally:
        release.set()
        widget.shutdown()
        widget.close()


def test_rerun_preserves_selected_time_filter_scroll_focus_and_geometry(qt_app):
    collection = _Collection()
    collection._dates = [VALID.replace(hour=(18 + index) % 24) for index in range(8)]
    for member, base in (("c00", 1800.0), ("p01", 600.0), ("p02", 1200.0)):
        collection._profs[member] = [
            SimpleNamespace(values={"mlcape": base + index}) for index in range(8)
        ]
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.resize(760, 520)
        widget.show()
        widget.refresh(collection)
        widget._evaluate(True)
        _wait(qt_app, lambda: widget._worker is None)
        widget.timeline.selectRow(6)
        widget.member_filter.setCurrentIndex(3)
        widget.timeline.verticalScrollBar().setValue(
            widget.timeline.verticalScrollBar().maximum()
        )
        saved_scroll = widget.timeline.verticalScrollBar().value()
        saved_size = widget.size()
        selected_time = widget._selected_result().valid_time
        widget.member_filter.setFocus()
        qt_app.processEvents()
        assert widget.member_filter.hasFocus()

        widget._evaluate(True)
        _wait(qt_app, lambda: widget._worker is None)

        assert widget._selected_result().valid_time == selected_time
        assert widget.member_filter.currentIndex() == 3
        assert widget.timeline.verticalScrollBar().value() == saved_scroll
        assert widget.member_filter.hasFocus()
        assert widget.size() == saved_size
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
        assert [row[1].currentText() for row in widget.condition_rows] == [
            "MLCAPE (J/kg)",
            "0-6 km shear (kt)",
            "0-3 km SRH (m²/s²)",
        ]
        assert widget.cancel.text() == action_label("cancel")
        assert widget.export.text() == action_label("export_csv")
        assert widget.evaluate_current.shortcut().toString() == "Ctrl+Return"
        assert widget.timeline.accessibleName() == "Threshold coverage by valid time"
        assert widget.members.accessibleName() == (
            "Threshold outcome by ensemble member"
        )
        expected_suffixes = (" J/kg", " kt", " m²/s²")
        for controls, suffix in zip(widget.condition_rows, expected_suffixes):
            enabled, metric, operator, value, upper = controls
            assert enabled.accessibleName()
            assert metric.accessibleName()
            assert operator.accessibleName()
            assert value.suffix() == suffix
            assert upper.suffix() == suffix
        for enabled, _metric, _operator, _value, _upper in widget.condition_rows:
            enabled.setChecked(True)

        assert widget.definition().metrics == (
            "mlcape",
            "shear_6km",
            "srh_3km",
        )
    finally:
        widget.close()


def test_enlarged_text_keeps_threshold_and_member_rows_visible(qt_app):
    gui_theme.apply_theme(
        qt_app, color_style="standard", text_scale=200, density="comfortable"
    )
    collection = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "not found"),),
    ).attach(collection)
    widget = EnsembleThresholdExplorer(_Engine())
    try:
        widget.resize(900, 600)
        widget.show()
        widget.refresh(collection)
        widget._evaluate(False)
        _wait(qt_app, lambda: widget._worker is None)
        qt_app.processEvents()

        assert widget.condition_rows[0][0].text() == "Required"
        timeline_row = widget.timeline.visualItemRect(widget.timeline.item(0, 0))
        member_row = widget.members.visualItemRect(widget.members.item(0, 0))
        assert timeline_row.height() >= widget.timeline.fontMetrics().height()
        assert member_row.height() >= widget.members.fontMetrics().height()
        assert widget.timeline.viewport().rect().intersects(timeline_row)
        assert widget.members.viewport().rect().intersects(member_row)
    finally:
        widget.shutdown()
        widget.close()
        gui_theme.apply_theme(
            qt_app, color_style="standard", text_scale=100, density="comfortable"
        )


def test_narrow_enlarged_text_panel_does_not_require_outer_horizontal_scroll(qt_app):
    gui_theme.apply_theme(
        qt_app, color_style="standard", text_scale=200, density="comfortable"
    )
    collection = _Collection()
    EnsembleAcquisition.complete(("c00", "p01", "p02")).attach(collection)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    widget = EnsembleThresholdExplorer(_Engine())
    scroll.setWidget(widget)
    try:
        scroll.resize(560, 650)
        scroll.show()
        widget.refresh(collection)
        qt_app.processEvents()

        assert scroll.horizontalScrollBar().maximum() == 0
        assert widget.condition_rows[0][1].isVisible()
        assert widget.evaluate_current.isVisible()
        assert widget.retry.isVisible()
    finally:
        widget.shutdown()
        scroll.close()
        gui_theme.apply_theme(
            qt_app, color_style="standard", text_scale=100, density="comfortable"
        )
