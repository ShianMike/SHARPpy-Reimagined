"""Metric-aware numeric control contracts used by threshold workflows."""

from __future__ import annotations

import math

from sharpmod.box_analysis import PARAMETERS
from sharpmod.metric_controls import control_for


def test_every_registered_metric_has_an_explicit_usable_control_profile():
    for item in PARAMETERS:
        control = control_for(item.key)
        assert not control.derived, item.key
        assert control.units == item.display_units
        assert control.decimals == item.decimals
        assert math.isfinite(control.minimum)
        assert math.isfinite(control.maximum)
        assert control.minimum < control.suggested < control.maximum
        assert 0.0 < control.step <= control.span
        assert control.step >= 10.0 ** -control.decimals
        assert control.suffix == (f" {item.display_units}" if item.display_units else "")
        assert control.upper_for(control.suggested) >= control.suggested


def test_shipped_thresholds_remain_the_defaults_for_their_diagnostics():
    assert control_for("mlcape").suggested == 1000.0
    assert control_for("shear_6km").suggested == 35.0
    assert control_for("srh_3km").suggested == 150.0


def test_unknown_future_metric_uses_a_disclosed_finite_fallback():
    control = control_for("future_metric")
    assert control.derived
    assert control.minimum < control.suggested < control.maximum
    assert "FUTURE METRIC" in control.describe()
