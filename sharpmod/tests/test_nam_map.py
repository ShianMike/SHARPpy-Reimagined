"""Original NAM fields must use their own records, grid and forecast cadence."""

import base64
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import contours, fields
from sharpmod.bridge.errors import BridgeError
from sharpmod.models.lambert_grid import NAM_GRID
from sharpmod.providers import hrrr_field as hf, hrrr_products as hp

RUN = datetime(2026, 10, 27, tzinfo=timezone.utc)
REQUEST = {"model": "nam", "run_time": "2026-10-27T00:00:00Z", "forecast_hour": 6}


def test_nam_only_offers_supported_nest_parameters_with_native_record_addresses():
    products = {product.key: product for product in hp.available_products("nam")}
    nest = {product.key for product in hp.available_products("nam-3km-conus")}
    assert products.keys() == nest - {"uh-0-3km", "uh-2-5km-1h", "uh-2-5km-3h"}
    assert len(products) == 34
    catalog = fields.catalog({"model": "nam"})
    assert catalog["field_model"] == "nam"
    assert {product["key"] for product in catalog["products"]} == products.keys()
    assert [catalog["max_forecast_hours"][h] for h in (0, 6, 12, 18)] == [84] * 4
    assert catalog["field_bounds"] == fields._bounds(NAM_GRID.bounds())
    for product in products.values():
        assert product.palette is hp.PRODUCTS[product.key].palette
        for spec in product.all_fields:
            expected = "prs" if re.fullmatch(r"\d+ mb", spec.level) \
                or spec.variable in {"VUCSH", "VVCSH"} else "sfc"
            assert spec.source == expected, spec
    assert hf.grib_url(RUN, 6, "sfc", "nam").endswith("nam.t00z.awip1206.tm00.grib2")
    assert hf.grib_url(RUN, 6, "prs", "nam").endswith("nam.t00z.awphys06.tm00.grib2")


def test_nam_map_limits_and_unavailable_parameters_are_rejected():
    assert fields._request({**REQUEST, "product": "refc", "forecast_hour": 84})[-1] == 84
    for change in ({"forecast_hour": 1}, {"forecast_hour": 85},
                   {"run_time": "2026-10-27T01:00:00Z"},
                   {"product": "uh-0-3km"}, {"product": "uh-2-5km-1h"}, {"product": "uh-2-5km-3h"}):
        with pytest.raises(BridgeError):
            fields._request({**REQUEST, "product": "refc", **change})


def test_decoder_accepts_the_nam_grid_and_refuses_hrrr_geometry():
    size = int(np.prod(NAM_GRID.shape))
    api = SimpleNamespace(
        codes_new_from_message=lambda _payload: 1,
        codes_get=lambda _handle, key: {
            "Ni": NAM_GRID.shape[1], "Nj": NAM_GRID.shape[0], "missingValue": 9999,
        }[key],
        codes_get_values=lambda _handle: np.full(size, 280.0),
        codes_release=lambda _handle: None,
    )
    assert hf.decode_message(b"GRIB", api, model="nam").shape == NAM_GRID.shape
    with pytest.raises(hf.HrrrFieldError, match="unexpected model grid"):
        hf.decode_message(b"GRIB", api)


def test_projection_cache_separates_nam_and_hrrr_and_places_the_correct_cell():
    bounds = (-98.0, -97.0, 35.0, 36.0)
    mapping = hf.index_map((2, 2), bounds, model="nam")
    assert mapping is hf.index_map((2, 2), bounds, model="nam")
    assert mapping is not hf.index_map((2, 2), bounds)
    for index, (lon, lat) in enumerate(((-97.75, 35.75), (-97.25, 35.75),
                                       (-97.75, 35.25), (-97.25, 35.25))):
        x, y = NAM_GRID.forward(lon, lat)
        col = round((x - NAM_GRID.x0) / NAM_GRID.spacing)
        row = round((y - NAM_GRID.y0) / NAM_GRID.spacing)
        assert mapping.inside[index]
        assert mapping.flat[index] == row * NAM_GRID.shape[1] + col
    first_lon, first_lat = NAM_GRID.inverse(NAM_GRID.x0, NAM_GRID.y0)
    assert first_lon == pytest.approx(-133.459)
    assert first_lat == pytest.approx(12.19)


def test_wind_barbs_rotate_using_nams_own_cone():
    product = next(p for p in hp.available_products("nam") if p.key == "hgt-wind-300")
    u = np.full(NAM_GRID.shape, 10, dtype=np.float32)
    v = np.zeros_like(u)
    indices = np.array([200 * NAM_GRID.shape[1] + col for col in (50, 300, 550)])
    mapping = hf._IndexMap(3, 1, hf.field_bounds("nam"), indices, np.ones(3, dtype=bool))
    result = contours.wind_grid(product, {"ugrd300": u, "vgrd300": v}, mapping, model="nam")
    packed = np.frombuffer(base64.b64decode(result["data_base64"]), dtype="<u2")
    for index, cell in zip(indices, packed, strict=True):
        lon, _lat = NAM_GRID.inverse(
            NAM_GRID.x0 + index % NAM_GRID.shape[1] * NAM_GRID.spacing,
            NAM_GRID.y0 + index // NAM_GRID.shape[1] * NAM_GRID.spacing,
        )
        assert int(cell & 511) == round((270 + (lon - NAM_GRID.lon_origin) * NAM_GRID._n) % 360)
        assert int(cell >> 9) == 4


def test_scalar_contours_and_winds_render_on_the_same_nam_extent(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(fields, "FRAME", (32, 16))
    calls = []

    def download(product, run, hour, **kwargs):
        calls.append(kwargs["model"])
        return {spec.name: np.full(NAM_GRID.shape, 10 if spec.variable == "UGRD"
                                 else 5000 if spec.variable == "HGT" else 0, dtype=np.float32)
                for spec in product.all_fields}

    monkeypatch.setattr(hf, "_download_fields", download)
    hf.clear_derived_cache()
    try:
        request = {**REQUEST, "product": "hgt-wind-500"}
        scalar = fields.grid(request)
        auxiliary = contours.contours(request)
        assert scalar["bounds"] == auxiliary["wind"]["bounds"] \
            == fields._bounds(NAM_GRID.bounds())
        assert (scalar["width"], scalar["height"]) == (32, 16)
        assert "awphys06" in scalar["source_url"] and "NAM" in scalar["attribution"]
        assert scalar["value_max"] == pytest.approx(10 * hp.MS_TO_KT)
        assert calls == ["nam"]
    finally:
        hf.clear_derived_cache()
