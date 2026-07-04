"""Synthetic ERA5 dataset builder shared by the ERA5_Extractor tests.

Builds a small, physically-plausible in-memory :class:`xarray.Dataset` shaped
like the ERA5 pressure-level product (dims ``time, isobaricInhPa, latitude,
longitude``) so the extractor's :func:`sharpmod.tools.era5_extract.extract` can
run through ``dataset=`` without touching the network / Herbie.

The variable names (``t``, ``z``, ``r``, ``u``, ``v``) match the cfgrib
conventions the extractor recognises, and the values are chosen so every
per-level field resolves to a finite (non-missing) number.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import xarray as xr

# Standard gravity used to convert height (m) -> geopotential (m^2 s^-2).
G0 = 9.80665


def make_era5_dataset(lats, lons, levels, times, seed=0):
    """Return a synthetic ERA5-like pressure-level ``xarray.Dataset``.

    Parameters
    ----------
    lats, lons : sequence of float
        1-D latitude / longitude coordinate vectors (degrees).
    levels : sequence of float
        Pressure levels in hPa (any order).
    times : sequence of datetime
        Analysis times (tz-aware or naive UTC).
    seed : int
        Seed for the (deterministic) pseudo-random wind/moisture fields.
    """
    rng = np.random.default_rng(seed)

    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    levels = np.asarray(levels, dtype=float)
    times64 = np.array(
        [np.datetime64(_naive_utc(t), "ns") for t in times],
        dtype="datetime64[ns]")

    nt, nl, ny, nx = times64.size, levels.size, lats.size, lons.size
    shape = (nt, nl, ny, nx)

    # Temperature (K): decreases with height (i.e. with decreasing pressure).
    # Standard-ish: warmer at high pressure, colder aloft, plus a small spread.
    lvl_temp = 220.0 + 0.06 * levels  # ~280 K at 1000 hPa, ~226 K at 100 hPa
    t = np.empty(shape, dtype=float)
    for it in range(nt):
        for il in range(nl):
            t[it, il] = lvl_temp[il] + rng.uniform(-1.0, 1.0, size=(ny, nx))

    # Geopotential height (m) via the hypsometric-ish log-pressure relation,
    # then stored as geopotential z = height * g0 (what ERA5 stores).
    height_m = 8000.0 * np.log(1000.0 / np.clip(levels, 1.0, None))
    z = np.empty(shape, dtype=float)
    for il in range(nl):
        z[:, il, :, :] = height_m[il] * G0

    # Relative humidity (%) in a comfortable, always-valid band.
    r = rng.uniform(10.0, 90.0, size=shape)

    # Zonal / meridional wind (m/s).
    u = rng.uniform(-40.0, 40.0, size=shape)
    v = rng.uniform(-40.0, 40.0, size=shape)

    dims = ("time", "isobaricInhPa", "latitude", "longitude")
    ds = xr.Dataset(
        data_vars={
            "t": (dims, t),
            "z": (dims, z),
            "r": (dims, r),
            "u": (dims, u),
            "v": (dims, v),
        },
        coords={
            "time": ("time", times64),
            "isobaricInhPa": ("isobaricInhPa", levels),
            "latitude": ("latitude", lats),
            "longitude": ("longitude", lons),
        },
    )
    return ds


def _naive_utc(t):
    """Return a tz-naive UTC :class:`datetime` for datetime64-friendly coords."""
    if isinstance(t, np.datetime64):
        return t
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def default_grid():
    """A small deterministic (lats, lons, levels, times) tuple for examples."""
    lats = np.array([30.0, 35.0, 40.0, 45.0], dtype=float)
    lons = np.array([260.0, 265.0, 270.0, 275.0], dtype=float)  # 0..360 frame
    levels = np.array([1000.0, 850.0, 700.0, 500.0, 300.0, 200.0], dtype=float)
    times = [
        datetime(2020, 6, 1, 0, tzinfo=timezone.utc),
        datetime(2020, 6, 1, 6, tzinfo=timezone.utc),
        datetime(2020, 6, 1, 12, tzinfo=timezone.utc),
    ]
    return lats, lons, levels, times
