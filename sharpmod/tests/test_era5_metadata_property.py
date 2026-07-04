"""Property-based test for ERA5 metadata recording (task 12.4).

Feature: sharppy-modernization, Property 19: ERA5 metadata records requested
and selected coordinates and time

Property 19 (design.md): *For any* successful ERA5 extraction, the output
metadata sidecar records the requested source latitude, requested longitude,
requested valid time, the selected grid-point latitude and longitude, and the
selected ERA5 analysis time.

**Validates: Requirements 8.7**

The extraction runs against a synthetic in-memory ``xarray.Dataset`` (via the
``dataset=`` hook); the ``.json`` sidecar written alongside the ``.npz`` is then
parsed and its recorded requested/selected fields are checked -- including that
the recorded *selected* grid point and time are the true nearest ones.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from sharpmod.tools import era5_extract as era5
from sharpmod.tests.era5_synth import make_era5_dataset

_LAT_VALUES = st.integers(min_value=-85, max_value=85)
_LON_VALUES = st.integers(min_value=1, max_value=358)
_LEVELS = [1000.0, 850.0, 700.0, 500.0, 300.0, 200.0]
_TIMES = [
    datetime(2021, 3, 5, 0, tzinfo=timezone.utc),
    datetime(2021, 3, 5, 6, tzinfo=timezone.utc),
    datetime(2021, 3, 5, 12, tzinfo=timezone.utc),
    datetime(2021, 3, 5, 18, tzinfo=timezone.utc),
]

_REQUIRED_META_KEYS = (
    "requested_lat", "requested_lon", "requested_valid",
    "selected_lat", "selected_lon", "selected_valid",
)


@st.composite
def _extraction_request(draw):
    lats = sorted(draw(st.sets(_LAT_VALUES, min_size=2, max_size=6)))
    lons = sorted(draw(st.sets(_LON_VALUES, min_size=2, max_size=6)))
    req_lat = draw(st.floats(min_value=float(lats[0]), max_value=float(lats[-1]),
                             allow_nan=False, allow_infinity=False))
    req_lon = draw(st.floats(min_value=float(lons[0]), max_value=float(lons[-1]),
                             allow_nan=False, allow_infinity=False))
    req_time = draw(st.sampled_from(_TIMES))
    return (np.array(lats, dtype=float), np.array(lons, dtype=float),
            float(req_lat), float(req_lon), req_time)


@settings(max_examples=120, deadline=None)
@given(_extraction_request())
def test_metadata_records_requested_and_selected(data):
    """The .json sidecar records requested + selected lat/lon/time.

    Feature: sharppy-modernization, Property 19: ERA5 metadata records requested
    and selected coordinates and time
    Validates: Requirements 8.7
    """
    lats, lons, req_lat, req_lon, req_time = data
    ds = make_era5_dataset(lats, lons, _LEVELS, _TIMES, seed=3)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "era5_point.npz")
        era5.extract(req_lat, req_lon, req_time, out_path, dataset=ds)

        json_path = os.path.splitext(out_path)[0] + ".json"
        assert os.path.exists(json_path), "metadata sidecar was not written"
        with open(json_path, encoding="utf-8") as fh:
            meta = json.load(fh)

        # All required requested/selected fields are present.
        for key in _REQUIRED_META_KEYS:
            assert key in meta, f"metadata missing {key!r}"

        # Requested values echo the request exactly.
        assert meta["requested_lat"] == req_lat
        assert meta["requested_lon"] == req_lon
        assert meta["requested_valid"] == req_time.strftime("%Y-%m-%d %H:%M")

        # Selected grid point is the true great-circle nearest of the grid.
        (_, _), true_lat, true_lon = era5.select_nearest_grid_point(
            lats, lons, req_lat, req_lon)
        true_lon_norm = ((true_lon + 180.0) % 360.0) - 180.0
        assert meta["selected_lat"] == true_lat
        assert meta["selected_lon"] == true_lon_norm

        # Selected valid time is the true nearest analysis time.
        _, true_time = era5.select_nearest_time(_TIMES, req_time)
        assert meta["selected_valid"] == true_time.strftime("%Y-%m-%d %H:%M")


def test_metadata_deterministic_example():
    """A fixed request records the expected requested/selected metadata.

    Feature: sharppy-modernization, Property 19: ERA5 metadata records requested
    and selected coordinates and time
    Validates: Requirements 8.7
    """
    lats = np.array([30.0, 35.0, 40.0], dtype=float)
    lons = np.array([260.0, 265.0, 270.0], dtype=float)
    ds = make_era5_dataset(lats, lons, _LEVELS, _TIMES, seed=9)

    # Request nearest to (35, 265) and to the 06Z analysis.
    req_lat, req_lon, req_time = 34.6, 264.4, _TIMES[1]

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "era5_point.npz")
        era5.extract(req_lat, req_lon, req_time, out_path, dataset=ds)
        with open(os.path.splitext(out_path)[0] + ".json", encoding="utf-8") as fh:
            meta = json.load(fh)

    assert meta["requested_lat"] == req_lat
    assert meta["requested_lon"] == req_lon
    assert meta["requested_valid"] == "2021-03-05 06:00"
    assert meta["selected_lat"] == 35.0
    # 265 in 0..360 normalizes to -95 in [-180, 180).
    assert meta["selected_lon"] == -95.0
    assert meta["selected_valid"] == "2021-03-05 06:00"
