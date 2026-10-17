"""Member acquisition for thresholds, with offline download stand-ins.

The member download is replaced by the exporter's own stand-in (one fixed
offset per member, one member that fails), so the module runs offline and
fast; decode, combine, and the acquisition ledger are the real code.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sharpmod.bridge import ensemble
from sharpmod.bridge.errors import BridgeError

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts" / "export_sounding_fixtures.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_sounding_fixtures", EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORT = _exporter()


def _acquire(**overrides):
    request = dict(EXPORT.ENSEMBLE_REQUEST)
    request.update(overrides)
    with patch("sharpmod.tools.model_extract.extract", EXPORT.stand_in_extract(EXPORT.EXAMPLE)):
        return ensemble.acquire(request)


def test_member_acquisition_keeps_partial_failures_in_the_ledger():
    _, ledger = _acquire()
    # The ledger says what was asked for, what loaded, and why the rest did not.
    assert ledger.requested_members == ("c00", "p01", "p02", "p03")
    assert ledger.loaded_members == ("c00", "p01", "p02")
    assert len(ledger.failures) == 1
    failed = ledger.failures[0]
    assert (failed.member, failed.status, failed.reason) == (
        "p03", "failed", "no GRIB found for member p03",
    )


@pytest.mark.parametrize("fail_all", [False, True])
def test_ensemble_removes_member_downloads_and_metadata(monkeypatch, tmp_path, fail_all):
    from sharpmod.bridge import sounding_cache
    from sharpmod.tools import era5_extract, model_extract

    monkeypatch.setattr(sounding_cache, "_CACHE", sounding_cache.SoundingCache(tmp_path))
    stand_in = EXPORT.stand_in_extract(EXPORT.EXAMPLE)
    downloads = []

    def extract(*args, download_dir, **kwargs):
        directory = Path(download_dir)
        assert directory.parent == Path(kwargs["out_path"]).parent
        assert directory.parent.parent == tmp_path
        downloads.append(directory)
        directory.mkdir()
        (directory / "member.grib2").write_bytes(b"GRIB")
        (directory / "member.grib2.provenance.json").write_text("{}")
        if fail_all:
            raise era5_extract.RetrievalError("offline")
        return stand_in(*args, **kwargs)

    monkeypatch.setattr(model_extract, "extract", extract)
    if fail_all:
        with pytest.raises(BridgeError, match="could not be retrieved"):
            ensemble.acquire(dict(EXPORT.ENSEMBLE_REQUEST))
    else:
        ensemble.acquire(dict(EXPORT.ENSEMBLE_REQUEST))
    assert len(downloads) == len(EXPORT.ENSEMBLE_REQUEST["members"])
    assert all(not directory.parent.exists() for directory in downloads)


def test_ensemble_refuses_bad_member_requests():
    with pytest.raises(BridgeError) as info:
        _acquire(members=["c00"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _acquire(members=["c00", "c00"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _acquire(members=["c00", "p77"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _acquire(members="c00")
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _acquire(members=["c00"] + [f"p{i:02d}" for i in range(1, 32)])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _acquire(model="hrrr")
    assert info.value.code == "INVALID_ARGUMENT" and "deterministic" in info.value.message
    with pytest.raises(BridgeError) as info:
        _acquire(model="nope")
    assert info.value.code == "INVALID_ARGUMENT"


def test_ensemble_isolates_member_failures_and_fails_when_none_load(monkeypatch):
    from sharpmod.tools import era5_extract
    from sharpmod.tools import model_extract as mx

    def unreachable(*_args, **_kwargs):
        raise era5_extract.RetrievalError("the archive did not answer\ntraceback lines")

    monkeypatch.setattr(mx, "extract", unreachable)
    with pytest.raises(BridgeError) as info:
        ensemble.acquire(dict(EXPORT.ENSEMBLE_REQUEST))
    assert info.value.code == "SOUNDING_FETCH_FAILED" and info.value.retryable is True
    assert "traceback" not in info.value.message

    def out_of_domain(*_args, **_kwargs):
        raise era5_extract.ParameterRangeError("outside the domain")

    monkeypatch.setattr(mx, "extract", out_of_domain)
    with pytest.raises(BridgeError) as info:
        ensemble.acquire(dict(EXPORT.ENSEMBLE_REQUEST))
    assert info.value.code == "INVALID_ARGUMENT"


def test_the_ensemble_handler_loads_no_qt_module():
    code = (
        "import sys; from sharpmod.bridge import ensemble; "
        "import sharpmod.bridge.handlers; "
        "print(sorted(m for m in sys.modules if m.startswith(('PySide', 'PyQt'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
