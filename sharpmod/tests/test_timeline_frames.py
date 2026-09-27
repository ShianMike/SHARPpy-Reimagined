"""A timeline says what it asked for, not just what happened to arrive.

Before this ledger, a request for twelve forecast hours that returned four
presented itself as a four-hour forecast. The other eight were not anywhere: the
vendored collection only stores hours that loaded, the failure strings lived on a
Qt worker that was deleted, and the validated requested range was discarded once
the fetch started. A reader could not tell "the model does not publish F009" from
"F009 failed to download" from "you cancelled before F009 started".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sharpmod.timeline_frames import (
    FRAME_STATES,
    STATE_LABELS,
    TimelineCoverage,
    TimelineFrame,
    normalize_state,
)

RUN = datetime(2026, 9, 17, 6, tzinfo=timezone.utc)


def _valid(hour):
    return RUN + timedelta(hours=hour)


# --------------------------------------------------------------------------- #
# The state vocabulary
# --------------------------------------------------------------------------- #


def test_every_state_the_goal_names_exists_and_is_distinct():
    assert FRAME_STATES == (
        "requested",
        "loading",
        "loaded",
        "failed",
        "canceled",
        "unavailable",
        "unknown",
    )
    # Each one has to read differently to a user, or the distinction is not real.
    assert len({STATE_LABELS[state] for state in FRAME_STATES}) == len(FRAME_STATES)


def test_worker_vocabulary_is_translated_rather_than_rejected():
    """The batch runner emits its own words, including a different spelling."""
    assert normalize_state("completed") == "loaded"
    assert normalize_state("cancelled") == "canceled"
    assert normalize_state("canceled") == "canceled"
    assert normalize_state("working") == "loading"
    assert normalize_state("queued") == "requested"
    assert normalize_state("error") == "failed"
    assert normalize_state("  LOADED  ") == "loaded"


def test_an_unrecognised_status_becomes_unknown_rather_than_raising():
    """This is fed from an event stream; a new word must not stop playback."""
    assert normalize_state("teleported") == "unknown"
    assert normalize_state(None) == "unknown"
    assert normalize_state("") == "unknown"


# --------------------------------------------------------------------------- #
# One frame
# --------------------------------------------------------------------------- #


def test_a_frame_starts_requested_and_names_itself_the_way_the_ui_does():
    frame = TimelineFrame(18)

    assert frame.state == "requested"
    assert frame.label == "F018"
    assert frame.available is False
    assert frame.pending is True
    assert frame.valid_time is None


def test_a_loaded_frame_must_carry_the_time_it_loaded():
    """Otherwise the ledger claims a usable hour nobody can address."""
    with pytest.raises(ValueError):
        TimelineFrame(3, "loaded")

    frame = TimelineFrame(3, "loaded", _valid(3))
    assert frame.available is True
    assert frame.pending is False


def test_a_frame_explains_itself_with_its_reason():
    frame = TimelineFrame(9, "failed", reason="HTTP 404 from the source")

    assert frame.describe() == "F009: failed \u2014 HTTP 404 from the source"
    # And without one it still names the state.
    assert TimelineFrame(9, "canceled").describe() == "F009: canceled"


def test_a_frame_rejects_a_non_datetime_valid_time():
    with pytest.raises(ValueError):
        TimelineFrame(1, "loaded", "2026-09-17T07:00Z")


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def test_a_request_opens_as_entirely_requested_and_nothing_else():
    coverage = TimelineCoverage.requested_range((0, 3, 6, 9), run_time=RUN)

    assert coverage.requested_hours == (0, 3, 6, 9)
    assert coverage.hours_in("requested") == (0, 3, 6, 9)
    assert coverage.loaded_hours == ()
    assert coverage.missing_hours == (0, 3, 6, 9)
    assert coverage.is_complete() is False
    assert coverage.run_time == RUN


def test_the_ledger_is_immutable_and_transitions_return_a_new_one():
    opened = TimelineCoverage.requested_range((0, 3))

    loading = opened.with_state(0, "loading")
    loaded = loading.with_state(0, "loaded", valid_time=_valid(0))

    # The original is untouched, which is what makes it safe to hand around.
    assert opened.state_of(0) == "requested"
    assert loading.state_of(0) == "loading"
    assert loaded.state_of(0) == "loaded"
    assert loaded.frame_for(0).valid_time == _valid(0)


def test_hours_are_kept_sorted_however_they_arrive():
    """Hours stream back out of order; the ledger is read in forecast order."""
    coverage = TimelineCoverage(
        (
            TimelineFrame(6, "loaded", _valid(6)),
            TimelineFrame(0, "loaded", _valid(0)),
            TimelineFrame(3, "failed"),
        )
    )

    assert coverage.requested_hours == (0, 3, 6)


def test_one_hour_cannot_hold_two_states():
    with pytest.raises(ValueError):
        TimelineCoverage((TimelineFrame(3, "loaded", _valid(3)), TimelineFrame(3, "failed")))


def test_the_ledger_is_keyed_by_forecast_hour_not_by_position():
    """The reason a late earlier hour cannot scramble the reasons.

    ``profile_timeline.append_collection`` re-sorts every index-keyed parallel
    list when a streamed hour lands, so an index-keyed ledger would re-associate
    each reason with a different hour.
    """
    coverage = (
        TimelineCoverage.requested_range((0, 3, 6))
        .with_state(6, "loaded", valid_time=_valid(6))
        .with_state(3, "failed", reason="download timed out")
    )

    # F000 arriving last must not move F003's reason onto it.
    coverage = coverage.with_state(0, "loaded", valid_time=_valid(0))

    assert coverage.frame_for(3).reason == "download timed out"
    assert coverage.frame_for(3).state == "failed"
    assert coverage.frame_for(0).reason is None
    assert coverage.loaded_hours == (0, 6)


def test_each_category_is_reported_separately():
    coverage = (
        TimelineCoverage.requested_range((0, 1, 2, 3, 4, 5))
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "loading")
        .with_state(2, "failed", reason="boom")
        .with_state(3, "canceled")
        .with_state(4, "unavailable", reason="the model stops at F003")
    )

    assert coverage.loaded_hours == (0,)
    assert coverage.loading_hours == (1,)
    assert coverage.failed_hours == (2,)
    assert coverage.canceled_hours == (3,)
    assert coverage.unavailable_hours == (4,)
    # Never transitioned, so still exactly what it was: requested.
    assert coverage.hours_in("requested") == (5,)
    assert coverage.missing_hours == (1, 2, 3, 4, 5)
    assert coverage.pending_hours == (1, 5)
    assert coverage.is_complete() is False


def test_a_finished_request_is_complete_even_with_failures():
    """Complete means resolved, not successful."""
    coverage = (
        TimelineCoverage.requested_range((0, 3))
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(3, "failed", reason="no data")
    )

    assert coverage.is_complete() is True
    assert coverage.loaded_hours == (0,)


def test_stopping_a_queue_marks_what_never_started_as_canceled():
    """Not unknown: the cause is known, and the two mean different things."""
    coverage = (
        TimelineCoverage.requested_range((0, 3, 6, 9))
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(3, "loading")
        .with_state(6, "failed", reason="boom")
    )

    stopped = coverage.with_pending_resolved(reason="the viewer was closed")

    assert stopped.canceled_hours == (3, 9)
    assert stopped.frame_for(9).reason == "the viewer was closed"
    # A resolved hour is not rewritten.
    assert stopped.frame_for(6).state == "failed"
    assert stopped.frame_for(6).reason == "boom"
    assert stopped.loaded_hours == (0,)
    assert stopped.is_complete() is True


def test_a_loaded_valid_time_survives_a_later_transition():
    coverage = (
        TimelineCoverage.requested_range((3,))
        .with_state(3, "loaded", valid_time=_valid(3))
        .with_state(3, "unknown", reason="superseded")
    )

    assert coverage.frame_for(3).valid_time == _valid(3)
    assert coverage.frame_for(3).state == "unknown"


def test_an_unrequested_hour_that_arrives_is_kept():
    coverage = TimelineCoverage.requested_range((0, 3)).with_state(
        1, "loaded", valid_time=_valid(1)
    )

    assert coverage.requested_hours == (0, 1, 3)
    assert coverage.loaded_hours == (1,)


def test_a_frame_can_be_found_by_the_valid_time_it_loaded():
    """The playback slider knows a valid time; it needs the frame's state."""
    coverage = TimelineCoverage.requested_range((0, 3)).with_state(
        3, "loaded", valid_time=_valid(3)
    )

    assert coverage.frame_at(_valid(3)).forecast_hour == 3
    assert coverage.frame_at(_valid(0)) is None
    assert coverage.frame_at(None) is None


def test_an_hour_never_requested_reads_as_unknown_not_as_loaded():
    coverage = TimelineCoverage.requested_range((0, 3))

    assert coverage.state_of(99) == "unknown"
    assert coverage.frame_for(99) is None
    assert coverage.frame_for("nonsense") is None


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def test_the_summary_counts_every_category_that_is_present():
    coverage = (
        TimelineCoverage.requested_range((0, 1, 2, 3))
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(1, "failed", reason="boom")
        .with_state(2, "unavailable")
    )

    summary = coverage.summary()

    assert "1 of 4 requested hour(s) loaded" in summary
    assert "1 failed" in summary
    assert "1 not published by the source" in summary
    assert "1 requested, not started" in summary


def test_an_empty_request_says_so_rather_than_reporting_zero_of_zero():
    assert TimelineCoverage().summary() == "No forecast hours were requested."
    assert TimelineCoverage().explain() == ()
    assert TimelineCoverage().is_complete() is True


def test_the_explanation_lists_only_what_is_not_loaded():
    coverage = (
        TimelineCoverage.requested_range((0, 3, 6))
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(3, "failed", reason="HTTP 500")
    )

    lines = coverage.explain()

    assert lines == (
        "F003: failed \u2014 HTTP 500",
        "F006: requested, not started",
    )


# --------------------------------------------------------------------------- #
# Portability
# --------------------------------------------------------------------------- #


class _Collection:
    def __init__(self, dates=(), hours=None, meta=None):
        self._dates = list(dates)
        self._meta = dict(meta or {})
        if hours is not None:
            self._meta["timeline_hours"] = list(hours)

    def getMeta(self, key):  # noqa: N802 - upstream API
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802 - upstream API
        self._meta[key] = value


def test_the_ledger_round_trips_through_collection_metadata():
    coverage = (
        TimelineCoverage.requested_range((0, 3, 6), run_time=RUN)
        .with_state(0, "loaded", valid_time=_valid(0))
        .with_state(3, "failed", reason="HTTP 500")
    )
    collection = _Collection()

    coverage.attach(collection)
    restored = TimelineCoverage.from_collection(collection)

    assert restored.requested_hours == (0, 3, 6)
    assert restored.loaded_hours == (0,)
    assert restored.frame_for(3).reason == "HTTP 500"
    assert restored.frame_for(0).valid_time == _valid(0)
    assert restored.run_time == RUN


def test_a_collection_without_a_ledger_reports_its_loaded_hours_truthfully():
    """No claim is invented about hours nobody recorded as requested."""
    collection = _Collection(
        dates=(_valid(0), _valid(3)), hours=(0, 3), meta={"run": RUN}
    )

    coverage = TimelineCoverage.from_collection(collection)

    assert coverage.requested_hours == (0, 3)
    assert coverage.loaded_hours == (0, 3)
    assert coverage.missing_hours == ()
    assert coverage.is_complete() is True
    assert coverage.run_time == RUN


def test_a_collection_with_no_forecast_hours_falls_back_to_position():
    collection = _Collection(dates=(_valid(0), _valid(1)))

    coverage = TimelineCoverage.from_collection(collection)

    assert coverage.requested_hours == (0, 1)
    assert coverage.loaded_hours == (0, 1)


def test_a_malformed_saved_ledger_degrades_instead_of_raising():
    collection = _Collection(
        dates=(_valid(0),),
        hours=(0,),
        meta={
            "timeline_frames": [
                {"forecast_hour": "not a number", "state": "loaded"},
                {"forecast_hour": 3, "state": "loaded"},
                "not even a mapping",
                {"forecast_hour": 6, "state": "teleported"},
            ]
        },
    )

    coverage = TimelineCoverage.from_collection(collection)

    # The unparseable entries are dropped; a "loaded" with no time cannot be
    # proven loaded, so it is unknown rather than a claim.
    assert coverage.requested_hours == (3, 6)
    assert coverage.state_of(3) == "unknown"
    assert coverage.state_of(6) == "unknown"


def test_an_empty_saved_ledger_falls_back_to_the_loaded_hours():
    collection = _Collection(
        dates=(_valid(0),), hours=(0,), meta={"timeline_frames": []}
    )

    coverage = TimelineCoverage.from_collection(collection)

    assert coverage.loaded_hours == (0,)


def test_attach_works_on_a_collection_without_setmeta():
    class _Bare:
        def __init__(self):
            self._meta = {}

    collection = _Bare()
    TimelineCoverage.requested_range((0,)).attach(collection)

    assert collection._meta["timeline_frames"][0]["forecast_hour"] == 0
