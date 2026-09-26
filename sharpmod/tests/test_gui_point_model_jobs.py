"""Point-model request ownership and saved-artifact retry, without network."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QObject, QTimer, Signal
from qtpy.QtWidgets import QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton, QStatusBar, QWidget
from sharppy.sharptab.prof_collection import ProfCollection

from sharpmod import gui_model_point, gui_picker
from sharpmod.gui_jobs import JobStatus
from sharpmod.gui_model_compare import ComparisonRequestSpec
from sharpmod.gui_workers import _ModelFetchWorker as NativePointWorker
from sharpmod.tools import model_extract


VALID = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def point_job(qt_app, tmp_path, monkeypatch):
    workers, viewers, settings = [], [], {}

    class Worker(QObject):
        finished_ok = Signal(str, str, object, int)
        failed = Signal(str)
        cancelled = Signal()
        progress = Signal(str, int, int, float)
        finished = Signal()

        def __init__(self, model, lat, lon, run_time, fxx, out_path, **kwargs):
            super().__init__(kwargs["parent"])
            self._model, self._lat, self._lon = model, lat, lon
            self._run_time, self._fxx, self._out_path = run_time, fxx, out_path
            self._member, self._loc = kwargs.get("member"), kwargs.get("loc")
            self._download_dir = kwargs["download_dir"]
            self.completed_pair = kwargs.get("completed_pair")
            self.result = None
            self.interrupted = False
            workers.append(self)

        def start(self):
            pass

        def requestInterruption(self):
            self.interrupted = True

    class Picker(QWidget):
        def __init__(self):
            super().__init__()
            self._model_worker = self._model_timeline_worker = self._model_compare_worker = None
            self._model_disk_cache = self._model_hour_cache = None
            self._model_cancel_btn = QPushButton(self)
            self._model_progress, self._model_progress_detail = QProgressBar(self), QLabel(self)
            self._model_progress_timer = QTimer(self)
            self._model_job = JobStatus(self)
            self._model_job_retry = None
            self._viewers, self.busy = [], []
            self._status = QStatusBar(self)
            self.focus_widget = QLineEdit("Saved point", self)
            self.scroll_value = 0
            self.window_geometry = None

        def _set_model_busy(self, value):
            self.busy.append((value, self._model_worker))

        def _on_model_fetch_progress(self, stage, *_args):
            self._model_progress_detail.setText(stage)

        def _config(self):
            return object()

        def statusBar(self):
            return self._status

        def _snapshot_context(self):
            return (
                self.focus_widget.text(),
                self.focus_widget.cursorPosition(),
                self.scroll_value,
                self.window_geometry,
            )

    def decode(_path):
        if settings.get("decode_error"):
            raise ValueError("temporary decoder failure")
        valid = VALID + timedelta(hours=1 if settings.get("wrong_time") else 0)
        return ProfCollection({"": [object()]}, [valid], loc="Saved point"), "Saved point"

    def compose(_config, collection, picker, **kwargs):
        assert kwargs.get("activate") is False
        win = QMainWindow(picker)
        win.spc_widget = SimpleNamespace(prof_collections=[collection], pc_idx=0)
        viewers.append(win)
        if settings.get("nested"):
            settings["nested"](workers[-1], win)
        return win

    monkeypatch.setattr(gui_model_point, "_ModelFetchWorker", Worker)
    # Placeholder bytes test Qt ownership only; real extraction is checked separately.
    monkeypatch.setattr(gui_model_point, "_portable_pair_valid", lambda path: Path(path).is_file())
    monkeypatch.setattr(gui_picker, "_render", lambda: SimpleNamespace(decode=decode))
    monkeypatch.setattr(gui_picker, "compose_interactive", compose)
    monkeypatch.setattr(gui_picker, "_fill_profile_metadata", lambda *_args: None)
    monkeypatch.setattr(gui_picker, "_start_locator_overlay_fetch", lambda *_args, **_kwargs: None)
    picker = Picker()
    coordinator = gui_model_point.PointModelCoordinator(picker)
    root = tmp_path / "point"
    request = gui_model_point.PointModelRequest(
        ComparisonRequestSpec("point", "gfs", "GFS", VALID, 0), 35.22, -97.44, "Saved point", str(root),
    )
    coordinator.open(request)

    def ready(worker=None):
        worker = worker or workers[-1]
        path = Path(worker._out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"placeholder point")
        path.with_suffix(".json").write_text("{}", encoding="utf-8")
        worker.result = (str(path), "GFS", VALID, 0)
        worker.finished_ok.emit(*worker.result)

    yield SimpleNamespace(
        picker=picker, owner=coordinator, workers=workers, viewers=viewers,
        settings=settings, request=request, root=root, ready=ready,
    )
    coordinator.shutdown()
    for worker in workers:
        worker.finished.emit()
    picker.close()


def test_cancel_queued_success_keeps_artifact_for_saved_retry(point_job):
    p = point_job
    worker = p.workers[-1]
    p.picker.focus_widget.setCursorPosition(4)
    p.picker.scroll_value = 17
    p.picker.window_geometry = (11, 23, 640, 480)
    before = p.picker._snapshot_context()
    p.owner.cancel()
    p.ready(worker)
    worker.finished.emit()
    assert not p.viewers and p.root.is_dir()
    assert p.picker._model_job.snapshot.state == "cancelled"
    assert p.picker._model_job.snapshot.counts.completed == 1
    assert p.picker._snapshot_context() == before
    assert p.owner.retry()
    retry = p.workers[-1]
    assert (retry._lat, retry._lon, retry._loc) == (35.22, -97.44, "Saved point")
    assert retry.completed_pair
    assert p.picker._snapshot_context() == before
    p.ready(retry)
    retry.finished.emit()
    assert len(p.viewers) == 1
    assert p.picker.busy[-1] == (False, None)
    assert p.picker._snapshot_context() == before


def test_wrong_time_and_decode_failure_keep_download_for_retry(point_job):
    p = point_job
    p.settings["wrong_time"] = True
    p.ready()
    p.workers[-1].finished.emit()
    assert not p.viewers and p.root.is_dir()
    assert p.picker._model_job.snapshot.counts.failed == 1
    assert "valid time" in p.picker._model_job.snapshot.message
    p.settings["wrong_time"] = False
    p.settings["decode_error"] = True
    assert p.owner.retry()
    assert p.workers[-1].completed_pair
    p.ready()
    p.workers[-1].finished.emit()
    assert not p.viewers and p.root.is_dir()
    p.settings["decode_error"] = False
    assert p.owner.retry()
    p.ready()
    p.workers[-1].finished.emit()
    assert len(p.viewers) == 1


def test_old_result_and_cancel_cannot_poison_new_point_attempt(point_job):
    p = point_job
    old = p.workers[-1]
    p.owner.cancel()
    old.cancelled.emit()
    old.finished.emit()
    assert p.owner.retry()
    token = p.picker._model_job.snapshot.token
    old.cancelled.emit()
    p.ready(old)
    old.finished.emit()
    assert not p.viewers
    assert p.picker._model_job.snapshot.token == token
    assert p.picker._model_job.snapshot.state == "running"
    p.ready()
    p.workers[-1].finished.emit()
    assert len(p.viewers) == 1


def test_cancel_during_first_composition_does_not_publish_candidate(point_job):
    p = point_job
    def nested(worker, _win):
        p.owner.cancel()
        worker.finished.emit()
    p.settings["nested"] = nested
    p.ready()
    assert not p.picker._viewers
    assert p.picker._model_worker is None
    assert p.root.is_dir()
    assert p.picker._model_job.snapshot.state == "cancelled"
    p.settings.pop("nested")
    assert p.owner.retry()
    assert p.workers[-1].completed_pair
    p.ready()
    p.workers[-1].finished.emit()
    assert len(p.picker._viewers) == 1


def test_finished_without_terminal_is_unknown_not_complete(point_job):
    p = point_job
    p.workers[-1].finished.emit()
    snapshot = p.picker._model_job.snapshot
    assert snapshot.counts.unknown == 1 and snapshot.counts.completed == 0
    assert snapshot.retryable and p.owner.retry()


def test_shutdown_defers_unpublished_root_cleanup_until_worker_finishes(point_job):
    p = point_job
    p.owner.shutdown()
    assert p.root.is_dir()
    p.ready()
    assert not p.viewers
    p.workers[-1].finished.emit()
    assert not p.root.exists()


def test_changed_complete_pair_retries_extraction_instead_of_reusing(point_job):
    p = point_job
    p.owner.cancel()
    p.ready()
    p.workers[-1].finished.emit()
    Path(p.request.output_path).write_bytes(b"changed")
    assert p.owner.retry()
    assert p.workers[-1].completed_pair is None


def test_real_picker_point_open_owns_job_and_worker_lifecycle(
    qt_app, tmp_path, monkeypatch
):
    """Opening one real-picker point run owns the job, worker, and shutdown."""
    from qtpy.QtWidgets import QApplication

    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog", lambda *_a, **_k: None
    )
    picker = gui_picker.PickerWindow()
    qt_app.processEvents()
    try:
        from sharpmod import gui_model_point

        assert gui_model_point.PointModelCoordinator is not None
        picker._select_tab("Forecast Model")
        qt_app.processEvents()
        focus = picker._model_loc
        focus.setFocus()
        focus.setCursorPosition(1)
        geometry = picker.geometry()
        assert picker._model_job.snapshot is None

        request = gui_model_point.PointModelRequest(
            ComparisonRequestSpec("point", "gfs", "GFS", VALID, 0),
            35.22, -97.44, "Saved point", str(tmp_path / "real-point"),
        )
        coordinator = getattr(picker, "_model_point_coordinator", None)
        if coordinator is None:
            coordinator = gui_model_point.PointModelCoordinator(picker)
            picker._model_point_coordinator = coordinator
        started = []
        real_start = coordinator._start

        def watch_start(session):
            started.append(session.request)
            return real_start(session)

        monkeypatch.setattr(coordinator, "_start", watch_start)
        assert coordinator.open(request)
        assert started == [request]
        worker = coordinator.worker
        assert worker is not None
        assert worker._sharpmod_point_token == coordinator.token
        assert worker._sharpmod_point_session is coordinator.session
        assert picker._model_worker is worker
        assert picker._model_job.snapshot.state == "running"
        assert picker._model_job.snapshot.token == coordinator.token
        assert "F000" in picker._model_job.snapshot.affected_input
        assert coordinator._current(worker)

        coordinator.cancel()
        assert picker._model_job.snapshot.state == "cancelling"
        assert picker._model_job.snapshot.token == coordinator.token
        assert coordinator._current(worker)
        assert picker.geometry() == geometry
    finally:
        try:
            coordinator.shutdown()
        except Exception:
            pass
        picker._shutdown_model_cache()
        picker.close()
        picker.deleteLater()
        QApplication.processEvents()


def test_native_complete_then_cancel_acks_and_retains_pair(qt_app, tmp_path, monkeypatch):
    worker = NativePointWorker("gfs", 35.22, -97.44, VALID, 0,
                              str(tmp_path / "sounding.npz"), download_dir=str(tmp_path))
    completed, cancelled = [], []
    worker.finished_ok.connect(lambda *args: completed.append(args))
    worker.cancelled.connect(lambda: cancelled.append(True))
    def extract(*_args, **kwargs):
        path = Path(kwargs["out_path"])
        path.write_bytes(b"prepared sounding")
        path.with_suffix(".json").write_text("{}", encoding="utf-8")
        worker.requestInterruption()
        return str(path)
    monkeypatch.setattr(model_extract, "extract", extract)
    worker.run()
    assert cancelled == [True] and not completed
    assert worker.result[0] == str(tmp_path / "sounding.npz")
    assert Path(worker.result[0]).is_file()


def test_native_prestart_cancel_never_extracts(qt_app, tmp_path, monkeypatch):
    worker = NativePointWorker("gfs", 35.22, -97.44, VALID, 0,
                              str(tmp_path / "sounding.npz"), download_dir=str(tmp_path))
    monkeypatch.setattr(model_extract, "extract", lambda *_a, **_k: pytest.fail("cancelled work started"))
    cancelled = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.requestInterruption()
    worker.run()
    assert cancelled == [True] and worker.result is None
