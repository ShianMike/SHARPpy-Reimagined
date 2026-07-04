"""Property-based test for physical-range enforcement (task 8.5).

Feature: sharppy-modernization, Property 11: Every returned value lies within
its documented physical range

Property 11 (design.md): *For any* valid Profile and *for every* derived
parameter, every non-:data:`MISSING` value returned off the Profile lies within
the ``[phys_min, phys_max]`` bounds documented for that parameter in the
:data:`~sharpmod.sharptab.constants.PARAM_REGISTRY`. A value that would fall
outside its documented range is clamped to :data:`MISSING` before it is ever
returned (Requirements 14.5, 14.6).

**Validates: Requirements 14.6**

Oracle / cross-check
--------------------
The oracle is the registry itself. For every attribute in
:data:`~sharpmod.sharptab.profile.DERIVED_ATTRS`, the value is read off a real
``Profile`` (built from the generated sounding through the same lazy,
range-clamping ``__getattr__`` path the renderer uses) and asserted to either be
``MISSING`` or lie within ``[phys_min, phys_max]`` from that parameter's
:class:`~sharpmod.sharptab.constants.ParamSpec`.

* Scalar parameters are compared directly against their bounds.
* The vector-valued SFC-500 m mean wind (a ``(u, v)`` knot tuple) is range-checked
  on its magnitude, matching the clamp applied in
  :func:`sharpmod.sharptab.profile._clamp_to_range`.

Reads must **never raise** -- a parameter whose inputs are absent degrades to
``MISSING`` by contract. A deterministic strongly-buoyant, strongly-sheared
sounding guarantees that at least some parameters resolve to real, in-range
values so the range clause is genuinely exercised (not vacuously satisfied by an
all-``MISSING`` profile).
"""

from __future__ import annotations

import numpy as np
from hypothesis import event, given

from sharpmod.sharptab.constants import PARAM_REGISTRY, is_missing
from sharpmod.sharptab.profile import DERIVED_ATTRS, create_profile
from sharpmod.tests.strategies import SoundingData, profiles


def _magnitude(value):
    """Return the magnitude used for the physical-range check.

    Scalars are returned as ``float``; the vector-valued mean wind ``(u, v)`` is
    reduced to its Euclidean magnitude (matching the Profile's clamp).
    """
    if isinstance(value, (tuple, list)):
        return float(np.hypot(*value))
    return float(value)


def _profile_from(snd):
    """Build a ``Profile`` from a generated sounding, preserving masks.

    The masked source arrays are passed straight through so the ``Profile``
    keeps the strategy's masks (rather than round-tripping through a numeric
    fill sentinel), exercising the real lazy/range-clamp path.
    """
    return create_profile(
        pres=snd.pres, hght=snd.hght, tmpc=snd.tmpc,
        dwpc=snd.dwpc, wdir=snd.wdir, wspd=snd.wspd,
        omeg=snd.omeg, meta=snd.meta,
    )


def _assert_in_range(name, value):
    """Assert a single derived value is ``MISSING`` or within its bounds.

    Returns a short event tag describing the outcome for coverage tracking.
    """
    if is_missing(value):
        return f"{name}: MISSING"

    spec = PARAM_REGISTRY[name]
    magnitude = _magnitude(value)
    assert np.isfinite(magnitude), (
        f"{name} returned non-finite value {value!r}; out-of-range/non-finite "
        f"results must be clamped to MISSING"
    )
    assert spec.phys_min <= magnitude <= spec.phys_max, (
        f"{name} value {magnitude!r} lies outside its documented physical range "
        f"[{spec.phys_min}, {spec.phys_max}]; out-of-range results must be "
        f"clamped to MISSING (Requirement 14.6)"
    )
    return f"{name}: in-range"


@given(profiles())
def test_derived_values_within_physical_range(snd):
    """Every non-MISSING derived value lies within its documented range.

    Feature: sharppy-modernization, Property 11: Every returned value lies within
    its documented physical range
    Validates: Requirements 14.6
    """
    prof = _profile_from(snd)

    for name in sorted(DERIVED_ATTRS):
        # Reads never raise: an unresolved parameter degrades to MISSING.
        value = getattr(prof, name)
        event(_assert_in_range(name, value))


def _severe_sounding() -> SoundingData:
    """A deterministic strongly-buoyant, strongly-sheared severe-weather sounding.

    Warm/moist near the surface with a steep mid-level lapse rate, a dry mid
    layer, and veering/strengthening winds through the depth so many derived
    parameters resolve to real (non-MISSING) values -- guaranteeing the
    in-range clause is genuinely exercised rather than vacuously satisfied.
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


def test_physical_range_exercised_on_severe_example():
    """A severe-weather sounding: every derived value is MISSING or in-range.

    Guards the property's precondition: at least one derived parameter resolves
    to a real, non-MISSING value so the in-range assertion is genuinely
    exercised.

    Feature: sharppy-modernization, Property 11: Every returned value lies within
    its documented physical range
    Validates: Requirements 14.6
    """
    prof = _profile_from(_severe_sounding())

    resolved = 0
    for name in sorted(DERIVED_ATTRS):
        value = getattr(prof, name)
        _assert_in_range(name, value)
        if not is_missing(value):
            resolved += 1

    assert resolved > 0, (
        "expected at least one derived parameter to resolve to a real value on "
        "the severe-weather sounding so the in-range clause is exercised"
    )
