"""Hazard labels from a real sounding and randomized convective inputs."""

from __future__ import annotations

import pytest
from hypothesis import event, given

from sharpmod.io.decoder import load_npz
from sharpmod.sharptab.hazard import HAZARD_LABELS, classify
from sharpmod.tests._examples import examples_dir
from sharpmod.tests.strategies import ParamProfile, hazard_inputs


def test_bundled_sounding_classifies_as_tornado():
    """Decode and promote a sounding before classifying its computed diagnostics."""
    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.is_file():
        pytest.skip("bundled HRRR sounding unavailable")
    collection, _ = load_npz(str(example))
    # The sample's STP, SCP, shear and helicity all exceed the tornado thresholds.
    assert classify(collection.getHighlightedProf()) == "tornado"


@given(hazard_inputs())
def test_classify_from_resolved_inputs_returns_valid_label(values):
    """All six finite inputs must resolve to one concrete hazard label."""
    label = classify(ParamProfile(values))
    assert isinstance(label, str)
    assert label in HAZARD_LABELS
    assert label != "insufficient data"
    event(f"resolved label={label}")
