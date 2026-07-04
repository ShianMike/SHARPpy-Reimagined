"""Tests for :func:`sharpmod.sharptab.params.lapse_rate` (SFC-1 km, Req 3)."""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
import pytest
from hypothesis import given

from sharpmod.sharptab import params
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles


def _sounding(hght_agl, tmpc, sfc_elev=0.0, mask_tmpc=None, mask_hght=None):
    """Build a minimal SoundingData from AGL heights and temperatures."""
    n = len(hght_agl)
    hght = np.asarray(hght_agl, dtype=float) + sfc_elev
    pres = 1000.0 * np.exp(-np.asarray(hght_agl, dtype=float) / 8000.0)
    tmpc = ma.asarray(np.asarray(tmpc, dtype=float))
    hght = ma.asarray(hght)
    if mask_tmpc is not None:
        tmpc = ma.masked_array(tmpc, mask=mask_tmpc)
    if mask_hght is not None:
        hght = ma.masked_array(hght, mask=mask_hght)
    dwpc = tmpc - 5.0
    wdir = np.full(n, 270.0)
    wspd = np.full(n, 20.0)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_sfc_1km_lapse_rate_reported_level():
    # 1 km AGL is a reported level: T_sfc=20, T_1km=13 => 7 C/km.
    prof = _sounding([0.0, 500.0, 1000.0, 2000.0], [20.0, 16.5, 13.0, 6.0])
    lr = params.lapse_rate(prof, 0, 1000, agl=True)
    assert not is_missing(lr)
    assert lr == pytest.approx(7.0, abs=1e-6)


def test_sfc_1km_lapse_rate_interpolated():
    # 1 km AGL falls between 500 m and 1500 m; linear T => T_1km = 10.0.
    # levels: 0 m -> 20, 500 -> 15, 1500 -> 5  => at 1000 m, 10.0. LR = 10.
    prof = _sounding([0.0, 500.0, 1500.0], [20.0, 15.0, 5.0])
    lr = params.lapse_rate(prof, 0, 1000, agl=True)
    assert lr == pytest.approx(10.0, abs=1e-6)


def test_sfc_1km_lapse_rate_nonzero_sfc_elevation():
    # Surface elevated 1500 m; AGL conversion must still resolve 1 km AGL.
    prof = _sounding([0.0, 1000.0, 2000.0], [10.0, 4.0, -2.0], sfc_elev=1500.0)
    lr = params.lapse_rate(prof, 0, 1000, agl=True)
    assert lr == pytest.approx(6.0, abs=1e-6)


def test_profile_below_1km_returns_missing():
    # Highest valid level (800 m AGL) is below 1 km AGL -> MISSING (Req 3.4).
    prof = _sounding([0.0, 400.0, 800.0], [20.0, 17.0, 14.0])
    assert is_missing(params.lapse_rate(prof, 0, 1000, agl=True))


def test_masked_surface_temp_returns_missing():
    # Surface temperature masked -> MISSING (Req 3.5).
    prof = _sounding([0.0, 500.0, 1000.0, 2000.0], [20.0, 16.5, 13.0, 6.0],
                     mask_tmpc=[True, False, False, False])
    assert is_missing(params.lapse_rate(prof, 0, 1000, agl=True))


def test_masked_surface_height_returns_missing():
    # Surface height masked -> MISSING (Req 3.5).
    prof = _sounding([0.0, 500.0, 1000.0, 2000.0], [20.0, 16.5, 13.0, 6.0],
                     mask_hght=[True, False, False, False])
    assert is_missing(params.lapse_rate(prof, 0, 1000, agl=True))


def test_equal_bounds_returns_missing():
    prof = _sounding([0.0, 1000.0, 2000.0], [20.0, 13.0, 6.0])
    assert is_missing(params.lapse_rate(prof, 500, 500, agl=True))


@given(profiles(shallow_top="1km"))
def test_shallow_profiles_missing(prof):
    # Profiles whose top is below 1 km AGL must yield MISSING.
    assert is_missing(params.lapse_rate(prof, 0, 1000, agl=True))
