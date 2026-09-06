"""Box region geometry and sample-plan budgeting."""

from datetime import datetime, timezone

import pytest

from sharpmod.box_sounding import (
    DEFAULT_TARGET_POINTS,
    MAX_BOX_POINTS,
    MAX_POINT_PROVIDER_POINTS,
    BoxRegion,
    BoxRegionError,
    BoxSampleError,
    box_requests,
    describe_plan,
    km_per_deg_lon,
    plan_box_samples,
    region_from_bounds,
)
from sharpmod.tools import model_extract


RUN = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)

# A mesoscale box over northern Oklahoma / southern Kansas: comfortably inside
# every CONUS product's domain.
PLAINS = (34.0, -99.0, 37.0, -95.0)


def _plains() -> BoxRegion:
    return BoxRegion.from_corners(*PLAINS)


# -- region geometry ------------------------------------------------------- #


def test_from_corners_orders_edges_regardless_of_drag_direction():
    forward = BoxRegion.from_corners(34.0, -99.0, 37.0, -95.0)
    backward = BoxRegion.from_corners(37.0, -95.0, 34.0, -99.0)
    assert forward == backward
    assert forward.lat0 == 34.0
    assert forward.lat1 == 37.0
    assert forward.lon0 == -99.0
    assert forward.lon_span == pytest.approx(4.0)


def test_region_reports_center_span_and_physical_size():
    region = _plains()
    assert region.lat_span == pytest.approx(3.0)
    assert region.center_lat == pytest.approx(35.5)
    assert region.center_lon == pytest.approx(-97.0)
    assert region.height_km == pytest.approx(3.0 * 111.32)
    assert region.width_km == pytest.approx(
        4.0 * km_per_deg_lon(35.5))
    assert region.area_km2 == pytest.approx(
        region.width_km * region.height_km)


@pytest.mark.parametrize(
    "corners",
    (
        (35.0, -97.0, 35.0, -97.0),        # a stray click, no area
        (35.0, -97.0, 35.0, -95.0),        # zero latitude span
        (34.0, -97.0, 37.0, -97.0),        # zero longitude span
    ),
)
def test_degenerate_rectangles_are_rejected(corners):
    with pytest.raises(BoxRegionError):
        BoxRegion.from_corners(*corners)


def test_non_numeric_corners_are_rejected():
    with pytest.raises(BoxRegionError):
        BoxRegion.from_corners("north", -97.0, 37.0, -95.0)


def test_latitudes_are_clamped_to_the_poles():
    region = BoxRegion.from_corners(80.0, 10.0, 120.0, 20.0)
    assert region.lat1 == 90.0
    assert region.lat0 == 80.0


def test_longitude_span_cannot_exceed_a_full_turn():
    with pytest.raises(BoxRegionError):
        BoxRegion(lat0=0.0, lat1=10.0, lon0=0.0, lon_span=361.0)


# -- antimeridian ---------------------------------------------------------- #


def test_unwrapped_corners_preserve_an_antimeridian_span():
    # A drag from 170E to 170W is a 20-degree box, not the 340-degree
    # complement. The map hands over unwrapped longitudes to say so.
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    assert region.lon_span == pytest.approx(20.0)
    assert region.lon0 == pytest.approx(170.0)
    assert region.lon1 == pytest.approx(-170.0)
    assert region.crosses_antimeridian is True


def test_antimeridian_region_membership_wraps():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    assert region.contains(53.0, 178.0) is True
    assert region.contains(53.0, -175.0) is True
    assert region.contains(53.0, 160.0) is False
    assert region.contains(40.0, 178.0) is False


def test_antimeridian_bounds_and_segments_match_model_extract_convention():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    # model_extract encodes a wrapped extent as lon0 > lon1.
    assert region.bounds == (170.0, -170.0, 50.0, 56.0)
    assert region.longitude_segments() == ((170.0, 180.0), (-180.0, -170.0))


def test_non_wrapping_region_has_one_segment():
    assert _plains().longitude_segments() == ((-99.0, -95.0),)


def test_sampled_longitudes_cross_the_antimeridian_normalized():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    plan = plan_box_samples("gfs", region, target_points=16)
    lons = [plan.point_at(0, col).lon for col in range(plan.cols)]
    assert all(-180.0 <= lon <= 180.0 for lon in lons)
    # The row must step east through the dateline, changing sign exactly once.
    sign_changes = sum(
        1 for a, b in zip(lons, lons[1:]) if (a > 0) != (b > 0)
    )
    assert sign_changes == 1


def test_region_from_bounds_round_trips_a_wrapped_extent():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    assert region_from_bounds(region.bounds).lon_span == pytest.approx(
        region.lon_span)


def test_region_from_bounds_reads_full_width_as_a_whole_turn():
    assert region_from_bounds(
        (-180.0, 180.0, -60.0, 60.0)).lon_span == pytest.approx(360.0)


def test_region_from_bounds_rejects_malformed_input():
    with pytest.raises(BoxRegionError):
        region_from_bounds((1.0, 2.0, 3.0))


# -- grid snapping --------------------------------------------------------- #


def test_spacing_is_never_finer_than_the_model_grid():
    plan = plan_box_samples("hrrr", _plains(), spacing_km=1.0)
    native = model_extract.grid_spacing_km("hrrr")
    assert plan.native_spacing_km == pytest.approx(native)
    assert plan.spacing_km >= native
    assert plan.oversampled is False
    assert any("finer than" in note for note in plan.notes)


def test_spacing_snaps_to_a_whole_multiple_of_the_native_grid():
    # 13 km RAP: an 20 km request must land on 26 km, not stay at 20.
    plan = plan_box_samples("rap", _plains(), spacing_km=20.0)
    native = model_extract.grid_spacing_km("rap")
    assert plan.spacing_km == pytest.approx(2.0 * native)


def test_requested_spacing_is_never_rounded_down_to_a_native_multiple():
    # Keep the box small enough that the point budget does not coarsen the
    # snapped spacing a second time.
    region = BoxRegion.from_corners(35.0, -97.0, 35.2, -96.8)
    plan = plan_box_samples("hrrr", region, spacing_km=4.0)
    native = model_extract.grid_spacing_km("hrrr")

    assert plan.spacing_km == pytest.approx(2.0 * native)


def test_edge_inclusive_lattice_intervals_are_not_finer_than_the_plan():
    height_km = 10.8
    region = BoxRegion.from_corners(
        35.0, -97.0, 35.0 + height_km / 111.32, -96.0)
    plan = plan_box_samples("hrrr", region, spacing_km=3.0)

    assert plan.rows == 4
    assert region.height_km / (plan.rows - 1) >= plan.spacing_km


def test_default_plan_lands_near_the_default_target():
    plan = plan_box_samples("hrrr", _plains())
    # Rounding to a whole grid multiple moves the count, but not by much.
    assert 0.4 * DEFAULT_TARGET_POINTS <= plan.count <= 2.5 * (
        DEFAULT_TARGET_POINTS)


def test_explicit_target_points_is_respected_within_the_budget():
    plan = plan_box_samples("hrrr", _plains(), target_points=16)
    assert plan.count <= MAX_BOX_POINTS
    assert plan.count >= 4


# -- budgeting ------------------------------------------------------------- #


def test_continental_box_against_a_3km_grid_stays_within_budget():
    # This is the case that would otherwise ask for millions of soundings.
    region = BoxRegion.from_corners(25.0, -125.0, 49.0, -67.0)
    plan = plan_box_samples("hrrr", region, spacing_km=3.0)
    assert plan.count <= MAX_BOX_POINTS
    assert any("Coarsened" in note for note in plan.notes)


def test_max_points_is_honoured_and_capped_by_the_hard_ceiling():
    plan = plan_box_samples("hrrr", _plains(), max_points=9)
    assert plan.count <= 9
    huge = plan_box_samples("hrrr", _plains(), max_points=10_000)
    assert huge.count <= MAX_BOX_POINTS


@pytest.mark.parametrize("value", (0, -3))
def test_invalid_max_points_is_rejected(value):
    with pytest.raises(BoxSampleError):
        plan_box_samples("hrrr", _plains(), max_points=value)


def test_point_only_provider_gets_a_smaller_budget_and_says_why():
    plan = plan_box_samples("openmeteo-icon-global", _plains())
    assert plan.point_only_provider is True
    assert plan.count <= MAX_POINT_PROVIDER_POINTS
    assert any("one request per point" in note for note in plan.notes)
    # Every node is its own transfer, unlike a shared grid subset.
    assert plan.estimated_downloads == len(plan.requestable_points)


def test_gridded_model_shares_one_download():
    plan = plan_box_samples("hrrr", _plains())
    assert plan.point_only_provider is False
    assert plan.estimated_downloads == 1


# -- lattice structure ----------------------------------------------------- #


def test_lattice_is_rectangular_and_row_zero_is_north():
    plan = plan_box_samples("hrrr", _plains(), target_points=25)
    assert plan.count == plan.rows * plan.cols
    assert plan.shape == (plan.rows, plan.cols)
    north = plan.point_at(0, 0)
    south = plan.point_at(plan.rows - 1, 0)
    assert north.lat > south.lat
    assert north.lat == pytest.approx(37.0)
    assert south.lat == pytest.approx(34.0)


def test_lattice_spans_the_box_edges_inclusively():
    plan = plan_box_samples("hrrr", _plains(), target_points=25)
    west = plan.point_at(0, 0)
    east = plan.point_at(0, plan.cols - 1)
    assert west.lon == pytest.approx(-99.0)
    assert east.lon == pytest.approx(-95.0)


def test_request_ids_are_unique_stable_and_sortable():
    plan = plan_box_samples("hrrr", _plains(), target_points=25)
    ids = [point.request_id for point in plan.points]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert plan.point_at(0, 0).request_id == "r000c000"


def test_point_at_returns_none_outside_the_lattice():
    plan = plan_box_samples("hrrr", _plains(), target_points=16)
    assert plan.point_at(-1, 0) is None
    assert plan.point_at(0, plan.cols) is None


def test_thin_box_collapses_to_a_single_row_at_its_center():
    # Thinner north-to-south than the grid can resolve.
    region = BoxRegion.from_corners(35.00, -99.0, 35.02, -95.0)
    plan = plan_box_samples("rap", region)
    assert plan.rows == 1
    assert plan.point_at(0, 0).lat == pytest.approx(35.01, abs=1e-6)


# -- domain handling ------------------------------------------------------- #


def test_points_outside_the_domain_are_kept_but_flagged():
    # Straddles the southern edge of the configured CONUS domain.
    region = BoxRegion.from_corners(18.0, -105.0, 24.0, -99.0)
    plan = plan_box_samples("hrrr", region)
    assert plan.skipped_points
    assert plan.requestable_points
    # The lattice stays a true rectangle so a field map shows honest holes.
    assert plan.count == plan.rows * plan.cols
    assert all(not point.in_domain for point in plan.skipped_points)
    assert any("outside the" in note for note in plan.notes)


def test_box_entirely_outside_the_domain_is_refused():
    region = BoxRegion.from_corners(-40.0, 20.0, -30.0, 30.0)
    with pytest.raises(BoxSampleError, match="outside that domain"):
        plan_box_samples("hrrr", region)


def test_withheld_model_is_refused_with_its_reason():
    with pytest.raises(BoxSampleError, match="cannot produce a sounding"):
        plan_box_samples("aigfs", _plains())


def test_unknown_model_is_refused():
    with pytest.raises(KeyError):
        plan_box_samples("not-a-model", _plains())


# -- argument validation --------------------------------------------------- #


def test_spacing_and_target_are_mutually_exclusive():
    with pytest.raises(BoxSampleError, match="not both"):
        plan_box_samples(
            "hrrr", _plains(), spacing_km=10.0, target_points=10)


@pytest.mark.parametrize("value", (0.0, -5.0, float("nan")))
def test_invalid_spacing_is_rejected(value):
    with pytest.raises(BoxSampleError):
        plan_box_samples("hrrr", _plains(), spacing_km=value)


def test_invalid_target_points_is_rejected():
    with pytest.raises(BoxSampleError):
        plan_box_samples("hrrr", _plains(), target_points=0)


def test_plan_requires_a_box_region():
    with pytest.raises(BoxSampleError, match="BoxRegion"):
        plan_box_samples("hrrr", (34.0, -99.0, 37.0, -95.0))


# -- batch request conversion ---------------------------------------------- #


def test_requests_cover_in_domain_nodes_and_share_one_model_hour():
    plan = plan_box_samples("hrrr", _plains(), target_points=16)
    requests = box_requests(plan, run_time=RUN, fxx=6)
    assert len(requests) == len(plan.requestable_points)
    # One group means one download and one bulk decode.
    groups = {
        (item.model, item.run_time, item.fxx, item.member)
        for item in requests
    }
    assert len(groups) == 1
    first = requests[0]
    assert first.model == "hrrr"
    assert first.fxx == 6
    assert first.output == f"{first.id}.npz"
    assert first.id in {node.request_id for node in plan.requestable_points}


def test_requests_carry_the_member_and_location_prefix():
    plan = plan_box_samples("gefs", _plains(), target_points=9)
    requests = box_requests(
        plan, run_time=RUN, fxx=0, member="c00", loc="Test Box")
    assert all(item.member == "c00" for item in requests)
    assert requests[0].loc.startswith("Test Box ")


def test_requests_default_their_prefix_to_the_model_label():
    plan = plan_box_samples("hrrr", _plains(), target_points=9)
    requests = box_requests(plan, run_time=RUN)
    assert requests[0].loc.startswith("HRRR ")


def test_request_outputs_are_unique_relative_paths():
    plan = plan_box_samples("hrrr", _plains(), target_points=25)
    outputs = [item.output for item in box_requests(plan, run_time=RUN)]
    assert len(set(outputs)) == len(outputs)
    assert all(not output.startswith(("/", "\\")) for output in outputs)


def test_box_requests_rejects_a_non_plan():
    with pytest.raises(BoxSampleError, match="BoxSamplePlan"):
        box_requests(object(), run_time=RUN)


# -- description ----------------------------------------------------------- #


def test_describe_plan_reports_the_facts_a_reader_needs():
    plan = plan_box_samples("hrrr", _plains(), target_points=16)
    text = describe_plan(plan)
    for expected in ("HRRR", "Lattice", "Spacing", "Downloads", "native"):
        assert expected in text


def test_describe_plan_rejects_a_non_plan():
    with pytest.raises(BoxSampleError):
        describe_plan(object())


# -- forecast-hour sequences ----------------------------------------------- #


def test_normalize_hours_sorts_and_deduplicates():
    from sharpmod.box_sounding import normalize_hours

    assert normalize_hours([12, 0, 6, 6]) == (0, 6, 12)
    assert normalize_hours((3,)) == (3,)


@pytest.mark.parametrize(
    "hours",
    ((), (-1,), tuple(range(20)), ("a",)),
)
def test_normalize_hours_rejects_bad_input(hours):
    from sharpmod.box_sounding import normalize_hours

    with pytest.raises(BoxSampleError):
        normalize_hours(hours)


def test_hour_cap_is_stated_in_its_error():
    from sharpmod.box_sounding import MAX_BOX_HOURS, normalize_hours

    with pytest.raises(BoxSampleError, match=str(MAX_BOX_HOURS)):
        normalize_hours(range(MAX_BOX_HOURS + 1))


def test_sequence_requests_cover_every_hour_and_point():
    from sharpmod.box_sounding import box_sequence_requests

    plan = plan_box_samples("hrrr", _plains(), target_points=16)
    hours = (0, 6, 12)
    requests = box_sequence_requests(plan, run_time=RUN, hours=hours)
    assert len(requests) == len(plan.requestable_points) * len(hours)
    # Unique ids, and one model-hour group per hour: a sequence pays the
    # download once per hour, not once per point.
    assert len({item.id for item in requests}) == len(requests)
    groups = {
        (item.model, item.run_time, item.fxx, item.member)
        for item in requests
    }
    assert len(groups) == len(hours)
    assert {item.fxx for item in requests} == set(hours)


def test_sequence_outputs_are_grouped_by_hour_directory():
    from sharpmod.box_sounding import box_sequence_requests

    plan = plan_box_samples("hrrr", _plains(), target_points=9)
    requests = box_sequence_requests(plan, run_time=RUN, hours=(0, 3))
    outputs = [item.output.replace("\\", "/") for item in requests]
    assert all(path.startswith(("f000/", "f003/")) for path in outputs)
    assert len(set(outputs)) == len(outputs)
    # The leaf name is still the lattice id, so one hour can be analyzed alone.
    assert outputs[0].endswith(f"{plan.point_at(0, 0).request_id}.npz")


def test_sequence_request_ids_encode_hour_and_cell():
    from sharpmod.box_sounding import sequence_request_id

    plan = plan_box_samples("hrrr", _plains(), target_points=9)
    node = plan.point_at(0, 0)
    assert sequence_request_id(6, node) == f"f006{node.request_id}"


def test_sequence_requests_share_member_and_run():
    from sharpmod.box_sounding import box_sequence_requests

    plan = plan_box_samples("gefs", _plains(), target_points=9)
    requests = box_sequence_requests(
        plan, run_time=RUN, hours=(0, 3), member="c00", loc="Box")
    assert all(item.member == "c00" for item in requests)
    assert all(item.run_time == RUN for item in requests)
    assert requests[0].loc.startswith("Box ")


def test_sequence_budget_is_enforced_across_hours_and_points():
    from sharpmod.box_sounding import MAX_SEQUENCE_NODES, box_sequence_requests

    plan = plan_box_samples("hrrr", _plains(), target_points=256)
    with pytest.raises(BoxSampleError, match=str(MAX_SEQUENCE_NODES)):
        box_sequence_requests(plan, run_time=RUN, hours=range(12))


def test_sequence_requests_reject_a_non_plan():
    from sharpmod.box_sounding import box_sequence_requests

    with pytest.raises(BoxSampleError, match="BoxSamplePlan"):
        box_sequence_requests(object(), run_time=RUN, hours=(0,))
