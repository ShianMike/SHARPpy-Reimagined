"""T25 contracts for shared locator extent and persistence state."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sharpmod import locator_presentation as lp


POINT = (35.22, -97.44)


class _Collection:
    def __init__(self, **meta):
        self._meta = dict(meta)

    def getMeta(self, key):  # noqa: N802 - upstream spelling
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802 - upstream spelling
        self._meta[key] = value


def _contains(bounds, point):
    west, south, east, north = bounds
    lat, lon = point
    while lon < west:
        lon += 360.0
    while lon > east:
        lon -= 360.0
    return west <= lon <= east and south <= lat <= north


def test_map_and_area_tuple_orders_are_explicit_round_trips():
    assert lp.bounds_from_map_view((-100.0, -90.0, 30.0, 40.0)) == (
        -100.0, 30.0, -90.0, 40.0)
    assert lp.bounds_to_map_view((-100.0, 30.0, -90.0, 40.0)) == (
        -100.0, -90.0, 30.0, 40.0)
    assert lp.area_from_map_box((30.0, -100.0, 40.0, -90.0)) == (
        -100.0, 30.0, -90.0, 40.0)
    assert lp.area_to_map_box((-100.0, 30.0, -90.0, 40.0)) == (
        30.0, -100.0, 40.0, -90.0)


def test_custom_bounds_accept_an_antimeridian_crossing():
    bounds = lp.normalise_bounds((170.0, 10.0, -170.0, 20.0))

    assert bounds == pytest.approx((170.0, 10.0, 190.0, 20.0))


def test_corrupt_extreme_longitudes_are_rejected_without_wrap_iteration():
    assert lp.normalise_bounds((1.0e308, 10.0, -1.0e308, 20.0)) is None
    assert lp.normalise_bounds((520.0, 10.0, 560.0, 20.0)) == (
        520.0, 10.0, 560.0, 20.0)


def test_local_and_regional_are_distinct_sensible_extents():
    local = lp.LocatorPresentation(extent_mode=lp.EXTENT_LOCAL).effective_bounds(
        POINT)
    regional = lp.LocatorPresentation(
        extent_mode=lp.EXTENT_REGIONAL).effective_bounds(POINT)

    assert _contains(local, POINT)
    assert _contains(regional, POINT)
    assert 1.9 < local[3] - local[1] < 2.0
    assert regional[3] - regional[1] == pytest.approx(11.0)
    assert regional[2] - regional[0] > local[2] - local[0]


def test_fit_selected_area_contains_the_full_box_and_off_centre_point():
    area = (-101.0, 31.0, -91.0, 39.0)
    state = lp.LocatorPresentation(
        extent_mode=lp.EXTENT_SELECTED, selected_area=area)

    bounds = state.effective_bounds(POINT)

    assert bounds[0] < area[0] < area[2] < bounds[2]
    assert bounds[1] < area[1] < area[3] < bounds[3]
    assert _contains(bounds, POINT)


def test_fit_selected_area_keeps_broad_and_pole_touching_boxes_whole():
    broad = (-170.0, -75.0, 170.0, 75.0)
    high_latitude = (-20.0, 80.0, 10.0, 90.0)
    fitted = lp.fit_bounds((0.0, 0.0), broad)
    polar = lp.fit_bounds((85.0, -5.0), high_latitude)

    assert fitted[0] < broad[0] and fitted[2] > broad[2]
    assert fitted[1] <= broad[1] and fitted[3] >= broad[3]
    assert polar[0] < high_latitude[0] and polar[2] > high_latitude[2]
    assert polar[1] < high_latitude[1] and polar[3] == 90.0

    represented = lp.LocatorPresentation(extent_mode=lp.EXTENT_LOCAL)
    local = represented.effective_bounds((0.0, 0.0), represented_area=broad)
    assert local[0] < broad[0] and local[2] > broad[2]
    assert local[1] <= broad[1] and local[3] >= broad[3]


def test_follow_uses_the_main_map_exactly_without_erasing_pinned_custom_state():
    custom = (-99.0, 33.0, -95.0, 37.0)
    first_main = (-110.0, 25.0, -80.0, 48.0)
    second_main = (-105.0, 28.0, -88.0, 44.0)
    state = lp.LocatorPresentation(
        link_mode=lp.LINK_FOLLOW,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=custom,
        main_bounds=first_main,
    )

    assert state.effective_bounds(POINT) == first_main
    refreshed = state.updated(main_bounds=second_main)
    assert refreshed.effective_bounds(POINT) == second_main
    pinned = refreshed.updated(link_mode=lp.LINK_PINNED)
    assert pinned.effective_bounds(POINT) == custom
    assert pinned.custom_bounds == custom


def test_follow_without_a_main_view_names_the_fallback_extent():
    state = lp.LocatorPresentation(
        link_mode=lp.LINK_FOLLOW, extent_mode=lp.EXTENT_REGIONAL)

    assert "view unavailable" in state.summary()
    assert "Regional" in state.summary()
    assert state.effective_bounds(POINT) == lp.LocatorPresentation(
        extent_mode=lp.EXTENT_REGIONAL).effective_bounds(POINT)


def test_local_still_fits_the_area_a_mean_sounding_represents():
    represented = (-101.0, 31.0, -91.0, 39.0)
    bounds = lp.LocatorPresentation().effective_bounds(
        POINT, represented_area=represented)

    assert bounds[0] <= represented[0]
    assert bounds[1] <= represented[1]
    assert bounds[2] >= represented[2]
    assert bounds[3] >= represented[3]


def test_mapping_round_trip_keeps_locked_custom_and_follow_context():
    state = lp.LocatorPresentation(
        link_mode=lp.LINK_FOLLOW,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
        main_bounds=(-110.0, 25.0, -80.0, 48.0),
        selected_area=(-101.0, 31.0, -91.0, 39.0),
        scale_units="imperial",
    )

    restored = lp.LocatorPresentation.from_mapping(state.to_mapping())

    assert restored == state
    assert restored.summary() == "Follow main map"


def test_collection_state_is_portable_plain_metadata():
    collection = _Collection()
    state = lp.LocatorPresentation(
        link_mode=lp.LINK_PINNED,
        extent_mode=lp.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
    )

    lp.set_collection_presentation(collection, state)

    assert collection._meta[lp.PRESENTATION_META_KEY] == state.to_mapping()
    assert lp.collection_presentation(collection) == state


def test_overlay_status_records_source_state_without_payload_bytes():
    collection = _Collection()

    statuses = lp.set_overlay_status(
        collection,
        "spc_outlook",
        "unavailable",
        family="risk",
        product="torn",
        detail="Outside SPC outlook coverage",
    )

    status = statuses["spc_outlook"]
    assert status["state"] == "unavailable"
    assert status["product"] == "torn"
    assert status["detail"] == "Outside SPC outlook coverage"
    assert isinstance(status["updated_at"], datetime)
    assert status["updated_at"].tzinfo == timezone.utc
    assert "image_bytes" not in status
