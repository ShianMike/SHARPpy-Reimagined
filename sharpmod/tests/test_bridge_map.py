"""The bridge's map operations against real sharpmod, with offline stand-ins.

The providers' network calls are replaced by the stand-ins of
``scripts/export_map_fixtures.py``; the bridge code, the HRRR derivation and
reprojection, the SPC parser, and the place index are the real ones.
"""

from __future__ import annotations

import base64
import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from sharpmod.bridge import fields, map_layers
from sharpmod.bridge.errors import BridgeError

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "src-tauri" / "contracts"
EXPORTER = ROOT / "scripts" / "export_map_fixtures.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_map_fixtures", EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORT = _exporter()


@pytest.fixture(autouse=True)
def map_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path / "maps"))


@pytest.fixture
def offline():
    with EXPORT.stand_ins():
        yield


def test_downloaded_frames_survive_memory_eviction_and_corrupt_files_reload(offline, monkeypatch):
    from sharpmod.bridge import contours, frame_cache
    from sharpmod.providers import hrrr_field

    request = {"model": "hrrr", "product": "hgt-wind-500", "run_time": EXPORT.RUN_TIME, "forecast_hour": 1}
    grid = fields.grid(request)
    lines = contours.contours(request)
    hrrr_field.clear_cache()
    monkeypatch.setattr(hrrr_field, "_download_fields", lambda *_args, **_kwargs: pytest.fail("redownloaded a saved frame"))
    assert fields.grid(request) == grid
    assert contours.contours(request) == lines
    model, product, run, hour = fields._request(request)
    target = frame_cache.path("grid", model, product.key, run, hour, fields.FRAME)
    assert target != frame_cache.path("grid", "rrfs-a", product.key, run, hour, fields.FRAME)
    assert target != frame_cache.path("grid", model, product.key, run, hour, (1, 1))
    target.write_bytes(b"interrupted download")
    assert frame_cache.read(target) is None
    with pytest.raises(BridgeError):
        fields.grid({**request, "forecast_hour": -1})


def test_the_committed_map_fixtures_are_current():
    for relative, data in EXPORT.build().items():
        assert (CONTRACTS / relative).read_bytes() == data, (
            f"{relative} is stale; run scripts/export_map_fixtures.py"
        )


@pytest.mark.parametrize("model", ["hrrr", "rrfs-a", "nam-3km-conus", *(f"refs-m{i}" for i in range(1, 6))])
def test_model_catalogues_reuse_scales_and_only_offer_published_products(model):
    from sharpmod.providers import hrrr_products as hp

    catalog = fields.catalog({"model": model})
    assert catalog["field_model"] == model
    assert len(catalog["products"]) == (22 if model.startswith("refs-") else 36 if model == "nam-3km-conus" else 38)
    assert any(item["key"] == "ltng" for item in catalog["products"]) == (model != "nam-3km-conus")
    if model == "nam-3km-conus":
        with pytest.raises(BridgeError, match="not a nam-3km-conus field product"):
            fields._request({"model": model, "product": "ltng", "run_time": EXPORT.RUN_TIME, "forecast_hour": 1})
    for product in hp.available_products(model):
        assert product.palette is hp.PRODUCTS[product.key].palette
        for spec in product.all_fields:
            if model == "nam-3km-conus":
                assert spec.source == hp.SOURCE_SURFACE
            if model != "hrrr" and spec.variable == "REFC":
                assert spec.level == "entire atmosphere (considered as a single layer)"
            if spec.variable == "LTNG":
                assert spec.level == "entire atmosphere"
                assert spec.forecast == ("max fcst" if model == "rrfs-a" or model.startswith("refs-") else "")
    if model.startswith("refs-"):
        assert "Composite Parameters" not in catalog["categories"]
        assert {item["key"] for item in catalog["products"]}.isdisjoint(hp._REFS_UNSUPPORTED)


def test_switching_models_keeps_separate_numeric_grids(offline, monkeypatch):
    from sharpmod.providers import hrrr_field

    calls = []
    def download(_product, _run, _hour, *, model, **_kwargs):
        calls.append(model)
        return {"refc": np.full(hrrr_field.HRRR_SHAPE, len(calls), dtype=np.float32)}

    monkeypatch.setattr(hrrr_field, "_download_fields", download)
    models = ["hrrr", "rrfs-a", "nam-3km-conus", *(f"refs-m{i}" for i in range(1, 6))]
    for _repeat in range(2):
        for value, model in enumerate(models, start=1):
            grid = fields.grid({"model": model, "product": "refc", "run_time": EXPORT.RUN_TIME, "forecast_hour": 1})
            assert grid["value_min"] == grid["value_max"] == value
            if model.startswith("refs-"):
                assert f"m00{model[-1]}" in grid["source_url"]
    assert calls == models


@pytest.mark.parametrize(("model", "hour"), [("nam-3km-conus", 60), ("rrfs-a", 84), ("refs-m5", 60)])
def test_model_forecast_limits_apply_to_grids_and_contours(model, hour):
    request = {"model": model, "product": "hgt-wind-300", "run_time": EXPORT.RUN_TIME, "forecast_hour": hour}
    assert fields._request(request)[-1] == hour
    for changes in ({"forecast_hour": hour + 1}, {"run_time": "2026-10-04T17:00:00Z"}):
        with pytest.raises(BridgeError):
            fields._request({**request, **changes})
    with pytest.raises(BridgeError, match="not a refs-m5 field product"):
        fields._request({**request, "model": "refs-m5", "product": "mlcape"})


def test_nam_one_kilometre_shear_interpolates_above_terrain_and_keeps_missing():
    from sharpmod.providers import hrrr_products as hp

    product = next(item for item in hp.available_products("nam-3km-conus") if item.key == "shear-0-1km")
    terrain = np.array([[0, 2000, np.nan]], dtype=np.float32)
    inputs = {"sfchgt": terrain, "ugrd10": np.array([[3, 3, 3]]), "vgrd10": np.array([[4, 4, 4]])}
    for level, height in zip(hp._SHEAR_LEVELS, (600, 1400, 2400, 3400, 4400, 6400), strict=True):
        inputs[f"hgt{level}"] = np.full(terrain.shape, height)
        for component, base, slope in (("ugrd", 3, 0.01), ("vgrd", 4, 0.02)):
            inputs[f"{component}{level}"] = base + (height - terrain - 10) * slope
    result = product.derive(inputs)
    np.testing.assert_allclose(result[0, :2], np.hypot(9.9, 19.8) * hp.MS_TO_KT, rtol=1e-6)
    assert np.isnan(result[0, 2])


def test_the_grid_is_the_derived_field_quantized_without_visible_loss(offline):
    from sharpmod.providers import hrrr_field

    grid = fields.grid(
        {"product": "refc", "run_time": EXPORT.RUN_TIME, "forecast_hour": 1}
    )
    cells = np.frombuffer(base64.b64decode(grid["data_base64"]), dtype="<u2")
    run = datetime(2026, 10, 4, 18, tzinfo=UTC)
    values = hrrr_field.cached_derived("refc", run, 1, EXPORT.FIXTURE_FRAME).reshape(-1)
    decoded = np.where(
        cells == grid["nodata"], np.nan, cells * grid["scale"] + grid["offset"]
    )
    finite = np.isfinite(values)
    assert np.array_equal(finite, np.isfinite(decoded))
    assert (
        np.nanmax(np.abs(decoded[finite] - values[finite])) <= grid["scale"] / 2 + 1e-6
    )
    assert grid["value_min"] == pytest.approx(float(np.nanmin(values)))
    assert (grid["width"], grid["height"]) == EXPORT.FIXTURE_FRAME


@pytest.mark.parametrize(
    ("key", "record", "value", "units", "ticks"),
    [
        ("rh-2m", "rh2m", 67.5, "%", list(range(0, 101, 10))),
        ("ltng", "ltng", 18.5, "flashes", [0, 0.1, 0.3, 1, 3, 6, 15]),
    ],
)
def test_native_surface_grid_keeps_values_units_and_catalogue_scale(
    offline, monkeypatch, key, record, value, units, ticks
):
    from sharpmod.providers import hrrr_field

    monkeypatch.setattr(
        hrrr_field,
        "_download_fields",
        lambda *_args, **_kwargs: {
            record: np.full(hrrr_field.HRRR_SHAPE, value, dtype=np.float32)
        },
    )
    grid = fields.grid(
        {"product": key, "run_time": EXPORT.RUN_TIME, "forecast_hour": 1}
    )
    cells = np.frombuffer(base64.b64decode(grid["data_base64"]), dtype="<u2")
    finite = cells != grid["nodata"]
    assert finite.any() and (~finite).any()
    np.testing.assert_array_equal(cells[finite] * grid["scale"] + grid["offset"], value)
    assert grid["units"] == units
    assert grid["value_min"] == grid["value_max"] == value
    assert "wrfsfcf01.grib2" in grid["source_url"]
    product = next(item for item in fields.catalog({})["products"] if item["key"] == key)
    assert product["category"] == "Surface"
    assert product["palette"]["ticks"] == ticks


def test_wind_barbs_rotate_to_earth_and_keep_weak_calm_and_missing_winds():
    from pyproj import Transformer
    from sharpmod.bridge.contours import wind_grid
    from sharpmod.models.lambert_grid import HRRR_GRID, HRRR_PROJ4
    from sharpmod.providers import hrrr_field, hrrr_products

    product = hrrr_products.PRODUCTS["hgt-wind-300"]
    u = np.zeros(HRRR_GRID.shape, dtype=np.float32)
    v = np.zeros_like(u)
    indices = np.array([500 * u.shape[1] + col for col in (100, 600, 900, 1200, 1700)])
    u.ravel()[indices] = [10, 50, 0, np.nan, 20]
    v.ravel()[indices] = [0, 10, 0, 0, -10]
    mapping = hrrr_field._IndexMap(
        5,
        1,
        hrrr_field.COVERAGE_BOUNDS,
        indices,
        np.array([True, True, True, True, False]),
    )
    result = wind_grid(product, {"ugrd300": u, "vgrd300": v}, mapping)
    cells = np.frombuffer(base64.b64decode(result["data_base64"]), dtype="<u2")
    assert list(cells >> 9)[:3] == [
        4,
        20,
        0,
    ]  # 20 kt remains visible below the 50 kt colour floor.
    assert list(cells[3:]) == [65535, 65535]
    inverse = Transformer.from_crs(HRRR_PROJ4, "EPSG:4326", always_xy=True)
    for index, cell in zip(indices[:2], cells[:2], strict=True):
        lon, _ = inverse.transform(
            HRRR_GRID.x0 + index % u.shape[1] * HRRR_GRID.spacing,
            HRRR_GRID.y0 + index // u.shape[1] * HRRR_GRID.spacing,
        )
        turn = np.radians((lon - HRRR_GRID.lon_origin) * HRRR_GRID._n)
        earth_u = u.ravel()[index] * np.cos(turn) + v.ravel()[index] * np.sin(turn)
        earth_v = v.ravel()[index] * np.cos(turn) - u.ravel()[index] * np.sin(turn)
        expected = round(float(np.degrees(np.arctan2(-earth_u, -earth_v)) % 360)) % 360
        assert int(cell & 511) == expected


def test_a_forecast_hour_past_the_cycle_is_refused_honestly(offline):
    with pytest.raises(BridgeError) as info:
        fields.grid(
            {"product": "refc", "run_time": "2026-10-04T17:00:00Z", "forecast_hour": 30}
        )
    assert info.value.code == "FIELD_UNAVAILABLE"
    assert "F018" in str(info.value)
    with pytest.raises(BridgeError) as info:
        fields.grid(
            {"product": "nope", "run_time": EXPORT.RUN_TIME, "forecast_hour": 1}
        )
    assert info.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        ("HrrrFieldUnavailable", "FIELD_UNAVAILABLE", False),
        ("HrrrFieldOffline", "FIELD_FETCH_FAILED", True),
    ],
)
def test_field_failures_become_structured_errors(
    offline, monkeypatch, error, code, retryable
):
    from sharpmod.providers import hrrr_field

    def fail(*_args, **_kwargs):
        raise getattr(hrrr_field, error)("no data")

    monkeypatch.setattr(hrrr_field, "_download_fields", fail)
    with pytest.raises(BridgeError) as info:
        fields.grid(
            {"product": "mlcape", "run_time": EXPORT.RUN_TIME, "forecast_hour": 2}
        )
    assert (info.value.code, info.value.retryable) == (code, retryable)


def test_outlook_areas_keep_spc_colours_and_holes(offline):
    cat = map_layers.spc_outlook({"product": "cat", "valid_time": EXPORT.OUTLOOK_TIME})[
        "outlook"
    ]
    assert [area["label"] for area in cat["areas"]] == ["TSTM", "MRGL", "SLGT"]
    assert [len(area["rings"]) for area in cat["areas"]] == [2, 2, 1]
    assert cat["covers"] is True
    none = map_layers.spc_outlook(
        {"product": "wind", "valid_time": EXPORT.OUTLOOK_TIME}
    )
    assert none["outlook"] is None


def test_outlook_and_radar_failures_are_retryable(offline, monkeypatch):
    from sharpmod.providers import radar_mosaic, spc_outlook, spc_outlook_fetch

    def outlook_fail(*_args, **_kwargs):
        raise spc_outlook.OutlookError("bad payload")

    def radar_fail(*_args, **_kwargs):
        raise radar_mosaic.RadarError("HTTP 503")

    monkeypatch.setattr(spc_outlook_fetch, "fetch_layer", outlook_fail)
    monkeypatch.setattr(radar_mosaic, "fetch_frame", radar_fail)
    for call in (
        lambda: map_layers.spc_outlook(
            {"product": "cat", "valid_time": EXPORT.OUTLOOK_TIME}
        ),
        lambda: map_layers.radar_mosaic({}),
    ):
        with pytest.raises(BridgeError) as info:
            call()
        assert (info.value.code, info.value.retryable) == ("OVERLAY_FETCH_FAILED", True)


def test_browser_radar_keeps_the_providers_default_resolution(offline, monkeypatch):
    from sharpmod.providers import radar_mosaic

    fetch = radar_mosaic.fetch_frame
    calls = []

    def record(product, **kwargs):
        calls.append(kwargs)
        return fetch(product, **kwargs)

    monkeypatch.setattr(radar_mosaic, "fetch_frame", record)
    assert map_layers.radar_mosaic({})["byte_length"] > 0
    assert calls == [{}]


def test_places_are_the_major_cities_then_one_town_per_quarter_degree():
    from sharpmod.maps.map_geography import MAJOR_CITIES

    places = map_layers.place_labels({})["places"]
    cities = [place for place in places if place["tier"] == 1]
    assert [place["name"] for place in cities] == [
        name for name, _lat, _lon in MAJOR_CITIES
    ]
    towns = [place for place in places if place["tier"] == 2]

    def quarter(value: float) -> tuple[int, int]:
        whole = int(np.floor(value))
        return whole, min(3, int((value - whole) * 4.0))

    cells = {(quarter(town["lat"]), quarter(town["lon"])) for town in towns}
    assert len(towns) > 1000 and len(cells) == len(towns)


def test_availability_splits_bundled_and_extra_stations(offline):
    listed = map_layers.station_availability(
        {"observation_time": "2026-10-04T12:00:00Z"}
    )
    assert listed["listed_ids"] == ["72357", "72451"]
    assert [station["id"] for station in listed["extra_stations"]] == ["99901"]
    with pytest.raises(BridgeError) as info:
        map_layers.station_availability({"observation_time": "2026-10-06T12:00:00Z"})
    assert info.value.code == "INVALID_ARGUMENT"


def test_surface_plots_split_speeds_and_distinguish_every_state(offline):
    from sharpmod.bridge import surface_plots

    result = surface_plots.plots(
        {"latitude": 35.2, "longitude": -97.4, "valid_time": "2026-10-04T21:00:00Z"}
    )
    plots = {plot["id"]: plot for plot in result["plots"]}
    # 20 kt from the west: a staff pointing back into the wind, two barbs.
    assert plots["KOUN"]["staff"] == {
        "angle_deg": 180.0,
        "pennants": 0,
        "barbs": 2,
        "halves": 0,
    }
    assert plots["KOUN"]["upper_text"] == "22" and plots["KOUN"]["lower_text"] == "12"
    assert plots["KOUN"]["temperature_c"] == 21.5
    assert plots["KOUN"]["dewpoint_c"] == 12.2
    # Calm stands without a staff; a speed without a direction asks instead.
    assert plots["KOKC"]["wind_state"] == "calm" and plots["KOKC"]["staff"] is None
    assert plots["KSPS"]["wind_state"] == "direction-missing"
    # A stale plot keeps its offset; a checked station and a failed query
    # arrive once each, never twice over.
    assert plots["KSWA"]["stale"] and plots["KSWA"]["offset_min"] == -45.0
    assert plots["KHBR"]["availability"] == "unavailable"
    assert plots["KFOO"]["availability"] == "unknown"
    assert len(plots) == 6
    assert result["counts"] == {
        "matched": 4,
        "stale": 1,
        "no_observation": 1,
        "failed": 1,
        "nearby": 6,
        "queried": 6,
        "unqueried": 0,
    }
    # The speed split the paint path draws: 50s as pennants, 10s as barbs,
    # and the 5 remainder as one half-barb at the staff's inner end.
    assert surface_plots._staff_spec(-65.0, 0.0) == {
        "angle_deg": 0.0,
        "pennants": 1,
        "barbs": 1,
        "halves": 1,
    }
    assert surface_plots._staff_spec(0.0, None) is None
    assert surface_plots._staff_spec(-5.0, -1e-15)["angle_deg"] == 0.0


def test_the_map_handlers_load_no_qt_module():
    code = (
        "import sys; from sharpmod.bridge import fields, map_layers; "
        "fields.catalog({}); map_layers.place_labels({}); "
        "print(sorted(m for m in sys.modules if m.startswith(('PySide', 'PyQt'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
