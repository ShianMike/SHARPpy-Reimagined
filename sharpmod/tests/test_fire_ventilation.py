"""The ventilation rate: mixing height times transport wind.

Upstream computes both factors and prints them separately but never their
product, which is the number that governs whether smoke will clear -- a deep
mixed layer with no wind ventilates as badly as a windy shallow one, and neither
factor alone says so.

The function is deliberately a plain function of two already-computed values
rather than a derived Profile attribute, so the panel's ventilation rate always
follows from the mixing height and transport wind printed beside it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sharpmod.sharptab.constants import MISSING, is_missing
from sharpmod.sharptab.fire import (
    KNOTS_TO_MS,
    VENDORED_MISSING,
    ventilation_rate,
)


def test_the_rate_is_depth_times_wind_in_metres_per_second():
    assert ventilation_rate(1500.0, 20.0) == pytest.approx(
        1500.0 * 20.0 * KNOTS_TO_MS)


def test_a_knot_converts_exactly():
    """The nautical mile is defined, so this conversion is not approximate."""
    assert KNOTS_TO_MS == pytest.approx(1852.0 / 3600.0, abs=1e-6)


def test_a_realistic_case_lands_where_it_should():
    """327 m mixed layer under a 13 kt transport wind."""
    assert ventilation_rate(327.0, 13.0) == pytest.approx(2186.9, abs=1.0)


@pytest.mark.parametrize("height,wind", [
    (0.0, 20.0),
    (1000.0, 0.0),
    (0.0, 0.0),
])
def test_a_calm_or_shallow_layer_ventilates_nothing(height, wind):
    """Zero is a real answer here, not a missing one."""
    result = ventilation_rate(height, wind)
    assert not is_missing(result)
    assert result == 0.0


@pytest.mark.parametrize("bad", [
    None,
    MISSING,
    -1.0,
    math.nan,
    math.inf,
    "tall",
    np.ma.masked,
])
def test_an_unusable_mixing_height_yields_missing(bad):
    assert is_missing(ventilation_rate(bad, 13.0))


@pytest.mark.parametrize("bad", [
    None,
    MISSING,
    -1.0,
    math.nan,
    math.inf,
    "windy",
    np.ma.masked,
])
def test_an_unusable_transport_wind_yields_missing(bad):
    assert is_missing(ventilation_rate(327.0, bad))


def test_two_vendored_sentinels_do_not_multiply_into_a_plausible_number():
    """The failure this guards against, and it is not hypothetical.

    The fire panel is fed the *vendored* profile, whose absent-value sentinel is
    ``-9999.0`` -- and ``sharpmod``'s own ``is_missing`` returns ``False`` for
    that, because its ``MISSING`` is ``numpy.ma.masked``. Two of them multiplied
    by the knot conversion come to about 51 million, which would read as an
    extraordinarily well-ventilated day rather than as no answer.
    """
    naive = VENDORED_MISSING * VENDORED_MISSING * KNOTS_TO_MS
    assert naive > 5e7, "sanity: the wrong answer really is large"

    result = ventilation_rate(VENDORED_MISSING, VENDORED_MISSING)

    assert is_missing(result)


@pytest.mark.parametrize("factor", ["height", "wind"])
def test_a_single_vendored_sentinel_is_enough_to_withhold_the_rate(factor):
    height = VENDORED_MISSING if factor == "height" else 1500.0
    wind = VENDORED_MISSING if factor == "wind" else 20.0

    assert is_missing(ventilation_rate(height, wind))


def test_a_masked_array_element_is_treated_as_absent():
    """Profile values arrive as masked arrays, so a masked entry must not leak."""
    masked = np.ma.masked_array([1500.0], mask=[True])[0]

    assert is_missing(ventilation_rate(masked, 20.0))


def test_an_unmasked_array_element_is_used():
    usable = np.ma.masked_array([1500.0], mask=[False])[0]

    assert ventilation_rate(usable, 20.0) == pytest.approx(
        1500.0 * 20.0 * KNOTS_TO_MS)


def test_the_rate_grows_with_both_factors():
    """Monotonic in each argument, which is the whole point of the product."""
    base = ventilation_rate(1000.0, 10.0)

    assert ventilation_rate(2000.0, 10.0) > base
    assert ventilation_rate(1000.0, 20.0) > base
