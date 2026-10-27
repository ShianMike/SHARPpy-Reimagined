"""GFS maps use native fields, global geometry and the full forecast cadence."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import fields, frame_cache
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import ecmwf_fields as ec, hrrr_field as hf, hrrr_products as hp
from sharpmod.tools.model_extract import forecast_hours

REQUEST = {"model": "gfs", "run_time": "2026-10-27T00:00:00Z", "forecast_hour": 6}


def test_gfs_reuses_ecmwf_parameter_choices_with_its_own_native_ingredients():
    products = hp.available_products("gfs")
    assert {p.key for p in products} == {p.key for p in ec.products()}
    assert len(products) == 20
    assert all(product is hp.PRODUCTS[product.key] for product in products)
    catalog = fields.catalog({"model": "gfs"})
    assert catalog["field_model"] == "gfs" and catalog["default_product"] == "tmp-2m"
    assert catalog["field_bounds"] == fields._bounds(ec.BOUNDS)
    assert catalog["max_forecast_hours"] == [384 if cycle % 6 == 0 else 0 for cycle in range(24)]


def test_native_forecast_cadence_is_hourly_to_f120_then_three_hourly():
    for hour in (0, 1, 119, 120, 123, 381, 384):
        assert fields._request({**REQUEST, "product": "tmp-2m", "forecast_hour": hour})[-1] == hour
    for change in ({"forecast_hour": 121}, {"forecast_hour": 122}, {"forecast_hour": 385},
                   {"run_time": "2026-10-27T01:00:00Z"}, {"product": "refc"}):
        with pytest.raises(BridgeError):
            fields._request({**REQUEST, "product": "tmp-2m", **change})


def test_selected_run_inventory_hides_missing_fields_and_f000_precipitation(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    specs = {spec for product in hp.available_products("gfs") for spec in product.all_fields}
    rows = tuple(hf.InventoryRow(i, i * 100, 100, spec.variable, spec.level,
                                spec.forecast.replace("*", "6 hour")) for i, spec in enumerate(specs))
    missing = {"SNOD", "CAPE"}
    calls = []

    def inventory(url):
        calls.append(url)
        return tuple(row for row in rows if row.variable not in missing
                     and (not url.endswith("f000") or row.variable != "APCP"))

    monkeypatch.setattr(hf, "fetch_inventory", inventory)
    catalog = fields.catalog(REQUEST)
    assert len(catalog["products"]) == 18
    assert not {p["key"] for p in catalog["products"]} & {"mucape", "snod"}
    assert set(calls) == {"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261027/00/atmos/gfs.t00z.pgrb2.0p25.f006"}
    missing.clear()
    assert len(fields.catalog({**REQUEST, "forecast_hour": 0})["products"]) == 19
    assert len(fields.catalog({**REQUEST, "forecast_hour": 384})["products"]) == 20
    monkeypatch.setattr(hf, "fetch_inventory", lambda _url: ())
    with pytest.raises(BridgeError, match="No supported GFS"):
        fields.catalog(REQUEST)


def test_native_run_total_and_instantaneous_rate_skip_interval_records():
    rows = hf.parse_inventory("1:0:d=2026102700:APCP:surface:378-384 hour acc fcst:\n"
                              "2:100:d=2026102700:APCP:surface:0-16 day acc fcst:\n"
                              "3:200:d=2026102700:PRATE:surface:378-384 hour ave fcst:\n"
                              "4:300:d=2026102700:PRATE:surface:384 hour fcst:\n")
    assert hf.locate(rows, hp.F_APCP).offset == 100
    assert hf.locate(rows, hp.F_PRATE).offset == 300


def test_gfs_decoder_wraps_longitude_and_keeps_precipitation_in_millimetres(monkeypatch):
    monkeypatch.setattr(hf, "field_grid", lambda _model: SimpleNamespace(shape=(1, 4), spacing=0.25))
    header = {"Ni": 4, "Nj": 1, "gridType": "regular_ll", "latitudeOfFirstGridPointInDegrees": 90.0,
              "longitudeOfFirstGridPointInDegrees": 0.0, "iDirectionIncrementInDegrees": 0.25,
              "jDirectionIncrementInDegrees": 0.25, "jScansPositively": 0, "iScansNegatively": 0,
              "jPointsAreConsecutive": 0, "alternativeRowScanning": 0, "uvRelativeToGrid": 0,
              "shortName": "tp", "missingValue": 9999}
    api = SimpleNamespace(codes_new_from_message=lambda _: 1, codes_get=lambda _, key: header[key],
                          codes_get_values=lambda _: [25.4, 9999, 3, 4], codes_release=lambda _: None)
    result = hf.decode_message(b"GRIB", api, model="gfs")
    assert result[0, :3] == pytest.approx([3, 4, 25.4])
    assert np.isnan(result[0, 3])
    header["uvRelativeToGrid"] = 1
    with pytest.raises(hf.HrrrFieldError, match="latitude/longitude grid"):
        hf.decode_message(b"GRIB", api, model="gfs")


def test_cache_reports_the_full_209_hour_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    hours = list(forecast_hours("gfs"))
    assert len(hours) == 209 and hours[-1] == 384
    frame_cache.write(frame_cache.path("grid", "gfs", "tmp-2m", datetime(2026, 10, 27, tzinfo=timezone.utc), 384, fields.FRAME),
                      {"product": "tmp-2m", "forecast_hour": 384})
    request = {"model": "gfs", "products": ["tmp-2m"], "run_time": REQUEST["run_time"], "hours": hours}
    assert frame_cache.cached_hours(request) == [384]
    with pytest.raises(BridgeError):
        frame_cache.cached_hours({**request, "hours": [0] * 386})
