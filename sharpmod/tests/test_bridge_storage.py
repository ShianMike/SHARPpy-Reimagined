"""Storage cleanup touches only completed, regenerable data in managed roots."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sharpmod.bridge import sounding_cache, storage
from sharpmod.bridge.errors import BridgeError
from sharpmod.models.model_disk_cache import ModelDiskCache
from sharpmod.models.model_hour_cache import ModelHourKey


def write(path: Path, data: bytes = b"cache") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for variable, directory in (("SHARPMOD_MODEL_CACHE", "models"),
                                ("SHARPMOD_HRRR_FIELD_CACHE", "maps"),
                                ("SHARPMOD_OUTLOOK_CACHE", "outlooks")):
        monkeypatch.setenv(variable, str(tmp_path / directory))
    session = sounding_cache.SoundingCache(tmp_path / "session")
    monkeypatch.setattr(sounding_cache, "cache", lambda: session)
    return tmp_path, session


def test_clear_all_keeps_pinned_active_saved_and_open_data_and_reports_real_sizes(roots):
    root, session = roots
    disk = ModelDiskCache()
    directories = [disk.directory_for(ModelHourKey.create("hrrr", datetime(2026, 10, 16, tzinfo=UTC), hour))
                   for hour in range(3)]
    for directory in directories:
        write(directory / "subset.grib2", b"GRIB7777")
    disk.set_pinned(directories[1])
    entry_id = "a" * 16
    profile = write(session.data_path(entry_id, ".npz"), b"keep portable sounding")
    metadata = write(profile.with_suffix(".json"), b"keep metadata")
    rendered = write(session.render_path(entry_id, "ml", "skewt"), b"keep current chart")
    session.add(sounding_cache.Entry(entry_id, profile, {}))
    completed = write(session.root / f"{entry_id}-grib" / "native.grib2", b"GRIBdata7777")
    active = write(session.root / f"{'b' * 16}-grib" / "native.grib2", b"GRIBdata7777")
    map_image = write(root / "maps" / "forecast.png", b"maps")
    partial = write(root / "maps" / "downloading.part", b"in progress")
    outlook = write(root / "outlooks" / "ab" / "outlook.json", b"outlooks")
    saved = write(root / "saved-sounding.npz", b"keep user's export")
    with disk.protect(directories[2]):
        before = storage.usage({})
        assert before["caches"][0] == {"key": "models", "bytes": 48, "files": 5, "clearable_bytes": 20}
        cleared = storage.clear({"cache": "all"})
        assert cleared["freed_bytes"] == 32
        assert cleared["failed_files"] == 0
        assert cleared["usage"] == {"caches": [
            {"key": "models", "bytes": 28, "files": 3, "clearable_bytes": 0},
            {"key": "maps", "bytes": 11, "files": 1, "clearable_bytes": 0},
            {"key": "outlooks", "bytes": 0, "files": 0, "clearable_bytes": 0},
            *[{"key": key, "bytes": 0, "files": 0, "clearable_bytes": 0}
              for key in ("radar", "satellite", "observations", "alerts")],
        ]}
        assert not directories[0].exists()
        assert not any(path.exists() for path in (completed, map_image, outlook))
        assert all(path.exists() for path in (directories[1], directories[2], profile, metadata, rendered, active, partial, saved))
        assert session.get(entry_id).path == profile
        assert storage.clear({"cache": "all"})["freed_bytes"] == 0


@pytest.mark.parametrize("target", ["maps", "outlooks"])
def test_single_category_never_deletes_other_caches(roots, target):
    root, _ = roots
    paths = {"maps": write(root / "maps" / "frame.png"),
             "outlooks": write(root / "outlooks" / "ab" / "outlook.json")}
    result = storage.clear({"cache": target})
    assert result["freed_bytes"] == 5
    assert not paths[target].exists()
    assert all(path.exists() for key, path in paths.items() if key != target)


def test_map_cleanup_counts_downloaded_grids_and_winds_but_keeps_inflight_files(roots):
    root, _ = roots
    grid = write(root / "maps" / "grid-v1.json.gz", b"grid")
    wind = write(root / "maps" / "contours-v1.json.gz", b"winds")
    partial = write(root / "maps" / "downloading.part")
    assert storage.usage({})["caches"][1] == {"key": "maps", "bytes": 14, "files": 3, "clearable_bytes": 9}
    assert storage.clear({"cache": "maps"})["freed_bytes"] == 9
    assert not grid.exists() and not wind.exists() and partial.exists()


@pytest.mark.parametrize("target", ["radar", "satellite", "observations", "alerts"])
def test_live_cleanup_counts_its_own_data_and_keeps_active_writes(roots, target):
    _, session = roots
    live = write(session.root / "live" / target / "frame.json.gz", b"live")
    partial = write(session.root / "live" / target / "downloading.part", b"active")
    others = [write(session.root / "live" / key / "frame.json.gz", b"keep")
              for key in ("radar", "satellite", "observations", "alerts") if key != target]
    assert storage.usage({})["caches"][storage.KEYS.index(target)] == {"key": target, "bytes": 10, "files": 2, "clearable_bytes": 4}
    assert storage.clear({"cache": target})["freed_bytes"] == 4
    assert not live.exists() and partial.exists()
    assert all(path.exists() for path in others)


def test_radar_storage_includes_level2_volumes_and_clears_reusable_memory(roots, monkeypatch):
    from sharpmod.providers import radar_level2, radar_site

    _, session = roots
    volume = write(session.root / "radar-volumes" / "PGUA.ar2", b"native volume")
    image = write(session.root / "live" / "radar" / "frame.json.gz", b"frame")
    partial = write(session.root / "radar-volumes" / "active.part", b"active")
    calls = []
    monkeypatch.setattr(radar_level2, "clear_cache", lambda: calls.append("volumes"))
    monkeypatch.setattr(radar_site, "clear_cache", lambda: calls.append("frames"))
    row = storage.usage({})["caches"][storage.KEYS.index("radar")]
    assert row == {"key": "radar", "bytes": 24, "files": 3, "clearable_bytes": 18}
    result = storage.clear({"cache": "radar"})
    assert result["freed_bytes"] == 18 and calls == ["volumes", "frames"]
    assert not volume.exists() and not image.exists() and partial.exists()


@pytest.mark.parametrize("target", ["maps", "outlooks"])
def test_cleanup_reports_all_partials_and_reclaims_only_abandoned_ones(roots, target):
    root, _ = roots
    abandoned = write(root / target / "ab" / "interrupted.part", b"abandoned")
    recent = write(root / target / "ab" / "writing.part", b"active")
    os.utime(abandoned, (0, 0))
    category = storage.usage({})["caches"][storage.KEYS.index(target)]
    assert category == {"key": target, "bytes": 15, "files": 2, "clearable_bytes": 9}
    result = storage.clear({"cache": "all"})
    assert result["freed_bytes"] == 9 and result["failed_files"] == 0
    assert not abandoned.exists() and recent.read_bytes() == b"active"
    assert result["usage"]["caches"][storage.KEYS.index(target)] == {
        "key": target, "bytes": 6, "files": 1, "clearable_bytes": 0,
    }


def test_model_cache_root_is_never_treated_as_a_deletable_entry(roots):
    root, _ = roots
    disk = ModelDiskCache()
    directory = disk.directory_for(ModelHourKey.create("hrrr", datetime(2026, 10, 16, tzinfo=UTC), 0))
    protected = write(directory / "subset.grib2", b"GRIB7777")
    disk.set_pinned(directory)
    write(root / "models" / ".cache.json", b"{}")
    assert storage.clear({"cache": "models"})["freed_bytes"] == 0
    assert protected.exists()


@pytest.mark.parametrize("params", [{}, {"cache": "../saved"}, {"cache": True},
                                    {"cache": ["maps"]}, {"cache": "all", "path": "elsewhere"}])
def test_clear_rejects_paths_and_unrecognised_arguments_before_scanning(monkeypatch, params):
    monkeypatch.setattr(storage, "_scan", lambda: pytest.fail("invalid input reached the filesystem"))
    with pytest.raises(BridgeError) as exc:
        storage.clear(params)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_partial_failure_is_reported_and_kept_in_usage(roots, monkeypatch):
    root, _ = roots
    blocked = write(root / "maps" / "locked.png")
    removed = write(root / "maps" / "other.png")
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("in use")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    result = storage.clear({"cache": "maps"})
    assert result["freed_bytes"] == 5
    assert result["failed_files"] == 1
    assert result["usage"]["caches"][1]["bytes"] == 5
    assert blocked.exists() and not removed.exists()


def test_links_cannot_make_cleanup_escape_a_cache_root(roots):
    root, _ = roots
    outside = write(root / "saved" / "never-delete.png", b"keep")
    linked = root / "maps" / "linked"
    linked.parent.mkdir()
    try:
        linked.symlink_to(outside.parent, target_is_directory=True)
        (linked.parent / "file-link.png").symlink_to(outside)
    except OSError:
        pytest.skip("creating symbolic links requires OS permission")
    assert storage.clear({"cache": "all"})["freed_bytes"] == 0
    assert outside.read_bytes() == b"keep"


def test_usage_failure_is_not_misreported_as_an_empty_cache(monkeypatch):
    def unreadable():
        raise PermissionError("unreadable")
    monkeypatch.setattr(storage, "_scan", unreadable)
    with pytest.raises(BridgeError, match="Cache usage could not be read"):
        storage.usage({})


def test_clear_scans_model_entries_only_before_and_after_deleting(roots, monkeypatch):
    root, _ = roots
    directory = ModelDiskCache().directory_for(
        ModelHourKey.create("hrrr", datetime(2026, 10, 16, tzinfo=UTC), 0))
    write(directory / "subset.grib2", b"GRIB7777")
    write(root / "maps" / "frame.png")
    scans = 0
    original = ModelDiskCache._entries

    def counted(self):
        nonlocal scans
        scans += 1
        return original(self)

    monkeypatch.setattr(ModelDiskCache, "_entries", counted)
    result = storage.clear({"cache": "all"})
    assert scans == 2
    assert result["freed_bytes"] == 13
    assert result["failed_files"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_cache_scans_and_cleanup_never_follow_windows_junctions(roots):
    from _winapi import CreateJunction

    root, _ = roots
    outside = write(root / "saved" / "never-delete.png", b"keep")
    write(outside.parent / ".cache.json", b"{}")
    for category in ("models", "maps", "outlooks"):
        directory = root / category
        directory.mkdir(exist_ok=True)
        CreateJunction(str(outside.parent), str(directory / "linked"))
    assert ModelDiskCache().entries() == []
    assert storage.clear({"cache": "all"})["freed_bytes"] == 0
    assert outside.read_bytes() == b"keep"
