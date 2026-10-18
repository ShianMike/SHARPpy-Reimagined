"""Live responses belong to this process, not persistent model/provider caches."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sharpmod.bridge import live_cache, sounding_cache


@pytest.fixture
def session(tmp_path, monkeypatch):
    owner = sounding_cache.SoundingCache(tmp_path / "session")
    monkeypatch.setattr(live_cache, "cache", lambda: owner)
    return owner


def test_live_results_reuse_session_bytes_and_corruption_refetches(session):
    calls = []

    def handler(params):
        calls.append(params)
        return {"scan": len(calls)}

    assert live_cache.call("radar.mosaic", {}, handler) == {"scan": 1}
    assert live_cache.call("radar.mosaic", {}, handler) == {"scan": 1}
    (target,) = (session.root / "live").rglob("*.json.gz")
    target.write_bytes(b"interrupted download")
    assert live_cache.call("radar.mosaic", {}, handler) == {"scan": 2}
    os.utime(target, (time.time() - 61, time.time() - 61))
    assert live_cache.call("radar.mosaic", {}, handler) == {"scan": 3}


def test_history_survives_ttl_but_live_overlays_can_update(session):
    old = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    calls = []

    def handler(params):
        calls.append(params)
        return {"scan": len(calls)}

    for method in (
        "radar.mosaic",
        "radar.site",
        "satellite.goes",
        "surface.plots",
        "alerts.weather",
    ):
        request = {"valid_time": old, "method": method}
        before = live_cache.call(method, request, handler)
        for target in (session.root / "live").rglob("*.json.gz"):
            os.utime(target, (time.time() - 120, time.time() - 120))
        after = live_cache.call(method, request, handler)
        assert (before == after) == (
            method in {"radar.mosaic", "radar.site", "satellite.goes", "alerts.weather"}
        )


def test_session_responses_have_entry_and_byte_budgets(session, monkeypatch):
    monkeypatch.setattr(live_cache, "MAX_ENTRIES", 2)
    for index in range(4):
        live_cache.call("radar.mosaic", {"index": index}, lambda params: params)
    assert len(list((session.root / "live").rglob("*.json.gz"))) == 2
    budget = max(
        path.stat().st_size for path in (session.root / "live").rglob("*.json.gz")
    )
    monkeypatch.setattr(live_cache, "MAX_BYTES", budget)
    live_cache.call("radar.mosaic", {"index": 5}, lambda params: params)
    assert (
        sum(path.stat().st_size for path in (session.root / "live").rglob("*.json.gz"))
        <= budget
    )


def test_live_categories_share_the_global_budget_and_keep_their_own_paths(session, monkeypatch):
    monkeypatch.setattr(live_cache, "MAX_ENTRIES", 2)
    for method, category in live_cache.METHODS.items():
        live_cache.call(method, {}, lambda _: {"source": method})
        assert len(list((session.root / "live").rglob("*.json.gz"))) <= 2
        assert len(list((session.root / "live" / category).glob("*.json.gz"))) >= 1


def test_previous_minute_alert_snapshot_stays_available_without_refetching(session):
    when = (datetime.now(UTC) - timedelta(minutes=1)).replace(second=0, microsecond=0).isoformat()
    calls = []
    def handler(params):
        calls.append(params)
        return {"alerts": ["original snapshot"]}
    request = {"valid_time": when}
    expected = live_cache.call("alerts.weather", request, handler)
    for target in (session.root / "live").rglob("*.json.gz"):
        os.utime(target, (time.time() - 120, time.time() - 120))
    assert live_cache.call("alerts.weather", request, handler) == expected
    assert len(calls) == 1


def test_locked_files_cannot_make_the_cache_grow_beyond_its_budget(
    session, monkeypatch
):
    monkeypatch.setattr(live_cache, "MAX_ENTRIES", 1)
    live_cache.call("radar.mosaic", {"index": 0}, lambda params: params)
    (protected,) = (session.root / "live").rglob("*.json.gz")
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == protected:
            raise PermissionError("in use")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    assert live_cache.call("radar.mosaic", {"index": 1}, lambda params: params) == {
        "index": 1
    }
    assert list((session.root / "live").rglob("*.json.gz")) == [protected]


def test_non_live_operations_and_errors_are_not_cached(session):
    calls = []

    def handler(params):
        calls.append(params)
        raise ValueError("provider unavailable")

    for _ in range(2):
        with pytest.raises(ValueError, match="provider unavailable"):
            live_cache.call("radar.mosaic", {}, handler)
    assert len(calls) == 2
    for _ in range(2):
        assert (
            live_cache.call("soundings.analysis", {}, lambda _: calls.append(1)) is None
        )
    assert len(calls) == 4
    assert not (session.root / "live").exists()


def test_unwritable_session_cache_does_not_hide_live_data(monkeypatch):
    def unavailable():
        raise PermissionError("cache directory is not writable")

    monkeypatch.setattr(live_cache, "cache", unavailable)
    assert live_cache.call("radar.mosaic", {}, lambda _: {"scan": 1}) == {"scan": 1}


def test_concurrent_responses_are_complete_and_bounded(session, monkeypatch):
    monkeypatch.setattr(live_cache, "MAX_ENTRIES", 3)

    def fetch(index):
        return live_cache.call(
            "radar.mosaic",
            {"index": index},
            lambda params: {**params, "body": "x" * 1000},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, range(12)))
    assert [item["index"] for item in results] == list(range(12))
    assert len(list((session.root / "live").rglob("*.json.gz"))) <= 3
    assert not list((session.root / "live").rglob("*.part"))


def test_existing_atexit_cleanup_removes_live_files(tmp_path):
    env = {**os.environ, "SHARPMOD_BRIDGE_CACHE": str(tmp_path)}
    script = """
from sharpmod.bridge import live_cache, sounding_cache
from sharpmod.providers import radar_level2
live_cache.call('radar.mosaic', {}, lambda _: {'png': 'session-only'})
root = sounding_cache.cache().root
assert len(list((root / 'live').rglob('*.json.gz'))) == 1
radar_level2.disk_cache_root().mkdir()
(radar_level2.disk_cache_root() / 'volume.ar2').write_bytes(b'native radar')
print(root)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    root = Path(result.stdout.strip())
    assert root.parent == tmp_path
    assert not root.exists()
