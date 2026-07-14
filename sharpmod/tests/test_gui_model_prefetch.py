"""Forecast-fetch cancellation and optional prefetch regressions."""

from __future__ import annotations

from datetime import datetime, timezone

from qtpy.QtWidgets import QApplication

from sharpmod.gui_settings import _build_settings
from sharpmod.gui_workers import _ModelPrefetchWorker


def test_prefetch_setting_is_disabled_by_default(tmp_path):
    settings = _build_settings(path=tmp_path / "settings.ini")

    assert settings.value("model/prefetch_next_hour", True, bool) is False


def test_prefetch_worker_cancelled_before_run_does_not_touch_cache():
    QApplication.instance() or QApplication([])

    class ExplodingCache:
        def lease(self, *_args, **_kwargs):
            raise AssertionError("cancelled prefetch must not lease the cache")

    worker = _ModelPrefetchWorker(
        "hrrr", 35.0, -97.0,
        datetime(2026, 7, 14, tzinfo=timezone.utc),
        1, None, ExplodingCache(),
    )
    emitted = []
    worker.cancelled.connect(lambda: emitted.append("cancelled"))
    worker.requestInterruption()

    worker.run()

    assert emitted == ["cancelled"]
