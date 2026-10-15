"""The bridge's threshold explorer against real sharpmod, offline stand-ins.

The member download is replaced by the exporter's own stand-in (one fixed
offset per member, one member that fails), so the module runs offline and
fast; decode, combine, the evaluator, and the CSV writer are the real code.
The results are pinned to the exported contract fixtures, so drift breaks
the suite.
"""

from __future__ import annotations

import importlib.util
import csv
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sharpmod.bridge import thresholds
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


def _request(**overrides):
    request = dict(EXPORT.ENSEMBLE_REQUEST)
    request["definition"] = json.loads(json.dumps(EXPORT.THRESHOLD_DEFINITION))
    request.update(overrides)
    return request


def _evaluate(**overrides):
    request = _request(**overrides)
    with patch("sharpmod.tools.model_extract.extract", EXPORT.stand_in_extract(EXPORT.EXAMPLE)):
        return thresholds.evaluate(request)


def test_threshold_evaluation_matches_the_exported_fixture():
    expected = _fixture("bridge/soundings/threshold.result.example.json")
    decimals = {row["key"]: row["decimals"] for row in _fixture(
        "bridge/soundings/threshold-metrics.result.json")["metrics"]}
    # Match numbers below 1/100 of their display step; keep all other fields exact.
    for member in expected["members"]:
        member["values"] = {
            key: pytest.approx(value, rel=0, abs=10 ** (-decimals[key] - 2))
            for key, value in member["values"].items()
        }
    assert _evaluate() == expected


def test_threshold_metrics_match_the_exported_fixture():
    assert thresholds.metrics({}) == _fixture("bridge/soundings/threshold-metrics.result.json")


def test_threshold_csv_matches_the_exported_fixture():
    request = _request()
    with patch("sharpmod.tools.model_extract.extract", EXPORT.stand_in_extract(EXPORT.EXAMPLE)):
        actual = thresholds.export_csv(request)
    expected = _fixture("bridge/soundings/threshold-csv.result.example.json")
    assert actual["file_name"] == expected["file_name"]
    decimals = {row["key"]: row["decimals"] for row in _fixture(
        "bridge/soundings/threshold-metrics.result.json")["metrics"]}
    for row, reference in zip(csv.DictReader(io.StringIO(actual["csv"])),
                              csv.DictReader(io.StringIO(expected["csv"])), strict=True):
        values = json.loads(row.pop("diagnostic_values"))
        reference_values = json.loads(reference.pop("diagnostic_values"))
        assert values == {
            key: pytest.approx(value, rel=0, abs=10 ** (-decimals[key] - 2))
            for key, value in reference_values.items()
        }
        assert row == reference


def test_threshold_classifies_members_with_explicit_denominators():
    result = _evaluate()
    # The definition and the sentence naming the rule it actually tests.
    assert result["definition_summary"] == (
        "MLCAPE ≥ 2350 J/kg and 0-6 km shear between 20 and 60 kt"
    )
    assert [c["units"] for c in result["definition"]["conditions"]] == ["J/kg", "kt"]
    # The counts partition the requested ensemble and name their denominators.
    assert result["requested_member_count"] == 4
    assert result["loaded_member_count"] == 3
    assert result["usable_member_count"] == 3
    assert result["qualifying_member_count"] == 1
    assert result["nonqualifying_member_count"] == 2
    assert result["loaded_unusable_member_count"] == 0
    assert result["not_loaded_member_count"] == 1
    assert result["resolved_member_count"] == 4
    assert result["not_evaluated_member_count"] == 0
    assert result["reconciles"] is True and result["cancelled"] is False
    # Every ratio shows its denominator rather than a bare percentage.
    assert result["agreement"] == "1/3 (33%)"
    assert result["agreement_fraction"] == pytest.approx(1 / 3)
    assert result["coverage"] == "3/4 (75%)"
    assert result["requested_coverage"] == 0.75
    # The provider's own interpretation sentence crosses intact.
    assert result["label"] == (
        "Ensemble ingredient agreement; not a calibrated severe-weather probability."
    )


def test_threshold_member_rows_name_their_state_and_values():
    result = _evaluate()
    rows = {row["member"]: row for row in result["members"]}
    assert list(rows) == ["c00", "p01", "p02", "p03"]
    winner = rows["c00"]
    assert winner["state"] == "qualifying"
    assert winner["state_label"] == "qualifies"
    assert winner["satisfied"] is True and winner["usable"] is True
    assert set(winner["values"]) == {"mlcape", "shear_6km"}
    assert winner["values"]["mlcape"] > 2350
    assert winner["values"]["shear_6km"] is not None
    loser = rows["p02"]
    assert loser["state"] == "nonqualifying"
    assert loser["state_label"] == "does not qualify"
    assert loser["satisfied"] is False
    missing = rows["p03"]
    assert missing["state"] == "not_loaded"
    assert missing["state_label"] == "not loaded"
    assert missing["satisfied"] is None and missing["usable"] is False
    assert missing["reason"] == "member was not loaded"
    assert missing["values"] == {"mlcape": None, "shear_6km": None}


def test_threshold_catalog_carries_labels_units_and_control_bounds():
    catalog = thresholds.metrics({})
    assert [op for op in catalog["operators"]] == [">", ">=", "<", "<=", "==", "between"]
    assert catalog["default_metrics"] == ["mlcape", "shear_6km", "srh_3km"]
    keys = [row["key"] for row in catalog["metrics"]]
    assert "mlcape" in keys and "shear_6km" in keys and len(keys) == 59
    row = next(item for item in catalog["metrics"] if item["key"] == "shear_6km")
    assert row["label"] == "0-6 km shear" and row["units"] == "kt"
    assert row["decimals"] == 0 and row["step"] == 5.0
    assert row["minimum"] < row["suggested"] < row["maximum"]
    assert row["display_label"] == "0-6 km shear (kt)"
    assert "stepping by" in row["guidance"]


def test_threshold_csv_keeps_the_engine_rows_unchanged():
    request = _request()
    with patch("sharpmod.tools.model_extract.extract", EXPORT.stand_in_extract(EXPORT.EXAMPLE)):
        exported = thresholds.export_csv(request)
    assert exported["file_name"] == "ensemble-threshold-results.csv"
    lines = exported["csv"].splitlines()
    assert lines[0].startswith("definition,definition_summary,valid_time,member,")
    assert len(lines) == 5  # one header row plus one row per requested member
    body = "\n".join(lines)
    assert "Warm-core shear" in body
    assert "1/3 (33%)" in body and "3/4 (75%)" in body
    assert "Ensemble ingredient agreement; not a calibrated severe-weather probability." in body


def test_threshold_refuses_bad_definitions():
    for bad, needle in [
        ({"name": "x", "conditions": []}, "1 to 3 conditions"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": "~", "value": 1.0}]},
         "unsupported threshold operator"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": "between", "value": 5.0}]},
         "between requires an upper value"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": ">", "value": 5.0, "upper": 6.0}]},
         "upper is only valid with the between operator"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": ">", "value": 5.0},
                                      {"metric": "mlcape", "operator": "<", "value": 9.0}]},
         "a metric may appear only once"),
        ({"name": "x", "conditions": [{"metric": "not-a-diagnostic", "operator": ">", "value": 5.0}]},
         "not one of the registry diagnostics"),
        ({"name": " ", "conditions": [{"metric": "mlcape", "operator": ">", "value": 5.0}]},
         "name must be text"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": ">", "value": float("inf")}]},
         "value must be"),
        ({"name": "x", "conditions": [{"metric": "mlcape", "operator": ">", "value": 5.0},
                                      {"metric": "mlcin", "operator": "<", "value": 9.0},
                                      {"metric": "srh_1km", "operator": ">", "value": 1.0},
                                      {"metric": "stp_cin", "operator": ">", "value": 1.0}]},
         "1 to 3 conditions"),
        ("not an object", "definition must be an object"),
    ]:
        with pytest.raises(BridgeError) as info:
            _evaluate(definition=bad)
        assert info.value.code == "INVALID_ARGUMENT", bad
        assert needle in info.value.message, (bad, info.value.message)


def test_threshold_refuses_bad_requests():
    with pytest.raises(BridgeError) as info:
        _evaluate(model="hrrr")
    assert info.value.code == "INVALID_ARGUMENT" and "deterministic" in info.value.message
    with pytest.raises(BridgeError) as info:
        _evaluate(members=["c00"])
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        thresholds.evaluate({**_request(), "extra": 1})
    assert info.value.code == "INVALID_ARGUMENT"
    with pytest.raises(BridgeError) as info:
        thresholds.metrics({"extra": 1})
    assert info.value.code == "INVALID_ARGUMENT"


def test_the_threshold_handlers_load_no_qt_module():
    code = (
        "import sys; import sharpmod.bridge.thresholds; "
        "import sharpmod.bridge.handlers; "
        "import sharpmod.analysis.metric_controls; "
        "print(sorted(m for m in sys.modules if m.startswith(('PySide', 'PyQt'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
