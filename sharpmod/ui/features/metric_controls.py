"""Qt spin-box glue for the registry-derived numeric controls.

The numeric-control profiles themselves are pure registry math and live in
:mod:`sharpmod.analysis.metric_controls`, where the Python bridge and other
Qt-free callers reach them. This module keeps the one helper that touches a
widget, and re-exports the profiles so established import paths still resolve
to the same names."""

from __future__ import annotations

from sharpmod.analysis.metric_controls import MetricControl, control_for

__all__ = ["MetricControl", "apply_to_spin", "control_for"]


def apply_to_spin(spin, control: MetricControl, *, value=None) -> float:
    """Configure one ``QDoubleSpinBox`` for a diagnostic and return its value.

    Order matters: decimals must be set before the range, or Qt rounds the bounds
    to the previous precision and a valid value lands outside them.
    """
    spin.setDecimals(control.decimals)
    spin.setRange(control.minimum, control.maximum)
    spin.setSingleStep(control.step)
    spin.setSuffix(control.suffix)
    wanted = control.suggested if value is None else control.clamp(value)
    spin.setValue(wanted)
    return wanted
