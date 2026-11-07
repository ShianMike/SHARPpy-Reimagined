"""Regression tests for point-extractor NSTP surface-vorticity metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from sharpmod.io import decoder as decoder_mod
from sharpmod.tools import era5_extract as era5
from sharpmod.tools import ifs_extract as ifs
from sharpmod.tools import model_extract
from sharpmod.tests.era5_synth import make_era5_dataset


def _add_vo(ds, surface_value):
    """Return ``ds`` with pressure-level relative vorticity in s^-1."""
    dims = ds["t"].dims
    levels = np.asarray(ds["isobaricInhPa"].values, dtype=float)
    vo = np.full(ds["t"].shape, 2.0e-5, dtype=float)
    vo[:, int(np.nanargmax(levels)), :, :] = surface_value
    return ds.assign(vo=(dims, vo))


def test_ifs_extract_writes_surface_relative_vorticity_to_npz(tmp_path):
    """IFS ``vo`` reaches the archive, sidecar, decoder, and profile metadata."""
    levels = [850.0, 1000.0, 700.0]
    when = datetime(2026, 7, 5, 9, tzinfo=timezone.utc)
    ds = make_era5_dataset(
        lats=[13.0, 13.25],
        lons=[145.5, 145.75],
        levels=levels,
        times=[when],
        seed=12,
    )
    ds = _add_vo(ds, surface_value=8.0e-5)

    out_path = tmp_path / "ifs_point.npz"
    ifs.extract(
        13.1, 145.7, when, str(out_path), dataset=ds,
        run_time=when, loc="Guam")

    with np.load(out_path, allow_pickle=True) as npz:
        value = float(np.asarray(
            npz["surface_relative_vorticity"]).reshape(-1)[0])
        assert value == pytest.approx(8.0e-5)
        assert bool(npz["observed"]) is False

    with open(out_path.with_suffix(".json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta["surface_relative_vorticity"] == pytest.approx(8.0e-5)
    assert meta["observed"] is False

    prof_collection, _ = decoder_mod.load_npz(str(out_path))
    assert prof_collection.getMeta("surface_relative_vorticity") == pytest.approx(
        8.0e-5)
    assert prof_collection.getMeta("observed") is False
    prof = next(iter(prof_collection._profs.values()))[0]
    assert prof.surface_relative_vorticity == pytest.approx(8.0e-5)


def test_hrrr_model_extract_writes_relative_vorticity_and_forecast_metadata(tmp_path):
    """The supported HRRR route subtracts Coriolis at the selected grid point."""
    levels = np.array([850.0, 1000.0, 700.0], dtype=float)
    lat_grid = np.array([[35.0, 35.0], [36.0, 36.0]], dtype=float)
    lon_grid = np.array([[-100.0, -99.0], [-100.0, -99.0]], dtype=float)
    shape = (levels.size, lat_grid.shape[0], lat_grid.shape[1])

    rel_by_level = np.array([3.0e-5, 8.5e-5, 1.0e-5], dtype=float)
    absv = np.empty(shape, dtype=float)
    coriolis = 2.0 * era5.EARTH_ROTATION_RATE * np.sin(np.radians(lat_grid))
    for ilev, rel_vort in enumerate(rel_by_level):
        absv[ilev] = rel_vort + coriolis

    ds = xr.Dataset(
        data_vars={
            "t": (("isobaricInhPa", "y", "x"), np.full(shape, 290.0)),
            "gh": (("isobaricInhPa", "y", "x"), np.broadcast_to(
                np.array([1500.0, 150.0, 3000.0])[:, None, None], shape)),
            "r": (("isobaricInhPa", "y", "x"), np.full(shape, 60.0)),
            "u": (("isobaricInhPa", "y", "x"), np.full(shape, 5.0)),
            "v": (("isobaricInhPa", "y", "x"), np.full(shape, 2.0)),
            "w": (("isobaricInhPa", "y", "x"), np.zeros(shape)),
            "absv": (("isobaricInhPa", "y", "x"), absv),
            "sp": (("y", "x"), np.full(lat_grid.shape, 100500.0)),
            "orog": (("y", "x"), np.full(lat_grid.shape, 100.0)),
            "t2m": (("y", "x"), np.full(lat_grid.shape, 292.0)),
            "d2m": (("y", "x"), np.full(lat_grid.shape, 284.0)),
            "u10": (("y", "x"), np.full(lat_grid.shape, 5.0)),
            "v10": (("y", "x"), np.full(lat_grid.shape, 2.0)),
        },
        coords={
            "isobaricInhPa": ("isobaricInhPa", levels),
            "latitude": (("y", "x"), lat_grid),
            "longitude": (("y", "x"), lon_grid),
        },
    )

    output = tmp_path / "hrrr.npz"
    model_extract.extract(
        "hrrr", 36.05, -99.05, out_path=output, dataset=ds,
        run_time=datetime(2026, 7, 5, tzinfo=timezone.utc), fxx=1,
    )
    with np.load(output, allow_pickle=False) as sounding:
        assert float(sounding["lat"]) == pytest.approx(36.0)
        assert float(sounding["lon"]) == pytest.approx(-99.0)
        assert sounding["pres"][:2] == pytest.approx([1005.0, 1000.0])
        assert float(sounding["surface_relative_vorticity"]) == pytest.approx(8.5e-5)
        assert bool(sounding["observed"]) is False
    metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["observed"] is False
    assert metadata["surface_relative_vorticity"] == pytest.approx(8.5e-5)


def test_surface_vorticity_falls_back_to_wind_grid_gradient():
    """Six-variable u/v grids can still supply near-surface relative vorticity."""
    levels = np.array([1000.0, 850.0], dtype=float)
    lats = np.array([10.0, 11.0, 12.0], dtype=float)
    lons = np.array([100.0, 101.0, 102.0], dtype=float)
    when = datetime(2026, 7, 5, 9, tzinfo=timezone.utc)
    ds = make_era5_dataset(lats, lons, levels, [when], seed=13)

    target_vort = 1.0e-4
    dx = era5._east_west_distance_m(11.0, 100.0, 102.0)
    v_surface = np.array([0.0, 0.5 * target_vort * dx, target_vort * dx])
    u = np.zeros(ds["u"].shape, dtype=float)
    v = np.zeros(ds["v"].shape, dtype=float)
    v[:, 0, :, :] = v_surface[None, None, :]
    ds = ds.assign(u=(ds["u"].dims, u), v=(ds["v"].dims, v))

    cols, _ = era5._build_columns(ds.isel(time=0), (1, 1), latitude=11.0)

    assert cols["surface_relative_vorticity"] == pytest.approx(
        target_vort, rel=1.0e-6)


def test_surface_vorticity_windows_curvilinear_coordinates_with_wind():
    """Windowed wind gradients retain matching 2-D latitude/longitude grids."""
    levels = np.array([1000.0, 850.0], dtype=float)
    when = datetime(2026, 7, 5, 9, tzinfo=timezone.utc)
    ds = make_era5_dataset(
        np.arange(5, dtype=float),
        np.arange(5, dtype=float),
        levels,
        [when],
        seed=14,
    ).rename({"latitude": "y", "longitude": "x"})

    yy, xx = np.meshgrid(
        np.arange(5, dtype=float),
        np.arange(5, dtype=float),
        indexing="ij",
    )
    lat_grid = 10.0 + 0.5 * yy + 0.02 * xx
    lon_grid = 100.0 + 0.03 * yy + 0.5 * xx
    ds = ds.assign_coords(
        latitude=(("y", "x"), lat_grid),
        longitude=(("y", "x"), lon_grid),
    )

    target_vort = 8.0e-5
    iy = ix = 2
    dx = era5._east_west_distance_m(
        lat_grid[iy, ix], lon_grid[iy, ix - 1], lon_grid[iy, ix + 1])
    v_surface = np.zeros(5, dtype=float)
    v_surface[ix + 1] = target_vort * dx
    u = np.zeros(ds["u"].shape, dtype=float)
    v = np.zeros(ds["v"].shape, dtype=float)
    v[:, 0, :, :] = v_surface[None, None, :]
    ds = ds.assign(u=(ds["u"].dims, u), v=(ds["v"].dims, v))

    cols, _ = era5._build_columns(
        ds.isel(time=0), (iy, ix), latitude=float(lat_grid[iy, ix]))

    assert cols["surface_relative_vorticity"] == pytest.approx(
        target_vort, rel=1.0e-6)
