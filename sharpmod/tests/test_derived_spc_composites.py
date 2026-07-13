"""Focused tests for SPC LSCP, NSTP, and Modified SHERBE composites."""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from sharpmod.sharptab import derived
from sharpmod.sharptab.constants import KTS_PER_MS, is_missing
from sharpmod.viz.SPCWindow import _derived_profile


def test_lscp_uses_left_esrh_and_current_spc_cin_term(monkeypatch):
    """LSCP follows the SPC Mesoanalysis left-mover formula."""
    sp = SimpleNamespace(
        etop=500.0,
        ebottom=900.0,
        mupcl=SimpleNamespace(bplus=2000.0, bminus=-80.0),
        left_esrh=[-100.0],
        ebwspd=20.0 * KTS_PER_MS,
    )
    monkeypatch.setattr(derived, "_convective_oracle_profile", lambda prof: sp)

    value = derived.left_supercell_composite(SimpleNamespace())

    assert value == pytest.approx(-2.0)


def test_nstp_requires_surface_relative_vorticity(monkeypatch):
    """NSTP stays missing unless a source supplies surface-relative vorticity."""
    sp = SimpleNamespace(
        mlpcl=SimpleNamespace(b3km=100.0, bminus=-25.0),
        sfc_6km_shear=(13.0 * KTS_PER_MS, 0.0),
    )
    monkeypatch.setattr(derived, "_convective_oracle_profile", lambda prof: sp)

    prof = SimpleNamespace(lapserate_sfc_1km=9.0)
    assert is_missing(derived.non_supercell_tornado_parameter(prof))

    prof.meta = {"sfc_relative_vorticity": 8.0e-5}
    value = derived.non_supercell_tornado_parameter(prof)

    assert value == pytest.approx(1.25)


def test_modified_sherbe_formula(monkeypatch):
    """Modified SHERBE applies the MOSHE four-term SPC formula."""
    sp = SimpleNamespace(lapserate_3km=6.0, ebwspd=18.0 * KTS_PER_MS)
    monkeypatch.setattr(derived, "_convective_oracle_profile", lambda prof: sp)
    monkeypatch.setattr(derived, "_bulk_shear_ms", lambda prof, top_agl: 18.0)
    monkeypatch.setattr(derived, "_max_thetae_vertical_velocity", lambda prof: 8.0)

    value = derived.modified_sherbe(SimpleNamespace())

    assert value == pytest.approx(2.0)


def test_derived_profile_preserves_omega_wetbulb_and_metadata():
    """The vendored-profile bridge keeps fields needed by the SPC composites."""
    prof = SimpleNamespace(
        pres=np.array([1000.0, 900.0, 800.0]),
        hght=np.array([100.0, 1000.0, 2000.0]),
        tmpc=np.array([25.0, 18.0, 10.0]),
        dwpc=np.array([20.0, 15.0, 5.0]),
        wdir=np.array([180.0, 200.0, 220.0]),
        wspd=np.array([10.0, 20.0, 30.0]),
        omeg=np.array([-0.5, -0.4, -0.3]),
        wetbulb=np.array([22.0, 16.0, 8.0]),
        date=datetime(2026, 7, 5, 9),
        location="Guam",
        latitude=35.0,
        longitude=-97.0,
        surface_relative_vorticity=8.0e-5,
    )

    sm_prof = _derived_profile(prof)

    assert np.allclose(sm_prof.omeg, prof.omeg)
    assert np.allclose(sm_prof.wetbulb, prof.wetbulb)
    assert sm_prof.meta["date"] == datetime(2026, 7, 5, 9)
    assert sm_prof.meta["location"] == "Guam"
    assert sm_prof.meta["loc"] == "Guam"
    assert sm_prof.meta["lat"] == pytest.approx(35.0)
    assert sm_prof.meta["lon"] == pytest.approx(-97.0)
    assert sm_prof.meta["surface_relative_vorticity"] == pytest.approx(8.0e-5)
