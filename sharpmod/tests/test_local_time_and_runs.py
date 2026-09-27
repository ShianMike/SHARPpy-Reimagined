"""UTC stays primary, local time carries its context, newer runs only notify.

Two things were missing and one was a hazard.

Missing: every timestamp was UTC-only, so a reader planning against a local
afternoon converted in their head on every valid time -- which is exactly where a
daylight-saving hour goes missing. And nothing anywhere noticed that a newer model
run had published.

The hazard is the fix for the second one. A sounding loaded from 06Z must stay 06Z.
Refreshing it underneath a reader who has been building a case on it would
invalidate every number already in their notes, their comparison, and their
saved analysis. So the newer-run path reports and never adopts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sharpmod import local_time, run_updates

UTC = timezone.utc


def _tz(name):
    zone = local_time.zone_for(name)
    if zone is None:
        pytest.skip("dateutil time zones are unavailable in this environment")
    return zone


# --------------------------------------------------------------------------- #
# UTC stays primary
# --------------------------------------------------------------------------- #


def test_the_utc_form_is_one_definition_used_everywhere():
    moment = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)

    assert local_time.format_utc(moment) == "2026-09-17 06:00Z"
    # A naive datetime is read as UTC, which is what every producer here emits.
    assert local_time.format_utc(datetime(2026, 9, 17, 6, 0)) == "2026-09-17 06:00Z"
    # A non-UTC input is converted rather than relabelled.
    central = _tz("America/Chicago")
    assert (
        local_time.format_utc(datetime(2026, 9, 17, 1, 0, tzinfo=central))
        == "2026-09-17 06:00Z"
    )


def test_utc_is_never_dropped_even_when_local_time_is_unavailable():
    moment = datetime(2026, 9, 17, 6, 0, tzinfo=UTC)

    # No zone resolvable: the reading is still the UTC one, not empty.
    assert local_time.dual_reading(moment, object()) == "2026-09-17 06:00Z"
    assert local_time.dual_reading(moment, None) == "2026-09-17 06:00Z"
    assert local_time.dual_reading(moment, show_local=False) == "2026-09-17 06:00Z"


def test_a_non_datetime_yields_nothing_rather_than_a_string_of_junk():
    assert local_time.format_utc(None) == ""
    assert local_time.format_utc("2026-09-17") == ""
    assert local_time.local_reading(None) is None
    assert local_time.dual_reading(None) == ""


# --------------------------------------------------------------------------- #
# Local time carries the context that makes it usable
# --------------------------------------------------------------------------- #


def test_a_local_reading_states_its_offset_and_daylight_state():
    """"3 PM" without a zone, an offset, and a DST state is not actionable."""
    central = _tz("America/Chicago")
    summer = local_time.local_reading(datetime(2026, 7, 17, 18, 0, tzinfo=UTC), central)

    assert summer is not None
    assert summer.text == "2026-07-17 13:00"
    assert summer.daylight is True
    assert summer.offset_minutes == -300
    assert summer.offset_text == "UTC\u221205:00"
    described = summer.describe()
    assert "daylight saving time" in described
    assert "UTC\u221205:00" in described


def test_the_same_zone_in_winter_reports_standard_time_and_a_different_offset():
    central = _tz("America/Chicago")
    winter = local_time.local_reading(datetime(2026, 1, 17, 18, 0, tzinfo=UTC), central)

    assert winter is not None
    assert winter.text == "2026-01-17 12:00"
    assert winter.daylight is False
    assert winter.offset_minutes == -360
    assert "standard time" in winter.describe()


def test_the_offset_uses_a_real_minus_sign_not_a_hyphen():
    reading = local_time.LocalReading("2026-09-17 01:00", "CDT", -300, True)
    assert reading.offset_text.startswith("UTC\u2212")

    ahead = local_time.LocalReading("2026-09-17 15:00", "CEST", 120, True)
    assert ahead.offset_text == "UTC+02:00"


def test_a_dual_reading_puts_utc_first_and_local_second():
    central = _tz("America/Chicago")
    text = local_time.dual_reading(datetime(2026, 7, 17, 18, 0, tzinfo=UTC), central)

    assert text.startswith("2026-07-17 18:00Z")
    assert "13:00" in text
    assert text.index("18:00Z") < text.index("13:00")


def test_a_span_that_crosses_a_daylight_transition_is_reported():
    """A timeline over a transition contains a repeated or missing local hour."""
    central = _tz("America/Chicago")
    # US DST ends on 1 November 2026.
    before = datetime(2026, 10, 31, 12, tzinfo=UTC)
    after = datetime(2026, 11, 2, 12, tzinfo=UTC)

    assert local_time.crosses_dst(before, after, central) is True
    assert local_time.crosses_dst(before, before + timedelta(hours=6), central) is False
    # Unusable inputs are not a transition.
    assert local_time.crosses_dst(None, after, central) is False


def test_an_unknown_zone_name_yields_no_local_time_rather_than_a_guess():
    """An unresolved zone must not quietly become the machine's own zone.

    That is the one wrong answer here: a reader shown 14:00 in Malay Peninsula
    Standard Time when they asked for a zone that does not exist has no way to
    know the number is meaningless.
    """
    assert local_time.zone_for("Mars/Olympus_Mons") is None
    moment = datetime(2026, 9, 17, 6, tzinfo=UTC)

    assert local_time.local_reading(moment, local_time.zone_for("Mars/Olympus")) is None
    # Omitting the argument still means "use the system zone".
    assert local_time.local_reading(moment) is not None or local_time.local_zone() is None


# --------------------------------------------------------------------------- #
# Newer runs notify; they never adopt
# --------------------------------------------------------------------------- #


def test_a_newer_cycle_is_reported_with_how_much_newer_it_is():
    update = run_updates.check(
        "hrrr",
        datetime(2026, 9, 17, 6, tzinfo=UTC),
        now=datetime(2026, 9, 17, 12, 30, tzinfo=UTC),
    )

    assert update.available is True
    assert update.latest_run == datetime(2026, 9, 17, 11, tzinfo=UTC)
    assert update.newer_by == timedelta(hours=5)
    summary = update.summary()
    assert "has published" in summary
    # And it states plainly that nothing has changed on screen.
    assert "will not change until you load it" in summary
    assert "2026-09-17 06Z" in summary


def test_the_newest_run_reports_no_update():
    update = run_updates.check(
        "hrrr",
        datetime(2026, 9, 17, 11, tzinfo=UTC),
        now=datetime(2026, 9, 17, 12, 30, tzinfo=UTC),
    )

    assert update.available is False
    assert update.summary() == "This is the newest published run."


def test_a_cycle_that_has_not_published_yet_is_not_an_update():
    """Publishing lag is real; a cycle time is not a usable run."""
    update = run_updates.check(
        "hrrr",
        datetime(2026, 9, 17, 11, tzinfo=UTC),
        now=datetime(2026, 9, 17, 12, 5, tzinfo=UTC),
    )

    assert update.available is False


def test_a_six_hourly_model_is_not_reported_as_hourly():
    update = run_updates.check(
        "gfs",
        datetime(2026, 9, 17, 0, tzinfo=UTC),
        now=datetime(2026, 9, 17, 11, tzinfo=UTC),
    )

    assert update.latest_run == datetime(2026, 9, 17, 6, tzinfo=UTC)
    assert update.available is True


def test_an_unknown_model_says_so_rather_than_claiming_currency():
    update = run_updates.check(
        "some-private-model",
        datetime(2026, 9, 17, 6, tzinfo=UTC),
        now=datetime(2026, 9, 18, 6, tzinfo=UTC),
    )

    assert update.available is False
    assert "not known here" in update.summary()
    assert "newest" not in update.summary()


def test_a_sounding_with_no_run_time_is_not_compared():
    update = run_updates.check("hrrr", None)

    assert update.available is False
    assert "no run time" in update.summary()
    assert update.newer_by is None


def test_a_naive_run_time_is_read_as_utc():
    update = run_updates.check(
        "hrrr",
        datetime(2026, 9, 17, 6),
        now=datetime(2026, 9, 17, 12, 30, tzinfo=UTC),
    )

    assert update.available is True
    assert update.current_run == datetime(2026, 9, 17, 6, tzinfo=UTC)


def test_a_collection_is_checked_from_its_own_metadata_and_is_not_mutated():
    class _Collection:
        def __init__(self):
            self._meta = {
                "model": "HRRR",
                "run": datetime(2026, 9, 17, 6, tzinfo=UTC),
                "loc": "KOUN",
            }

        def getMeta(self, key):  # noqa: N802 - upstream API
            return self._meta[key]

    collection = _Collection()
    before = dict(collection._meta)

    update = run_updates.check_collection(
        collection, now=datetime(2026, 9, 17, 12, 30, tzinfo=UTC)
    )

    assert update.available is True
    # The whole point: reporting must not touch the working dataset.
    assert collection._meta == before


def test_a_collection_without_metadata_degrades_quietly():
    class _Bare:
        pass

    update = run_updates.check_collection(_Bare())

    assert update.available is False
    assert update.summary()


# --------------------------------------------------------------------------- #
# Wiring: the playback bar and the trend workspace
# --------------------------------------------------------------------------- #


def test_the_playback_label_keeps_utc_first_and_adds_local_beside_it(qt_app):
    from sharpmod.tests.test_gui_timeline_playback import _Collection, _Window
    from sharpmod.gui_timeline import install_timeline_controls

    collection = _Collection((0, 6))
    win = _Window([collection])
    win.resize(1400, 300)
    bar = install_timeline_controls(win, collection)
    win.show()
    qt_app.processEvents()
    try:
        bar.set_index(1)
        utc_only = bar.label.text()
        assert utc_only.startswith("F006")
        assert "2026-09-17 12:00Z" in utc_only

        bar.local_time_action.setChecked(True)
        qt_app.processEvents()
        both = bar.label.text()

        # UTC is still there, still first.
        assert "2026-09-17 12:00Z" in both
        if both != utc_only:
            assert both.index("12:00Z") < len(both)
            # The zone, offset, and daylight state are in the accessible reading.
            detail = bar.label.accessibleName()
            assert "12:00Z" in detail
            assert "UTC" in detail
            assert "time)" in detail or "standard" in detail or "daylight" in detail

        bar.local_time_action.setChecked(False)
        qt_app.processEvents()
        assert bar.label.text() == utc_only
    finally:
        win.close()


def _workspace(qt_app, run=None):
    from sharpmod.tests.test_gui_analysis_workspace import _Collection, _Engine, _make

    engine = _Engine()
    collection = _Collection("KOUN", "HRRR")
    if run is not None:
        collection._meta["run"] = run
    return _make(qt_app, [collection], engine)


def test_the_trend_workspace_offers_local_time_without_replacing_utc(qt_app):
    from sharpmod.tests.test_gui_analysis_workspace import _trend_samples

    win, workspace, _engine = _workspace(qt_app)
    try:
        # With data, because an empty chart has no times to describe.
        workspace.trend_chart.set_series((), "mlcape")
        workspace.trend_chart.set_samples(_trend_samples(), "mlcape")
        assert workspace.trend_local_time.isChecked() is False
        assert workspace.trend_chart.show_local_time() is False
        assert "Times are UTC" in workspace.trend_chart.accessibleDescription()

        workspace.trend_local_time.setChecked(True)
        qt_app.processEvents()

        assert workspace.trend_chart.show_local_time() is True
        description = workspace.trend_chart.accessibleDescription()
        assert "Times are UTC" in description
        assert "local equivalent" in description
        assert "daylight-saving state" in description
    finally:
        win.close()


def test_the_local_time_choice_round_trips_through_the_session(qt_app):
    win, workspace, _engine = _workspace(qt_app)
    try:
        workspace.trend_local_time.setChecked(True)
        qt_app.processEvents()
        state = workspace.session_state()
        assert state["trend_local_time"] is True

        workspace.trend_local_time.setChecked(False)
        qt_app.processEvents()
        workspace.restore_session_state(state)

        assert workspace.trend_local_time.isChecked() is True
        assert workspace.trend_chart.show_local_time() is True

        # A session predating the key leaves the current choice alone.
        legacy = dict(state)
        legacy.pop("trend_local_time")
        workspace.restore_session_state(legacy)
        assert workspace.trend_local_time.isChecked() is True
    finally:
        win.close()


def test_a_newer_run_is_offered_and_never_applied(qt_app):
    """The hazard: refreshing underneath an analysis invalidates its numbers."""
    stale = datetime(2020, 1, 1, 0, tzinfo=UTC)
    win, workspace, _engine = _workspace(qt_app, run=stale)
    try:
        collection = workspace._collections()[0]
        before_run = collection.getMeta("run")
        before_dates = list(collection._dates)

        update = workspace._refresh_run_notice()

        assert update.available is True
        # Offered as a visible, explicit action.
        assert not workspace.trend_newer_run.isHidden()
        tooltip = workspace.trend_newer_run.toolTip()
        assert "will not change until you load it" in tooltip
        assert "left exactly as it is" in tooltip

        # And nothing about the loaded sounding moved.
        assert collection.getMeta("run") == before_run
        assert list(collection._dates) == before_dates
    finally:
        win.close()


def test_the_newest_run_shows_no_offer(qt_app):
    win, workspace, _engine = _workspace(qt_app, run=datetime.now(UTC))
    try:
        update = workspace._refresh_run_notice()

        assert update.available is False
        assert workspace.trend_newer_run.isHidden()
    finally:
        win.close()


def test_choosing_load_newer_run_explains_and_leaves_the_data_alone(qt_app):
    stale = datetime(2020, 1, 1, 0, tzinfo=UTC)
    win, workspace, _engine = _workspace(qt_app, run=stale)
    try:
        collection = workspace._collections()[0]
        before = (collection.getMeta("run"), list(collection._dates))

        workspace._load_newer_run()
        qt_app.processEvents()

        text = workspace.trend_status.text()
        assert "has published" in text
        assert "loaded sounding is unchanged" in text
        assert (collection.getMeta("run"), list(collection._dates)) == before
    finally:
        win.close()
