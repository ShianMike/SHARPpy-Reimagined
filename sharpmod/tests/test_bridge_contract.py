"""Bridge payloads against the shared contract fixtures in ``src-tauri/contracts``.

Real handler output is checked against explicit field schemas and the fixture
key sets, never against fixture values, so the module passes with and without
the Rust wheel. The golden catalogue files are checked byte for byte against
the exporter's output.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

from sharpmod.bridge import handlers
from sharpmod.bridge.errors import ERROR_CODES, BridgeError
from sharpmod.io import uwyo_catalog

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "src-tauri" / "contracts"
EXPORTER = ROOT / "scripts" / "export_frontend_fixtures.py"

HEALTH_OK = {
    "status": {"ok"},
    "requested_backend": {"auto", "python", "rust"},
    "active_backend": {"python", "rust"},
    "rust_installed": (bool,),
    "rust_version": (str, type(None)),
    "fallback_reason": (str, type(None)),
}
HEALTH_UNAVAILABLE = {
    "status": {"unavailable"},
    "code": {"BACKEND_UNAVAILABLE", "BACKEND_CONFIG_INVALID"},
    "message": (str,),
}
HELLO = {"protocol_version": (int,), "sharpmod_version": (str,),
         "python_version": (str,), "python_executable": (str,), "pid": (int,)}


def _fixture(relative: str):
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


def _assert_schema(obj: dict, schema: dict) -> None:
    assert set(obj) == set(schema)
    for key, allowed in schema.items():
        value = obj[key]
        if isinstance(allowed, set):
            assert type(value) is str and value in allowed, (key, value)
        else:
            assert type(value) in allowed, (key, value)


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_frontend_fixtures", EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hello_matches_its_schema_and_fixture_keys():
    result = handlers.hello({"protocol_version": 1})
    _assert_schema(result, HELLO)
    assert result["protocol_version"] == 1
    assert set(result) == set(_fixture("bridge/hello.result.json"))


def test_health_matches_one_schema_and_its_fixture_keys():
    result = handlers.health({})
    assert set(result) == {"scientific_backend"}
    backend = result["scientific_backend"]
    if backend["status"] == "ok":
        _assert_schema(backend, HEALTH_OK)
        for case in ("ok", "python-fallback"):
            fixture = _fixture(f"bridge/health.result.{case}.json")
            assert set(fixture["scientific_backend"]) == set(backend)
    else:
        _assert_schema(backend, HEALTH_UNAVAILABLE)
        for case in ("backend-unavailable", "config-invalid"):
            fixture = _fixture(f"bridge/health.result.{case}.json")
            assert set(fixture["scientific_backend"]) == set(backend)


def test_invalid_backend_setting_reports_config_invalid(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "gpu")
    backend = handlers.health({})["scientific_backend"]
    _assert_schema(backend, HEALTH_UNAVAILABLE)
    assert backend["code"] == "BACKEND_CONFIG_INVALID"
    assert "'gpu'" in backend["message"]
    fixture = _fixture("bridge/health.result.config-invalid.json")
    assert set(fixture["scientific_backend"]) == set(backend)


def test_catalog_sample_records_are_real_records():
    result = handlers.stations_catalog({})
    sample = _fixture("bridge/stations.catalog.result.sample.json")
    assert set(result) == set(sample)
    assert result["origin"] == "bundled" and result["dropped_count"] == 0
    by_id = {station["id"]: station for station in result["stations"]}
    assert len(by_id) == len(result["stations"]) == 933
    for station in sample["stations"]:
        assert by_id[station["id"]] == station
    assert {s["src"] for s in sample["stations"]} == {"FM35", "BUFR", "FM37"}
    assert any(s["name"] == "" for s in sample["stations"])


def test_golden_catalogue_equals_all_stations():
    golden = _fixture("public/stations.catalog.full.json")
    expected = [
        {"id": r["id"], "name": r["name"], "latitude": r["lat"],
         "longitude": r["lon"], "source": r["src"], "availability": "unknown"}
        for r in uwyo_catalog.all_stations()
    ]
    assert golden["stations"] == expected
    assert {k: v for k, v in golden.items() if k != "stations"} == {
        "origin": "bundled", "availabilityBasis": {"kind": "catalogOnly"},
        "droppedCount": 0}


def test_search_cases_equal_search_stations():
    cases = _fixture("public/stations.search-cases.json")["cases"]
    assert len(cases) >= 10
    for case in cases:
        assert len(case["query"]) <= 64 and case["limit"] == 8
        found = uwyo_catalog.search_stations(case["query"], case["limit"])
        assert case["ids"] == [r["id"] for r in found], case["query"]


def test_catalogue_text_is_ascii():
    for record in uwyo_catalog.all_stations():
        assert record["id"].isascii() and record["name"].isascii(), record["id"]


def test_exporter_renders_the_committed_golden_files():
    exporter = _load_exporter()
    rendered = exporter.render_all()
    assert sorted(rendered) == sorted(exporter.GOLDEN_FILES)
    for relative, text in rendered.items():
        assert text.isascii(), relative
        assert (CONTRACTS / relative).read_bytes() == text.encode("ascii"), (
            f"{relative} is stale; run scripts/export_frontend_fixtures.py")


def test_seed_fallback_is_detected(monkeypatch):
    monkeypatch.setattr(uwyo_catalog, "load_catalog", uwyo_catalog._seed_catalog)
    result = handlers.stations_catalog({})
    assert result == _fixture("bridge/stations.catalog.result.seed-fallback.json")
    assert result["origin"] == "seed_fallback"


def test_bundled_catalogue_is_not_mistaken_for_the_seed():
    records = uwyo_catalog.all_stations()
    assert not handlers.is_seed_catalog(records, uwyo_catalog.SEED_STATIONS)
    seed = uwyo_catalog._seed_catalog()
    renamed = [dict(r, name=r["name"] + "!") for r in seed]
    assert handlers.is_seed_catalog(seed, uwyo_catalog.SEED_STATIONS)
    assert not handlers.is_seed_catalog(renamed, uwyo_catalog.SEED_STATIONS)


def test_unexpected_catalogue_failure_is_catalog_unavailable(monkeypatch):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(uwyo_catalog, "all_stations", broken)
    with pytest.raises(BridgeError) as info:
        handlers.stations_catalog({})
    assert info.value.code == "CATALOG_UNAVAILABLE"
    assert info.value.retryable is True
    assert "disk gone" not in info.value.message


def test_error_codes_equal_the_bridge_error_fixtures():
    files = sorted(CONTRACTS.glob("bridge/error.*.json"))
    codes = {json.loads(p.read_text(encoding="utf-8"))["code"] for p in files}
    assert codes == {p.stem.removeprefix("error.") for p in files}
    assert ERROR_CODES == codes
    for code in codes:
        BridgeError(code, "ok", False)
    for code in ("BACKEND_UNAVAILABLE", "BRIDGE_TIMEOUT", "UNKNOWN"):
        with pytest.raises(ValueError):
            BridgeError(code, "no", False)


GOOD = {"id": "72357", "name": "Norman", "lat": 35.18, "lon": -97.44, "src": "FM35"}


@pytest.mark.parametrize("bad", [
    dict(GOOD, id=""),
    dict(GOOD, id="   "),
    dict(GOOD, id="x" * 17),
    dict(GOOD, id=72357),
    dict(GOOD, lat=math.nan),
    dict(GOOD, lat=91.0),
    dict(GOOD, lon=-180.5),
    dict(GOOD, lon=math.inf),
    dict(GOOD, lat=True),
    dict(GOOD, lat="35.18"),
    dict(GOOD, name="n" * 201),
    dict(GOOD, name=None),
    dict(GOOD, src=""),
    {k: v for k, v in GOOD.items() if k != "src"},
])
def test_record_rule_drops_and_counts_bad_records(bad, caplog):
    kept, dropped = handlers.filter_records([bad, dict(GOOD, id="72358")])
    assert dropped == 1
    assert [r["id"] for r in kept] == ["72358"]
    assert "dropped record" in caplog.text


def test_record_rule_keeps_the_first_of_a_repeated_id():
    kept, dropped = handlers.filter_records([GOOD, dict(GOOD, name="Second")])
    assert dropped == 1 and kept == [GOOD]
    kept, dropped = handlers.filter_records(
        [dict(GOOD, id=" 72357 ", lat=35, name="n" * 200)])
    assert dropped == 0
    assert kept[0]["id"] == "72357" and type(kept[0]["lat"]) is float
