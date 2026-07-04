"""Property-based test for ERA5 output completeness + shared-path loading
(task 12.3).

Feature: sharppy-modernization, Property 18: ERA5 output is complete and loads
through the shared point-sounding path

Property 18 (design.md): *For any* successful ERA5 extraction, the written
output populates pressure, height, temperature, dewpoint, and u/v wind
components for each present pressure level, and loading that output through the
existing ``.npz`` point-sounding path yields a Profile with populated pressure,
height, temperature, dewpoint, and wind arrays.

**Validates: Requirements 8.2, 8.4**

The extraction runs against a synthetic in-memory ``xarray.Dataset`` (via the
``dataset=`` hook) so no network / Herbie access is required. Loading is
verified through :func:`sharpmod.io.decoder.load_npz` -- the *same* code path
the HRRR ``.npz`` sidecar uses (Requirement 8.4).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import numpy as np
import numpy.ma as ma
from hypothesis import given, settings
from hypothesis import strategies as st

from sharpmod.io import decoder as decoder_mod
from sharpmod.tools import era5_extract as era5
from sharpmod.tests.era5_synth import make_era5_dataset

MISSING = -9999.0

# Per-level fields the extractor must populate for each present pressure level.
_LEVEL_FIELDS = ("pres", "hght", "tmpc", "dwpc", "uwnd", "vwnd", "wdir", "wspd")
# Fields the shared .npz loader must expose as populated Profile arrays.
_PROFILE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")

_LAT_VALUES = st.integers(min_value=-85, max_value=85)
_LON_VALUES = st.integers(min_value=1, max_value=358)
_LEVELS = [1000.0, 925.0, 850.0, 700.0, 500.0, 300.0, 250.0, 200.0, 100.0]
_TIMES = [
    datetime(2019, 7, 10, 0, tzinfo=timezone.utc),
    datetime(2019, 7, 10, 6, tzinfo=timezone.utc),
    datetime(2019, 7, 10, 12, tzinfo=timezone.utc),
]


@st.composite
def _extraction_request(draw):
    """Draw (lats, lons, levels, req_lat, req_lon, req_time)."""
    lats = sorted(draw(st.sets(_LAT_VALUES, min_size=2, max_size=6)))
    lons = sorted(draw(st.sets(_LON_VALUES, min_size=2, max_size=6)))
    n_lev = draw(st.integers(min_value=3, max_value=len(_LEVELS)))
    levels = _LEVELS[:n_lev]

    req_lat = draw(st.floats(min_value=float(lats[0]), max_value=float(lats[-1]),
                             allow_nan=False, allow_infinity=False))
    req_lon = draw(st.floats(min_value=float(lons[0]), max_value=float(lons[-1]),
                             allow_nan=False, allow_infinity=False))
    req_time = draw(st.sampled_from(_TIMES))
    return (np.array(lats, dtype=float), np.array(lons, dtype=float),
            levels, float(req_lat), float(req_lon), req_time)


def _finite_valid(arr):
    """Finite, non-missing entries of a decoded/stored field."""
    data = np.asarray(ma.asarray(arr, dtype=float).filled(MISSING), dtype=float)
    finite = data[np.isfinite(data)]
    return finite[finite != MISSING]


@settings(max_examples=120, deadline=None)
@given(_extraction_request())
def test_era5_output_is_complete_and_loads_through_shared_path(data):
    """ERA5 output populates every per-level field and loads via load_npz.

    Feature: sharppy-modernization, Property 18: ERA5 output is complete and
    loads through the shared point-sounding path
    Validates: Requirements 8.2, 8.4
    """
    lats, lons, levels, req_lat, req_lon, req_time = data
    ds = make_era5_dataset(lats, lons, levels, _TIMES, seed=1)
    n_levels = len(levels)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = f"{tmp}/era5_point.npz"
        result = era5.extract(req_lat, req_lon, req_time, out_path, dataset=ds)
        assert result == out_path

        # (8.2) The written .npz populates every per-level field for each level.
        with np.load(out_path, allow_pickle=True) as npz:
            for key in _LEVEL_FIELDS:
                assert key in npz.files, f"missing output array {key!r}"
                arr = np.asarray(npz[key], dtype=float)
                assert arr.shape == (n_levels,), (
                    f"{key!r} has shape {arr.shape}, expected ({n_levels},)")
                # Synthetic source is complete -> no missing sentinels.
                assert np.all(np.isfinite(arr)), f"{key!r} has non-finite values"
                assert np.all(arr != MISSING), f"{key!r} has missing sentinels"

        # (8.4) Loading through the shared .npz path yields a populated Profile.
        prof_collection, loc = decoder_mod.load_npz(out_path)
        assert loc, "load_npz returned an empty location label"

        profs = prof_collection._profs
        member = next(iter(profs))
        prof = profs[member][0]

        lengths = set()
        for name in _PROFILE_FIELDS:
            assert hasattr(prof, name), f"Profile missing {name!r}"
            arr = np.asarray(ma.asarray(getattr(prof, name)))
            assert arr.size > 0, f"{name!r} is empty"
            lengths.add(arr.size)
            assert _finite_valid(getattr(prof, name)).size > 0, (
                f"{name!r} has no valid values after load")
        assert len(lengths) == 1, f"Profile fields disagree in length: {lengths}"


def test_era5_output_completeness_deterministic_example():
    """A fixed request yields a complete .npz that loads via the shared path.

    Guards the property precondition: at least one successful extraction is
    exercised end-to-end.

    Feature: sharppy-modernization, Property 18: ERA5 output is complete and
    loads through the shared point-sounding path
    Validates: Requirements 8.2, 8.4
    """
    lats = np.array([30.0, 35.0, 40.0], dtype=float)
    lons = np.array([260.0, 265.0, 270.0], dtype=float)
    levels = [1000.0, 850.0, 700.0, 500.0, 300.0]
    ds = make_era5_dataset(lats, lons, levels, _TIMES, seed=7)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = f"{tmp}/era5_point.npz"
        era5.extract(36.0, 263.0, _TIMES[1], out_path, dataset=ds)

        with np.load(out_path, allow_pickle=True) as npz:
            for key in _LEVEL_FIELDS:
                arr = np.asarray(npz[key], dtype=float)
                assert arr.shape == (len(levels),)
                assert np.all(np.isfinite(arr))

        prof_collection, loc = decoder_mod.load_npz(out_path)
        assert loc
        prof = next(iter(prof_collection._profs.values()))[0]
        for name in _PROFILE_FIELDS:
            assert _finite_valid(getattr(prof, name)).size > 0
