"""Property-based test for ERA5 nearest grid-point / analysis-time selection
(task 12.2).

Feature: sharppy-modernization, Property 17: ERA5 selects the nearest grid
point and analysis time

Property 17 (design.md): *For any* requested latitude, longitude, and valid
time over the ERA5 grid, the selected grid point minimizes the horizontal
great-circle distance to the requested location and the selected analysis time
minimizes the absolute difference from the requested valid time.

**Validates: Requirements 8.1**

Strategy / oracle
-----------------
Synthetic lat/lon coordinate vectors and a request point are generated, and the
index returned by :func:`sharpmod.tools.era5_extract.select_nearest_grid_point`
is checked against an independent brute-force minimum over the same great-circle
distance metric. Likewise a synthetic list of analysis times and a target time
are generated and
:func:`sharpmod.tools.era5_extract.select_nearest_time` is checked against a
brute-force minimum absolute difference. Ties are compared on the achieved
distance / time-delta (not the index), since either tied point is a valid
minimizer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from sharpmod.tools import era5_extract as era5

# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #
# Integer-degree coordinate vectors keep the values distinct and readable while
# still exercising the argmin/unravel logic. Distances are compared as floats,
# so ties (if any) are handled by comparing achieved distance rather than index.
_LAT_VALUES = st.integers(min_value=-89, max_value=89)
_LON_VALUES = st.integers(min_value=0, max_value=359)


@st.composite
def _grid_and_point(draw):
    """Draw (lats, lons, lat0, lon0): a coordinate grid plus a request point."""
    lats = sorted(draw(st.sets(_LAT_VALUES, min_size=1, max_size=8)))
    lons = sorted(draw(st.sets(_LON_VALUES, min_size=1, max_size=8)))
    lat0 = draw(st.floats(min_value=-90.0, max_value=90.0,
                          allow_nan=False, allow_infinity=False))
    lon0 = draw(st.floats(min_value=-180.0, max_value=360.0,
                          allow_nan=False, allow_infinity=False))
    return (np.array(lats, dtype=float), np.array(lons, dtype=float),
            float(lat0), float(lon0))


@st.composite
def _times_and_target(draw):
    """Draw (times, target): a list of analysis times and a requested time."""
    base = datetime(2010, 1, 1, tzinfo=timezone.utc)
    # Distinct hour offsets keep the times unique.
    offsets = draw(st.sets(st.integers(min_value=0, max_value=20000),
                           min_size=1, max_size=8))
    times = [base + timedelta(hours=h) for h in sorted(offsets)]
    target_off = draw(st.integers(min_value=-5000, max_value=25000))
    target = base + timedelta(hours=target_off, minutes=draw(
        st.integers(min_value=0, max_value=59)))
    return times, target


# --------------------------------------------------------------------------- #
# Property 17 -- nearest grid point
# --------------------------------------------------------------------------- #
@settings(max_examples=200)
@given(_grid_and_point())
def test_selected_grid_point_minimizes_great_circle_distance(data):
    """The selected grid point achieves the minimum great-circle distance.

    Feature: sharppy-modernization, Property 17: ERA5 selects the nearest grid
    point and analysis time
    Validates: Requirements 8.1
    """
    lats, lons, lat0, lon0 = data

    (ilat, ilon), glat, glon = era5.select_nearest_grid_point(
        lats, lons, lat0, lon0)

    # Independent brute-force minimum over every grid combination.
    best = np.inf
    for la in lats:
        for lo in lons:
            d = era5.great_circle_distance_km(lat0, lon0, la, lo)
            best = min(best, float(d))

    selected_dist = float(
        era5.great_circle_distance_km(lat0, lon0, glat, glon))

    # The returned index must point at the returned coordinates.
    assert glat == float(lats[ilat])
    assert glon == float(lons[ilon])
    # And that point must be a genuine minimizer of the distance metric.
    assert selected_dist <= best + 1e-6, (
        f"selected distance {selected_dist} exceeds brute-force min {best}")


@settings(max_examples=200)
@given(_grid_and_point())
def test_selected_grid_point_2d_curvilinear(data):
    """Selection also minimizes distance for 2-D (curvilinear) coordinates.

    Feature: sharppy-modernization, Property 17: ERA5 selects the nearest grid
    point and analysis time
    Validates: Requirements 8.1
    """
    lats, lons, lat0, lon0 = data
    lat2d, lon2d = np.meshgrid(lats, lons, indexing="ij")

    (iy, ix), glat, glon = era5.select_nearest_grid_point(
        lat2d, lon2d, lat0, lon0)

    dist = era5.great_circle_distance_km(lat0, lon0, lat2d, lon2d)
    best = float(np.min(dist))
    selected_dist = float(
        era5.great_circle_distance_km(lat0, lon0, glat, glon))

    assert glat == float(lat2d[iy, ix])
    assert glon == float(lon2d[iy, ix])
    assert selected_dist <= best + 1e-6


# --------------------------------------------------------------------------- #
# Property 17 -- nearest analysis time
# --------------------------------------------------------------------------- #
@settings(max_examples=200)
@given(_times_and_target())
def test_selected_time_minimizes_absolute_difference(data):
    """The selected analysis time minimizes |selected - target|.

    Feature: sharppy-modernization, Property 17: ERA5 selects the nearest grid
    point and analysis time
    Validates: Requirements 8.1
    """
    times, target = data

    idx, selected = era5.select_nearest_time(times, target)

    # Selected index and value agree.
    assert selected == times[idx]

    selected_delta = abs((selected - target).total_seconds())
    best = min(abs((t - target).total_seconds()) for t in times)

    assert selected_delta <= best + 1e-6, (
        f"selected time delta {selected_delta}s exceeds brute-force min "
        f"{best}s")


@settings(max_examples=200)
@given(_times_and_target())
def test_selected_time_accepts_datetime64(data):
    """Selection works on NumPy datetime64 inputs (the ERA5 coord dtype).

    Feature: sharppy-modernization, Property 17: ERA5 selects the nearest grid
    point and analysis time
    Validates: Requirements 8.1
    """
    times, target = data
    times64 = [np.datetime64(t.replace(tzinfo=None), "ns") for t in times]

    idx, _selected = era5.select_nearest_time(times64, target)

    best = min(abs((t - target).total_seconds()) for t in times)
    selected_delta = abs((times[idx] - target).total_seconds())
    assert selected_delta <= best + 1e-6
