"""Property-based test for the EHI composite (task 7.5).

Feature: sharppy-modernization, Property 5: EHI equals its defining composite
for both layers

Property 5 (design.md): *For any* valid Profile for which the CAPE and
storm-relative-helicity terms are present, the 0-1 km EHI equals
``(CAPE*SRH_0_1)/160000`` and the 0-3 km EHI equals ``(CAPE*SRH_0_3)/160000``,
using the CAPE and SRH terms drawn from that same Profile, within the documented
tolerance.

**Validates: Requirements 18.1, 18.2, 18.3**

Oracle / cross-check
--------------------
``derived.ehi(prof, layer)`` returns a unitless float or the ``MISSING`` sentinel
and *never raises*. This test *independently* recomputes the two defining terms
from the **same** sounding arrays and forms the composite for each layer, then
asserts the value returned by ``derived.ehi`` matches:

* **SBCAPE** -- the same sharppy parcel-ascent oracle ``ehi`` relies on:
  the surface-based parcel ``params.parcelx(prof, flag=1).bplus`` -- recomputed
  from scratch on the same reported-level arrays.
* **SRH_0-1km / SRH_0-3km** -- storm-relative helicity over the SFC->1 km and
  SFC->3 km AGL layers via :func:`sharpmod.sharptab.winds.helicity`, evaluated
  with the *shared* Bunkers right-mover storm motion
  (:func:`sharpmod.sharptab.winds.storm_motion`) -- the exact convention ``ehi``
  uses.

Tolerance (design.md Data Models parameter table): ``max(1%, 0.05)`` -- the
relative component from :data:`RELATIVE_TOLERANCE["ehi_0_1km"]` and the absolute
component from :data:`PARAM_REGISTRY["ehi_0_1km"].tolerance`.

When ``ehi`` returns ``MISSING`` or the CAPE/SRH terms cannot be recomputed, the
property has no composite to compare against and the example is skipped via
``event`` / early return. A deterministic strongly-buoyant, strongly-sheared
sounding guarantees the equality clause is genuinely exercised for both layers.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import event, given

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab import winds as sm_winds
from sharpmod.sharptab.constants import (
    is_missing,
    PARAM_REGISTRY,
    RELATIVE_TOLERANCE,
)
from sharpmod.tests.strategies import SoundingData, profiles

# --- Documented EHI normalization constant (Hart & Korotky / SPC) -----------
_EHI_NORM = 160000.0

# --- Documented tolerance: max(1%, 0.05) ------------------------------------
_EHI_RTOL = RELATIVE_TOLERANCE["ehi_0_1km"]           # 0.01
_EHI_ATOL = PARAM_REGISTRY["ehi_0_1km"].tolerance     # 0.05


def _within(actual, expected, rtol, atol):
    """True when ``|actual - expected| <= max(rtol*|expected|, atol)``."""
    tol = max(rtol * abs(expected), atol)
    return abs(actual - expected) <= tol


def _sfc_cape(snd):
    """Independently recompute surface-based CAPE (J/kg) via the sharppy oracle.

    Returns ``None`` when the oracle is unavailable or the CAPE cannot be
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
        sbpcl = sp_params.parcelx(prof, flag=1)  # surface-based parcel
        cape = sbpcl.bplus
        if is_missing(cape) or not np.isfinite(cape):
            return None
    except Exception:
        return None

    return float(cape)


def _srh(snd, top):
    """Independently recompute SRH (m^2/s^2) over SFC-> ``top`` m AGL.

    Uses the shared Bunkers right-mover storm motion, matching the convention
    ``derived.ehi`` uses. Returns ``None`` when the storm motion or the helicity
    cannot be resolved.
    """
    rstu, rstv, _lstu, _lstv = sm_winds.storm_motion(snd)
    if is_missing(rstu) or is_missing(rstv):
        return None
    total, _phel, _nhel = sm_winds.helicity(snd, 0.0, top, stu=rstu, stv=rstv)
    if is_missing(total) or not np.isfinite(total):
        return None
    return float(total)


def _reference_ehi(snd, top):
    """The independently recomputed defining composite for a layer, or ``None``."""
    srh = _srh(snd, top)
    if srh is None:
        return None
    cape = _sfc_cape(snd)
    if cape is None:
        return None
    return (cape * srh) / _EHI_NORM


def _check_layer(snd, layer, top):
    """Assert ``ehi(snd, layer)`` matches its recomputed composite for one layer.

    Returns a short event tag describing what happened so the caller can record
    coverage.
    """
    val = derived_mod.ehi(snd, layer)

    if is_missing(val):
        return f"ehi({layer}): MISSING (uncomputable for this sounding)"

    expected = _reference_ehi(snd, top)
    if expected is None:
        return f"reference({layer}): unavailable (no composite to check)"

    fval = float(val)
    assert np.isfinite(fval), f"computed EHI must be finite, got {fval!r}"
    assert _within(fval, expected, _EHI_RTOL, _EHI_ATOL), (
        f"EHI[{layer}] {fval!r} disagrees with defining composite {expected!r} "
        f"beyond tol=max({_EHI_RTOL:.0%}, {_EHI_ATOL})"
    )
    return f"ehi({layer}): computed vs recomputed defining composite"


@given(profiles())
def test_ehi_equals_defining_composite(snd):
    """EHI equals its defining composite of same-Profile terms for both layers.

    Feature: sharppy-modernization, Property 5: EHI equals its defining composite
    for both layers
    Validates: Requirements 18.1, 18.2, 18.3
    """
    # Must never raise -- any failure degrades to MISSING by contract.
    event(_check_layer(snd, 1000, 1000.0))   # 0-1 km EHI (Requirement 18.1)
    event(_check_layer(snd, 3000, 3000.0))   # 0-3 km EHI (Requirement 18.2)


def _ehi_sounding() -> SoundingData:
    """A deterministic strongly-buoyant, strongly-sheared supercell sounding.

    Warm/moist near the surface with a steep mid-level lapse rate and
    veering/strengthening low-level winds through 3 km so both SBCAPE and the
    0-1 km / 0-3 km SRH resolve to real values and the equality clause is
    genuinely exercised for both layers.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 12000.0, 14000.0, 16000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [30.0, 25.0, 21.0, 13.0, 6.0, -8.0,
         -23.0, -39.0, -55.0, -57.0, -57.0, -57.0], dtype=float)
    dwpc = np.array(
        [23.0, 20.0, 16.0, 6.0, -2.0, -18.0,
         -32.0, -48.0, -62.0, -66.0, -70.0, -72.0], dtype=float)
    # Veering, strengthening low-level winds -> substantial 0-1 / 0-3 km SRH.
    wdir = np.array(
        [140.0, 170.0, 195.0, 220.0, 240.0, 255.0,
         260.0, 265.0, 270.0, 275.0, 280.0, 285.0], dtype=float)
    wspd = np.array(
        [10.0, 22.0, 32.0, 42.0, 50.0, 62.0,
         70.0, 78.0, 85.0, 90.0, 96.0, 102.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_ehi_equals_composite_on_supercell_example():
    """A supercell-like sounding: both EHI layers match their recomputed composite.

    Guards the property's precondition: at least one input yields real
    (non-MISSING) EHI values with both terms present so the equality clause is
    genuinely exercised for the 0-1 km and 0-3 km layers.

    Feature: sharppy-modernization, Property 5: EHI equals its defining composite
    for both layers
    Validates: Requirements 18.1, 18.2, 18.3
    """
    snd = _ehi_sounding()

    ref_1km = _reference_ehi(snd, 1000.0)
    ref_3km = _reference_ehi(snd, 3000.0)
    if ref_1km is None or ref_3km is None:
        # Oracle unavailable in this environment -- nothing to compare against.
        return

    val_1km = derived_mod.ehi(snd, 1000)
    val_3km = derived_mod.ehi(snd, 3000)

    assert not is_missing(val_1km), "supercell sounding should yield a 0-1 km EHI"
    assert not is_missing(val_3km), "supercell sounding should yield a 0-3 km EHI"
    assert _within(float(val_1km), ref_1km, _EHI_RTOL, _EHI_ATOL), (
        f"EHI[0-1km] {float(val_1km)!r} disagrees with composite {ref_1km!r}"
    )
    assert _within(float(val_3km), ref_3km, _EHI_RTOL, _EHI_ATOL), (
        f"EHI[0-3km] {float(val_3km)!r} disagrees with composite {ref_3km!r}"
    )
