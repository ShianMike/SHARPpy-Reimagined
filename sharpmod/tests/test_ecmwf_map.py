"""ECMWF map fields must keep their grid, units and published ingredients."""

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import contours, fields
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import ecmwf_fields as ec, hrrr_field as hf, hrrr_products as hp

RUN = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
REQUEST = {"run_time": "2026-10-26T12:00:00Z", "forecast_hour": 12}


@pytest.mark.parametrize("model", hp.ECMWF_MODELS)
def test_only_twenty_supported_products_and_native_forecast_steps(model):
    catalog = fields.catalog({"model": model})
    keys = {product["key"] for product in catalog["products"]}
    assert len(keys) == 20
    assert {"tmp-2m", "rh-2m", "snod", "mucape", "prate-frzr", "hgt-wind-200"} <= keys
    assert not keys & {"refc", "ltng", "sbcape", "mlcape", "stp", "shear-0-1km", "uh-0-3km", "uh-2-5km-1h", "uh-2-5km-3h"}
    assert catalog["default_product"] == "tmp-2m"
    assert [catalog["max_forecast_hours"][h] for h in (0, 6, 12, 18)] == [360, 144, 360, 144]
    for hour in (0, 144, 150, 360):
        assert fields._request({**REQUEST, "model": model, "product": "tmp-2m", "forecast_hour": hour})[-1] == hour
    for change in ({"forecast_hour": 1}, {"forecast_hour": 147}, {"product": "refc"},
                   {"forecast_hour": 150, "run_time": "2026-10-26T06:00:00Z"}):
        with pytest.raises(BridgeError):
            fields._request({**REQUEST, "model": model, "product": "tmp-2m", **change})


def test_eccodes_inventory_preserves_levels_ranges_and_accumulations():
    text = "\n".join(json.dumps(row) for row in (
        {"param": "tp", "levtype": "sfc", "step": "12", "_offset": 100, "_length": 50},
        {"param": "u", "levtype": "pl", "levelist": "500", "_offset": 150, "_length": 80},
    ))
    rows = ec.parse_inventory(text)
    assert hf.locate(rows, hp.F_APCP).length == 50
    assert hf.locate(rows, hp.FieldSpec("u500", "prs", "UGRD", "500 mb")).offset == 150
    for invalid in ("{", json.dumps({"param": "2t", "levtype": "sfc", "_offset": -1, "_length": 20})):
        with pytest.raises(hf.HrrrFieldError, match="invalid record"):
            ec.parse_inventory(invalid)


@pytest.mark.parametrize("model", hp.ECMWF_MODELS)
def test_catalog_hides_missing_ingredients_for_the_exact_run_and_hour(monkeypatch, tmp_path, model):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    specs = {spec for product in ec.products() for spec in product.all_fields}
    rows = tuple(hf.InventoryRow(i, i * 100, 100, spec.variable, spec.level,
                                spec.forecast.replace("*", "12")) for i, spec in enumerate(specs))
    missing = {"ptype", "rsn", "CAPE"}
    urls = []

    def inventory(url):
        urls.append(url)
        return tuple(row for row in rows if row.variable not in missing)

    monkeypatch.setattr(hf, "fetch_inventory", inventory)
    catalog = fields.catalog({**REQUEST, "model": model})
    keys = {product["key"] for product in catalog["products"]}
    assert len(keys) == 15
    assert not keys & {"mucape", "snod", "prate-frzr", "prate-sleet", "prate-snow"}
    assert all("20261026120000-12h" in url for url in urls)
    assert len(set(urls)) == (2 if model == "ecmwf-ifs-9km" else 1)
    if model == "ecmwf-ifs-9km":
        missing.clear()
        def partial_inventory(url):
            if url.endswith("-pl.grib2"):
                raise hf.HrrrFieldUnavailable("pressure file is still publishing")
            return inventory(url)
        monkeypatch.setattr(hf, "fetch_inventory", partial_inventory)
        partial = fields.catalog({**REQUEST, "model": model})
        assert len(partial["products"]) == 14
        assert not any(product["category"] == "Height and Wind" for product in partial["products"])
    monkeypatch.setattr(hf, "fetch_inventory", lambda _url: ())
    with pytest.raises(BridgeError, match="No supported ECMWF"):
        fields.catalog({**REQUEST, "model": model})


def test_derived_humidity_snow_depth_and_precipitation_types():
    products = {product.key: product for product in ec.products()}
    rh = products["rh-2m"].derive({"tmp2m": np.array([293.15, 293.15, np.nan]),
                                  "dpt2m": np.array([283.15, 293.15, 283.15])})
    assert rh[:2] == pytest.approx([52.5, 100], abs=0.2)
    assert np.isnan(rh[2])
    snow = products["snod"].derive({"swe": np.array([0.02, 0, 0.02, -1, np.nan]),
                                   "snow_density": np.array([200, 0, 0, 200, 200])})
    assert snow[:2] == pytest.approx([100 / 25.4, 0])
    assert np.isnan(snow[2:]).all()
    for key, flags in (("prate-frzr", [1, 0, 0, 0, 0]), ("prate-sleet", [0, 1, 0, 0, 0]),
                       ("prate-snow", [0, 0, 1, 1, 0])):
        rate = products[key].derive({"prate": np.full(6, 0.001), "ptype": np.array([3, 8, 5, 6, 7, 255])})
        assert rate[:5] == pytest.approx(np.array(flags) * 3.6)
        assert np.isnan(rate[-1])


@pytest.mark.parametrize("model", hp.GLOBAL_MODELS)
def test_native_cell_mapping_wraps_longitude_and_keeps_north_at_the_top(model):
    mapping = hf.index_map((4, 2), (-180, 180, -60, 60), model=model)
    native = ec.grid(model)
    for index, (lon, lat) in enumerate(((-135, 30), (-45, 30), (45, 30), (135, 30),
                                       (-135, -30), (-45, -30), (45, -30), (135, -30))):
        row, col = round((90 - lat) / native.spacing), round((lon + 180) / native.spacing)
        assert mapping.inside[index] and mapping.flat[index] == row * native.shape[1] + col
    seam = hf.index_map((1, 1), (179.9, 180.1, -0.01, 0.01), model=model)
    assert seam.flat[0] % native.shape[1] == 0
    cells = hf.index_map((native.shape[1], 1), (-180, 180, -0.01, 0.01), model=model)
    assert len(set(cells.flat % native.shape[1])) == native.shape[1]


def test_decoder_converts_metres_to_millimetres_after_masking_missing(monkeypatch):
    native = SimpleNamespace(shape=(1, 2), spacing=0.25)
    monkeypatch.setattr(hf, "field_grid", lambda _model: native)
    header = {"Ni": 2, "Nj": 1, "gridType": "regular_ll", "latitudeOfFirstGridPointInDegrees": 90.0,
              "longitudeOfFirstGridPointInDegrees": 180.0, "iDirectionIncrementInDegrees": 0.25,
              "jDirectionIncrementInDegrees": 0.25, "jScansPositively": 0, "iScansNegatively": 0,
              "jPointsAreConsecutive": 0, "alternativeRowScanning": 0,
              "uvRelativeToGrid": 0, "shortName": "tp", "missingValue": 9999}
    api = SimpleNamespace(codes_new_from_message=lambda _: 1, codes_get=lambda _, key: header[key],
                          codes_get_values=lambda _: [0.0254, 9999], codes_release=lambda _: None)
    result = hf.decode_message(b"GRIB", api, model=hp.ECMWF_MODELS[0])
    assert result[0, 0] == pytest.approx(25.4)
    assert np.isnan(result[0, 1])
    header["longitudeOfFirstGridPointInDegrees"] = 0
    with pytest.raises(hf.HrrrFieldError, match="latitude/longitude grid"):
        hf.decode_message(b"GRIB", api, model=hp.ECMWF_MODELS[0])


@pytest.mark.parametrize("model", hp.GLOBAL_MODELS)
def test_shared_scalar_contour_and_wind_pipeline_uses_global_grid(tmp_path, monkeypatch, model):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(fields, "FRAME", (16, 8))
    monkeypatch.setattr(hf, "field_grid", lambda _model: SimpleNamespace(shape=(5, 8), spacing=45))
    monkeypatch.setattr(hf, "_download_fields", lambda product, *_args, **_kwargs: {
        spec.name: np.full((5, 8), 10 if spec.variable == "UGRD" else 5000 if spec.variable == "HGT" else 0, dtype=np.float32)
        for spec in product.all_fields
    })
    hf.clear_derived_cache()
    request = {**REQUEST, "model": model, "product": "hgt-wind-500"}
    try:
        scalar = fields.grid(request)
        auxiliary = contours.contours(request)
        assert scalar["bounds"] == auxiliary["wind"]["bounds"] == fields._bounds(ec.BOUNDS)
        assert scalar["value_max"] == pytest.approx(10 * hp.MS_TO_KT)
        packed = np.frombuffer(base64.b64decode(auxiliary["wind"]["data_base64"]), dtype="<u2")
        assert (packed & 511 == 270).all()  # These global winds already reference true north.
        assert ("GFS" if model == "gfs" else "AIGFS" if model == "aigfs" else "ECMWF") in scalar["attribution"]
        assert ("/gfs." if model == "gfs" else "/aigfs/" if model == "aigfs" else "/ifs/") in scalar["source_url"]
    finally:
        hf.clear_derived_cache()
