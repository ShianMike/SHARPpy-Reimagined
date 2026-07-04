"""Property-based test for hazard-classifier determinism (task 10.3).

Feature: sharppy-modernization, Property 21: Hazard classification is a
deterministic function of its inputs

Property 21 (design.md): *For any* two Profiles that produce identical values
for every input parameter used by the classification, the Hazard_Classifier
assigns both the same label, derived solely from those Profile-computed
parameters evaluated against the fixed documented thresholds.

**Validates: Requirements 9.2, 9.3**

How the property is exercised
-----------------------------
Two independent :class:`~sharpmod.tests.strategies.ParamProfile` objects are
built from the *same* drawn input values (see
:func:`~sharpmod.tests.strategies.hazard_inputs`). Because the label must be a
function of the six inputs alone, both profiles -- distinct objects that agree
on every input parameter -- must receive the identical label. A repeated call on
the same profile is also asserted identical, pinning the "deterministic" half of
the contract.

The suite-wide Hypothesis profile (see ``conftest.py``) runs each property for
at least 100 examples.
"""

from __future__ import annotations

from hypothesis import event, given

from sharpmod.sharptab.hazard import HAZARD_LABELS, classify
from sharpmod.tests.strategies import ParamProfile, hazard_inputs


@given(hazard_inputs())
def test_identical_inputs_yield_identical_label(values):
    """Two profiles agreeing on every input receive the same label; repeated
    classification of the same profile is stable.

    Feature: sharppy-modernization, Property 21: Hazard classification is a
    deterministic function of its inputs
    Validates: Requirements 9.2, 9.3
    """
    prof_a = ParamProfile(values)
    prof_b = ParamProfile(dict(values))  # a distinct object, identical inputs

    label_a = classify(prof_a)
    label_b = classify(prof_b)

    assert label_a in HAZARD_LABELS
    assert label_a == label_b, (
        "two profiles that produce identical values for every classification "
        f"input must receive the same label, got {label_a!r} vs {label_b!r} "
        f"for inputs {values!r}"
    )

    # Determinism: reclassifying the same profile never changes the answer.
    assert classify(prof_a) == label_a, (
        f"reclassifying the same profile changed the label from {label_a!r}"
    )
    event(f"label={label_a}")
