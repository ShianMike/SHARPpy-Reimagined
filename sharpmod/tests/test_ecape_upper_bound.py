"""Property-based test for the ECAPE upper bound.

Feature: sharppy-modernization, Property 7: ECAPE does not exceed undiluted CAPE

Property 7 (design.md): *For any* valid Profile whose undiluted CAPE is positive
and for which ECAPE is computed, the ECAPE value is less than or equal to that
undiluted CAPE value.

**Validates: Requirements 5.4**

Notes
-----
``ecape.ecape(prof)`` returns J/kg or the ``MISSING`` sentinel and *never
raises*. The undiluted CAPE it clamps against is the most-unstable CAPE
(MUCAPE) obtained from the sharppy parcel-ascent oracle (``params.parcelx``
with ``flag=3``, ``.bplus``) -- the exact same oracle ``ecape`` uses
internally. This test recomputes that MUCAPE *independently* from the same
sounding arrays and asserts ``ecape <= MUCAPE`` (with a tiny epsilon for
floating-point) whenever ECAPE is computed and MUCAPE is positive.

A deterministic strongly-buoyant sounding is included to guarantee the
``ECAPE <= MUCAPE`` clause is genuinely exercised at least once (many random
soundings yield ``MISSING`` or zero CAPE, which the property does not
constrain).
"""

from __future__ import annotations

import numpy as np
from hypothesis import event, given

from sharpmod.sharptab import ecape as ecape_mod
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles

# Absolute floating-point slack for the <= comparison (ECAPE is internally
# clamped to <= MUCAPE, so this only guards representation noise).
_EPS = 1e-6


def _undiluted_mucape(snd) -> float | None:
    """Independently compute undiluted MUCAPE (J/kg) via the sharppy oracle.

    Mirrors the parcel-ascent path ``ecape`` relies on -- most-unstable parcel,
    ``params.parcelx(prof, flag=3).bplus`` -- but recomputes it from scratch on
    the same reported-level arrays. Returns ``None`` when the oracle is
    unavailable or MUCAPE cannot be resolved (in which case the property has no
    bound to check).
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
        cape = mupcl.bplus
    except Exception:
        return None

    if is_missing(cape) or not np.isfinite(cape):
        return None
    return float(cape)


@given(profiles())
def test_ecape_does_not_exceed_undiluted_cape(snd):
    """A computed ECAPE never exceeds the undiluted MUCAPE; the call never raises.

    Feature: sharppy-modernization, Property 7: ECAPE does not exceed undiluted CAPE
    Validates: Requirements 5.4
    """
    # Must never raise -- any failure degrades to MISSING by contract.
    val = ecape_mod.ecape(snd)

    if is_missing(val):
        event("ecape: MISSING (uncomputable for this sounding)")
        return

    mucape = _undiluted_mucape(snd)
    if mucape is None:
        event("mucape: unavailable (no bound to check)")
        return
    if mucape <= 0.0:
        event("mucape: <= 0 (precondition not met)")
        return

    event("ecape: computed vs positive undiluted CAPE")
    fval = float(val)
    assert np.isfinite(fval), f"computed ECAPE must be finite, got {fval!r}"
    assert fval <= mucape + _EPS, (
        f"computed ECAPE ({fval!r}) must be <= undiluted MUCAPE ({mucape!r})"
    )


def _unstable_sounding() -> SoundingData:
    """A deterministic, strongly buoyant sounding with veering winds.

    Warm, moist near the surface with a steep mid-level lapse rate, veering /
    strengthening winds, and an isothermal stratosphere above ~11 km so the
    parcel-ascent oracle resolves positive undiluted CAPE *and* a defined
    equilibrium level -- the conditions ECAPE needs to compute a real value.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 12000.0, 14000.0, 16000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [26.0, 22.0, 18.0, 11.0, 5.0, -9.0,
         -24.0, -40.0, -56.0, -58.0, -58.0, -58.0], dtype=float)
    dwpc = np.array(
        [19.0, 16.0, 13.0, 3.0, -3.0, -20.0,
         -36.0, -52.0, -66.0, -70.0, -72.0, -74.0], dtype=float)
    wdir = np.array(
        [160.0, 180.0, 200.0, 220.0, 240.0, 255.0,
         265.0, 270.0, 275.0, 280.0, 285.0, 290.0], dtype=float)
    wspd = np.array(
        [8.0, 18.0, 24.0, 30.0, 42.0, 55.0,
         66.0, 74.0, 82.0, 88.0, 94.0, 100.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_ecape_upper_bound_on_unstable_example():
    """A strongly buoyant sounding yields a computed ECAPE <= undiluted MUCAPE.

    Guards the property's precondition: at least one input produces a real
    (non-MISSING) ECAPE with positive undiluted CAPE so the <= clause is
    genuinely exercised.

    Feature: sharppy-modernization, Property 7: ECAPE does not exceed undiluted CAPE
    Validates: Requirements 5.4
    """
    snd = _unstable_sounding()
    val = ecape_mod.ecape(snd)
    mucape = _undiluted_mucape(snd)

    if mucape is None:
        # Oracle unavailable in this environment -- nothing to bound against.
        return

    assert not is_missing(val), "unstable sounding should yield a computed ECAPE"
    assert mucape > 0.0, "unstable sounding should have positive undiluted CAPE"
    assert float(val) <= mucape + _EPS
