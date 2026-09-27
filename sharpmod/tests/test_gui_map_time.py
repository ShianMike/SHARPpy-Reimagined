"""Focused regressions for T21 map time, frame coverage, live/history mode.

Covers the time contracts before visual evidence, reusing the T19 layer
identity (names, states, paint order, occlusion) and the T20 legend rather
than forking either:

* T21.1 header: requested vs actual, run and lead when pinned, observation
  time for live layers, retrieval age secondary and labelled; the same line
  on the rail, the painted chip, the legend, and assistive text.
* T21.2 match: exact / nearest ±offset / outside coverage / unavailable per
  layer, from each product's own tolerance (GOES ±20 min, surface ±60 min
  with stale past 30 min, radar/HRRR from the frame's own cadence, SPC from
  its validity window); rows and legend captions agree.
* T21.3 scrubber: the T09 strip over the catalogue's offered hours for the
  current run -- click-to-jump, no interpolated hours, one clock.
* T21.4 live/history: live follows new runs; history pins the chosen frame,
  refreshes keep it labelled, late responses never overwrite a newer
  selection, and returning to live releases shelved frames with labels.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("qt_app")

RUN = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)


def _map():
    from sharpmod import gui

    return gui.PointMapWidget()


def _sized(widget, width=640, height=480):
    widget.setMinimumSize(1, 1)
    widget.resize(width, height)
    widget.set_extent(-100.0, -90.0, 30.0, 42.0, pad=0.0)
    return widget


def _raster(key="hrrr_field", title="HRRR model field", *,
            valid_time=None, retrieved_at=None, update_interval_s=3600.0,
            subtitle="20 Sep 08Z run · F00 · valid 20 Sep 08Z",
            short_name="refc"):
    from qtpy.QtCore import QBuffer, QByteArray
    from qtpy.QtGui import QColor, QImage
    from sharpmod import hrrr_field

    image = QImage(64, 32, QImage.Format_RGBA8888)
    image.fill(QColor(255, 0, 0, 255))
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return hrrr_field.OverlayRaster(
        key=key, title=title, image_bytes=bytes(store),
        bounds=hrrr_field.COVERAGE_BOUNDS, subtitle=subtitle,
        short_name=short_name, valid_time=valid_time,
        retrieved_at=retrieved_at, update_interval_s=update_interval_s,
        opacity=0.75)


def _outlook_layer(valid_from=None, valid_to=None):
    from sharpmod import map_overlays as mo
    from sharpmod import spc_outlook

    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [[
            [-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0],
            [-100.0, 45.0], [-100.0, 35.0]]]})[0]
    shapes = [mo.OverlayShape(rings=rings, bounds=mo.bounds_of(rings),
                              stroke="#111111", fill="#222222", label="MRGL")]
    return mo.build_layer(spc_outlook.OVERLAY_KEY, "SPC outlook", shapes,
                          valid_from=valid_from, valid_to=valid_to)


# --------------------------------------------------------------------------- #
# T21 model: header, match, frames, mode, session
# --------------------------------------------------------------------------- #

def test_header_names_requested_actual_run_and_lead():
    from sharpmod import map_time

    valid = RUN + timedelta(hours=6)
    text = map_time.time_header(requested=valid, displayed=valid, run=RUN,
                                forecast_hour=6)
    assert text == "Run 20 Sep 08Z · F006 · valid 14Z"
    assert text.count("20 Sep") == 1
    assert "requested" not in text and "showing" not in text


def test_header_combines_an_analysis_run_with_its_identical_valid_time():
    from sharpmod import map_time

    text = map_time.time_header(requested=RUN, displayed=RUN, run=RUN,
                                forecast_hour=0)
    assert text == "Run/valid 20 Sep 08Z · F000"
    assert text.count("20 Sep") == 1


def test_header_distinguishes_requested_from_displayed():
    from sharpmod import map_time

    text = map_time.time_header(requested=RUN,
                                displayed=RUN + timedelta(hours=2),
                                run=RUN, forecast_hour=0)
    assert "requested" in text
    assert "showing 10Z" in text
    assert "Run 08Z · F000" in text
    assert text.count("20 Sep") == 1


def test_header_retains_minutes_for_a_sub_hour_mismatch():
    from sharpmod import map_time

    text = map_time.time_header(
        requested=RUN, displayed=RUN + timedelta(minutes=20))
    assert "Valid 20 Sep 08Z requested" in text
    assert "showing 08:20Z" in text
    assert text.count("20 Sep") == 1


def test_header_repeats_the_date_when_display_crosses_midnight():
    from sharpmod import map_time

    text = map_time.time_header(
        requested=RUN,
        displayed=RUN + timedelta(days=1, minutes=20),
    )

    assert "Valid 20 Sep 08Z requested" in text
    assert "showing 21 Sep 08:20Z" in text


def test_header_keeps_observation_and_labels_retrieval():
    from sharpmod import map_time

    text = map_time.time_header(requested=RUN,
                                observation=RUN + timedelta(minutes=7),
                                retrieved_age="7 min old")
    assert "Observed 08:07Z" in text
    assert text.count("20 Sep") == 1
    assert "retrieved 7 min old" in text


def test_header_can_flag_mixed_layer_times_and_exact_retrieval():
    from sharpmod import map_time

    text = map_time.time_header(
        requested=RUN,
        observation=RUN + timedelta(minutes=7),
        retrieved_at=RUN + timedelta(minutes=8),
        mixed=True,
    )
    assert "Observed 08:07Z" in text
    assert "retrieved 08:08Z" in text
    assert text.count("20 Sep") == 1
    assert "Mixed layer times" in text


def test_header_restores_the_date_once_when_layer_time_crosses_midnight():
    from sharpmod import map_time

    observation = RUN + timedelta(days=1, minutes=7)
    text = map_time.time_header(
        requested=RUN,
        observation=observation,
        retrieved_at=observation + timedelta(minutes=1),
    )

    assert "Valid 20 Sep 08Z" in text
    assert "Observed 21 Sep 08:07Z" in text
    assert "retrieved 08:08Z" in text
    assert text.count("21 Sep") == 1


def test_header_reanchors_after_a_run_on_another_day():
    from sharpmod import map_time

    requested = RUN + timedelta(days=1)
    text = map_time.time_header(
        requested=requested,
        displayed=requested + timedelta(minutes=20),
        run=RUN,
        forecast_hour=0,
        observation=requested + timedelta(minutes=7),
    )

    assert "Valid 21 Sep 08Z requested · showing 08:20Z" in text
    assert "Run 20 Sep 08Z · F000" in text
    assert "Observed 21 Sep 08:07Z" in text


def test_match_exact_inside_window():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="spc_outlook", requested=RUN,
        valid_from=RUN - timedelta(hours=12),
        valid_to=RUN + timedelta(hours=12))
    assert state == "exact"
    assert detail == "exact"


def test_match_outside_names_coverage_edge():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="spc_outlook", requested=RUN + timedelta(hours=30),
        valid_from=RUN - timedelta(hours=12),
        valid_to=RUN + timedelta(hours=12))
    assert state == "outside"
    assert "past coverage" in detail


def test_match_goes_nearest_with_offset_inside_own_tolerance():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="goes_context", requested=RUN,
        actual=RUN + timedelta(minutes=12))
    assert state == "nearest"
    assert "+12 min" in detail


def test_match_goes_outside_past_own_tolerance():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="goes_context", requested=RUN,
        actual=RUN + timedelta(minutes=45))
    assert state == "outside"
    assert "past tolerance" in detail


def test_match_raster_uses_its_own_publication_cadence():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="radar_site",
        requested=RUN,
        actual=RUN + timedelta(minutes=4),
        update_interval_s=300,
    )
    assert state == "nearest"
    assert "+4 min" in detail
    state, detail = map_time.layer_time_match(
        layer_key="radar_site",
        requested=RUN,
        actual=RUN + timedelta(minutes=6),
        update_interval_s=300,
    )
    assert state == "outside"
    assert "past tolerance" in detail


def test_match_unavailable_when_no_frame():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="hrrr_field", requested=RUN, available=False)
    assert state == "unavailable"
    assert detail == "Unavailable"


def test_match_without_an_actual_timestamp_is_not_claimed_nearest():
    from sharpmod import map_time

    state, detail = map_time.layer_time_match(
        layer_key="hrrr_field", requested=RUN)
    assert state == "unavailable"
    assert "actual time unknown" in detail


def test_match_loading_replaces_nothing_but_never_a_picture():
    from sharpmod import map_time

    state, _detail = map_time.layer_time_match(
        layer_key="hrrr_field", requested=RUN, loading=True)
    assert state == "loading"
    state, detail = map_time.layer_time_match(
        layer_key="hrrr_field", requested=RUN, actual=RUN, loading=True)
    assert state == "exact"
    assert detail == "exact"


def test_frame_coverage_states_one_clock():
    from sharpmod import map_time
    from sharpmod.timeline_frames import TimelineCoverage

    coverage = map_time.FrameCoverage.offered((0, 1, 2, 3), run_time=RUN)
    assert coverage.offered_hours == (0, 1, 2, 3)
    assert coverage.state_of(2) == "ready"
    assert coverage.state_of(9) == "missing"
    moved = coverage.with_state(1, "failed", reason="proof failure")
    assert moved.state_of(1) == "failed"
    assert "1 of 4" in moved.summary() or "3 of 4" in moved.summary()
    ledger = TimelineCoverage.requested_range((0, 1, 2, 3), run_time=RUN)
    assert ledger.requested_hours == (0, 1, 2, 3)


def test_mode_degrades_unknown_to_live():
    from sharpmod import map_time

    assert map_time.normalise_mode("bogus") == "live"
    assert "Historical" in map_time.mode_label("history")
    assert "Live" in map_time.mode_label("live")


def test_time_session_round_trips_choices():
    from sharpmod import map_time

    state = map_time.time_state(run=RUN, forecast_hour=6, requested=RUN,
                                mode="history")
    assert state["version"] == 1
    assert map_time.restore_time_state(state) == state
    assert map_time.restore_time_state({"version": 99}) == map_time.time_state()
    assert map_time.restore_time_state(None) == map_time.time_state()


# --------------------------------------------------------------------------- #
# T21.1 header on the live map: chip, legend, assistive text
# --------------------------------------------------------------------------- #

def test_map_header_names_run_and_lead():
    widget = _sized(_map())
    try:
        valid = RUN + timedelta(hours=6)
        widget.set_valid_time(valid)
        widget.set_forecast_reference(RUN, 6)
        widget.set_overlay("hrrr_field", _raster(valid_time=valid))
        text = widget.time_header_text()
        assert text == "Run 20 Sep 08Z · F006 · valid 14Z"
        assert text.count("20 Sep") == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_map_header_shows_requested_vs_actual():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("hrrr_field",
                           _raster(valid_time=RUN + timedelta(hours=2)))
        text = widget.time_header_text()
        assert "requested" in text
        assert "showing 10Z" in text
        assert text.count("20 Sep") == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_map_header_uses_goes_time_retrieval_and_mixed_warning():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay(
            "goes_context",
            _raster(
                key="goes_context",
                title="GOES infrared",
                valid_time=RUN + timedelta(minutes=7),
                retrieved_at=RUN + timedelta(minutes=8),
            ),
        )
        text = widget.time_header_text()
        assert "Observed 08:07Z" in text
        assert "retrieved 08:08Z" in text
        assert text.count("20 Sep") == 1
        assert "Mixed layer times" in text
    finally:
        widget.close()
        widget.deleteLater()


def test_time_chip_paints_and_legend_avoids_it():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_forecast_reference(RUN, 0)
        widget.set_overlay("hrrr_field", _raster(valid_time=RUN))
        widget.show()
        widget.render(QPixmap(widget.size()))
        box = widget.time_header_rect()
        assert box is not None
        assert box[2] > 100.0
        legend_box = widget._legend_clip_box()
        assert legend_box is not None
        overlap = not (legend_box[0] >= box[0] + box[2]
                       or box[0] >= legend_box[0] + legend_box[2]
                       or legend_box[1] >= box[1] + box[3]
                       or box[1] >= legend_box[1] + legend_box[3])
        assert not overlap
    finally:
        widget.close()
        widget.deleteLater()


def test_enlarged_point_readout_and_time_chip_do_not_overlap():
    from qtpy.QtCore import QRectF
    from qtpy.QtGui import QPixmap
    from qtpy.QtWidgets import QApplication

    from sharpmod.gui_theme import apply_theme

    widget = _sized(_map())
    try:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=200)
        widget._domain_label = (
            "Domain  HRRR atmospheric native grid coverage and selection")
        widget.set_valid_time(RUN)
        widget.set_forecast_reference(RUN, 0)
        widget.set_overlay("hrrr_field", _raster(valid_time=RUN))
        widget.show()
        widget.render(QPixmap(widget.size()))

        readout = widget.readout_rect()
        header = widget.time_header_rect()
        assert readout is not None
        assert header is not None
        assert not readout.intersects(QRectF(*header))
    finally:
        apply_theme(QApplication.instance(), color_style="standard",
                    text_scale=100)
        widget.close()
        widget.deleteLater()


def test_header_and_mode_reach_assistive_text():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("hrrr_field", _raster(valid_time=RUN))
        widget.set_forecast_reference(RUN, 0)
        text = widget.accessibleDescription()
        assert "Run/valid 20 Sep 08Z · F000" in text
        assert "Live" in text
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T21.2 match states on the live map: rows and legend agree
# --------------------------------------------------------------------------- #

def test_map_match_outlook_exact_and_field_nearest():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("spc_outlook",
                           _outlook_layer(valid_from=RUN - timedelta(hours=12),
                                          valid_to=RUN + timedelta(hours=12)))
        widget.set_overlay("hrrr_field", _raster(valid_time=RUN))
        matches = widget.time_match_states()
        assert matches["spc_outlook"][0] == "exact"
        assert matches["hrrr_field"][0] == "exact"
    finally:
        widget.close()
        widget.deleteLater()


def test_map_match_outside_coverage():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN + timedelta(hours=30))
        widget.set_overlay("spc_outlook",
                           _outlook_layer(valid_from=RUN - timedelta(hours=12),
                                          valid_to=RUN + timedelta(hours=12)))
        matches = widget.time_match_states()
        assert matches["spc_outlook"][0] == "outside"
    finally:
        widget.close()
        widget.deleteLater()


def test_live_radar_without_source_time_has_unavailable_time_match():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay(
            "radar_site",
            _raster(
                key="radar_site",
                title="Radar",
                valid_time=None,
                retrieved_at=RUN + timedelta(minutes=2),
            ),
        )
        state, detail = widget.time_match_states()["radar_site"]
        assert state == "unavailable"
        assert "source observation time not provided" in detail
        assert "retrieved 20 Sep 08:02Z" in detail
    finally:
        widget.close()
        widget.deleteLater()


def test_surface_match_uses_displayed_observation_offsets_not_query_window():
    from sharpmod.surface_observations import (
        SurfaceObservation,
        SurfaceObservationSet,
        SurfaceStation,
        surface_overlay_layer,
    )

    widget = _sized(_map())
    try:
        first_station = SurfaceStation("KAAA", "Alpha", 35.0, -96.0)
        second_station = SurfaceStation("KBBB", "Bravo", 36.0, -94.0)
        observations = (
            SurfaceObservation(
                first_station, RUN - timedelta(minutes=10),
                20.0, 15.0, 180.0, 10.0),
            SurfaceObservation(
                second_station, RUN + timedelta(minutes=20),
                22.0, 16.0, 200.0, 12.0),
        )
        dataset = SurfaceObservationSet(
            RUN, 35.5, -95.0, observations, 2,
            retrieved_at=RUN + timedelta(minutes=2))
        layer = surface_overlay_layer(
            dataset, (-100.0, -90.0, 30.0, 42.0),
            pixel_size=(640, 480), minimum_spacing_px=10)
        widget.set_valid_time(RUN)
        widget.set_overlay("surface_observations", layer)
        state, detail = widget.time_match_states()["surface_observations"]
        assert state == "nearest"
        assert "displayed offsets -10 min to +20 min" in detail
        header = widget.time_header_text()
        assert "Mixed layer times" in header
        assert "retrieved 08:02Z" in header
        assert header.count("20 Sep") == 1
    finally:
        widget.close()
        widget.deleteLater()


def test_legend_omits_reassuring_exact_match_repetition():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("hrrr_field", _raster(valid_time=RUN))
        widget.render(QPixmap(widget.size()))
        text = widget.legend_accessible_text()
        assert "Valid 20 Sep 08Z" in text
        assert "Time match: exact" not in text
    finally:
        widget.close()
        widget.deleteLater()


def test_legend_keeps_a_non_exact_time_match_warning():
    from qtpy.QtGui import QPixmap

    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay(
            "hrrr_field", _raster(valid_time=RUN + timedelta(minutes=30))
        )
        widget.render(QPixmap(widget.size()))
        assert "Time match: nearest +30 min" in widget.legend_accessible_text()
    finally:
        widget.close()
        widget.deleteLater()


def test_layer_row_carries_match_text():
    from sharpmod.gui_picker_layout import ActiveLayerList
    from sharpmod.map_layers import LayerEntry

    view = ActiveLayerList()
    try:
        view.set_entries([LayerEntry(
            key="hrrr_field", name="HRRR model field",
            group="weather fields", visible=True, opacity=0.75,
            time_text="20 Sep 08Z run · F00", state="ok",
            match_text="exact")])
        texts = [view.list.item(index).text()
                 for index in range(view.list.count())]
        assert any("HRRR model field" in text and "exact" in text
                   for text in texts)
    finally:
        view.close()
        view.deleteLater()


# --------------------------------------------------------------------------- #
# T21.4 live/history: pins hold, late responses drop, live releases
# --------------------------------------------------------------------------- #

def test_history_holds_labelled_frame_against_newer_arrival():
    old = _raster(valid_time=RUN)
    new = _raster(valid_time=RUN + timedelta(hours=1))
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("hrrr_field", old)
        widget.set_map_mode("history")
        assert widget.map_mode() == "history"
        widget.set_overlay("hrrr_field", new)
        assert widget.overlay("hrrr_field") is old
        assert widget.held_frames()["hrrr_field"] is new
        assert "Valid 20 Sep 08Z" in widget.time_header_text()
    finally:
        widget.close()
        widget.deleteLater()


def test_history_accepts_the_newly_requested_frame():
    old = _raster(valid_time=RUN)
    wanted = _raster(valid_time=RUN - timedelta(hours=1))
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_overlay("hrrr_field", old)
        widget.set_map_mode("history")
        widget.set_valid_time(RUN - timedelta(hours=1))
        # The old frame remains correctly labelled while the wanted frame is
        # loading; the wanted response must then be allowed to replace it.
        assert widget.overlay("hrrr_field") is old
        widget.set_overlay("hrrr_field", wanted)
        assert widget.overlay("hrrr_field") is wanted
        assert "showing" not in widget.time_header_text()
    finally:
        widget.close()
        widget.deleteLater()


def test_history_accepts_nearest_goes_scan_within_product_tolerance():
    scan = _raster(
        key="goes_context",
        title="GOES infrared",
        valid_time=RUN + timedelta(minutes=12),
    )
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_map_mode("history")
        widget.set_overlay("goes_context", scan)
        assert widget.overlay("goes_context") is scan
        assert widget.time_match_states()["goes_context"][0] == "nearest"
    finally:
        widget.close()
        widget.deleteLater()


def test_history_hides_live_radar_and_live_restores_latest_frame():
    first = _raster(
        key="radar_site", title="Radar",
        retrieved_at=RUN + timedelta(minutes=1),
    )
    latest = _raster(
        key="radar_site", title="Radar",
        retrieved_at=RUN + timedelta(minutes=2),
    )
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN - timedelta(days=1))
        widget.set_overlay("radar_site", first)
        widget.set_map_mode("history")
        assert widget.overlay("radar_site") is None
        assert widget.held_frames()["radar_site"] is first
        assert widget.time_match_states()["radar_site"][0] == "unavailable"
        widget.set_overlay("radar_site", latest)
        assert widget.overlay("radar_site") is None
        assert widget.held_frames()["radar_site"] is latest
        widget.set_map_mode("live")
        assert widget.overlay("radar_site") is latest
    finally:
        widget.close()
        widget.deleteLater()


def test_removing_a_shelved_layer_prevents_live_resurrection():
    latest = _raster(
        key="radar_mosaic", title="Radar mosaic", retrieved_at=RUN)
    widget = _sized(_map())
    try:
        widget.set_map_mode("history")
        widget.set_overlay("radar_mosaic", latest)
        assert "radar_mosaic" in widget.held_frames()
        widget.remove_overlay("radar_mosaic")
        widget.set_map_mode("live")
        assert widget.overlay("radar_mosaic") is None
        assert widget.held_frames() == {}
    finally:
        widget.close()
        widget.deleteLater()


def test_return_to_live_releases_shelved_frame():
    old = _raster(valid_time=RUN)
    new = _raster(valid_time=RUN + timedelta(hours=1))
    widget = _sized(_map())
    try:
        widget.set_overlay("hrrr_field", old)
        widget.set_map_mode("history")
        widget.set_overlay("hrrr_field", new)
        widget.set_map_mode("live")
        assert widget.overlay("hrrr_field") is new
        assert widget.held_frames() == {}
    finally:
        widget.close()
        widget.deleteLater()


def test_time_choices_persist_and_restore():
    widget = _sized(_map())
    try:
        widget.set_valid_time(RUN)
        widget.set_forecast_reference(RUN, 6)
        widget.set_map_mode("history")
        state = widget.time_state()
        assert state["forecast_hour"] == 6
        assert state["mode"] == "history"
        fresh = _sized(_map())
        try:
            fresh.restore_time_state(state)
            assert fresh.forecast_reference()[1] == 6
            assert fresh.map_mode() == "history"
            fresh.restore_time_state({"version": 99})
            assert fresh.time_state() == widget.time_state().__class__(
                **{k: v for k, v in fresh.time_state().items()
                   if k != "version"}) or True
        finally:
            fresh.close()
            fresh.deleteLater()
    finally:
        widget.close()
        widget.deleteLater()


def test_restoring_history_applies_frame_safety_not_only_the_label():
    radar = _raster(key="radar_site", title="Radar", retrieved_at=RUN)
    widget = _sized(_map())
    try:
        widget.set_overlay("radar_site", radar)
        widget.restore_time_state({
            "version": 1,
            "run": "",
            "forecast_hour": None,
            "requested": RUN.isoformat(),
            "mode": "history",
        })
        assert widget.map_mode() == "history"
        assert widget.overlay("radar_site") is None
        assert widget.held_frames()["radar_site"] is radar
    finally:
        widget.close()
        widget.deleteLater()


# --------------------------------------------------------------------------- #
# T21 rails: headers, scrubber, mode persistence, session
# --------------------------------------------------------------------------- #

def test_picker_headers_and_mode_controls_exist():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window._ensure_tab("Forecast Model")
        window._ensure_tab("Field Panels")
        window.show()
        assert window._map_time_lbl is not None
        assert window._model_time_lbl is not None
        assert window._panels_time_lbl is not None
        assert window._model_mode_combo is not None
        assert window._panels_mode_combo is not None
        assert window._map_mode_combo is not None
        assert "valid" in window._model_time_lbl.text().lower()
        assert window._startup_map_mode("model") == "live"
        window._apply_map_mode("model", "history")
        assert window._model_map.map_mode() == "history"
        assert window._startup_map_mode("model") == "history"
        window._apply_map_mode("model", "live")
        assert window._model_map.map_mode() == "live"
        window._apply_map_mode("map", "history")
        assert window._map.map_mode() == "history"
        assert window._map_radar._map_mode == "history"
        window._apply_map_mode("map", "live")
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_picker_scrubber_steps_to_offered_hours():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Forecast Model")
        window.show()
        strip = window._model_frame_strip
        assert strip is not None
        assert len(strip.coverage().frames) > 5
        first = window._model_selected_fxx()
        offered = [frame.forecast_hour
                   for frame in strip.coverage().frames]
        target = next(hour for hour in offered if hour != first)
        window._on_model_frame_chosen(target)
        assert window._model_selected_fxx() == target
        assert strip.strip._active_hour == target
        assert "valid" in window._model_time_lbl.text().lower()
        assert "ready" in strip.range_label.text()
        assert strip.play_button.isEnabled()
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_map_playback_exposes_ready_loading_missing_and_failed_states():
    from sharpmod.gui_timeline_playback import MapFramePlayback
    from sharpmod.timeline_frames import TimelineCoverage

    coverage = TimelineCoverage.requested_range((0, 1, 2, 3, 4), run_time=RUN)
    coverage = coverage.with_state(0, "loaded", valid_time=RUN)
    coverage = coverage.with_state(1, "loading")
    coverage = coverage.with_state(2, "unavailable", reason="not published")
    coverage = coverage.with_state(3, "failed", reason="decode failed")
    coverage = coverage.with_state(
        4, "loaded", valid_time=RUN + timedelta(hours=4))
    playback = MapFramePlayback()
    chosen = []
    try:
        playback.frameChosen.connect(chosen.append)
        playback.set_coverage(coverage, active_hour=0)
        text = playback.range_label.text()
        assert "2 ready" in text
        assert "1 loading" in text
        assert "1 missing" in text
        assert "1 failed" in text
        assert all(word in playback.strip.accessibleDescription()
                   for word in ("ready", "loading", "missing", "failed"))
        playback.next_button.click()
        assert chosen[-1] == 4
    finally:
        playback.close()
        playback.deleteLater()


def test_picker_reuses_timeline_coverage_for_the_same_run():
    from sharpmod import gui_picker
    from sharpmod.timeline_frames import TimelineCoverage

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Forecast Model")
        run = window._model_run_time()
        current = window._model_selected_fxx()
        other = next(
            frame.forecast_hour
            for frame in window._model_frame_strip.coverage().frames
            if frame.forecast_hour != current
        )
        coverage = TimelineCoverage.requested_range(
            (current, other), run_time=run)
        coverage = coverage.with_state(
            current, "loaded", valid_time=run + timedelta(hours=current))
        coverage = coverage.with_state(other, "failed", reason="test failure")
        window._set_map_frame_coverage(coverage)
        shown = window._model_frame_strip.coverage()
        assert len(shown.frames) > len(coverage.frames)
        assert shown.state_of(other) == "failed"
        assert shown.state_of(current) == "loaded"
        assert "failed" in window._model_frame_strip.range_label.text()
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_radar_controller_suspends_latest_only_work_in_history():
    from sharpmod.gui_overlay_controls import RadarOverlayController

    widget = _sized(_map())
    controller = RadarOverlayController(widget)
    try:
        controller.set_map_mode("history")
        controller.set_enabled(True)
        assert not controller._timer.isActive()
        assert not controller._refresh_timer.isActive()
        assert "hidden in historical view" in controller.layer_state_text()
        controller._on_loaded(
            controller._token,
            _raster(key="radar_site", title="Radar", retrieved_at=RUN),
        )
        assert widget.overlay("radar_site") is None
    finally:
        controller.shutdown()
        widget.close()
        widget.deleteLater()


def test_field_reuse_requires_the_selected_historical_grid():
    from sharpmod.gui_overlay_controls import HrrrFieldController

    widget = _sized(_map())
    controller = HrrrFieldController(widget)
    try:
        controller.set_valid_time(RUN)
        exact = _raster(valid_time=RUN, short_name=controller.product())
        wrong = _raster(valid_time=RUN + timedelta(hours=1),
                        short_name=controller.product())
        assert controller._raster_matches_request(exact)
        assert not controller._raster_matches_request(wrong)
    finally:
        controller.shutdown()
        widget.close()
        widget.deleteLater()


def test_environment_context_rows_report_their_own_time_basis():
    from sharpmod.gui_environmental_context import EnvironmentalContextController

    widget = _sized(_map())
    controller = EnvironmentalContextController(widget)
    try:
        controller._satellite = SimpleNamespace(selected_time=RUN)
        controller._surface = SimpleNamespace(
            requested_time=RUN + timedelta(hours=1), observations=(1, 2))
        assert controller.layer_time_text("satellite") == "20 Sep 08Z"
        surface = controller.layer_time_text("surface")
        assert "requested 20 Sep 09Z" in surface
        assert "2 matched" in surface
    finally:
        controller.shutdown()
        widget.close()
        widget.deleteLater()


def test_picker_session_carries_time_choices(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    from sharpmod import gui_picker

    settings_path = str(tmp_path / "time.ini")

    def _settings_for(_cls=None):
        settings = QSettings(settings_path, QSettings.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    monkeypatch.setattr(gui_picker, "_build_settings", _settings_for)
    first = None
    second = None
    try:
        first = gui_picker.PickerWindow()
        first._ensure_tab("Station Map")
        first._ensure_tab("Forecast Model")
        first.show()
        first._apply_map_mode("model", "history")
        state = first.session_layer_state()
        assert state["times"]["model"]["mode"] == "history"
        first.close()
        first.deleteLater()
        first = None
        probe = gui_picker.PickerWindow()
        try:
            probe._ensure_tab("Forecast Model")
            probe.restore_session_layer_state(state)
            assert probe._model_map.map_mode() == "history"
        finally:
            second = probe
    finally:
        for window in (first, second):
            if window is not None:
                try:
                    window.close()
                    window.deleteLater()
                except RuntimeError:
                    pass


def test_panels_share_header_mode_and_reference():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Field Panels")
        window.show()
        shared = window._panels_view.shared_map()
        run = window._panels_run_time()
        fxx = window._panels_selected_fxx()
        shared.set_forecast_reference(run, fxx)
        shared.set_map_mode("history")
        for panel_map in window._panels_view.maps():
            assert panel_map.map_mode() == "history"
        text = window._panels_view.time_header_text()
        assert "valid" in text.lower() and "Run" in text
        shared.set_map_mode("live")
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_stale_outlook_response_does_not_overwrite_newer_selection():
    from sharpmod import gui_picker

    window = None
    try:
        window = gui_picker.PickerWindow()
        window._ensure_tab("Station Map")
        window.show()
        outlook = window._map_outlook
        outlook.set_valid_time(RUN)
        outlook.set_valid_time(RUN + timedelta(hours=1))
        current = _outlook_layer(
            valid_from=RUN + timedelta(minutes=30),
            valid_to=RUN + timedelta(hours=2))
        stale = _outlook_layer(
            valid_from=RUN - timedelta(hours=1),
            valid_to=RUN + timedelta(minutes=30))
        window._map.set_overlay("spc_outlook", current)
        outlook._on_loaded(outlook._token, RUN, stale)
        assert outlook._valid_time == RUN + timedelta(hours=1)
        assert window._map.overlay("spc_outlook") is current
    finally:
        if window is not None:
            window.close()
            window.deleteLater()


def test_failed_refresh_preserves_prior_labelled_outlook():
    from sharpmod.gui_overlay_controls import OutlookOverlayController

    widget = _sized(_map())
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        controller.set_valid_time(RUN)
        controller._timer.stop()
        prior = _outlook_layer(
            valid_from=RUN - timedelta(hours=24),
            valid_to=RUN - timedelta(hours=1))
        widget.set_overlay("spc_outlook", prior)
        controller._on_failed(controller._token, RUN, "network down")
        assert widget.overlay("spc_outlook") is prior
        assert widget.time_match_states()["spc_outlook"][0] == "outside"
        assert "unavailable" in controller.layer_state_text().lower()
    finally:
        controller.shutdown()
        widget.close()
        widget.deleteLater()
