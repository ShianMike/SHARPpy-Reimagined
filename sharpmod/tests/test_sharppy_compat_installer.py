"""Contracts for the hash-pinned upstream SHARPpy compatibility installer."""

from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
from email.policy import default
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install_sharppy_compat.py"
SPEC = importlib.util.spec_from_file_location("install_sharppy_compat", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
compat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compat
SPEC.loader.exec_module(compat)


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _record_bytes(files: dict[str, bytes], record_path: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name, data in files.items():
        writer.writerow((name, _record_hash(data), len(data)))
    writer.writerow((record_path, "", ""))
    return stream.getvalue().encode()


def _synthetic_upstream_wheel(
    path: Path,
    *,
    numpy_requirement: str = "numpy (==1.15.*)",
) -> Path:
    dist_info = "SHARPpy-1.4.0a5.dist-info"
    record_path = f"{dist_info}/RECORD"
    files = {
        "sharppy/__init__.py": b'__version__ = "1.4.0a5"\n',
        "sharppy/databases/sample.txt": b"upstream payload\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: SHARPpy\n"
            "Version: 1.4.0a5\n"
            "Requires-Dist: python-dateutil\n"
            "Requires-Dist: requests\n"
            f"Requires-Dist: {numpy_requirement}\n"
            "\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
    }
    files[record_path] = _record_bytes(files, record_path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_project_numpy_requirement_matches_core_metadata():
    assert compat.project_numpy_requirement(ROOT) == "numpy>=1.24,<3.0"


def test_pinned_upstream_artifact_contract_is_explicit():
    assert compat.UPSTREAM_FILENAME == \
        "SHARPpy-1.4.0a5-py2.py3-none-any.whl"
    assert compat.UPSTREAM_SIZE == 13_580_446
    assert compat.UPSTREAM_SHA256 == (
        "13582f88ba1932b842cbf3ceb6f5f1ddadc17b0b2fd9172a3fc74ed0bcadb868"
    )
    assert compat.UPSTREAM_URL.startswith(
        "https://files.pythonhosted.org/"
    )
    assert compat.UPSTREAM_URL.endswith(compat.UPSTREAM_FILENAME)


def test_download_retries_transient_connection_reset(tmp_path, monkeypatch):
    payload = b"verified"
    calls = []
    sleeps = []

    class Response:
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def geturl():
            return compat.UPSTREAM_URL

        def read(self, _size):
            nonlocal payload
            result, payload = payload, b""
            return result

    def fake_urlopen(_request, *, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise ConnectionResetError("connection reset by peer")
        return Response()

    verified = b"verified"
    monkeypatch.setattr(compat, "UPSTREAM_SIZE", len(verified))
    monkeypatch.setattr(
        compat,
        "UPSTREAM_SHA256",
        hashlib.sha256(verified).hexdigest(),
    )
    monkeypatch.setattr(compat, "urlopen", fake_urlopen)
    monkeypatch.setattr(compat.time, "sleep", sleeps.append)

    destination = tmp_path / compat.UPSTREAM_FILENAME
    result = compat.download_upstream_wheel(
        destination,
        timeout=12.0,
        retry_delay=0.25,
    )

    assert result.read_bytes() == verified
    assert calls == [12.0, 12.0]
    assert sleeps == [0.25]


def test_repack_changes_only_metadata_and_auditing_files(tmp_path):
    source = _synthetic_upstream_wheel(
        tmp_path / compat.UPSTREAM_FILENAME
    )
    output = tmp_path / "patched" / compat.UPSTREAM_FILENAME
    expected_sha = _sha256(source)
    result = compat.repack_wheel(
        source,
        output,
        "numpy>=1.24,<3.0",
        expected_sha256=expected_sha,
        expected_size=source.stat().st_size,
    )

    assert result.path == output
    assert result.source_sha256 == expected_sha
    assert result.patched_sha256 == _sha256(output)
    assert output.is_file()

    with zipfile.ZipFile(source) as upstream, zipfile.ZipFile(output) as patched:
        metadata_path = "SHARPpy-1.4.0a5.dist-info/METADATA"
        record_path = "SHARPpy-1.4.0a5.dist-info/RECORD"
        provenance_path = (
            "SHARPpy-1.4.0a5.dist-info/SHARPMOD-PROVENANCE.json"
        )
        excluded = {metadata_path, record_path, provenance_path}
        for name in upstream.namelist():
            if name not in excluded:
                assert patched.read(name) == upstream.read(name)

        metadata = BytesParser(policy=default).parsebytes(
            patched.read(metadata_path)
        )
        requirements = metadata.get_all("Requires-Dist", [])
        assert "numpy>=1.24,<3.0" in requirements
        assert "numpy (==1.15.*)" not in requirements

        provenance = json.loads(patched.read(provenance_path))
        assert provenance["upstream"]["sha256"] == expected_sha
        assert provenance["metadata_change"] == {
            "field": "Requires-Dist",
            "original": "numpy (==1.15.*)",
            "replacement": "numpy>=1.24,<3.0",
        }
        assert provenance["upstream_payload_files_unchanged"] is True
        compat._verify_record(patched, record_path)


def test_repack_is_deterministic_and_reuses_identical_output(tmp_path):
    source = _synthetic_upstream_wheel(
        tmp_path / compat.UPSTREAM_FILENAME
    )
    output = tmp_path / "patched" / compat.UPSTREAM_FILENAME
    kwargs = {
        "expected_sha256": _sha256(source),
        "expected_size": source.stat().st_size,
    }

    first = compat.repack_wheel(
        source, output, "numpy>=1.24,<3.0", **kwargs
    )
    second = compat.repack_wheel(
        source, output, "numpy>=1.24,<3.0", **kwargs
    )

    assert first.patched_sha256 == second.patched_sha256
    assert output.is_file()


def test_repack_rejects_hash_drift_before_writing(tmp_path):
    source = _synthetic_upstream_wheel(
        tmp_path / compat.UPSTREAM_FILENAME
    )
    output = tmp_path / "patched" / compat.UPSTREAM_FILENAME

    with pytest.raises(
        compat.CompatibilityInstallError,
        match="SHA-256 mismatch",
    ):
        compat.repack_wheel(
            source,
            output,
            "numpy>=1.24,<3.0",
            expected_sha256="0" * 64,
            expected_size=source.stat().st_size,
        )

    assert not output.exists()


def test_repack_rejects_unexpected_upstream_metadata(tmp_path):
    source = _synthetic_upstream_wheel(
        tmp_path / compat.UPSTREAM_FILENAME,
        numpy_requirement="numpy>=1.24",
    )
    output = tmp_path / "patched" / compat.UPSTREAM_FILENAME

    with pytest.raises(
        compat.CompatibilityInstallError,
        match="changed unexpectedly",
    ):
        compat.repack_wheel(
            source,
            output,
            "numpy>=1.24,<3.0",
            expected_sha256=_sha256(source),
            expected_size=source.stat().st_size,
        )

    assert not output.exists()
