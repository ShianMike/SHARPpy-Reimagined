"""Fast contract tests for the shared profile metrics layer."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import box_analysis
from sharpmod.profile_metrics import (
    DEFAULT_METRIC_KEYS,
    ComparisonSample,
    MetricSample,
    ProfileMetricsEngine,
    export_comparison_csv,
    export_timeline_csv,
)


def test_default_summary_metrics_stay_on_fast_tier():
    """Opening a comparison or full ensemble must not trigger composites."""
    calls = {"fast": 0, "composite": 0}

    def fast(profile):
        calls["fast"] += 1
        return {key: profile.value for key in DEFAULT_METRIC_KEYS}

    def composite(_profile):
        calls["composite"] += 1
        return {"stp_cin": 99.0}

    values = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    ).values(_Profile(2.0), DEFAULT_METRIC_KEYS)

    assert values["stp_cin"] == 2.0
    assert calls == {"fast": 1, "composite": 0}


RUN = datetime(2026, 9, 10, 0)


class _Profile:
    def __init__(self, value, temperature=20.0):
        self.value = float(value)
        self.pres = np.ma.asarray([1000.0, 900.0, 800.0])
        self.hght = np.ma.asarray([100.0, 1000.0, 2000.0])
        self.tmpc = np.ma.asarray([temperature, temperature - 10.0, temperature - 20.0])
        self.dwpc = np.ma.asarray(
            [temperature - 5.0, temperature - 15.0, temperature - 25.0]
        )
        self.wdir = np.ma.asarray([180.0, 190.0, 200.0])
        self.wspd = np.ma.asarray([10.0, 20.0, 30.0])


class _Collection:
    def __init__(
        self,
        profiles,
        dates,
        *,
        highlight="control",
        current=0,
        model="HRRR",
        loc="TEST",
    ):
        self._profs = profiles
        self._dates = list(dates)
        self._highlight = highlight
        self._prof_idx = current
        self._meta = {"model": model, "loc": loc, "run": RUN}


@pytest.fixture
def analyzers():
    calls = {"fast": 0, "composite": 0}

    def fast(profile):
        calls["fast"] += 1
        return {
            "mlcape": profile.value,
            "mlcin": -profile.value,
            "shear_6km": profile.value / 10.0,
            "srh_1km": profile.value * 2.0,
            "stp_cin": profile.value / 1000.0,
        }

    def composite(profile):
        calls["composite"] += 1
        return {
            "stp_cin": profile.value / 1000.0,
            "ship": profile.value / 500.0,
        }

    return calls, fast, composite


def test_values_are_tier_lazy_cached_and_read_only(analyzers):
    calls, fast, composite = analyzers
    profile = _Profile(1500.0)
    engine = ProfileMetricsEngine(
        max_entries=4,
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    assert engine.values(profile, ("mlcape",))["mlcape"] == 1500.0
    assert engine.values(profile, ("shear_6km",))["shear_6km"] == 150.0
    assert calls == {"fast": 1, "composite": 0}

    values = engine.values(profile, ("mlcape", "stp_cin"))
    assert values == {"mlcape": 1500.0, "stp_cin": 1.5}
    assert calls == {"fast": 2, "composite": 0}
    assert engine.values(profile, ("ship",))["ship"] == 3.0
    assert calls == {"fast": 2, "composite": 1}
    with pytest.raises(TypeError):
        values["mlcape"] = 0.0


def test_cache_detects_in_place_column_edits(analyzers):
    calls, _fast, composite = analyzers

    def temperature_fast(profile):
        calls["fast"] += 1
        return {"mlcape": float(profile.tmpc[0])}

    profile = _Profile(1.0)
    engine = ProfileMetricsEngine(
        fast_analyzer=temperature_fast,
        composite_analyzer=composite,
    )
    assert engine.values(profile, ("mlcape",))["mlcape"] == 20.0
    profile.tmpc[0] = 25.0
    assert engine.values(profile, ("mlcape",))["mlcape"] == 25.0
    assert calls["fast"] == 2


def test_cache_is_bounded_and_profile_replacement_is_not_stale(analyzers):
    calls, fast, composite = analyzers
    engine = ProfileMetricsEngine(
        max_entries=2,
        fast_analyzer=fast,
        composite_analyzer=composite,
    )
    first = _Profile(100.0)
    for value in (first, _Profile(200.0), _Profile(300.0)):
        engine.values(value, ("mlcape",))
    assert engine.cache_size == 2

    replacement = _Profile(900.0)
    assert engine.values(replacement, ("mlcape",))["mlcape"] == 900.0
    assert calls["fast"] == 4


def test_timeline_keeps_missing_points_without_mutating_selection(analyzers):
    _calls, fast, composite = analyzers
    dates = [RUN + timedelta(hours=hour) for hour in (0, 3, 6)]
    collection = _Collection(
        {
            "control": [_Profile(1.0), _Profile(2.0), _Profile(3.0)],
            "member-1": [_Profile(10.0), _Profile(20.0)],
        },
        dates,
        highlight="control",
        current=2,
    )
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    samples = engine.timeline(collection, ("mlcape",), member="member-1")

    assert [sample.valid_time for sample in samples] == dates
    assert [sample.values["mlcape"] for sample in samples] == [10.0, 20.0, None]
    assert [sample.available for sample in samples] == [True, True, False]
    assert collection._prof_idx == 2
    assert collection._highlight == "control"


def test_compare_requires_exact_valid_time_and_exposes_mismatch(analyzers):
    _calls, fast, composite = analyzers
    valid = RUN + timedelta(hours=3)
    first = _Collection(
        {"control": [_Profile(1.0), _Profile(2.0)]},
        [RUN, valid],
        current=1,
        model="HRRR",
    )
    second = _Collection(
        {"control": [_Profile(3.0)]},
        [valid],
        current=0,
        model="RAP",
    )
    mismatched = _Collection(
        {"control": [_Profile(4.0)]},
        [RUN + timedelta(hours=6)],
        current=0,
        model="NAM",
    )
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    rows = engine.compare((first, second, mismatched), ("mlcape",))

    assert rows[0].requested_time == valid
    assert [row.values["mlcape"] for row in rows] == [2.0, 3.0, None]
    assert [row.aligned for row in rows] == [True, True, False]
    assert rows[2].valid_time == RUN + timedelta(hours=6)
    assert "unavailable" in rows[2].reason
    assert rows[1].label.startswith("RAP · TEST")
    assert first._prof_idx == 1
    assert second._prof_idx == 0
    assert mismatched._prof_idx == 0


def test_ensemble_reports_scalar_and_thermodynamic_percentile_bands(analyzers):
    _calls, fast, composite = analyzers
    collection = _Collection(
        {
            "control": [_Profile(1000.0, 20.0)],
            "member-1": [_Profile(2000.0, 22.0)],
            "member-2": [_Profile(3000.0, 24.0)],
            "missing": [],
        },
        [RUN],
    )
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    summary = engine.ensemble(
        collection,
        ("mlcape", "stp_cin"),
        pressure_levels=(1000.0, 900.0, 850.0, 700.0),
    )

    assert summary.available
    assert summary.member_count == 4
    assert summary.available_member_count == 3
    assert summary.members == ("control", "member-1", "member-2")
    assert summary.missing_members == ("missing",)
    assert summary.scalar["mlcape"].count == 3
    assert summary.scalar["mlcape"].median == 2000.0
    assert summary.scalar["mlcape"].p10 == pytest.approx(1200.0)
    assert summary.scalar["stp_cin"].p90 == pytest.approx(2.8)

    level_900 = summary.bands[1]
    assert level_900.pressure_hpa == 900.0
    assert level_900.temperature.count == 3
    assert level_900.temperature.median == 12.0
    assert level_900.dewpoint.median == 7.0
    assert summary.bands[-1].temperature.count == 0
    assert summary.bands[-1].temperature.median is None


def test_default_ensemble_batches_fast_and_summary_once_in_member_order(monkeypatch):
    collection = _Collection(
        {
            "control": [_Profile(1000.0, 20.0)],
            "member-1": [_Profile(2000.0, 22.0)],
            "member-2": [_Profile(3000.0, 24.0)],
            "missing": [],
        },
        [RUN],
    )
    calls = []

    def batch(profiles, *, include_effective_stp=False):
        profiles = tuple(profiles)
        calls.append(
            (
                tuple(profile.value for profile in profiles),
                include_effective_stp,
            )
        )
        return tuple(
            {
                "mlcape": profile.value,
                "stp_cin": profile.value / 1000.0,
            }
            for profile in profiles
        )

    monkeypatch.setattr(box_analysis, "fast_values_many", batch)
    engine = ProfileMetricsEngine()

    first = engine.ensemble(
        collection,
        ("mlcape", "stp_cin"),
        pressure_levels=(1000.0, 900.0),
    )
    second = engine.ensemble(
        collection,
        ("mlcape", "stp_cin"),
        pressure_levels=(1000.0, 900.0),
    )

    assert calls == [((1000.0, 2000.0, 3000.0), True)]
    assert first.members == second.members == ("control", "member-1", "member-2")
    assert first.missing_members == ("missing",)
    assert first.scalar["mlcape"].median == 2000.0
    assert first.scalar["stp_cin"].median == 2.0


def test_ensemble_batch_failure_falls_back_with_per_member_isolation(monkeypatch):
    profiles = (_Profile(1.0), _Profile(2.0), _Profile(3.0))
    collection = _Collection(
        {f"member-{index}": [profile] for index, profile in enumerate(profiles)},
        [RUN],
    )
    calls = []

    monkeypatch.setattr(
        box_analysis,
        "fast_values_many",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("batch unavailable")
        ),
    )

    def serial(profile):
        calls.append(profile.value)
        if profile.value == 2.0:
            raise RuntimeError("member failed")
        return {"mlcape": profile.value}

    monkeypatch.setattr(box_analysis, "fast_values", serial)
    summary = ProfileMetricsEngine().ensemble(
        collection,
        ("mlcape",),
        pressure_levels=(1000.0,),
    )

    assert calls == [1.0, 2.0, 3.0]
    assert summary.members == ("member-0", "member-2")
    assert summary.missing_members == ("member-1",)
    assert summary.scalar["mlcape"].count == 2
    assert summary.scalar["mlcape"].median == 2.0


def test_ensemble_keeps_explicit_custom_analyzer_off_batch_path(monkeypatch):
    collection = _Collection(
        {"control": [_Profile(1.0)], "member-1": [_Profile(2.0)]},
        [RUN],
    )
    calls = []

    def custom(profile):
        calls.append(profile.value)
        return {"mlcape": profile.value * 10.0}

    monkeypatch.setattr(
        box_analysis,
        "fast_values_many",
        lambda *_args, **_kwargs: pytest.fail("custom analyzers must not batch"),
    )
    summary = ProfileMetricsEngine(fast_analyzer=custom).ensemble(
        collection,
        ("mlcape",),
        pressure_levels=(1000.0,),
    )

    assert calls == [1.0, 2.0]
    assert summary.scalar["mlcape"].median == 15.0


def test_ensemble_unavailable_time_returns_explicit_empty_summary(analyzers):
    _calls, fast, composite = analyzers
    collection = _Collection({"control": [_Profile(1.0)]}, [RUN])
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    summary = engine.ensemble(
        collection,
        ("mlcape",),
        valid_time=RUN + timedelta(hours=1),
    )

    assert not summary.available
    assert summary.available_member_count == 0
    assert summary.scalar["mlcape"].count == 0
    assert "unavailable" in summary.reason


def test_csv_exports_keep_gaps_and_alignment_status(tmp_path):
    timeline = (
        MetricSample(RUN, "control", {"mlcape": 1000.0}, True),
        MetricSample(
            RUN + timedelta(hours=3),
            "control",
            {"mlcape": None},
            False,
            "missing",
        ),
    )
    timeline_path = export_timeline_csv(tmp_path / "timeline.csv", timeline)
    with timeline_path.open(encoding="utf-8", newline="") as stream:
        timeline_rows = list(csv.DictReader(stream))
    assert timeline_rows[0]["mlcape"] == "1000.0"
    assert timeline_rows[1]["mlcape"] == ""
    assert timeline_rows[1]["reason"] == "missing"

    comparison = (
        ComparisonSample(
            0,
            "HRRR",
            RUN,
            RUN,
            "control",
            {"mlcape": 1000.0},
            True,
            True,
        ),
        ComparisonSample(
            1,
            "NAM",
            RUN,
            RUN + timedelta(hours=3),
            None,
            {"mlcape": None},
            False,
            False,
            "unavailable",
        ),
    )
    comparison_path = export_comparison_csv(tmp_path / "comparison.csv", comparison)
    with comparison_path.open(encoding="utf-8", newline="") as stream:
        comparison_rows = list(csv.DictReader(stream))
    assert comparison_rows[0]["aligned"] == "True"
    assert comparison_rows[1]["aligned"] == "False"
    assert comparison_rows[1]["valid_time"].endswith("03:00:00")


def test_72_point_fast_timeline_stays_within_feedback_budget(analyzers):
    calls, fast, composite = analyzers
    dates = [RUN + timedelta(hours=hour) for hour in range(72)]
    profiles = [_Profile(float(hour)) for hour in range(72)]
    collection = _Collection({"control": profiles}, dates)
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )

    started = perf_counter()
    first = engine.timeline(collection, ("mlcape",))
    second = engine.timeline(collection, ("mlcape",))
    elapsed = perf_counter() - started

    assert len(first) == len(second) == 72
    assert calls == {"fast": 72, "composite": 0}
    # Stub analysis is intentionally near-zero cost; this generous ceiling
    # catches accidental quadratic work or sleeps without being a microbenchmark.
    assert elapsed < 1.0


def test_unknown_metric_is_rejected_before_analyzers_run(analyzers):
    calls, fast, composite = analyzers
    engine = ProfileMetricsEngine(
        fast_analyzer=fast,
        composite_analyzer=composite,
    )
    with pytest.raises(Exception, match="unknown box parameter"):
        engine.values(SimpleNamespace(), ("not-a-metric",))
    assert calls == {"fast": 0, "composite": 0}


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "soundings"
        / "hrrr_point_36.68N_95.66W_f018.npz"
    ).exists(),
    reason="no portable NPZ sample",
)
def test_optimized_summary_stp_matches_full_convective_profile():
    from sharpmod.io.decoder import load_npz

    sample = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "soundings"
        / "hrrr_point_36.68N_95.66W_f018.npz"
    )
    collection, _station_id = load_npz(sample)
    raw = next(iter(collection._profs.values()))[0]

    optimized = box_analysis.summary_values(raw)["stp_cin"]
    full = collection.getHighlightedProf().stp_cin

    assert optimized == pytest.approx(float(full), rel=1.0e-9, abs=1.0e-9)
