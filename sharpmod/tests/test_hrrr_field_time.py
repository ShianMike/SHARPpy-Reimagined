"""Which HRRR run and forecast hour a map field resolves to.

The overlay has to answer two different questions. The Forecast Model tab knows
a *cycle*, and the field must be that cycle so the map shows the grid the
sounding is cut from. The observed tab knows only a *moment*, and the field must
be the freshest run reaching it. Confusing the two puts a different forecast on
the map from the one in the sounding window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sharpmod import hrrr_field

UTC = timezone.utc

#: 14:30Z, so the newest run judged published is 13Z (a 50 minute lag).
NOW = datetime(2026, 9, 4, 14, 30, tzinfo=UTC)
REFERENCE = datetime(2026, 9, 4, 13, tzinfo=UTC)


def _valid(run, fxx):
    return run + timedelta(hours=fxx)


def test_the_newest_published_run_is_the_reference():
    assert hrrr_field.latest_run(NOW) == REFERENCE


def test_no_selection_resolves_to_the_newest_analysis():
    assert hrrr_field.resolve_request(None, now=NOW) == (REFERENCE, 0)


@pytest.mark.parametrize(
    "hours_ahead",
    (0, 1, 6, 12, 18),
)
def test_a_reachable_hour_comes_from_the_newest_run(hours_ahead):
    """Freshest guidance wins while the newest run can still reach the hour."""
    wanted = REFERENCE + timedelta(hours=hours_ahead)

    run, fxx = hrrr_field.resolve_request(wanted, now=NOW)

    assert run == REFERENCE
    assert fxx == hours_ahead
    assert _valid(run, fxx) == wanted


@pytest.mark.parametrize("hours_back", (1, 6, 24, 72, 24 * 14))
def test_a_past_hour_resolves_to_its_own_analysis(hours_back):
    """A RAOB from last week must not be paired with today's run at F000.

    Searching forward from the newest run cannot reach backwards, so this used
    to fall through to the clamp and return the newest run at F000 -- a field
    valid days away from the sounding beside it.
    """
    wanted = REFERENCE - timedelta(hours=hours_back)

    run, fxx = hrrr_field.resolve_request(wanted, now=NOW)

    assert fxx == 0
    assert run == wanted
    assert _valid(run, fxx) == wanted


def test_a_long_lead_steps_back_to_an_extended_cycle():
    """Only 00/06/12/18Z reach beyond F18, so a long lead must use one.

    The 13Z reference run stops at F18. Clamping there would answer a different
    question than the one asked; the 12Z run reaches F48 and does contain the
    hour.
    """
    wanted = REFERENCE + timedelta(hours=25)

    run, fxx = hrrr_field.resolve_request(wanted, now=NOW)

    assert run.hour in hrrr_field.EXTENDED_CYCLES
    assert run == datetime(2026, 9, 4, 12, tzinfo=UTC)
    assert fxx == 26
    assert _valid(run, fxx) == wanted


def test_an_unreachable_hour_clamps_to_the_published_limit():
    wanted = REFERENCE + timedelta(hours=200)

    run, fxx = hrrr_field.resolve_request(wanted, now=NOW)

    assert run == REFERENCE
    assert fxx == hrrr_field.max_forecast_hour(REFERENCE.hour)
    assert _valid(run, fxx) < wanted


def test_every_resolved_hour_is_one_hrrr_publishes():
    """No resolution may name a forecast hour past its cycle's own limit."""
    for offset in range(-48, 72):
        wanted = REFERENCE + timedelta(hours=offset)
        run, fxx = hrrr_field.resolve_request(wanted, now=NOW)
        assert 0 <= fxx <= hrrr_field.max_forecast_hour(run.hour), offset


# --------------------------------------------------------------------------- #
# Pinning an exact cycle
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cycle", "fxx"),
    ((0, 48), (6, 12), (12, 18), (12, 48), (13, 18), (23, 0)),
)
def test_a_pinned_cycle_is_returned_unchanged(cycle, fxx):
    run = datetime(2026, 9, 4, cycle, tzinfo=UTC)

    assert hrrr_field.pin_request(run, fxx) == (run, fxx)


def test_pinning_beats_matching_the_valid_time():
    """The point of pinning: same hour, different forecast.

    A 12Z F18 sounding is valid at the same moment as the 13Z run's F17, but
    they are two different forecasts. The map must show the one the sounding
    came from.
    """
    selected_run = datetime(2026, 9, 4, 12, tzinfo=UTC)
    selected_fxx = 18
    valid = _valid(selected_run, selected_fxx)

    pinned = hrrr_field.pin_request(selected_run, selected_fxx)
    derived = hrrr_field.resolve_request(valid, now=NOW)

    assert pinned == (selected_run, selected_fxx)
    assert derived != pinned, "sanity: the two answers really do differ"
    assert _valid(*derived) == valid, "both are valid at the requested hour"


def test_a_forecast_hour_past_the_cycle_limit_is_clamped():
    """A 13Z run stops at F18; asking for F30 must not name an absent file."""
    run = datetime(2026, 9, 4, 13, tzinfo=UTC)

    assert hrrr_field.pin_request(run, 30) == (run, 18)


def test_a_naive_run_is_read_as_utc():
    naive = datetime(2026, 9, 4, 6)

    run, fxx = hrrr_field.pin_request(naive, 12)

    assert run == datetime(2026, 9, 4, 6, tzinfo=UTC)
    assert fxx == 12


def test_a_run_is_truncated_to_its_hour():
    run, fxx = hrrr_field.pin_request(
        datetime(2026, 9, 4, 6, 47, 31, 500, tzinfo=UTC), 3)

    assert run == datetime(2026, 9, 4, 6, tzinfo=UTC)
    assert fxx == 3


def test_a_non_utc_run_is_converted_not_relabelled():
    eastern = timezone(timedelta(hours=-5))
    run, _fxx = hrrr_field.pin_request(
        datetime(2026, 9, 4, 7, tzinfo=eastern), 0)

    assert run == datetime(2026, 9, 4, 12, tzinfo=UTC)


@pytest.mark.parametrize("fxx", (None, 0, -5))
def test_a_missing_or_negative_hour_becomes_the_analysis(fxx):
    run = datetime(2026, 9, 4, 12, tzinfo=UTC)

    assert hrrr_field.pin_request(run, fxx) == (run, 0)


# --------------------------------------------------------------------------- #
# fetch_field routing
# --------------------------------------------------------------------------- #
def _capture_request(monkeypatch):
    """Stop fetch_field at the download boundary and report the run it chose."""
    seen = {}

    def _stop(_product, run, fxx, **_kwargs):
        seen["run"] = run
        seen["fxx"] = fxx
        raise hrrr_field.HrrrFieldUnavailable("stopped before download")

    monkeypatch.setattr(hrrr_field, "_download_fields", _stop)
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)
    hrrr_field.clear_cache()
    return seen


def test_fetch_field_pins_an_explicit_run(monkeypatch):
    seen = _capture_request(monkeypatch)

    with pytest.raises(hrrr_field.HrrrFieldError):
        hrrr_field.fetch_field(
            "refc", run=datetime(2026, 9, 4, 12, tzinfo=UTC), fxx=18, now=NOW)

    assert seen == {"run": datetime(2026, 9, 4, 12, tzinfo=UTC), "fxx": 18}


def test_fetch_field_follows_a_valid_time_when_no_run_is_pinned(monkeypatch):
    seen = _capture_request(monkeypatch)

    with pytest.raises(hrrr_field.HrrrFieldError):
        hrrr_field.fetch_field(
            "refc", valid_time=REFERENCE + timedelta(hours=6), now=NOW)

    assert seen == {"run": REFERENCE, "fxx": 6}


def test_a_pinned_run_overrides_a_conflicting_valid_time(monkeypatch):
    """The cycle is the stronger statement: it names a specific forecast."""
    seen = _capture_request(monkeypatch)

    with pytest.raises(hrrr_field.HrrrFieldError):
        hrrr_field.fetch_field(
            "refc",
            valid_time=REFERENCE + timedelta(hours=1),
            run=datetime(2026, 9, 4, 6, tzinfo=UTC),
            fxx=12,
            now=NOW,
        )

    assert seen == {"run": datetime(2026, 9, 4, 6, tzinfo=UTC), "fxx": 12}
