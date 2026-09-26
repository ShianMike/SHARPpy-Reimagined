"""Saved comparison/member acquisition and GUI publication, without network."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QObject, QTimer, Signal
from qtpy.QtWidgets import (
    QDialog, QDoubleSpinBox, QLabel, QLineEdit, QMainWindow, QProgressBar,
    QPushButton, QStatusBar, QWidget,
)
from sharppy.sharptab.prof_collection import ProfCollection

from sharpmod import gui_model_compare, gui_picker
from sharpmod.batch_extract import BatchItemResult, BatchRunResult
from sharpmod.ensemble_members import EnsembleAcquisition
from sharpmod.gui_jobs import JobStatus
from sharpmod.gui_model_compare import ModelComparisonWorker


VALID = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def acquisition(qt_app, tmp_path, monkeypatch):
    workers, viewers = [], []
    plans = {"kind": "compare", "specs": gui_model_compare.plan_model_comparison(
        ("hrrr", "rap", "gfs"), VALID,
    )}

    class Worker(QObject):
        progress = Signal(str, int, int)
        result_ready = Signal(object)
        cancelled = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, specs, lat, lon, output_dir, **kwargs):
            super().__init__(kwargs["parent"])
            self.specs = tuple(specs)
            self.lat, self.lon, self.output_dir = lat, lon, str(output_dir)
            self.loc = kwargs.get("loc")
            self.result = None
            self.interrupted = False
            workers.append(self)

        def start(self):
            pass

        def requestInterruption(self):
            self.interrupted = True

    class Dialog:
        def __init__(self, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def specs(self):
            return plans["specs"]

        def workspace_kind(self):
            return plans["kind"]

    class Picker(QWidget):
        _cancel_model_fetch = gui_picker.PickerWindow._cancel_model_fetch
        _retry_model_job = gui_picker.PickerWindow._retry_model_job

        def __init__(self):
            super().__init__()
            for name in ("_model_worker", "_model_timeline_worker", "_model_compare_worker"):
                setattr(self, name, None)
            self._model_lat, self._model_lon = QDoubleSpinBox(self), QDoubleSpinBox(self)
            self._model_lat.setRange(-90, 90)
            self._model_lon.setRange(-180, 180)
            self._model_lat.setValue(35.22)
            self._model_lon.setValue(-97.44)
            self._model_loc = QLineEdit("Saved point", self)
            self._model_cancel_btn = QPushButton("Cancel", self)
            self._model_progress, self._model_progress_detail = QProgressBar(self), QLabel(self)
            self._model_progress_timer = QTimer(self)
            self._model_disk_cache = object()
            self._model_job = JobStatus(self)
            self._model_job_retry = None
            self._model_job.cancelRequested.connect(self._cancel_model_fetch)
            self._model_job.retryRequested.connect(self._retry_model_job)
            self._viewers = []
            self.busy = []
            self._status = QStatusBar(self)

        def _model_config(self):
            return SimpleNamespace(key="hrrr", label="HRRR")

        def _model_point_ok(self):
            return True

        def _model_run_time(self):
            return VALID

        def _model_selected_fxx(self):
            return 0

        def _ensure_model_cache(self):
            return self._model_disk_cache, None

        def _cancel_model_prefetch(self, **_kwargs):
            pass

        def _remember_point(self, *_args):
            pass

        def _set_model_busy(self, value):
            self.busy.append(value)

        def _config(self):
            return object()

        def _prune_closed_viewers(self):
            pass

        def statusBar(self):
            return self._status

    def decode(path):
        spec = next(spec for spec in workers[-1].specs if spec.request_id == Path(path).stem)
        valid = spec.valid_time + timedelta(hours=1 if plans.get("wrong") == spec.request_id else 0)
        collection = ProfCollection({"": [object()]}, [valid],
                                    loc="Saved point", requested_lat=35.22, requested_lon=-97.44,
                                    model=spec.label, run=spec.run_time, fxx=spec.fxx)
        return collection, "Saved point"

    def compose(_config, collection, picker, **kwargs):
        assert kwargs.get("activate") is False
        win = QMainWindow(picker)
        win.spc_widget = SimpleNamespace(prof_collections=[collection], pc_idx=0,
                                         updateProfs=lambda: None)
        win._sharpmod_analysis_workspace = SimpleNamespace(
            show_compare=lambda: None, show_ensemble=lambda: None,
        )
        def add(collection, *, focus, **_kwargs):
            assert not focus
            win.spc_widget.prof_collections.append(collection)
        win.addProfileCollection = add
        viewers.append(win)
        if plans.get("nested"):
            plans["nested"](workers[-1], win)
        return win

    monkeypatch.setattr(gui_model_compare, "ModelComparisonWorker", Worker)
    monkeypatch.setattr(gui_model_compare, "ModelComparisonDialog", Dialog)
    monkeypatch.setattr(gui_picker, "_render", lambda: SimpleNamespace(decode=decode))
    monkeypatch.setattr(gui_picker, "compose_interactive", compose)
    monkeypatch.setattr(gui_picker, "_fill_profile_metadata", lambda *_args: None)
    monkeypatch.setattr(gui_picker, "_start_locator_overlay_fetch", lambda *_a, **_k: None)
    monkeypatch.setattr(gui_picker, "_locator_spec_for", lambda *_args: None)
    monkeypatch.setattr(gui_picker, "_overlay_product_for", lambda *_args: None)
    monkeypatch.setattr(gui_model_compare.QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr("sharpmod.tools.model_extract.requires_grib_runtime", lambda _cfg: False)
    roots = iter(tmp_path / str(index) for index in range(10))
    def root(**_kwargs):
        directory = next(roots)
        directory.mkdir()
        return str(directory)
    monkeypatch.setattr(gui_model_compare.tempfile, "mkdtemp", root)
    picker = Picker()
    coordinator = gui_model_compare._ModelComparisonCoordinator(picker)
    picker._model_compare_coordinator = coordinator
    picker.resize(900, 650)
    picker.show()
    picker._model_loc.setFocus()
    picker._model_loc.setCursorPosition(2)
    qt_app.processEvents()
    try:
        yield picker, coordinator, workers, viewers, plans
    finally:
        for win in viewers:
            try:
                win.close()
                win.deleteLater()
            except RuntimeError:  # a deliberate close test already deleted it
                pass
        picker.close()
        picker.deleteLater()


def result_for(worker, statuses):
    root = Path(worker.output_dir)
    states = tuple(statuses)
    return BatchRunResult(root / "batch-manifest.json", "acquisition",
        states.count("completed"), states.count("failed"), states.count("cancelled"), 0,
        tuple(BatchItemResult(spec.request_id, status, root / f"{spec.request_id}.npz",
            root / f"{spec.request_id}.json", False,
            {"message": "Fixture HTTP 503"} if status == "failed" else None)
            for spec, status in zip(worker.specs, states)), {})


def test_cancelled_acquisition_retains_manifest_and_retries_immutable_scope(acquisition, qt_app):
    picker, owner, workers, viewers, _plans = acquisition
    original_geometry = picker.geometry()
    owner.open()
    first = workers[-1]
    assert picker._model_job.snapshot.total == 3
    picker._model_job.cancel_button.click()
    assert first.interrupted and picker._model_job.snapshot.state == "cancelling"
    first.result = result_for(first, ("completed", "cancelled", "cancelled"))
    first.cancelled.emit(first.result)
    first.finished.emit()
    assert not viewers and Path(first.output_dir).is_dir()
    assert picker._model_job.snapshot.counts.completed == 1
    assert picker._model_job.snapshot.counts.cancelled == 2
    assert picker._model_job.snapshot.retryable
    picker._model_lat.setValue(40)
    picker._model_lon.setValue(-105)
    picker._model_loc.setText("Edited point")
    picker._model_job.retry_button.click()
    retry = workers[-1]
    assert retry is not first and retry.specs == first.specs
    assert (retry.lat, retry.lon, retry.loc, retry.output_dir) == (
        35.22, -97.44, "Saved point", first.output_dir,
    )
    token = picker._model_job.snapshot.token
    first.result_ready.emit(first.result)
    first.cancelled.emit(first.result)
    assert picker._model_job.snapshot.token == token and not viewers
    assert owner._cancel_requested is False
    assert picker._model_job.snapshot.total == 2
    assert picker.geometry() == original_geometry and picker._model_loc.hasFocus()


@pytest.mark.parametrize("nested", (False, True))
def test_cancelled_queued_completion_cannot_publish_a_viewer(acquisition, nested):
    picker, owner, workers, viewers, plans = acquisition
    owner.open()
    worker = workers[-1]
    result = result_for(worker, ("completed",) * 3)
    if nested:
        plans["nested"] = lambda current, _win: (owner.cancel(), current.finished.emit())
    else:
        owner.cancel()
    worker.result_ready.emit(result)
    if not nested:
        worker.finished.emit()
    assert not picker._viewers
    assert picker._model_job.snapshot.state == "cancelled"
    assert picker._model_job.snapshot.counts.completed == 3
    assert picker._model_job.snapshot.retryable
    assert Path(worker.output_dir).is_dir()
    assert owner.worker is None


def test_partial_comparison_retry_appends_only_missing_exact_time(acquisition):
    picker, owner, workers, viewers, plans = acquisition
    owner.open()
    first = workers[-1]
    first.result_ready.emit(result_for(first, ("completed", "completed", "failed")))
    first.finished.emit()
    assert len(picker._viewers) == 1
    viewer = picker._viewers[0]
    original = tuple(viewer.spc_widget.prof_collections)
    viewer.spc_widget.pc_idx = 1
    viewer.resize(760, 510)
    geometry = viewer.geometry()
    plans["wrong"] = first.specs[-1].request_id
    picker._model_job.retry_button.click()
    retry = workers[-1]
    retry.result_ready.emit(result_for(retry, ("completed",) * 3))
    retry.finished.emit()
    assert tuple(viewer.spc_widget.prof_collections) == original
    assert picker._model_job.snapshot.counts.failed == 1
    assert "valid time" in picker._model_job.snapshot.message
    plans.pop("wrong")
    picker._model_job.retry_button.click()
    retry = workers[-1]
    retry.result_ready.emit(result_for(retry, ("completed",) * 3))
    retry.finished.emit()
    assert len(viewer.spc_widget.prof_collections) == 3
    assert tuple(viewer.spc_widget.prof_collections[:2]) == original
    assert len(picker._viewers) == 1 and viewer.spc_widget.pc_idx == 1
    assert viewer.geometry() == geometry and picker._model_loc.hasFocus()
    assert picker._model_job.snapshot.state == "completed"


def test_ensemble_member_retry_preserves_selection_and_existing_profiles(acquisition):
    picker, owner, workers, viewers, plans = acquisition
    plans["kind"] = "ensemble"
    plans["specs"] = gui_model_compare.plan_ensemble("gefs", run_time=VALID, fxx=0, count=3)
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    win = picker._viewers[0]
    collection = win.spc_widget.prof_collections[0]
    original = collection._profs["p01"][0]
    collection.setHighlightedMember("p01")
    def no_reopen():
        pytest.fail("member retry must not change the working tab")
    win._sharpmod_analysis_workspace.show_ensemble = no_reopen
    assert owner.retry_ensemble(win, collection, ("p02",))
    retry = workers[-1]
    assert tuple(spec.member for spec in retry.specs) == ("p02",)
    retry.result_ready.emit(result_for(retry, ("completed",)))
    retry.finished.emit()
    assert collection._profs["p01"][0] is original
    assert set(collection._profs) == {"c00", "p01", "p02"}
    assert collection.getHighlightedMemberName() == "p01"
    assert EnsembleAcquisition.from_collection(collection).loaded_count == 3
    assert picker._model_job.snapshot.counts.completed == 1


def test_unreported_outcome_is_unknown_and_keeps_useful_artifacts(acquisition):
    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    worker.result = result_for(worker, ("completed", "completed"))
    worker.failed.emit("Child transport stopped")
    worker.finished.emit()
    counts = picker._model_job.snapshot.counts
    assert (counts.completed, counts.unknown, counts.failed, counts.unattempted) == (2, 1, 0, 0)
    assert picker._model_job.snapshot.state == "failed"
    assert "Child transport stopped" in picker._model_job.snapshot.message
    assert len(picker._viewers) == 1 and Path(worker.output_dir).is_dir()


def test_owner_shutdown_defers_unpublished_root_cleanup_until_worker_finish(acquisition):
    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    owner.shutdown()
    picker._shutdown_started = True
    assert worker.interrupted and Path(worker.output_dir).is_dir()
    worker.result_ready.emit(result_for(worker, ("completed",) * 3))
    worker.finished.emit()
    assert not picker._viewers and not Path(worker.output_dir).exists()
    assert picker._model_job.snapshot.state == "superseded"


def test_replaced_member_retry_target_rejects_late_merge(acquisition):
    picker, owner, workers, _viewers, plans = acquisition
    plans["kind"] = "ensemble"
    plans["specs"] = gui_model_compare.plan_ensemble("gefs", run_time=VALID, fxx=0, count=3)
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    win = picker._viewers[0]
    collection = win.spc_widget.prof_collections[0]
    assert owner.retry_ensemble(win, collection, ("p02",))
    retry = workers[-1]
    win.spc_widget.prof_collections.clear()
    retry.result_ready.emit(result_for(retry, ("completed",)))
    retry.finished.emit()
    assert set(collection._profs) == {"c00", "p01"}
    assert picker._model_job.snapshot.state == "superseded"
    assert not Path(retry.output_dir).exists()


def test_finished_without_terminal_signal_is_not_a_false_success(acquisition):
    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    worker.finished.emit()
    assert picker._model_job.snapshot.state == "failed"
    assert picker._model_job.snapshot.counts.unknown == 3
    assert picker._model_job.snapshot.retryable and Path(worker.output_dir).is_dir()


def test_closed_acquisition_viewer_cancels_and_cleans_after_worker_finish(acquisition, qt_app):
    from qtpy.QtCore import QCoreApplication, QEvent

    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    viewer = picker._viewers[0]
    picker._model_job.retry_button.click()
    retry = workers[-1]
    root = Path(retry.output_dir)
    viewer.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert retry.interrupted and root.is_dir()
    retry.result_ready.emit(result_for(retry, ("completed",) * 3))
    retry.finished.emit()
    assert not root.exists() and picker._model_job.snapshot.state == "superseded"


def test_same_explorer_member_retry_reuses_a_withheld_completed_artifact(acquisition):
    picker, owner, workers, _viewers, plans = acquisition
    plans["kind"] = "ensemble"
    plans["specs"] = gui_model_compare.plan_ensemble("gefs", run_time=VALID, fxx=0, count=3)
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    win = picker._viewers[0]
    collection = win.spc_widget.prof_collections[0]
    assert owner.retry_ensemble(win, collection, ("p02",))
    first = workers[-1]
    owner.cancel()
    first.result = result_for(first, ("completed",))
    first.result_ready.emit(first.result)
    first.finished.emit()
    assert set(collection._profs) == {"c00", "p01"}
    assert owner.retry_ensemble(win, collection, ("p02",))
    retry = workers[-1]
    assert retry.output_dir == first.output_dir and retry.specs == first.specs
    assert picker._model_job.snapshot.total == 0
    retry.result_ready.emit(result_for(retry, ("completed",)))
    retry.finished.emit()
    assert set(collection._profs) == {"c00", "p01", "p02"}
    assert not owner.retry_ensemble(win, collection, ("p02",))


def test_pre_start_cancel_has_acknowledged_cancel_counts(acquisition):
    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    worker.batch_started = False
    owner.cancel()
    worker.cancelled.emit(None)
    worker.finished.emit()
    assert picker._model_job.snapshot.counts.cancelled == 3
    assert picker._model_job.snapshot.counts.unknown == 0


def test_unstarted_comparison_owner_shutdown_does_not_touch_another_job(acquisition):
    picker, owner, _workers, _viewers, _plans = acquisition
    picker._model_job.begin("timeline", "Forecast timeline", "GFS F000", "Downloading")
    owner.shutdown()
    assert picker._model_job.snapshot.state == "running"


def test_changed_target_request_metadata_rejects_a_same_object_response(acquisition):
    picker, owner, workers, _viewers, plans = acquisition
    plans["kind"] = "ensemble"
    plans["specs"] = gui_model_compare.plan_ensemble("gefs", run_time=VALID, fxx=0, count=3)
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    win = picker._viewers[0]
    collection = win.spc_widget.prof_collections[0]
    assert owner.retry_ensemble(win, collection, ("p02",))
    retry = workers[-1]
    collection.setMeta("requested_lat", 40.0)
    retry.result_ready.emit(result_for(retry, ("completed",)))
    retry.finished.emit()
    assert set(collection._profs) == {"c00", "p01"}
    assert picker._model_job.snapshot.state == "superseded"


def test_retired_shutdown_worker_does_not_break_actual_viewer_cleanup(acquisition):
    from qtpy.QtCore import QCoreApplication, QEvent

    picker, owner, workers, _viewers, _plans = acquisition
    owner.open()
    worker = workers[-1]
    worker.result_ready.emit(result_for(worker, ("completed", "completed", "failed")))
    worker.finished.emit()
    session = owner._session
    native = ModelComparisonWorker(session.request.specs, 35.22, -97.44,
                                   session.request.output_dir)
    native._sharpmod_acquisition_session = session
    owner.worker = native
    picker._shutdown_started = True
    native.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    session.viewer.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not Path(session.request.output_dir).exists()


@pytest.mark.parametrize("kind", ("models", "runs", "ensemble"))
def test_actual_200_percent_chooser_keeps_scope_and_actions_reachable(qt_app, kind):
    from qtpy.QtWidgets import QDialogButtonBox
    from sharpmod.gui_theme import apply_theme

    apply_theme(qt_app, color_style="standard", text_scale=200)
    dialog = gui_model_compare.ModelComparisonDialog(
        current_model="gefs", run_time=VALID, fxx=24, lat=35.22, lon=-97.44,
    )
    try:
        dialog.mode.setCurrentIndex(dialog.mode.findData(kind))
        dialog.resize(600, 500)
        dialog.show()
        qt_app.processEvents()
        assert (dialog.width(), dialog.height()) == (600, 500)
        assert dialog.preview.height() >= dialog.preview.heightForWidth(dialog.preview.width())
        if kind == "ensemble":
            assert "c00" in dialog.preview.text() and "p30" in dialog.preview.text()
            assert dialog.preview.text().count("GEFS") == 1
            assert dialog.models.isHidden()
        else:
            assert len(dialog.specs()) >= 2
        assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
        dialog.body_scroll.ensureWidgetVisible(dialog.preview)
        qt_app.processEvents()
        assert dialog.preview.visibleRegion().boundingRect().height() > 0
        for button in dialog.findChild(QDialogButtonBox).buttons():
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    finally:
        dialog.close()
        dialog.deleteLater()
