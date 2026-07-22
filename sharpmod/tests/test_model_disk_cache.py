"""Persistent bounded forecast-model disk-cache regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

import pytest

from sharpmod.model_disk_cache import ModelDiskCache, default_model_cache_root
from sharpmod.model_hour_cache import ModelHourKey


RUN = datetime(2026, 7, 14, 0, tzinfo=timezone.utc)


def _key(fxx=0):
    return ModelHourKey.create("hrrr", RUN, fxx)


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
    with zipfile.ZipFile(directory / "era5-point.npz", "w") as archive:
        for name in (
            "pres.npy", "hght.npy", "tmpc.npy", "dwpc.npy", "wdir.npy",
            "wspd.npy", "omeg.npy", "valid.npy", "run.npy", "loc.npy",
        ):
            archive.writestr(name, b"value")
    (directory / "era5-point.json").write_text("{}", encoding="utf-8")

    entry = cache.entries()[0]

    assert entry.valid_grib is False
    assert entry.valid_sounding is True


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
