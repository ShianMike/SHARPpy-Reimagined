"""Numeric-control bounds, step, and precision derived from the metric registry.

A threshold control that accepts anything from -100000 to +100000 in steps of one,
with one decimal place regardless of the diagnostic, is wrong in four ways at once.
It permits values no sounding can produce, it steps by an amount too small to be
useful for CAPE and too coarse to be useful for STP, it shows a decimal place that
CAPE does not have and hides the two that STP needs, and -- the part that actually
misleads -- switching the diagnostic leaves the previous number in place, so
``1000 J/kg`` silently becomes ``1000 kt``.

The registry already knows the units and the precision. What it does not carry is a
plausible range or a sensible step, so those are derived here from the unit, once,
in one place, rather than guessed at each call site.

Nothing here is a meteorological criterion. Bounds are generous limits that keep a
control honest, not statements about what values matter; the only starting values
stated explicitly are the three this application already shipped.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from sharpmod.analysis.box_analysis import BoxAnalysisError, parameter


#: ``(minimum, maximum, step)`` per raw registry unit.
#:
#: Keyed on the raw ``units`` string rather than the display form, because that is
#: what the registry stores and what saved threshold documents contain. One range
#: covers both signs of a unit that has both -- CAPE and CIN share ``J/kg`` -- so a
#: reader who wants a negative CIN gate is not blocked by a positive-only bound.
_UNIT_RANGES = {
    "J/kg": (-1000.0, 8000.0, 100.0),
    "kt": (0.0, 200.0, 5.0),
    "m2/s2": (-500.0, 1500.0, 25.0),
    "m3/s3": (0.0, 100000.0, 1000.0),
    "m": (0.0, 20000.0, 100.0),
    "m AGL": (0.0, 20000.0, 100.0),
    "hPa": (100.0, 1100.0, 10.0),
    "C": (-60.0, 60.0, 1.0),
    "F": (-40.0, 130.0, 5.0),
    "C/km": (-10.0, 15.0, 0.5),
    "g/kg": (0.0, 30.0, 0.5),
    "%": (0.0, 100.0, 5.0),
    "in": (0.0, 4.0, 0.1),
    "deg": (0.0, 360.0, 5.0),
}

#: Unitless diagnostics split by their own precision. The two-decimal SPC
#: composites sit near zero to ten; the whole-number legacy indices run far wider,
#: so one unitless range would be wrong for one group or the other.
_UNITLESS_RANGES = {
    2: (-5.0, 20.0, 0.25),
    1: (-20.0, 60.0, 0.5),
    0: (-50.0, 100.0, 1.0),
}

#: Used only when a diagnostic arrives with a unit this module has never seen.
#: Wide enough not to block a real value, and reported as a fallback rather than
#: pretending to be a considered range.
_FALLBACK = (-100000.0, 100000.0, 1.0)

#: Starting values this application already shipped, preserved exactly so adding
#: derived defaults for the other fifty-six diagnostics does not quietly change
#: the three a reader may already be used to.
_SHIPPED_SUGGESTIONS = {
    "mlcape": 1000.0,
    "shear_6km": 35.0,
    "srh_3km": 150.0,
}

#: How far above the lower value a ``between`` upper bound starts, in steps.
_BETWEEN_STEPS = 10


@dataclass(frozen=True)
class MetricControl:
    """Everything a numeric control needs to present one diagnostic honestly."""

    key: str
    label: str
    units: str
    decimals: int
    minimum: float
    maximum: float
    step: float
    suggested: float
    derived: bool = False

    @property
    def span(self) -> float:
        return self.maximum - self.minimum

    @property
    def suffix(self) -> str:
        """The spin-box suffix, including its leading space, or an empty string."""
        return f" {self.units}" if self.units else ""

    def clamp(self, value) -> float:
        """Return ``value`` inside the bounds, at this diagnostic's precision."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return self.suggested
        if not math.isfinite(number):
            return self.suggested
        number = max(self.minimum, min(self.maximum, number))
        return round(number, max(0, int(self.decimals)))

    def upper_for(self, value) -> float:
        """Return a starting upper bound for a ``between`` rule on this metric.

        Scaled by this diagnostic's own step. A fixed offset -- the previous
        behaviour added five hundred to everything -- is meaningless for a
        composite index that tops out near ten and negligible for ``m3/s3``.
        """
        return self.clamp(self.clamp(value) + self.step * _BETWEEN_STEPS)

    def describe(self) -> str:
        """One sentence naming the units, precision, step, and permitted range."""
        units = self.units or "unitless values"
        return (
            f"{self.label} in {units}, to {self.decimals} decimal place"
            f"{'' if self.decimals == 1 else 's'}, stepping by "
            f"{self._number(self.step)}, between {self._number(self.minimum)} and "
            f"{self._number(self.maximum)}."
        )

    def _number(self, value) -> str:
        return f"{float(value):.{max(0, int(self.decimals))}f}"


def _nice_step(step: float, decimals: int) -> float:
    """Round a step to this diagnostic's precision without collapsing to zero."""
    smallest = 10.0 ** -max(0, int(decimals))
    return max(smallest, round(float(step), max(0, int(decimals))))


def _derived_suggestion(minimum: float, maximum: float, step: float, notable: str):
    """Return a neutral starting value inside the bounds, snapped to the step.

    An eighth of the notable extent, where the notable extent is the negative
    bound only for a low-notable diagnostic that actually has one. Taking the
    minimum unconditionally for every low-notable diagnostic put a zero in the
    LCL-height control and pinned the inflow-top control to its own lower bound,
    both of which read as a broken control rather than a starting point.

    This is a placeholder a reader replaces, not a recommendation. Deriving a
    meaningful threshold from units alone is not possible, and inventing one per
    diagnostic would put meteorological claims in a units table.
    """
    extent = maximum
    if str(notable).strip().lower() == "low" and minimum < 0.0:
        extent = minimum
    snapped = round(float(extent) / 8.0 / step) * step
    return max(minimum, min(maximum, snapped))


def control_for(key) -> MetricControl:
    """Return the numeric-control profile for one registry diagnostic."""
    try:
        item = parameter(str(key))
    except (BoxAnalysisError, Exception):  # noqa: BLE001 - unknown key still usable
        minimum, maximum, step = _FALLBACK
        return MetricControl(
            str(key),
            str(key).replace("_", " ").upper(),
            "",
            1,
            minimum,
            maximum,
            step,
            0.0,
            derived=True,
        )
    decimals = max(0, int(item.decimals))
    if item.units:
        bounds = _UNIT_RANGES.get(item.units)
    else:
        bounds = _UNITLESS_RANGES.get(decimals)
    derived = bounds is None
    minimum, maximum, step = bounds or _FALLBACK
    step = _nice_step(step, decimals)
    suggested = _SHIPPED_SUGGESTIONS.get(item.key)
    if suggested is None:
        suggested = _derived_suggestion(minimum, maximum, step, item.notable)
    return MetricControl(
        item.key,
        item.label,
        item.display_units,
        decimals,
        minimum,
        maximum,
        step,
        round(float(suggested), decimals),
        derived=derived,
    )


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


__all__ = ["MetricControl", "apply_to_spin", "control_for"]
