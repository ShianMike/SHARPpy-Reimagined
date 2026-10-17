"""Theta-e thermodynamics, wind-only map geometry, and CONUS model outlines."""

import base64
from dataclasses import replace

import numpy as np
import pytest

from sharpmod.bridge import contours, fields
from sharpmod.models import lambert_grid
from sharpmod.providers import hrrr_field as hf, hrrr_products as hp
from sharpmod.tools import model_extract


MODELS = ("hrrr", "nam-3km-conus", "rrfs-a", *(f"refs-m{i}" for i in range(1, 6)))


def test_thetae_matches_bolton_reference_and_increases_with_moisture_and_height():
    result = hp._thetae_2m({
        "pres": np.array([[85000, 100000, 100000, 90000]], dtype=np.float32),
        "tmp2m": np.full((1, 4), 293.15, dtype=np.float32),
        "dpt2m": np.array([[291.15, 273.15, 288.15, 288.15]], dtype=np.float32),
    })

    # MetPy's documented Bolton example: 850 hPa, 20 C, 18 C -> 353.898874 K.
    assert result[0, 0] == pytest.approx(353.898874, abs=0.001)
    assert result.shape == (1, 4)
    assert np.isfinite(result).all()
    assert 293.15 < result[0, 1] < result[0, 2] < result[0, 3]


def test_thetae_masks_missing_and_invalid_pressure_without_a_sea_level_fallback():
    pressure = np.array([[100000, np.nan, 0, -1000, np.inf, -np.inf]])
    result = hp._thetae_2m({
        "pres": pressure,
        "tmp2m": np.full(pressure.shape, 293.15),
        "dpt2m": np.full(pressure.shape, 288.15),
    })

    assert np.isfinite(result[0, 0])
    assert np.isnan(result[0, 1:]).all()
    with pytest.raises(KeyError, match="pres"):
        hp._thetae_2m({"tmp2m": np.array([293.15]), "dpt2m": np.array([288.15])})


@pytest.mark.parametrize("model", MODELS)
def test_thetae_model_ingredients_and_catalogue_keep_kelvin_and_wind(model):
    product = next(p for p in hp.available_products(model) if p.key == "thetae-2m")
    assert len(product.all_fields) == 5
    assert {spec.name: (spec.variable, spec.level) for spec in product.all_fields} == {
        "tmp2m": ("TMP", "2 m above ground"),
        "dpt2m": ("DPT", "2 m above ground"),
        "pres": ("PRES", "surface"),
        "u10": ("UGRD", "10 m above ground"),
        "v10": ("VGRD", "10 m above ground"),
    }
    assert all(spec.source == "sfc" and spec.forecast == "" for spec in product.all_fields)
    assert product.wind and product.contour is None
    assert product.palette is hp.PALETTE_THETAE
    assert product.palette.units == "K" and product.palette.display == "fixed"
    assert product.palette.stepped

    wire = next(p for p in fields.catalog({"model": model})["products"]
                if p["key"] == "thetae-2m")
    assert wire["wind"] and wire["contour"] is None
    assert wire["palette"]["units"] == "K"
    assert wire["palette"]["display"] == "fixed"


@pytest.mark.parametrize("model", MODELS)
def test_thetae_wind_only_endpoint_encodes_winds_without_contour_segments(model, monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path / "maps"))
    grid = replace(lambert_grid.HRRR_GRID, shape=(2, 3), x0=0, y0=0)
    monkeypatch.setattr(lambert_grid, "HRRR_GRID", grid)
    monkeypatch.setattr(hf, "HRRR_SHAPE", grid.shape)
    monkeypatch.setattr(fields, "FRAME", (6, 4))
    mapping = hf._IndexMap(3, 2, hf.COVERAGE_BOUNDS, np.arange(6),
                           np.array([True, True, True, True, True, False]))
    decoded = {
        "u10": np.array([[10, 0, np.nan], [3, 5, 20]], dtype=np.float32),
        "v10": np.array([[0, 0, 0], [4, 0, 0]], dtype=np.float32),
    }
    calls = []

    def download(product, run, hour, **kwargs):
        calls.append((product.key, run.isoformat(), hour, kwargs["model"]))
        return decoded

    def index_map(size, bounds):
        assert size == (3, 2) and bounds == hf.COVERAGE_BOUNDS
        return mapping

    monkeypatch.setattr(hf, "_download_fields", download)
    monkeypatch.setattr(hf, "index_map", index_map)
    result = contours.contours({
        "model": model, "product": "thetae-2m",
        "run_time": "2026-10-16T00:00:00Z", "forecast_hour": 1,
    })

    assert calls == [("thetae-2m", "2026-10-16T00:00:00+00:00", 1, model)]
    assert result["product"] == "thetae-2m" and result["forecast_hour"] == 1
    assert result["segments"] == []
    assert (result["unit"], result["interval"]) == ("kt", 5.0)
    wind = result["wind"]
    assert (wind["width"], wind["height"]) == (3, 2)
    assert wind["bounds"] == fields._bounds(hf.COVERAGE_BOUNDS)
    cells = np.frombuffer(base64.b64decode(wind["data_base64"]), dtype="<u2")
    assert cells.shape == (6,)
    assert (cells[[0, 1, 3, 4]] >> 9).tolist() == [4, 0, 2, 2]
    assert int(cells[0] & 511) == 270
    assert cells[[2, 5]].tolist() == [65535, 65535]


@pytest.mark.parametrize("model", MODELS)
def test_conus_model_outline_is_the_closed_hrrr_grid_perimeter(model):
    config = model_extract.get_config(model)
    expected = lambert_grid.HRRR_GRID.outline()
    assert config.domain_outline == expected
    assert config.domain_bounds == lambert_grid.HRRR_GRID.bounds()
    assert expected[0] == expected[-1]
    assert len(expected) > 5
    assert np.isfinite(expected).all()
    south = np.array(expected[:len(expected) // 4])[:, 1]
    assert np.ptp(south) > 3  # A latitude/longitude rectangle loses the curved edge.
