"""The bridge's ensemble summary against real sharpmod, with offline stand-ins.

The member download is replaced by the exporter's own stand-in (one fixed
offset per member, one member that fails), so the module runs offline and
fast; decode, combine, ledger, and diagnostics are the real code. The result
is pinned to the exported contract fixture, so drift breaks the suite.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sharpmod.bridge import ensemble
from sharpmod.bridge.errors import BridgeError

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "src-tauri" / "contracts"
EXPORTER = ROOT / "scripts" / "export_sounding_fixtures.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_sounding_fixtures", EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORT = _exporter()


def _fixture(relative: str):
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


def _summary(**overrides):
    request = dict(EXPORT.ENSEMBLE_REQUEST)
    request.update(overrides)
    with patch("sharpmod.tools.model_extract.extract", EXPORT.stand_in_extract(EXPORT.EXAMPLE)):
        return ensemble.summary(request)


def test_ensemble_summary_matches_the_exported_fixture():
    assert _summary() == _fixture("bridge/soundings/ensemble.result.example.json")


def test_ensemble_summary_labels_every_metric_and_keeps_the_ledger():
    result = _summary()
    # The ledger says what was asked for, what loaded, and why the rest did not.
    assert result["requested_members"] == ["c00", "p01", "p02", "p03"]
    assert result["loaded_members"] == ["c00", "p01", "p02"]
    assert result["failures"] == [
        {"member": "p03", "status": "failed", "reason": "no GRIB found for member p03"}
    ]
    assert result["valid_time"] == "2026-06-26T00:00:00Z" and result["reason"] is None
    # Each diagnostic arrives labelled; the frontend names no quantity.
    keys = [row["key"] for row in result["metrics"]]
    assert keys == ["mlcape", "mlcin", "shear_6km", "srh_1km", "stp_cin"]
    first = result["metrics"][0]
    assert (first["label"], first["units"], first["decimals"]) == ("MLCAPE", "J/kg", 0)
    assert first["count"] == 3 and first["p10"] <= first["median"] <= first["p90"]
    assert first["minimum"] <= first["p10"] and first["maximum"] >= first["p90"]
    # Every requested member keeps one row with its own outcome and reason.
    samples = {row["member"]: row for row in result["samples"]}
    assert set(samples) == {"c00", "p01", "p02", "p03"}
    assert all(samples[name]["outcome"] == "completed" for name in ("c00", "p01", "p02"))
    assert all(len(samples[name]["values"]) == len(keys) for name in samples)
    missing = samples["p03"]
    assert missing["outcome"] == "unavailable" and missing["values"] == [None] * 5
    assert "no GRIB found for member p03" in missing["reason"]


def test_ensemble_metric_rows_agree_with_the_member_samples():
    result = _summary()
    completed = [row for row in result["samples"] if row["outcome"] == "completed"]
    for index, metric in enumerate(result["metrics"]):
        column = [row["values"][index] for row in completed]
        assert metric["count"] == sum(value is not None for value in column)
        assert metric["minimum"] == min(column) and metric["maximum"] == max(column)


def test_ensemble_refuses_bad_member_requests():
    with pytest.raises(BridgeError) as info:
        _summary(members=["c00"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _summary(members=["c00", "c00"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _summary(members=["c00", "p77"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _summary(members="c00")
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _summary(members=["c00"] + [f"p{i:02d}" for i in range(1, 32)])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        _summary(model="hrrr")
    assert info.value.code == "INVALID_ARGUMENT" and "deterministic" in info.value.message
    with pytest.raises(BridgeError) as info:
        _summary(model="nope")
    assert info.value.code == "INVALID_ARGUMENT"


def test_ensemble_isolates_member_failures_and_fails_when_none_load(monkeypatch):
    from sharpmod.tools import era5_extract
    from sharpmod.tools import model_extract as mx

    def unreachable(*_args, **_kwargs):
        raise era5_extract.RetrievalError("the archive did not answer\ntraceback lines")

    monkeypatch.setattr(mx, "extract", unreachable)
    with pytest.raises(BridgeError) as info:
        ensemble.summary(dict(EXPORT.ENSEMBLE_REQUEST))
    assert info.value.code == "SOUNDING_FETCH_FAILED" and info.value.retryable is True
    assert "traceback" not in info.value.message

    def out_of_domain(*_args, **_kwargs):
        raise era5_extract.ParameterRangeError("outside the domain")

    monkeypatch.setattr(mx, "extract", out_of_domain)
    with pytest.raises(BridgeError) as info:
        ensemble.summary(dict(EXPORT.ENSEMBLE_REQUEST))
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
