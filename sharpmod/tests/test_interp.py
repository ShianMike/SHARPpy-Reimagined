"""Unit tests for the SharpTab interpolation helpers (:mod:`sharpmod.sharptab.interp`).

These are worked-example tests for the layer/isotherm level-lookup helpers added
in task 3.1 and exercised by the layer-derived parameters:

* height-AGL lookup (:func:`interp.temp_at_hght_agl`,
  :func:`interp.pres_at_hght_agl`, :func:`interp.components_at_hght_agl`), and
* isotherm lookup (:func:`interp.pres_at_isotherm`,
  :func:`interp.hght_at_isotherm`).

Each case pins the interpolation convention against a hand-computed value:

* fields defined per height (temperature, wind) are **linear in height**, so a
  target halfway between two reported heights returns the arithmetic mean of the
  bracketing values;
* pressure is interpolated in **log-pressure** space, so a target halfway (in
  height) between two reported levels returns the *geometric* mean of the
  bracketing pressures.

Requirements covered: 1.4 (SFC-500 m layer top), 3.3 (interpolated 1 km AGL
temperature), 19.3 (interpolated -10/-30 degrees C isotherm levels), 21.3
(interpolated 6 km AGL layer top).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod.sharptab import interp
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _linear_profile(sfc_elev: float = 0.0) -> SoundingData:
    """A clean 7-level profile with round numbers for hand-computed checks.

    Surface height is ``sfc_elev`` m MSL, so AGL heights equal the MSL heights
    minus ``sfc_elev``. The temperature falls linearly 20 -> -40 degrees C from
    the surface to 6000 m AGL (10 degrees C per km), placing:

    * the -10 degrees C isotherm exactly on the 3000 m AGL / 700 hPa level, and
    * the -30 degrees C isotherm exactly on the 5000 m AGL / 500 hPa level.
    """
    hght_agl = np.array([0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0])
    pres = np.array([1000.0, 900.0, 800.0, 700.0, 600.0, 500.0, 400.0])
    tmpc = np.array([20.0, 10.0, 0.0, -10.0, -20.0, -30.0, -40.0])
    dwpc = tmpc - 10.0
    # Constant westerly-ish wind so component interpolation is easy to reason
    # about: wdir=270 (wind from the west) -> u = +wspd, v = 0.
    wdir = np.full(7, 270.0)
    wspd = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
    return SoundingData(
        pres=pres, hght=hght_agl + sfc_elev, tmpc=tmpc, dwpc=dwpc,
        wdir=wdir, wspd=wspd,
    )


# ---------------------------------------------------------------------------
# 1. Target that IS a reported level -> exact reported value
# ---------------------------------------------------------------------------

def test_temp_at_reported_level_returns_exact_value():
    """1 km AGL is a reported level: return its temperature exactly (Req 3.3)."""
    snd = _linear_profile()
    assert interp.temp_at_hght_agl(snd, 1000.0) == pytest.approx(10.0)


def test_pres_at_reported_level_returns_exact_value():
    """6 km AGL is a reported level: return its pressure exactly (Req 21.3)."""
    snd = _linear_profile()
    assert interp.pres_at_hght_agl(snd, 6000.0) == pytest.approx(400.0)


def test_isotherm_on_reported_level_returns_exact_level():
    """-10/-30 degrees C fall exactly on reported levels (Req 19.3)."""
    snd = _linear_profile()
    assert interp.pres_at_isotherm(snd, -10.0) == pytest.approx(700.0)
    assert interp.hght_at_isotherm(snd, -10.0) == pytest.approx(3000.0)
    assert interp.pres_at_isotherm(snd, -30.0) == pytest.approx(500.0)
    assert interp.hght_at_isotherm(snd, -30.0) == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# 2. Target BETWEEN reported levels -> bracketing interpolation
# ---------------------------------------------------------------------------

def test_temp_between_levels_is_linear_in_height():
    """500 m AGL lies halfway between 0 and 1000 m: mean temperature (Req 1.4)."""
    snd = _linear_profile()
    # T(0) = 20, T(1000) = 10 -> T(500) = 15.
    assert interp.temp_at_hght_agl(snd, 500.0) == pytest.approx(15.0)


def test_pres_between_levels_is_log_linear():
    """Pressure at 500 m AGL is the geometric mean of the bracketing levels."""
    snd = _linear_profile()
    # log-pressure linear in height => geometric mean of 1000 and 900 hPa.
    expected = math.sqrt(1000.0 * 900.0)
    assert interp.pres_at_hght_agl(snd, 500.0) == pytest.approx(expected)


def test_components_between_levels_linear_in_height():
    """Wind components at 500 m AGL are the mean of the bracketing levels."""
    snd = _linear_profile()
    # wdir=270 everywhere -> u = +wspd, v = 0. wspd(0)=10, wspd(1000)=20.
    u, v = interp.components_at_hght_agl(snd, 500.0)
    assert u == pytest.approx(15.0)
    assert v == pytest.approx(0.0, abs=1e-9)


def test_isotherm_between_levels_interpolates_pres_and_hght():
    """-25 degrees C lies halfway between the -20 and -30 degrees C levels (Req 19.3)."""
    snd = _linear_profile()
    # height linear -> 4500 m; pressure log-linear -> geometric mean(600, 500).
    assert interp.hght_at_isotherm(snd, -25.0) == pytest.approx(4500.0)
    assert interp.pres_at_isotherm(snd, -25.0) == pytest.approx(
        math.sqrt(600.0 * 500.0))


def test_height_agl_respects_surface_elevation():
    """AGL targets convert through the surface height, not absolute MSL."""
    snd = _linear_profile(sfc_elev=1000.0)
    # Surface at 1000 m MSL; 500 m AGL == 1500 m MSL -> mean of 20 and 10.
    assert interp.temp_at_hght_agl(snd, 500.0) == pytest.approx(15.0)
    # A reported level 1 km AGL == 2000 m MSL still returns its exact value.
    assert interp.temp_at_hght_agl(snd, 1000.0) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 3. Height-AGL lookup at the documented layer tops (500 m / 1 km / 6 km)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "h_agl, expected_t",
    [(500.0, 15.0), (1000.0, 10.0), (6000.0, -40.0)],
)
def test_temp_at_documented_layer_tops(h_agl, expected_t):
    """Temperatures at the 500 m / 1 km / 6 km AGL layer tops (Reqs 1.4, 3.3, 21.3)."""
    snd = _linear_profile()
    assert interp.temp_at_hght_agl(snd, h_agl) == pytest.approx(expected_t)


def test_pres_at_documented_layer_tops():
    """Pressures at the 500 m / 1 km / 6 km AGL layer tops."""
    snd = _linear_profile()
    assert interp.pres_at_hght_agl(snd, 500.0) == pytest.approx(
        math.sqrt(1000.0 * 900.0))
    assert interp.pres_at_hght_agl(snd, 1000.0) == pytest.approx(900.0)
    assert interp.pres_at_hght_agl(snd, 6000.0) == pytest.approx(400.0)


def test_height_agl_above_profile_top_is_missing():
    """A target above the profile top yields MISSING, not an exception (Reqs 1.4/21.3)."""
    snd = _linear_profile()  # top is 6000 m AGL
    assert is_missing(interp.temp_at_hght_agl(snd, 7000.0))
    assert is_missing(interp.pres_at_hght_agl(snd, 7000.0))
    u, v = interp.components_at_hght_agl(snd, 7000.0)
    assert is_missing(u) and is_missing(v)


def test_height_agl_below_surface_is_missing():
    """A negative AGL target sits below the surface and yields MISSING."""
    snd = _linear_profile()
    assert is_missing(interp.temp_at_hght_agl(snd, -100.0))


# ---------------------------------------------------------------------------
# 4. Isotherm lookup, including isotherms never crossed
# ---------------------------------------------------------------------------

def test_isotherm_never_crossed_colder_than_top_is_missing():
    """An isotherm colder than the profile top is never crossed -> MISSING (Req 19.3/4)."""
    snd = _linear_profile()  # top temperature is -40 degrees C
    assert is_missing(interp.pres_at_isotherm(snd, -50.0))
    assert is_missing(interp.hght_at_isotherm(snd, -50.0))


def test_isotherm_never_crossed_warmer_than_surface_is_missing():
    """An isotherm warmer than the surface is never crossed -> MISSING."""
    snd = _linear_profile()  # surface temperature is 20 degrees C
    assert is_missing(interp.pres_at_isotherm(snd, 30.0))
    assert is_missing(interp.hght_at_isotherm(snd, 30.0))


# ---------------------------------------------------------------------------
# 5. Degenerate / short profiles -> MISSING, never an exception
# ---------------------------------------------------------------------------

def test_single_level_profile_is_missing_everywhere():
    """A one-level profile has no bracket for any lookup -> MISSING (no raise)."""
    snd = SoundingData(
        pres=np.array([1000.0]), hght=np.array([0.0]), tmpc=np.array([20.0]),
        dwpc=np.array([10.0]), wdir=np.array([270.0]), wspd=np.array([10.0]),
    )
    assert is_missing(interp.temp_at_hght_agl(snd, 500.0))
    assert is_missing(interp.pres_at_hght_agl(snd, 500.0))
    assert is_missing(interp.pres_at_isotherm(snd, -10.0))
    assert is_missing(interp.hght_at_isotherm(snd, -10.0))
    u, v = interp.components_at_hght_agl(snd, 500.0)
    assert is_missing(u) and is_missing(v)


def test_fewer_than_two_usable_levels_after_masking_is_missing():
    """Masking a field down to a single usable level yields MISSING for that field."""
    pres = np.array([1000.0, 900.0, 800.0])
    hght = np.array([0.0, 1000.0, 2000.0])
    tmpc = ma.array([20.0, 10.0, 0.0], mask=[False, True, True])
    dwpc = np.array([10.0, 5.0, -5.0])
    wdir = np.full(3, 270.0)
    wspd = np.array([10.0, 20.0, 30.0])
    snd = SoundingData(pres=pres, hght=hght, tmpc=tmpc, dwpc=dwpc,
                       wdir=wdir, wspd=wspd)
    # Only one usable temperature level -> temperature lookup is MISSING ...
    assert is_missing(interp.temp_at_hght_agl(snd, 500.0))
    assert is_missing(interp.pres_at_isotherm(snd, -5.0))
    # ... while pressure (still fully valid) interpolates fine.
    assert not is_missing(interp.pres_at_hght_agl(snd, 500.0))


def test_degenerate_lookups_do_not_raise():
    """Every degenerate lookup returns MISSING rather than raising."""
    snd = SoundingData(
        pres=np.array([1000.0]), hght=np.array([0.0]), tmpc=np.array([20.0]),
        dwpc=np.array([10.0]), wdir=np.array([270.0]), wspd=np.array([10.0]),
    )
    # Should not raise for any of these calls.
    interp.temp_at_hght_agl(snd, 1000.0)
    interp.dwpt_at_hght_agl(snd, 1000.0)
    interp.pres_at_hght_agl(snd, 1000.0)
    interp.pres_at_isotherm(snd, -30.0)
    interp.hght_at_isotherm(snd, -30.0)
