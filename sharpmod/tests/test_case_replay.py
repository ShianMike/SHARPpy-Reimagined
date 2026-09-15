"""Historical replay and portable offline-case package contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import threading
import zipfile

import pytest

from sharpmod import case_replay
from sharpmod.case_replay import (
    CaseAsset,
    CaseError,
    CaseManifest,
    ReplayClock,
    create_case_package,
    download_case_assets,
    load_case_package,
)


UTC = timezone.utc
T0 = datetime(2024, 5, 20, 18, tzinfo=UTC)
T1 = T0 + timedelta(minutes=10)
T2 = T0 + timedelta(minutes=20)


def _asset(asset_id, kind, event, *, available=None, status="complete"):
    return CaseAsset(
        asset_id,
        kind,
        event,
        available,
        f"https://example.test/{asset_id}.dat",
        status=status,
    )


def test_replay_clock_pauses_steps_loops_and_selects_exact_times():
    clock = ReplayClock((T2, T0, T1))
    assert clock.current == T0
    assert clock.play() == T0 and clock.playing
    assert clock.tick() == T1
    assert clock.step(99) == T2 and not clock.playing
    assert clock.select(T0) == T0
    with pytest.raises(CaseError, match="exact"):
        clock.select(T0 + timedelta(minutes=1))

    looping = ReplayClock((T0, T1), loop=True)
    assert looping.step(-1) == T1
    assert looping.step(1) == T0


def test_training_mode_reveals_only_known_availability_times():
    manifest = CaseManifest(
        "May 20 case",
        (
            _asset("radar", "radar-level3", T0, available=T1),
            _asset("outlook", "outlook", T0, available=T0),
            _asset("unknown", "storm-reports", T0, available=None),
            _asset("missing", "satellite", T0, available=T0, status="missing"),
        ),
    )

    assert {item.asset_id for item in manifest.visible_assets(T0)} == {
        "radar",
        "outlook",
        "unknown",
    }
    assert {
        item.asset_id for item in manifest.visible_assets(T0, training_mode=True)
    } == {"outlook"}
    assert {
        item.asset_id for item in manifest.visible_assets(T1, training_mode=True)
    } == {
        "radar",
        "outlook",
    }
    assert manifest.training_limitations == ("unknown: availability time is unknown",)


def test_package_round_trip_verifies_bytes_and_keeps_missing_explicit(tmp_path):
    radar = tmp_path / "radar.nids"
    radar.write_bytes(b"level-3 bytes")
    sounding = tmp_path / "sounding.sharppy-session"
    sounding.write_bytes(b'{"format":"sharpmod-analysis-session"}')
    manifest = CaseManifest(
        "Portable case",
        (
            _asset("radar", "radar-level3", T0, available=T1),
            _asset("sounding", "sounding-session", T0, available=T0),
            _asset("satellite", "satellite", T0, available=T0, status="planned"),
        ),
        notes="Training package",
        source_session_version=2,
    )

    path = create_case_package(
        tmp_path / "case.sharppy-case",
        manifest,
        {"radar": radar, "sounding": sounding},
    )
    loaded = load_case_package(path)

    assert loaded.manifest.name == "Portable case"
    assert loaded.asset_bytes("radar") == b"level-3 bytes"
    assert loaded.asset_bytes("sounding").startswith(b"{")
    missing = next(
        item for item in loaded.manifest.assets if item.asset_id == "satellite"
    )
    assert missing.status == "missing"
    assert "not packaged" in missing.note


def test_package_streams_source_files_instead_of_buffering_them(monkeypatch, tmp_path):
    source = tmp_path / "large-notes.bin"
    source.write_bytes(b"case-data" * 200_000)
    asset = _asset("notes", "notes", T0, available=T0)
    original = Path.read_bytes

    def guarded_read_bytes(path):
        if path == source:
            pytest.fail("case-package source was buffered with Path.read_bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    package = create_case_package(
        tmp_path / "streamed.sharpmod-case",
        CaseManifest("Streamed package", (asset,)),
        {"notes": source},
    )
    loaded = load_case_package(package)

    assert loaded.asset_bytes("notes") == b"case-data" * 200_000


def test_package_integrity_failure_is_detected_before_assets_are_exposed(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("original", encoding="utf-8")
    manifest = CaseManifest(
        "Tamper test", (_asset("notes", "notes", T0, available=T0),)
    )
    path = create_case_package(tmp_path / "case.zip", manifest, {"notes": source})

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(path, "r") as source_archive, zipfile.ZipFile(
        tampered, "w"
    ) as target_archive:
        for name in source_archive.namelist():
            payload = (
                b"changed"
                if name == "assets/notes.txt"
                else source_archive.read(name)
            )
            target_archive.writestr(name, payload)
    path = tampered

    with pytest.raises(CaseError, match="integrity"):
        load_case_package(path)


def test_unsafe_or_unlisted_members_are_rejected_without_extraction(tmp_path):
    path = tmp_path / "unsafe.zip"
    manifest = CaseManifest("Unsafe", ())
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest.as_dict()))
        archive.writestr("../payload.py", b"raise RuntimeError('executed')")

    with pytest.raises(CaseError, match="unsafe member"):
        load_case_package(path)
    assert not (tmp_path.parent / "payload.py").exists()


def test_missing_package_and_manifest_asset_raise_case_errors(tmp_path):
    with pytest.raises(CaseError, match="could not be read"):
        load_case_package(tmp_path / "missing.sharpmod-case")

    asset = CaseAsset(
        "missing-bytes",
        "notes",
        T0,
        T0,
        "https://example.test/missing.dat",
        package_name="assets/missing.dat",
        sha256="0" * 64,
        size_bytes=1,
        status="complete",
    )
    package = tmp_path / "missing-member.sharpmod-case"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(CaseManifest("Missing member", (asset,)).as_dict()),
        )

    with pytest.raises(CaseError, match="could not be read"):
        load_case_package(package)


def test_duplicate_or_oversized_expanded_zip_members_are_rejected(
    monkeypatch, tmp_path
):
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("assets/repeated.bin", b"first")
            archive.writestr("assets/repeated.bin", b"second")
    with pytest.raises(CaseError, match="duplicate"):
        load_case_package(duplicate)

    expanded = tmp_path / "expanded.zip"
    with zipfile.ZipFile(expanded, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("assets/compressed.bin", b"0" * 600)
    assert expanded.stat().st_size < 500
    monkeypatch.setattr(case_replay, "MAX_PACKAGE_BYTES", 500)
    with pytest.raises(CaseError, match="expanded data"):
        load_case_package(expanded)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_bounded_download_records_failures_and_preserves_successes(tmp_path):
    manifest = CaseManifest(
        "Download",
        (
            _asset("good", "radar-image", T0, available=T0, status="planned"),
            _asset("bad", "satellite", T0, available=T0, status="planned"),
        ),
    )

    def opener(request, timeout):
        assert timeout == 5.0
        if request.full_url.endswith("bad.dat"):
            raise OSError("provider unavailable")
        return _Response(b"usable bytes")

    result = download_case_assets(
        manifest, tmp_path / "downloads", opener=opener, timeout=5.0, max_workers=2
    )

    by_id = {item.asset_id: item for item in result.manifest.assets}
    assert by_id["good"].status == "complete"
    assert by_id["good"].sha256
    assert result.files["good"].read_bytes() == b"usable bytes"
    assert by_id["bad"].status == "failed"
    assert "provider unavailable" in by_id["bad"].note


def test_download_streams_to_the_partial_file(monkeypatch, tmp_path):
    manifest = CaseManifest(
        "Streamed download",
        (_asset("remote", "notes", T0, status="planned"),),
    )
    original = Path.write_bytes

    def guarded_write_bytes(path, payload):
        if path.name.startswith(".remote"):
            pytest.fail("download payload was buffered before writing")
        return original(path, payload)

    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)

    result = download_case_assets(
        manifest,
        tmp_path / "downloads",
        opener=lambda *_args, **_kwargs: _Response(b"chunked bytes"),
        max_workers=1,
    )

    assert result.files["remote"].read_bytes() == b"chunked bytes"


def test_cancellation_retains_any_finished_downloads(tmp_path):
    cancelled = threading.Event()
    manifest = CaseManifest(
        "Cancelled download",
        tuple(
            _asset(f"asset-{index}", "radar-image", T0, status="planned")
            for index in range(3)
        ),
    )

    def progress(done, total, _asset_value):
        assert total == 3
        if done == 1:
            cancelled.set()

    result = download_case_assets(
        manifest,
        tmp_path / "downloads",
        opener=lambda *_args, **_kwargs: _Response(b"frame"),
        max_workers=1,
        cancel=cancelled,
        progress=progress,
    )

    assert result.cancelled
    complete = [item for item in result.manifest.assets if item.status == "complete"]
    assert complete, "cancellation discarded downloads that had already completed"
    assert all(result.files[item.asset_id].is_file() for item in complete)
