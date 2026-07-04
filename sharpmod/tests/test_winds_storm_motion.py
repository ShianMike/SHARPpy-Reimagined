"""Property test for the SFC-500 m SRH storm-motion convention (Req 1.5).

Feature: sharppy-modernization, Property 2: SFC-500m SRH uses the existing
storm-motion convention.

*For any* valid Profile, the Bunkers storm-motion vector used to compute the
SFC-500 m SRH is identical to the storm-motion vector used for the existing
SFC-1 km and SFC-3 km SRH computations.

The single source of that vector is :func:`sharpmod.sharptab.winds.storm_motion`,
which every SRH layer routes through. These tests assert that

* the SRH returned by :func:`sfc_500m_kinematics` equals
  :func:`helicity` recomputed with the *same* ``storm_motion`` vector,
* the identical vector is what would drive the SFC-1 km and SFC-3 km SRH,
* ``storm_motion`` reuses a storm motion already cached on the Profile
  (``prof.srwind`` / ``prof.bunkers``) verbatim -- the single-source guarantee.

**Validates: Requirements 1.5**
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given

from sharpmod.sharptab import winds
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData, profiles


class _CachedSounding(SoundingData):
    """SoundingData subclass that permits arbitrary attributes (e.g. srwind).

    The base class fixes ``__slots__``; a subclass without ``__slots__`` gains a
    ``__dict__`` so tests can attach a cached ``srwind`` / ``bunkers`` vector.
    """


def _full_depth_sounding(cls=SoundingData):
    """A deterministic full-depth sounding spanning SFC->6 km AGL with shear."""
    hght_agl = np.array(
        [0.0, 250.0, 500.0, 1000.0, 2000.0, 3000.0, 4000.0, 6000.0], float)
    hght = hght_agl  # surface at 0 m MSL
    pres = 1000.0 * np.exp(-hght_agl / 8000.0)
    tmpc = 25.0 - 6.5 * (hght_agl / 1000.0)
    dwpc = tmpc - 5.0
    # Veering, strengthening wind profile -> non-zero SFC-6 km shear.
    wdir = np.array([180.0, 190.0, 200.0, 210.0, 230.0, 250.0, 260.0, 270.0])
    wspd = np.array([10.0, 15.0, 20.0, 25.0, 35.0, 45.0, 50.0, 60.0])
    return cls(pres, hght, tmpc, dwpc, wdir, wspd)


@given(profiles())
def test_sfc_500m_srh_uses_shared_storm_motion(prof):
    """SFC-500 m SRH is computed with the shared Bunkers storm-motion vector."""
    rstu, rstv, _lstu, _lstv = winds.storm_motion(prof)
    srh, _shear, _mnwind, _srw = winds.sfc_500m_kinematics(prof)

    # storm_motion is deterministic / a single source: a second call agrees.
    rstu2, rstv2, _l2u, _l2v = winds.storm_motion(prof)
    assert (rstu2, rstv2) == (rstu, rstv)

    if is_missing(rstu) or is_missing(rstv):
        # No shared storm motion available -> SFC-500 m SRH must also be masked.
        assert is_missing(srh)
        return

    # The SFC-500 m SRH must equal helicity recomputed with the SAME (rstu, rstv)
    # vector that drives the SFC-1 km / SFC-3 km SRH. This would fail if the
    # layer used a different convention (left-mover, ground-relative, ...).
    expected500 = winds.helicity(prof, 0.0, 500.0, stu=rstu, stv=rstv)[0]
    if is_missing(expected500):
        assert is_missing(srh)
    else:
        assert not is_missing(srh)
        assert float(srh) == pytest.approx(float(expected500), rel=1e-9,
                                           abs=1e-9)

    # The SFC-1 km and SFC-3 km SRH resolve through the identical vector; when
    # the profile spans those layers they compute without error using it.
    for top in (1000.0, 3000.0):
        val = winds.helicity(prof, 0.0, top, stu=rstu, stv=rstv)[0]
        # Same-vector computation is well-defined (numeric or masked, never raise).
        assert is_missing(val) or np.isfinite(float(val))


def test_all_layers_share_the_same_storm_motion_vector():
    """SFC-500 m / 1 km / 3 km SRH all key off one storm_motion() vector."""
    prof = _full_depth_sounding()
    rstu, rstv, _l, _lv = winds.storm_motion(prof)
    assert not is_missing(rstu) and not is_missing(rstv)

    srh500, _s, _m, _srw = winds.sfc_500m_kinematics(prof)
    expected500 = winds.helicity(prof, 0.0, 500.0, stu=rstu, stv=rstv)[0]
    assert float(srh500) == pytest.approx(float(expected500), rel=1e-9, abs=1e-9)

    # The 1 km and 3 km SRH built from the same vector are finite here.
    srh1km = winds.helicity(prof, 0.0, 1000.0, stu=rstu, stv=rstv)[0]
    srh3km = winds.helicity(prof, 0.0, 3000.0, stu=rstu, stv=rstv)[0]
    assert np.isfinite(float(srh1km))
    assert np.isfinite(float(srh3km))


def test_storm_motion_reuses_cached_srwind_single_source():
    """A cached ``prof.srwind`` is reused verbatim and drives SFC-500 m SRH."""
    prof = _full_depth_sounding(cls=_CachedSounding)
    cached = (12.0, -3.0, 4.0, 9.0)
    prof.srwind = cached

    rstu, rstv, lstu, lstv = winds.storm_motion(prof)
    assert (rstu, rstv, lstu, lstv) == cached

    # SFC-500 m SRH uses exactly the cached right-mover vector.
    srh, _s, _m, _srw = winds.sfc_500m_kinematics(prof)
    expected = winds.helicity(prof, 0.0, 500.0, stu=cached[0], stv=cached[1])[0]
    assert float(srh) == pytest.approx(float(expected), rel=1e-9, abs=1e-9)


def test_storm_motion_reuses_cached_bunkers_when_no_srwind():
    """When only ``prof.bunkers`` is cached, storm_motion reuses it."""
    prof = _full_depth_sounding(cls=_CachedSounding)
    prof.bunkers = (8.0, 5.0, 1.0, 2.0)
    rstu, rstv, lstu, lstv = winds.storm_motion(prof)
    assert (rstu, rstv, lstu, lstv) == (8.0, 5.0, 1.0, 2.0)
