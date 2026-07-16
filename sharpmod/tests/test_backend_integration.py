"""Repository-owned call sites delegate through the backend facade."""

from __future__ import annotations

import os

import numpy as np
import pytest

from sharpmod import backends
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.sharptab import interp
from sharpmod.sharptab.profile import Profile
from sharpmod.tools import era5_extract


def _profile_columns():
    return (
        np.array([1000.0, 900.0, 800.0]),
        np.array([100.0, 1000.0, 2000.0]),
        np.array([20.0, 12.0, 5.0]),
        np.array([15.0, 8.0, 0.0]),
        np.array([180.0, 225.0, 270.0]),
        np.array([10.0, 20.0, 30.0]),
    )


def test_profile_component_creation_delegates_to_backend(monkeypatch):
    seen = {}

    def fake_wind_to_components(direction, speed, *, missing=None):
        seen["direction"] = direction
        seen["speed"] = speed
        seen["missing"] = missing
        return np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0])

    monkeypatch.setattr(backends, "wind_to_components", fake_wind_to_components)

    profile = Profile(*_profile_columns())

    np.testing.assert_array_equal(profile.u, [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(profile.v, [4.0, 5.0, 6.0])
    np.testing.assert_array_equal(seen["direction"], _profile_columns()[4])
    np.testing.assert_array_equal(seen["speed"], _profile_columns()[5])
    assert seen["missing"] is None


def test_generic_height_interpolation_delegates_to_backend(monkeypatch):
    seen = {}

    def fake_interpolate(target, coordinate, values, *, missing=None, log=False):
        seen.update(
            target=target,
            coordinate=coordinate,
            values=values,
            missing=missing,
            log=log,
        )
        return 42.5

    monkeypatch.setattr(backends, "interpolate_1d", fake_interpolate)

    result = interp.generic_interp_hght(
        500.0, np.array([0.0, 1000.0]), np.array([10.0, 20.0]), log=True)

    assert result == 42.5
    assert seen["target"] == 500.0
    np.testing.assert_array_equal(seen["coordinate"], [0.0, 1000.0])
    np.testing.assert_array_equal(seen["values"], [10.0, 20.0])
    assert seen["missing"] is None
    assert seen["log"] is True


def test_generic_pressure_interpolation_delegates_to_backend(monkeypatch):
    calls = []

    def fake_interpolate(target, coordinate, values, *, missing=None, log=False):
        calls.append((target, coordinate, values, missing, log))
        return 17.0

    monkeypatch.setattr(backends, "interpolate_1d", fake_interpolate)

    result = interp.generic_interp_pres(
        np.log10(925.0),
        np.log10(np.array([1000.0, 850.0])),
        np.array([20.0, 10.0]),
    )

    assert result == 17.0
    assert len(calls) == 1
    assert calls[0][3:] == (None, False)


def test_extractor_conversion_delegates_but_keeps_unit_policy(monkeypatch):
    seen = {}

    def fake_components_to_wind(u, v, *, missing=None):
        seen.update(u=u, v=v, missing=missing)
        return np.array([180.0, 270.0]), np.array([10.0, 20.0])

    monkeypatch.setattr(
        backends, "components_to_wind", fake_components_to_wind)

    direction, speed_knots = era5_extract.uv_to_dir_spd(
        np.array([1.0, 2.0]), np.array([3.0, 4.0]))

    np.testing.assert_array_equal(direction, [180.0, 270.0])
    np.testing.assert_allclose(speed_knots, [19.4384449, 38.8768898])
    np.testing.assert_array_equal(seen["u"], [1.0, 2.0])
    np.testing.assert_array_equal(seen["v"], [3.0, 4.0])
    assert seen["missing"] is None


def test_forced_rust_selector_drives_real_repository_call_sites(monkeypatch):
    try:
        import sharpmod_rs  # noqa: F401
    except (ImportError, OSError) as exc:
        if os.environ.get("SHARPMOD_BACKEND", "auto").strip().lower() == "rust":
            pytest.fail(
                "forced-Rust integration test could not import sharpmod_rs: "
                f"{type(exc).__name__}: {exc}",
                pytrace=False,
            )
        pytest.skip("optional sharpmod_rs extension is not installed")

    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    backends.reset_backend_cache()
    try:
        info = backends.backend_info()
        assert info["requested_backend"] == "rust"
        assert info["active_backend"] == "rust"
        assert info["fallback_reason"] is None

        python = PythonBackend()
        columns = _profile_columns()
        expected_u, expected_v = python.wind_to_components(
            columns[4], columns[5])
        profile = Profile(*columns)
        np.testing.assert_allclose(profile.u, expected_u, rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(profile.v, expected_v, rtol=1e-12, atol=1e-12)

        interpolated = interp.generic_interp_hght(
            500.0,
            np.array([0.0, 1000.0]),
            np.array([10.0, 20.0]),
        )
        assert interpolated == pytest.approx(15.0)

        u = np.array([0.0, -10.0, 10.0])
        v = np.array([-10.0, 0.0, 0.0])
        expected_direction, expected_speed = python.components_to_wind(u, v)
        direction, speed_knots = era5_extract.uv_to_dir_spd(u, v)
        np.testing.assert_allclose(
            direction, expected_direction, rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(
            speed_knots,
            np.asarray(expected_speed) * 1.94384449,
            rtol=1e-12,
            atol=1e-12,
        )
    finally:
        backends.reset_backend_cache()
