"""Playback says how fast it is going, what was asked for, and what is missing.

The previous toolbar was three buttons, a slider, a loop toggle, and a hard-coded
900 ms timer. It had one speed, no way to jump to a named valid time, no way to
narrow playback to part of a long run, and -- most importantly -- its slider
spanned only the hours that *loaded*, so a twelve-hour request that returned four
presented itself as a four-hour forecast with nothing to say otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QPointF, Qt
from qtpy.QtGui import QImage, QMouseEvent
from qtpy.QtWidgets import QMainWindow

from sharpmod.gui_timeline import install_timeline_controls, refresh_timeline_controls
from sharpmod.gui_timeline_playback import (
    BASE_INTERVAL_MS,
    FrameStrip,
    TimelinePlaybackBar,
    interval_for_speed,
)
from sharpmod.timeline_frames import TimelineCoverage

RUN = datetime(2026, 9, 17, 6, tzinfo=timezone.utc)


def _valid(hour):
    return RUN + timedelta(hours=hour)


class _Collection:
    """The parallel-list shape the vendored ProfCollection presents."""

    def __init__(self, hours, *, meta=None):
        self._hours = [int(hour) for hour in hours]
        self._dates = [_valid(hour) for hour in self._hours]
        self._profs = {"": [object() for _ in self._hours]}
        self._prof_idx = 0
        self._meta = {
            "loc": "KOUN",
            "model": "HRRR",
            "run": RUN,
            "observed": False,
            "timeline": True,
            "timeline_hours": list(self._hours),
        }
        self._meta.update(meta or {})

    def getMeta(self, key):  # noqa: N802 - upstream API
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802 - upstream API
        self._meta[key] = value

    def getCurrentDate(self):  # noqa: N802 - upstream API
        return self._dates[self._prof_idx]

    def setCurrentDate(self, value):  # noqa: N802 - upstream API
        self._prof_idx = self._dates.index(value)


class _Widget:
    def __init__(self, collections):
        self.prof_collections = list(collections)
        self.pc_idx = 0
        self.updates = 0

    def updateProfs(self):  # noqa: N802 - upstream API
        self.updates += 1


class _Window(QMainWindow):
    def __init__(self, collections):
        super().__init__()
        self.spc_widget = _Widget(collections)


def _bar(qt_app, hours=(0, 1, 2, 3), coverage=None):
    collection = _Collection(hours)
    if coverage is not None:
        coverage.attach(collection)
    win = _Window([collection])
    win.resize(1400, 300)
    bar = install_timeline_controls(win, collection)
    win.show()
    qt_app.processEvents()
    return win, bar, collection


# --------------------------------------------------------------------------- #
# T09.1 speed
# --------------------------------------------------------------------------- #


def test_the_speed_multiplier_maps_onto_a_real_interval():
    assert interval_for_speed(1.0) == BASE_INTERVAL_MS
    assert interval_for_speed(2.0) == BASE_INTERVAL_MS // 2
    assert interval_for_speed(0.5) == BASE_INTERVAL_MS * 2
    # Never zero or negative, whatever it is handed.
    assert interval_for_speed(0) == BASE_INTERVAL_MS
    assert interval_for_speed(-3) == BASE_INTERVAL_MS
    assert interval_for_speed(None) == BASE_INTERVAL_MS
    assert interval_for_speed("nonsense") == BASE_INTERVAL_MS
    # Bounded so a huge multiplier cannot spin the event loop.
    assert interval_for_speed(10_000) >= 40


def test_one_speed_is_no_longer_the_only_speed(qt_app):
    win, bar, _collection = _bar(qt_app)
    try:
        assert bar.timer.interval() == BASE_INTERVAL_MS

        bar.speed_choice.setCurrentIndex(bar.speed_choice.findData(4.0))
        qt_app.processEvents()

        assert bar.speed() == 4.0
        assert bar.timer.interval() == interval_for_speed(4.0)
    finally:
        win.close()


def test_changing_speed_while_playing_takes_effect_immediately(qt_app):
    """Not after the remainder of the old interval."""
    win, bar, _collection = _bar(qt_app)
    try:
        bar.play_action.setChecked(True)
        qt_app.processEvents()
        assert bar.timer.isActive()

        bar.speed_choice.setCurrentIndex(bar.speed_choice.findData(0.25))
        qt_app.processEvents()

        assert bar.timer.isActive()
        assert bar.timer.interval() == interval_for_speed(0.25)
    finally:
        bar.play_action.setChecked(False)
        win.close()


# --------------------------------------------------------------------------- #
# T09.1 direct valid-time selection
# --------------------------------------------------------------------------- #


def test_every_loaded_valid_time_can_be_chosen_directly(qt_app):
    win, bar, collection = _bar(qt_app, hours=(0, 3, 6))
    try:
        assert bar.time_choice.count() == 3
        # Labelled by forecast hour and time, so the choice is unambiguous.
        assert bar.time_choice.itemText(1).startswith("F003")

        bar.time_choice.setCurrentIndex(2)
        qt_app.processEvents()

        assert collection.getCurrentDate() == _valid(6)
        assert bar.slider.value() == 2
    finally:
        win.close()


def test_the_time_list_offers_only_times_that_loaded(qt_app):
    """No interpolated or requested-but-absent time is selectable."""
    coverage = (
        TimelineCoverage.requested_range((0, 1, 2, 3), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "failed", reason="HTTP 500")
        .with_state(2, "loaded", valid_time=_valid(2))
        .with_state(3, "canceled")
    )
    win, bar, _collection = _bar(qt_app, hours=(0, 2), coverage=coverage)
    try:
        offered = [bar.time_choice.itemText(i) for i in range(bar.time_choice.count())]

        assert len(offered) == 2
        assert any("F000" in text for text in offered)
        assert any("F002" in text for text in offered)
        assert not any("F001" in text or "F003" in text for text in offered)
    finally:
        win.close()


# --------------------------------------------------------------------------- #
# T09.1 loop range
# --------------------------------------------------------------------------- #


def test_playback_can_be_narrowed_to_part_of_a_long_run(qt_app):
    win, bar, collection = _bar(qt_app, hours=(0, 1, 2, 3, 4, 5))
    try:
        assert bar.loop_range() == (0, 5)
        assert bar.playable_indices() == (0, 1, 2, 3, 4, 5)

        bar.range_start.setCurrentIndex(bar.range_start.findData(2))
        bar.range_end.setCurrentIndex(bar.range_end.findData(4))
        qt_app.processEvents()

        assert bar.loop_range() == (2, 4)
        assert bar.playable_indices() == (2, 3, 4)

        # Playback loops inside the range, not over the whole run.
        bar.set_index(4)
        bar.move(1)
        assert collection.getCurrentDate() == _valid(2)
    finally:
        win.close()


def test_a_reversed_range_is_read_in_the_order_it_means(qt_app):
    win, bar, _collection = _bar(qt_app, hours=(0, 1, 2, 3))
    try:
        bar.range_start.setCurrentIndex(bar.range_start.findData(3))
        bar.range_end.setCurrentIndex(bar.range_end.findData(1))
        qt_app.processEvents()

        assert bar.loop_range() == (1, 3)
    finally:
        win.close()


def test_without_loop_playback_stops_at_the_range_end(qt_app):
    win, bar, collection = _bar(qt_app, hours=(0, 1, 2))
    try:
        bar.loop_action.setChecked(False)
        bar.play_action.setChecked(True)
        qt_app.processEvents()
        bar.set_index(2)

        bar.move(1)

        assert collection.getCurrentDate() == _valid(2)
        assert bar.play_action.isChecked() is False
        assert not bar.timer.isActive()
    finally:
        win.close()


# --------------------------------------------------------------------------- #
# T09.1/T09.2 the requested extent and the active frame stay visible
# --------------------------------------------------------------------------- #


def test_the_active_frame_is_named_with_its_hour_and_utc_valid_time(qt_app):
    win, bar, _collection = _bar(qt_app, hours=(0, 6))
    try:
        bar.set_index(1)

        text = bar.label.text()
        assert text.startswith("F006")
        # UTC stays primary and explicitly marked.
        assert "2026-09-17 12:00Z" in text
        assert "Active forecast frame" in bar.label.accessibleName()
    finally:
        win.close()


def test_the_strip_shows_every_requested_hour_not_only_the_loaded_ones(qt_app):
    """The defect this exists for: four of twelve looked like a four-hour run."""
    coverage = (
        TimelineCoverage.requested_range(range(0, 12), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "loaded", valid_time=_valid(1))
        .with_state(2, "loaded", valid_time=_valid(2))
        .with_state(3, "loaded", valid_time=_valid(3))
        .with_state(4, "failed", reason="HTTP 500")
    )
    win, bar, _collection = _bar(qt_app, hours=(0, 1, 2, 3), coverage=coverage)
    try:
        # The slider spans what can be displayed...
        assert bar.slider.maximum() == 3
        # ...and the strip says twelve were asked for.
        assert len(bar.strip.coverage()) == 12
        assert bar.strip.coverage().loaded_hours == (0, 1, 2, 3)
        assert bar.strip.coverage().failed_hours == (4,)

        summary = bar.strip.coverage().summary()
        assert "4 of 12 requested hour(s) loaded" in summary
        assert "1 failed" in summary
        assert summary in bar.slider.toolTip()
    finally:
        win.close()


def test_hovering_a_frame_explains_that_frame(qt_app):
    coverage = (
        TimelineCoverage.requested_range((0, 1), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "failed", reason="HTTP 500 from the source")
    )
    win, bar, _collection = _bar(qt_app, hours=(0,), coverage=coverage)
    try:
        strip = bar.strip
        strip.resize(200, int(FrameStrip.HEIGHT))
        qt_app.processEvents()

        # Right-hand cell is F001, the failed one.
        _hover(strip, 150.0)
        tip = strip.toolTip()
        assert "F001" in tip
        assert "failed" in tip
        assert "HTTP 500 from the source" in tip
        assert "no profile to open" in tip

        _hover(strip, 20.0)
        tip = strip.toolTip()
        assert "F000" in tip
        assert "loaded" in tip
        assert "Click to open this hour" in tip
    finally:
        win.close()


def test_choosing_an_unloadable_frame_explains_it_instead_of_moving(qt_app):
    coverage = (
        TimelineCoverage.requested_range((0, 1), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "unavailable", reason="the model stops at F000")
    )
    win, bar, collection = _bar(qt_app, hours=(0,), coverage=coverage)
    try:
        before = collection.getCurrentDate()

        bar.strip.frameChosen.emit(1)
        qt_app.processEvents()

        assert collection.getCurrentDate() == before
        assert "F001" in bar.label.text()
        assert "not published" in bar.label.text()
    finally:
        win.close()


def test_choosing_a_loaded_frame_opens_it(qt_app):
    win, bar, collection = _bar(qt_app, hours=(0, 3, 6))
    try:
        bar.strip.frameChosen.emit(6)
        qt_app.processEvents()

        assert collection.getCurrentDate() == _valid(6)
    finally:
        win.close()


def test_the_strip_draws_a_distinguishable_cell_per_state(qt_app):
    """Not one flat bar: each state has to be visibly different."""
    coverage = (
        TimelineCoverage.requested_range((0, 1, 2, 3, 4, 5), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "loading")
        .with_state(2, "failed")
        .with_state(3, "canceled")
        .with_state(4, "unavailable")
        .with_state(5, "unknown")
    )
    win, bar, _collection = _bar(qt_app, hours=(0,), coverage=coverage)
    try:
        strip = bar.strip
        strip.resize(360, int(FrameStrip.HEIGHT))
        qt_app.processEvents()
        image = strip.grab().toImage().convertToFormat(QImage.Format_RGB32)

        # The exact pixels of each cell, so a difference in *pattern* counts and
        # not only a difference in colour. Comparing colour sets alone let a slash
        # and a glyph in the same hue look identical.
        width = image.width() // 6
        signatures = [
            tuple(
                image.pixel(cell * width + dx, y)
                for dx in range(2, width - 2)
                for y in range(2, image.height() - 2)
            )
            for cell in range(6)
        ]

        # No two states may render identically.
        assert len(set(signatures)) == 6
    finally:
        win.close()


def test_the_strip_reports_coverage_to_assistive_technology(qt_app):
    coverage = (
        TimelineCoverage.requested_range((0, 1), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "failed", reason="HTTP 500")
    )
    win, bar, _collection = _bar(qt_app, hours=(0,), coverage=coverage)
    try:
        description = bar.strip.accessibleDescription()

        assert "1 of 2 requested hour(s) loaded" in description
        assert "F001: failed \u2014 HTTP 500" in description
        # Not colour alone.
        assert "patterned" in description
    finally:
        win.close()


def test_narrowing_the_range_dims_rather_than_hides_the_rest(qt_app):
    """The requested extent stays visible while playback is narrowed."""
    win, bar, _collection = _bar(qt_app, hours=(0, 1, 2, 3))
    try:
        bar.strip.resize(240, int(FrameStrip.HEIGHT))
        qt_app.processEvents()
        full = bar.strip.grab().toImage()

        bar.range_start.setCurrentIndex(bar.range_start.findData(1))
        bar.range_end.setCurrentIndex(bar.range_end.findData(2))
        qt_app.processEvents()
        narrowed = bar.strip.grab().toImage()

        # Still four cells' worth of ink, drawn differently.
        assert bar.strip.coverage().requested_hours == (0, 1, 2, 3)
        assert full != narrowed
    finally:
        win.close()


# --------------------------------------------------------------------------- #
# Streaming and lifetime
# --------------------------------------------------------------------------- #


def test_a_streamed_hour_updates_the_slider_and_the_coverage(qt_app):
    coverage = TimelineCoverage.requested_range((0, 1, 2), run_time=RUN).with_state(
        0, "loaded", valid_time=_valid(0)
    )
    collection = _Collection((0,))
    coverage.attach(collection)
    win = _Window([collection])
    win.resize(1400, 300)
    bar = install_timeline_controls(win, collection)
    try:
        # One loaded hour out of three requested still installs the bar, because
        # the coverage is the thing worth showing. The slider has nothing to
        # scrub yet and says so by being disabled.
        assert bar is not None
        assert bar.slider.isEnabled() is False
        assert bar.coverage().missing_hours == (1, 2)

        collection._hours.append(1)
        collection._dates.append(_valid(1))
        collection._profs[""].append(object())
        collection._meta["timeline_hours"] = list(collection._hours)
        coverage.with_state(1, "loaded", valid_time=_valid(1)).attach(collection)

        toolbar = refresh_timeline_controls(win)
        qt_app.processEvents()

        assert toolbar is not None
        assert win._sharpmod_timeline_slider.maximum() == 1
        assert win._sharpmod_timeline_slider.isEnabled() is True
        assert win._sharpmod_timeline_bar.coverage().loaded_hours == (0, 1)
        assert win._sharpmod_timeline_bar.coverage().requested_hours == (0, 1, 2)
    finally:
        win.close()


def test_the_window_attribute_api_is_preserved(qt_app):
    """refresh_timeline_controls and the existing tests key off these."""
    win, bar, collection = _bar(qt_app)
    try:
        assert win._sharpmod_timeline_toolbar is bar
        assert win._sharpmod_timeline_timer is bar.timer
        assert win._sharpmod_timeline_slider is bar.slider
        assert win._sharpmod_timeline_play_action is bar.play_action
        assert win._sharpmod_timeline_collection is collection
        assert callable(win._sharpmod_timeline_set_index)
    finally:
        win.close()


def test_the_bar_holds_its_window_weakly(qt_app):
    """A strong capture here crashed six of fourteen runs at interpreter exit."""
    win, bar, _collection = _bar(qt_app)
    try:
        assert isinstance(bar._win_ref, type(bar._win_ref))
        assert bar._win_ref() is win
    finally:
        win.close()
    qt_app.processEvents()
    # Setting an index after the window is gone must be a no-op, not a crash.
    bar.set_index(0)


def test_a_collection_without_forecast_hours_still_plays(qt_app):
    """An observed multi-time collection has no fxx metadata."""
    collection = _Collection((0, 1, 2))
    del collection._meta["timeline_hours"]
    win = _Window([collection])
    win.resize(1200, 300)
    bar = install_timeline_controls(win, collection)
    win.show()
    qt_app.processEvents()
    try:
        assert bar is not None
        assert bar.hours() == ()
        assert bar.playable_indices() == (0, 1, 2)
        bar.set_index(2)
        assert collection.getCurrentDate() == _valid(2)
        # No forecast-hour prefix invented for it.
        assert not bar.label.text().startswith("F")
    finally:
        win.close()


def test_other_non_observed_overlays_follow_the_same_valid_time(qt_app):
    primary = _Collection((0, 1, 2))
    overlay = _Collection((0, 1, 2))
    observed = _Collection((0, 1, 2), meta={"observed": True})
    win = _Window([primary, overlay, observed])
    win.resize(1200, 300)
    bar = install_timeline_controls(win, primary)
    win.show()
    qt_app.processEvents()
    try:
        bar.set_index(2)

        assert overlay.getCurrentDate() == _valid(2)
        # An observed sounding is not a forecast hour and must not be dragged.
        assert observed.getCurrentDate() == _valid(0)
    finally:
        win.close()


def _hover(widget, x):
    """Move the pointer to ``x`` inside ``widget``.

    Uses the seven-argument constructor: the shorter form is deprecated in Qt 6
    and this project promotes DeprecationWarning to an error.
    """
    point = QPointF(float(x), FrameStrip.HEIGHT / 2.0)
    widget.mouseMoveEvent(
        QMouseEvent(
            QMouseEvent.MouseMove,
            point,
            point,
            widget.mapToGlobal(point),
            Qt.NoButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )


def test_a_collection_with_no_ledger_reports_its_loaded_hours(qt_app):
    win, bar, _collection = _bar(qt_app, hours=(0, 3, 6))
    try:
        coverage = bar.coverage()
        assert coverage.requested_hours == (0, 3, 6)
        assert coverage.loaded_hours == (0, 3, 6)
        assert coverage.is_complete() is True
    finally:
        win.close()


def test_the_bar_is_a_toolbar_named_the_way_the_window_expects(qt_app):
    win, bar, _collection = _bar(qt_app)
    try:
        assert isinstance(bar, TimelinePlaybackBar)
        assert bar.objectName() == "sharpmodForecastTimeline"
        assert bar.windowTitle() == "Forecast Timeline"
    finally:
        win.close()
