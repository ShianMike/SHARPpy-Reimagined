"""HiResW products use their published records, cycles and native 5 km grid."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import fields, contours
from sharpmod.bridge.errors import BridgeError
from sharpmod.models.lambert_grid import HRW_GRID
from sharpmod.providers import hrrr_products as hp, hrrr_field as hf

RUN = datetime(2026, 10, 27, tzinfo=timezone.utc)
REQUEST = {"run_time": "2026-10-27T00:00:00Z", "forecast_hour": 6}


@pytest.mark.parametrize("model", hp.HRW_MODELS)
def test_hrw_supported_parameters_and_native_cycles(model):
    products = {p.key: p for p in hp.available_products(model)}
    assert len(products) == 28
    assert {"refc", "tmp-2m", "hgt-wind-200", "apcp", "shear-0-1km", "uh-0-3km", "maxuvv"} <= products.keys()
    assert not products.keys() & {"ltng", "efhl", "snod", "prate", "prate-frzr", "prate-sleet", "prate-snow", "mucape", "scp", "stp"}
    assert all(p.sources == ("sfc",) for p in products.values())
    assert products["refc"].fields[0].level == "entire atmosphere (considered as a single layer)"
    assert products["maxuvv"].fields[0].level == "100-1000 mb"
    assert products["uh-0-3km"].label.endswith("(1 h max)")
    assert products["lftx"].derive({"lftx": np.array([273.15, 268.15])}) == pytest.approx([0, -5])
    limit = 60 if model == "hrw-fv3" else 48
    catalog = fields.catalog({"model": model})
    assert catalog["field_model"] == model
    assert catalog["max_forecast_hours"] == [limit if h in (0, 12) else 0 for h in range(24)]
    assert fields._request({**REQUEST, "model": model, "product": "tmp-2m", "forecast_hour": limit})[-1] == limit
    for change in ({"forecast_hour": limit + 1}, {"run_time": "2026-10-27T06:00:00Z"}, {"product": "prate"}):
        with pytest.raises(BridgeError):
            fields._request({**REQUEST, "model": model, "product": "refc", **change})
    url = hf.grib_url(RUN, 6, "sfc", model)
    assert ("fv3_5km" if model == "hrw-fv3" else "arw_5km") in url
    assert url.endswith("conusmem2.grib2" if model == "hrw-wrf-nssl" else "conus.grib2")


@pytest.mark.parametrize("model", hp.HRW_MODELS)
def test_picker_checks_every_ingredient_at_the_selected_hour(model, monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    # Native NOAA inventory spellings; maxima have no analysis record.
    inventory = "\n".join((
        "1:0:d=2026102700:TMP:2 m above ground:6 hour fcst:",
        "2:100:d=2026102700:REFC:entire atmosphere (considered as a single layer):6 hour fcst:",
        "3:200:d=2026102700:MAXUVV:100-1000 mb:5-6 hour max fcst:",
        "4:300:d=2026102700:MXUPHL:3000-0 m above ground:5-6 hour max fcst:",
        "5:400:d=2026102700:UGRD:500 mb:6 hour fcst:",
        "6:500:d=2026102700:HGT:500 mb:6 hour fcst:",
    ))
    rows = hf.parse_inventory(inventory)
    calls = []
    def published(url):
        calls.append(url)
        return rows[:2] if ".f00." in url else rows
    monkeypatch.setattr(hf, "fetch_inventory", published)
    catalog = fields.catalog({**REQUEST, "model": model})
    assert {p["key"] for p in catalog["products"]} == {"tmp-2m", "refc", "maxuvv", "uh-0-3km"}
    assert len(calls) == 1
    analysis = fields.catalog({**REQUEST, "model": model, "forecast_hour": 0})
    assert {p["key"] for p in analysis["products"]} == {"tmp-2m", "refc"}
    assert "hgt-wind-500" not in {p["key"] for p in catalog["products"]}  # V wind is missing.


@pytest.mark.parametrize("model", hp.HRW_MODELS)
def test_hrw_grid_decode_and_geographic_placement(model):
    size = int(np.prod(HRW_GRID.shape))
    api = SimpleNamespace(codes_new_from_message=lambda _: 1,
        codes_get=lambda _, key: {"Ni": 1473, "Nj": 1025, "missingValue": 9999}[key],
        codes_get_values=lambda _: np.full(size, 280), codes_release=lambda _: None)
    assert hf.decode_message(b"GRIB", api, model=model).shape == (1025, 1473)
    with pytest.raises(hf.HrrrFieldError, match="unexpected model grid"):
        hf.decode_message(b"GRIB", api)
    lon, lat = HRW_GRID.inverse(HRW_GRID.x0, HRW_GRID.y0)
    assert (lon, lat) == pytest.approx((-133.459, 12.19))
    bounds = (-98, -97, 35, 36)
    mapping = hf.index_map((2, 2), bounds, model=model)
    for index, (lon, lat) in enumerate(((-97.75, 35.75), (-97.25, 35.75), (-97.75, 35.25), (-97.25, 35.25))):
        x, y = HRW_GRID.forward(lon, lat)
        assert mapping.inside[index]
        assert mapping.flat[index] == round((y - HRW_GRID.y0) / 5079) * 1473 + round((x - HRW_GRID.x0) / 5079)


@pytest.mark.parametrize("model", hp.HRW_MODELS)
def test_scalar_contours_and_wind_share_hrw_geometry(model, monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(fields, "FRAME", (32, 16))
    calls = []
    def download(product, run, hour, **kwargs):
        calls.append(kwargs["model"])
        return {spec.name: np.full(HRW_GRID.shape, 10 if spec.variable == "UGRD" else 5000 if spec.variable == "HGT" else 0, dtype=np.float32)
                for spec in product.all_fields}
    monkeypatch.setattr(hf, "_download_fields", download)
    hf.clear_derived_cache()
    try:
        request = {**REQUEST, "model": model, "product": "hgt-wind-500"}
        scalar, auxiliary = fields.grid(request), contours.contours(request)
        assert scalar["bounds"] == auxiliary["wind"]["bounds"] == fields._bounds(HRW_GRID.bounds())
        assert scalar["value_max"] == pytest.approx(10 * hp.MS_TO_KT)
        assert "HiResW" in scalar["attribution"] and "hiresw" in scalar["source_url"]
        assert calls == [model]
    finally:
        hf.clear_derived_cache()
