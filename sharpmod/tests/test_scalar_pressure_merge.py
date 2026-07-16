"""Regression tests for scalar pressure coordinates in split GRIB groups."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from sharpmod.tools import era5_extract as era5
from sharpmod.tools import ifs_extract as ifs
from sharpmod.tools import model_extract


xr = pytest.importorskip("xarray")


def _split_pressure_groups():
    levels = np.array([1000.0, 850.0, 700.0])
    latitudes = np.array([34.0, 35.0, 36.0])
    longitudes = np.array([262.0, 263.0, 264.0])
    shape = (levels.size, latitudes.size, longitudes.size)
    primary = xr.Dataset(
        {"t": (
            ("isobaricInhPa", "latitude", "longitude"),
            np.full(shape, 280.0),
        )},
        coords={
            "isobaricInhPa": levels,
            "latitude": latitudes,
            "longitude": longitudes,
        },
    )
    omega = xr.Dataset(
        {"w": (
            ("latitude", "longitude"),
            np.full((latitudes.size, longitudes.size), -0.25),
        )},
        coords={
            "isobaricInhPa": 850.0,
            "latitude": latitudes,
            "longitude": longitudes,
        },
    )
    return primary, omega


def _assert_omega_aligned_to_scalar_pressure(dataset):
    assert dataset["w"].dims == (
        "isobaricInhPa", "latitude", "longitude")
    assert float(dataset["w"].sel(
        isobaricInhPa=850.0).isel(latitude=1, longitude=1)) == -0.25
    other_levels = dataset["w"].sel(isobaricInhPa=[1000.0, 700.0])
    assert np.isnan(other_levels.values).all()

    columns, count = era5._build_columns(
        dataset, (1, 1), latitude=35.0)
    assert count == 3
    np.testing.assert_array_equal(
        columns["pres"], np.array([1000.0, 850.0, 700.0]))
    np.testing.assert_array_equal(
        columns["omeg"], np.array([era5.MISSING, -0.25, era5.MISSING]))


def test_full_cfgrib_merge_preserves_scalar_field_pressure():
    merged = era5._merge_datasets(_split_pressure_groups())
    try:
        _assert_omega_aligned_to_scalar_pressure(merged)
    finally:
        merged.close()


def test_ifs_merge_preserves_scalar_field_pressure():
    sources = _split_pressure_groups()
    merged = ifs._merge_datasets(sources)
    try:
        _assert_omega_aligned_to_scalar_pressure(merged)
    finally:
        merged.close()
        for source in sources:
            source.close()


def test_compact_model_merge_preserves_scalar_field_pressure():
    sources = _split_pressure_groups()
    merged = model_extract._merge_point_datasets(
        sources,
        35.0,
        -97.0,
        datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    try:
        _assert_omega_aligned_to_scalar_pressure(merged)
    finally:
        merged.close()
        for source in sources:
            source.close()
