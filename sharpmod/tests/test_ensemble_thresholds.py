"""Deterministic contracts for the ensemble ingredient threshold explorer."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
from sharpmod.ensemble_thresholds import (
    DISCLAIMER,
    ThresholdCondition,
    ThresholdDefinition,
    ThresholdError,
    evaluate_threshold,
    evaluate_threshold_timeline,
    export_threshold_csv,
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


def test_cancellation_preserves_completed_timeline_results(collection):
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    results = evaluate_threshold_timeline(
        collection, _definition(), _Engine(), cancel=cancelled
    )
    assert [result.valid_time for result in results] == [T0]


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
