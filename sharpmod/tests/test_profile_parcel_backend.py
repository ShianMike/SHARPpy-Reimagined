"""Contract and SharpTab integration tests for the parcel workspace."""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from sharpmod import backends
from sharpmod.backends.protocol import ParcelDiagnostics, ParcelWorkspace
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.sharptab import derived, parcels, params, profile
from sharpmod.sharptab.constants import is_missing


def _columns():
    pres = np.geomspace(1000.0, 100.0, 80)
    hght = 44330.0 * (1.0 - (pres / 1013.25) ** 0.1903)
    tropospheric_height = np.minimum(hght, 12000.0)
    tmpc = (
        31.0
        - (7.4 * tropospheric_height / 1000.0)
        + (1.5 * np.maximum(hght - 12000.0, 0.0) / 1000.0)
    )
    dwpc = tmpc - (4.0 + hght / 2500.0)
    return pres, hght, tmpc, dwpc


def _profile():
    pres, hght, tmpc, dwpc = _columns()
    wdir = np.linspace(160.0, 250.0, pres.size)
    wspd = np.linspace(8.0, 65.0, pres.size)
    return profile.create_profile(
        pres, hght, tmpc, dwpc, wdir, wspd,
    )


def test_python_workspace_returns_typed_standard_parcels():
    result = PythonBackend().profile_parcels(*_columns())

    assert isinstance(result, ParcelWorkspace)
    assert isinstance(result.surface, ParcelDiagnostics)
    assert result.parcel("sb") is result.surface
    assert result.parcel("mu") is result.most_unstable
    assert result.parcel("ml") is result.mixed_layer
    assert result.surface.start_pressure == 1000.0
    assert result.most_unstable.cape >= result.surface.cape
    assert np.isfinite(result.mixed_layer.lcl_height)


def test_workspace_normalizes_masks_and_missing_sentinel():
    pres, hght, tmpc, dwpc = _columns()
    dwpc = ma.array(dwpc, mask=np.arange(dwpc.size) == 30)
    dwpc = dwpc.copy()
    dwpc[45] = -9999.0

    result = PythonBackend().profile_parcels(
        pres, hght, tmpc, dwpc,
    )

    assert np.isfinite(result.surface.cape)
    assert np.isfinite(result.most_unstable.start_pressure)
    assert np.isfinite(result.mixed_layer.cin)


def test_shallow_workspace_preserves_typed_shape_with_missing_results():
    pres, hght, tmpc, dwpc = _columns()

    result = PythonBackend().profile_parcels(
        pres[:2], hght[:2], tmpc[:2], dwpc[:2],
    )

    assert isinstance(result, ParcelWorkspace)
    assert np.isnan(result.surface.cape)
    assert np.isnan(result.most_unstable.lcl_pressure)
    assert np.isnan(result.mixed_layer.start_pressure)


def test_sharptab_consumers_reuse_one_cached_workspace(monkeypatch):
    prof = _profile()
    original = backends.profile_parcels
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backends, "profile_parcels", counted)
    first = parcels.profile_parcels(prof)
    second = parcels.profile_parcels(prof)
    ncape, _ncin = derived.normalized_cape_cin(prof)
    vgp = derived.vorticity_generation_parameter(prof)
    cape_6km = params.layer_cape_agl(prof, 0.0, 6000.0)

    assert calls == 1
    assert first is second
    assert first.surface is parcels.parcel(prof, "surface")
    assert not is_missing(ncape)
    assert not is_missing(vgp)
    assert not is_missing(cape_6km)


def test_sparse_six_km_cape_uses_reference_integral(monkeypatch):
    hght = np.arange(0.0, 14000.0, 1000.0)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = 31.0 - (7.0 * hght / 1000.0)
    dwpc = tmpc - 6.0
    prof = profile.create_profile(
        pres,
        hght,
        tmpc,
        dwpc,
        np.full(hght.size, 220.0),
        np.full(hght.size, 25.0),
    )

    def cached_parcel_must_not_run(*_args, **_kwargs):
        raise AssertionError("coarse profiles must not use cached native 6CAPE")

    monkeypatch.setattr(parcels, "parcel", cached_parcel_must_not_run)
    monkeypatch.setattr(params, "_layer_cape", lambda *_args: 42.0)

    assert params.layer_cape_agl(prof, 0.0, 6000.0) == 42.0


def test_sharptab_falls_back_when_workspace_backend_fails(monkeypatch):
    prof = _profile()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("native workspace unavailable")

    monkeypatch.setattr(backends, "profile_parcels", unavailable)

    assert parcels.profile_parcels(prof) is None
    assert not is_missing(derived.normalized_cape_cin(prof)[0])
    assert not is_missing(derived.vorticity_generation_parameter(prof))
    assert not is_missing(params.layer_cape_agl(prof, 0.0, 6000.0))


def test_failed_convective_oracle_is_cached(monkeypatch):
    from sharppy.sharptab import profile as sp_profile

    prof = _profile()
    original = sp_profile.create_profile
    calls = 0

    def fail_convective(*args, **kwargs):
        nonlocal calls
        if kwargs.get("profile") == "convective":
            calls += 1
            raise RuntimeError("convective oracle unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(sp_profile, "create_profile", fail_convective)

    assert derived._convective_oracle_profile(prof) is None
    assert derived._convective_oracle_profile(prof) is None
    assert calls == 1
