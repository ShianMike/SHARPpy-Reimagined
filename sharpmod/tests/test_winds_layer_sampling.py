"""Regression tests for the 1 hPa layer-mean sampling bounds.

The pressure-weighted and non-pressure-weighted layer means integrate the
interpolated wind at a fixed 1 hPa increment. A fixed increment cannot land on
the layer top when the layer depth is not a whole number of hectopascals, and
stepping one increment past the top used to push the final sample *outside* the
reported profile. That sample interpolated to ``MISSING`` and was silently
dropped, so the reported layer mean depended on where the increment happened to
fall rather than on the requested layer.

For a profile whose top reported level *is* the layer top, dropping that sample
removes the topmost wind from the mean entirely. When the layer wind is near zero
except at the top -- which is exactly what the SFC-6 km Bunkers mean wind sees on
a strongly sheared profile -- the dropped sample moved the Bunkers storm motion
enough to shift SFC-500 m SRH by ~1 m^2/s^2 against upstream SHARPpy.

These tests pin the corrected behaviour: the sample set is bounded by
``[pbot, ptop]``, ends exactly at ``ptop``, and never relies on extrapolation
outside the profile.
"""

from __future__ import annotations

import numpy as np
import pytest

from sharpmod.sharptab import interp, winds
from sharpmod.sharptab.constants import is_missing
from sharpmod.tests.strategies import SoundingData


def _sheared_top_sounding():
    """Profile whose top reported level sits exactly at 6 km AGL.

    The wind is calm through the layer except for the topmost level, so any
    sample dropped at the layer top changes the SFC-6 km mean wind measurably.
    """
    hght = np.arange(0.0, 6000.0 + 1.0, 187.5)
    pres = 1004.0 * np.exp(-hght / 8000.0)
    tmpc = 25.0 - 6.5 * (hght / 1000.0)
    dwpc = tmpc - 5.0
    wdir = np.full(hght.size, 220.0)
    wspd = np.zeros(hght.size)
    wspd[-1] = 95.0
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_pressure_samples_end_exactly_at_the_layer_top():
    """A fractional layer depth still terminates on the layer top."""
    ptop = 479.01764478935456
    ps = winds._pressure_samples(1004.0, ptop)

    assert ps[0] == 1004.0
    assert ps[-1] == ptop
    # No sample may sit above the layer top (lower pressure than ``ptop``).
    assert np.all(ps >= ptop)
    # Strictly decreasing, so no duplicated level is introduced.
    assert np.all(np.diff(ps) < 0.0)


def test_pressure_samples_preserve_whole_hectopascal_layers():
    """A whole-hectopascal layer keeps its historical sample set."""
    ps = winds._pressure_samples(1000.0, 500.0)

    assert ps[0] == 1000.0
    assert ps[-1] == 500.0
    assert ps.size == 501
    np.testing.assert_allclose(ps, np.arange(1000.0, 499.0, -1.0))


@pytest.mark.parametrize(
    ("pbot", "ptop", "expected"),
    [
        (700.0, 700.0, [700.0]),   # degenerate layer -> the level itself
        (500.0, 700.0, []),        # inverted bounds -> unusable
        (np.nan, 700.0, []),       # non-finite bound -> unusable
        (700.0, np.nan, []),
    ],
)
def test_pressure_samples_edge_cases(pbot, ptop, expected):
    ps = winds._pressure_samples(pbot, ptop)
    assert ps.tolist() == expected


def test_layer_mean_includes_the_top_level_wind():
    """The layer mean must not drop the wind at the layer top.

    Regression: the top sample used to fall outside the profile and be dropped,
    which removed the only non-calm level from the SFC-6 km mean wind.
    """
    data = _sheared_top_sounding()
    psfc = winds._sfc_pres(data)
    ptop = interp.pres_at_hght_agl(data, 6000.0)
    assert not is_missing(ptop)

    ps = winds._pressure_samples(psfc, ptop)
    u, v = interp.components(data, ps)

    # Every sample resolves: no reliance on out-of-profile extrapolation.
    assert int(np.ma.count(np.ma.asanyarray(u))) == ps.size
    assert int(np.ma.count(np.ma.asanyarray(v))) == ps.size

    mnu, mnv = winds.mean_wind_npw(data, psfc, ptop)
    assert not is_missing(mnu)

    # The top-level wind contributes exactly one sample to the mean.
    u_top, v_top = interp.components(data, ptop)
    expected_u = (float(np.ma.asanyarray(u)[:-1].sum()) + float(u_top)) / ps.size
    expected_v = (float(np.ma.asanyarray(v)[:-1].sum()) + float(v_top)) / ps.size
    assert float(mnu) == pytest.approx(expected_u, rel=0, abs=1e-9)
    assert float(mnv) == pytest.approx(expected_v, rel=0, abs=1e-9)

    # A calm layer with a single strong top level cannot average to calm.
    assert abs(float(mnu)) + abs(float(mnv)) > 0.0


def test_layer_mean_matches_backend_workspace():
    """The Python path and the backend workspace agree on the layer mean."""
    data = _sheared_top_sounding()
    workspace = winds._profile_kinematics(data)
    if workspace is None:
        pytest.skip("backend workspace unavailable")

    layer = workspace.layer(6000.0, tolerance=1.0e-6)
    assert layer is not None

    psfc = winds._sfc_pres(data)
    ptop = interp.pres_at_hght_agl(data, 6000.0)
    mnu, mnv = winds.mean_wind_npw(data, psfc, ptop)
    pwu, pwv = winds.mean_wind(data, psfc, ptop)

    assert float(layer.mean_npw_u) == pytest.approx(float(mnu), abs=1e-9)
    assert float(layer.mean_npw_v) == pytest.approx(float(mnv), abs=1e-9)
    assert float(layer.mean_u) == pytest.approx(float(pwu), abs=1e-9)
    assert float(layer.mean_v) == pytest.approx(float(pwv), abs=1e-9)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
