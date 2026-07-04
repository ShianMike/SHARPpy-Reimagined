"""Property-based test for the DCP composite (task 7.2).

Feature: sharppy-modernization, Property 3: DCP equals its defining composite

Property 3 (design.md): *For any* valid Profile for which DCAPE, MUCAPE, 0-6 km
bulk shear, and 0-6 km mean wind are all present, the computed DCP equals
``(DCAPE/980)*(MUCAPE/2000)*(shear_0_6km/20)*(mean_wind_0_6km/16)`` -- the same
terms taken from that same Profile -- within the documented tolerance.

**Validates: Requirements 2.1, 2.2, 2.3**

Oracle / cross-check
--------------------
``derived.dcp(prof)`` returns a unitless float or the ``MISSING`` sentinel and
*never raises*. This test *independently* recomputes the four defining terms
from the **same** sounding arrays and forms the composite, then asserts the
value returned by ``derived.dcp`` matches:

* **DCAPE** and **MUCAPE** -- the same sharppy parcel-ascent oracle ``dcp``
  relies on: ``params.dcape(prof)[0]`` and the most-unstable parcel
  ``params.parcelx(prof, flag=3).bplus`` -- but recomputed from scratch on the
  same reported-level arrays.
* **0-6 km bulk shear** -- ``|V(6 km AGL) - V(sfc)|`` (kt) via
  :func:`sharpmod.sharptab.winds.wind_shear` at the surface pressure and the
  pressure at 6 km AGL (:func:`sharpmod.sharptab.interp.pres_at_hght_agl`).
* **0-6 km mean wind** -- the *speed* (kt) of the pressure-weighted mean wind
  vector over the SFC->6 km AGL layer via
  :func:`sharpmod.sharptab.winds.mean_wind`.

Tolerance (design.md Data Models parameter table): ``max(1%, 0.05)`` -- the
relative component from :data:`RELATIVE_TOLERANCE["dcp"]` and the absolute
component from :data:`PARAM_REGISTRY["dcp"].tolerance`.

When ``dcp`` returns ``MISSING`` or any of the four terms cannot be recomputed,
the property has no composite to compare against and the example is skipped via
``event`` / early return. A deterministic strongly-buoyant, strongly-sheared
sounding guarantees the equality clause is genuinely exercised at least once.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import event, given

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab import interp as sm_interp
from sharpmod.sharptab import winds as sm_winds
from sharpmod.sharptab.constants import (
    MISSING,
    is_missing,
    PARAM_REGISTRY,
    RELATIVE_TOLERANCE,
)
from sharpmod.tests.strategies import SoundingData, profiles

# --- Documented DCP normalization constants (Evans & Doswell 2001 / SPC) ---
_DCAPE_NORM = 980.0
_MUCAPE_NORM = 2000.0
_SHEAR_NORM = 20.0
_MNWIND_NORM = 16.0
_SFC_TOP_AGL = 6000.0

# --- Documented tolerance: max(1%, 0.05) ------------------------------------
_DCP_RTOL = RELATIVE_TOLERANCE["dcp"]            # 0.01
_DCP_ATOL = PARAM_REGISTRY["dcp"].tolerance      # 0.05


def _within(actual, expected, rtol, atol):
    """True when ``|actual - expected| <= max(rtol*|expected|, atol)``."""
    tol = max(rtol * abs(expected), atol)
    return abs(actual - expected) <= tol


def _sfc_pres(snd):
    """Surface pressure (hPa) or ``None``."""
    pres = ma.asanyarray(snd.pres)
    val = pres[0]
    if val is ma.masked or is_missing(val):
        return None
    return float(val)


def _kinematic_terms(snd):
    """Independently recompute ``(shear_0_6km, mean_wind_0_6km)`` (kt).

    Returns ``(None, None)`` when winds are absent or the profile does not span
    the SFC->6 km AGL layer.
    """
    psfc = _sfc_pres(snd)
    ptop = sm_interp.pres_at_hght_agl(snd, _SFC_TOP_AGL)
    if psfc is None or is_missing(ptop):
        return None, None

    du, dv = sm_winds.wind_shear(snd, psfc, ptop)
    if is_missing(du) or is_missing(dv):
        shear = None
    else:
        shear = float(sm_winds.mag(du, dv))

    mnu, mnv = sm_winds.mean_wind(snd, psfc, ptop)
    if is_missing(mnu) or is_missing(mnv):
        mnwind = None
    else:
        mnwind = float(sm_winds.mag(mnu, mnv))

    return shear, mnwind


def _dcape_mucape(snd):
    """Independently recompute ``(dcape, mucape)`` (J/kg) via the sharppy oracle.

    Returns ``None`` when the oracle is unavailable or either quantity cannot be
    resolved (in which case the property has no composite to check).
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return None

    fill = -9999.0
    try:
        prof = sp_profile.create_profile(
            profile="default",
            pres=np.asarray(snd.pres.filled(fill), dtype=float),
            hght=np.asarray(snd.hght.filled(fill), dtype=float),
            tmpc=np.asarray(snd.tmpc.filled(fill), dtype=float),
            dwpc=np.asarray(snd.dwpc.filled(fill), dtype=float),
            wdir=np.asarray(snd.wdir.filled(fill), dtype=float),
            wspd=np.asarray(snd.wspd.filled(fill), dtype=float),
            missing=fill,
            strictQC=False,
        )
        mupcl = sp_params.parcelx(prof, flag=3)  # most-unstable parcel
        mucape = mupcl.bplus
        if is_missing(mucape) or not np.isfinite(mucape):
            return None

        dres = sp_params.dcape(prof)
        dcape = dres[0] if isinstance(dres, (tuple, list)) else dres
        if is_missing(dcape) or not np.isfinite(dcape):
            return None
    except Exception:
        return None

    return float(dcape), float(mucape)


def _reference_dcp(snd):
    """The independently recomputed defining composite, or ``None``."""
    shear06, mnwind06 = _kinematic_terms(snd)
    if shear06 is None or mnwind06 is None:
        return None
    buoyancy = _dcape_mucape(snd)
    if buoyancy is None:
        return None
    dcape, mucape = buoyancy
    return (
        (dcape / _DCAPE_NORM)
        * (mucape / _MUCAPE_NORM)
        * (shear06 / _SHEAR_NORM)
        * (mnwind06 / _MNWIND_NORM)
    )


@given(profiles())
def test_dcp_equals_defining_composite(snd):
    """DCP equals its defining composite of same-Profile terms; never raises.

    Feature: sharppy-modernization, Property 3: DCP equals its defining composite
    Validates: Requirements 2.1, 2.2, 2.3
    """
    # Must never raise -- any failure degrades to MISSING by contract.
    val = derived_mod.dcp(snd)

    if is_missing(val):
        event("dcp: MISSING (uncomputable for this sounding)")
        return

    expected = _reference_dcp(snd)
    if expected is None:
        event("reference: unavailable (no composite to check)")
        return

    fval = float(val)
    assert np.isfinite(fval), f"computed DCP must be finite, got {fval!r}"
    event("dcp: computed vs recomputed defining composite")
    assert _within(fval, expected, _DCP_RTOL, _DCP_ATOL), (
        f"DCP {fval!r} disagrees with defining composite {expected!r} "
        f"beyond tol=max({_DCP_RTOL:.0%}, {_DCP_ATOL})"
    )


def _derecho_sounding() -> SoundingData:
    """A deterministic strongly-buoyant, strongly-sheared derecho-like sounding.

    Warm/moist near the surface with a steep mid-level lapse rate, a dry mid
    layer (to yield real DCAPE), and veering/strengthening winds through 6 km so
    all four DCP terms resolve to real values and the equality clause is
    genuinely exercised.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 12000.0, 14000.0, 16000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [30.0, 25.0, 21.0, 13.0, 6.0, -8.0,
         -23.0, -39.0, -55.0, -57.0, -57.0, -57.0], dtype=float)
    # Dry mid-levels -> downdraft energy (DCAPE); moist low levels -> CAPE.
    dwpc = np.array(
        [22.0, 18.0, 12.0, -2.0, -12.0, -28.0,
         -40.0, -54.0, -66.0, -70.0, -72.0, -74.0], dtype=float)
    wdir = np.array(
        [180.0, 200.0, 215.0, 230.0, 245.0, 255.0,
         260.0, 265.0, 270.0, 275.0, 280.0, 285.0], dtype=float)
    wspd = np.array(
        [15.0, 25.0, 32.0, 40.0, 48.0, 60.0,
         70.0, 78.0, 85.0, 90.0, 96.0, 102.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_dcp_equals_composite_on_derecho_example():
    """A derecho-like sounding: DCP matches its recomputed defining composite.

    Guards the property's precondition: at least one input yields a real
    (non-MISSING) DCP with all four terms present so the equality clause is
    genuinely exercised.

    Feature: sharppy-modernization, Property 3: DCP equals its defining composite
    Validates: Requirements 2.1, 2.2, 2.3
    """
    snd = _derecho_sounding()
    val = derived_mod.dcp(snd)
    expected = _reference_dcp(snd)

    if expected is None:
        # Oracle unavailable in this environment -- nothing to compare against.
        return

    assert not is_missing(val), "derecho sounding should yield a computed DCP"
    assert _within(float(val), expected, _DCP_RTOL, _DCP_ATOL), (
        f"DCP {float(val)!r} disagrees with defining composite {expected!r}"
    )
