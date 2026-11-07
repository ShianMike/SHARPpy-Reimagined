"""The updater must point to the signed installer actually staged for release."""

import base64
import json
from pathlib import Path
import runpy

import pytest


WRITE_MANIFEST = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "packaging/write_updater_manifest.py")
)["write_manifest"]
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_manifest_uses_renamed_release_asset_and_signature_contents(tmp_path):
    version = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["version"]
    release = json.loads((FRONTEND / "release-notes.json").read_text(encoding="utf-8"))[version]
    tag = f"v{version}"
    installer = tmp_path / f"SHARPpy-Reimagined-{tag}-windows-x64-setup.exe"
    installer.write_bytes(b"installer")
    signature = base64.b64encode(b"test signature").decode()
    installer.with_suffix(".exe.sig").write_text(signature + "\n", encoding="utf-8")
    manifest = json.loads(WRITE_MANIFEST(installer, tag).read_text(encoding="utf-8"))
    assert manifest["version"] == version
    assert manifest["pub_date"].endswith("+00:00")
    summary, _, highlights = manifest["notes"].partition("\n\n")
    assert summary == release["summary"]
    assert highlights.splitlines() == [
        f"- {item['title']}: {item['description']}" for item in release["highlights"]
    ]
    platform = manifest["platforms"]["windows-x86_64"]
    assert platform["signature"] == signature
    assert platform["url"] == (
        "https://github.com/ShianMike/SHARPpy-Reimagined/releases/download/"
        f"{tag}/{installer.name}"
    )


def test_manifest_refuses_missing_or_invalid_signature(tmp_path):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    with pytest.raises(FileNotFoundError):
        WRITE_MANIFEST(installer, "v3.1.0")
    for invalid in ("", "not a base64 signature"):
        installer.with_suffix(".exe.sig").write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            WRITE_MANIFEST(installer, "v3.1.0")
    assert not (tmp_path / "latest.json").exists()


def test_manifest_requires_highlights_for_the_exact_release(tmp_path):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    installer.with_suffix(".exe.sig").write_text(
        base64.b64encode(b"test signature").decode(), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="No release highlights for 99.0.0"):
        WRITE_MANIFEST(installer, "v99.0.0")
    assert not (tmp_path / "latest.json").exists()


def test_current_application_version_has_release_highlights():
    version = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["version"]
    release = json.loads((FRONTEND / "release-notes.json").read_text(encoding="utf-8"))[version]
    assert isinstance(release["summary"], str) and release["summary"].strip()
    assert release["highlights"]
    for item in release["highlights"]:
        assert isinstance(item["title"], str) and item["title"].strip()
        assert isinstance(item["description"], str) and item["description"].strip()
