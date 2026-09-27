"""Forecast-timeline GUI queue integration without network access."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QObject, QTimer, Signal
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QWidget,
)
from sharppy.sharptab import prof_collection, profile

from sharpmod import gui_batch_process, gui_picker, gui_timeline
from sharpmod.batch_extract import BatchItemResult, BatchRunResult
from sharpmod.gui_jobs import JobStatus
from sharpmod.gui_timeline import ForecastTimelineCoordinator, ModelTimelineWorker


def test_timeline_worker_streams_completed_and_missing_hours(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    calls = {}

    class FakeRunner:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def run(self, requests, **kwargs):
            calls["hours"] = [request.fxx for request in requests]
            calls["workers"] = kwargs["max_workers"]
            progress_callback = kwargs["progress_callback"]
            for request in requests[:2]:
                progress_callback({
                    "event": "completed", "request_id": request.id,
                })
            progress_callback({
                "event": "failed", "request_id": requests[2].id,
                "error": {"message": "not published"},
            })
            return SimpleNamespace(
                completed=2,
                items=(
                    SimpleNamespace(status="completed"),
                    SimpleNamespace(status="completed"),
                    SimpleNamespace(status="failed"),
                ),
            )

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", FakeRunner)
    ready = []
    failed = []
    results = []
    worker = ModelTimelineWorker(
        "gfs", 35.0, -97.0,
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        (0, 3, 6), tmp_path,
    )
    worker.item_ready.connect(lambda path, hour: ready.append((path, hour)))
    worker.item_failed.connect(lambda hour, message: failed.append((hour, message)))
    worker.result_ready.connect(results.append)

    worker.run()

    assert calls == {"hours": [0, 3, 6], "workers": 2}
    assert [hour for _path, hour in ready] == [0, 3]
    assert failed == [(6, "not published")]
    assert results[0].completed == 2
    assert app is not None


@pytest.mark.parametrize("stage", ("before", "lookup"))
def test_timeline_worker_cancel_before_batch_spawn_does_not_start_network_work(
    qt_app, tmp_path, monkeypatch, stage,
):
    worker = ModelTimelineWorker(
        "gfs", 35.0, -97.0, datetime(2026, 9, 10, tzinfo=timezone.utc),
        (0, 3, 6), tmp_path, resolve_place=True,
    )
    outcomes = []
    worker.cancelled.connect(outcomes.append)

    def no_batch():
        raise AssertionError("cancelled timeline started a batch")

    def lookup(*_args):
        assert stage == "lookup"
        worker.requestInterruption()
        return "Resolved place"

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", no_batch)
    monkeypatch.setattr("sharpmod.place_names.reverse_town_name", lookup)
    if stage == "before":
        worker.requestInterruption()
    worker.run()
    assert outcomes == [None]


@pytest.fixture
def timeline_job_fixture(
    qt_app, tmp_path, monkeypatch
):
    workers = []

    class FakeWorker(QObject):
        item_ready = Signal(str, int)
        item_failed = Signal(int, str)
        progress = Signal(int, str, int, int)
        result_ready = Signal(object)
        cancelled = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(
            self, model, lat, lon, run_time, hours, output_dir, *, loc=None,
            resolve_place=False, member=None, disk_cache=None,
            completed_hours=(), parent=None,
        ):
            super().__init__(parent)
            self.model = model
            self.lat = lat
            self.lon = lon
            self.run_time = run_time
            self.hours = tuple(hours)
            self.output_dir = str(output_dir)
            self.loc = loc
            self.resolve_place = resolve_place
            self.member = member
            self.disk_cache = disk_cache
            self.completed_hours = tuple(completed_hours)
            self.interrupted = False
            workers.append(self)

        def start(self):
            pass

        def requestInterruption(self):
            self.interrupted = True

    class FakeDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def hours(self):
            return (0, 3, 6)

    class FakePicker(QWidget):
        def __init__(self):
            super().__init__()
            self._model_worker = None
            self._model_timeline_worker = None
            self._model_compare_worker = None
            self._model_prefetch_worker = None
            self._model_disk_cache = object()
            self._model_lat = QDoubleSpinBox(self)
            self._model_lon = QDoubleSpinBox(self)
            self._model_lat.setRange(-90, 90)
            self._model_lon.setRange(-180, 180)
            self._model_lat.setValue(35.22)
            self._model_lon.setValue(-97.44)
            self._model_loc = QLineEdit("KOUN", self)
            self._model_fetch_btn = QPushButton("Load Sounding", self)
            self._model_timeline_btn = QPushButton("Timeline…", self)
            self._model_cancel_btn = QPushButton("Cancel", self)
            self._model_progress = QProgressBar(self)
            self._model_progress_detail = QLabel(self)
            self._model_progress_timer = QTimer(self)
            self._status = QStatusBar(self)
            self._viewers = []
            self._model_job = JobStatus(self)
            self._model_job_retry = None
            self._model_job.cancelRequested.connect(self._cancel_model_fetch)
            self._model_job.retryRequested.connect(self._retry_model_job)
            self.run_time = datetime(2026, 9, 10, 0, tzinfo=timezone.utc)
            self.member = "p01"
            self.busy = []

        def _retry_model_job(self):
            if callable(self._model_job_retry):
                self._model_job_retry()

        def _cancel_model_fetch(self):
            gui_picker.PickerWindow._cancel_model_fetch(self)

        def _model_config(self):
            return SimpleNamespace(key="gfs", label="GFS", domain="global")

        def _model_point_ok(self):
            return True

        def _ensure_model_cache(self):
            return self._model_disk_cache, None

        def _model_selected_fxx(self):
            return 0

        def _model_run_time(self):
            return self.run_time

        def _model_member_value(self):
            return self.member

        def _cancel_model_prefetch(self, *, wait):
            return True

        def _remember_point(self, *_args):
            pass

        def _set_model_busy(self, value):
            self.busy.append(bool(value))

        def _config(self):
            return object()

        def _prune_closed_viewers(self):
            pass

        def statusBar(self):
            return self._status

    monkeypatch.setattr(gui_timeline, "ForecastTimelineDialog", FakeDialog)
    monkeypatch.setattr(gui_timeline, "ModelTimelineWorker", FakeWorker)
    monkeypatch.setattr(gui_timeline.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))
    from sharpmod.tools import model_extract
    monkeypatch.setattr(model_extract, "forecast_hours", lambda *_a, **_k: (0, 3, 6))
    monkeypatch.setattr(model_extract, "requires_grib_runtime", lambda _cfg: False)

    picker = FakePicker()
    coordinator = ForecastTimelineCoordinator(picker)
    picker._model_timeline_coordinator = coordinator
    picker.resize(900, 650)
    picker.show()
    picker._model_loc.setFocus()
    picker._model_loc.setCursorPosition(2)
    qt_app.processEvents()
    try:
        yield picker, coordinator, workers
    finally:
        for viewer in picker._viewers:
            viewer.close()
            viewer.deleteLater()
        picker.close()
        picker.deleteLater()


def _batch_result(root, statuses):
    words = tuple(statuses.values())
    return BatchRunResult(
        root / "batch-manifest.json", "timeline", words.count("completed"),
        words.count("failed"), words.count("cancelled"), 0,
        tuple(
            BatchItemResult(
                f"f{hour:03d}", status, root / f"f{hour:03d}.npz",
                root / f"f{hour:03d}.json", False,
                {"message": "not published"} if status == "failed" else None,
            )
            for hour, status in statuses.items()
        ), {},
    )


def test_timeline_shared_job_cancel_and_retry_keep_the_saved_request(
    qt_app, tmp_path, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    original_size = picker.size()
    coordinator.open()
    first = workers[-1]
    assert picker._model_timeline_worker is first
    assert picker._model_job.snapshot.state == "running"
    assert "GFS" in picker._model_job.snapshot.affected_input
    assert "F000–F006" in picker._model_job.snapshot.affected_input
    assert picker._model_job.snapshot.total == 3

    coordinator._record_frame(first, 0, "loaded", valid_time=picker.run_time)
    picker._model_job.cancel_button.click()
    assert first.interrupted is True
    assert picker._model_job.snapshot.state == "cancelling"
    partial = _batch_result(tmp_path, {0: "completed", 3: "cancelled", 6: "cancelled"})
    first.cancelled.emit(partial)
    first.finished.emit()
    qt_app.processEvents()

    assert picker._model_job.snapshot.state == "cancelled"
    assert picker._model_job.snapshot.counts.completed == 1
    assert picker._model_job.snapshot.counts.cancelled == 2
    assert picker._model_job.snapshot.retryable is True
    assert picker._model_loc.hasFocus()
    assert picker._model_loc.cursorPosition() == 2
    assert picker.size() == original_size

    picker.run_time = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    picker.member = "edited"
    picker._model_lat.setValue(40.0)
    picker._model_lon.setValue(-105.0)
    picker._model_loc.setText("Edited location")
    picker._model_job.retry_button.click()
    qt_app.processEvents()
    retry = workers[-1]
    assert retry is not first
    assert (retry.model, retry.lat, retry.lon) == ("gfs", 35.22, -97.44)
    assert retry.run_time == datetime(2026, 9, 10, 0, tzinfo=timezone.utc)
    assert retry.member == "p01"
    assert retry.loc == "KOUN"
    assert retry.output_dir == first.output_dir
    assert retry.hours == (0, 3, 6)
    assert retry.completed_hours == (0,)
    assert picker._model_job.snapshot.total == 2
    token = picker._model_job.snapshot.token
    first.result_ready.emit(partial)
    qt_app.processEvents()
    assert picker._model_job.snapshot.token == token
    assert picker._model_job.snapshot.state == "running"


def _display_fixture(monkeypatch, picker, *, wrong_hour=None):
    def decode(path):
        hour = int(Path(path).stem.lstrip("f"))
        valid = datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(
            hours=hour + (1 if wrong_hour == hour else 0)
        )
        raw = profile.create_profile(
            profile="raw", pres=[1000, 900, 800], hght=[100, 1000, 2000],
            tmpc=[25, 18, 10], dwpc=[20, 12, 4], wdir=[180, 200, 220],
            wspd=[10, 20, 30], location="KOUN", date=valid,
            latitude=35.22, missing=-9999.0,
        )
        collection = prof_collection.ProfCollection({"": [raw]}, [valid])
        for name, value in {"loc": "KOUN", "model": "GFS", "fxx": hour,
                            "run": datetime(2026, 9, 10, tzinfo=timezone.utc),
                            "npz_path": str(path)}.items():
            collection.setMeta(name, value)
        return collection, "KOUN"

    def compose(_config, collection, _picker, **kwargs):
        assert kwargs["activate"] is False
        viewer = QMainWindow(picker)
        viewer.spc_widget = SimpleNamespace(
            prof_collections=[collection], pc_idx=0, updateProfs=lambda: None
        )
        return viewer

    monkeypatch.setattr(gui_timeline, "_render", lambda: SimpleNamespace(decode=decode))
    monkeypatch.setattr(gui_timeline, "compose_interactive", compose)


def test_queued_cancel_saves_completed_artifacts_without_replacing_display(
    qt_app, tmp_path, monkeypatch, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    _display_fixture(monkeypatch, picker)
    coordinator.open()
    first = workers[-1]
    first.item_ready.emit(str(tmp_path / "f003.npz"), 3)
    session = coordinator._session
    viewer = session.viewer
    current = session.collection.getCurrentDate()
    viewer.resize(850, 600)
    original_geometry = viewer.geometry()
    picker._model_loc.setFocus()
    picker._model_loc.setCursorPosition(2)
    picker._model_job.cancel_button.click()
    first.item_ready.emit(str(tmp_path / "f000.npz"), 0)
    first.item_failed.emit(3, "late failure")
    first.progress.emit(6, "late stage", 3, 3)
    result = _batch_result(tmp_path, {0: "completed", 3: "completed", 6: "completed"})
    first.result_ready.emit(result)
    first.failed.emit("duplicate terminal failure")
    first.finished.emit()
    qt_app.processEvents()
    assert session.completed_hours == (3,)
    assert session.collection.getCurrentDate() == current
    assert picker._model_job.snapshot.state == "cancelled"
    assert picker._model_job.snapshot.counts.completed == 3
    assert "2 completed artifact(s) await explicit Retry" in picker._model_job.snapshot.message
    assert "valid 2026-09-10 03:00Z" in picker._model_job.snapshot.retained
    assert coordinator.retry() is True
    retry = workers[-1]
    assert retry.completed_hours == (3,)
    retry.result_ready.emit(result)  # recover even without stdout item events
    qt_app.processEvents()
    assert coordinator._session.viewer is viewer
    assert session.completed_hours == (0, 3, 6)
    assert session.collection.getCurrentDate() == current
    assert len(session.collection._dates) == 3
    assert picker._model_job.snapshot.state == "completed"
    identity = picker._model_job.snapshot.retained
    session.collection.setCurrentDate(datetime(2026, 9, 10, 6, tzinfo=timezone.utc))
    assert coordinator._retained_identity(session) == identity
    assert picker._model_loc.hasFocus()
    assert picker._model_loc.cursorPosition() == 2
    assert viewer.geometry() == original_geometry


@pytest.mark.parametrize("cancelled", (False, True))
def test_terminal_callbacks_during_first_viewer_composition_do_not_reenter(
    tmp_path, monkeypatch, timeline_job_fixture, cancelled,
):
    picker, coordinator, workers = timeline_job_fixture
    _display_fixture(monkeypatch, picker)
    plain_compose = gui_timeline.compose_interactive
    composing = []

    def nested_compose(*args, **kwargs):
        assert not composing, "a queued terminal result reentered first-viewer composition"
        composing.append(True)
        try:
            viewer = plain_compose(*args, **kwargs)
            worker = workers[-1]
            if cancelled:
                coordinator.cancel()
            worker.result_ready.emit(_batch_result(tmp_path, {0: "completed", 3: "completed", 6: "completed"}))
            worker.finished.emit()
            return viewer
        finally:
            composing.pop()

    monkeypatch.setattr(gui_timeline, "compose_interactive", nested_compose)
    coordinator.open()
    workers[-1].item_ready.emit(str(tmp_path / "f000.npz"), 0)
    session = coordinator._session
    assert picker._model_timeline_worker is None
    assert session.completed_hours == (() if cancelled else (0, 3, 6))
    assert len(picker._viewers) == (0 if cancelled else 1)
    assert picker._model_job.snapshot.state == ("cancelled" if cancelled else "completed")


def test_wrong_valid_time_is_not_substituted_and_retry_uses_saved_manifest(
    tmp_path, monkeypatch, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    _display_fixture(monkeypatch, picker, wrong_hour=3)
    coordinator.open()
    worker = workers[-1]
    worker.result_ready.emit(_batch_result(tmp_path, {0: "completed", 3: "completed", 6: "failed"}))
    session = coordinator._session
    assert session.completed_hours == (0,)
    assert session.coverage.state_of(3) == "failed"
    assert "expected valid 2026-09-10 03:00Z" in session.failures[3]
    assert picker._model_job.snapshot.counts.completed == 1
    assert picker._model_job.snapshot.counts.failed == 2
    worker.finished.emit()
    _display_fixture(monkeypatch, picker)
    assert coordinator.retry() is True
    retry = workers[-1]
    assert retry.output_dir == worker.output_dir
    assert retry.completed_hours == (0,)
    retry.result_ready.emit(_batch_result(tmp_path, {0: "completed", 3: "completed", 6: "completed"}))
    assert session.completed_hours == (0, 3, 6)


def test_unreported_worker_failure_keeps_unknown_counts_and_resolved_location(
    tmp_path, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    picker._model_loc.clear()
    coordinator.open()
    worker = workers[-1]
    worker.loc = "Resolved place"
    worker.finished.emit()
    assert picker._model_job.snapshot.state == "failed"
    assert picker._model_job.snapshot.counts.unknown == 3
    assert picker._model_job.snapshot.counts.unattempted == 0
    assert coordinator._session.coverage.unknown_hours == (0, 3, 6)
    assert coordinator.retry() is True
    assert workers[-1].loc == "Resolved place"
    assert workers[-1].resolve_place is False
    picker._shutdown_started = True
    workers[-1].result_ready.emit(_batch_result(tmp_path, {0: "completed", 3: "completed", 6: "completed"}))
    assert coordinator._session.completed_hours == ()
    assert coordinator.retry() is False


def test_closed_viewer_rejects_arrivals_and_cleans_on_finish(
    qt_app, tmp_path, monkeypatch, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    _display_fixture(monkeypatch, picker)
    coordinator.open()
    worker = workers[-1]
    worker.item_ready.emit(str(tmp_path / "f000.npz"), 0)
    session = coordinator._session
    coordinator._on_timeline_viewer_destroyed(session, str(tmp_path))
    assert worker.interrupted is True
    worker.item_ready.emit(str(tmp_path / "f003.npz"), 3)
    assert session.completed_hours == (0,)
    assert coordinator.retry() is False
    assert picker._model_job.snapshot.state == "superseded"
    # The fixture owns this root; cleanup is held until its active worker ends.
    assert tmp_path.is_dir()
    worker.finished.emit()
    assert not tmp_path.exists()


def test_no_viewer_shutdown_cleans_on_finish(
    tmp_path, timeline_job_fixture,
):
    picker, coordinator, workers = timeline_job_fixture
    coordinator.open()
    pending = workers[-1]
    coordinator.shutdown()
    pending.item_ready.emit(str(tmp_path / "f000.npz"), 0)
    assert coordinator._session.completed_hours == ()
    assert tmp_path.is_dir()
    pending.finished.emit()
    assert not tmp_path.exists()


def test_picker_delegates_timeline_lifecycle_to_one_coordinator(monkeypatch):
    from sharpmod import gui_picker, gui_timeline

    opened = []

    class FakeCoordinator:
        def __init__(self, picker):
            self.picker = picker

        def open(self):
            opened.append(self.picker)

    monkeypatch.setattr(
        gui_timeline,
        "ForecastTimelineCoordinator",
        FakeCoordinator,
    )
    picker = SimpleNamespace(_model_timeline_coordinator=None)

    gui_picker.PickerWindow._model_fetch_timeline(picker)
    first = picker._model_timeline_coordinator
    gui_picker.PickerWindow._model_fetch_timeline(picker)

    assert picker._model_timeline_coordinator is first
    assert opened == [picker, picker]
