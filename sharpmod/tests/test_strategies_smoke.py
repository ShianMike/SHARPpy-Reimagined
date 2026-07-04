"""Trivial smoke tests for the shared :func:`profiles` strategy harness.

These verify the generator imports, produces physically plausible soundings,
honours its edge-case flags, and integrates with the SharpTab interpolation
helpers. They are intentionally lightweight -- the substantive property tests
live alongside the parameters they validate.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import given

from sharpmod.sharptab import interp
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SHALLOW_CEILINGS, profiles


@given(profiles())
def test_core_invariants_hold(snd):
    """Pressure decreases, height increases, T >= Td, winds are valid."""
    pres = snd.pres.compressed()
    hght = snd.hght.compressed()
    assert np.all(np.diff(pres) < 0), "pressure must strictly decrease"
    assert np.all(np.diff(hght) > 0), "height must strictly increase"

    # T >= Td at every level where both are present.
    both = ~ma.getmaskarray(snd.tmpc) & ~ma.getmaskarray(snd.dwpc)
    assert np.all(np.asarray(snd.tmpc)[both] >= np.asarray(snd.dwpc)[both])

    wdir = snd.wdir.compressed()
    wspd = snd.wspd.compressed()
    assert np.all((wdir >= 0.0) & (wdir < 360.0))
    assert np.all(wspd >= 0.0)


@given(profiles(shallow_top="500m"))
def test_shallow_top_stays_below_ceiling(snd):
    """A shallow-top profile does not reach the requested ceiling AGL."""
    top_agl = float(snd.hght[-1] - snd.hght[snd.sfc])
    assert top_agl < SHALLOW_CEILINGS["500m"]
    # interp to 500 m AGL is therefore out of range -> MISSING.
    assert is_missing(interp.temp_at_hght_agl(snd, 500.0))


@given(profiles(span_hgz=True))
def test_span_hgz_crosses_both_isotherms(snd):
    """A spanning profile reaches both the -10 and -30 degrees C isotherms."""
    assert not is_missing(interp.pres_at_isotherm(snd, -10.0))
    assert not is_missing(interp.pres_at_isotherm(snd, -30.0))


@given(profiles(span_hgz=False))
def test_no_span_hgz_never_reaches_minus10(snd):
    """A non-spanning profile never crosses the -10 degrees C isotherm."""
    assert is_missing(interp.pres_at_isotherm(snd, -10.0))


@given(profiles(masked_fields=["tmpc"]))
def test_masked_fields_are_masked_but_usable(snd):
    """Masking a field still leaves >= 2 valid levels for interpolation."""
    assert snd.tmpc.compressed().size >= 2


@given(profiles())
def test_usable_with_interp_helpers(snd):
    """A generated sounding drives the interp helpers without raising."""
    # A mid-column pressure always lies within range and returns a value.
    p_mid = float(np.sqrt(float(snd.pres[0]) * float(snd.pres[-1])))
    val = interp.temp(snd, p_mid)
    assert not is_missing(val)


@given(profiles())
def test_to_profile_kwargs_shapes(snd):
    """``to_profile_kwargs`` returns the six create_profile arrays."""
    kw = snd.to_profile_kwargs()
    for key in ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd"):
        assert kw[key].shape == (snd.nlevels,)
    assert kw["missing"] == -9999.0
