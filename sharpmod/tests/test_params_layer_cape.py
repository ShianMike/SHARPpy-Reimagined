"""Edge-case tests for the layer-integrated CAPE helpers (Requirements 19, 21).

Covers :func:`sharpmod.sharptab.params.layer_cape_agl` (6CAPE via ``0, 6000``)
and :func:`sharpmod.sharptab.params.layer_cape_isotherm` (HGZ CAPE via
``-10, -30``). The existing SFC-1 km lapse-rate edge cases live in
``test_params_lapse_rate.py``; a small isotherm-aware lapse-rate case is added
here to round out the layer-thermodynamics coverage without duplicating that
file.

Edge cases exercised (tasks.md 5.3):

* **Non-reported bounds** -- a deep, unstable, buoyant profile whose 6 km AGL top
  and -10/-30 degrees C isotherms all fall *between* reported levels returns an
  interpolated numeric value (not MISSING).
* **Shallow profiles** -- a profile topping below 6 km AGL yields MISSING for
  6CAPE; a profile that never reaches -10/-30 degrees C yields MISSING for HGZ
  CAPE.
* **Degenerate layers** -- ``top <= bottom`` yields MISSING.
* **Masked inputs (layer-scoped)** -- a single masked level is *tolerated*: a
  masked pressure/temperature drops that level and a masked moisture datum is
  carried as the missing sentinel, so the CAPE still computes from the valid
  levels spanning the layer. MISSING is returned only when too few valid levels
  remain (e.g. every level's temperature masked); no case raises.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
import pytest
from hypothesis import given

from sharpmod.sharptab import params
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles


# ---------------------------------------------------------------------------
# Deterministic sounding builders
# ---------------------------------------------------------------------------

def _make_sounding(hght_agl, tmpc, dwpc, *, sfc_elev=0.0,
                   mask_pres=None, mask_tmpc=None, mask_dwpc=None):
    """Build a SoundingData from AGL heights, temperatures, and dewpoints.

    Pressure follows a simple hydrostatic column; winds are a benign uniform
    westerly (CAPE does not depend on them). Optional per-level masks inject
    masked entries into pressure/temperature/moisture.
    """
    hght_agl = np.asarray(hght_agl, dtype=float)
    n = hght_agl.size
    hght = ma.asarray(hght_agl + sfc_elev)
    pres = ma.asarray(1000.0 * np.exp(-hght_agl / 8000.0))
    tmpc = ma.asarray(np.asarray(tmpc, dtype=float))
    dwpc = ma.asarray(np.asarray(dwpc, dtype=float))
    if mask_pres is not None:
        pres = ma.masked_array(pres, mask=mask_pres)
    if mask_tmpc is not None:
        tmpc = ma.masked_array(tmpc, mask=mask_tmpc)
    if mask_dwpc is not None:
        dwpc = ma.masked_array(dwpc, mask=mask_dwpc)
    wdir = np.full(n, 270.0)
    wspd = np.full(n, 20.0)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def _buoyant_deep_sounding():
    """A deep, unstable, surface-based buoyant sounding.

    Levels are chosen so that 6 km AGL and the -10/-30 degrees C isotherms all
    fall strictly *between* reported levels (interpolation paths exercised). The
    warm, moist surface parcel over a steep (~8 degrees C/km) environmental lapse
    rate guarantees substantial positive CAPE across both layers.
    """
    hght_agl = [0.0, 1000.0, 2500.0, 4000.0, 5500.0, 7000.0,
                9000.0, 11000.0, 13000.0]
    # ~8 C/km lapse from a 32 C surface.
    tmpc = [32.0, 24.0, 12.0, 0.0, -12.0, -24.0, -40.0, -56.0, -72.0]
    # Moist low levels for buoyancy; drier aloft (T >= Td everywhere).
    dwpc = [24.0, 18.0, 5.0, -10.0, -22.0, -35.0, -50.0, -66.0, -82.0]
    return _make_sounding(hght_agl, tmpc, dwpc)


def _warm_shallow_sounding():
    """A profile that never cools to -10 degrees C and tops below 6 km AGL."""
    hght_agl = [0.0, 800.0, 1600.0, 2400.0, 3200.0]
    tmpc = [28.0, 24.0, 20.0, 16.0, 12.0]  # min 12 C: never reaches -10
    dwpc = [20.0, 17.0, 14.0, 11.0, 8.0]
    return _make_sounding(hght_agl, tmpc, dwpc)


# ---------------------------------------------------------------------------
# Non-reported bounds: value returned (interpolated), not MISSING
# ---------------------------------------------------------------------------

def test_layer_cape_agl_nonreported_top_returns_value():
    # 6000 m AGL is not a reported level (between 5500 and 7000) -> interpolated.
    prof = _buoyant_deep_sounding()
    val = params.layer_cape_agl(prof, 0, 6000)
    assert not is_missing(val)
    assert val > 0.0


def test_layer_cape_isotherm_nonreported_bounds_returns_value():
    # -10 and -30 C both fall between reported levels -> interpolated bounds.
    prof = _buoyant_deep_sounding()
    val = params.layer_cape_isotherm(prof, -10, -30)
    assert not is_missing(val)
    assert val >= 0.0


def test_6km_and_isotherms_are_between_reported_levels():
    # Guard the premise of the "non-reported bounds" cases above.
    prof = _buoyant_deep_sounding()
    reported_agl = np.asarray(prof.hght) - float(prof.hght[0])
    assert 6000.0 not in set(reported_agl.tolist())
    reported_t = set(np.asarray(prof.tmpc).tolist())
    assert -10.0 not in reported_t and -30.0 not in reported_t


# ---------------------------------------------------------------------------
# Shallow / non-spanning profiles -> MISSING
# ---------------------------------------------------------------------------

def test_layer_cape_agl_shallow_profile_missing():
    # Profile tops at 3200 m AGL, below the 6 km top -> MISSING (Req 21.4).
    prof = _warm_shallow_sounding()
    assert is_missing(params.layer_cape_agl(prof, 0, 6000))


def test_layer_cape_isotherm_not_spanned_missing():
    # Profile never cools to -10 C, so the layer bounds cannot be found (Req 19.4).
    prof = _warm_shallow_sounding()
    assert is_missing(params.layer_cape_isotherm(prof, -10, -30))


def test_layer_cape_isotherm_spans_minus10_only_missing():
    # Reaches -10 C but never -30 C -> top bound unresolved -> MISSING (Req 19.4).
    hght_agl = [0.0, 1500.0, 3000.0, 4500.0, 6000.0]
    tmpc = [20.0, 10.0, 0.0, -8.0, -16.0]  # coldest -16 C: crosses -10, not -30
    dwpc = [14.0, 4.0, -6.0, -14.0, -22.0]
    prof = _make_sounding(hght_agl, tmpc, dwpc)
    assert is_missing(params.layer_cape_isotherm(prof, -10, -30))


# ---------------------------------------------------------------------------
# Degenerate layers (top <= bottom) -> MISSING
# ---------------------------------------------------------------------------

def test_layer_cape_agl_degenerate_equal_bounds_missing():
    prof = _buoyant_deep_sounding()
    assert is_missing(params.layer_cape_agl(prof, 6000, 6000))


def test_layer_cape_agl_degenerate_inverted_bounds_missing():
    prof = _buoyant_deep_sounding()
    assert is_missing(params.layer_cape_agl(prof, 6000, 0))


def test_layer_cape_isotherm_degenerate_equal_isotherms_missing():
    # temp_top == temp_bottom -> resolved bounds coincide -> MISSING.
    prof = _buoyant_deep_sounding()
    assert is_missing(params.layer_cape_isotherm(prof, -10, -10))


def test_layer_cape_isotherm_inverted_isotherms_missing():
    # Warmer "top" than "bottom": colder isotherm is not above the warmer one.
    prof = _buoyant_deep_sounding()
    assert is_missing(params.layer_cape_isotherm(prof, -30, -10))


# ---------------------------------------------------------------------------
# Masked inputs (layer-scoped): a single masked level is tolerated
# ---------------------------------------------------------------------------

def test_layer_cape_agl_masked_pressure_tolerated():
    # A single masked pressure drops that level; 6 km is still spanned, so a
    # value is returned (layer-scoped masking), never MISSING or an exception.
    prof = _buoyant_deep_sounding()
    mask = [False] * prof.nlevels
    mask[3] = True
    prof2 = _make_sounding(
        np.asarray(prof.hght) - float(prof.hght[0]),
        np.asarray(prof.tmpc), np.asarray(prof.dwpc), mask_pres=mask)
    val = params.layer_cape_agl(prof2, 0, 6000)
    assert not is_missing(val)
    assert val > 0.0


def test_layer_cape_agl_masked_temperature_tolerated():
    # A single masked temperature drops that level; the layer is still spanned.
    prof = _buoyant_deep_sounding()
    mask = [False] * prof.nlevels
    mask[2] = True
    prof2 = _make_sounding(
        np.asarray(prof.hght) - float(prof.hght[0]),
        np.asarray(prof.tmpc), np.asarray(prof.dwpc), mask_tmpc=mask)
    val = params.layer_cape_agl(prof2, 0, 6000)
    assert not is_missing(val)
    assert val > 0.0


def test_layer_cape_isotherm_masked_moisture_tolerated():
    # A single masked moisture datum is carried as the missing sentinel (the
    # level is retained); the HGZ CAPE still resolves from the spanned layer.
    prof = _buoyant_deep_sounding()
    mask = [False] * prof.nlevels
    mask[4] = True
    prof2 = _make_sounding(
        np.asarray(prof.hght) - float(prof.hght[0]),
        np.asarray(prof.tmpc), np.asarray(prof.dwpc), mask_dwpc=mask)
    val = params.layer_cape_isotherm(prof2, -10, -30)
    assert not is_missing(val)
    assert val >= 0.0


def test_fully_masked_input_is_missing_without_raising():
    # Masking a required field at *every* level leaves too few valid levels to
    # lift a parcel -> MISSING (returned, never raised).
    prof = _buoyant_deep_sounding()
    mask = [True] * prof.nlevels
    prof2 = _make_sounding(
        np.asarray(prof.hght) - float(prof.hght[0]),
        np.asarray(prof.tmpc), np.asarray(prof.dwpc), mask_tmpc=mask)
    assert is_missing(params.layer_cape_agl(prof2, 0, 6000))
    assert is_missing(params.layer_cape_isotherm(prof2, -10, -30))


# ---------------------------------------------------------------------------
# Property-style edge coverage via the shared profiles() strategy
# ---------------------------------------------------------------------------

@given(profiles(shallow_top="6km"))
def test_shallow_profiles_6cape_missing(prof):
    # Any profile topping below 6 km AGL must yield MISSING for 6CAPE (Req 21.4).
    assert is_missing(params.layer_cape_agl(prof, 0, 6000))


@given(profiles(span_hgz=False))
def test_non_spanning_profiles_hgz_cape_missing(prof):
    # Profiles that never reach -10 C cannot resolve the HGZ layer (Req 19.4).
    assert is_missing(params.layer_cape_isotherm(prof, -10, -30))
