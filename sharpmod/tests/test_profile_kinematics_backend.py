"""Contract and SharpTab integration tests for the profile workspace."""

from __future__ import annotations

import warnings

import numpy as np
import numpy.ma as ma
import pytest

from sharpmod import backends
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.sharptab import interp, profile, winds
from sharpmod.sharptab.constants import is_missing


_TOPS = (500.0, 1000.0, 3000.0, 4000.0, 6000.0)


def _columns():
    hght = np.array(
        [0.0, 250.0, 500.0, 1000.0, 2000.0, 3000.0, 4000.0, 6000.0],
    )
    pres = 1000.0 * np.exp(-hght / 8000.0)
    u = np.array([0.0, 2.0, 7.0, 12.0, 28.0, 42.0, 50.0, 60.0])
    v = np.array([10.0, 14.0, 18.0, 21.0, 19.0, 15.0, 7.0, 0.0])
    return pres, hght, u, v


def _profile():
    pres, hght, u, v = _columns()
    speed = np.hypot(u, v)
    direction = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    tmpc = 25.0 - (6.5 * hght / 1000.0)
    return profile.create_profile(
        pres, hght, tmpc, tmpc - 5.0, direction, speed,
    )


def test_python_workspace_returns_typed_standard_layers():
    pres, hght, u, v = _columns()

    result = PythonBackend().profile_kinematics(
        pres, hght, u, v, _TOPS,
    )

    assert tuple(layer.top_agl for layer in result.layers) == _TOPS
    assert result.layer(1000.0) is result.layers[1]
    assert result.layer(2500.0) is None
    assert np.isfinite(result.storm_motion).all()
    layer_500 = result.layer(500.0)
    assert layer_500.height_shear_u == pytest.approx(u[2] - u[0])
    assert layer_500.height_shear_v == pytest.approx(v[2] - v[0])
    assert layer_500.srh_total == pytest.approx(
        layer_500.srh_positive + layer_500.srh_negative,
    )


def test_workspace_normalizes_masks_and_missing_sentinel():
    pres, hght, u, v = _columns()
    masked_u = ma.array(u, mask=[0, 0, 0, 1, 0, 0, 0, 0])
    sentinel_v = v.copy()
    sentinel_v[4] = -9999.0

    result = PythonBackend().profile_kinematics(
        pres, hght, masked_u, sentinel_v, _TOPS,
    )

    assert len(result.layers) == len(_TOPS)
    assert np.isfinite(result.layer(500.0).mean_u)
    assert np.isfinite(result.layer(500.0).mean_v)


def test_shallow_workspace_preserves_shape_and_marks_unavailable_values():
    pres, hght, u, v = _columns()

    result = PythonBackend().profile_kinematics(
        pres[:3], hght[:3], u[:3], v[:3], _TOPS,
    )

    assert len(result.layers) == len(_TOPS)
    assert np.isnan(result.storm_motion).all()
    assert np.isfinite(result.layer(500.0).height_shear_u)
    assert np.isnan(result.layer(1000.0).top_pressure)


@pytest.mark.parametrize(
    ("columns", "tops", "sfc", "message"),
    [
        (([1000.0], [0.0, 1.0], [0.0], [0.0]), [500.0], 0, "same length"),
        (([1000.0], [0.0], [0.0], [0.0]), [-1.0], 0, "non-negative"),
        (([1000.0], [0.0], [0.0], [0.0]), [500.0], 1, "outside"),
        (([1000.0], [0.0], [0.0], [0.0]), [500.0], True, "integer"),
    ],
)
def test_workspace_rejects_invalid_contract_inputs(columns, tops, sfc, message):
    with pytest.raises(ValueError, match=message):
        PythonBackend().profile_kinematics(
            *columns, tops, sfc=sfc,
        )


def test_sharptab_consumers_reuse_one_cached_workspace(monkeypatch):
    prof = _profile()
    original = backends.profile_kinematics
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backends, "profile_kinematics", counted)
    srh, shear, mean, storm_relative = winds.sfc_500m_kinematics(prof)
    motion = winds.non_parcel_bunkers_motion(prof)
    ptop = interp.pres_at_hght_agl(prof, 6000.0)
    layer_shear = winds.wind_shear(prof, prof.pres[prof.sfc], ptop)
    layer_mean = winds.mean_wind(prof, prof.pres[prof.sfc], ptop)

    assert calls == 1
    assert np.isfinite([srh, shear]).all()
    assert np.isfinite(mean).all()
    assert np.isfinite(storm_relative).all()
    assert np.isfinite(motion).all()
    assert np.isfinite(layer_shear).all()
    assert np.isfinite(layer_mean).all()


def test_sharptab_falls_back_when_workspace_backend_fails(monkeypatch):
    prof = _profile()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("native workspace unavailable")

    monkeypatch.setattr(backends, "profile_kinematics", unavailable)
    ptop = interp.pres_at_hght_agl(prof, 2000.0)
    shear = winds.wind_shear(prof, prof.pres[prof.sfc], ptop)
    motion = winds.non_parcel_bunkers_motion(prof)

    assert not is_missing(shear[0])
    assert not is_missing(motion[0])
    assert np.isfinite(shear).all()
    assert np.isfinite(motion).all()


def test_helicity_rejects_nonfinite_motion_without_runtime_warning():
    prof = _profile()

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = winds.helicity(
            prof,
            0.0,
            1000.0,
            stu=np.inf,
            stv=0.0,
        )

    assert all(is_missing(value) for value in result)


def test_helicity_skips_nonfinite_interior_wind_pair():
    prof = _profile()
    prof.u[3] = np.inf
    prof.v[3] = np.inf
    setattr(prof, "_sharpmod_profile_kinematics", None)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = winds.helicity(
            prof,
            0.0,
            1000.0,
            stu=10.0,
            stv=5.0,
        )

    assert np.isfinite(result).all()
