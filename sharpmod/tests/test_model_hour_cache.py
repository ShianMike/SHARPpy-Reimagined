"""Bounded forecast model-hour cache ownership regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from sharpmod.model_hour_cache import ModelHourCache, ModelHourKey
from sharpmod.models.model_transport import DownloadCancelled


class _FakeDataset:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def _key(model="hrrr", hour=0, fxx=0, member=None):
    return ModelHourKey.create(
        model,
        datetime(2026, 7, 14, hour, tzinfo=timezone.utc),
        fxx,
        member,
    )


def test_model_hour_key_normalizes_timezone_and_blank_member():
    local = timezone(timedelta(hours=8))

    left = ModelHourKey.create(
        "HRRR", datetime(2026, 7, 14, 8, tzinfo=local), 0, "  "
    )
    right = _key()

    assert left == right
    assert left.run_time == datetime(2026, 7, 14, 0, tzinfo=timezone.utc)
    assert left.member is None


def test_model_hour_key_separates_point_subset_regions():
    left = ModelHourKey.create(
        "hrrr", datetime(2026, 7, 14, tzinfo=timezone.utc), 0,
        spatial="35.0000,-97.0000",
    )
    right = ModelHourKey.create(
        "hrrr", datetime(2026, 7, 14, tzinfo=timezone.utc), 0,
        spatial="36.0000,-98.0000",
    )

    assert left != right
    assert left.spatial == "35.0000,-97.0000"


def test_repeated_key_reuses_dataset_and_cache_directory():
    cache = ModelHourCache(max_entries=1)
    dataset = _FakeDataset()
    loads = []

    def loader(download_dir):
        loads.append(Path(download_dir))
        Path(download_dir, "subset.grib2").write_bytes(b"grib")
        return dataset, SimpleNamespace(grib="memory://hrrr")

    with cache.lease(_key(), loader) as (first, first_hit):
        assert first_hit is False
        assert first.dataset is dataset
        assert first.source_grib == "memory://hrrr"
        assert Path(first.download_dir, "subset.grib2").exists()

    with cache.lease(_key(), loader) as (second, second_hit):
        assert second_hit is True
        assert second is first

    assert len(loads) == 1
    assert dataset.close_calls == 0
    cache.clear()
    assert dataset.close_calls == 1
    assert not loads[0].exists()


def test_loaded_source_metadata_is_forwarded_to_persistent_cache():
    written = []
    cache = ModelHourCache(
        max_entries=1,
        metadata_writer=lambda directory, **values: written.append(
            (directory, values)
        ),
    )
    source = SimpleNamespace(
        grib="https://example.test/subset.grib2",
        _sharpmod_fields=("TMP", "HGT"),
        _sharpmod_transport="parallel-ranges",
    )

    with cache.lease(_key(), lambda _directory: (_FakeDataset(), source)):
        pass

    assert written[0][1] == {
        "source_url": "https://example.test/subset.grib2",
        "source_transport": "parallel-ranges",
        "source_fields": ("TMP", "HGT"),
    }
    cache.clear()


def test_one_entry_limit_evicts_and_closes_previous_hour():
    cache = ModelHourCache(max_entries=1)
    first_dataset = _FakeDataset()
    second_dataset = _FakeDataset()

    with cache.lease(
        _key(hour=0), lambda _directory: (first_dataset, None)
    ):
        pass
    with cache.lease(
        _key(hour=1), lambda _directory: (second_dataset, None)
    ):
        pass

    assert first_dataset.close_calls == 1
    assert second_dataset.close_calls == 0
    cache.clear()
    assert second_dataset.close_calls == 1


def test_failed_load_removes_partial_cache_directory():
    cache = ModelHourCache(max_entries=1)
    created = []

    def loader(download_dir):
        created.append(Path(download_dir))
        Path(download_dir, "partial.grib2").write_bytes(b"partial")
        raise RuntimeError("download failed")

    with pytest.raises(RuntimeError, match="download failed"):
        with cache.lease(_key(), loader):
            pass

    assert len(created) == 1
    assert not created[0].exists()


def test_clear_defers_disposal_until_active_lease_finishes():
    cache = ModelHourCache(max_entries=1)
    dataset = _FakeDataset()
    created = []

    def loader(download_dir):
        created.append(Path(download_dir))
        return dataset, None

    with cache.lease(_key(), loader):
        cache.clear()
        assert dataset.close_calls == 0
        assert created[0].exists()

    assert dataset.close_calls == 1
    assert not created[0].exists()


def test_exception_during_lease_invalidates_bad_dataset():
    cache = ModelHourCache(max_entries=1)
    datasets = [_FakeDataset(), _FakeDataset()]
    created = []

    def loader(download_dir):
        created.append(Path(download_dir))
        return datasets[len(created) - 1], None

    with pytest.raises(RuntimeError, match="decode failed"):
        with cache.lease(_key(), loader):
            raise RuntimeError("decode failed")

    assert datasets[0].close_calls == 1
    assert not created[0].exists()

    with cache.lease(_key(), loader) as (_entry, cache_hit):
        assert cache_hit is False

    assert len(created) == 2
    cache.clear()


def test_legacy_caller_error_invalidates_entry_but_keeps_another_active_lease():
    cache = ModelHourCache()
    dataset = _FakeDataset()
    with cache.lease(_key(), lambda _directory: (dataset, None)) as (entry, _hit):
        with pytest.raises(RuntimeError, match="point failed"):
            with cache.lease(_key(), lambda _directory: pytest.fail("source reloaded")):
                raise RuntimeError("point failed")
        assert len(cache) == 0 and dataset.close_calls == 0
        assert Path(entry.download_dir).exists()
    assert dataset.close_calls == 1 and not Path(entry.download_dir).exists()


def test_same_key_concurrent_leases_share_one_inflight_load():
    cache = ModelHourCache(max_entries=1)
    dataset = _FakeDataset()
    started = threading.Event()
    release = threading.Event()
    load_count = 0
    load_lock = threading.Lock()

    def loader(_download_dir):
        nonlocal load_count
        with load_lock:
            load_count += 1
        started.set()
        assert release.wait(timeout=5)
        return dataset, None

    def lease_once():
        with cache.lease(_key(), loader) as (entry, hit):
            return entry, hit

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(lease_once)
        assert started.wait(timeout=5)
        follower = pool.submit(lease_once)
        release.set()
        first = leader.result(timeout=5)
        second = follower.result(timeout=5)

    assert load_count == 1
    assert first[0] is second[0]
    assert sorted((first[1], second[1])) == [False, True]
    cache.clear()


def test_persistent_directory_factory_keeps_download_after_memory_clear(
        tmp_path):
    persistent = tmp_path / "persistent"
    cache = ModelHourCache(
        max_entries=1,
        directory_factory=lambda _key: persistent,
        delete_download_dirs=False,
    )

    def loader(download_dir):
        Path(download_dir, "subset.grib2").write_bytes(b"GRIB7777")
        return _FakeDataset(), None

    with cache.lease(_key(), loader):
        pass
    cache.clear()

    assert (persistent / "subset.grib2").read_bytes() == b"GRIB7777"


def test_persistent_directory_preserves_partial_failed_download(tmp_path):
    persistent = tmp_path / "persistent"
    cache = ModelHourCache(
        max_entries=1,
        directory_factory=lambda _key: persistent,
        delete_download_dirs=False,
    )

    def loader(download_dir):
        Path(download_dir, "range.part").write_bytes(b"partial")
        raise RuntimeError("network failed")

    with pytest.raises(RuntimeError, match="network failed"):
        with cache.lease(_key(), loader):
            pass

    assert (persistent / "range.part").read_bytes() == b"partial"


@pytest.mark.parametrize("stopped_consumer", [0, 1])
def test_shared_load_stop_releases_either_consumer_without_killing_the_other(stopped_consumer):
    cache = ModelHourCache()
    entered, finish = threading.Event(), threading.Event()
    stops = [threading.Event(), threading.Event()]
    received = [[], []]
    datasets = []

    def loader(directory, *, cancelled, progress_callback, on_bytes):
        Path(directory, "source.grib2").write_bytes(b"GRIB7777")
        entered.set()
        assert finish.wait(5)
        assert not cancelled()
        progress_callback("downloading", 8)
        on_bytes(8)
        dataset = _FakeDataset()
        datasets.append(dataset)
        return dataset, None

    def consume(index):
        with cache.lease(_key(), loader, cancelled=stops[index].is_set,
                         on_bytes=received[index].append) as (entry, _hit):
            return entry.dataset

    try:
        with ThreadPoolExecutor(2) as pool:
            owner = pool.submit(consume, 0)
            assert entered.wait(5)
            waiter = pool.submit(consume, 1)
            deadline = time.monotonic() + 5
            while True:
                with cache._condition:
                    joined = len(cache._inflight[_key()].consumers) == 2
                if joined:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.005)
            stops[stopped_consumer].set()
            try:
                with pytest.raises(DownloadCancelled):
                    (owner, waiter)[stopped_consumer].result(timeout=2)
            finally:
                finish.set()
            assert (owner, waiter)[1 - stopped_consumer].result(timeout=5) is datasets[0]
        assert received[stopped_consumer] == []
        assert received[1 - stopped_consumer] == [8]
        assert len(datasets) == 1 and len(cache) == 1
        assert not cache._inflight and not cache.protected_directories()
    finally:
        finish.set()
        cache.clear()
    assert datasets[0].close_calls == 1


def test_last_shared_consumer_stop_discards_late_success_and_does_not_block_retry():
    cache = ModelHourCache()
    entered, finish, exited = (threading.Event() for _ in range(3))
    stop = threading.Event()
    dataset = _FakeDataset()
    directories = []

    def loader(directory, *, cancelled, **_callbacks):
        directories.append(Path(directory))
        entered.set()
        try:
            assert finish.wait(5)
            assert cancelled()
            return dataset, None
        finally:
            exited.set()

    def consume():
        with cache.lease(_key(), loader, cancelled=stop.is_set):
            pytest.fail("cancelled consumer acquired a source")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(consume)
        assert entered.wait(5)
        stop.set()
        try:
            with pytest.raises(DownloadCancelled):
                future.result(timeout=2)
            with cache.lease(_key(), lambda _directory: (_FakeDataset(), None)):
                pass
        finally:
            finish.set()
    assert exited.wait(5)
    deadline = time.monotonic() + 5
    while directories[0].exists():
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert dataset.close_calls == 1
    assert len(cache) == 1  # The abandoned owner's late result did not replace the retry.
    cache.clear()


def test_shared_load_failure_wakes_every_waiter_and_releases_directory():
    cache = ModelHourCache()
    entered, finish = threading.Event(), threading.Event()
    directories = []

    def loader(directory, **_callbacks):
        directories.append(Path(directory))
        entered.set()
        assert finish.wait(5)
        raise RuntimeError("source failed")

    def consume():
        with cache.lease(_key(), loader, cancelled=lambda: False):
            pass

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(consume)]
        assert entered.wait(5)
        futures.append(pool.submit(consume))
        deadline = time.monotonic() + 5
        while len(cache._inflight[_key()].consumers) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        finish.set()
        for future in futures:
            with pytest.raises(RuntimeError, match="source failed"):
                future.result(timeout=5)
    assert not cache._inflight and not cache.protected_directories()
    assert not directories[0].exists()


@pytest.mark.parametrize("abandoned_failure", [False, True])
def test_blocked_background_load_bounds_stop_new_key_cycles_and_same_key_retry(abandoned_failure):
    cache = ModelHourCache()
    started = [threading.Event(), threading.Event()]
    finish = [threading.Event(), threading.Event()]
    directories, datasets = [], []
    lock = threading.Lock()
    active = peak = 0

    def loader(directory, *, cancelled, **_callbacks):
        nonlocal active, peak
        with lock:
            index = len(directories)
            directories.append(Path(directory))
            active += 1
            peak = max(peak, active)
        try:
            assert index < 2, "a cancelled pending key started a producer"
            Path(directory, "subset.grib2").write_bytes(b"GRIB7777")
            started[index].set()
            assert finish[index].wait(10)
            if index == 0:
                assert cancelled()
                if abandoned_failure:
                    raise RuntimeError("abandoned source failed")
            dataset = _FakeDataset()
            datasets.append(dataset)
            return dataset, None
        finally:
            with lock:
                active -= 1

    def consume(key, stop, checked=None):
        def cancelled():
            if checked is not None:
                checked.set()
            return stop.is_set()
        with cache.lease(key, loader, cancelled=cancelled) as (entry, _hit):
            return entry.dataset

    with ThreadPoolExecutor(2) as pool:
        stop = threading.Event()
        owner = pool.submit(consume, _key(), stop)
        assert started[0].wait(5)
        try:
            stop.set()
            with pytest.raises(DownloadCancelled):
                owner.result(timeout=2)
            for hour in range(1, 13):
                pending_stop, checked = threading.Event(), threading.Event()
                pending = pool.submit(consume, _key(fxx=hour), pending_stop, checked)
                assert checked.wait(5)
                pending_stop.set()
                with pytest.raises(DownloadCancelled):
                    pending.result(timeout=2)
                assert len(directories) == 1 and cache._background_loads == 1
            checked = threading.Event()
            retry = pool.submit(consume, _key(), threading.Event(), checked)
            assert checked.wait(5) and not started[1].is_set()
            finish[0].set()
            assert started[1].wait(5)
            assert not directories[0].exists()
            follower = pool.submit(consume, _key(), threading.Event())
            deadline = time.monotonic() + 5
            while len(cache._inflight[_key()].consumers) < 2:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            finish[1].set()
            assert retry.result(timeout=5) is follower.result(timeout=5)
        finally:
            for event in finish:
                event.set()
    assert peak == 1 and active == 0 and len(directories) == 2
    deadline = time.monotonic() + 5
    while cache._background_loads:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert not cache._inflight and len(cache) == 1
    cache.clear()
    assert not any(directory.exists() for directory in directories)
    assert all(dataset.close_calls == 1 for dataset in datasets)


def test_clear_during_shared_load_preserves_consumer_lease_without_reinserting():
    cache = ModelHourCache()
    entered, loaded, finish, release = (threading.Event() for _ in range(4))
    directories = []
    dataset = _FakeDataset()

    def loader(directory, **_callbacks):
        directories.append(Path(directory))
        entered.set()
        assert finish.wait(5)
        return dataset, None

    def consume():
        with cache.lease(_key(), loader, cancelled=lambda: False):
            loaded.set()
            assert release.wait(5)
            assert directories[0].exists() and dataset.close_calls == 0

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(consume)
        assert entered.wait(5)
        cache.clear()
        try:
            assert tuple(map(str, directories)) == cache.protected_directories()
            finish.set()
            assert loaded.wait(5)
            assert len(cache) == 0 and directories[0].exists()
        finally:
            finish.set()
            release.set()
        future.result(timeout=5)
    assert dataset.close_calls == 1 and not directories[0].exists()
