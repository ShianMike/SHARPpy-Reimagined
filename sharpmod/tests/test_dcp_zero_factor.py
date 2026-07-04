"""Property-based test for the DCP zero-factor case.

Feature: sharppy-modernization, Property 4: A zero buoyancy factor makes DCP exactly zero

Property 4 (design.md): *For any* valid Profile whose DCP input terms are all
present and whose computed DCAPE or MUCAPE is zero, the computed DCP is exactly
zero rather than a missing value.

**Validates: Requirements 2.5**

Notes
-----
``derived.dcp(prof)`` is defined as::

    DCP = (DCAPE/980) * (MUCAPE/2000) * (shear_0_6km/20) * (mean_wind_0_6km/16)

with all four terms drawn from the *same* analyzed Profile. Requirement 2.5 is
conditional: it applies only when *every* input term is present (the 0-6 km
shear/mean-wind terms resolve and the parcel oracle resolves both DCAPE and
MUCAPE) **and** the computed DCAPE or MUCAPE is exactly zero. For those Profiles
the contract is strict -- ``dcp`` must return exactly ``0.0`` (not ``MISSING``
and not a positive value).

This test reproduces ``dcp``'s own inputs independently:

* MUCAPE from the most-unstable parcel ascent (``params.parcelx(prof,
  flag=3).bplus``) and DCAPE from ``params.dcape(prof)``, both via the installed
  ``sharppy`` oracle from the *same* arrays ``dcp`` uses; and
* the presence of the 0-6 km shear / mean-wind terms via the same
  ``derived`` helpers ``dcp`` uses internally.

Hypothesis' ``assume()`` keeps only the soundings where the 0-6 km wind terms
are present and (MUCAPE == 0 or DCAPE == 0). The :func:`profiles` harness is
driven with ``zero_cape=True`` (a dry, stable column biased toward zero
undiluted CAPE, hence zero MUCAPE) so a meaningful fraction of drawn examples
satisfy the precondition. A deterministic stable/dry sounding with valid 0-6 km
winds is also asserted directly so the ``== 0.0`` clause is always exercised at
least once regardless of what the random draws filter to.
"""

from __future__ import annotations

import numpy as np
from hypothesis import assume, event, given

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles


def _oracle_dcape_mucape(snd: SoundingData):
    """Return ``(dcape, mucape)`` (J/kg) from the sharppy oracle, or ``None``.

    Mirrors :func:`sharpmod.sharptab.derived._dcape_mucape`: MUCAPE is the
    ``bplus`` of a most-unstable parcel (``flag=3``) and DCAPE is the first
    element of ``params.dcape``. Returns ``None`` when the oracle is unavailable
    or cannot resolve either quantity, so the caller can skip the example rather
    than assert on an undefined precondition.
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
        mucape = mupcl.bplus
        if mucape is None or not np.isfinite(mucape):
            return None
        dres = sp_params.dcape(prof)
        dcape = dres[0] if isinstance(dres, (tuple, list)) else dres
        if dcape is None or not np.isfinite(dcape):
            return None
    except Exception:
        return None
    return float(dcape), float(mucape)


def _wind_terms_present(snd: SoundingData) -> bool:
    """True when both 0-6 km DCP wind terms resolve (are not MISSING)."""
    shear06 = derived_mod._shear_0_6km(snd)
    mnwind06 = derived_mod._mean_wind_0_6km(snd)
    return not is_missing(shear06) and not is_missing(mnwind06)


@given(profiles(zero_cape=True))
def test_zero_buoyancy_factor_makes_dcp_zero(snd):
    """When all DCP terms are present and DCAPE or MUCAPE is zero, DCP == 0.0.

    Feature: sharppy-modernization, Property 4: A zero buoyancy factor makes DCP exactly zero
    Validates: Requirements 2.5
    """
    # Precondition 1: the 0-6 km wind/height terms must be present.
    assume(_wind_terms_present(snd))

    buoyancy = _oracle_dcape_mucape(snd)
    # Precondition 2: the parcel oracle resolves both DCAPE and MUCAPE.
    assume(buoyancy is not None)
    dcape, mucape = buoyancy

    # Precondition 3: a zero buoyancy factor (DCAPE == 0 or MUCAPE == 0).
    assume(dcape == 0.0 or mucape == 0.0)
    event("zero buoyancy factor with all DCP terms present (precondition met)")

    val = derived_mod.dcp(snd)

    # Requirement 2.5: a zero buoyancy factor -> exactly 0.0 (not MISSING, not >0).
    assert not is_missing(val), (
        "a zero buoyancy factor with all terms present must yield DCP 0.0, "
        "got MISSING")
    assert float(val) == 0.0, (
        f"a zero buoyancy factor must yield DCP == 0.0, got {float(val)!r}")


def _stable_dry_sounding() -> SoundingData:
    """A deterministic stable/dry sounding: zero MUCAPE, valid 0-6 km winds.

    A strong low-level inversion plus a warm, very dry column gives the
    most-unstable parcel no positive area, so the oracle resolves undiluted
    (MU) CAPE = 0. The column extends well past 6 km AGL with monotone winds so
    the 0-6 km shear and mean-wind terms both resolve, guaranteeing that every
    DCP input term is present while the MUCAPE factor is exactly zero.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 13000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    # Surface-based inversion (T rises 0->1 km) then a stable lapse aloft: a
    # parcel lifted from any level stays colder than its environment -> no CAPE.
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


def test_zero_factor_example_yields_zero_dcp():
    """A deterministic stable/dry sounding (MUCAPE == 0) gives DCP exactly 0.0.

    Guarantees the ``== 0.0`` clause of Property 4 is exercised at least once,
    independent of what the random draws filter to.

    Feature: sharppy-modernization, Property 4: A zero buoyancy factor makes DCP exactly zero
    Validates: Requirements 2.5
    """
    snd = _stable_dry_sounding()

    # All DCP input terms must be present for Requirement 2.5 to apply.
    assert _wind_terms_present(snd), (
        "deterministic sounding must have valid 0-6 km wind terms")
    buoyancy = _oracle_dcape_mucape(snd)
    assert buoyancy is not None, "oracle must resolve DCAPE and MUCAPE"
    dcape, mucape = buoyancy
    assert mucape == 0.0, (
        f"deterministic sounding must have zero undiluted CAPE, got {mucape!r}")

    val = derived_mod.dcp(snd)
    assert not is_missing(val), "a zero buoyancy factor must yield 0.0, got MISSING"
    assert float(val) == 0.0, f"expected DCP == 0.0, got {float(val)!r}"
