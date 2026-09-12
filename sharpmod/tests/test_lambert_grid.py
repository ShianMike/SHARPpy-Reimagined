"""The Lambert conformal grids the mesoscale models actually run on.

Two things are being pinned here. First that the pure-Python transform is right,
against HRRR's own published grid geometry rather than against itself. Second
that a longitude/latitude box is not an acceptable stand-in for one of these
grids -- which is the whole reason the module exists, and is easy to lose sight
of because the error is invisible in the middle of the domain and largest exactly
where nobody looks.
"""

from __future__ import annotations

import math

import pytest

from sharpmod.lambert_grid import HRRR_GRID, LambertConformalGrid

#: HRRR's first grid point, as its own GRIB header publishes it:
#: ``latitudeOfFirstGridPointInDegrees`` / ``longitudeOfFirstGridPointInDegrees``
#: of 21.138123 and 237.280472.
HRRR_FIRST_GRID_POINT = (-122.719528, 21.138123)


def test_the_south_west_corner_is_the_grid_point_hrrr_publishes():
    """Checked against the model's header, not against this module's own maths.

    A transform that round-trips perfectly can still sit on the wrong cone; only
    an external coordinate catches that.
    """
    lon, lat = HRRR_GRID.inverse(HRRR_GRID.x0, HRRR_GRID.y0)

    assert lon == pytest.approx(HRRR_FIRST_GRID_POINT[0], abs=1e-5)
    assert lat == pytest.approx(HRRR_FIRST_GRID_POINT[1], abs=1e-5)


def test_the_grid_spans_the_point_counts_its_header_declares():
    """1799 x 1059 *points* at 3 km, so 1798 and 1058 gaps.

    Off by one here is a 3 km error along two edges of the outline.
    """
    rows, cols = HRRR_GRID.shape

    assert HRRR_GRID.x1 - HRRR_GRID.x0 == pytest.approx(
        (cols - 1) * HRRR_GRID.spacing)
    assert HRRR_GRID.y1 - HRRR_GRID.y0 == pytest.approx(
        (rows - 1) * HRRR_GRID.spacing)


@pytest.mark.parametrize("lat", [22.0, 30.0, 38.5, 45.0, 52.0])
@pytest.mark.parametrize("lon", [-130.0, -110.0, -97.5, -80.0, -65.0])
def test_the_transform_round_trips(lon, lat):
    x, y = HRRR_GRID.forward(lon, lat)
    back_lon, back_lat = HRRR_GRID.inverse(x, y)

    assert back_lon == pytest.approx(lon, abs=1e-9)
    assert back_lat == pytest.approx(lat, abs=1e-9)


def test_a_tangent_cone_does_not_divide_by_zero():
    """HRRR's standard parallels are equal, where the secant formula is 0/0."""
    assert HRRR_GRID.lat_1 == HRRR_GRID.lat_2
    assert HRRR_GRID._n == pytest.approx(
        math.sin(math.radians(HRRR_GRID.lat_1)))


def test_a_secant_cone_uses_two_standard_parallels():
    """The other branch, so the tangent special case cannot hide a broken one."""
    secant = LambertConformalGrid(
        lat_origin=38.5, lon_origin=-97.5, lat_1=33.0, lat_2=45.0,
        radius=6371229.0, shape=(500, 500), spacing=12000.0,
        x0=-2000000.0, y0=-1500000.0)

    assert secant._n != pytest.approx(math.sin(math.radians(33.0)))
    # Standard parallels are where the projection is true to scale, so they are
    # the two latitudes whose scale factor is 1.
    for parallel in (33.0, 45.0):
        span = 0.001
        x_a, y_a = secant.forward(-97.5, parallel)
        x_b, y_b = secant.forward(-97.5 + span, parallel)
        drawn = math.hypot(x_b - x_a, y_b - y_a)
        true = (secant.radius * math.radians(span)
                * math.cos(math.radians(parallel)))
        assert drawn / true == pytest.approx(1.0, abs=2e-4), \
            f"scale is not true at the {parallel} standard parallel"


# --------------------------------------------------------------------------- #
# why a box will not do
# --------------------------------------------------------------------------- #
def test_the_southern_edge_arches_well_north_of_its_own_corners():
    """This is the error a lon/lat box makes, and it is degrees, not minutes.

    The grid's south edge is a straight line in the projection plane, so in
    longitude/latitude it curves: latitude is highest on the central meridian and
    falls away to both corners. Any box drawn round it is wrong by this much.
    """
    edge = [HRRR_GRID.inverse(
        HRRR_GRID.x0 + (HRRR_GRID.x1 - HRRR_GRID.x0) * index / 8,
        HRRR_GRID.y0) for index in range(9)]
    latitudes = [lat for _lon, lat in edge]

    crest = latitudes[4]
    corners = (latitudes[0] + latitudes[-1]) / 2.0
    assert crest > corners, "the south edge must arch north of its corners"
    assert crest - corners == pytest.approx(3.2, abs=0.2)


def test_the_outline_is_a_closed_ring_of_sampled_curves():
    outline = HRRR_GRID.outline(samples_per_edge=8)

    assert outline[0] == outline[-1], "the perimeter has to close"
    assert len(outline) == 8 * 4 + 1
    assert all(-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0
               for lon, lat in outline)


def test_a_denser_outline_hugs_the_boundary_more_closely():
    """Sampling is the knob; more samples must not move the corners."""
    coarse = HRRR_GRID.outline(samples_per_edge=4)
    fine = HRRR_GRID.outline(samples_per_edge=64)

    assert coarse[0] == pytest.approx(fine[0]), "the corners are fixed"

    def path_length(ring):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(ring, ring[1:]))

    # A polyline inscribed in a curve gets longer as it is refined, so this is
    # the direct statement that the fine ring is following the arc and the coarse
    # one is cutting across it.
    assert path_length(fine) > path_length(coarse)


def test_the_envelope_is_measured_off_the_grid_not_rounded():
    lon0, lon1, lat0, lat1 = HRRR_GRID.bounds()

    assert (lon0, lat0) == pytest.approx((-134.10, 21.14), abs=0.02)
    assert (lon1, lat1) == pytest.approx((-60.92, 52.62), abs=0.02)
    # And it really is an envelope: every perimeter point sits inside it.
    for lon, lat in HRRR_GRID.outline():
        assert lon0 <= lon <= lon1 and lat0 <= lat <= lat1


def test_a_point_inside_the_envelope_can_still_be_off_the_grid():
    """The case that makes a box unusable rather than merely approximate."""
    lon0, lon1, lat0, lat1 = HRRR_GRID.bounds()
    lon, lat = -97.5, 22.0     # under the southern arch, on the central meridian

    assert lon0 <= lon <= lon1 and lat0 <= lat <= lat1, "inside the box"
    assert not HRRR_GRID.contains(lat, lon), "but off the grid"


def test_the_grid_carries_the_points_it_should():
    assert HRRR_GRID.contains(35.18, -97.44), "Norman OK"
    assert HRRR_GRID.contains(40.71, -74.01), "New York"
    assert HRRR_GRID.contains(47.60, -122.33), "Seattle"


def test_the_grid_refuses_what_it_does_not_cover():
    assert not HRRR_GRID.contains(61.22, -149.90), "Anchorage"
    assert not HRRR_GRID.contains(21.31, -157.86), "Honolulu"
    assert not HRRR_GRID.contains(51.51, -0.13), "London"


def test_a_point_a_half_turn_round_the_cone_is_not_folded_back_in():
    """Without the wrap guard the antipode projects into the grid rectangle."""
    antipode_lon = HRRR_GRID.lon_origin + 180.0

    assert not HRRR_GRID.contains(38.5, antipode_lon)


@pytest.mark.parametrize("lat,lon", [
    (float("nan"), -97.5),
    (38.5, float("inf")),
    (200.0, -97.5),
    (None, None),
    ("north", "west"),
])
def test_unusable_coordinates_are_refused_rather_than_raising(lat, lon):
    """This decides whether a sounding may be requested; it must not raise."""
    assert HRRR_GRID.contains(lat, lon) is False
