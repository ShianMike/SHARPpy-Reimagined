"""Fast contracts for aligned model/run comparison acquisition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sharpmod.gui_model_compare import (
    ComparisonRequestSpec,
    ModelComparisonWorker,
    plan_ensemble,
    plan_model_comparison,
    plan_run_comparison,
)


VALID = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


def test_model_comparison_plans_exact_shared_valid_time():
    specs = plan_model_comparison(
        ("hrrr", "rap", "nam-3km-conus"),
        VALID,
        anchor_model="hrrr",
        anchor_run=VALID - timedelta(hours=6),
        anchor_fxx=6,
    )

    assert [spec.model for spec in specs] == ["hrrr", "rap", "nam-3km-conus"]
    assert specs[0].run_time == VALID - timedelta(hours=6)
    assert all(spec.valid_time == VALID for spec in specs)
    assert len({spec.request_id for spec in specs}) == len(specs)


def test_run_comparison_keeps_anchor_then_uses_older_compatible_cycles():
    specs = plan_run_comparison(
        "hrrr",
        VALID,
        anchor_run=VALID - timedelta(hours=2),
        anchor_fxx=2,
        count=4,
    )

    assert [spec.run_time.hour for spec in specs] == [10, 9, 8, 7]
    assert [spec.fxx for spec in specs] == [2, 3, 4, 5]
    assert all(spec.valid_time == VALID for spec in specs)


def test_ensemble_plan_is_control_plus_perturbations_with_unique_artifacts():
    specs = plan_ensemble("gefs", run_time=VALID - timedelta(hours=6), fxx=6, count=5)

    assert [spec.member for spec in specs] == ["c00", "p01", "p02", "p03", "p04"]
    assert len({spec.request_id for spec in specs}) == 5
    assert all(spec.valid_time == VALID for spec in specs)


def test_comparison_worker_uses_one_bounded_batch_and_streams_progress(
    tmp_path, monkeypatch
):
    from sharpmod import gui_batch_process

    calls = {}

    class FakeRunner:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def run(self, requests, **kwargs):
            requests = tuple(requests)
            calls["requests"] = requests
            calls["workers"] = kwargs["max_workers"]
            progress_callback = kwargs["progress_callback"]
            for request in requests:
                progress_callback(
                    {
                        "event": "completed",
                        "request_id": request.id,
                    }
                )
            return SimpleNamespace(completed=len(requests), items=())

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", FakeRunner)
    specs = (
        ComparisonRequestSpec("hrrr-a", "hrrr", "HRRR", VALID - timedelta(hours=1), 1),
        ComparisonRequestSpec("rap-a", "rap", "RAP", VALID - timedelta(hours=2), 2),
    )
    progress = []
    results = []
    worker = ModelComparisonWorker(specs, 35.0, -97.0, tmp_path, loc="TEST")
    worker.progress.connect(
        lambda stage, completed, total: progress.append((stage, completed, total))
    )
    worker.result_ready.connect(results.append)

    worker.run()

    assert [request.model for request in calls["requests"]] == ["hrrr", "rap"]
    assert [request.fxx for request in calls["requests"]] == [1, 2]
    assert calls["workers"] == 2
    assert progress[-1] == ("completed", 2, 2)
    assert results[0].completed == 2
