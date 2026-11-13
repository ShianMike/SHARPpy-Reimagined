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

#: Starting values this application already shipped, preserved exactly so adding
#: derived defaults for the other fifty-six diagnostics does not quietly change
#: the three a reader may already be used to. The keys are also the threshold
#: form's starting diagnostics.
SHIPPED_SUGGESTIONS = {
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


def control_for(key) -> MetricControl:
    """Return the numeric-control profile for one registry diagnostic."""
    import sharpmod_rs

    return MetricControl(*sharpmod_rs.threshold_control_for(str(key)))


__all__ = ["MetricControl", "SHIPPED_SUGGESTIONS", "control_for"]
