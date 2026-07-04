"""Property-based test for zero-CAPE ECAPE.

Feature: sharppy-modernization, Property 8: Zero undiluted CAPE yields zero ECAPE

Property 8 (design.md): *For any* valid Profile whose undiluted CAPE is zero,
the computed ECAPE is zero.

**Validates: Requirements 5.5**

Notes
-----
``ecape.ecape(prof)`` derives its undiluted CAPE from a most-unstable parcel
ascent performed by the installed ``sharppy`` oracle (``params.parcelx(prof,
flag=3).bplus``). Property 8 is conditional on that undiluted CAPE being *zero*,
so this test computes the same oracle MUCAPE independently and uses Hypothesis'
``assume()`` to keep only the soundings where it is exactly ``0`` J/kg. For those
soundings the contract (Requirement 5.5) is strict: ``ecape`` must return exactly
``0.0`` -- not ``MISSING`` and not a positive value.

The :func:`profiles` harness is driven with ``zero_cape=True`` (a dry, stable
column biased toward zero surface-based CAPE) so a meaningful fraction of drawn
examples satisfy the ``MUCAPE == 0`` precondition. A deterministic, unambiguously
stable/dry sounding is also asserted directly so the ``== 0.0`` clause is always
exercised at least once regardless of what the random draws filter to.
"""

from __future__ import annotations

import numpy as np
from hypothesis import assume, event, given

from sharpmod.sharptab import ecape as ecape_mod
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles


def _oracle_mucape(snd: SoundingData):
    """Undiluted MUCAPE (J/kg) from the sharppy oracle, or ``None`` on failure.

    Mirrors the ascent ``ecape`` performs internally: a most-unstable parcel
    (``flag=3``) whose ``bplus`` is the undiluted CAPE. Returns ``None`` when the
    oracle is unavailable or cannot resolve the ascent, so the caller can skip
    the example rather than assert on an undefined precondition.
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return None

    kwargs = snd.to_profile_kwargs()
    try:
        prof = sp_profile.create_profile(profile="default", strictQC=False,
                                         **kwargs)
        mupcl = sp_params.parcelx(prof, flag=3)
        cape = mupcl.bplus
    except Exception:
        return None
    if cape is None or not np.isfinite(cape):
        return None
    return float(cape)


@given(profiles(zero_cape=True))
def test_zero_undiluted_cape_yields_zero_ecape(snd):
    """When the oracle undiluted CAPE is exactly zero, ECAPE is exactly 0.0.

    Feature: sharppy-modernization, Property 8: Zero undiluted CAPE yields zero ECAPE
    Validates: Requirements 5.5
    """
    mucape = _oracle_mucape(snd)
    # Precondition: only soundings whose undiluted CAPE is exactly zero.
    assume(mucape is not None)
    assume(mucape == 0.0)
    event("oracle MUCAPE == 0 (precondition satisfied)")

    val = ecape_mod.ecape(snd)

    # Requirement 5.5: zero undiluted CAPE -> exactly zero ECAPE (not MISSING).
    assert not is_missing(val), (
        "zero undiluted CAPE must yield a numeric ECAPE of 0.0, got MISSING")
    assert float(val) == 0.0, (
        f"zero undiluted CAPE must yield ECAPE == 0.0 J/kg, got {float(val)!r}")


def _stable_dry_sounding() -> SoundingData:
    """A deterministic, unambiguously stable and dry sounding (zero CAPE).

    A strong low-level inversion plus a warm, very dry column gives a
    most-unstable parcel no positive area, so the oracle resolves undiluted
    CAPE = 0. Winds veer/strengthen normally (irrelevant to CAPE but complete
    the profile).
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 13000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    # Surface-based inversion (T rises 0->1 km) then a stable (near-isothermal
    # aloft) lapse: a parcel lifted from any level is colder than its
    # environment, so there is no CAPE.
    tmpc = np.array(
        [10.0, 14.0, 16.0, 12.0, 8.0, 0.0,
         -8.0, -18.0, -30.0, -42.0], dtype=float)
    # Very dry throughout (large dewpoint depression) -> no latent buoyancy.
    dwpc = np.array(
        [-25.0, -24.0, -24.0, -28.0, -32.0, -40.0,
         -48.0, -58.0, -70.0, -82.0], dtype=float)
    wdir = np.array(
        [180.0, 200.0, 220.0, 240.0, 250.0, 260.0,
         270.0, 275.0, 280.0, 285.0], dtype=float)
    wspd = np.array(
        [5.0, 10.0, 15.0, 20.0, 28.0, 36.0,
         45.0, 52.0, 60.0, 68.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_zero_cape_example_yields_zero_ecape():
    """A deterministic stable/dry sounding (zero undiluted CAPE) gives ECAPE 0.0.

    Guarantees the ``== 0.0`` clause of Property 8 is exercised at least once,
    independent of what the random draws filter to.

    Feature: sharppy-modernization, Property 8: Zero undiluted CAPE yields zero ECAPE
    Validates: Requirements 5.5
    """
    snd = _stable_dry_sounding()
    mucape = _oracle_mucape(snd)
    assert mucape is not None, "oracle must resolve the deterministic ascent"
    assert mucape == 0.0, (
        f"deterministic sounding must have zero undiluted CAPE, got {mucape!r}")

    val = ecape_mod.ecape(snd)
    assert not is_missing(val), "zero undiluted CAPE must yield 0.0, got MISSING"
    assert float(val) == 0.0, f"expected ECAPE == 0.0 J/kg, got {float(val)!r}"
