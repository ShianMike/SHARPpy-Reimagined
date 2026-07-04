"""Property test for SFC-500m layer kinematics (task 4.2).

Feature: sharppy-modernization, Property 1: SFC-500m kinematics agree with
reference.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.7 -- *for any* valid Profile that
extends through 500 m AGL with valid wind and height data, the computed
SFC-500m SRH, bulk shear, and pressure-weighted mean wind each match their
reference implementation within the documented tolerance:

* SRH   : max(1%, 1 m^2/s^2)
* shear : max(1%, 0.5 kt)
* mean wind (per component and vector magnitude of the difference): max(1%, 0.5 kt)

Oracle used
-----------
The **upstream SHARPpy** ``sharppy.sharptab`` library is used as the reference
implementation, exactly as the task prescribes. Despite the legacy decoders'
reliance on the removed ``imp`` shim / ``np.float`` alias, the ``sharptab``
computation modules (``profile``, ``winds``, ``interp``) *do* import and run on
Python 3.14, so they serve as a genuine independent oracle here.

For every generated sounding the reference SFC-500m quantities are computed by:

1. building a SHARPpy ``Profile`` from the same generated arrays via
   :meth:`SoundingData.to_profile_kwargs`;
2. taking the Bunkers right-mover storm motion from
   ``sharppy.sharptab.winds.non_parcel_bunkers_motion`` -- the *same* convention
   the SHARPpy Reimagined ``storm_motion`` helper falls back to (Requirement 1.5), so the
   SRH comparison is apples-to-apples;
3. computing SRH over 0-500 m AGL (``winds.helicity``), the SFC->500 m bulk
   shear magnitude (``winds.wind_shear``), and the pressure-weighted layer mean
   wind (``winds.mean_wind``) at the SFC and 500 m AGL pressures resolved with
   ``interp``.

If (and only if) the upstream ``sharptab`` modules cannot be imported on the
running interpreter, the test falls back to an independent hand-rolled reference
computed directly from the generated arrays (documented in
:func:`_handrolled_reference`).

The generator is the shared :func:`sharpmod.tests.strategies.profiles` with the
default full-depth profile (top 6-16 km AGL), which always extends past 500 m
AGL so the layer is well defined (Requirement 1.4).
"""

from __future__ import annotations

import warnings

import numpy as np
import numpy.ma as ma
import pytest
from hypothesis import given

from sharpmod.sharptab import winds as sm_winds
from sharpmod.sharptab import interp as sm_interp
from sharpmod.sharptab.constants import (
    MISSING,
    is_missing,
    PARAM_REGISTRY,
    RELATIVE_TOLERANCE,
)
from sharpmod.tests.strategies import profiles


# --------------------------------------------------------------------------- #
# Documented tolerances (design.md Data Models parameter table).
# --------------------------------------------------------------------------- #
SRH_RTOL = RELATIVE_TOLERANCE["srh500"]              # 1%
SRH_ATOL = PARAM_REGISTRY["srh500"].tolerance        # 1 m^2/s^2
SHEAR_RTOL = RELATIVE_TOLERANCE["shear_sfc_500m"]    # 1%
SHEAR_ATOL = PARAM_REGISTRY["shear_sfc_500m"].tolerance   # 0.5 kt
MW_RTOL = RELATIVE_TOLERANCE["mean_wind_sfc_500m"]   # 1%
MW_ATOL = PARAM_REGISTRY["mean_wind_sfc_500m"].tolerance  # 0.5 kt

TOP_AGL = 500.0


# --------------------------------------------------------------------------- #
# Reference oracle: upstream SHARPpy sharptab (with a hand-rolled fallback).
# --------------------------------------------------------------------------- #
try:
    from sharppy.sharptab import profile as _sp_profile
    from sharppy.sharptab import winds as _sp_winds
    from sharppy.sharptab import interp as _sp_interp

    _HAVE_SHARPPY = True
    _ORACLE = "sharppy.sharptab"
except Exception:  # pragma: no cover - exercised only on platforms w/o sharppy
    _HAVE_SHARPPY = False
    _ORACLE = "hand-rolled"


def _f(x):
    """Return ``x`` as a plain float, or ``None`` if it is missing/NaN."""
    if is_missing(x):
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(xf) else xf


def _sharppy_reference(data):
    """Reference SFC-500m (srh, shear, (mnu, mnv)) via upstream SHARPpy."""
    kw = data.to_profile_kwargs()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prof = _sp_profile.create_profile(profile="default", **kw)
        rstu, rstv, _lstu, _lstv = _sp_winds.non_parcel_bunkers_motion(prof)
        if is_missing(rstu) or is_missing(rstv):
            srh = None
        else:
            srh = _f(_sp_winds.helicity(prof, 0, TOP_AGL, stu=rstu, stv=rstv)[0])

        psfc = prof.pres[prof.sfc]
        p500 = _sp_interp.pres(prof, _sp_interp.to_msl(prof, TOP_AGL))
        shu, shv = _sp_winds.wind_shear(prof, psfc, p500)
        shear = None if is_missing(shu) else _f(np.hypot(shu, shv))
        mnu, mnv = _sp_winds.mean_wind(prof, psfc, p500)
        mw = (_f(mnu), _f(mnv))
    return srh, shear, mw


def _handrolled_reference(data):  # pragma: no cover - fallback path only
    """Independent hand-rolled SFC-500m reference from the generated arrays.

    * shear    : |V(500 m AGL) - V(sfc)| with components linearly interpolated
      in height.
    * mean wind: pressure-weighted mean of the wind components integrated at a
      1 hPa step over the SFC->500 m layer.
    * SRH      : discrete storm-relative streamwise-vorticity integral over the
      layer, sampled every 10 m AGL, using the shared Bunkers storm motion. This
      is a distinct numerical integration from the SHARPpy Reimagined reported-level
      implementation, so it is a genuine cross-check.
    """
    u_sfc, v_sfc = sm_interp.components_at_hght_agl(data, 0.0)
    u_top, v_top = sm_interp.components_at_hght_agl(data, TOP_AGL)
    shear = None if is_missing(u_sfc) or is_missing(u_top) \
        else _f(np.hypot(u_top - u_sfc, v_top - v_sfc))

    psfc = float(ma.asanyarray(data.pres)[0])
    ptop = sm_interp.pres_at_hght_agl(data, TOP_AGL)
    if is_missing(ptop):
        mw = (None, None)
    else:
        ps = np.arange(psfc, float(ptop) - 1.0, -1.0)
        us = np.array([sm_interp.components(data, p)[0] for p in ps], float)
        vs = np.array([sm_interp.components(data, p)[1] for p in ps], float)
        mw = (_f(np.average(us, weights=ps)), _f(np.average(vs, weights=ps)))

    rstu, rstv, _l1, _l2 = sm_winds.non_parcel_bunkers_motion(data)
    if is_missing(rstu):
        srh = None
    else:
        hs = np.arange(0.0, TOP_AGL + 10.0, 10.0)
        us = np.array([sm_interp.components_at_hght_agl(data, h)[0] for h in hs])
        vs = np.array([sm_interp.components_at_hght_agl(data, h)[1] for h in hs])
        sru = sm_winds.kts2ms(us - float(rstu))
        srv = sm_winds.kts2ms(vs - float(rstv))
        layers = (sru[1:] * srv[:-1]) - (sru[:-1] * srv[1:])
        srh = _f(np.nansum(layers))
    return srh, shear, mw


def _reference(data):
    return _sharppy_reference(data) if _HAVE_SHARPPY \
        else _handrolled_reference(data)


def _within(actual, expected, rtol, atol):
    """True when ``|actual - expected| <= max(rtol*|expected|, atol)``."""
    tol = max(rtol * abs(expected), atol)
    return abs(actual - expected) <= tol


# --------------------------------------------------------------------------- #
# Property 1
# --------------------------------------------------------------------------- #
@given(data=profiles())
def test_sfc_500m_kinematics_agree_with_reference(data):
    """Feature: sharppy-modernization, Property 1: SFC-500m kinematics agree
    with reference.

    Validates Requirements 1.1, 1.2, 1.3, 1.4, 1.7.
    """
    srh_sm, shear_sm, mw_sm, _srw_sm = sm_winds.sfc_500m_kinematics(data)
    srh_ref, shear_ref, mw_ref = _reference(data)

    # --- SRH (Requirements 1.1, 1.5, 1.7) --------------------------------- #
    srh_sm_f = _f(srh_sm)
    if srh_sm_f is not None and srh_ref is not None:
        assert _within(srh_sm_f, srh_ref, SRH_RTOL, SRH_ATOL), (
            f"SFC-500m SRH {srh_sm_f} m^2/s^2 disagrees with reference "
            f"{srh_ref} beyond tol=max({SRH_RTOL:.0%}, {SRH_ATOL} m^2/s^2) "
            f"[oracle={_ORACLE}]")

    # --- bulk shear (Requirements 1.2, 1.4, 1.7) -------------------------- #
    shear_sm_f = _f(shear_sm)
    if shear_sm_f is not None and shear_ref is not None:
        assert _within(shear_sm_f, shear_ref, SHEAR_RTOL, SHEAR_ATOL), (
            f"SFC-500m bulk shear {shear_sm_f} kt disagrees with reference "
            f"{shear_ref} beyond tol=max({SHEAR_RTOL:.0%}, {SHEAR_ATOL} kt) "
            f"[oracle={_ORACLE}]")

    # --- pressure-weighted mean wind (Requirements 1.3, 1.4, 1.7) --------- #
    if not is_missing(mw_sm) and mw_ref[0] is not None:
        u_sm, v_sm = float(mw_sm[0]), float(mw_sm[1])
        u_ref, v_ref = mw_ref
        # Per-component agreement.
        assert _within(u_sm, u_ref, MW_RTOL, MW_ATOL), (
            f"SFC-500m mean-wind u {u_sm} kt disagrees with reference "
            f"{u_ref} beyond tol=max({MW_RTOL:.0%}, {MW_ATOL} kt) "
            f"[oracle={_ORACLE}]")
        assert _within(v_sm, v_ref, MW_RTOL, MW_ATOL), (
            f"SFC-500m mean-wind v {v_sm} kt disagrees with reference "
            f"{v_ref} beyond tol=max({MW_RTOL:.0%}, {MW_ATOL} kt) "
            f"[oracle={_ORACLE}]")
        # Vector magnitude of the difference.
        vec_diff = float(np.hypot(u_sm - u_ref, v_sm - v_ref))
        ref_mag = float(np.hypot(u_ref, v_ref))
        assert vec_diff <= max(MW_RTOL * ref_mag, MW_ATOL), (
            f"SFC-500m mean-wind vector {(u_sm, v_sm)} kt disagrees with "
            f"reference {(u_ref, v_ref)} by {vec_diff} kt beyond "
            f"tol=max({MW_RTOL:.0%}, {MW_ATOL} kt) [oracle={_ORACLE}]")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
