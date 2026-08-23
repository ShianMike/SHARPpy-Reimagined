"""Persistent bounded forecast-model disk-cache regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path

import numpy as np
import pytest

from sharpmod.model_disk_cache import (
    MODEL_CACHE_CONTRACT_VERSION,
    ModelDiskCache,
    default_model_cache_root,
)
from sharpmod.model_hour_cache import ModelHourKey


RUN = datetime(2026, 7, 14, 0, tzinfo=timezone.utc)


def _key(fxx=0):
    return ModelHourKey.create("hrrr", RUN, fxx)


def _write_portable_pair(directory, name="era5-point"):
    path = directory / f"{name}.npz"
    levels = np.array([1000.0, 900.0, 800.0])
    np.savez(
        path,
        pres=levels,
        hght=np.array([100.0, 1000.0, 2000.0]),
        tmpc=np.array([20.0, 15.0, 10.0]),
        dwpc=np.array([15.0, 10.0, 5.0]),
        wdir=np.array([180.0, 200.0, 220.0]),
        wspd=np.array([10.0, 20.0, 30.0]),
        omeg=np.array([0.0, -1.0, -2.0]),
        valid="2026-07-14 00:00",
        run="2026-07-14 00:00",
        loc="ERA5",
        lat=35.0,
        lon=-97.0,
    )
    path.with_suffix(".json").write_text("{}", encoding="utf-8")
    return path


def test_default_cache_root_honors_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_MODEL_CACHE", str(tmp_path / "chosen"))

    assert default_model_cache_root() == tmp_path / "chosen"


def test_directory_is_stable_and_survives_prune_under_limits(tmp_path):
    cache = ModelDiskCache(tmp_path, max_bytes=1024, max_age_hours=24)

    first = cache.directory_for(_key())
    second = cache.directory_for(_key())
    (first / "subset.grib2").write_bytes(b"GRIB7777")
    removed = cache.prune()

    assert first == second
    assert first.exists()
    assert removed == []
    assert first.relative_to(tmp_path).parts[0] == (
        f"v{MODEL_CACHE_CONTRACT_VERSION}"
    )


def test_point_subset_regions_use_different_directories(tmp_path):
    cache = ModelDiskCache(tmp_path)
    left = ModelHourKey.create("hrrr", RUN, 0, spatial="35,-97")
    right = ModelHourKey.create("hrrr", RUN, 0, spatial="36,-98")

    assert cache.directory_for(left) != cache.directory_for(right)


def test_prune_removes_oldest_entries_until_size_limit(tmp_path):
    cache = ModelDiskCache(tmp_path, max_bytes=10, max_age_hours=24)
    oldest = cache.directory_for(_key(0))
    newest = cache.directory_for(_key(1))
    (oldest / "subset.grib2").write_bytes(b"12345678")
    (newest / "subset.grib2").write_bytes(b"abcdefgh")
    for path, accessed in ((oldest, 1.0), (newest, 2.0)):
        metadata = json.loads((path / ".cache.json").read_text("utf-8"))
        metadata["accessed"] = accessed
        (path / ".cache.json").write_text(json.dumps(metadata), "utf-8")

    removed = cache.prune(now=3.0)

    assert oldest in removed
    assert not oldest.exists()
    assert newest.exists()


def test_protected_entry_is_not_pruned(tmp_path):
    cache = ModelDiskCache(tmp_path, max_bytes=1, max_age_hours=0)
    directory = cache.directory_for(_key())
    (directory / "subset.grib2").write_bytes(b"too large")

    with cache.protect(directory):
        assert cache.prune(now=10_000_000_000.0) == []
        assert directory.exists()

    assert cache.prune(now=10_000_000_000.0) == [directory]
    assert not directory.exists()


def test_clear_removes_only_managed_entries(tmp_path):
    cache = ModelDiskCache(tmp_path)
    managed = cache.directory_for(_key())
    unmanaged = tmp_path / "consumer-file.txt"
    unmanaged.write_text("keep", encoding="utf-8")

    cache.clear()

    assert not managed.exists()
    assert unmanaged.exists()


def test_entries_expose_metadata_validity_and_newest_first(tmp_path):
    cache = ModelDiskCache(tmp_path)
    older = cache.directory_for(_key(0))
    newer = cache.directory_for(_key(1))
    (older / "subset.grib2").write_bytes(b"GRIB7777")
    (newer / "incomplete.part").write_bytes(b"partial")
    cache.touch(older, now=1.0)
    cache.touch(newer, now=2.0)

    entries = cache.entries()

    assert [entry.fxx for entry in entries] == [1, 0]
    assert entries[0].valid_grib is False
    assert entries[1].valid_grib is True
    assert entries[1].model == "hrrr"


def test_entries_recognize_portable_sounding_pair(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    sounding = _write_portable_pair(directory)

    entry = cache.entries()[0]

    assert entry.valid_grib is False
    assert entry.valid_sounding is True
    assert cache.valid_sounding_paths(directory) == (sounding,)


def test_stale_contract_payloads_are_visible_but_never_reused(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    sounding = _write_portable_pair(directory)
    grib = directory / "subset.grib2"
    grib.write_bytes(b"GRIB7777")
    metadata_path = directory / ".cache.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["contract_version"] = MODEL_CACHE_CONTRACT_VERSION - 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    entry = cache.entries()[0]

    assert entry.contract_version == MODEL_CACHE_CONTRACT_VERSION - 1
    assert entry.valid_grib is False
    assert entry.valid_sounding is False
    assert cache.valid_grib_paths(directory) == ()
    assert cache.valid_sounding_paths(directory) == ()
    assert sounding.exists()
    assert grib.exists()


def test_entries_reject_malformed_npz_members_and_select_only_valid_pairs(
        tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    malformed = directory / "00-bad.npz"
    malformed.write_bytes(b"not an npz")
    malformed.with_suffix(".json").write_text("{}", encoding="utf-8")
    valid = _write_portable_pair(directory, "01-good")

    entry = cache.entries()[0]

    assert entry.valid_sounding is True
    assert cache.valid_sounding_paths(directory) == (valid,)


def test_only_malformed_portable_pair_is_never_reported_ready(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    malformed = directory / "bad.npz"
    malformed.write_bytes(b"not an npz")
    malformed.with_suffix(".json").write_text("{}", encoding="utf-8")

    entry = cache.entries()[0]

    assert entry.valid_sounding is False
    assert cache.valid_sounding_paths(directory) == ()


def test_source_provenance_can_be_copied_from_cache_entry(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())

    cache.annotate(
        directory,
        source_url="https://example.test/model.grib2",
        source_transport="parallel-ranges",
        source_fields=("TMP", "HGT"),
    )
    entry = cache.entries()[0]

    assert entry.source_url.endswith("model.grib2")
    assert entry.source_transport == "parallel-ranges"
    assert entry.source_fields == ("TMP", "HGT")


def test_range_fragments_are_never_exposed_as_reusable_grib(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    fragments = directory / ".subset.grib2.ranges"
    fragments.mkdir()
    (fragments / "0-11.part").write_bytes(b"GRIBxxxx7777")

    entry = cache.entries()[0]

    assert entry.valid_grib is False
    assert entry.file_count == 0
    assert cache.valid_grib_paths(directory) == ()


def test_complete_grib_payload_can_be_opened_for_offline_reextract(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    grib = directory / "subset.grib2"
    grib.write_bytes(b"GRIB7777")
    (directory / "ignored.part").write_bytes(b"GRIB7777")

    assert cache.valid_grib_paths(directory) == (grib,)


def test_pinned_entries_survive_prune_and_default_clear(tmp_path):
    cache = ModelDiskCache(tmp_path, max_bytes=1, max_age_hours=0)
    directory = cache.directory_for(_key())
    (directory / "subset.grib2").write_bytes(b"GRIB7777")

    pinned = cache.set_pinned(directory, True)

    assert pinned.pinned is True
    assert cache.prune(now=10_000_000_000.0) == []
    assert cache.clear() == []
    assert directory.exists()
    assert cache.clear(include_pinned=True) == [directory]


def test_explicit_delete_rejects_unmanaged_and_respects_lease(tmp_path):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())

    with pytest.raises(ValueError):
        cache.delete(tmp_path / "not-managed")
    with cache.protect(directory):
        assert cache.delete(directory) is False
    assert cache.delete(directory) is True


def test_dead_process_lease_is_removed_and_does_not_block_clear(
        tmp_path, monkeypatch):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    marker = directory / ".lease-999999-dead"
    marker.touch()
    monkeypatch.setattr(cache, "_process_is_running", lambda _pid: False)

    removed = cache.clear(include_pinned=True)

    assert removed == [directory]
    assert not directory.exists()


def test_windows_invalid_pid_system_error_is_treated_as_not_running(
        monkeypatch):
    cause = OSError(errno.EINVAL, "The parameter is incorrect")

    def invalid_pid(_pid, _signal):
        raise SystemError(
            "<built-in function kill> returned a result with an exception set"
        ) from cause

    monkeypatch.setattr(os, "kill", invalid_pid)

    assert ModelDiskCache._process_is_running(999_999_999) is False


def test_expired_unparseable_lease_is_removed(tmp_path, monkeypatch):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    marker = directory / ".lease-legacy"
    marker.touch()
    monkeypatch.setenv("SHARPMOD_MODEL_CACHE_LEASE_HOURS", "1")
    old = 1_000.0
    marker.touch()
    os.utime(marker, (old, old))

    assert cache._lease_is_active(marker, now=old + 3601.0) is False
    assert not marker.exists()


def test_clear_does_not_report_failed_deletion(tmp_path, monkeypatch):
    cache = ModelDiskCache(tmp_path)
    directory = cache.directory_for(_key())
    (directory / "subset.grib2").write_bytes(b"GRIB7777")
    monkeypatch.setattr(cache, "_remove_tree", lambda _path: False)

    assert cache.clear(include_pinned=True) == []
    assert directory.exists()


def test_prune_keeps_failed_deletion_in_size_accounting(tmp_path, monkeypatch):
    cache = ModelDiskCache(tmp_path, max_bytes=1, max_age_hours=0)
    directory = cache.directory_for(_key())
    (directory / "subset.grib2").write_bytes(b"GRIB7777")
    monkeypatch.setattr(cache, "_remove_tree", lambda _path: False)

    assert cache.prune(now=10_000_000_000.0) == []
    assert directory.exists()
