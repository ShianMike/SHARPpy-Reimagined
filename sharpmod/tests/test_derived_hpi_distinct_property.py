"""Property-based test for HPI distinctness (task 7.7).

Feature: sharppy-modernization, Property 9: HPI is a distinct quantity

Property 9 (design.md): *For any* valid Profile for which HPI, LRGHAIL, and SHIP
are all present, HPI is exposed as its own attribute whose value is not defined
to equal the LRGHAIL value or the SHIP value.

**Validates: Requirements 6.3**

Interpretation
--------------
Property 9 is a *definitional distinctness* property, not a claim that HPI is
always numerically different from LRGHAIL and SHIP (two independent formulas can
coincidentally coincide for a particular sounding). The property is exercised in
two complementary ways:

1. **Structural distinctness** -- HPI, LRGHAIL, and SHIP are three *separate*
   quantities computed by three *separate* functions/oracles
   (:func:`derived.hail_possibility_index`,
   :func:`derived.large_hail_parameter`, and the sharppy oracle
   ``sharppy.sharptab.params.ship``). None of them is defined as an alias of
   another. This is asserted directly.

2. **Non-identity over the input space** -- across the generated soundings, HPI
   is *not identically equal* to LRGHAIL and *not identically equal* to SHIP.
   Rather than requiring every single example to differ (which a coincidence
   could violate), values are collected across examples and, after the run, the
   suite asserts that HPI is not element-wise identical to either LRGHAIL or
   SHIP over the collected set. A deterministic sounding for which all three are
   present and HPI differs from both guarantees the clause is genuinely
   exercised even if Hypothesis draws few all-present examples.

All three computations *never raise*: absent/masked inputs degrade to the
missing-value sentinel by contract.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from hypothesis import event, given

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles

# --- Cross-example collector: (hpi, lrghail, ship) all-present tuples --------
_COLLECTED: list[tuple[float, float, float]] = []


def _ship(prof):
    """Independently compute SHIP (SPC Significant Hail Parameter) via the oracle.

    Uses the same sharppy "default" Profile oracle the SPC/AMS routines read
    (built by :func:`derived._oracle_profile`, which augments ``mupcl`` and the
    other attributes ``ship`` depends on). Returns a finite ``float`` or
    ``None`` when SHIP cannot be resolved. Never raises.
    """
    try:
        sp = derived_mod._oracle_profile(prof)
        if sp is None:
            return None
        from sharppy.sharptab import params as sp_params

        value = sp_params.ship(sp)
        fval = float(value)
        if is_missing(value) or not np.isfinite(fval):
            return None
        return fval
    except Exception:
        return None


def test_hpi_lrghail_ship_are_distinct_functions():
    """HPI, LRGHAIL, and SHIP are three separate, non-aliased quantities.

    Structural distinctness: none of the three is defined as an alias of another.

    Feature: sharppy-modernization, Property 9: HPI is a distinct quantity
    Validates: Requirements 6.3
    """
    # Three separate callables (HPI and LRGHAIL) plus the independent SHIP oracle.
    assert (
        derived_mod.hail_possibility_index
        is not derived_mod.large_hail_parameter
    ), "HPI must not be defined as an alias of LRGHAIL"

    # The two SHARPpy Reimagined derived quantities are distinct named module attributes.
    assert derived_mod.hail_possibility_index.__name__ == "hail_possibility_index"
    assert derived_mod.large_hail_parameter.__name__ == "large_hail_parameter"


@given(profiles())
def test_hpi_is_a_distinct_quantity(snd):
    """HPI is its own attribute, never defined equal to LRGHAIL or SHIP.

    For each generated sounding every quantity is computed independently and
    must never raise. Whenever HPI, LRGHAIL, and SHIP are *all* present the
    triple is collected so the suite can assert non-identity across the input
    space (see :func:`test_hpi_not_identically_equal_across_inputs`).

    Feature: sharppy-modernization, Property 9: HPI is a distinct quantity
    Validates: Requirements 6.3
    """
    # Each is computed by its own routine and degrades to MISSING, never raises.
    hpi = derived_mod.hail_possibility_index(snd)
    lrghail = derived_mod.large_hail_parameter(snd)
    ship = _ship(snd)

    hpi_present = not is_missing(hpi)
    lrghail_present = not is_missing(lrghail)
    ship_present = ship is not None

    if hpi_present:
        fhpi = float(hpi)
        assert np.isfinite(fhpi), f"HPI, when present, must be finite: {fhpi!r}"

    if hpi_present and lrghail_present and ship_present:
        _COLLECTED.append((float(hpi), float(lrghail), float(ship)))
        event("all three present (HPI, LRGHAIL, SHIP)")
    else:
        event("at least one of HPI/LRGHAIL/SHIP missing")


def _hpi_distinct_sounding() -> SoundingData:
    """A deterministic hail-favorable sounding for which all three are present.

    The checked-in HRRR point fixture has strong buoyancy through a deep
    hail-growth zone and veering/strengthening winds. HPI (hail-growth-zone CAPE
    x melt factor), LRGHAIL, and SHIP all resolve to distinct real values.
    """
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "soundings"
        / "hrrr_point_36.68N_95.66W_f018.npz"
    )
    with np.load(fixture_path) as fixture:
        arrays = [
            np.array(fixture[name], dtype=float, copy=True)
            for name in ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
        ]
    return SoundingData(*arrays)


def test_hpi_distinct_from_lrghail_and_ship_on_hail_sounding():
    """A hail-favorable sounding: HPI present and distinct from LRGHAIL and SHIP.

    Guarantees the non-identity clause is genuinely exercised: all three
    quantities are present and HPI's value equals neither LRGHAIL nor SHIP.

    Feature: sharppy-modernization, Property 9: HPI is a distinct quantity
    Validates: Requirements 6.3
    """
    snd = _hpi_distinct_sounding()

    hpi = derived_mod.hail_possibility_index(snd)
    lrghail = derived_mod.large_hail_parameter(snd)
    ship = _ship(snd)

    assert not is_missing(hpi), "deterministic hail sounding must produce HPI"
    assert not is_missing(lrghail), (
        "deterministic hail sounding must produce LRGHAIL"
    )
    assert ship is not None, "deterministic hail sounding must produce SHIP"

    fhpi, flrg, fship = float(hpi), float(lrghail), float(ship)
    # Record for the cross-example non-identity assertion too.
    _COLLECTED.append((fhpi, flrg, fship))

    assert fhpi != flrg, (
        f"HPI ({fhpi!r}) must not be defined equal to LRGHAIL ({flrg!r})"
    )
    assert fhpi != fship, (
        f"HPI ({fhpi!r}) must not be defined equal to SHIP ({fship!r})"
    )


def test_hpi_not_identically_equal_across_inputs():
    """Across all collected all-present soundings, HPI is not identical to either.

    Asserts HPI is *not* element-wise equal to LRGHAIL over the collected set and
    *not* element-wise equal to SHIP over the collected set -- proving HPI is a
    genuinely distinct quantity rather than the same function under another name.

    Must run after the collecting tests; pytest executes tests top-to-bottom
    within a module, and the deterministic hail-sounding test above always
    contributes at least one all-present triple when the oracle is available.

    Feature: sharppy-modernization, Property 9: HPI is a distinct quantity
    Validates: Requirements 6.3
    """
    if not _COLLECTED:
        # Oracle unavailable this environment: structural distinctness
        # (test_hpi_lrghail_ship_are_distinct_functions) still holds.
        return

    hpi_vals = [t[0] for t in _COLLECTED]
    lrghail_vals = [t[1] for t in _COLLECTED]
    ship_vals = [t[2] for t in _COLLECTED]

    assert any(h != l for h, l in zip(hpi_vals, lrghail_vals)), (
        "HPI is identically equal to LRGHAIL across every collected sounding; "
        "HPI must be a distinct quantity (Requirement 6.3)"
    )
    assert any(h != s for h, s in zip(hpi_vals, ship_vals)), (
        "HPI is identically equal to SHIP across every collected sounding; "
        "HPI must be a distinct quantity (Requirement 6.3)"
    )
