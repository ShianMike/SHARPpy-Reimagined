"""Fast contracts for aligned model/run comparison acquisition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod.gui_model_compare import (
    ComparisonRequestSpec,
    ModelComparisonWorker,
    _merge_retried_member,
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


def test_retry_fills_an_existing_members_missing_valid_time_without_overwrite():
    earlier = VALID - timedelta(hours=1)
    original = object()
    recovered = object()
    collection = SimpleNamespace(
        _dates=[earlier, VALID],
        _profs={"p02": [original]},
    )
    decoded = SimpleNamespace(
        _dates=[VALID],
        _profs={"deterministic": [recovered]},
    )

    assert _merge_retried_member(collection, "p02", decoded)
    assert collection._profs["p02"] == [original, recovered]

    newer = object()
    decoded._profs["deterministic"] = [newer]
    assert not _merge_retried_member(collection, "p02", decoded)
    assert collection._profs["p02"] == [original, recovered]


def test_retry_adds_a_new_member_aligned_to_the_ensemble_dates():
    collection = SimpleNamespace(
        _dates=[VALID - timedelta(hours=1), VALID],
        _profs={},
    )
    recovered = object()
    decoded = SimpleNamespace(
        _dates=[VALID],
        _profs={"deterministic": [recovered]},
    )

    assert _merge_retried_member(collection, "p05", decoded)
    assert collection._profs["p05"] == [None, recovered]


def test_retry_matches_the_same_utc_instant_across_portable_datetime_shapes():
    recovered = object()
    collection = SimpleNamespace(_dates=[VALID.replace(tzinfo=None)], _profs={})
    decoded = SimpleNamespace(_dates=[VALID], _profs={"": [recovered]})
    assert _merge_retried_member(collection, "p05", decoded)
    assert collection._profs["p05"] == [recovered]


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


@pytest.mark.parametrize("outcome", ("cancelled", "failed", "returned-after-cancel"))
def test_comparison_worker_retains_a_stopped_child_result(
    tmp_path, monkeypatch, outcome,
):
    from sharpmod import gui_batch_process

    result = SimpleNamespace(completed=1, items=())
    worker = ModelComparisonWorker(
        (ComparisonRequestSpec("gfs", "gfs", "GFS", VALID, 0),),
        35.0, -97.0, tmp_path,
    )

    class Runner:
        def cancel(self):
            pass

        def run(self, *_args, **_kwargs):
            if outcome == "failed":
                raise gui_batch_process.IsolatedBatchError("child stopped", result=result)
            worker.requestInterruption()
            if outcome == "cancelled":
                raise gui_batch_process.IsolatedBatchCancelled("stopped", result=result)
            return result

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", Runner)
    cancelled, ready, failed = [], [], []
    worker.cancelled.connect(cancelled.append)
    worker.result_ready.connect(ready.append)
    worker.failed.connect(failed.append)
    worker.run()

    assert worker.result is result
    assert not ready
    if outcome == "failed":
        assert not cancelled
        assert failed and "child stopped" in failed[0]
    else:
        assert cancelled == [result]
        assert not failed


def test_comparison_worker_cancel_before_start_never_spawns_a_child(tmp_path, monkeypatch):
    from sharpmod import gui_batch_process

    def unexpected_runner():
        pytest.fail("cancelled work must not spawn a child")

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", unexpected_runner)
    worker = ModelComparisonWorker(
        (ComparisonRequestSpec("gfs", "gfs", "GFS", VALID, 0),),
        35.0, -97.0, tmp_path,
    )
    cancelled, ready = [], []
    worker.cancelled.connect(cancelled.append)
    worker.result_ready.connect(ready.append)
    worker.requestInterruption()
    worker.run()

    assert cancelled == [None]
    assert not ready


def test_comparison_cancel_during_runner_construction_still_skips_the_child(tmp_path, monkeypatch):
    from sharpmod import gui_batch_process

    worker = ModelComparisonWorker(
        (ComparisonRequestSpec("gfs", "gfs", "GFS", VALID, 0),), 35, -97, tmp_path,
    )
    class Runner:
        def __init__(self):
            worker.requestInterruption()

        def run(self, *_args, **_kwargs):
            pytest.fail("cancellation before child creation must skip extraction")

    monkeypatch.setattr(gui_batch_process, "IsolatedBatchRunner", Runner)
    cancelled = []
    worker.cancelled.connect(cancelled.append)
    worker.run()
    assert cancelled == [None] and not worker.batch_started
