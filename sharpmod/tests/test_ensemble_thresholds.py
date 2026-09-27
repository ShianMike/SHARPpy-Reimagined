"""Deterministic contracts for the ensemble ingredient threshold explorer."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
from sharpmod.ensemble_thresholds import (
    DISCLAIMER,
    MEMBER_FAILED,
    MEMBER_NONQUALIFYING,
    MEMBER_NOT_EVALUATED,
    MEMBER_NOT_LOADED,
    MEMBER_NO_PROFILE,
    MEMBER_QUALIFYING,
    MEMBER_UNUSABLE,
    ThresholdCondition,
    ThresholdDefinition,
    ThresholdError,
    evaluate_threshold,
    evaluate_threshold_timeline,
    export_threshold_csv,
    fraction_text,
    read_threshold_definition,
    write_threshold_results,
)


UTC = timezone.utc
T0 = datetime(2026, 5, 20, 18, tzinfo=UTC)
T1 = datetime(2026, 5, 20, 19, tzinfo=UTC)


class _Collection:
    def __init__(self):
        self._dates = [T0, T1]
        self._profs = {
            "c00": [
                SimpleNamespace(values={"mlcape": 1800, "shear": 42}),
                SimpleNamespace(values={"mlcape": 2100, "shear": 45}),
            ],
            "p01": [
                SimpleNamespace(values={"mlcape": 900, "shear": 48}),
                SimpleNamespace(values={"mlcape": 1500, "shear": 30}),
            ],
            "p02": [
                SimpleNamespace(values={"mlcape": None, "shear": 50}),
                SimpleNamespace(values={"mlcape": 2400, "shear": 55}),
            ],
        }
        self._meta = {}

    def getCurrentDate(self):
        return self._dates[0]

    def getMeta(self, key):
        return self._meta[key]

    def setMeta(self, key, value):
        self._meta[key] = value


class _Engine:
    def values(self, profile, keys):
        return {key: profile.values.get(key) for key in keys}


@pytest.fixture
def collection():
    value = _Collection()
    EnsembleAcquisition(
        requested_members=("c00", "p01", "p02", "p03"),
        loaded_members=("c00", "p01", "p02"),
        failures=(MemberFailure("p03", "failed", "404"),),
    ).attach(value)
    return value


def _definition():
    return ThresholdDefinition(
        "CAPE and shear",
        (
            ThresholdCondition("mlcape", ">=", 1000, units="J kg-1"),
            ThresholdCondition("shear", ">=", 40, units="kt"),
        ),
    )


def test_joint_threshold_uses_only_the_common_valid_subset(collection):
    result = evaluate_threshold(collection, _definition(), _Engine())

    assert result.requested_member_count == 4
    assert result.loaded_member_count == 3
    assert result.usable_member_count == 2
    assert result.qualifying_member_count == 1
    assert result.agreement_fraction == pytest.approx(0.5)
    assert result.requested_coverage == pytest.approx(0.5)
    assert result.qualifying_members == ("c00",)
    assert result.nonqualifying_members == ("p01",)
    assert result.unusable_members == ("p02", "p03")


def test_threshold_uses_ordered_batch_metrics_when_engine_supports_them(collection):
    class BatchEngine:
        def __init__(self):
            self.batches = []
            self.serial_calls = 0

        def values_many(self, profiles, keys):
            profiles = tuple(profiles)
            self.batches.append(tuple(profile.values for profile in profiles))
            return tuple(
                {key: profile.values.get(key) for key in keys} for profile in profiles
            )

        def values(self, profile, keys):
            self.serial_calls += 1
            return {key: profile.values.get(key) for key in keys}

    engine = BatchEngine()
    result = evaluate_threshold(collection, _definition(), engine)

    assert len(engine.batches) == 1
    assert [values["mlcape"] for values in engine.batches[0]] == [1800, 900, None]
    assert engine.serial_calls == 0
    assert result.qualifying_members == ("c00",)
    assert result.unusable_members == ("p02", "p03")


def test_threshold_batch_failure_keeps_per_member_failure_isolation(collection):
    class FallbackEngine(_Engine):
        def values_many(self, _profiles, _keys):
            raise RuntimeError("batch unavailable")

        def values(self, profile, keys):
            if profile.values["mlcape"] == 900:
                raise RuntimeError("one bad member")
            return super().values(profile, keys)

    result = evaluate_threshold(collection, _definition(), FallbackEngine())

    by_member = {member.member: member for member in result.members}
    assert by_member["c00"].satisfied is True
    assert by_member["p01"].satisfied is None
    assert "one bad member" in by_member["p01"].reason
    assert by_member["p02"].satisfied is None


def test_missing_diagnostic_is_unknown_not_a_failed_threshold(collection):
    result = evaluate_threshold(collection, _definition(), _Engine())
    p02 = next(item for item in result.members if item.member == "p02")
    p03 = next(item for item in result.members if item.member == "p03")

    assert p02.loaded and not p02.usable and p02.satisfied is None
    assert not p03.loaded and not p03.usable and p03.satisfied is None


def test_single_between_condition_is_supported(collection):
    definition = ThresholdDefinition(
        "Moderate CAPE", (ThresholdCondition("mlcape", "between", 800, 1900),)
    )
    result = evaluate_threshold(collection, definition, _Engine())
    assert result.qualifying_members == ("c00", "p01")


@pytest.mark.parametrize(
    "condition",
    [
        lambda: ThresholdCondition("", ">=", 1),
        lambda: ThresholdCondition("mlcape", "~", 1),
        lambda: ThresholdCondition("mlcape", "between", 2, 1),
    ],
)
def test_invalid_conditions_are_explained(condition):
    with pytest.raises(ThresholdError):
        condition()


class _CountingEngine(_Engine):
    """Count evaluated members so a cancel can be timed against real work."""

    def __init__(self):
        self.evaluated = 0

    def values(self, profile, keys):
        self.evaluated += 1
        return super().values(profile, keys)


def test_cancellation_preserves_completed_timeline_results(collection):
    """A completed valid time survives a cancel that arrives after it.

    Timed against evaluated members rather than against the number of times the
    cancel predicate is polled: three members are loaded, so the first valid time
    is finished once three have been evaluated.
    """
    engine = _CountingEngine()
    results = evaluate_threshold_timeline(
        collection, _definition(), engine, cancel=lambda: engine.evaluated >= 3
    )
    assert [result.valid_time for result in results] == [T0]
    # The retained time is whole, not a fragment: nothing was cancelled inside it.
    assert results[0].cancelled is False
    assert results[0].not_evaluated_member_count == 0
    assert results[0].resolved_member_count == 4
    assert results[0].reconciles


def test_cancellation_inside_one_time_keeps_the_members_already_evaluated(collection):
    """A cancel part way through a valid time no longer discards that time.

    Cancellation was only checked between valid times, so a single evaluation --
    thirty members of composite diagnostics in a real ensemble -- could not be
    interrupted at all, and ``ThresholdResult.cancelled`` was never once set.
    """
    engine = _CountingEngine()
    result = evaluate_threshold(
        collection, _definition(), engine, cancel=lambda: engine.evaluated >= 1
    )
    assert result.cancelled is True
    assert result.not_evaluated_member_count == 2
    assert result.resolved_member_count == 2
    assert result.members[0].state == MEMBER_QUALIFYING
    # A member needing a diagnostic run is withheld; a member whose status is
    # already certain and free to determine is still reported precisely.
    assert [item.state for item in result.members[1:]] == [
        MEMBER_NOT_EVALUATED,
        MEMBER_NOT_EVALUATED,
        MEMBER_NOT_LOADED,
    ]
    assert result.members[1].loaded is True
    assert result.members[3].loaded is False
    assert result.members[1].usable is False
    assert result.members[1].satisfied is None
    assert "cancelled" in result.members[1].reason
    # It must not be counted as usable, and the funnel must still add up.
    assert result.usable_member_count == 1
    assert result.qualifying_member_count == 1
    assert result.reconciles


def test_resume_retries_only_failed_and_cancelled_members(collection):
    """A retry must not recalculate successful or deterministic member outcomes."""

    class FirstPass(_Engine):
        def __init__(self):
            self.calls = 0

        def values(self, profile, keys):
            self.calls += 1
            if profile.values["mlcape"] == 900:
                raise RuntimeError("transient diagnostic failure")
            return super().values(profile, keys)

    first_engine = FirstPass()
    first = evaluate_threshold(
        collection,
        _definition(),
        first_engine,
        cancel=lambda: first_engine.calls >= 2,
    )
    assert [item.state for item in first.members] == [
        MEMBER_QUALIFYING,
        MEMBER_FAILED,
        MEMBER_NOT_EVALUATED,
        MEMBER_NOT_LOADED,
    ]

    retried_values = []

    class RetryEngine(_Engine):
        def values(self, profile, keys):
            retried_values.append(profile.values["mlcape"])
            return super().values(profile, keys)

    progress = []
    repaired = evaluate_threshold(
        collection,
        _definition(),
        RetryEngine(),
        resume=first,
        progress=lambda done, total: progress.append((done, total)),
    )

    assert retried_values == [900, None]
    assert [item.state for item in repaired.members] == [
        MEMBER_QUALIFYING,
        MEMBER_NONQUALIFYING,
        MEMBER_UNUSABLE,
        MEMBER_NOT_LOADED,
    ]
    assert repaired.cancelled is False
    assert repaired.evaluation_retryable_members == ()
    assert progress[-1] == (4, 4)


def test_timeline_resume_reuses_complete_times_and_only_runs_missing_work(collection):
    first_engine = _CountingEngine()
    first = evaluate_threshold_timeline(
        collection,
        _definition(),
        first_engine,
        cancel=lambda: first_engine.evaluated >= 3,
    )
    assert [result.valid_time for result in first] == [T0]

    retry_engine = _CountingEngine()
    progress = []
    repaired = evaluate_threshold_timeline(
        collection,
        _definition(),
        retry_engine,
        resume=first,
        progress=lambda done, total: progress.append((done, total)),
    )

    assert [result.valid_time for result in repaired] == [T0, T1]
    assert retry_engine.evaluated == 3
    assert progress[-1] == (8, 8)


def test_counts_partition_the_requested_ensemble(collection):
    """Every requested member lands in exactly one terminal group."""
    result = evaluate_threshold(collection, _definition(), _Engine())

    assert result.qualifying_member_count == 1
    assert result.nonqualifying_member_count == 1
    assert result.loaded_unusable_member_count == 1
    assert result.not_loaded_member_count == 1
    assert result.reconciles
    assert dict(result.state_counts) == {
        MEMBER_QUALIFYING: 1,
        MEMBER_NONQUALIFYING: 1,
        MEMBER_UNUSABLE: 1,
        MEMBER_NO_PROFILE: 0,
        MEMBER_FAILED: 0,
        MEMBER_NOT_LOADED: 1,
        MEMBER_NOT_EVALUATED: 0,
    }
    # "Not loaded" and "loaded but unusable" are different problems with
    # different remedies; the umbrella tuple still reports both together.
    assert [item.member for item in result.members_in(MEMBER_NOT_LOADED)] == ["p03"]
    assert [item.member for item in result.members_in(MEMBER_UNUSABLE)] == ["p02"]
    assert result.unusable_members == ("p02", "p03")
    assert result.retryable_members == ("p03",)


def test_every_reported_ratio_shows_its_denominator(collection):
    """A bare percentage cannot be reconciled against anything."""
    result = evaluate_threshold(collection, _definition(), _Engine())

    assert result.agreement_text == "1/2 (50%)"
    assert result.coverage_text == "2/4 (50%)"
    assert fraction_text(3, 0) == "\u2014"
    assert fraction_text(None, 4) == "\u2014"
    assert fraction_text(1, 3, decimals=1) == "1/3 (33.3%)"


def test_a_definition_states_the_rule_it_actually_tests(collection):
    """A saved definition was only ever labelled by the name someone typed."""
    definition = _definition()
    assert definition.conditions[0].summary == "MLCAPE \u2265 1000 J/kg"
    # An unregistered metric still names itself and keeps its stored units.
    assert definition.conditions[1].summary == "SHEAR \u2265 40.0 kt"
    assert definition.summary == (
        "MLCAPE \u2265 1000 J/kg and SHEAR \u2265 40.0 kt"
    )
    between = ThresholdDefinition(
        "Banded",
        (ThresholdCondition("stp_cin", "between", 1.0, 3.0),),
    )
    assert between.summary == "STP (CIN) between 1.00 and 3.00"


def test_an_unusable_member_names_the_diagnostic_that_was_missing(collection):
    """One message for three ingredients did not say which was absent."""
    result = evaluate_threshold(collection, _definition(), _Engine())
    unusable = result.members_in(MEMBER_UNUSABLE)
    assert [item.member for item in unusable] == ["p02"]
    assert unusable[0].reason == "no value for mlcape"
    assert unusable[0].state_label == "loaded, a required diagnostic is unavailable"
    assert unusable[0].short_state_label == "Diagnostic unavailable"
    assert unusable[0].excluded is True
    assert result.members[0].excluded is False


def test_definition_results_and_csv_round_trip(collection, tmp_path):
    result = evaluate_threshold(collection, _definition(), _Engine())
    json_path = write_threshold_results(
        tmp_path / "threshold.json", _definition(), (result,)
    )
    csv_path = export_threshold_csv(tmp_path / "threshold.csv", (result,))

    assert read_threshold_definition(json_path) == _definition()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["results"][0]["usable_member_count"] == 2
    text = csv_path.read_text(encoding="utf-8")
    assert "p03,False,False," in text
    assert DISCLAIMER in text
