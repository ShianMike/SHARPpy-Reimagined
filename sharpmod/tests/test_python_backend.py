"""Authoritative numerical and sounding-row behavior of the Python backend."""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod.backends.python_backend import PythonBackend
from sharpmod.backends._common import (
    prepare_broadcast_pair,
    prepare_interpolation,
    restore_pair,
)
from sharpmod.sharptab.constants import MISSING


@pytest.fixture
def backend():
    return PythonBackend()


def test_common_float64_inputs_keep_zero_copy_views_when_no_rewrite_is_needed():
    left = np.arange(8.0, dtype=np.float64)
    right = left + 1.0
    target = left + 0.5

    left_out, right_out, _ = prepare_broadcast_pair(left, right)
    target_out, coordinate_out, values_out, _ = prepare_interpolation(
        target, left, right)

    assert np.shares_memory(left, left_out)
    assert np.shares_memory(right, right_out)
    assert np.shares_memory(target, target_out)
    assert np.shares_memory(left, coordinate_out)
    assert np.shares_memory(right, values_out)


def test_common_missing_rewrite_copies_without_mutating_callers():
    original = np.array([1.0, -9999.0], dtype=np.float64)

    left_out, right_out, _ = prepare_broadcast_pair(
        original, original, missing=-9999.0)

    np.testing.assert_array_equal(original, [1.0, -9999.0])
    assert not np.shares_memory(original, left_out)
    assert not np.shares_memory(original, right_out)
    assert np.isnan(left_out[1]) and np.isnan(right_out[1])


def test_common_restore_reuses_owned_backend_output_storage():
    """Wrapping a fresh backend result must not copy it a second time."""
    left = np.array([1.0, np.nan, 3.0], dtype=np.float64)
    right = np.array([4.0, 5.0, 6.0], dtype=np.float64)

    left_out, right_out = restore_pair(left, right, left.shape)

    assert np.shares_memory(ma.getdata(left_out), left)
    assert np.shares_memory(ma.getdata(right_out), right)
    assert ma.getmaskarray(left_out).tolist() == [False, True, False]


def test_wind_to_components_uses_meteorological_convention(backend):
    direction = np.array([0.0, 90.0, 180.0, 270.0, 360.0])
    speed = np.full(5, 10.0)

    u, v = backend.wind_to_components(direction, speed)

    np.testing.assert_allclose(u, [0.0, -10.0, 0.0, 10.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(v, [-10.0, 0.0, 10.0, 0.0, -10.0], atol=1e-12)


def test_wind_to_components_preserves_masks_and_optional_sentinel(backend):
    direction = ma.array([270.0, 180.0, 90.0], mask=[False, True, False])
    speed = np.array([10.0, 20.0, -9999.0])

    u, v = backend.wind_to_components(direction, speed, missing=-9999.0)

    np.testing.assert_allclose(u.compressed(), [10.0], atol=1e-12)
    np.testing.assert_array_equal(ma.getmaskarray(u), [False, True, True])
    np.testing.assert_array_equal(ma.getmaskarray(v), [False, True, True])


def test_wind_to_components_supports_numpy_broadcasting(backend):
    u, v = backend.wind_to_components(np.array([0.0, 90.0]), 10.0)

    np.testing.assert_allclose(u, [0.0, -10.0], atol=1e-12)
    np.testing.assert_allclose(v, [-10.0, 0.0], atol=1e-12)


def test_wind_conversion_rejects_nonbroadcastable_shapes(backend):
    with pytest.raises(ValueError, match="broadcast"):
        backend.wind_to_components(np.ones(2), np.ones(3))
    with pytest.raises(ValueError, match="broadcast"):
        backend.components_to_wind(np.ones(2), np.ones(3))


def test_components_to_wind_handles_calm_and_extreme_directions(backend):
    direction = np.array([-450.0, -90.0, 0.0, 360.0, 810.0])
    speed = np.array([5.0, 10.0, 0.0, 15.0, 20.0])
    u, v = backend.wind_to_components(direction, speed)

    out_direction, out_speed = backend.components_to_wind(u, v)

    np.testing.assert_allclose(out_speed, speed, atol=1e-12)
    np.testing.assert_allclose(out_direction[[0, 1, 3, 4]], [270, 270, 0, 90], atol=1e-12)
    # The forward formula produces signed negative zeros for a calm 0-degree
    # wind, so the authoritative atan2 formula resolves the round trip to 90.
    assert out_direction[2] == 90.0

    positive_calm_direction, positive_calm_speed = \
        backend.components_to_wind(0.0, 0.0)
    assert positive_calm_direction == 270.0
    assert positive_calm_speed == 0.0


def test_scalar_wind_inputs_return_python_scalars_or_missing(backend):
    u, v = backend.wind_to_components(270.0, 12.0)
    assert isinstance(u, float) and isinstance(v, float)
    assert u == pytest.approx(12.0)

    missing_u, missing_v = backend.wind_to_components(np.nan, 12.0)
    assert missing_u is MISSING
    assert missing_v is MISSING


def test_interpolation_sorts_descending_coordinates_and_preserves_target_shape(backend):
    target = np.array([[1.5, 0.5], [2.0, -1.0]])
    result = backend.interpolate_1d(
        target,
        np.array([2.0, 1.0, 0.0]),
        np.array([20.0, 10.0, 0.0]),
    )

    assert result.shape == target.shape
    np.testing.assert_allclose(result[:1], [[15.0, 5.0]])
    assert result[1, 0] == 20.0
    assert ma.getmaskarray(result)[1, 1]


def test_interpolation_duplicate_coordinate_matches_numpy(backend):
    coordinate = np.array([2.0, 1.0, 1.0, 0.0])
    values = np.array([30.0, 10.0, 20.0, 0.0])
    targets = np.array([0.999, 1.0, 1.001])

    result = backend.interpolate_1d(targets, coordinate, values)
    expected = np.interp(
        targets,
        np.array([0.0, 1.0, 1.0, 2.0]),
        np.array([0.0, 10.0, 20.0, 30.0]),
        left=np.nan,
        right=np.nan,
    )

    np.testing.assert_allclose(result, expected, rtol=0.0, atol=1e-12)
    assert result[1] == 20.0


def test_interpolation_filters_masks_nan_inf_and_sentinel(backend):
    coordinate = ma.array(
        [0.0, 1.0, 2.0, 3.0, 4.0, -9999.0],
        mask=[False, True, False, False, False, False],
    )
    values = np.array([0.0, 100.0, 20.0, np.nan, np.inf, 99.0])

    result = backend.interpolate_1d(
        np.array([1.0, 2.0]), coordinate, values, missing=-9999.0)

    np.testing.assert_allclose(result, [10.0, 20.0])


def test_interpolation_empty_and_singleton_inputs_are_missing(backend):
    assert backend.interpolate_1d(1.0, [], []) is MISSING
    assert backend.interpolate_1d(1.0, [1.0], [2.0]) is MISSING

    array_result = backend.interpolate_1d(np.array([1.0, 2.0]), [1.0], [2.0])
    np.testing.assert_array_equal(ma.getmaskarray(array_result), [True, True])


def test_interpolation_log_output_and_scalar_types(backend):
    result = backend.interpolate_1d(1.0, [0.0, 2.0], [1.0, 3.0], log=True)

    assert isinstance(result, float)
    assert result == pytest.approx(100.0)


def test_interpolation_rejects_mismatched_lengths(backend):
    with pytest.raises(ValueError, match="same shape"):
        backend.interpolate_1d(1.0, [0.0, 1.0], [0.0])


def test_pressure_sort_dedup_indices_is_stable_and_descending(backend):
    pressure = np.array([850.0, np.nan, 1000.0, 850.0, -9999.0, 700.0, 0.0])

    indices = backend.pressure_sort_dedup_indices(pressure)

    np.testing.assert_array_equal(indices, np.array([2, 0, 5], dtype=np.intp))


def test_pressure_sort_dedup_indices_handles_empty_and_single_inputs(backend):
    np.testing.assert_array_equal(
        backend.pressure_sort_dedup_indices([]), np.array([], dtype=np.intp))
    np.testing.assert_array_equal(
        backend.pressure_sort_dedup_indices([500.0]), np.array([0], dtype=np.intp))


def test_pressure_sort_dedup_indices_allows_no_explicit_sentinel(backend):
    np.testing.assert_array_equal(
        backend.pressure_sort_dedup_indices(
            [1000.0, 925.0, 925.0, 850.0], missing=None),
        np.array([0, 1, 3], dtype=np.intp),
    )


def test_basic_sounding_qc_accepts_valid_profile_and_direction_360(backend):
    result = backend.basic_sounding_qc(
        [1000.0, 900.0, 800.0],
        [100.0, 1000.0, 2000.0],
        [20.0, 12.0, 5.0],
        [15.0, 8.0, 0.0],
        [0.0, 180.0, 360.0],
        [0.0, 20.0, 30.0],
    )

    assert result.valid is True
    assert result.valid_level_count == 3
    assert result.issues == ()


def test_basic_sounding_qc_reports_deterministic_issue_codes(backend):
    result = backend.basic_sounding_qc(
        [1000.0, -9999.0, 1000.0],
        [100.0, -9999.0, 50.0],
        [20.0, -274.0, 5.0],
        [15.0, -274.0, 0.0],
        [0.0, 361.0, 180.0],
        [0.0, -1.0, 30.0],
    )

    assert result.valid is False
    assert result.valid_level_count == 2
    assert result.issues == (
        "missing_pressure",
        "pressure_not_strictly_decreasing",
        "height_not_strictly_increasing",
        "temperature_below_absolute_zero",
        "dewpoint_below_absolute_zero",
        "wind_direction_out_of_range",
        "negative_wind_speed",
    )


def test_basic_sounding_qc_rejects_mismatched_lengths(backend):
    with pytest.raises(ValueError, match="same length"):
        backend.basic_sounding_qc(
            [1000.0, 900.0], [0.0], [20.0, 10.0], [15.0, 5.0],
            [180.0, 190.0], [10.0, 20.0],
        )


def test_basic_sounding_qc_allows_no_explicit_sentinel(backend):
    result = backend.basic_sounding_qc(
        [1000.0, -9999.0, 900.0],
        [100.0, 500.0, 1000.0],
        [20.0, 15.0, 10.0],
        [15.0, 10.0, 5.0],
        [180.0, 190.0, 200.0],
        [10.0, 20.0, 30.0],
        missing=None,
    )

    assert "missing_pressure" not in result.issues
    assert "nonpositive_pressure" in result.issues


def test_parse_sounding_rows_accepts_header_comments_commas_and_whitespace(backend):
    text = """
    # pressure profile
    pres,hght,tmpc,dwpc,wdir,wspd
    1000,100,20,15,180,10
    900 1000 12 8 200 20
    """

    columns = backend.parse_sounding_rows(text)

    assert len(columns) == 6
    for column in columns:
        assert column.dtype == np.float64
        assert column.shape == (2,)
    np.testing.assert_array_equal(columns[0], [1000.0, 900.0])
    np.testing.assert_array_equal(columns[5], [10.0, 20.0])


def test_parse_sounding_rows_normalizes_explicit_missing_values(backend):
    columns = backend.parse_sounding_rows(
        "1000,100,20,,nan,inf\n900,1000,12,8,180,-9999")

    assert columns[3][0] == -9999.0
    assert columns[4][0] == -9999.0
    assert columns[5][0] == -9999.0
    assert columns[5][1] == -9999.0


def test_parse_sounding_rows_uses_nan_when_missing_is_none(backend):
    columns = backend.parse_sounding_rows(
        "1000,100,20,,nan,inf", missing=None)

    assert np.isnan(columns[3][0])
    assert np.isnan(columns[4][0])
    assert np.isnan(columns[5][0])


def test_parse_sounding_rows_uses_python_float_underscore_rules(backend):
    columns = backend.parse_sounding_rows(
        "1_000,1_0,2_0.5,.1_5,1_8_0,1e1_0")
    np.testing.assert_array_equal(
        [column[0] for column in columns],
        [1000.0, 10.0, 20.5, 0.15, 180.0, 1.0e10],
    )

    with pytest.raises(ValueError, match="nonnumeric value '1__000'"):
        backend.parse_sounding_rows("1__000,100,20,15,180,10")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no sounding rows"),
        ("1000,100,20", "line 1.*expected 6 columns"),
        ("1000,100,twenty,15,180,10", "line 1.*nonnumeric"),
    ],
)
def test_parse_sounding_rows_rejects_malformed_input(backend, text, message):
    with pytest.raises(ValueError, match=message):
        backend.parse_sounding_rows(text)
