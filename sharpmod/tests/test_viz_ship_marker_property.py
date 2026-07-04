"""Property-based test for the SHIP inset marker mapping (task 14.2).

Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
clamped monotonic mapping of the value

Property 26 (design.md): *For any* SHIP value, the position of the marker along
the inset's scale axis is a clamped, monotonic non-decreasing function of the
value. Larger values never move the marker left, and values above the
documented maximum bound clamp to the maximum-bound endpoint rather than moving
beyond the drawn scale.

**Validates: Requirements 20.2, 20.3**

How the property is exercised
-----------------------------
:meth:`sharpmod.viz.ship.plotSHIP.scale_fraction` is the pure marker-position
function: it maps a SHIP value to a fraction in ``[0, 1]`` along the axis (0 at
:data:`SHIP_SCALE_MIN`, 1 at :data:`SHIP_SCALE_MAX`). Because the drawn marker
x-coordinate is an affine, order-preserving transform of that fraction
(``x = x0 + frac * (x1 - x0)`` with ``x1 > x0``), asserting the properties on
``scale_fraction`` is equivalent to asserting them on the marker position, and
needs no Qt / running QApplication.

The suite-wide Hypothesis profile (see ``conftest.py``) runs each property for
at least 100 examples.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from sharpmod.viz.ship import SHIP_SCALE_MAX, SHIP_SCALE_MIN, plotSHIP


# Finite SHIP values spanning well below the min and well above the max so the
# clamping behavior on both ends is exercised, not just the interior mapping.
_ship_values = st.floats(
    min_value=-50.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)


@given(_ship_values, _ship_values)
def test_scale_fraction_is_monotonic_non_decreasing(a, b):
    """value_a <= value_b implies fraction(a) <= fraction(b).

    Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
    clamped monotonic mapping of the value
    Validates: Requirements 20.2
    """
    lo, hi = (a, b) if a <= b else (b, a)
    frac_lo = plotSHIP.scale_fraction(lo)
    frac_hi = plotSHIP.scale_fraction(hi)

    assert frac_lo <= frac_hi + 1e-12, (
        f"scale_fraction is not monotonic non-decreasing: "
        f"f({lo})={frac_lo} > f({hi})={frac_hi}"
    )


@given(_ship_values)
def test_scale_fraction_is_bounded_to_unit_interval(value):
    """Every mapped fraction stays within [0, 1] (marker never leaves the axis).

    Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
    clamped monotonic mapping of the value
    Validates: Requirements 20.2, 20.3
    """
    frac = plotSHIP.scale_fraction(value)

    assert 0.0 <= frac <= 1.0, f"fraction {frac} for value {value} left [0, 1]"


@given(st.floats(min_value=SHIP_SCALE_MAX, max_value=1e6,
                 allow_nan=False, allow_infinity=False))
def test_values_at_or_above_max_clamp_to_endpoint(value):
    """Values >= the documented max bound map to the max endpoint (frac == 1.0).

    This is the clamping requirement: the marker sits at the maximum-bound
    endpoint rather than beyond the drawn scale (Requirement 20.3).

    Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
    clamped monotonic mapping of the value
    Validates: Requirements 20.3
    """
    assert plotSHIP.scale_fraction(value) == 1.0, (
        f"value {value} >= SHIP_SCALE_MAX ({SHIP_SCALE_MAX}) did not clamp to 1.0"
    )


@given(st.floats(max_value=SHIP_SCALE_MIN, allow_nan=False, allow_infinity=False))
def test_values_at_or_below_min_clamp_to_start(value):
    """Values <= the documented min bound map to the start endpoint (frac == 0.0).

    Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
    clamped monotonic mapping of the value
    Validates: Requirements 20.2
    """
    assert plotSHIP.scale_fraction(value) == 0.0, (
        f"value {value} <= SHIP_SCALE_MIN ({SHIP_SCALE_MIN}) did not clamp to 0.0"
    )


def test_endpoints_map_to_scale_ends():
    """The documented bounds map exactly to the axis endpoints.

    Feature: sharppy-modernization, Property 26: SHIP inset marker position is a
    clamped monotonic mapping of the value
    Validates: Requirements 20.2
    """
    assert plotSHIP.scale_fraction(SHIP_SCALE_MIN) == 0.0
    assert plotSHIP.scale_fraction(SHIP_SCALE_MAX) == 1.0
