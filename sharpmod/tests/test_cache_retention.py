"""Retention must respect managed roots, in-flight writes and dataset leases."""

from datetime import UTC, datetime
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

from sharpmod.models.model_hour_cache import ModelHourKey
from sharpmod.providers import hrrr_field
from sharpmod.tools import batch_extract


def test_map_retention_reserves_space_and_preserves_active_parts(tmp_path, monkeypatch):
    root = tmp_path / "maps"
    root.mkdir()
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(root))
    monkeypatch.setattr(hrrr_field, "MAX_MAP_DISK_BYTES", 16)
    old = root / "tmp-2m_2026101800_f00_2048x1024.png"
    old.write_bytes(b"oldframe")
    os.utime(old, (1, 1))
    recent = root / "grid-v1_hrrr_tmp-2m_2026101800_f001_2048x1024.json.gz"
    recent.write_bytes(b"newframe")
    active = root / "tmpabcdefgh.part"
    active.write_bytes(b"inflight")
    unmanaged = root / "my-notes.txt"
    unmanaged.write_text("keep")
    nested = root / "unmanaged"
    nested.mkdir()
    (nested / "keep.png").write_bytes(b"keep")

    assert hrrr_field._prune_disk_cache()
    assert not old.exists() and recent.exists() and active.exists()
    incoming = root / "tmp-2m_2026101800_f02_2048x1024.png"
    hrrr_field._disk_write(incoming, b"incoming")
    assert not recent.exists()
    assert incoming.read_bytes() == b"incoming"
    assert active.read_bytes() == b"inflight" and unmanaged.read_text() == "keep"
    assert (nested / "keep.png").read_bytes() == b"keep"
    oversized = root / "tmp-2m_2026101800_f03_2048x1024.png"
    hrrr_field._disk_write(oversized, b"x" * 17)
    assert not oversized.exists()

    real_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == incoming:
            raise PermissionError("in use")
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    cannot_fit = root / "tmp-2m_2026101800_f04_2048x1024.png"
    hrrr_field._disk_write(cannot_fit, b"newframe")
    assert not cannot_fit.exists()
    os.utime(active, (time.time() - 7200,) * 2)
    hrrr_field._prune_disk_cache()
    assert not active.exists()


def test_batch_retention_runs_after_cached_dataset_and_lease_close(tmp_path, monkeypatch):
    root = tmp_path / "models"
    closed = []
    key = ModelHourKey("hrrr", datetime(2026, 10, 18, tzinfo=UTC), 0)
    created = []
    def run(_self, _requests, *, model_hour_cache, **_kwargs):
        def load(directory):
            path = Path(directory)
            (path / "subset.grib2").write_bytes(b"GRIB7777")
            created.append(path)
            return SimpleNamespace(close=lambda: closed.append(True)), {}
        with model_hour_cache.lease(key, load) as (entry, _hit):
            assert entry.download_dir and Path(entry.download_dir).exists()
            assert not closed
        assert created[0].exists()  # The cache still owns the dataset and lease.
        return SimpleNamespace(completed=1, failed=0, cancelled=0, skipped=0,
                               manifest_path=tmp_path / "manifest.json", ok=True)
    monkeypatch.setattr(batch_extract, "load_batch_spec", lambda _spec: [])
    monkeypatch.setattr(batch_extract.BatchExtractor, "run", run)
    assert batch_extract.main([str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "out"),
                               "--cache-root", str(root), "--cache-max-bytes", "0"]) == 0
    assert closed == [True]
    assert not created[0].exists()


def test_map_retention_rejects_a_reparse_root_and_unmanaged_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    other = tmp_path.parent / "not-a-managed-map.png"
    hrrr_field._disk_write(other, b"keep out")
    assert not other.exists()
    real_stat = Path.lstat
    def reparse(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return real_stat(path)
    monkeypatch.setattr(Path, "lstat", reparse)
    assert not hrrr_field._prune_disk_cache()


def test_map_retention_keeps_unmanaged_images_and_gzip_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(hrrr_field, "MAX_MAP_DISK_BYTES", 0)
    for name in ("my-map.png", "notes.json.gz", "unfinished.part"):
        path = tmp_path / name
        path.write_bytes(b"unmanaged")
        os.utime(path, (1, 1))
    assert hrrr_field._prune_disk_cache()
    assert {path.name for path in tmp_path.iterdir()} == {"my-map.png", "notes.json.gz", "unfinished.part"}


def test_map_retention_reconciles_simultaneous_process_writes(tmp_path):
    code = """
import os, sys, time
from pathlib import Path
from sharpmod.providers import hrrr_field
root, hour = Path(sys.argv[1]), sys.argv[2]
os.environ['SHARPMOD_HRRR_FIELD_CACHE'] = str(root)
hrrr_field.MAX_MAP_DISK_BYTES = 10
original = hrrr_field.tempfile.mkstemp
def simultaneous(*args, **kwargs):
    (root / ('ready' + hour)).touch()
    deadline = time.monotonic() + 10
    while len(list(root.glob('ready*'))) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return original(*args, **kwargs)
hrrr_field.tempfile.mkstemp = simultaneous
hrrr_field._disk_write(root / ('tmp-2m_2026101800_f' + hour + '_2048x1024.png'), b'12345678')
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), hour])
                for hour in ("01", "02")]
    try:
        assert [child.wait(timeout=20) for child in children] == [0, 0]
        assert sum(path.stat().st_size for path in tmp_path.glob("*.png")) <= 10
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
