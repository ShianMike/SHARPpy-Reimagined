"""The bridge's sounding operations against real sharpmod, with offline stand-ins.

The example files under ``examples/soundings`` are decoded and analysed by the
real handlers. The network providers (observed archives, model downloads) and
the Qt render worker are replaced by stand-ins, so the module runs offline
and fast; the real worker is exercised by the desktop verification instead.
Opened soundings are module-scoped and read-only.
"""

from __future__ import annotations

import base64
import json
import math
import shutil
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sharpmod.bridge import render_client, sounding_cache, soundings
from sharpmod.bridge.errors import BridgeError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "soundings"
NPZ = EXAMPLES / "hrrr_point_36.68N_95.66W_f018.npz"
BUFKIT = EXAMPLES / "hrrr_kbvo_20260625_06z.buf"
CONTRACTS = ROOT / "src-tauri" / "contracts"
#: Enough of a PNG for the size check: signature, IHDR, 64 by 32.
FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + (13).to_bytes(4, "big")
    + b"IHDR"
    + (64).to_bytes(4, "big")
    + (32).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
    + b"\x00" * 4
)


def _upload(path: Path, name: str | None = None) -> dict[str, str]:
    return {
        "file_name": name or path.name,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def _fixture(relative: str):
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def bridge_cache(tmp_path_factory):
    cache = sounding_cache.reset_cache(tmp_path_factory.mktemp("bridge") / "soundings")
    yield cache
    sounding_cache.reset_cache(None)


@pytest.fixture(scope="module")
def opened(bridge_cache):
    return soundings.open_file(_upload(NPZ))


@pytest.fixture(scope="module")
def analysis(opened):
    return soundings.analysis({"sounding_id": opened["sounding_id"]})


def _table_value(table: dict, row: str, column: str) -> float | None:
    index = [c["key"] for c in table["columns"]].index(column)
    return next(r for r in table["rows"] if r["key"] == row)["values"][index]


def test_open_file_summary_matches_the_contract(opened):
    expected = _fixture("bridge/soundings/open.result.example.json")
    assert set(opened) == set(expected)
    assert opened["kind"] == "file" and opened["file_name"] == NPZ.name
    assert (
        opened["model"] == "HRRR"
        and opened["level_count"] == 39
        and opened["time_count"] == 1
    )
    assert opened["valid_time"] == "2026-06-26T00:00:00Z"
    assert math.isclose(opened["latitude"], 36.675, abs_tol=0.01)


@pytest.mark.parametrize("format", ["npz", "spc", "bufkit", "pecan", "uwyo", "uwyo-wsgi", "wrf-npz"])
def test_file_upload_preserves_supported_formats(tmp_path, format):
    import numpy as np

    from sharpmod.tests.test_decoders import (
        _assert_populated,
        _raw_profile,
        _write_pecan_sample,
        _write_wrf_arw_sample,
    )
    from sharpmod.tests.uwyo_fixtures import render_uwyo_text
    from sharpmod.tools import wrf_extract

    if format == "pecan":
        path = _write_pecan_sample(tmp_path / "sample.pecan")
    elif format.startswith("uwyo"):
        with np.load(NPZ, allow_pickle=False) as source:
            text = render_uwyo_text(*(source[key] for key in (
                "pres", "hght", "tmpc", "dwpc", "wdir", "wspd",
            )))
        if format == "uwyo-wsgi":
            text = text.replace("</PRE><H3>", "</PRE>\n<H3>").replace("\n", "\r\n")
        path = tmp_path / "sounding.html"
        path.write_text(text, encoding="utf-8")
    elif format == "wrf-npz":
        raw = _write_wrf_arw_sample(tmp_path / "wrfout_d01.nc")
        path = Path(wrf_extract.extract(str(raw), 41, -96, str(tmp_path / "wrf.npz")))
    else:
        path = {"npz": NPZ, "spc": EXAMPLES / "14061619.OAX", "bufkit": BUFKIT}[format]
    opened = soundings.open_file(_upload(path))
    assert opened["kind"] == "file" and opened["file_name"] == path.name
    assert opened["level_count"] >= 12 and opened["time_count"] >= 1
    assert opened["valid_time"] is not None
    collection = sounding_cache.cache().get(opened["sounding_id"]).collection
    _assert_populated(_raw_profile(collection))


def test_raw_wrf_inspection_and_import_preserve_the_source(tmp_path):
    import numpy as np
    from netCDF4 import Dataset
    from sharpmod.tests.test_decoders import (
        _write_wrf_arw_sample, known_netcdf4_numpy_shape_deprecation,
    )

    path = _write_wrf_arw_sample(tmp_path / "wrfout_d01.nc", nt=2)
    with known_netcdf4_numpy_shape_deprecation(), Dataset(str(path), "r+") as dataset:
        dataset.variables["Times"][1] = np.array(list("2014-06-16_20:00:00"), dtype="S1")
    source = path.read_bytes()
    info = soundings.inspect_wrf({"file_path": str(path)})
    assert info["times"] == ["2014-06-16T19:00:00Z", "2014-06-16T20:00:00Z"]
    assert info["latitude"] == 41 and info["longitude"] == -96
    opened = soundings.open_wrf({
        "file_path": info["file_path"], "time": info["times"][1],
        "latitude": 41, "longitude": -96,
    })
    assert opened["kind"] == "file" and opened["model"] == "WRF-ARW"
    assert opened["valid_time"] == info["times"][1]
    assert opened["level_count"] == 12
    assert path.read_bytes() == source


@pytest.mark.parametrize("change", [
    {"latitude": 0}, {"time": "2014-06-16T21:00:00Z"}, {"longitude": float("nan")},
])
def test_raw_wrf_rejects_invalid_points_and_times_without_cache_files(tmp_path, change):
    from sharpmod.tests.test_decoders import _write_wrf_arw_sample

    path = _write_wrf_arw_sample(tmp_path / "wrfout_d01.nc")
    before = set(sounding_cache.cache().root.iterdir())
    with pytest.raises(BridgeError) as error:
        soundings.open_wrf({
            "file_path": str(path), "time": "2014-06-16T19:00:00Z",
            "latitude": 41, "longitude": -96, **change,
        })
    assert error.value.code == "INVALID_ARGUMENT"
    assert set(sounding_cache.cache().root.iterdir()) == before


@pytest.mark.parametrize("path", ["wrfout_d01.nc", "https://example.com/wrfout.nc"])
def test_raw_wrf_requires_an_existing_local_path(path):
    with pytest.raises(BridgeError) as error:
        soundings.inspect_wrf({"file_path": path})
    assert error.value.code == "INVALID_ARGUMENT"


def test_analysis_carries_the_audited_values_of_the_example(analysis):
    expected = _fixture("bridge/soundings/analysis.result.example.json")
    assert set(analysis) == set(expected)
    # AUDIT.md §5.6: MLCAPE 2465.3, SBCAPE 2989.4, STP (CIN) 5.16, fixed STP
    # 3.64, and right-mover SCP 21.22.
    assert math.isclose(
        _table_value(analysis["parcel_table"], "ml", "cape"), 2465.3, abs_tol=0.5
    )
    assert math.isclose(
        _table_value(analysis["parcel_table"], "sb", "cape"), 2989.4, abs_tol=0.5
    )
    values = {p["key"]: p["value"] for g in analysis["groups"] for p in g["parameters"]}
    assert math.isclose(values["stp_cin"], 5.16, abs_tol=0.01)
    assert math.isclose(values["stp_fixed"], 3.64, abs_tol=0.01)
    assert math.isclose(values["scp"], 21.22, abs_tol=0.01)
    assert analysis["watch_type"] == "PDS TOR"
    assert analysis["hazard"] == "tornado"
    motion = analysis["motion_table"]
    assert [r["key"] for r in motion["rows"]] == ["bunkers_right", "bunkers_left"]
    assert math.isclose(
        _table_value(motion, "bunkers_right", "direction"), 274.9, abs_tol=0.5
    )


def test_analysis_lists_the_whole_registry_in_order(analysis):
    from sharpmod.analysis import box_analysis

    keys = [p["key"] for g in analysis["groups"] for p in g["parameters"]]
    assert keys == [parameter.key for parameter in box_analysis.PARAMETERS]
    for group in analysis["groups"]:
        for parameter in group["parameters"]:
            value = parameter["value"]
            assert value is None or math.isfinite(value)
    json.dumps(analysis, allow_nan=False)


def test_analysis_is_cached_per_sounding(opened, analysis):
    assert soundings.analysis({"sounding_id": opened["sounding_id"]}) is analysis


def test_trends_cover_every_time_of_a_forecast_file():
    from sharpmod.analysis import profile_metrics

    summary = soundings.open_file(_upload(BUFKIT))
    assert summary["time_count"] == 49
    trends = soundings.trends({"sounding_id": summary["sounding_id"]})
    assert [c["key"] for c in trends["columns"]] == list(
        profile_metrics.DEFAULT_METRIC_KEYS
    )
    assert len(trends["points"]) == 49 and trends["truncated"] is False
    times = [point["time"] for point in trends["points"]]
    assert times[0] == "2026-06-25T06:00:00Z" and times == sorted(times)
    assert all(
        len(point["values"]) == len(trends["columns"]) for point in trends["points"]
    )
    json.dumps(trends, allow_nan=False)


@pytest.mark.parametrize(
    "params, code",
    [
        ({"file_name": "a.txt"}, "INVALID_ARGUMENT"),
        ({"file_name": "a.txt", "content_base64": "@@@"}, "INVALID_ARGUMENT"),
        ({"file_name": "a.txt", "content_base64": "", "extra": 1}, "INVALID_ARGUMENT"),
        ({"file_name": "empty.txt", "content_base64": ""}, "SOUNDING_INVALID"),
        (
            {
                "file_name": "notes.txt",
                "content_base64": base64.b64encode(b"not a sounding\n").decode(),
            },
            "SOUNDING_INVALID",
        ),
    ],
)
def test_open_file_rejects_bad_input(params, code):
    before = set(sounding_cache.cache().root.iterdir())
    with pytest.raises(BridgeError) as info:
        soundings.open_file(params)
    assert info.value.code == code and info.value.retryable is False
    assert set(sounding_cache.cache().root.iterdir()) == before


def test_open_file_checks_decoded_size_before_writing(monkeypatch):
    from sharpmod.bridge import sounding_open

    monkeypatch.setattr(sounding_open, "MAX_UPLOAD_BYTES", 3)
    before = set(sounding_cache.cache().root.iterdir())
    with pytest.raises(BridgeError) as info:
        soundings.open_file({"file_name": "large.npz", "content_base64": "YWJjZA=="})
    assert info.value.code == "INVALID_ARGUMENT"
    assert set(sounding_cache.cache().root.iterdir()) == before


def test_unknown_or_malformed_ids_are_structured_errors():
    with pytest.raises(BridgeError) as info:
        soundings.analysis({"sounding_id": "0000000000000000"})
    assert info.value.code == "SOUNDING_NOT_FOUND"
    with pytest.raises(BridgeError) as info:
        soundings.render({"sounding_id": 7})
    assert info.value.code == "INVALID_ARGUMENT"


def test_models_catalog_lists_every_selectable_model():
    from sharpmod.tools import model_extract

    catalog = soundings.models_catalog({})
    models = {m["key"]: m for m in catalog["models"]}
    assert set(models) == {c.key for c in model_extract.available_models()}
    hrrr = models["hrrr"]
    assert len(hrrr["outline"]) > 100 and hrrr["cycles"] == list(range(24))
    assert hrrr["max_forecast_hours"][5] == 18
    assert hrrr["max_forecast_hours"][6] == 48
    ecmwf = models["openmeteo-ecmwf-ifs025"]
    assert [ecmwf["max_forecast_hours"][h] for h in (0, 6, 12, 18)] == [360, 144, 360, 144]
    for model in catalog["models"]:
        assert model["suggested_run"] in model["recent_runs"]
        assert model["recent_runs"] == sorted(model["recent_runs"], reverse=True)
        assert model["forecast_hours"]
        assert len(model["max_forecast_hours"]) == 24
        assert set(model["max_forecast_hours"]) <= set(model["forecast_hours"])


def test_open_observed_writes_and_decodes_the_provider_result(monkeypatch):
    from sharpmod.providers import observations as obs

    collection, _ = sounding_cache.decode_collection(NPZ)
    profile = collection.getHighlightedProf()
    when = datetime(2026, 6, 26, 0, tzinfo=UTC)

    def fetch(station, when_utc, *, providers):
        assert station == "72357" and providers == obs.DEFAULT_PROVIDER_ORDER
        return obs.ObservedSounding(
            profile=profile,
            provider="uwyo",
            provider_name="University of Wyoming Upper Air Archive",
            station_id="72357",
            requested_station=station,
            valid=when,
            source_url="https://example.invalid",
            metadata={
                "station_name": "Norman",
                "lat": 35.18,
                "lon": -97.44,
                "valid": when,
            },
        )

    monkeypatch.setattr(obs, "fetch_observed", fetch)
    summary = soundings.open_observed(
        {"station_id": " 72357 ", "time": "2026-06-26T00:00:00Z"}
    )
    assert summary["kind"] == "observed" and summary["title"] == "72357 Norman"
    assert (
        summary["valid_time"] == "2026-06-26T00:00:00Z"
        and summary["provider"] == "uwyo"
    )
    assert summary["level_count"] == 39


@pytest.mark.parametrize(
    "error_type, code, retryable",
    [
        ("ObservedRetrievalError", "SOUNDING_FETCH_FAILED", True),
        ("ObservedUnavailableError", "SOUNDING_UNAVAILABLE", False),
    ],
)
def test_observed_failures_map_to_structured_errors(
    monkeypatch, error_type, code, retryable
):
    from sharpmod.providers import observations as obs

    def fail(*_args, **_kwargs):
        raise obs.ObservedFallbackError(
            [{"provider": "uwyo", "error_type": error_type, "error": "x"}]
        )

    monkeypatch.setattr(obs, "fetch_observed", fail)
    with pytest.raises(BridgeError) as info:
        soundings.open_observed({"station_id": "72357", "time": "2026-06-26T00:00:00Z"})
    assert (info.value.code, info.value.retryable) == (code, retryable)


def test_open_era5_uses_the_existing_extractor_and_records_reanalysis(monkeypatch):
    from sharpmod.tests.era5_synth import make_era5_dataset
    from sharpmod.tools import era5_extract

    when = datetime(2018, 9, 1, 5, tzinfo=UTC)
    dataset = make_era5_dataset(
        [35.0, 35.25], [-97.5, -97.25],
        [1000, 950, 900, 850, 700, 500, 300, 200, 100], [when],
    )
    calls = []

    def retrieve(lat, lon, time):
        calls.append((lat, lon, time))
        return dataset

    monkeypatch.setattr(era5_extract, "_retrieve_dataset", retrieve)
    summary = soundings.open_era5({
        "time": "2018-09-01T05:00:00Z", "latitude": 35.22, "longitude": -97.44,
    })
    assert calls == [(35.22, -97.44, when)]
    assert summary["kind"] == "era5" and summary["model"] == "ERA5"
    assert summary["valid_time"] == "2018-09-01T05:00:00Z"
    assert summary["run_time"] is None and summary["forecast_hour"] is None
    assert (summary["latitude"], summary["longitude"]) == (35.25, -97.5)
    assert summary["provider"] == "cds" and summary["level_count"] >= 8
    assert sounding_cache.cache().get(summary["sounding_id"]).path.is_file()


@pytest.mark.parametrize("patch", [
    {"time": "2018-09-01T05:30:00Z"},
    {"time": "1939-12-31T00:00:00Z"},
    {"time": "2099-01-01T00:00:00Z"},
    {"latitude": 91}, {"longitude": float("nan")}, {"latitude": True},
    {"forecast_hour": 0},
])
def test_open_era5_rejects_invalid_inputs_before_retrieval(monkeypatch, patch):
    from sharpmod.tools import era5_extract

    def retrieve(*_args, **_kwargs):
        pytest.fail("invalid ERA5 requests must not reach CDS")

    monkeypatch.setattr(era5_extract, "_retrieve_dataset", retrieve)
    with pytest.raises(BridgeError) as info:
        soundings.open_era5({
            "time": "2018-09-01T05:00:00Z", "latitude": 35.22, "longitude": -97.44, **patch,
        })
    assert info.value.code == "INVALID_ARGUMENT" and not info.value.retryable


def test_open_era5_keeps_the_actionable_provider_error(monkeypatch):
    from sharpmod.tools import era5_extract

    def retrieve(*_args, **_kwargs):
        raise era5_extract.RetrievalError("ERA5 retrieval requires CDS API credentials.\nprivate traceback")

    monkeypatch.setattr(era5_extract, "_retrieve_dataset", retrieve)
    with pytest.raises(BridgeError) as info:
        soundings.open_era5({
            "time": "2018-09-01T05:00:00Z", "latitude": 35.22, "longitude": -97.44,
        })
    assert info.value.code == "SOUNDING_FETCH_FAILED" and info.value.retryable
    assert "requires CDS API credentials" in info.value.message
    assert "private traceback" not in info.value.message


def test_open_model_extracts_to_the_cache_and_maps_errors(monkeypatch):
    from sharpmod.tools import era5_extract
    from sharpmod.tools import model_extract as mx

    calls = []

    def extract(model, lat, lon, *, run_time, fxx, out_path, loc):
        calls.append((model, lat, lon, run_time, fxx, loc))
        shutil.copyfile(NPZ, out_path)
        return out_path

    monkeypatch.setattr(mx, "extract", extract)
    summary = soundings.open_model(
        {
            "model": "hrrr",
            "forecast_hour": 18,
            "latitude": 36.68,
            "longitude": -95.66,
            "run_time": "2026-06-25T06:00:00Z",
        }
    )
    assert calls[0][:3] == ("hrrr", 36.68, -95.66) and calls[0][4] == 18
    assert (
        summary["kind"] == "model"
        and summary["model"] == "HRRR"
        and summary["forecast_hour"] == 18
    )
    assert summary["title"] == "HRRR 36.68N 95.66W"

    def out_of_domain(*_args, **_kwargs):
        raise era5_extract.ParameterRangeError("outside the domain")

    monkeypatch.setattr(mx, "extract", out_of_domain)
    with pytest.raises(BridgeError) as info:
        soundings.open_model(
            {"model": "hrrr", "forecast_hour": 0, "latitude": 0.0, "longitude": 0.0}
        )
    assert info.value.code == "INVALID_ARGUMENT"

    def unreachable(*_args, **_kwargs):
        raise era5_extract.RetrievalError("the archive did not answer\ntraceback lines")

    monkeypatch.setattr(mx, "extract", unreachable)
    with pytest.raises(BridgeError) as info:
        soundings.open_model(
            {"model": "hrrr", "forecast_hour": 0, "latitude": 35.0, "longitude": -97.0}
        )
    assert info.value.code == "SOUNDING_FETCH_FAILED" and info.value.retryable is True
    assert "traceback" not in info.value.message


def test_render_returns_the_png_and_reuses_it(monkeypatch, opened):
    drawn = []

    class StandIn:
        def render(self, infile, outfile, parcel, image_mode):
            drawn.append((Path(infile).name, parcel, image_mode))
            Path(outfile).write_bytes(FAKE_PNG)

    monkeypatch.setattr(render_client, "client", lambda: StandIn())
    first = soundings.render({"sounding_id": opened["sounding_id"], "parcel": "ML"})
    again = soundings.render({"sounding_id": opened["sounding_id"], "parcel": "ML"})
    assert drawn == [(f"{opened['sounding_id']}.npz", "ML", "hd")]
    assert (first["width"], first["height"], first["byte_length"]) == (
        64,
        32,
        len(FAKE_PNG),
    )
    assert base64.b64decode(again["png_base64"]) == FAKE_PNG
    with pytest.raises(BridgeError) as info:
        soundings.render({"sounding_id": opened["sounding_id"], "parcel": "USER"})
    assert info.value.code == "INVALID_ARGUMENT"


def test_the_largest_image_reply_fits_one_frame():
    from sharpmod.bridge.protocol import MAX_FRAME_BYTES, MAX_REQUEST_ID, encode_frame

    size = soundings.MAX_IMAGE_BYTES
    reply = {
        "id": MAX_REQUEST_ID,
        "result": {
            "sounding_id": "f" * 64,
            "parcel": "FCST",
            "image_mode": "uhd",
            "width": 99_999,
            "height": 99_999,
            "byte_length": size,
            "png_base64": base64.b64encode(bytes(size)).decode("ascii"),
        },
    }
    # encode_frame raises for a frame over the cap.
    assert len(encode_frame(reply)) <= MAX_FRAME_BYTES + 1


WORKER = textwrap.dedent("""
    import json, sys, time
    for line in sys.stdin:
        request = json.loads(line)
        mode = request["infile"]
        if mode == "sleep":
            time.sleep(30)
        if mode == "exit":
            sys.exit(3)
        if mode == "fail":
            reply = {"id": request["id"], "ok": False, "message": "no profile"}
            print(json.dumps(reply), flush=True)
            continue
        open(request["outfile"], "wb").write(b"png")
        print(json.dumps({"id": request["id"], "ok": True}), flush=True)
""")


@pytest.fixture
def stand_in_worker(monkeypatch):
    monkeypatch.setattr(render_client, "WORKER_ARGS", ("-c", WORKER))
    monkeypatch.setattr(render_client, "STARTUP_ALLOWANCE_S", 0.0)
    monkeypatch.setattr(render_client, "RENDER_TIMEOUT_S", 5.0)
    client = render_client.RenderClient()
    yield client
    client.close()


def test_render_client_speaks_the_worker_protocol(stand_in_worker, tmp_path):
    out = tmp_path / "a.png"
    stand_in_worker.render(Path("ok"), out, "MU", "hd")
    assert out.read_bytes() == b"png"
    with pytest.raises(BridgeError) as info:
        stand_in_worker.render(Path("fail"), out, "MU", "hd")
    assert info.value.code == "RENDER_FAILED" and info.value.retryable is False
    assert "no profile" in info.value.message


def test_render_client_recovers_from_a_crashed_or_hung_worker(
    stand_in_worker, tmp_path, monkeypatch
):
    with pytest.raises(BridgeError) as info:
        stand_in_worker.render(Path("exit"), tmp_path / "a.png", "MU", "hd")
    assert info.value.code == "RENDER_FAILED" and info.value.retryable is True
    monkeypatch.setattr(render_client, "RENDER_TIMEOUT_S", 0.5)
    with pytest.raises(BridgeError) as info:
        stand_in_worker.render(Path("sleep"), tmp_path / "b.png", "MU", "hd")
    assert info.value.retryable is True and "too long" in info.value.message
    monkeypatch.setattr(render_client, "RENDER_TIMEOUT_S", 5.0)
    stand_in_worker.render(Path("ok"), tmp_path / "c.png", "MU", "hd")
    assert (tmp_path / "c.png").read_bytes() == b"png"


def test_sounding_handlers_load_no_qt_module(tmp_path):
    import subprocess

    code = textwrap.dedent(f"""
        import base64, sys
        from pathlib import Path
        from sharpmod.bridge import sounding_cache, soundings
        sounding_cache.reset_cache(Path(sys.argv[1]))
        data = base64.b64encode(Path({str(NPZ)!r}).read_bytes()).decode()
        upload = {{"file_name": "x.npz", "content_base64": data}}
        opened = soundings.open_file(upload)
        soundings.analysis({{"sounding_id": opened["sounding_id"]}})
        soundings.models_catalog({{}})
        roots = ("PySide6", "shiboken6", "qtpy")
        qt = sorted(m for m in sys.modules if m.split(".")[0] in roots)
        print("QT", qt)
    """)
    with subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "cache")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
    ) as proc:
        out, err = proc.communicate(timeout=180)
    assert proc.returncode == 0, err.decode("utf-8", "replace")[-2000:]
    assert b"QT []" in out
