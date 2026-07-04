"""Property-based test for single-valid-label hazard classification (task 10.2).

Feature: sharppy-modernization, Property 20: Hazard classification always
returns exactly one valid label

Property 20 (design.md): *For any* valid Profile, the Hazard_Classifier returns
exactly one label drawn from the set {none, marginal, tornado, supercell, wind,
hail, insufficient data}.

**Validates: Requirements 9.1**

How the property is exercised
-----------------------------
:func:`sharpmod.sharptab.hazard.classify` returns a single ``str``; the property
asserts that string is always a member of
:data:`sharpmod.sharptab.hazard.HAZARD_LABELS` and never raises. Two generators
are used so the label space is genuinely exercised, not just the
degrade-to-missing path:

* the shared :func:`~sharpmod.tests.strategies.profiles` strategy feeds bare
  soundings straight into ``classify`` (the oracle-lifting path), and
* :func:`~sharpmod.tests.strategies.hazard_inputs` +
  :class:`~sharpmod.tests.strategies.ParamProfile` feed profiles that already
  expose the six convective inputs, so the pinned decision cascade resolves to a
  concrete (non-"insufficient data") hazard label.

The suite-wide Hypothesis profile (see ``conftest.py``) runs each property for
at least 100 examples.
"""

from __future__ import annotations

from hypothesis import event, given

from sharpmod.sharptab.hazard import HAZARD_LABELS, classify
from sharpmod.tests.strategies import ParamProfile, hazard_inputs, profiles


@given(profiles())
def test_classify_returns_exactly_one_valid_label(snd):
    """``classify`` on any generated sounding returns one label from the set.

    Feature: sharppy-modernization, Property 20: Hazard classification always
    returns exactly one valid label
    Validates: Requirements 9.1
    """
    label = classify(snd)

    assert isinstance(label, str), f"classify returned a non-string: {label!r}"
    assert label in HAZARD_LABELS, (
        f"classify returned {label!r}, which is not one of the "
        f"{len(HAZARD_LABELS)} valid hazard labels {HAZARD_LABELS}"
    )
    event(f"label={label}")


@given(hazard_inputs())
def test_classify_from_resolved_inputs_returns_valid_label(values):
    """A profile exposing all six inputs classifies to exactly one valid label.

    This drives the pinned decision cascade to a concrete hazard category rather
    than the degrade-to-missing path, confirming every branch still yields a
    member of ``HAZARD_LABELS``.

    Feature: sharppy-modernization, Property 20: Hazard classification always
    returns exactly one valid label
    Validates: Requirements 9.1
    """
    label = classify(ParamProfile(values))

    assert label in HAZARD_LABELS, (
        f"classify returned {label!r}, not one of {HAZARD_LABELS}"
    )
    # With all six inputs present and finite the classifier must reach a real
    # hazard category, never the missing-input sentinel.
    assert label != "insufficient data", (
        "all six inputs were present and finite, so the classifier must resolve "
        f"a concrete hazard label, got {label!r}"
    )
    event(f"resolved label={label}")
