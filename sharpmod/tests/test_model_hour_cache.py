"""Bounded forecast model-hour cache ownership regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from sharpmod.model_hour_cache import ModelHourCache, ModelHourKey


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
