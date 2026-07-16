"""Numerical and error-contract equivalence for the optional Rust extension."""

from __future__ import annotations

import os

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod import __version__ as sharpmod_version
from sharpmod import backends as backend_facade
from sharpmod.backends.protocol import BACKEND_API_VERSION
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.backends.rust_backend import RustBackend


try:
    import sharpmod_rs
except (ImportError, OSError) as exc:
    requested_backend = os.environ.get(
        "SHARPMOD_BACKEND", "auto").strip().lower()
    if requested_backend == "rust":
        pytest.fail(
            "SHARPMOD_BACKEND=rust requires the sharpmod_rs extension; "
            f"import failed: {type(exc).__name__}: {exc}",
            pytrace=False,
        )
    pytest.skip(
        "optional sharpmod_rs extension is not installed",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def backends():
    return PythonBackend(), RustBackend(sharpmod_rs)


def test_forced_selector_activates_rust_without_fallback(monkeypatch):
    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    backend_facade.reset_backend_cache()
    try:
        info = backend_facade.backend_info()
        assert info["requested_backend"] == "rust"
        assert info["active_backend"] == "rust"
        assert info["rust_installed"] is True
        assert info["rust_version"] == sharpmod_version
        assert info["fallback_reason"] is None
    finally:
        backend_facade.reset_backend_cache()


def _filled(value):
    array = ma.asanyarray(value, dtype=float)
    return np.asarray(array.filled(np.nan), dtype=float)


def _assert_equivalent(python_value, rust_value, *, atol=1e-12):
    assert np.shape(python_value) == np.shape(rust_value)
    np.testing.assert_array_equal(
        ma.getmaskarray(ma.asanyarray(python_value)),
        ma.getmaskarray(ma.asanyarray(rust_value)),
    )
    np.testing.assert_allclose(
        _filled(rust_value),
        _filled(python_value),
        rtol=1e-12,
        atol=atol,
        equal_nan=True,
    )


def _assert_pair_equivalent(python_pair, rust_pair, *, atol=1e-12):
    assert len(python_pair) == len(rust_pair) == 2
    _assert_equivalent(python_pair[0], rust_pair[0], atol=atol)
    _assert_equivalent(python_pair[1], rust_pair[1], atol=atol)


@pytest.mark.parametrize(
    ("direction", "speed", "missing"),
    [
        (np.array([], dtype=float), np.array([], dtype=float), None),
        (0.0, 0.0, None),
        (270.0, 10.0, None),
        (
            np.array([-1080.0, -450.0, -0.0, 360.0, 810.0, 1440.0]),
            np.array([1.0, 5.0, 0.0, 15.0, 25.0, 100.0]),
            None,
        ),
        (
            ma.array([0.0, 90.0, np.nan, 270.0], mask=[0, 1, 0, 0]),
            np.array([10.0, 20.0, 30.0, -9999.0]),
            -9999.0,
        ),
        (np.arange(10.0).reshape(2, 5) * 77.0, 12.5, None),
    ],
)
def test_wind_to_components_equivalence(backends, direction, speed, missing):
    python, rust = backends
    _assert_pair_equivalent(
        python.wind_to_components(direction, speed, missing=missing),
        rust.wind_to_components(direction, speed, missing=missing),
    )


@pytest.mark.parametrize(
    ("u", "v", "missing"),
    [
        (np.array([], dtype=float), np.array([], dtype=float), None),
        (0.0, 0.0, None),
        (-0.0, -0.0, None),
        (
            np.array([0.0, -10.0, 0.0, 10.0, np.nan, -9999.0]),
            np.array([-10.0, 0.0, 10.0, 0.0, 4.0, 6.0]),
            -9999.0,
        ),
        (
            ma.array([1.0, 2.0, 3.0], mask=[0, 1, 0]),
            np.array([4.0, 5.0, np.inf]),
            None,
        ),
    ],
)
def test_components_to_wind_equivalence(backends, u, v, missing):
    python, rust = backends
    _assert_pair_equivalent(
        python.components_to_wind(u, v, missing=missing),
        rust.components_to_wind(u, v, missing=missing),
    )


@pytest.mark.parametrize(
    ("target", "coordinate", "values", "missing", "log"),
    [
        (1.0, [], [], None, False),
        (np.array([0.0, 1.0]), [0.0], [5.0], None, False),
        (
            np.array([[1000.0, 925.0], [850.0, 700.0]]),
            [700.0, 850.0, 925.0, 1000.0],
            [7.0, 8.5, 9.25, 10.0],
            None,
            False,
        ),
        (
            np.array([0.999, 1.0, 1.001]),
            [2.0, 1.0, 1.0, 0.0],
            [30.0, 10.0, 20.0, 0.0],
            None,
            False,
        ),
        (
            np.array([-9999.0, 0.5, 2.5, np.nan]),
            ma.array([0.0, 1.0, 2.0, 3.0], mask=[0, 1, 0, 0]),
            [0.0, 10.0, np.inf, 30.0],
            -9999.0,
            False,
        ),
        (np.array([0.0, 0.5, 1.0]), [0.0, 1.0], [2.0, 4.0], None, True),
    ],
)
def test_interpolation_equivalence(
    backends, target, coordinate, values, missing, log,
):
    python, rust = backends
    _assert_equivalent(
        python.interpolate_1d(
            target, coordinate, values, missing=missing, log=log),
        rust.interpolate_1d(
            target, coordinate, values, missing=missing, log=log),
        atol=1e-10,
    )


def test_scalar_log_interpolation_matches_legacy_power_exactly(backends):
    """Both adapters preserve the legacy scalar pressure-rounding path."""
    python, rust = backends
    coordinate = np.array([0.0, 6000.0])
    values = np.log10(np.array([992.0, 468.58762031908657]))
    expected = float(10.0 ** np.asarray(np.interp(
        6000.0, coordinate, values,
    ))[()])

    assert python.interpolate_1d(
        6000.0, coordinate, values, log=True,
    ) == expected
    assert rust.interpolate_1d(
        6000.0, coordinate, values, log=True,
    ) == expected


def test_large_vector_equivalence(backends):
    python, rust = backends
    size = 100_000
    direction = (np.arange(size, dtype=float) * 11.25) % 1080.0
    speed = 5.0 + (np.arange(size, dtype=float) % 75.0)
    _assert_pair_equivalent(
        python.wind_to_components(direction, speed),
        rust.wind_to_components(direction, speed),
        atol=1e-10,
    )

    coordinate = np.linspace(0.0, 20_000.0, size)
    values = np.sin(coordinate / 1000.0)
    target = coordinate[:-1] + np.diff(coordinate) / 2.0
    _assert_equivalent(
        python.interpolate_1d(target, coordinate, values),
        rust.interpolate_1d(target, coordinate, values),
        atol=1e-12,
    )


def test_large_profile_batch_wind_equivalence(backends):
    python, rust = backends
    shape = (2_048, 128)
    size = shape[0] * shape[1]
    direction = ((np.arange(size, dtype=float) * 11.25) % 1080.0).reshape(shape)
    speed = (5.0 + (np.arange(size, dtype=float) % 75.0)).reshape(shape)

    python_components = python.wind_to_components(direction, speed)
    rust_components = rust.wind_to_components(direction, speed)
    _assert_pair_equivalent(python_components, rust_components, atol=1e-10)
    _assert_pair_equivalent(
        python.components_to_wind(*python_components),
        rust.components_to_wind(*rust_components),
        atol=1e-10,
    )


@pytest.mark.parametrize(
    "columns",
    [
        (
            [1000.0, 900.0, 800.0],
            [100.0, 1000.0, 2000.0],
            [20.0, 12.0, 5.0],
            [15.0, 8.0, 0.0],
            [0.0, 180.0, 360.0],
            [0.0, 20.0, 30.0],
        ),
        (
            [1000.0, -9999.0, 1000.0],
            [100.0, -9999.0, 50.0],
            [20.0, -274.0, 5.0],
            [15.0, -274.0, 0.0],
            [0.0, 361.0, 180.0],
            [0.0, -1.0, 30.0],
        ),
        ([], [], [], [], [], []),
    ],
)
def test_quality_control_equivalence(backends, columns):
    python, rust = backends
    assert python.basic_sounding_qc(*columns) == rust.basic_sounding_qc(*columns)


def test_quality_control_none_disables_the_explicit_sentinel(backends):
    python, rust = backends
    columns = (
        [1000.0, -9999.0, 900.0],
        [100.0, 500.0, 1000.0],
        [20.0, 15.0, 10.0],
        [15.0, 10.0, 5.0],
        [180.0, 190.0, 200.0],
        [10.0, 20.0, 30.0],
    )

    python_result = python.basic_sounding_qc(*columns, missing=None)
    rust_result = rust.basic_sounding_qc(*columns, missing=None)

    assert rust_result == python_result
    assert "missing_pressure" not in python_result.issues
    assert "nonpositive_pressure" in python_result.issues


@pytest.mark.parametrize(
    "pressure",
    [
        [],
        [500.0],
        [850.0, np.nan, 1000.0, 850.0, -9999.0, 700.0, 0.0],
        [700.0, 850.0, 925.0, 1000.0],
    ],
)
def test_pressure_sort_dedup_equivalence(backends, pressure):
    python, rust = backends
    np.testing.assert_array_equal(
        rust.pressure_sort_dedup_indices(pressure),
        python.pressure_sort_dedup_indices(pressure),
    )


def test_pressure_sort_dedup_none_disables_the_explicit_sentinel(backends):
    python, rust = backends
    pressure = [1000.0, 925.0, 925.0, 850.0]

    np.testing.assert_array_equal(
        rust.pressure_sort_dedup_indices(pressure, missing=None),
        python.pressure_sort_dedup_indices(pressure, missing=None),
    )


@pytest.mark.parametrize(
    "text",
    [
        "pres,hght,tmpc,dwpc,wdir,wspd\n1000,100,20,15,180,10",
        "# comment\n1000 100 20 15 180 10\n900 1000 12 8 200 20",
        "1000,100,20,,nan,inf\n900,1000,12,8,180,-9999",
        "1000,100,20,15,180,10\n1000,110,19,14,185,11",
        "1_000,1_0,2_0.5,1_5,1_8_0,1_0",
        "١٠٠٠,١٠٠,٢٠,١٥,١٨٠,١٠",
        "\x1f# comment\x1f\n\x1f1000\x1f100\x1f20\x1f15\x1f180\x1f10\x1f",
        "\x1f1000\x1f,\x1f100\x1f,20,15,180,\x1f10\x1f",
    ],
)
def test_parser_equivalence(backends, text):
    python, rust = backends
    python_columns = python.parse_sounding_rows(text)
    rust_columns = rust.parse_sounding_rows(text)
    assert len(python_columns) == len(rust_columns) == 6
    for python_column, rust_column in zip(python_columns, rust_columns):
        np.testing.assert_array_equal(rust_column, python_column)


def test_parser_none_normalizes_missing_values_to_nan(backends):
    python, rust = backends
    text = "1000,100,20,,nan,inf\n900,1000,12,8,180,10"

    python_columns = python.parse_sounding_rows(text, missing=None)
    rust_columns = rust.parse_sounding_rows(text, missing=None)

    for python_column, rust_column in zip(python_columns, rust_columns):
        np.testing.assert_allclose(
            rust_column, python_column, rtol=0.0, atol=0.0, equal_nan=True)
    assert np.isnan(python_columns[3][0])
    assert np.isnan(python_columns[4][0])
    assert np.isnan(python_columns[5][0])


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_parser_matches_python_splitlines_separators(backends, separator):
    python, rust = backends
    text = (
        "1000,100,20,15,180,10"
        + separator
        + "900,1000,12,8,200,20"
    )

    python_columns = python.parse_sounding_rows(text)
    rust_columns = rust.parse_sounding_rows(text)

    for python_column, rust_column in zip(python_columns, rust_columns):
        np.testing.assert_array_equal(rust_column, python_column)


@pytest.mark.parametrize(
    ("text", "error_type"),
    [
        ("", ValueError),
        ("1000,100,20", ValueError),
        ("1000,100,twenty,15,180,10", ValueError),
        ("1__000,100,20,15,180,10", ValueError),
        ("1000,100,20,15,180,10\npres,hght,tmpc,dwpc,wdir,wspd", ValueError),
        (123, TypeError),
    ],
)
def test_parser_error_equivalence(backends, text, error_type):
    python, rust = backends
    with pytest.raises(error_type) as python_error:
        python.parse_sounding_rows(text)
    with pytest.raises(error_type) as rust_error:
        rust.parse_sounding_rows(text)
    assert str(rust_error.value) == str(python_error.value)


def test_mismatched_lengths_raise_in_both_backends(backends):
    python, rust = backends
    for backend in (python, rust):
        with pytest.raises(ValueError):
            backend.wind_to_components([0.0, 90.0], [10.0, 20.0, 30.0])
        with pytest.raises(ValueError):
            backend.interpolate_1d(0.5, [0.0, 1.0], [0.0])
        with pytest.raises(ValueError):
            backend.basic_sounding_qc(
                [1000.0, 900.0], [100.0], [20.0, 10.0], [15.0, 5.0],
                [180.0, 190.0], [10.0, 20.0],
            )


def test_raw_extension_reports_version_and_value_errors():
    assert sharpmod_rs.__version__ == sharpmod_version
    assert sharpmod_rs.__backend_api_version__ == BACKEND_API_VERSION
    with pytest.raises(ValueError, match="lengths differ"):
        sharpmod_rs.wind_to_components(
            np.array([0.0, 90.0]), np.array([10.0]), None)
    with pytest.raises(ValueError, match="expected 6 columns"):
        sharpmod_rs.parse_sounding_rows("1000,100,20", -9999.0)


def test_raw_extension_accepts_none_missing_policy():
    columns = tuple(np.array(values, dtype=np.float64) for values in (
        [1000.0, 900.0],
        [100.0, 1000.0],
        [20.0, 10.0],
        [15.0, 5.0],
        [180.0, 200.0],
        [10.0, 20.0],
    ))

    valid, valid_level_count, issues = sharpmod_rs.basic_sounding_qc(
        *columns, None)
    assert valid is True
    assert valid_level_count == 2
    assert issues == []
    np.testing.assert_array_equal(
        sharpmod_rs.pressure_sort_dedup_indices(columns[0], None), [0, 1])

    matrix = sharpmod_rs.parse_sounding_rows(
        "1000,100,20,,nan,inf", None)
    assert np.isnan(matrix[0, 3:]).all()
