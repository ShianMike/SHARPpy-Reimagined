"""Property-based test for idempotent derived-parameter reads (task 8.2).

Feature: sharppy-modernization, Property 13: Derived-parameter reads are idempotent

Property 13 (design.md): *For any* derived parameter and *for any* unmodified
Profile, reading the parameter twice returns bitwise-identical results on each
access, including when the value is masked or missing.

**Validates: Requirements 13.1, 13.2**

Mechanism under test
--------------------
Every new derived parameter is exposed as a lazily computed ``Profile``
attribute (:data:`sharpmod.sharptab.profile.DERIVED_ATTRS`). The first access
computes the value via the ``__getattr__`` dispatch table, clamps it to its
documented physical range, and caches it *as an instance attribute*; every
subsequent read short-circuits the lazy machinery and returns the cached value.

This test builds a ``Profile`` from a Hypothesis-generated sounding, then reads
each registered derived attribute several times and asserts that every read
returns a bitwise-identical result -- including when that value is the
``MISSING`` masked sentinel (a singleton, so masked reads compare identically)
and including the vector-valued ``mean_wind_sfc_500m`` ``(u, v)`` tuple.
"""

from __future__ import annotations

import numpy as np
from hypothesis import event, given

from sharpmod.sharptab.constants import is_missing
from sharpmod.sharptab.profile import DERIVED_ATTRS, create_profile
from sharpmod.tests.strategies import SoundingData, profiles

#: Number of times each derived attribute is read per Profile. Two reads is the
#: minimum to exercise the "second read returns the cached value" contract; a
#: third guards against any read-count-dependent state.
_READS = 3

#: Deterministic iteration order over the derived-attribute set.
_DERIVED_ATTRS = tuple(sorted(DERIVED_ATTRS))


def _identical(a, b) -> bool:
    """Return ``True`` when ``a`` and ``b`` are bitwise-identical results.

    Handles the three shapes a derived read can take:

    * the ``MISSING`` masked sentinel (or any masked/NaN value) -- two missing
      reads are identical, a missing vs. present pair never is;
    * a scalar float -- compared for exact equality (``NaN`` never reaches here
      because it is treated as missing);
    * a ``(u, v)`` vector tuple -- compared element-wise.
    """
    a_missing = is_missing(a)
    b_missing = is_missing(b)
    if a_missing or b_missing:
        return a_missing and b_missing

    a_seq = isinstance(a, (tuple, list))
    b_seq = isinstance(b, (tuple, list))
    if a_seq or b_seq:
        if not (a_seq and b_seq) or len(a) != len(b):
            return False
        return all(_identical(x, y) for x, y in zip(a, b))

    fa, fb = float(a), float(b)
    if np.isnan(fa) and np.isnan(fb):
        return True
    return fa == fb


def _profile_from(snd: SoundingData):
    """Build a ``Profile`` from a generated sounding, preserving field masks."""
    return create_profile(
        pres=snd.pres, hght=snd.hght, tmpc=snd.tmpc,
        dwpc=snd.dwpc, wdir=snd.wdir, wspd=snd.wspd,
        omeg=snd.omeg, meta=snd.meta,
    )


@given(profiles())
def test_derived_reads_are_idempotent(snd):
    """Reading each derived attribute repeatedly yields identical results.

    Feature: sharppy-modernization, Property 13: Derived-parameter reads are idempotent
    Validates: Requirements 13.1, 13.2
    """
    prof = _profile_from(snd)

    saw_missing = False
    saw_present = False

    for name in _DERIVED_ATTRS:
        reads = [getattr(prof, name) for _ in range(_READS)]
        first = reads[0]

        if is_missing(first):
            saw_missing = True
        else:
            saw_present = True

        for i, value in enumerate(reads[1:], start=2):
            assert _identical(first, value), (
                f"attribute {name!r}: read #{i} ({value!r}) is not "
                f"bitwise-identical to the first read ({first!r})"
            )

    if saw_missing:
        event("at least one derived attribute read as MISSING")
    if saw_present:
        event("at least one derived attribute read as a present value")


def _supercell_sounding() -> SoundingData:
    """A deterministic strongly-buoyant, strongly-sheared supercell sounding.

    Warm/moist near the surface with a steep mid-level lapse rate and
    veering/strengthening low-level winds through 6 km so that many of the
    derived attributes resolve to real (non-MISSING) values -- guaranteeing the
    idempotency contract is exercised for present values, not just for MISSING.
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
    wdir = np.array(
        [140.0, 170.0, 195.0, 220.0, 240.0, 255.0,
         260.0, 265.0, 270.0, 275.0, 280.0, 285.0], dtype=float)
    wspd = np.array(
        [10.0, 22.0, 32.0, 42.0, 50.0, 62.0,
         70.0, 78.0, 85.0, 90.0, 96.0, 102.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_derived_reads_are_idempotent_on_supercell_example():
    """A supercell sounding: repeated reads of every derived attribute agree.

    Guards the property's precondition: at least one input yields real
    (non-MISSING) derived values so the idempotency contract is genuinely
    exercised on present values (not just the MISSING sentinel).

    Feature: sharppy-modernization, Property 13: Derived-parameter reads are idempotent
    Validates: Requirements 13.1, 13.2
    """
    prof = _profile_from(_supercell_sounding())

    present = 0
    for name in _DERIVED_ATTRS:
        reads = [getattr(prof, name) for _ in range(_READS)]
        first = reads[0]
        if not is_missing(first):
            present += 1
        for i, value in enumerate(reads[1:], start=2):
            assert _identical(first, value), (
                f"attribute {name!r}: read #{i} ({value!r}) is not "
                f"bitwise-identical to the first read ({first!r})"
            )

    assert present > 0, (
        "supercell sounding should yield at least one present derived value "
        "so idempotency is exercised on a non-MISSING result"
    )
