"""Forecast-timeline GUI queue integration without network access."""

from datetime import datetime, timezone
from types import SimpleNamespace

from qtpy.QtWidgets import QApplication

from sharpmod import batch_extract
from sharpmod.gui_timeline import ModelTimelineWorker


def test_timeline_worker_streams_completed_and_missing_hours(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    calls = {}

    class FakeExtractor:
        def __init__(self, progress_callback=None):
            self.progress_callback = progress_callback
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def run(self, requests, **kwargs):
            calls["hours"] = [request.fxx for request in requests]
            calls["workers"] = kwargs["max_workers"]
            for request in requests[:2]:
                self.progress_callback({
                    "event": "completed", "request_id": request.id,
                })
            self.progress_callback({
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

    monkeypatch.setattr(batch_extract, "BatchExtractor", FakeExtractor)
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
