"""Property-based test for ECAPE non-negativity.

Feature: sharppy-modernization, Property 6: ECAPE is non-negative

Property 6 (design.md): *For any* valid Profile for which ECAPE is computed,
the ECAPE value is greater than or equal to zero J/kg.

**Validates: Requirements 5.3**

Notes
-----
``ecape.ecape(prof)`` returns J/kg or the ``MISSING`` sentinel and *never
raises*. ECAPE is often ``MISSING`` (it needs winds plus a resolvable
parcel ascent with positive undiluted CAPE); that is fine -- the property only
constrains *computed* (non-missing) values. The strategy therefore leans on
full-depth soundings with winds (the :func:`profiles` default) so a meaningful
fraction of examples exercise a real numeric ECAPE, and a deterministic
unstable sounding is included to guarantee the non-negativity clause is
actually exercised at least once.
"""

from __future__ import annotations

import warnings

import numpy as np
import numpy.ma as ma
from hypothesis import event, given

from sharpmod.sharptab import ecape as ecape_mod
from sharpmod.sharptab.constants import MISSING, is_missing
from sharpmod.tests.strategies import SoundingData, profiles


@given(profiles())
def test_ecape_is_non_negative(snd):
    """A computed (non-missing) ECAPE is always >= 0 J/kg; the call never raises.

    Feature: sharppy-modernization, Property 6: ECAPE is non-negative
    Validates: Requirements 5.3
    """
    # Must never raise -- any failure degrades to MISSING by contract.
    val = ecape_mod.ecape(snd)

    if is_missing(val):
        event("ecape: MISSING (uncomputable for this sounding)")
        return

    event("ecape: computed numeric value")
    fval = float(val)
    assert np.isfinite(fval), f"computed ECAPE must be finite, got {fval!r}"
    assert fval >= 0.0, f"computed ECAPE must be >= 0 J/kg, got {fval!r}"


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
    # Moderate CAPE; isothermal stratosphere (top three levels) guarantees an EL.
    tmpc = np.array(
        [26.0, 22.0, 18.0, 11.0, 5.0, -9.0,
         -24.0, -40.0, -56.0, -58.0, -58.0, -58.0], dtype=float)
    # Moist low levels (small depression), drier aloft.
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


def test_ecape_non_negative_on_unstable_example():
    """A strongly buoyant sounding yields a computed, non-negative ECAPE.

    Guards the property's precondition: at least one input produces a real
    (non-MISSING) ECAPE so the >= 0 clause is genuinely exercised.

    Feature: sharppy-modernization, Property 6: ECAPE is non-negative
    Validates: Requirements 5.3
    """
    val = ecape_mod.ecape(_unstable_sounding())
    assert not is_missing(val), "unstable sounding should yield a computed ECAPE"
    assert float(val) >= 0.0


def test_ecape_ignores_unphysical_saturation_state_above_equilibrium_level():
    """Upper-stratospheric levels outside the ECAPE integral emit no warning."""
    snd = _unstable_sounding()
    extended = SoundingData(
        np.append(snd.pres, 1.0),
        np.append(snd.hght, 49420.0),
        np.append(snd.tmpc, 5.2),
        np.append(snd.dwpc, -106.6),
        np.append(snd.wdir, 87.0),
        np.append(snd.wspd, 39.0),
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = ecape_mod.ecape(extended)

    assert not is_missing(value)
    assert not any(
        "Saturation mixing ratio is undefined" in str(item.message)
        for item in caught
    )
