"""Property-based test for insufficient-data hazard handling (task 10.4).

Feature: sharppy-modernization, Property 22: Missing classifier inputs yield
insufficient data

Property 22 (design.md): *For any* valid Profile for which one or more
parameters required by the classification are missing or masked, the
Hazard_Classifier assigns the "insufficient data" label rather than any other
hazard category.

**Validates: Requirements 9.4**

How the property is exercised
-----------------------------
Starting from a full set of six present, finite inputs (which resolves to a
concrete, non-"insufficient data" label), a single required input is then either

* **removed** -- left unset so every ``getattr`` alias resolves to ``None``, or
* **masked** -- assigned the ``numpy.ma.masked`` sentinel,

and the classifier must return exactly ``"insufficient data"``. A companion
check drives the same requirement through a real
:class:`~sharpmod.tests.strategies.SoundingData` profile whose reported-level
column is masked, exercising the column-lifting path.

The suite-wide Hypothesis profile (see ``conftest.py``) runs each property for
at least 100 examples.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import event, given
from hypothesis import strategies as st

from sharpmod.sharptab.hazard import REQUIRED_INPUTS, classify
from sharpmod.tests.strategies import (
    CORE_FIELDS,
    HAZARD_INPUT_ATTRS,
    ParamProfile,
    SoundingData,
    hazard_inputs,
    profiles,
)


@given(hazard_inputs(), st.sampled_from(sorted(HAZARD_INPUT_ATTRS)),
       st.sampled_from(["remove", "mask"]))
def test_missing_or_masked_input_yields_insufficient_data(values, dropped, mode):
    """Removing or masking any single required input yields "insufficient data".

    Feature: sharppy-modernization, Property 22: Missing classifier inputs yield
    insufficient data
    Validates: Requirements 9.4
    """
    # Precondition: with every input present the classifier resolves a concrete
    # (non-"insufficient data") hazard label.
    baseline = classify(ParamProfile(values))
    assert baseline != "insufficient data", (
        f"baseline with all inputs present should resolve a hazard label, "
        f"got {baseline!r}"
    )

    # Drop or mask exactly one required input.
    perturbed = dict(values)
    if mode == "remove":
        perturbed[dropped] = None          # unset -> getattr aliases resolve None
    else:
        perturbed[dropped] = ma.masked     # present but masked

    label = classify(ParamProfile(perturbed))

    assert label == "insufficient data", (
        f"{mode!r} of required input {dropped!r} must yield 'insufficient "
        f"data', got {label!r}"
    )
    event(f"{mode} input={dropped}")


@given(profiles(min_levels=6, max_levels=20), st.sampled_from(CORE_FIELDS),
       st.data())
def test_masked_profile_column_yields_insufficient_data(snd, field, data):
    """Masking a required reported-level column of a real Profile yields
    "insufficient data" (never any other hazard category, never raising).

    Feature: sharppy-modernization, Property 22: Missing classifier inputs yield
    insufficient data
    Validates: Requirements 9.4
    """
    idx = data.draw(st.integers(min_value=0, max_value=snd.nlevels - 1),
                    label="masked_level")

    cols = {name: ma.array(getattr(snd, name), copy=True) for name in CORE_FIELDS}
    target = cols[field]
    mask = ma.getmaskarray(target).copy()
    mask[idx] = True
    target.mask = mask

    masked = SoundingData(
        pres=cols["pres"], hght=cols["hght"], tmpc=cols["tmpc"],
        dwpc=cols["dwpc"], wdir=cols["wdir"], wspd=cols["wspd"],
        omeg=snd.omeg,
    )

    label = classify(masked)
    assert label == "insufficient data", (
        f"masking column {field!r} at level {idx} must yield 'insufficient "
        f"data', got {label!r}"
    )
    event(f"masked column={field}")


def test_required_inputs_cover_the_classifier_input_set():
    """Guard: the strategy perturbs exactly the classifier's required inputs.

    Keeps the property honest -- if ``hazard.REQUIRED_INPUTS`` changes, the
    shared strategy's input set must track it.

    Feature: sharppy-modernization, Property 22: Missing classifier inputs yield
    insufficient data
    Validates: Requirements 9.4
    """
    assert set(HAZARD_INPUT_ATTRS) == set(REQUIRED_INPUTS), (
        "hazard_inputs strategy must perturb exactly the classifier's required "
        f"inputs; strategy={sorted(HAZARD_INPUT_ATTRS)} vs "
        f"required={sorted(REQUIRED_INPUTS)}"
    )
