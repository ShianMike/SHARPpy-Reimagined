"""Runtime integration for backend parcel traces and DCAPE."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sharpmod import backends, render
from sharpmod.backends.protocol import (
    ConvectiveParcelWorkspace,
    DowndraftDiagnostics,
    ParcelAscent,
)
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.io import decoder
from sharpmod.sharptab.accelerated_profile import (
    AcceleratedConvectiveProfile,
)


SAMPLE = (
    Path(__file__).parents[2]
    / "examples"
    / "soundings"
    / "hrrr_point_36.68N_95.66W_f018.npz"
)


def _raw_columns():
    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    return tuple(
        getattr(raw, name)
        for name in ("pres", "hght", "tmpc", "dwpc")
    )


def test_python_extended_workspace_has_typed_traces():
    backend = PythonBackend()
    columns = _raw_columns()

    workspace = backend.profile_convective_parcels(*columns)
    downdraft = backend.profile_dcape(*columns)
    user = backend.lift_parcel(
        *columns,
        850.0,
        18.0,
        14.0,
    )

    assert isinstance(workspace, ConvectiveParcelWorkspace)
    assert isinstance(workspace.forecast, ParcelAscent)
    assert workspace.parcel("eff") is workspace.effective
    assert len(workspace.most_unstable.trace.pressure) > 2
    assert isinstance(downdraft, DowndraftDiagnostics)
    assert np.isfinite(downdraft.cape)
    assert len(downdraft.trace.pressure) == len(
        downdraft.trace.temperature,
    )
    assert isinstance(user, ParcelAscent)
    assert np.isfinite(user.diagnostics.cape)


def test_render_decode_uses_native_profile_without_python_integrators(
    monkeypatch,
):
    monkeypatch.setenv("SHARPMOD_BACKEND", "rust")
    backends.reset_backend_cache()
    backend = backends.backend_info()
    if backend["active_backend"] != "rust":
        pytest.skip(
            f"compatible Rust backend unavailable: {backend['fallback_reason']}"
        )
    from sharppy.sharptab import params as sp_params

    parcel_calls = 0
    dcape_calls = 0
    original_parcel = sp_params.parcelx
    original_dcape = sp_params.dcape

    def parcelx(*args, **kwargs):
        nonlocal parcel_calls
        parcel_calls += 1
        return original_parcel(*args, **kwargs)

    def dcape(*args, **kwargs):
        nonlocal dcape_calls
        dcape_calls += 1
        return original_dcape(*args, **kwargs)

    monkeypatch.setattr(sp_params, "parcelx", parcelx)
    monkeypatch.setattr(sp_params, "dcape", dcape)
    try:
        collection, _ = render.decode(str(SAMPLE))
        prof = collection.getHighlightedProf()
    finally:
        backends.reset_backend_cache()

    assert isinstance(prof, AcceleratedConvectiveProfile)
    assert parcel_calls == 0
    assert dcape_calls == 0
    assert np.isfinite(prof.mupcl.bplus)
    assert np.isfinite(prof.dcape)
    assert len(prof.mupcl.ptrace) > 2
    assert len(prof.dpcl_ptrace) > 2


def test_accelerated_profile_falls_back_to_sharppy(monkeypatch):
    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    from sharppy.sharptab import params as sp_params

    calls = {"parcel": 0, "dcape": 0}
    original_parcel = sp_params.parcelx
    original_dcape = sp_params.dcape

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("backend unavailable")

    def parcelx(*args, **kwargs):
        calls["parcel"] += 1
        return original_parcel(*args, **kwargs)

    def dcape(*args, **kwargs):
        calls["dcape"] += 1
        return original_dcape(*args, **kwargs)

    monkeypatch.setattr(backends, "profile_convective_parcels", unavailable)
    monkeypatch.setattr(backends, "profile_dcape", unavailable)
    monkeypatch.setattr(sp_params, "parcelx", parcelx)
    monkeypatch.setattr(sp_params, "dcape", dcape)

    prof = AcceleratedConvectiveProfile.copy(raw)

    assert np.isfinite(prof.mupcl.bplus)
    assert np.isfinite(prof.dcape)
    assert calls["parcel"] >= 4
    assert calls["dcape"] >= 1
