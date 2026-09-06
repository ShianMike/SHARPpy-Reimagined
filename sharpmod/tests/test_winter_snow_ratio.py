"""The Kuchera snow-to-liquid ratio.

Neither SHARPpy nor this fork computed a snow ratio anywhere, so the winter
panel could describe the dendritic growth zone five different ways without ever
saying how much snow an inch of liquid would make.

The method is two straight lines meeting at 12:1 at -2 C. What these tests
mostly pin is the asymmetry -- a warm layer removes ratio twice as fast as a
cold layer adds it -- and the difference between a ratio of zero and no ratio at
all, because those two mean opposite things to a forecaster.
"""

from __future__ import annotations

import math

import pytest

from sharpmod.sharptab import winter


#: -2 C, where the two branches meet.
PIVOT_C = winter.KUCHERA_PIVOT_K - winter.ZERO_CELSIUS_K


# --------------------------------------------------------------------------- #
# The two branches
# --------------------------------------------------------------------------- #
def test_the_pivot_temperature_gives_the_base_ratio():
    assert winter.kuchera_snow_liquid_ratio(PIVOT_C) == pytest.approx(
        winter.KUCHERA_BASE_RATIO)


def test_the_pivot_is_two_degrees_below_freezing():
    assert PIVOT_C == pytest.approx(-1.99, abs=0.01)


def test_a_colder_column_gains_ratio_one_for_one():
    """Ten degrees colder than the pivot is ten more inches of snow."""
    ratio = winter.kuchera_snow_liquid_ratio(PIVOT_C - 10.0)

    assert ratio == pytest.approx(winter.KUCHERA_BASE_RATIO + 10.0)


def test_a_warmer_column_loses_ratio_twice_as_fast():
    """The asymmetry is the whole point of the method."""
    ratio = winter.kuchera_snow_liquid_ratio(PIVOT_C + 2.0)

    assert ratio == pytest.approx(winter.KUCHERA_BASE_RATIO - 4.0)


def test_the_two_branches_meet_without_a_step():
    just_cold = winter.kuchera_snow_liquid_ratio(PIVOT_C - 1e-6)
    just_warm = winter.kuchera_snow_liquid_ratio(PIVOT_C + 1e-6)

    assert just_cold == pytest.approx(just_warm, abs=1e-4)


@pytest.mark.parametrize("max_temp_c,expected", [
    (-12.0, 22.0),
    (-7.0, 17.0),
    (-2.0, 12.0),
    (0.0, 8.0),
    (2.0, 4.0),
])
def test_the_published_curve_is_reproduced(max_temp_c, expected):
    assert winter.kuchera_snow_liquid_ratio(max_temp_c) == pytest.approx(
        expected, abs=0.05)


def test_the_ratio_rises_monotonically_as_the_column_cools():
    temps = [6.0, 4.0, 2.0, 0.0, -2.0, -6.0, -12.0, -20.0]
    ratios = [winter.kuchera_snow_liquid_ratio(value) for value in temps]

    assert ratios == sorted(ratios)


# --------------------------------------------------------------------------- #
# Zero is an answer; missing is not
# --------------------------------------------------------------------------- #
def test_a_warm_column_accumulates_nothing_rather_than_going_negative():
    """The two-line form runs negative without a floor, which is not a ratio."""
    assert winter.kuchera_snow_liquid_ratio(20.0) == 0.0


def test_the_floor_is_reached_a_few_degrees_above_freezing():
    assert winter.kuchera_snow_liquid_ratio(4.0) == pytest.approx(0.0, abs=0.05)


def test_zero_and_unavailable_are_different_results():
    """Zero means no accumulation; None means the question was not answerable."""
    assert winter.kuchera_snow_liquid_ratio(20.0) == 0.0
    assert winter.kuchera_snow_liquid_ratio(None) is None


@pytest.mark.parametrize("value", [
    None, float("nan"), float("inf"), float("-inf"), "warm", object(),
])
def test_an_unusable_temperature_yields_no_ratio(value):
    assert winter.kuchera_snow_liquid_ratio(value) is None


def test_the_vendored_missing_sentinel_is_not_treated_as_a_temperature():
    """The winter panel is fed the *vendored* profile.

    ``sharpmod``'s own missing value is ``numpy.ma.masked`` and its
    ``is_missing`` returns False for -9999.0, so a value-based test is the only
    thing that catches this. Left unguarded it would report a ratio of about
    ten thousand to one.
    """
    assert winter.kuchera_snow_liquid_ratio(winter.VENDORED_MISSING) is None


# --------------------------------------------------------------------------- #
# Finding the warmest point of the column
# --------------------------------------------------------------------------- #
class _Profile:
    def __init__(self, pres, tmpc):
        import numpy as np

        self.pres = np.ma.masked_invalid(np.ma.asarray(pres, dtype=float))
        self.tmpc = np.ma.masked_invalid(np.ma.asarray(tmpc, dtype=float))


def test_the_warmest_reported_level_is_found():
    profile = _Profile([1000.0, 900.0, 800.0, 700.0],
                       [-5.0, 3.0, -1.0, -20.0])

    assert winter.column_max_temperature_c(profile) == pytest.approx(3.0)


def test_levels_above_the_search_top_are_ignored():
    """A cold upper troposphere must not be mistaken for the melting layer."""
    profile = _Profile([1000.0, 700.0, 300.0, 200.0],
                       [-5.0, -8.0, 40.0, 50.0])

    assert winter.column_max_temperature_c(profile) == pytest.approx(-5.0)


def test_the_search_top_is_configurable():
    profile = _Profile([1000.0, 700.0, 400.0], [-5.0, -8.0, 10.0])

    assert winter.column_max_temperature_c(
        profile, ptop=300.0) == pytest.approx(10.0)


def test_masked_temperatures_are_skipped():
    profile = _Profile([1000.0, 900.0, 800.0],
                       [-5.0, float("nan"), -2.0])

    assert winter.column_max_temperature_c(profile) == pytest.approx(-2.0)


def test_sentinel_temperatures_are_skipped():
    profile = _Profile([1000.0, 900.0],
                       [-5.0, winter.VENDORED_MISSING])

    assert winter.column_max_temperature_c(profile) == pytest.approx(-5.0)


def test_a_profile_with_nothing_usable_reports_no_temperature():
    profile = _Profile([1000.0, 900.0], [float("nan"), float("nan")])

    assert winter.column_max_temperature_c(profile) is None


def test_a_profile_missing_its_arrays_reports_no_temperature():
    assert winter.column_max_temperature_c(object()) is None


def test_a_column_entirely_above_the_search_top_reports_nothing():
    profile = _Profile([300.0, 200.0], [-40.0, -50.0])

    assert winter.column_max_temperature_c(profile) is None


# --------------------------------------------------------------------------- #
# End to end, and formatting
# --------------------------------------------------------------------------- #
def test_a_cold_profile_yields_a_high_ratio():
    profile = _Profile([1000.0, 900.0, 800.0, 700.0],
                       [-12.0, -14.0, -18.0, -25.0])

    assert winter.profile_snow_liquid_ratio(profile) == pytest.approx(22.0,
                                                                     abs=0.05)


def test_a_profile_with_a_melting_layer_yields_a_low_ratio():
    """A warm nose aloft is what the method exists to catch."""
    cold = _Profile([1000.0, 900.0, 800.0], [-6.0, -8.0, -12.0])
    warm_nose = _Profile([1000.0, 900.0, 800.0], [-6.0, 2.0, -12.0])

    assert winter.profile_snow_liquid_ratio(warm_nose) < \
        winter.profile_snow_liquid_ratio(cold)


def test_an_unusable_profile_yields_no_ratio():
    assert winter.profile_snow_liquid_ratio(object()) is None


@pytest.mark.parametrize("ratio,expected", [
    (22.0, "22:1"), (12.0, "12:1"), (0.0, "0:1"), (13.6, "14:1"),
])
def test_a_ratio_is_written_the_way_a_forecaster_writes_it(ratio, expected):
    assert winter.format_snow_liquid_ratio(ratio) == expected


def test_an_absent_ratio_is_shown_as_missing_not_as_zero():
    assert winter.format_snow_liquid_ratio(None) == "M"


def test_the_published_constants_are_what_the_method_specifies():
    assert winter.KUCHERA_PIVOT_K == 271.16
    assert winter.KUCHERA_BASE_RATIO == 12.0
    assert winter.KUCHERA_WARM_FACTOR == 2.0
    assert math.isclose(winter.ZERO_CELSIUS_K, 273.15)
