"""Forecast-timeline GUI queue integration without network access."""

from datetime import datetime, timezone
from types import SimpleNamespace

from qtpy.QtWidgets import QApplication

from sharpmod import gui_batch_process
from sharpmod.gui_timeline import ModelTimelineWorker


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
