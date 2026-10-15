"""Runtime integration for backend parcel traces and DCAPE."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    try:
        backend = backends.backend_info()
    except backends.BackendUnavailableError as exc:
        pytest.skip(f"compatible Rust backend unavailable: {exc}")
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


def test_accelerated_profile_uses_one_shared_thermodynamic_call(monkeypatch):
    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    original = backends._profile_thermodynamics_buffers
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backends, "_profile_thermodynamics_buffers", counted)

    prof = AcceleratedConvectiveProfile.copy(raw)

    assert calls == 1
    assert np.isfinite(prof.mupcl.bplus)
    assert np.isfinite(prof.dcape)


def test_zero_shear_marks_undefined_storm_motion_missing():
    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]

    with np.errstate(invalid="ignore", divide="ignore"):
        prof = AcceleratedConvectiveProfile.copy(
            raw, wdir=np.full(raw.pres.shape, 270.0),
            wspd=np.full(raw.pres.shape, 10.0),
        )

    assert len(prof.srwind) == 4
    assert all(np.ma.is_masked(component) for component in prof.srwind)


def test_mmp_uses_the_validated_shear_pairs(monkeypatch):
    from sharppy.sharptab import params as sp_params
    from sharpmod.sharptab.derived import _mmp_linear_predictor

    def uninitialized_upstream(*_args, **_kwargs):
        pytest.fail("MMP must not use upstream's uninitialized shear array")

    monkeypatch.setattr(sp_params, "mmp", uninitialized_upstream)
    collection, _ = decoder.load_npz(str(SAMPLE))
    prof = collection.getHighlightedProf()
    expected = 1 / (1 + np.exp(_mmp_linear_predictor(prof)))
    assert 0 < prof.mmp < 1
    assert prof.mmp == pytest.approx(expected)


@pytest.mark.parametrize("gap", ["none", "wind", "all_wind", "height"])
def test_mmp_reused_endpoints_match_scalar_shear_pairs(monkeypatch, gap):
    from sharppy.sharptab import interp, params, profile, utils, winds
    from sharpmod.sharptab import derived

    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    prof = profile.BasicProfile.copy(raw)
    prof.mupcl = SimpleNamespace(bplus=2000.0)
    if gap == "wind":
        prof.u[2:4] = np.ma.masked
        prof.v[14:17] = np.ma.masked
    elif gap == "all_wind":
        prof.wdir[:] = np.ma.masked
    elif gap == "height":
        prof.hght[2] = np.ma.masked
    agl = np.asarray(interp.to_agl(prof, prof.hght))
    pbots = np.atleast_1d(interp.pres(prof, prof.hght[agl <= 1000.0]))
    ptops = np.atleast_1d(interp.pres(prof, prof.hght[(agl >= 6000.0) & (agl < 10000.0)]))
    magnitudes = [derived._finite_or_none(utils.mag(*winds.wind_shear(prof, pbot, ptop)))
                  for pbot in pbots for ptop in ptops]
    usable = [value for value in magnitudes if value is not None]
    monkeypatch.setattr(params, "lapse_rate", lambda *_args, **_kwargs: 7.0)
    monkeypatch.setattr(winds, "mean_wind", lambda *_args, **_kwargs: (30.0, 40.0))
    expected = None if not usable else (
        derived._MMP_A0 + derived._MMP_A1 * float(utils.KTS2MS(max(usable)))
        + derived._MMP_A2 * 7.0 + derived._MMP_A3 * 2000.0
        + derived._MMP_A4 * float(utils.KTS2MS(50.0))
    )
    actual = derived._mmp_linear_predictor(prof)
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)


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

    monkeypatch.setattr(
        backends,
        "_profile_thermodynamics_buffers",
        unavailable,
    )
    monkeypatch.setattr(backends, "profile_dcape", unavailable)
    monkeypatch.setattr(sp_params, "parcelx", parcelx)
    monkeypatch.setattr(sp_params, "dcape", dcape)

    prof = AcceleratedConvectiveProfile.copy(raw)

    assert np.isfinite(prof.mupcl.bplus)
    assert np.isfinite(prof.dcape)
    assert calls["parcel"] >= 4
    assert calls["dcape"] >= 1


def test_copy_carries_source_supplied_surface_scalars():
    """The vendored ``copy`` drops every attribute outside its whitelist.

    A profile collection re-upgrades its profiles whenever the target type
    changes, and selecting the accelerated class is exactly such a change. So a
    sounding that arrived with surface relative vorticity attached had it
    stripped the moment the renderer chose this class. NSTP is that value's only
    consumer, so it reported missing for every rendered sounding while computing
    correctly for the same sounding outside the renderer -- the kind of gap that
    looks like a broken formula rather than a lost input.
    """
    from sharpmod.sharptab.constants import OPTIONAL_SOURCE_SURFACE_FIELDS

    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    raw.surface_relative_vorticity = 1.4e-4

    prof = AcceleratedConvectiveProfile.copy(raw)

    assert prof.surface_relative_vorticity == pytest.approx(1.4e-4)
    # Absent fields must not be invented, only carried when present.
    for name in OPTIONAL_SOURCE_SURFACE_FIELDS:
        if name != "surface_relative_vorticity":
            assert not hasattr(prof, name), name


def test_nstp_survives_the_accelerated_profile_upgrade():
    """End to end: the value has to reach the panel that reads it.

    Guards the whole chain rather than just the copy, because the value passes
    through the decoder, a profile upgrade, and the derived-profile builder
    before anything displays it, and it was silently lost in the middle.
    """
    from sharpmod.sharptab import constants, derived
    from sharpmod.viz.SPCWindow import _derived_profile

    collection, _ = decoder.load_npz(str(SAMPLE))
    raw = next(iter(collection._profs.values()))[0]
    raw.surface_relative_vorticity = 1.4e-4

    prof = AcceleratedConvectiveProfile.copy(raw)
    derived_prof = _derived_profile(prof)

    assert derived._surface_relative_vorticity(derived_prof) == pytest.approx(
        1.4e-4)
    # Without the carry this is MISSING, which renders as the "--" indicator.
    assert not constants.is_missing(derived_prof.nstp)
