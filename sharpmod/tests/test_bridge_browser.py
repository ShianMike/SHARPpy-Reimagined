"""Browser HTTP boundary and byte-for-byte compatibility with desktop DTOs."""

import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sharpmod.bridge.browser import BrowserServer
from sharpmod.bridge.handlers import HANDLERS
from sharpmod.bridge.public import bridge_request, public_result

CONTRACTS = Path(__file__).resolve().parents[2] / "src-tauri" / "contracts"
CASES = [
    ("get_map_catalog", "map/catalog.result.json", "map/catalog.json"),
    ("get_weather_field", "map/field.result.json", "map/field.shrf"),
    ("get_radar_frame", "map/radar.result.json", "map/radar.shrf"),
    ("get_radar_site_frame", "map/radar-site.result.json", "map/radar-site.shrf"),
    ("get_radar_sites", "map/radar-sites.result.json", "map/radar-sites.json"),
    ("get_satellite_frame", "map/satellite.result.json", "map/satellite.shrf"),
    (
        "get_satellite_frame",
        "map/satellite.result.visible.json",
        "map/satellite.visible.shrf",
    ),
    ("get_storm_reports", "map/reports.result.json", "map/reports.json"),
    (
        "get_report_options",
        "map/reports-options.result.json",
        "map/reports-options.json",
    ),
    ("get_spc_outlook", "map/outlook.result.cat.json", "map/outlook.cat.json"),
    ("get_field_contours", "map/contours.result.json", "map/contours.json"),
    ("get_surface_plots", "map/surface.result.json", "map/surface.json"),
    ("get_place_labels", "map/places.result.json", "map/places.json"),
    (
        "get_station_availability",
        "map/availability.result.json",
        "map/availability.json",
    ),
    (
        "get_station_catalog",
        "stations.catalog.result.seed-fallback.json",
        "stations.catalog.seed-fallback.json",
    ),
    (
        "get_model_catalog",
        "soundings/models.catalog.result.json",
        "soundings/models.catalog.json",
    ),
    ("get_model_status", "soundings/models.status.result.json", "soundings/models.status.json"),
    (
        "open_sounding",
        "soundings/open.result.example.json",
        "soundings/summary.example.json",
    ),
    (
        "get_sounding_analysis",
        "soundings/analysis.result.example.json",
        "soundings/analysis.example.json",
    ),
    (
        "get_sounding_trends",
        "soundings/trends.result.example.json",
        "soundings/trends.example.json",
    ),
    (
        "get_ensemble_summary",
        "soundings/ensemble.result.example.json",
        "soundings/ensemble.example.json",
    ),
    (
        "get_threshold_metrics",
        "soundings/threshold-metrics.result.json",
        "soundings/threshold-metrics.json",
    ),
    (
        "evaluate_threshold",
        "soundings/threshold.result.example.json",
        "soundings/threshold.example.json",
    ),
    (
        "export_threshold_csv",
        "soundings/threshold-csv.result.example.json",
        "soundings/threshold-csv.example.json",
    ),
]


@pytest.mark.parametrize("command,bridge,public", CASES)
def test_browser_results_match_desktop_contracts(command, bridge, public):
    source = json.loads((CONTRACTS / "bridge" / bridge).read_bytes())
    expected = (CONTRACTS / "public" / public).read_bytes()
    result = public_result(command, source)
    assert result == (expected if public.endswith(".shrf") else json.loads(expected))


@pytest.fixture
def browser():
    with BrowserServer(("127.0.0.1", 0), "http://127.0.0.1:5174") as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        yield f"http://127.0.0.1:{server.server_port}/__backend"
        server.shutdown()
        worker.join(timeout=2)


def post(url, method, params=None, origin="http://127.0.0.1:5174"):
    return urlopen(
        Request(
            url,
            json.dumps({"id": 1, "method": method, "params": params or {}}).encode(),
            {"Content-Type": "application/json", "Origin": origin},
        ),
        timeout=5,
    )


def test_http_calls_real_handler_with_snake_case_args(browser, monkeypatch):
    def outlook(params):
        assert params == {"product": "cat", "valid_time": "2026-10-04T21:00:00Z"}
        return json.loads(
            (CONTRACTS / "bridge/map/outlook.result.cat.json").read_bytes()
        )

    monkeypatch.setitem(HANDLERS, "outlooks.spc", outlook)
    with post(
        browser,
        "get_spc_outlook",
        {"product": "cat", "validTime": "2026-10-04T21:00:00Z"},
    ) as response:
        assert json.load(response) == json.loads(
            (CONTRACTS / "public/map/outlook.cat.json").read_bytes()
        )
    with post(browser, "get_backend_health") as response:
        assert json.load(response)["bridge"]["status"] == "up"


def test_http_model_status_accepts_cycle_parameters(browser, monkeypatch):
    monkeypatch.setitem(HANDLERS, "models.status", lambda args: {
        **json.loads((CONTRACTS / "bridge/soundings/models.status.result.json").read_bytes()),
        "model": args["model"], "run_time": args["run_time"], "forecast_hour": args["forecast_hour"],
    })
    with post(browser, "get_model_status", {"model": "hrrr", "runTime": "2026-06-26T00:00:00Z", "forecastHour": 0}) as response:
        assert json.load(response) == json.loads((CONTRACTS / "public/soundings/models.status.json").read_bytes())


def test_http_rejects_cross_origin_and_invalid_requests(browser):
    for method, params, origin, status in [
        ("get_backend_health", {}, "https://untrusted.example", 403),
        ("get_backend_health", {"extra": True}, "http://127.0.0.1:5174", 400),
        ("fields.grid", {}, "http://127.0.0.1:5174", 400),
        ("get_spc_outlook", {"validTime": "not-a-time"}, "http://127.0.0.1:5174", 400),
    ]:
        with pytest.raises(HTTPError) as raised:
            post(browser, method, params, origin)
        assert raised.value.code == status
    with pytest.raises(HTTPError) as raised:
        urlopen(
            Request(
                browser,
                b'{"id":1,"method":"get_backend_health","params":{"bad":NaN}}',
                {"Content-Type": "application/json"},
            ),
            timeout=5,
        )
    assert json.load(raised.value)["code"] == "BRIDGE_PROTOCOL"


def test_special_request_arguments_and_corrupt_provider_bytes():
    assert bridge_request("inspect_wrf", {"filePath": "/data/wrfout.nc"}) == (
        "soundings.inspect_wrf", {"file_path": "/data/wrfout.nc"},
    )
    assert bridge_request("open_sounding", {"request": {
        "kind": "wrf", "filePath": "/data/wrfout.nc", "time": "2014-06-16T20:00:00Z",
        "latitude": 41, "longitude": -96,
    }}) == ("soundings.open_wrf", {"file_path": "/data/wrfout.nc", "time": "2014-06-16T20:00:00Z", "latitude": 41, "longitude": -96})
    assert bridge_request(
        "open_sounding",
        {"request": {"kind": "era5", "time": "2018-09-01T05:00:00Z", "latitude": 35.22, "longitude": -97.44, "location": None}},
    ) == ("soundings.open_era5", {"time": "2018-09-01T05:00:00Z", "latitude": 35.22, "longitude": -97.44, "location": None})
    assert bridge_request(
        "open_sounding",
        {"request": {"kind": "file", "fileName": "x.txt", "contentBase64": "YWJj"}},
    ) == ("soundings.open_file", {"file_name": "x.txt", "content_base64": "YWJj"})
    assert bridge_request("get_radar_site_frame", {"site": "KTLX"}) == (
        "radar.site",
        {"site_id": "KTLX"},
    )
    assert bridge_request("render_sounding", {"soundingId": "id", "ascent": "MU"}) == (
        "soundings.render",
        {"sounding_id": "id", "parcel": "MU"},
    )
    assert bridge_request("render_sounding_view", {
        "soundingId": "id", "ascent": "MU", "imageMode": "uhd", "view": {"edits": []}
    }) == ("soundings.view", {
        "sounding_id": "id", "parcel": "MU", "image_mode": "uhd", "view": {"edits": []}
    })
    with pytest.raises(ValueError, match="invalid provider PNG"):
        public_result("get_radar_frame", {"png_base64": "YWJj", "byte_length": 3})
