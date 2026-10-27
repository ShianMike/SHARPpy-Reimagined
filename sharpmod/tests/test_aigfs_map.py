"""AIGFS exposes only supported map fields and never creates a sounding."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import fields, soundings
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import hrrr_field as hf, hrrr_products as hp

RUN = datetime(2026, 10, 27, tzinfo=timezone.utc)
REQUEST = {"model": "aigfs", "run_time": "2026-10-27T00:00:00Z", "forecast_hour": 0}


def test_only_native_products_and_six_hour_steps_to_f384():
    catalog = fields.catalog({"model": "aigfs"})
    assert {p["key"] for p in catalog["products"]} == {
        *(f"hgt-wind-{level}" for level in (200, 300, 500, 700, 850)),
        "tmp-2m", "wind-10m", "apcp", "lapse-700-500",
    }
    assert catalog["default_product"] == "tmp-2m"
    assert catalog["max_forecast_hours"] == [384 if cycle % 6 == 0 else 0 for cycle in range(24)]
    for hour in (0, 6, 384):
        assert fields._request({**REQUEST, "product": "tmp-2m", "forecast_hour": hour})[-1] == hour
    for change in ({"forecast_hour": 1}, {"forecast_hour": 385}, {"product": "dpt-2m"},
                   {"run_time": "2026-10-27T01:00:00Z"}):
        with pytest.raises(BridgeError):
            fields._request({**REQUEST, "product": "tmp-2m", **change})


def test_exact_run_inventory_hides_unpublished_ingredients_and_uses_run_total(monkeypatch):
    products = hp.available_products("aigfs")
    specs = {spec for product in products for spec in product.all_fields}
    rows = tuple(hf.InventoryRow(i, i * 100, 100, spec.variable, spec.level,
                                spec.forecast.replace("*", "384 hour")) for i, spec in enumerate(specs))
    calls = []

    def inventory(url):
        calls.append(url)
        surface = ".sfc." in url
        return tuple(row for row in rows if (row.level in {"2 m above ground", "10 m above ground", "surface"}) == surface
                     and ("f000" not in url or row.variable != "APCP")
                     and row.variable != "VGRD")

    monkeypatch.setattr(hf, "fetch_inventory", inventory)
    catalog = fields.catalog(REQUEST)
    assert {p["key"] for p in catalog["products"]} == {"tmp-2m", "lapse-700-500"}
    assert {url.rsplit("/", 1)[-1] for url in calls} == {"aigfs.t00z.sfc.f000.grib2", "aigfs.t00z.pres.f000.grib2"}
    intervals = hf.parse_inventory("1:0:d=2026102700:APCP:surface:378-384 hour acc fcst:\n"
                                   "2:100:d=2026102700:APCP:surface:0-16 day acc fcst:\n")
    assert hf.locate(intervals, hp.F_APCP).offset == 100
    monkeypatch.setattr(hf, "fetch_inventory", lambda _url: ())
    with pytest.raises(BridgeError, match="No supported AIGFS"):
        fields.catalog(REQUEST)


def test_decode_rolls_zero_longitude_origin_and_preserves_missing_values(monkeypatch):
    monkeypatch.setattr(hf, "field_grid", lambda _model: SimpleNamespace(shape=(1, 4), spacing=0.25))
    header = {"Ni": 4, "Nj": 1, "gridType": "regular_ll", "latitudeOfFirstGridPointInDegrees": 90.0,
              "longitudeOfFirstGridPointInDegrees": 0.0, "iDirectionIncrementInDegrees": 0.25,
              "jDirectionIncrementInDegrees": 0.25, "jScansPositively": 0, "iScansNegatively": 0,
              "jPointsAreConsecutive": 0, "alternativeRowScanning": 0, "uvRelativeToGrid": 0,
              "shortName": "tp", "missingValue": 9999}
    api = SimpleNamespace(codes_new_from_message=lambda _: 1, codes_get=lambda _, key: header[key],
                          codes_get_values=lambda _: [1, 9999, 3, 4], codes_release=lambda _: None)
    result = hf.decode_message(b"GRIB", api, model="aigfs")
    assert result[0, :3] == pytest.approx([3, 4, 1])
    assert np.isnan(result[0, 3])
    header["longitudeOfFirstGridPointInDegrees"] = 180
    with pytest.raises(hf.HrrrFieldError, match="latitude/longitude grid"):
        hf.decode_message(b"GRIB", api, model="aigfs")


def test_native_contours_avoid_inventing_segments_above_the_bridge_limit(tmp_path, monkeypatch):
    from sharpmod.bridge import contours

    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(fields, "FRAME", (64, 32))
    monkeypatch.setattr(hf, "field_grid", lambda _model: SimpleNamespace(shape=(5, 8), spacing=45))
    monkeypatch.setattr(contours, "MAX_SEGMENTS", 100)
    product = next(p for p in hp.available_products("aigfs") if p.key == "hgt-wind-500")
    decoded = {spec.name: np.tile(np.linspace(5400, 6000, 8), (5, 1)) if spec.variable == "HGT"
               else np.full((5, 8), 10 if spec.variable == "UGRD" else 0)
               for spec in product.all_fields}
    result = contours._from_decoded("aigfs", product, RUN, 0, decoded)
    assert 0 < len(result["segments"]) <= 100
    assert result["wind"]["width"] == 32 and result["wind"]["height"] == 16


def test_sounding_requests_rejected_before_fetching(monkeypatch):
    from sharpmod.tools import model_extract as mx

    monkeypatch.setattr(mx, "extract", lambda *_args, **_kwargs: pytest.fail("AIGFS tried to fetch a sounding"))
    with pytest.raises(BridgeError, match="cannot produce a sounding") as error:
        soundings.open_model({**REQUEST, "latitude": 35, "longitude": -97})
    assert error.value.code == "INVALID_ARGUMENT" and not error.value.retryable


@pytest.mark.parametrize("available", [True, False])
def test_provider_status_checks_maps_without_a_sounding_probe(monkeypatch, available):
    from sharpmod.tools import model_extract as mx

    monkeypatch.setattr(mx, "probe", lambda *_args, **_kwargs: pytest.fail("AIGFS used a sounding probe"))

    def inventory(url, *, timeout):
        assert ".sfc.f000.grib2" in url and timeout == 2
        return (object(),) if available else ()

    monkeypatch.setattr(hf, "fetch_inventory", inventory)
    status = soundings.model_status(REQUEST)
    assert status["state"] == ("available" if available else "unavailable")
    assert status["provider"] == "NOAA/NCEP NOMADS"
