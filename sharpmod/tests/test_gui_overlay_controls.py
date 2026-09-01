"""Coverage for the picker-side overlay controller.

These tests are the guard against request storms: they assert that scrubbing a
date or forecast-hour field cannot turn into one SPC request per intermediate
value, and that an overlay switched off issues nothing at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from qtpy.QtCore import QEventLoop, QTimer

from sharpmod import (
    gui_maps,
    map_overlays as mo,
    radar_mosaic,
    spc_outlook,
)
from sharpmod.gui_overlay_controls import (
    OutlookOverlayController,
    RadarOverlayController,
)

UTC = timezone.utc
RING = [[-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0], [-100.0, 45.0],
        [-100.0, 35.0]]
START = datetime(2024, 5, 1, 18, tzinfo=UTC)


def _pump(ms: int = 700) -> None:
    """Run the event loop for ``ms`` so debounced timers can fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    if hasattr(loop, "exec"):
        loop.exec()
    else:  # pragma: no cover - Qt5 naming
        loop.exec_()


def _layer(valid_from, valid_to):
    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [RING]})[0]
    shape = mo.OverlayShape(rings=rings, bounds=mo.bounds_of(rings),
                            stroke="#005500", fill="#66A366", label="MRGL")
    return mo.build_layer(
        spc_outlook.OVERLAY_KEY, "SPC convective outlook \u2014 Day 1",
        [shape], valid_from=valid_from, valid_to=valid_to)


@pytest.fixture
def recorded(monkeypatch):
    """Replace the network fetch with a recorder returning nothing."""
    seen: list[datetime] = []

    def fake_fetch(valid_time, **kwargs):
        seen.append(valid_time)
        return None

    monkeypatch.setattr(spc_outlook, "fetch_layer", fake_fetch)
    return seen


@pytest.fixture
def bound(qt_app):
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        yield controller, widget
    finally:
        controller.shutdown()
        widget.close()


def test_disabled_controller_never_fetches(qt_app, recorded):
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget)  # default: off
    try:
        assert not controller.is_enabled()
        for day in range(10):
            controller.set_valid_time(START + timedelta(days=day))
        _pump(700)
        assert recorded == [], "an overlay that is off must issue no requests"
        assert widget.overlay_keys() == ()
        # The map still learns the time, so its legend can reason about it.
        assert widget.valid_time() == START + timedelta(days=9)
    finally:
        controller.shutdown()
        widget.close()


def test_rapid_scrubbing_collapses_to_one_request(bound, recorded):
    """Dragging a date field 40 days must not issue 40 requests."""
    controller, _widget = bound
    for day in range(40):
        controller.set_valid_time(START + timedelta(days=day))
    assert recorded == [], "nothing may be sent while the value is still moving"

    _pump(700)
    assert len(recorded) == 1, "40 selections must collapse into one request"
    assert recorded[0] == START + timedelta(days=39), \
        "the request must use the value the user landed on"


def test_deliberate_steps_each_resolve(bound, recorded):
    controller, _widget = bound
    for day in range(4):
        controller.set_valid_time(START + timedelta(days=100 + day))
        _pump(450)
    assert len(recorded) == 4


def test_switching_off_cancels_a_pending_request(bound, recorded):
    controller, widget = bound
    controller.set_valid_time(START)
    controller.set_enabled(False)
    _pump(700)
    assert recorded == []
    assert widget.overlay_keys() == ()


def _tracked_fetch(monkeypatch, valid_from, valid_to):
    """Resolve through the controller, recording each request."""
    calls: list[datetime] = []

    def fetch(valid_time, **kwargs):
        calls.append(valid_time)
        return _layer(valid_from, valid_to)

    monkeypatch.setattr(spc_outlook, "fetch_layer", fetch)
    return calls


def test_hours_inside_one_issuance_need_no_request(bound, monkeypatch):
    """Stepping forecast hours within a single issuance must be free.

    The layer has to be resolved through the controller rather than attached
    behind its back: it records which resolution produced the layer, and one it
    never resolved carries no such basis, so it would rightly refetch once to
    establish one.
    """
    controller, widget = bound
    valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
    valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
    calls = _tracked_fetch(monkeypatch, valid_from, valid_to)

    # 20Z onwards all fall under the 2000Z issuance of the same convective day.
    controller.set_valid_time(datetime(2024, 5, 1, 20, tzinfo=UTC))
    _pump()
    assert len(calls) == 1, "the first selection resolves the outlook"
    assert widget.overlay(spc_outlook.OVERLAY_KEY) is not None

    for hour in (21, 22, 23):
        controller.set_valid_time(datetime(2024, 5, 1, hour, tzinfo=UTC))
    _pump()
    assert len(calls) == 1, "every step stayed under the same issuance"


def test_crossing_an_issuance_boundary_does_refetch(bound, monkeypatch):
    """The regression: 18Z and 00Z share a candidate set but not an issuance.

    Every issuance of a convective day expires at the same 12Z, so the 1630Z
    outlook still covers 00Z the next morning even though the 2000Z update has
    superseded it for that hour. Coverage therefore cannot decide this, and
    neither can the mere set of available products, since both hours can reach
    exactly the same ones.
    """
    controller, _widget = bound
    valid_from = datetime(2026, 4, 15, 13, tzinfo=UTC)
    valid_to = datetime(2026, 4, 16, 12, tzinfo=UTC)
    calls = _tracked_fetch(monkeypatch, valid_from, valid_to)

    controller.set_valid_time(datetime(2026, 4, 15, 18, tzinfo=UTC))
    _pump()
    assert len(calls) == 1

    # 00Z the next day: still inside the loaded window, still the same day, but
    # the 2000Z issuance now applies.
    controller.set_valid_time(datetime(2026, 4, 16, 0, tzinfo=UTC))
    _pump()
    assert len(calls) == 2, \
        "moving past the 2000Z issuance must resolve again"


def test_leaving_the_window_does_request(bound, recorded):
    controller, widget = bound
    valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
    valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
    widget.set_overlay(spc_outlook.OVERLAY_KEY, _layer(valid_from, valid_to))

    controller.set_valid_time(valid_to + timedelta(hours=6))
    _pump(700)
    assert len(recorded) == 1


def test_repeating_the_same_time_is_idempotent(bound, recorded):
    controller, _widget = bound
    for _ in range(10):
        controller.set_valid_time(START)
    _pump(700)
    assert len(recorded) == 1


def test_toggling_back_on_reuses_a_covering_layer(qt_app, recorded):
    """Hiding and re-showing must not refetch geometry already in hand."""
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
        valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
        layer = _layer(valid_from, valid_to)
        controller.set_valid_time(valid_from.replace(hour=18))
        widget.set_overlay(spc_outlook.OVERLAY_KEY, layer)
        recorded.clear()

        controller.set_enabled(False)
        # Hidden, not detached: the geometry stays so re-enabling is free.
        assert not widget.is_overlay_visible(spc_outlook.OVERLAY_KEY)
        assert widget._visible_overlays() == []
        assert widget.overlay(spc_outlook.OVERLAY_KEY) is layer

        controller.set_enabled(True)
        _pump(700)

        assert recorded == [], "the layer was already covering this time"
        assert widget.overlay(spc_outlook.OVERLAY_KEY) is layer
        assert widget.is_overlay_visible(spc_outlook.OVERLAY_KEY)
    finally:
        controller.shutdown()
        widget.close()


def test_a_resolved_layer_is_attached_and_described(qt_app, monkeypatch):
    widget = gui_maps.StationMapWidget([])
    valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
    valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
    layer = _layer(valid_from, valid_to)
    monkeypatch.setattr(spc_outlook, "fetch_layer",
                        lambda valid_time, **kwargs: layer)
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        controller.set_valid_time(valid_from.replace(hour=18))
        _pump(700)
        assert widget.overlay(spc_outlook.OVERLAY_KEY) is layer
        assert "SPC convective outlook" in controller._status.text()
    finally:
        controller.shutdown()
        widget.close()


def test_no_outlook_reports_plainly_without_an_error(qt_app, recorded):
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        controller.set_valid_time(datetime(1998, 6, 1, 18, tzinfo=UTC))
        _pump(700)
        assert widget.overlay_keys() == ()
        text = controller._status.text()
        assert text.startswith("No ")
        assert "2020 onward" in text, "the reason should be stated"
        assert "unavailable" not in text.lower(), \
            "an absent product is not a failure"
    finally:
        controller.shutdown()
        widget.close()


def test_shutdown_stops_a_pending_request(bound, recorded):
    controller, _widget = bound
    controller.set_valid_time(START)
    controller.shutdown()
    _pump(700)
    assert recorded == []


# --------------------------------------------------------------------------- #
# hazard product selection
# --------------------------------------------------------------------------- #
def test_every_product_is_offered(bound):
    controller, _widget = bound
    offered = {controller._product.itemData(i)
               for i in range(controller._product.count())}
    assert offered == set(spc_outlook.PRODUCTS)
    assert controller.product() == spc_outlook.DEFAULT_PRODUCT


def test_changing_product_refetches(bound, recorded):
    controller, _widget = bound
    controller.set_valid_time(START)
    _pump(450)
    recorded.clear()

    controller.set_product("torn")
    assert controller.product() == "torn"
    _pump(700)
    assert len(recorded) == 1, "a different hazard needs its own request"


def test_changing_product_detaches_the_previous_hazard(bound, recorded):
    """The old hazard's layer must not survive into the new selection."""
    controller, widget = bound
    valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
    valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
    controller.set_valid_time(valid_from.replace(hour=18))
    widget.set_overlay(spc_outlook.OVERLAY_KEY, _layer(valid_from, valid_to))

    controller.set_product("hail")
    assert widget.overlay(spc_outlook.OVERLAY_KEY) is None, \
        "the categorical layer must be dropped when switching to hail"


def test_changing_product_while_off_still_detaches(bound, recorded):
    """Guards a regression that showed the previous hazard after re-enabling."""
    controller, widget = bound
    valid_from = datetime(2024, 5, 1, 13, tzinfo=UTC)
    valid_to = datetime(2024, 5, 2, 12, tzinfo=UTC)
    controller.set_valid_time(valid_from.replace(hour=18))
    widget.set_overlay(spc_outlook.OVERLAY_KEY, _layer(valid_from, valid_to))

    controller.set_enabled(False)
    controller.set_product("wind")
    assert widget.overlay(spc_outlook.OVERLAY_KEY) is None

    recorded.clear()
    controller.set_enabled(True)
    _pump(700)
    assert len(recorded) == 1, \
        "re-enabling after a product change must fetch the new hazard"


def test_the_worker_is_given_the_selected_product(qt_app, monkeypatch):
    seen: list[str] = []

    def fake_fetch(valid_time, **kwargs):
        seen.append(kwargs.get("product"))
        return None

    monkeypatch.setattr(spc_outlook, "fetch_layer", fake_fetch)
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget, enabled=True)
    try:
        controller.set_product("torn")
        controller.set_valid_time(START)
        _pump(700)
        assert seen == ["torn"]
    finally:
        controller.shutdown()
        widget.close()


# --------------------------------------------------------------------------- #
# supersession: a newer outlook replaces one already on screen
# --------------------------------------------------------------------------- #
#: A 21Z sounding on 20 May 2026; its convective day starts 20 May 12Z.
SUPERSEDE_TARGET = datetime(2026, 5, 20, 21, tzinfo=UTC)
#: Clock positions where SPC has issued the Day 3, Day 2, and Day 1 in turn.
AT_DAY3 = datetime(2026, 5, 18, 15, tzinfo=UTC)
AT_DAY2 = datetime(2026, 5, 19, 9, tzinfo=UTC)
AT_DAY1 = datetime(2026, 5, 20, 13, tzinfo=UTC)


@pytest.fixture
def fake_clock(monkeypatch):
    """Drive the controller's staleness check from a clock the test controls."""
    clock = {"now": AT_DAY3}
    served: list[int | None] = []
    real_signature = spc_outlook.resolution_signature

    def signature(valid_time, now=None, product=spc_outlook.DEFAULT_PRODUCT):
        return real_signature(valid_time, clock["now"], product)

    def fetch(valid_time, **kwargs):
        candidates = spc_outlook.candidates_for(
            valid_time, clock["now"], kwargs.get("product", "cat"))
        if not candidates:
            served.append(None)
            return None
        day = candidates[0].day
        served.append(day)
        return mo.build_layer(
            spc_outlook.OVERLAY_KEY, f"SPC convective outlook \u2014 Day {day}",
            [_shape()], subtitle=f"Day {day}",
            valid_from=datetime(2026, 5, 20, 12, tzinfo=UTC),
            valid_to=datetime(2026, 5, 21, 12, tzinfo=UTC))

    monkeypatch.setattr(spc_outlook, "resolution_signature", signature)
    monkeypatch.setattr(spc_outlook, "fetch_layer", fetch)
    return clock, served


def _shape():
    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [RING]})[0]
    return mo.OverlayShape(rings=rings, bounds=mo.bounds_of(rings),
                           stroke="#005500", fill="#66A366", label="MRGL")


def test_a_newer_outlook_day_replaces_the_one_on_screen(bound, fake_clock):
    """Day 3 gives way to Day 2 and then Day 1 for the same sounding."""
    clock, served = fake_clock
    controller, widget = bound

    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    assert served == [3]
    assert widget.overlay(spc_outlook.OVERLAY_KEY).subtitle == "Day 3"

    clock["now"] = AT_DAY2
    controller._check_superseded()
    _pump()
    assert served == [3, 2]
    assert widget.overlay(spc_outlook.OVERLAY_KEY).subtitle == "Day 2"

    clock["now"] = AT_DAY1
    controller._check_superseded()
    _pump()
    assert served == [3, 2, 1]
    assert widget.overlay(spc_outlook.OVERLAY_KEY).subtitle == "Day 1"


def test_coverage_alone_does_not_pin_a_stale_outlook(bound, fake_clock):
    """The Day 3 window still contains the target after Day 1 is issued."""
    clock, served = fake_clock
    controller, widget = bound
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    stale = widget.overlay(spc_outlook.OVERLAY_KEY)
    assert stale.covers(SUPERSEDE_TARGET), "sanity: it does still cover"

    clock["now"] = AT_DAY1
    controller.set_valid_time(SUPERSEDE_TARGET.replace(hour=22))
    _pump()
    assert served == [3, 1], "a covering but superseded layer must be replaced"


def test_nothing_refetches_while_no_new_outlook_exists(bound, fake_clock):
    clock, served = fake_clock
    controller, _widget = bound
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    assert len(served) == 1

    for _ in range(5):
        controller._check_superseded()
    _pump()
    assert len(served) == 1, "an unchanged basis must not cost a request"


def test_scrubbing_inside_the_window_stays_free(bound, fake_clock):
    """The rate-limit protection must survive the staleness check."""
    clock, served = fake_clock
    controller, _widget = bound
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    assert len(served) == 1

    for hour in (22, 23):
        controller.set_valid_time(SUPERSEDE_TARGET.replace(hour=hour))
    _pump()
    assert len(served) == 1, "hours inside the same outlook must not refetch"


def test_a_hazard_absent_on_day_three_is_retried_on_day_two(bound, fake_clock):
    clock, served = fake_clock
    controller, widget = bound
    controller.set_product("torn")
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    assert served == [None]
    assert widget.overlay(spc_outlook.OVERLAY_KEY) is None
    assert "No tornado probability" in controller._status.text()

    clock["now"] = AT_DAY2
    controller._check_superseded()
    _pump()
    assert served == [None, 2], \
        "an absent hazard must be retried once its outlook day is reachable"
    assert widget.overlay(spc_outlook.OVERLAY_KEY) is not None


def test_the_staleness_check_is_inert_while_switched_off(bound, fake_clock):
    clock, served = fake_clock
    controller, _widget = bound
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    controller.set_enabled(False)
    served.clear()

    clock["now"] = AT_DAY1
    controller._check_superseded()
    _pump()
    assert served == [], "a hidden overlay must not issue requests"


def test_the_staleness_timer_only_runs_while_enabled(qt_app):
    widget = gui_maps.StationMapWidget([])
    controller = OutlookOverlayController(widget)
    try:
        assert not controller._supersede_timer.isActive()
        controller.set_enabled(True)
        assert controller._supersede_timer.isActive()
        controller.set_enabled(False)
        assert not controller._supersede_timer.isActive()
        controller.set_enabled(True)
        controller.shutdown()
        assert not controller._supersede_timer.isActive()
    finally:
        widget.close()


def test_a_failure_does_not_settle_the_basis(bound, monkeypatch):
    """A transient failure must leave the next check free to retry."""
    controller, _widget = bound
    calls: list[int] = []

    def failing(valid_time, **kwargs):
        calls.append(1)
        raise OSError("network down")

    monkeypatch.setattr(spc_outlook, "fetch_layer", failing)
    controller.set_valid_time(SUPERSEDE_TARGET)
    _pump()
    assert calls, "sanity: it tried"
    assert controller._signature is None, \
        "a failure must not be recorded as a resolved basis"


# --------------------------------------------------------------------------- #
# Live radar overlay
# --------------------------------------------------------------------------- #
CONUS_VIEW = (-125.0, -66.0, 24.0, 50.0)
EUROPE_VIEW = (0.0, 30.0, 40.0, 60.0)


def _radar_png(size=8):
    """A real PNG, encoded by Qt so its checksums are correct."""
    from qtpy.QtCore import QBuffer
    from qtpy.QtGui import QColor, QImage

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(0, 255, 0, 180))
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def _look_at(widget, view):
    widget._lon0, widget._lon1, widget._lat0, widget._lat1 = view
    widget._invalidate()


class _RadarRecorder(list):
    """Records every stubbed fetch, and can be told to start failing."""

    failing = False


@pytest.fixture
def radar_calls(monkeypatch):
    """Replace the radar fetch with a recorder returning a usable frame."""
    seen = _RadarRecorder()

    def fake_fetch(product=None, *, size=None, opacity=1.0, now=None,
                   opener=None, should_cancel=None):
        seen.append({"product": product, "opacity": opacity})
        if seen.failing:
            raise radar_mosaic.RadarError("stubbed outage")
        spec = radar_mosaic.get_product(product)
        return mo.OverlayRaster(
            key=radar_mosaic.OVERLAY_KEY,
            title="%s (%s)" % (spec.label, spec.units),
            image_bytes=_radar_png(),
            bounds=radar_mosaic.COVERAGE_BOUNDS,
            retrieved_at=datetime.now(UTC),
            update_interval_s=spec.update_interval_s,
            opacity=opacity,
            attribution=radar_mosaic.ATTRIBUTION,
        )

    monkeypatch.setattr(radar_mosaic, "fetch_frame", fake_fetch)
    return seen


@pytest.fixture
def radar(qt_app):
    widget = gui_maps.StationMapWidget([])
    widget.resize(640, 480)
    _look_at(widget, CONUS_VIEW)
    controller = RadarOverlayController(widget)  # default: off
    try:
        yield controller, widget
    finally:
        controller.shutdown()
        widget.close()


def test_a_disabled_radar_overlay_never_fetches(radar, radar_calls):
    """It is an embellishment on a picker; it must not poll before asked."""
    controller, widget = radar
    assert not controller.is_enabled()
    _pump(400)
    assert radar_calls == []
    assert widget.overlay(radar_mosaic.OVERLAY_KEY) is None
    assert not controller._refresh_timer.isActive()


def test_enabling_fetches_exactly_one_frame(radar, radar_calls):
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    assert len(radar_calls) == 1
    raster = widget.overlay(radar_mosaic.OVERLAY_KEY)
    assert raster is not None
    assert widget.is_overlay_visible(radar_mosaic.OVERLAY_KEY)
    assert controller._refresh_timer.isActive()


def test_the_refresh_cadence_is_not_a_tight_poll(radar, radar_calls):
    """This timer really does reach the network each time it fires."""
    controller, _widget = radar
    controller.set_enabled(True)
    _pump(400)
    assert controller._refresh_timer.interval() >= 60_000


def test_an_opacity_change_costs_no_request(radar, radar_calls):
    """Dragging a slider must not re-download a full-extent frame."""
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    before = len(radar_calls)
    payload = widget.overlay(radar_mosaic.OVERLAY_KEY).image_bytes

    for value in (30, 45, 60, 75, 90):
        controller._opacity.setValue(value)
    _pump(400)

    assert len(radar_calls) == before, "an opacity drag issued a request"
    raster = widget.overlay(radar_mosaic.OVERLAY_KEY)
    assert raster.opacity == pytest.approx(0.90)
    assert raster.image_bytes is payload, "the payload object must be reused"


def test_switching_product_detaches_the_previous_frame(radar, radar_calls):
    """Otherwise the old product shows under the new product's name."""
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    before = len(radar_calls)

    controller.set_product("echo-tops")
    _pump(600)

    assert len(radar_calls) == before + 1
    assert radar_calls[-1]["product"] == "echo-tops"
    assert "echo tops" in widget.overlay(
        radar_mosaic.OVERLAY_KEY).title.lower()


def test_switching_off_keeps_the_frame_but_stops_the_timer(radar, radar_calls):
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    before = len(radar_calls)

    controller.set_enabled(False)
    _pump(400)

    assert len(radar_calls) == before, "no request on the way out"
    assert not controller._refresh_timer.isActive()
    assert not widget.is_overlay_visible(radar_mosaic.OVERLAY_KEY)
    # Kept, so switching back on costs nothing.
    assert widget.overlay(radar_mosaic.OVERLAY_KEY) is not None


def test_switching_back_on_reuses_a_fresh_frame(radar, radar_calls):
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    controller.set_enabled(False)
    _pump(200)
    before = len(radar_calls)

    controller.set_enabled(True)
    _pump(600)

    assert len(radar_calls) == before, "a fresh frame must be reused"
    assert widget.is_overlay_visible(radar_mosaic.OVERLAY_KEY)


def test_a_map_outside_coverage_spends_nothing(radar, radar_calls):
    """MRMS is CONUS only and this application picks points worldwide."""
    controller, widget = radar
    _look_at(widget, EUROPE_VIEW)

    controller.set_enabled(True)
    _pump(600)

    assert radar_calls == [], "a request was spent on an invisible frame"
    assert not widget.is_overlay_visible(radar_mosaic.OVERLAY_KEY)
    assert "outside" in controller._status.text().lower()


def test_panning_back_into_coverage_recovers_on_the_next_tick(radar,
                                                             radar_calls):
    """The map emits no view-changed signal, so the timer is the recovery path."""
    controller, widget = radar
    _look_at(widget, EUROPE_VIEW)
    controller.set_enabled(True)
    _pump(400)
    assert radar_calls == []

    _look_at(widget, CONUS_VIEW)
    controller._on_refresh_tick()
    _pump(600)

    assert len(radar_calls) == 1
    assert widget.overlay(radar_mosaic.OVERLAY_KEY) is not None


def test_a_failure_leaves_the_previous_frame_on_screen(radar, radar_calls):
    """An ageing image plus a stated failure beats a blank map."""
    controller, widget = radar
    controller.set_enabled(True)
    _pump(600)
    kept = widget.overlay(radar_mosaic.OVERLAY_KEY)
    assert kept is not None

    radar_calls.failing = True
    controller.refresh()
    _pump(600)

    assert widget.overlay(radar_mosaic.OVERLAY_KEY) is kept
    assert "unavailable" in controller._status.text().lower()


def test_shutdown_is_bounded_and_repeatable(radar, radar_calls):
    controller, _widget = radar
    controller.set_enabled(True)
    _pump(600)

    controller.shutdown()
    assert not controller._refresh_timer.isActive()
    assert not controller._timer.isActive()
    controller.shutdown()  # must not raise


def test_the_radar_controller_offers_every_audited_product(radar):
    controller, _widget = radar
    offered = {controller._product.itemData(index)
               for index in range(controller._product.count())}
    assert offered == set(radar_mosaic.PRODUCTS)
    assert controller.product() == radar_mosaic.DEFAULT_PRODUCT


def test_opacity_never_reaches_invisible_or_fully_opaque(radar):
    """0 is an overlay the user turned on and cannot see; 1 hides the borders."""
    controller, _widget = radar
    assert controller._opacity.minimum() > 0
    assert controller._opacity.maximum() < 100
    controller._opacity.setValue(controller._opacity.minimum())
    assert 0.0 < controller.opacity() < 1.0
    controller._opacity.setValue(controller._opacity.maximum())
    assert 0.0 < controller.opacity() < 1.0
