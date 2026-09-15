"""Scientific and persistence tests for forecast sounding verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.verification import (
    VerificationRules,
    aggregate_verification,
    export_verification_csv,
    identity_for_collection,
    read_verification_json,
    verify_pair,
    write_verification_json,
)


UTC = timezone.utc
VALID = datetime(2026, 5, 20, 18, tzinfo=UTC)
RUN = datetime(2026, 5, 20, 12, tzinfo=UTC)


class _Collection:
    def __init__(self, profile, **metadata):
        self._dates = [VALID]
        self._prof_idx = 0
        self._highlight = ""
        self._profs = {"": [profile]}
        self._meta = metadata

    def getMeta(self, key):
        return self._meta[key]


class _Engine:
    def values(self, profile, keys):
        return {key: profile.metrics.get(key) for key in keys}


def _profile(*, error=0.0, missing_middle_wind=False):
    u = np.array([10.0, 20.0, 30.0]) + error
    if missing_middle_wind:
        u[1] = np.nan
    return SimpleNamespace(
        pres=np.array([1000.0, 900.0, 800.0]),
        hght=np.array([100.0, 600.0, 1100.0]),
        tmpc=np.array([20.0, 16.0, 12.0]) + error,
        dwpc=np.array([15.0, 11.0, 7.0]) + error,
        u=u,
        v=np.array([5.0, 10.0, 15.0]) + error,
        metrics={"mlcape": 1000.0 + 100.0 * error, "shear_6km": 35.0 + error},
        latitude=35.22,
    )


def _forecast(*, error=2.0, **metadata):
    values = {
        "model": "hrrr",
        "source_provider": "NOAA NOMADS",
        "run": RUN,
        "fxx": 6,
        "selected_lat": 35.22,
        "selected_lon": -97.44,
        "terrain_elevation_m": 100.0,
        **metadata,
    }
    return _Collection(_profile(error=error), **values)


def _observation(**metadata):
    values = {
        "model": "Observed",
        "observed": True,
        "source_provider": "uwyo",
        "source_station": "KOUN",
        "selected_lat": 35.22,
        "selected_lon": -97.44,
        "terrain_elevation_m": 100.0,
        **metadata,
    }
    return _Collection(_profile(), **values)


def test_pair_matches_exact_time_and_calculates_forecast_minus_observed():
    pair = verify_pair(
        _forecast(),
        _observation(),
        _Engine(),
        rules=VerificationRules(station_id="koun"),
    )

    assert pair.matched
    assert pair.forecast.lead_hours == 6
    assert pair.distance_km == pytest.approx(0.0)
    assert len(pair.levels) == 3
    assert [level.temperature_c for level in pair.levels] == pytest.approx(
        [2.0, 2.0, 2.0]
    )
    errors = {item.key: item.error for item in pair.diagnostics}
    assert errors == pytest.approx({"mlcape": 200.0, "shear_6km": 2.0})


def test_vertical_gaps_and_missing_levels_remain_missing():
    forecast = _forecast()
    forecast._profs[""][0].hght = np.array([100.0, 2500.0])
    forecast._profs[""][0].tmpc = np.array([22.0, 4.0])
    forecast._profs[""][0].dwpc = np.array([17.0, -1.0])
    forecast._profs[""][0].u = np.array([12.0, 42.0])
    forecast._profs[""][0].v = np.array([7.0, 20.0])

    pair = verify_pair(
        forecast,
        _observation(),
        _Engine(),
        rules=VerificationRules(max_vertical_gap_m=750.0),
    )

    assert pair.matched
    assert pair.levels[0].temperature_c == pytest.approx(2.0)
    assert pair.levels[1].temperature_c is None
    assert pair.levels[2].u_wind_kt is None


def test_valid_time_mismatch_and_analysis_are_not_silently_used():
    observation = _observation()
    observation._dates[0] = VALID + timedelta(hours=1)
    analysis = _forecast(analysis=True)

    mismatch = verify_pair(_forecast(), observation, _Engine())
    wrong_kind = verify_pair(analysis, _observation(), _Engine())

    assert not mismatch.matched
    assert any("valid times" in item for item in mismatch.rejection_reasons)
    assert not wrong_kind.matched
    assert "classified as analysis" in wrong_kind.rejection_reasons[0]


def test_as_of_rule_blocks_later_run_or_post_event_availability():
    rules = VerificationRules(as_of=datetime(2026, 5, 20, 11, tzinfo=UTC))
    pair = verify_pair(
        _forecast(available_time=RUN), _observation(), _Engine(), rules=rules
    )
    assert not pair.matched
    assert any("as-of" in item for item in pair.rejection_reasons)

    later_run = _forecast(run=VALID + timedelta(hours=1))
    pair = verify_pair(later_run, _observation(), _Engine())
    assert not pair.matched
    assert any("later than its valid" in item for item in pair.rejection_reasons)


def test_zero_metre_terrain_is_preserved_not_replaced_by_profile_surface():
    identity = identity_for_collection(_forecast(terrain_elevation_m=0.0))
    assert identity.terrain_elevation_m == 0.0


def test_aggregate_excludes_rejected_pairs_and_discloses_counts():
    good = verify_pair(_forecast(error=2.0), _observation(), _Engine())
    second = verify_pair(_forecast(error=-1.0), _observation(), _Engine())
    rejected_observation = _observation()
    rejected_observation._dates[0] += timedelta(hours=1)
    rejected = verify_pair(_forecast(), rejected_observation, _Engine())

    aggregate = aggregate_verification((good, second, rejected))[0]
    assert aggregate.matched_pair_count == 2
    assert aggregate.rejected_pair_count == 1
    assert aggregate.vertical["temperature_c"].count == 6
    assert aggregate.vertical["temperature_c"].bias == pytest.approx(0.5)
    assert aggregate.vertical["temperature_c"].mae == pytest.approx(1.5)
    assert aggregate.vertical["temperature_c"].rmse == pytest.approx(np.sqrt(2.5))
    assert "distance" in aggregate.matching_rule_summary


def test_pair_can_be_saved_reopened_and_exported(tmp_path):
    pair = verify_pair(_forecast(), _observation(), _Engine())
    json_path = write_verification_json(tmp_path / "verification.json", (pair,))
    csv_path = export_verification_csv(tmp_path / "verification.csv", (pair,))

    restored = read_verification_json(json_path)
    assert restored == (pair,)
    text = csv_path.read_text(encoding="utf-8")
    assert "forecast_minus_observed" in text
    assert "temperature_c,2.0" in text
