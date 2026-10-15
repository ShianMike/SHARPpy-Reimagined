"""The updater must point to the signed installer actually staged for release."""

import base64
import json
from pathlib import Path
import runpy

import pytest


WRITE_MANIFEST = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "packaging/write_updater_manifest.py")
)["write_manifest"]


def test_manifest_uses_renamed_release_asset_and_signature_contents(tmp_path):
    installer = tmp_path / "SHARPpy-Reimagined-v3.1.0-windows-x64-setup.exe"
    installer.write_bytes(b"installer")
    signature = base64.b64encode(b"test signature").decode()
    installer.with_suffix(".exe.sig").write_text(signature + "\n", encoding="utf-8")
    manifest = json.loads(WRITE_MANIFEST(installer, "v3.1.0").read_text())
    assert manifest["version"] == "3.1.0"
    assert manifest["pub_date"].endswith("+00:00")
    platform = manifest["platforms"]["windows-x86_64"]
    assert platform["signature"] == signature
    assert platform["url"] == (
        "https://github.com/ShianMike/SHARPpy-Reimagined/releases/download/"
        "v3.1.0/SHARPpy-Reimagined-v3.1.0-windows-x64-setup.exe"
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
