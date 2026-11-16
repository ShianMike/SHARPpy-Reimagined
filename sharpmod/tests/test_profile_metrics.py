"""Fast contract tests for the shared profile metrics layer."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import box_analysis
from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
from sharpmod.profile_metrics import (
    DEFAULT_METRIC_KEYS,
    ProfileMetricsEngine,
    export_ensemble_csv,
)


def test_default_summary_metrics_stay_on_fast_tier():
    """Default ensemble diagnostics must not trigger full composites."""
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


def test_collection_snapshot_freezes_selection_containers_and_metadata():
    from sharpmod.profile_metrics import freeze_collection

    class OpaqueProvider:
        def __deepcopy__(self, _memo):
            raise TypeError("provider handle cannot be copied")

    profile = _Profile(1.0)
    collection = _Collection(
        {"control": [profile], "other": [_Profile(2.0)]}, [RUN], highlight="other",
    )
    collection._meta["nested"] = {"members": ["control", "other"]}
    collection._meta["provider_handle"] = OpaqueProvider()
    collection._mod_therm = [True]
    frozen = freeze_collection(collection)
    collection._mod_therm[0] = False
    collection._highlight = "control"
    collection._dates.append(RUN + timedelta(hours=1))
    collection._profs["control"][0] = _Profile(999.0)
    collection._meta["nested"]["members"].append("late")
    assert frozen._dates == (RUN,)
    assert frozen._profs["control"] == (profile,)
    assert frozen._highlight == "other"
    assert frozen.getCurrentDate() == RUN
    assert frozen.getMeta("nested")["members"] == ["control", "other"]
    assert frozen._mod_therm == (True,)
    with pytest.raises(TypeError):
        frozen._profs["late"] = ()


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


def test_partial_ensemble_discloses_requested_loaded_and_metric_denominators(
    tmp_path,
):
    requested = tuple(["c00", *(f"p{index:02d}" for index in range(1, 31))])
    loaded = requested[:24]
    collection = _Collection(
        {
            member: [_Profile(float(index + 1))]
            for index, member in enumerate(loaded)
        },
        [RUN],
        highlight="c00",
    )
    ledger = EnsembleAcquisition(
        requested,
        loaded,
        tuple(
            MemberFailure(member, "failed", "fixture unavailable")
            for member in requested[24:]
        ),
    )
    ledger.attach(collection)

    def partial(profile):
        index = int(profile.value)
        return {
            "mlcape": profile.value,
            "srh_1km": None if index > 22 else profile.value,
        }

    summary = ProfileMetricsEngine(fast_analyzer=partial).ensemble(
        collection,
        ("mlcape", "srh_1km"),
        pressure_levels=(1000.0,),
    )

    assert summary.member_count == 31
    assert summary.requested_member_count == 31
    assert summary.loaded_member_count == 24
    assert summary.available_member_count == 24
    assert summary.scalar["mlcape"].count == 24
    assert summary.scalar["srh_1km"].count == 22
    assert len(summary.failed_members) == 7

    exported = export_ensemble_csv(tmp_path / "ensemble.csv", summary, ledger)
    with exported.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    by_metric = {
        row["metric"]: row
        for row in rows
        if row["record_type"] == "diagnostic"
    }
    by_member = {
        row["member"]: row
        for row in rows
        if row["record_type"] == "member"
    }
    assert by_metric["mlcape"]["diagnostic_usable_count"] == "24"
    assert by_metric["srh_1km"]["diagnostic_usable_count"] == "22"
    assert by_member["p24"]["status"] == "failed"
    assert by_member["p24"]["reason"] == "fixture unavailable"
    assert not list(tmp_path.glob("*.tmp"))


def test_one_loaded_member_retains_value_without_claiming_distribution():
    collection = _Collection({"c00": [_Profile(1000.0)]}, [RUN], highlight="c00")
    EnsembleAcquisition(
        ("c00", "p01"),
        ("c00",),
        (MemberFailure("p01", "failed"),),
    ).attach(collection)
    summary = ProfileMetricsEngine(
        fast_analyzer=lambda profile: {"mlcape": profile.value}
    ).ensemble(collection, ("mlcape",), pressure_levels=(1000.0,))

    assert summary.available
    assert not summary.distribution_available
    assert summary.scalar["mlcape"].count == 1
    assert summary.scalar["mlcape"].median == 1000.0
    assert "at least two" in summary.reason


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


def test_values_many_reuses_the_native_batch_and_profile_cache(monkeypatch):
    profiles = (_Profile(1.0), _Profile(2.0), _Profile(3.0))
    calls = []

    def batch(items, *, include_effective_stp=False):
        items = tuple(items)
        calls.append((tuple(profile.value for profile in items), include_effective_stp))
        return tuple({"mlcape": profile.value} for profile in items)

    monkeypatch.setattr(box_analysis, "fast_values_many", batch)
    engine = ProfileMetricsEngine()

    first = engine.values_many(profiles, ("mlcape",))
    second = engine.values_many(profiles, ("mlcape",))

    assert calls == [((1.0, 2.0, 3.0), False)]
    assert [row["mlcape"] for row in first] == [1.0, 2.0, 3.0]
    assert second == first


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


def test_ensemble_resume_reuses_exact_member_diagnostics_and_columns(tmp_path):
    profiles = (_Profile(1, 20), _Profile(2, 22), _Profile(4, 24))
    collection = _Collection({"c00": [profiles[0]], "p01": [profiles[1]],
                              "p02": [], "p03": [profiles[2]]}, [RUN])
    EnsembleAcquisition(("c00", "p01", "p02", "p03", "p04"),
                        ("c00", "p01", "p02", "p03"),
                        (MemberFailure("p04", "failed", "download failed"),)).attach(collection)
    calls, progress = [], []
    stopped, fail_second = False, True

    def analyze(profile):
        nonlocal stopped, fail_second
        calls.append(profile.value)
        if len(calls) == 1:
            stopped = True
        if profile.value == 2 and fail_second:
            fail_second = False
            raise RuntimeError("transient member diagnostic")
        return {"mlcape": profile.value}

    engine = ProfileMetricsEngine(fast_analyzer=analyze)
    first = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000, 900),
                            cancel=lambda: stopped, progress=lambda done, total: progress.append((done, total)))
    assert [row.outcome for row in first.member_samples] == [
        "completed", "cancelled", "unavailable", "cancelled", "unavailable",
    ]
    assert first.members == ("c00",) and not first.distribution_available
    assert first.failed_members == ("p04",), "acquisition failure is not a diagnostic-job failure"
    assert progress[-1] == (3, 5)
    with pytest.raises(TypeError):
        first.member_samples[2].values["mlcape"] = 0
    cancelled_export = export_ensemble_csv(tmp_path / "cancelled.csv", first,
                                           EnsembleAcquisition.from_collection(collection))
    with cancelled_export.open(encoding="utf-8", newline="") as stream:
        cancelled = {row["member"]: row for row in csv.DictReader(stream) if row["record_type"] == "member"}
    assert cancelled["p01"]["status"] == "diagnostic-cancelled"
    second = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000, 900), resume=first)
    assert calls == [1, 2, 4]
    assert [row.outcome for row in second.member_samples] == [
        "completed", "failed", "unavailable", "completed", "unavailable",
    ]
    assert second.member_samples[0] is first.member_samples[0]
    assert second.member_samples[2] is first.member_samples[2]
    third = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000, 900), resume=second)
    assert calls == [1, 2, 4, 2]
    assert third.scalar["mlcape"].median == 2
    assert third.bands[0].temperature.median == 22
    assert third.requested_member_count == 5 and third.loaded_member_count == 4
    exported = export_ensemble_csv(tmp_path / "partial.csv", second,
                                   EnsembleAcquisition.from_collection(collection))
    with exported.open(encoding="utf-8", newline="") as stream:
        rows = {row["member"]: row for row in csv.DictReader(stream) if row["record_type"] == "member"}
    assert rows["p01"]["status"] == "diagnostic-failed"
    assert "transient member diagnostic" in rows["p01"]["reason"]
    assert rows["p04"]["status"] == "failed" and rows["p04"]["reason"] == "download failed"
    for kwargs in ({"keys": ("srh_1km",)}, {"pressure_levels": (850,)}, {"valid_time": RUN + timedelta(hours=1)}):
        saved = {"keys": ("mlcape",), "pressure_levels": (1000, 900), **kwargs}
        with pytest.raises(ValueError, match="saved request"):
            engine.ensemble(collection, resume=third, **saved)
    profiles[0].tmpc[0] += 1
    with pytest.raises(ValueError, match="saved request"):
        engine.ensemble(collection, ("mlcape",), pressure_levels=(1000, 900), resume=third)


def test_ensemble_cancel_preserves_a_completed_native_batch(monkeypatch):
    collection = _Collection({"c00": [_Profile(1)], "p01": [_Profile(2)],
                              "p02": [_Profile(3)], "missing": []}, [RUN])
    engine = ProfileMetricsEngine()
    stopped, calls = False, []

    def batch(profiles, keys):
        nonlocal stopped
        calls.append(tuple(profile.value for profile in profiles))
        stopped = True
        return tuple({"mlcape": profile.value} for profile in profiles)

    monkeypatch.setattr(engine, "_batch_values", batch)
    first = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000,), cancel=lambda: stopped)
    assert [row.outcome for row in first.member_samples] == ["completed"] * 3 + ["unavailable"]
    assert first.scalar["mlcape"].count == 3
    second = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000,), resume=first)
    assert calls == [(1, 2, 3)], "explicit retry must not rerun completed batch diagnostics"
    assert second.member_samples == first.member_samples


def test_ensemble_native_resume_batches_only_unresolved_members(monkeypatch):
    collection = _Collection({"c00": [_Profile(1)], "p01": [_Profile(2)], "p02": [_Profile(3)]}, [RUN])

    def analyze(profile):
        if profile.value == 2:
            raise RuntimeError("transient member failure")
        return {"mlcape": profile.value}

    engine = ProfileMetricsEngine(fast_analyzer=analyze)
    first = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000,))
    calls = []

    def batch(profiles, keys):
        calls.append(tuple(profile.value for profile in profiles))
        return tuple({"mlcape": profile.value} for profile in profiles)

    monkeypatch.setattr(engine, "_can_batch", lambda _keys: True)
    monkeypatch.setattr(engine, "_batch_values", batch)
    second = engine.ensemble(collection, ("mlcape",), pressure_levels=(1000,), resume=first)
    assert calls == [(2,)]
    assert second.member_samples[0] is first.member_samples[0]
    assert second.member_samples[2] is first.member_samples[2]
    assert second.scalar["mlcape"].median == 2


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
