"""Offline TOI calibration pipeline: manifests, fitting, blocking, and metrics.

Every dataset here is a declared synthetic fixture. These tests prove the
pipeline is correct and honest; they deliberately cannot prove that TOI is
calibrated, because that requires a real multi-year archive.
"""

from __future__ import annotations

import builtins
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from sharpmod.guidance import (
    TOI_PROBABILITY_VERSION,
    TOI_SCORECARD_VERSION,
    GuidanceState,
    TOIFeatures,
    compute_experimental_toi,
)
from sharpmod.guidance.hrrr import build_live_hrrr_guidance
from sharpmod.guidance.toi_calibration import (
    TOI_CALIBRATION_FEATURE_SCHEMA,
    TOI_TARGET_DEFINITIONS,
    TOICalibrationArtifact,
    TOICalibrationError,
    fit_logistic_calibrator,
    toi_feature_vector,
)
from sharpmod.guidance.toi_dataset import (
    TOI_FORBIDDEN_ANCHOR_SOURCES,
    TOICase,
    TOICaseRow,
    TOIDataset,
    TOIDatasetError,
    TOILabelManifest,
    build_toi_dataset,
    high_risk_worthy_proxy_v1,
)
from sharpmod.guidance.toi_evaluation import (
    TOIEvaluationError,
    average_precision,
    bootstrap_brier_difference,
    bootstrap_interval,
    brier_score,
    brier_skill_score,
    calibration_intercept_slope,
    contingency_metrics,
    expanding_year_folds,
    leave_one_year_out_folds,
    reliability_bins,
    roc_auc,
)
from sharpmod.guidance.toi_strata import (
    STRATUM_DIMENSIONS,
    TOIStratumError,
    conus_region,
    forecast_lead_bin,
    hrrr_era,
    season_name,
)
from sharpmod.guidance.toi_training import (
    cross_validate,
    evaluate_dataset,
    stratified_reports,
    train_toi_calibrator,
)
from sharpmod.guidance.toi_validation import (
    RECOMMENDED_DEVELOPMENT_YEARS,
    RECOMMENDED_TEST_YEARS,
    TOIPromotionCriteria,
    TOIProspectiveRecord,
    TOIValidationError,
    TOIValidationPlan,
    evaluate_promotion,
)
from sharpmod.tests._toi_fixtures import (
    DEFAULT_FORECAST_HOUR,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    synthetic_fetcher,
    synthetic_manifest_payload,
    year_spanning_manifest_payload,
)


#: The non-scientific gate used to drive the pipeline in tests.  It keeps these
#: runs fast and, by design, can never promote an artifact.
SMOKE = TOIPromotionCriteria.pipeline_smoke()


def _manifest(**overrides) -> TOILabelManifest:
    return TOILabelManifest.from_mapping(synthetic_manifest_payload(**overrides))


@pytest.fixture(scope="module")
def synthetic_dataset() -> TOIDataset:
    return build_toi_dataset(
        _manifest(), fetcher=synthetic_fetcher, weighting="population"
    )


# --------------------------------------------------------------------------
# Label manifests and leakage guards
# --------------------------------------------------------------------------


def test_official_riv_is_not_an_available_target_definition():
    assert "riv" not in TOI_TARGET_DEFINITIONS
    assert set(TOI_TARGET_DEFINITIONS) == {
        "manifest_label_v1",
        "high_risk_worthy_proxy_v1",
    }
    payload = synthetic_manifest_payload()
    payload["target_definition"] = "riv"

    with pytest.raises(TOIDatasetError, match="cannot be manufactured"):
        TOILabelManifest.from_mapping(payload)


def test_manifest_requires_outbreak_severe_and_null_cases():
    payload = synthetic_manifest_payload()
    payload["cases"] = [
        case for case in payload["cases"] if case["case_class"] == "outbreak"
    ]

    with pytest.raises(TOIDatasetError, match="missing: severe, null"):
        TOILabelManifest.from_mapping(payload)


def test_manifest_label_target_requires_an_explicit_label_for_every_case():
    payload = synthetic_manifest_payload(target_definition="manifest_label_v1")
    payload["cases"][0].pop("label")

    with pytest.raises(TOIDatasetError, match="requires an explicit label"):
        TOILabelManifest.from_mapping(payload)


@pytest.mark.parametrize("anchor", sorted(TOI_FORBIDDEN_ANCHOR_SOURCES))
def test_observed_tornado_anchors_are_rejected_as_leakage(anchor):
    with pytest.raises(TOIDatasetError, match="leak verifying observations"):
        TOICase(
            event_id="leaky",
            case_class="outbreak",
            run_time=datetime(2021, 3, 25, 6, tzinfo=timezone.utc),
            forecast_hour=6,
            latitude=35.0,
            longitude=-97.0,
            anchor_source=anchor,
        )


def test_high_risk_worthy_proxy_is_named_and_rule_based():
    violent = {
        "tornado_count": 30,
        "ef2_plus_count": 8,
        "ef3_plus_count": 3,
        "ef4_plus_count": 1,
        "longest_ef2_plus_path_miles": 55.0,
    }
    ordinary = {
        "tornado_count": 6,
        "ef2_plus_count": 1,
        "ef3_plus_count": 0,
        "ef4_plus_count": 0,
        "longest_ef2_plus_path_miles": 8.0,
    }
    long_track_only = {
        "tornado_count": 3,
        "ef2_plus_count": 1,
        "ef3_plus_count": 0,
        "ef4_plus_count": 0,
        "longest_ef2_plus_path_miles": 41.0,
    }

    assert high_risk_worthy_proxy_v1(violent) == 1
    assert high_risk_worthy_proxy_v1(ordinary) == 0
    assert high_risk_worthy_proxy_v1(long_track_only) == 1
    assert "not the official SPC Risk Impact Value" in (
        TOI_TARGET_DEFINITIONS["high_risk_worthy_proxy_v1"]
    )

    with pytest.raises(TOIDatasetError, match="nested"):
        high_risk_worthy_proxy_v1({"tornado_count": 1, "ef2_plus_count": 5})


def test_manifest_label_disagreeing_with_the_named_proxy_is_an_error():
    payload = synthetic_manifest_payload()
    payload["cases"][0]["label"] = 0

    with pytest.raises(TOIDatasetError, match="resolve the disagreement"):
        TOILabelManifest.from_mapping(payload).label_for(
            TOILabelManifest.from_mapping(payload).cases[0]
        )


# --------------------------------------------------------------------------
# Dataset extraction reuses the operational feature code
# --------------------------------------------------------------------------


def test_dataset_rows_reuse_operational_features_and_temporal_sampling(
    synthetic_dataset,
):
    manifest = _manifest()
    case = manifest.cases[0]
    operational = build_live_hrrr_guidance(
        case.run_time,
        case.forecast_hour,
        case.latitude,
        case.longitude,
        fetcher=synthetic_fetcher,
    )
    row = next(
        item for item in synthetic_dataset.rows if item["event_id"] == case.event_id
    )

    assert operational.toi.state is GuidanceState.EXPERIMENTAL
    features = operational.toi.features
    assert row["translation_speed_kt"] == pytest.approx(features.translation_speed_kt)
    assert row["maximum_jet_speed_kt"] == pytest.approx(features.maximum_jet_speed_kt)
    assert row["jet_to_risk_distance_km"] == pytest.approx(
        features.jet_to_risk_distance_km
    )
    assert row["jet_to_risk_bearing_deg"] == pytest.approx(
        features.jet_to_risk_bearing_deg
    )
    assert row["maximum_stp"] == pytest.approx(features.maximum_stp)
    assert row["experimental_score"] == pytest.approx(operational.toi.score)
    # Same three-hourly sampling as the live producer, recorded per row.
    assert row["sampling_interval_hours"] == "3"
    assert row["frame_count"] == "7"
    assert row["time_coverage_hours"] == "18"
    assert row["sampling_status"] == "complete"
    assert row["requested_forecast_hours"] == "0,3,6,9,12,15,18"


def test_dataset_row_schema_carries_every_required_calibration_field(
    synthetic_dataset,
):
    row = synthetic_dataset.rows[0]

    for name in (
        "issuance_time",
        "event_id",
        "year",
        "event_year",
        "translation_speed_kt",
        "maximum_jet_speed_kt",
        "jet_to_risk_distance_km",
        "jet_to_risk_bearing_deg",
        "maximum_stp",
        "month",
        "pressure_level_hpa",
        "experimental_score",
        "label",
        "sample_weight",
        "model_version",
        "provider_version",
        "risk_region_source",
    ):
        assert row[name] is not None, name
    assert row["year"] == datetime.fromisoformat(row["issuance_time"]).year
    assert row["event_year"] == row["year"]
    assert row["label_source"].startswith("synthetic fixture")
    # The scorecard and the probability transform are versioned separately.
    assert row["scorecard_version"] == TOI_SCORECARD_VERSION
    assert row["public_anchor_probability_version"] == TOI_PROBABILITY_VERSION
    assert row["scorecard_version"] != row["public_anchor_probability_version"]
    # The risk region is the forecast-time objective STP proxy region, never a
    # region drawn from later observed tornado locations.
    assert "STP proxy region" in row["risk_region_source"]


def test_dataset_forecast_hour_and_anchor_are_issuance_time_information(
    synthetic_dataset,
):
    for row in synthetic_dataset.rows:
        assert row["forecast_hour"] == DEFAULT_FORECAST_HOUR
        assert row["latitude"] == pytest.approx(DEFAULT_LATITUDE)
        assert row["longitude"] == pytest.approx(DEFAULT_LONGITUDE)
        assert row["anchor_source"] == "fixed_domain_grid"


def test_population_weighting_restores_the_documented_base_rate(synthetic_dataset):
    labels = np.asarray([row.label for row in synthetic_dataset.rows], dtype=float)
    weights = np.asarray(
        [row.sample_weight for row in synthetic_dataset.rows], dtype=float
    )

    assert synthetic_dataset.weighting == "population"
    assert float(np.sum(weights * labels) / np.sum(weights)) == pytest.approx(0.02)
    # The raw sampled frequency is much higher; that is exactly why the weights
    # exist rather than being an accident of case selection.
    assert float(labels.mean()) > 0.2


def test_natural_weighting_preserves_the_sampled_event_frequency():
    dataset = build_toi_dataset(
        _manifest(), fetcher=synthetic_fetcher, weighting="natural"
    )

    assert dataset.weighting == "natural"
    assert all(row.sample_weight == pytest.approx(1.0) for row in dataset.rows)


def test_population_weighting_requires_a_documented_population_base_rate():
    manifest = _manifest(population_base_rate=None)

    with pytest.raises(TOIDatasetError, match="population_base_rate"):
        build_toi_dataset(manifest, fetcher=synthetic_fetcher, weighting="population")


def test_dataset_round_trip_detects_tampered_rows(tmp_path, synthetic_dataset):
    path = tmp_path / "dataset.json"
    synthetic_dataset.save_json(path)
    reloaded = TOIDataset.load(path)

    assert reloaded.data_hash() == synthetic_dataset.data_hash()
    assert reloaded.years == synthetic_dataset.years
    assert reloaded.dataset_kind == "synthetic-fixture"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rows"][0]["label"] = 1 - int(payload["rows"][0]["label"])
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TOIDatasetError, match="modified after extraction"):
        TOIDataset.load(path)


def test_dataset_csv_export_has_one_row_per_case(tmp_path, synthetic_dataset):
    path = tmp_path / "dataset.csv"
    synthetic_dataset.save_csv(path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == len(synthetic_dataset.rows) + 1
    assert lines[0].startswith(
        "event_id,case_class,issuance_time,year,event_year"
    )


def test_unusable_cases_are_recorded_instead_of_silently_dropped():
    def broken(_run_time, _hour, *_args, **_kwargs):
        raise RuntimeError("archive object missing")

    with pytest.raises(TOIDatasetError, match="archive object missing"):
        build_toi_dataset(_manifest(), fetcher=broken)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_brier_and_skill_match_hand_computed_values():
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert brier_skill_score([0.5, 0.5], [1, 0]) == pytest.approx(0.0)
    assert brier_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0)
    assert brier_skill_score([1.0, 0.0], [1, 0]) == pytest.approx(1.0)

    with pytest.raises(TOIEvaluationError, match="one outcome"):
        brier_skill_score([0.5, 0.5], [1, 1])


def test_contingency_metrics_match_the_published_definitions():
    metrics = contingency_metrics([0.9, 0.8, 0.2, 0.1], [1, 0, 1, 0], threshold=0.5)

    assert metrics["hits"] == pytest.approx(1.0)
    assert metrics["misses"] == pytest.approx(1.0)
    assert metrics["false_alarms"] == pytest.approx(1.0)
    assert metrics["correct_negatives"] == pytest.approx(1.0)
    assert metrics["pod"] == pytest.approx(0.5)
    assert metrics["far"] == pytest.approx(0.5)
    assert metrics["csi"] == pytest.approx(1.0 / 3.0)
    assert metrics["frequency_bias"] == pytest.approx(1.0)


def test_discrimination_metrics_match_hand_computed_values():
    probabilities = [0.9, 0.8, 0.2, 0.1]
    labels = [1, 0, 1, 0]

    assert roc_auc(probabilities, labels) == pytest.approx(0.75)
    assert average_precision(probabilities, labels) == pytest.approx(
        0.8333333, abs=1e-6
    )


def test_average_precision_of_a_constant_forecast_is_the_base_rate():
    # A forecast that cannot discriminate must score exactly the event
    # frequency, not an artefact of how the rows happen to be ordered.
    assert average_precision([0.5] * 4, [1, 0, 1, 0]) == pytest.approx(0.5)
    assert average_precision([0.5] * 4, [1, 0, 0, 0]) == pytest.approx(0.25)
    assert average_precision(
        [0.3] * 4, [1, 0, 1, 0], [3.0, 1.0, 1.0, 1.0]
    ) == pytest.approx(4.0 / 6.0)


def test_average_precision_is_permutation_invariant_under_ties():
    probabilities = [0.6, 0.6, 0.6, 0.6, 0.2, 0.2]
    labels = [1, 0, 0, 1, 1, 0]
    baseline = average_precision(probabilities, labels)
    generator = np.random.default_rng(19)

    for _draw in range(25):
        order = generator.permutation(len(labels))
        shuffled = average_precision(
            [probabilities[index] for index in order],
            [labels[index] for index in order],
        )
        assert shuffled == pytest.approx(baseline)


def test_average_precision_treats_a_tied_group_as_one_threshold():
    # Every case shares 0.6, so the first group's precision is 2/4 and recall
    # 2/3; the 0.2 group then completes recall at precision 3/6.
    value = average_precision([0.6, 0.6, 0.6, 0.6, 0.2, 0.2], [1, 0, 0, 1, 1, 0])

    expected = (2.0 / 3.0) * (2.0 / 4.0) + (1.0 / 3.0) * (3.0 / 6.0)
    assert value == pytest.approx(expected)


def test_roc_area_is_also_permutation_invariant_under_ties():
    probabilities = [0.4, 0.4, 0.4, 0.9, 0.1]
    labels = [1, 0, 1, 0, 0]
    baseline = roc_auc(probabilities, labels)
    generator = np.random.default_rng(23)

    for _draw in range(15):
        order = generator.permutation(len(labels))
        assert roc_auc(
            [probabilities[index] for index in order],
            [labels[index] for index in order],
        ) == pytest.approx(baseline)


def test_undefined_contingency_scores_are_none_not_nan():
    # No case reaches the threshold, so FAR has no denominator.
    metrics = contingency_metrics([0.1, 0.2], [1, 0], threshold=0.9)

    assert metrics["far"] is None
    assert metrics["csi"] == pytest.approx(0.0)
    assert metrics["pod"] == pytest.approx(0.0)
    assert metrics["frequency_bias"] == pytest.approx(0.0)
    assert not any(
        isinstance(value, float) and np.isnan(value) for value in metrics.values()
    )
    # No observed event, so POD and frequency bias have no denominator.
    no_events = contingency_metrics([0.9, 0.8], [0, 0], threshold=0.5)
    assert no_events["pod"] is None
    assert no_events["frequency_bias"] is None
    assert no_events["far"] == pytest.approx(1.0)


def test_reliability_bins_use_spc_style_edges_and_weighted_counts():
    bins = reliability_bins(
        [0.02, 0.04, 0.31, 0.33], [0, 0, 1, 0], edges=(0.0, 0.05, 0.30, 1.0)
    )

    assert bins[0].count == pytest.approx(2.0)
    assert bins[0].observed_frequency == pytest.approx(0.0)
    # An empty bin carries None, not NaN, so reports stay strict JSON.
    assert bins[1].count == pytest.approx(0.0)
    assert bins[1].mean_forecast is None
    assert bins[1].observed_frequency is None
    assert bins[1].to_mapping()["mean_forecast"] is None
    assert bins[2].count == pytest.approx(2.0)
    assert bins[2].observed_frequency == pytest.approx(0.5)
    assert bins[2].mean_forecast == pytest.approx(0.32)


def test_calibration_intercept_and_slope_detect_a_well_calibrated_forecast():
    probabilities = [0.25] * 8 + [0.75] * 8
    labels = [1, 1, 0, 0, 0, 0, 0, 0] + [1, 1, 1, 1, 1, 1, 0, 0]

    intercept, slope = calibration_intercept_slope(probabilities, labels)

    assert intercept == pytest.approx(0.0, abs=0.05)
    assert slope == pytest.approx(1.0, abs=0.05)


def test_calibration_slope_flags_an_overconfident_forecast():
    probabilities = [0.02] * 10 + [0.98] * 10
    labels = [0] * 7 + [1] * 3 + [1] * 7 + [0] * 3

    _intercept, slope = calibration_intercept_slope(probabilities, labels)

    assert slope < 0.5


def test_bootstrap_interval_brackets_the_point_estimate_and_blocks_by_event():
    generator = np.random.default_rng(7)
    labels = np.repeat([0, 1], 40)
    probabilities = np.clip(labels * 0.6 + generator.normal(0.2, 0.1, 80), 0.01, 0.99)
    events = [f"event-{index // 4}" for index in range(80)]

    interval = bootstrap_interval(
        brier_score, probabilities, labels, samples=200, groups=events
    )

    assert interval["lower"] <= interval["point"] <= interval["upper"]
    assert interval["confidence"] == pytest.approx(0.95)
    assert interval["valid_resamples"] > 100


# --------------------------------------------------------------------------
# Fitting and blocked validation
# --------------------------------------------------------------------------


def test_logistic_fitter_recovers_a_known_relationship():
    generator = np.random.default_rng(11)
    features = generator.normal(0.0, 1.0, size=(4000, 2))
    logits = -0.5 + 1.5 * features[:, 0] - 0.75 * features[:, 1]
    labels = (generator.uniform(size=4000) < 1.0 / (1.0 + np.exp(-logits))).astype(
        float
    )

    intercept, coefficients, means, scales = fit_logistic_calibrator(
        features, labels, l2_penalty=1e-6
    )

    assert intercept == pytest.approx(-0.5, abs=0.15)
    assert coefficients[0] / scales[0] == pytest.approx(1.5, abs=0.2)
    assert coefficients[1] / scales[1] == pytest.approx(-0.75, abs=0.2)
    assert means[0] == pytest.approx(features[:, 0].mean(), abs=1e-9)


def test_logistic_fitter_refuses_single_outcome_and_bad_weights():
    features = np.asarray([[1.0, 2.0], [2.0, 3.0]])

    with pytest.raises(TOICalibrationError, match="both positive and negative"):
        fit_logistic_calibrator(features, [1, 1])
    with pytest.raises(TOICalibrationError, match="sample_weights"):
        fit_logistic_calibrator(features, [1, 0], sample_weights=[1.0, -1.0])
    with pytest.raises(TOICalibrationError, match="binary"):
        fit_logistic_calibrator(features, [1, 2])


def test_ridge_penalty_shrinks_coefficients_toward_zero():
    generator = np.random.default_rng(3)
    features = generator.normal(0.0, 1.0, size=(500, 2))
    labels = (features[:, 0] + generator.normal(0.0, 0.5, 500) > 0).astype(float)

    _weak_intercept, weak, _m, _s = fit_logistic_calibrator(
        features, labels, l2_penalty=1e-6
    )
    _strong_intercept, strong, _m2, _s2 = fit_logistic_calibrator(
        features, labels, l2_penalty=500.0
    )

    assert abs(strong[0]) < abs(weak[0])


@pytest.fixture(scope="module")
def year_spanning_dataset() -> TOIDataset:
    """A dataset whose outbreak event straddles 31 December / 1 January."""

    return build_toi_dataset(
        TOILabelManifest.from_mapping(year_spanning_manifest_payload()),
        fetcher=synthetic_fetcher,
    )


def test_a_year_spanning_event_gets_one_blocking_year(year_spanning_dataset):
    spanning = [
        row
        for row in year_spanning_dataset.rows
        if row.event_id == "newyear-outbreak"
    ]

    assert len(spanning) == 2
    # The two cycles genuinely fall in different calendar years ...
    assert sorted(row.year for row in spanning) == [2020, 2021]
    # ... but share one blocking year, taken from the earliest issuance.
    assert {row.event_year for row in spanning} == {2020}
    assert year_spanning_dataset.calendar_years == (2020, 2021, 2022)
    assert year_spanning_dataset.years == (2020, 2021, 2022)
    assert year_spanning_dataset.event_years["newyear-outbreak"] == 2020


def test_dataset_rejects_rows_that_disagree_about_an_event_year(
    year_spanning_dataset,
):
    rows = [dict(row.values) for row in year_spanning_dataset.rows]
    for row in rows:
        if row["event_id"] == "newyear-outbreak" and row["year"] == 2021:
            row["event_year"] = 2021

    with pytest.raises(TOIDatasetError, match="exactly one event_year"):
        TOIDataset(
            rows=tuple(TOICaseRow(row) for row in rows),
            target_definition=year_spanning_dataset.target_definition,
            label_source=year_spanning_dataset.label_source,
            manifest_digest=year_spanning_dataset.manifest_digest,
            weighting=year_spanning_dataset.weighting,
        )


def test_test_year_split_keeps_a_year_spanning_event_whole(year_spanning_dataset):
    from sharpmod.guidance.toi_training import _split_rows

    train, test = _split_rows(year_spanning_dataset, (2020,))

    # Both cycles of the spanning event are in the test period together, even
    # though one of them was issued in 2021.
    assert {row.event_id for row in test} == {"newyear-outbreak"}
    assert sorted(row.year for row in test) == [2020, 2021]
    assert "newyear-outbreak" not in {row.event_id for row in train}
    assert not (
        {row.event_id for row in train} & {row.event_id for row in test}
    )
    # Selecting 2021 must not pull in the spanning event's January cycle.
    train_2021, test_2021 = _split_rows(year_spanning_dataset, (2021,))
    assert {row.event_id for row in test_2021} == {"severe-span", "null-span"}
    assert "newyear-outbreak" in {row.event_id for row in train_2021}


def test_cross_validation_folds_never_split_a_year_spanning_event(
    year_spanning_dataset,
):
    report = cross_validate(year_spanning_dataset, scheme="leave-one-year-out")

    fold_events = {
        fold["verification_year"]: set(fold["verification_events"])
        for fold in report["folds"]
        if "verification_events" in fold
    }
    assert fold_events[2020] == {"newyear-outbreak"}
    for year, events in fold_events.items():
        others = set().union(
            *(value for key, value in fold_events.items() if key != year)
        )
        assert not (events & others), f"event repeated across folds for {year}"
    assert report["pooled_out_of_sample"]["cases"] == len(
        year_spanning_dataset.rows
    )


def test_training_artifact_years_use_event_blocking_not_calendar_years(
    year_spanning_dataset,
):
    artifact, metrics = train_toi_calibrator(
        year_spanning_dataset,
        calibration_version="year-spanning-v1",
        test_years=(2020,),
        criteria=SMOKE,
    )

    # 2021 remains a training year; the spanning event's January cycle went to
    # the test period with the rest of its event.
    assert artifact.training_years == (2021, 2022)
    assert artifact.test_years == (2020,)
    assert metrics["split"]["test_calendar_years"] == [2020, 2021]
    assert metrics["split"]["training_calendar_years"] == [2021, 2022]
    assert "never a split event" in metrics["split"]["blocking"]


def test_year_blocking_never_splits_a_single_year_across_folds():
    years = [2019, 2019, 2020, 2020, 2021]

    loyo = leave_one_year_out_folds(years)
    expanding = expanding_year_folds(years)

    assert [held for held, _train in loyo] == [2019, 2020, 2021]
    assert all(held not in train for held, train in loyo)
    assert expanding == ((2021, (2019, 2020)),)
    assert all(all(year < held for year in train) for held, train in expanding)

    with pytest.raises(TOIEvaluationError, match="at least two complete years"):
        leave_one_year_out_folds([2020, 2020])


def test_cross_validation_pools_out_of_sample_forecasts_by_year(synthetic_dataset):
    report = cross_validate(synthetic_dataset, scheme="leave-one-year-out")

    assert report["scheme"] == "leave-one-year-out"
    assert [fold["verification_year"] for fold in report["folds"]] == [
        2019,
        2020,
        2021,
        2022,
    ]
    for fold in report["folds"]:
        assert fold["verification_year"] not in fold["training_years"]
    pooled = report["pooled_out_of_sample"]
    assert pooled["cases"] == len(synthetic_dataset.rows)
    assert 0.0 <= pooled["brier_score"] <= 1.0
    assert pooled["contingency"]["threshold"] == pytest.approx(0.5)


def test_evaluation_always_compares_against_anchor_and_climatology(synthetic_dataset):
    report = evaluate_dataset(synthetic_dataset)

    assert set(report["reports"]) == {"public_anchor_transform", "climatology"}
    climatology = report["reports"]["climatology"]
    assert climatology["brier_skill_score"] == pytest.approx(0.0)
    comparison = report["comparison"]["public_anchor_transform"]
    for name in ("brier_score", "pod", "far", "csi", "frequency_bias"):
        assert name in comparison


def test_training_reports_every_required_metric_family(tmp_path, synthetic_dataset):
    artifact, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="synthetic-fixture-v1",
        test_years=(2022,),
        bootstrap_samples=60,
        criteria=SMOKE,
    )

    split = metrics["split"]
    assert split["training_years"] == [2019, 2020, 2021]
    assert split["test_years"] == [2022]
    assert "never random rows" in split["blocking"]
    held_out = metrics["held_out_test"]["reports"]
    assert set(held_out) == {
        "public_anchor_transform",
        "climatology",
        "calibrated_logistic",
    }
    calibrated = held_out["calibrated_logistic"]
    for name in (
        "brier_score",
        "brier_skill_score",
        "calibration_intercept",
        "calibration_slope",
        "reliability_bins",
        "roc_auc",
        "average_precision",
    ):
        assert name in calibrated
    for name in ("pod", "far", "csi", "frequency_bias"):
        assert name in calibrated["contingency"]
    assert "bootstrap" in calibrated
    assert "brier_score" in calibrated["bootstrap"]
    assert metrics["cross_validation"]["scheme"] == "leave-one-year-out"

    path = Path(artifact.save(tmp_path / "artifact.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["feature_schema"] == list(TOI_CALIBRATION_FEATURE_SCHEMA)
    assert payload["training_years"] == [2019, 2020, 2021]
    assert payload["test_years"] == [2022]
    assert payload["target_definition"] == "high_risk_worthy_proxy_v1"
    assert payload["data_hash"] == synthetic_dataset.data_hash()
    assert payload["experimental_not_official"] is True
    assert 0.0 < payload["base_rate"] < 1.0


def test_synthetic_fixture_can_never_claim_historical_validation(synthetic_dataset):
    artifact, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="synthetic-fixture-v1",
        test_years=(2022,),
        criteria=SMOKE,
    )

    assert artifact.validated is False
    assert artifact.dataset_kind == "synthetic-fixture"
    assert any(
        "synthetic fixture" in blocker
        for blocker in metrics["validation_blockers"]
    )
    assert "NOT eligible to replace" in artifact.notes

    with pytest.raises(TOICalibrationError, match="only a historical dataset"):
        TOICalibrationArtifact(
            calibration_version="claims-too-much",
            intercept=0.0,
            coefficients=(0.1, 0.1),
            feature_means=(3.0, 5.0),
            feature_scales=(1.0, 1.0),
            training_years=(2019, 2020),
            target_definition="manifest_label_v1",
            base_rate=0.02,
            data_hash="deadbeef",
            dataset_kind="synthetic-fixture",
            validated=True,
        )


def test_training_rejects_a_test_split_that_consumes_every_year(synthetic_dataset):
    with pytest.raises(TOIDatasetError, match="assigned to the test period"):
        train_toi_calibrator(
            synthetic_dataset,
            calibration_version="bad-split",
            test_years=synthetic_dataset.years,
            criteria=SMOKE,
        )

    with pytest.raises(TOIDatasetError, match="absent from the dataset"):
        train_toi_calibrator(
            synthetic_dataset,
            calibration_version="bad-split",
            test_years=(1999,),
            criteria=SMOKE,
        )


# --------------------------------------------------------------------------
# Strict, portable JSON
# --------------------------------------------------------------------------


def _assert_strict_json(path: Path) -> dict:
    """Assert a document parses strictly and carries no JSON5-only tokens."""

    text = path.read_text(encoding="utf-8")
    for token in ("NaN", "Infinity", "-Infinity"):
        assert token not in text, f"{path.name} contains {token}"

    def reject(constant):  # pragma: no cover - only runs on a regression
        raise AssertionError(f"{path.name} contains the non-standard {constant}")

    payload = json.loads(text, parse_constant=reject)
    # Round-tripping under allow_nan=False proves the parsed tree is strict too.
    json.dumps(payload, allow_nan=False)
    return payload


def test_strict_json_dumps_refuses_non_finite_values():
    from sharpmod.guidance.toi_evaluation import strict_json_dumps

    assert "null" in strict_json_dumps({"far": None})

    with pytest.raises(TOIEvaluationError, match="non-finite JSON"):
        strict_json_dumps({"far": float("nan")})
    with pytest.raises(TOIEvaluationError, match="non-finite JSON"):
        strict_json_dumps({"bias": float("inf")})


def test_dataset_json_is_strict_and_portable(tmp_path, synthetic_dataset):
    path = tmp_path / "dataset.json"
    synthetic_dataset.save_json(path)

    payload = _assert_strict_json(path)

    assert payload["blocking_unit"].startswith("event_year")
    assert payload["scorecard_version"] == TOI_SCORECARD_VERSION
    assert payload["public_anchor_probability_version"] == TOI_PROBABILITY_VERSION
    assert TOIDataset.load(path).data_hash() == synthetic_dataset.data_hash()


def test_artifact_and_reports_are_strict_json_with_empty_reliability_bins(
    tmp_path, synthetic_dataset
):
    artifact, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="strict-json-v1",
        test_years=(2022,),
        criteria=SMOKE,
    )
    reliability = metrics["held_out_test"]["reports"]["calibrated_logistic"][
        "reliability_bins"
    ]
    # The fixture cannot populate every SPC outlook bin, so at least one bin is
    # empty; that is exactly the case that used to emit NaN.
    empty = [item for item in reliability if item["count"] == 0]
    assert empty, "expected at least one empty reliability bin"
    assert all(item["mean_forecast"] is None for item in empty)
    assert all(item["observed_frequency"] is None for item in empty)

    artifact_path = Path(artifact.save(tmp_path / "artifact.json"))
    stored = _assert_strict_json(artifact_path)
    assert stored["calibration_version"] == "strict-json-v1"
    assert TOICalibrationArtifact.load(artifact_path) == artifact

    from sharpmod.tools.guidance_cli import _write_json

    report_path = _write_json(tmp_path / "report.json", metrics)
    _assert_strict_json(Path(report_path))


def test_artifact_save_refuses_a_non_finite_metric(tmp_path):
    artifact = _artifact(metrics={"brier_score": float("nan")})

    with pytest.raises(TOICalibrationError, match="non-finite"):
        artifact.save(tmp_path / "artifact.json")


# --------------------------------------------------------------------------
# Portable artifact and runtime selection
# --------------------------------------------------------------------------


def _artifact(**overrides) -> TOICalibrationArtifact:
    values = {
        "calibration_version": "unit-test-v1",
        "intercept": -1.0,
        "coefficients": (1.2, 0.4),
        "feature_means": (3.0, 5.0),
        "feature_scales": (1.0, 2.0),
        "training_years": (2019, 2020, 2021),
        "target_definition": "manifest_label_v1",
        "base_rate": 0.02,
        "data_hash": "abc123",
    }
    values.update(overrides)
    return TOICalibrationArtifact(**values)


def test_artifact_round_trip_preserves_every_exported_field(tmp_path):
    artifact = _artifact(metrics={"brier_score": 0.01}, notes="round trip")
    path = artifact.save(tmp_path / "nested" / "artifact.json")

    reloaded = TOICalibrationArtifact.load(path)

    assert reloaded == artifact
    assert reloaded.calibration_years == "2019-2021"
    assert reloaded.probability(4.0, 8.0) == artifact.probability(4.0, 8.0)


def test_artifact_inference_is_monotone_in_score_and_peak_stp():
    artifact = _artifact()

    assert artifact.probability(4.5, 8.0) > artifact.probability(3.5, 8.0)
    assert artifact.probability(4.0, 10.0) > artifact.probability(4.0, 2.0)
    assert 0.0 < artifact.probability(0.0, 0.0) < 1.0
    assert 0.0 < artifact.probability(5.0, 30.0) < 1.0


def test_artifact_inference_never_imports_scikit_learn(tmp_path, monkeypatch):
    path = _artifact().save(tmp_path / "artifact.json")
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.split(".")[0] == "sklearn":
            raise AssertionError("runtime inference must not require scikit-learn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    loaded = TOICalibrationArtifact.load(path)

    assert 0.0 < loaded.probability(4.35, 8.5) < 1.0


def test_artifact_rejects_unknown_targets_schemas_and_overlapping_years():
    with pytest.raises(TOICalibrationError, match="official RIV is not"):
        _artifact(target_definition="riv")
    with pytest.raises(TOICalibrationError, match="feature_schema must be"):
        _artifact(feature_schema=("toi_score",), coefficients=(1.0,),
                  feature_means=(3.0,), feature_scales=(1.0,))
    with pytest.raises(TOICalibrationError, match="must not overlap"):
        _artifact(test_years=(2020,))
    with pytest.raises(TOICalibrationError, match="base_rate"):
        _artifact(base_rate=0.0)
    with pytest.raises(TOICalibrationError, match="missing"):
        TOICalibrationArtifact.from_mapping({"calibration_version": "x"})


def test_feature_vector_reuses_the_operational_published_stp_bins():
    assert toi_feature_vector(4.35, 8.5) == (4.35, 8.5)
    assert toi_feature_vector(4.0, 5.0)[1] == 4.5
    assert toi_feature_vector(4.0, 12.0)[1] == 12.0

    with pytest.raises(TOICalibrationError, match="between 0 and 5"):
        toi_feature_vector(6.0, 5.0)
    with pytest.raises(TOICalibrationError, match="non-negative"):
        toi_feature_vector(4.0, -1.0)


def _features(**overrides) -> TOIFeatures:
    values = {
        "pressure_level_hpa": 500,
        "translation_speed_kt": 44.0,
        "maximum_jet_speed_kt": 79.5,
        "jet_to_risk_distance_km": 175.0 / 0.621371192237334,
        "jet_to_risk_bearing_deg": 290.0,
        "maximum_stp": 8.5,
        "month": 4,
    }
    values.update(overrides)
    return TOIFeatures(**values)


def test_public_anchor_transform_remains_the_default():
    result = compute_experimental_toi(_features())

    assert result.calibration_version == TOI_PROBABILITY_VERSION
    assert result.high_risk_probability == pytest.approx(0.87)


def test_an_artifact_must_be_selected_explicitly_to_take_effect():
    artifact = _artifact()

    default = compute_experimental_toi(_features())
    selected = compute_experimental_toi(_features(), calibrator=artifact)

    assert selected.calibration_version == "unit-test-v1"
    assert selected.high_risk_probability == artifact.probability(
        selected.score, 8.5
    )
    assert selected.high_risk_probability != default.high_risk_probability
    assert selected.score == default.score


def test_selected_calibration_identity_reaches_guidance_provenance():
    artifact = _artifact()
    guidance = build_live_hrrr_guidance(
        datetime(2021, 3, 25, 6, tzinfo=timezone.utc),
        DEFAULT_FORECAST_HOUR,
        DEFAULT_LATITUDE,
        DEFAULT_LONGITUDE,
        fetcher=synthetic_fetcher,
        calibrator=artifact,
    )

    assert guidance.toi.calibration_version == "unit-test-v1"
    assert guidance.provenance["toi_calibration_years"] == "2019-2021"
    assert guidance.provenance["toi_calibration_target"] == "manifest_label_v1"
    assert guidance.provenance["toi_calibration_validated"] == "no"
    assert guidance.provenance["toi_calibration_dataset"] == "synthetic-fixture"
    assert "not official SPC calibration" in guidance.provenance[
        "toi_probability_status"
    ]


def test_default_guidance_carries_no_calibration_identity():
    guidance = build_live_hrrr_guidance(
        datetime(2021, 3, 25, 6, tzinfo=timezone.utc),
        DEFAULT_FORECAST_HOUR,
        DEFAULT_LATITUDE,
        DEFAULT_LONGITUDE,
        fetcher=synthetic_fetcher,
    )

    assert guidance.toi.calibration_version == TOI_PROBABILITY_VERSION
    assert "toi_calibration_years" not in guidance.provenance
    assert "public-anchor transform" in guidance.provenance[
        "toi_probability_status"
    ]


# --------------------------------------------------------------------------
# sharpmod-guidance CLI
# --------------------------------------------------------------------------

FIXTURE_FETCHER = "sharpmod.tests._toi_fixtures:synthetic_fetcher"


def test_guidance_cli_builds_trains_and_evaluates_end_to_end(tmp_path, capsys):
    from sharpmod.tools.guidance_cli import main

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(synthetic_manifest_payload()), encoding="utf-8"
    )
    dataset_path = tmp_path / "dataset.json"
    csv_path = tmp_path / "dataset.csv"
    artifact_path = tmp_path / "artifact.json"
    train_report = tmp_path / "train.json"
    evaluate_report = tmp_path / "evaluate.json"

    assert (
        main(
            [
                "build-toi-dataset",
                "--manifest",
                str(manifest),
                "--output",
                str(dataset_path),
                "--csv",
                str(csv_path),
                "--weights",
                "population",
                "--fetcher",
                FIXTURE_FETCHER,
                "--download-dir",
                str(tmp_path),
                "--quiet",
            ]
        )
        == 0
    )
    build_output = capsys.readouterr().out
    assert "not official SPC guidance" in build_output
    assert "high_risk_worthy_proxy_v1" in build_output
    assert "synthetic-fixture" in build_output
    dataset = TOIDataset.load(dataset_path)
    assert len(dataset.rows) == 24
    assert dataset.years == (2019, 2020, 2021, 2022)
    assert csv_path.exists()

    assert (
        main(
            [
                "train-toi",
                "--dataset",
                str(dataset_path),
                "--output",
                str(artifact_path),
                "--calibration-version",
                "cli-fixture-v1",
                "--test-years",
                "2022",
                "--scheme",
                "expanding-year",
                "--bootstrap",
                "0",
                "--report",
                str(train_report),
            ]
        )
        == 0
    )
    train_output = capsys.readouterr().out
    assert "Runtime inference requires NumPy only" in train_output
    assert "did NOT promote this artifact" in train_output
    assert "public-anchor transform remains the default" in train_output
    artifact = TOICalibrationArtifact.load(artifact_path)
    assert artifact.calibration_version == "cli-fixture-v1"
    assert artifact.validated is False
    assert artifact.calibration_years == "2019-2021"
    report = json.loads(train_report.read_text(encoding="utf-8"))
    assert report["cross_validation"]["scheme"] == "expanding-year"

    assert (
        main(
            [
                "evaluate-toi",
                "--dataset",
                str(dataset_path),
                "--artifact",
                str(artifact_path),
                "--scheme",
                "leave-one-year-out",
                "--bootstrap",
                "0",
                "--report",
                str(evaluate_report),
            ]
        )
        == 0
    )
    evaluate_output = capsys.readouterr().out
    for name in ("public_anchor_transform", "climatology", "calibrated_logistic"):
        assert name in evaluate_output
    evaluation = json.loads(evaluate_report.read_text(encoding="utf-8"))
    assert evaluation["dataset"]["dataset_kind"] == "synthetic-fixture"
    assert evaluation["blocked_validation"]["scheme"] == "leave-one-year-out"
    assert set(evaluation["full_sample"]["reports"]) == {
        "public_anchor_transform",
        "climatology",
        "calibrated_logistic",
    }


def test_guidance_cli_reports_configuration_errors_without_a_traceback(
    tmp_path, capsys
):
    from sharpmod.tools.guidance_cli import main

    manifest = tmp_path / "manifest.json"
    payload = synthetic_manifest_payload()
    payload["cases"][0]["anchor_source"] = "observed_tornado_locations"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "build-toi-dataset",
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "unused.json"),
            "--fetcher",
            FIXTURE_FETCHER,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "leak verifying observations" in captured.err
    assert not (tmp_path / "unused.json").exists()


def test_guidance_cli_rejects_a_malformed_fetcher_reference(tmp_path, capsys):
    from sharpmod.tools.guidance_cli import main

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(synthetic_manifest_payload()), encoding="utf-8")

    exit_code = main(
        [
            "build-toi-dataset",
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "unused.json"),
            "--fetcher",
            "not-a-reference",
        ]
    )

    assert exit_code == 2
    assert "package.module:function" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Verification strata
# --------------------------------------------------------------------------


def test_region_classifier_uses_documented_boundaries():
    assert conus_region(45.0, -110.0) == "northern_rockies_west"
    assert conus_region(33.0, -110.0) == "southwest"
    assert conus_region(42.0, -95.0) == "northern_plains_midwest"
    assert conus_region(33.0, -95.0) == "southern_plains_lower_ms"
    assert conus_region(42.0, -80.0) == "northeast_ohio_valley"
    assert conus_region(33.0, -80.0) == "southeast"
    # Exactly on a boundary resolves to the northern/eastern side, not both.
    assert conus_region(39.0, -104.0) == "northern_plains_midwest"
    assert conus_region(20.0, -100.0) == "outside_conus"

    with pytest.raises(TOIStratumError, match="out of range"):
        conus_region(120.0, -95.0)


def test_season_lead_and_hrrr_era_classifiers():
    assert season_name(12) == "winter"
    assert season_name(4) == "spring"
    assert season_name(7) == "summer"
    assert season_name(10) == "autumn"
    assert forecast_lead_bin(0) == "f00-f06"
    assert forecast_lead_bin(6) == "f00-f06"
    assert forecast_lead_bin(7) == "f07-f12"
    assert forecast_lead_bin(18) == "f13-f18"
    assert forecast_lead_bin(30) == "f19-plus"
    # Documented NCEP HRRR implementation dates bound each era.
    assert hrrr_era("2015-05-01T06:00:00+00:00") == "HRRRv1"
    assert hrrr_era("2017-05-01T06:00:00+00:00") == "HRRRv2"
    assert hrrr_era("2019-05-01T06:00:00+00:00") == "HRRRv3"
    assert hrrr_era("2023-03-31T06:00:00+00:00") == "HRRRv4"
    assert hrrr_era("2014-01-01T00:00:00+00:00") == "pre-HRRRv1"

    with pytest.raises(TOIStratumError, match="month must be 1-12"):
        season_name(13)
    with pytest.raises(TOIStratumError, match="non-negative"):
        forecast_lead_bin(-1)
    with pytest.raises(TOIStratumError, match="ISO-8601"):
        hrrr_era("not-a-date")


def test_stratified_reports_cover_every_dimension_with_sample_counts(
    synthetic_dataset,
):
    probabilities = [0.4] * len(synthetic_dataset.rows)

    report = stratified_reports(
        synthetic_dataset.rows, probabilities, minimum_cases=10
    )

    assert set(report) == set(STRATUM_DIMENSIONS)
    seasons = report["season"]
    assert "spring" in seasons
    for strata in report.values():
        for entry in strata.values():
            assert entry["cases"] >= 1
            assert "event_groups" in entry
            assert "positive_events" in entry
            assert "reportable" in entry
            # A small stratum is listed but flagged, never silently dropped.
            assert entry["reportable"] == (entry["cases"] >= 10)


def test_stratified_reports_expose_degradation_against_the_anchor(
    synthetic_dataset,
):
    # A deliberately terrible forecast must show up as negative skill.
    inverted = [1.0 - float(row.label) for row in synthetic_dataset.rows]

    report = stratified_reports(synthetic_dataset.rows, inverted, minimum_cases=1)
    spring = report["season"]["spring"]

    assert spring["brier_skill_score"] < 0.0
    assert spring["brier_score_change_vs_anchor"] < 0.0
    assert "calibration_slope" in spring


# --------------------------------------------------------------------------
# Paired grouped bootstrap
# --------------------------------------------------------------------------


def test_paired_bootstrap_detects_a_real_improvement():
    generator = np.random.default_rng(5)
    labels = np.repeat([0.0, 1.0], 120)
    good = np.clip(labels * 0.7 + generator.normal(0.15, 0.05, 240), 0.01, 0.99)
    poor = np.full(240, 0.5)
    events = [f"event-{index // 4}" for index in range(240)]

    interval = bootstrap_brier_difference(
        good, poor, labels, groups=events, samples=200
    )

    assert interval["point"] > 0
    assert interval["lower"] > 0
    assert interval["improves"] is True
    assert interval["blocks"] == 60


def test_paired_bootstrap_refuses_to_call_noise_an_improvement():
    generator = np.random.default_rng(8)
    labels = (generator.uniform(size=60) < 0.3).astype(float)
    # Two forecasts that are both uninformative and nearly identical.
    reference = np.full(60, 0.3)
    candidate = np.clip(reference + generator.normal(0.0, 0.01, 60), 0.01, 0.99)
    events = [f"event-{index // 3}" for index in range(60)]

    interval = bootstrap_brier_difference(
        candidate, reference, labels, groups=events, samples=300
    )

    assert interval["lower"] <= 0.0
    assert interval["improves"] is False


def test_paired_bootstrap_is_grouped_by_event_not_by_row():
    # Eight events of ten highly correlated cases each: four events are
    # forecast well and four badly.  Rows inside an event are near-duplicates,
    # so resampling rows understates the uncertainty that resampling whole
    # events reveals.
    labels: list[float] = []
    candidate: list[float] = []
    events: list[str] = []
    for event in range(8):
        good = event % 2 == 0
        for case in range(10):
            label = float(case % 2)
            labels.append(label)
            candidate.append(0.9 * label + 0.05 if good else 0.9 * (1 - label) + 0.05)
            events.append(f"event-{event}")
    reference = np.full(len(labels), 0.5)

    grouped = bootstrap_brier_difference(
        candidate, reference, labels, groups=events, samples=400
    )
    ungrouped = bootstrap_brier_difference(
        candidate, reference, labels, samples=400
    )

    assert grouped["blocks"] == 8
    assert ungrouped["blocks"] == 80
    assert grouped["point"] == pytest.approx(ungrouped["point"])
    # Resampling whole events is the more conservative, correct choice here.
    grouped_width = grouped["upper"] - grouped["lower"]
    ungrouped_width = ungrouped["upper"] - ungrouped["lower"]
    assert grouped_width > 3.0 * ungrouped_width


# --------------------------------------------------------------------------
# Promotion criteria and pre-registration
# --------------------------------------------------------------------------


def test_research_target_criteria_encode_the_scientific_bar():
    criteria = TOIPromotionCriteria.research_target()

    assert criteria.scientific is True
    assert criteria.minimum_development_years >= 8
    assert criteria.minimum_event_groups >= 200
    assert criteria.minimum_positive_events >= 30
    assert criteria.minimum_fold_positive_events >= 1
    assert criteria.minimum_test_positive_events >= 1
    assert criteria.require_chronological_test_period is True
    assert criteria.require_bootstrap_improvement_over_climatology is True
    assert criteria.require_bootstrap_improvement_over_anchor is True
    assert criteria.require_stratified_reporting is True
    assert criteria.require_frozen_plan is True
    assert criteria.require_prospective_evaluation is True


def test_pipeline_smoke_criteria_can_never_promote():
    criteria = TOIPromotionCriteria.pipeline_smoke()

    assert criteria.scientific is False
    decision = evaluate_promotion(
        criteria=criteria,
        dataset_kind="historical",
        development_years=(2019, 2020),
        test_years=(2021,),
        development_rows=(),
        test_rows=(),
    )
    assert decision["validated"] is False
    assert any("non-scientific" in blocker for blocker in decision["blockers"])


def test_criteria_round_trip_and_reject_unknown_fields():
    criteria = TOIPromotionCriteria.research_target(minimum_event_groups=250)

    assert TOIPromotionCriteria.from_mapping(criteria.to_mapping()) == criteria

    with pytest.raises(TOIValidationError, match="unknown promotion criteria"):
        TOIPromotionCriteria.from_mapping({"minimum_events": 5})
    with pytest.raises(TOIValidationError, match="cannot exceed"):
        TOIPromotionCriteria(minimum_event_groups=10, minimum_positive_events=50)
    with pytest.raises(TOIValidationError, match="bootstrap_confidence"):
        TOIPromotionCriteria(bootstrap_confidence=1.5)


def test_recommended_plan_matches_the_documented_chronological_windows():
    plan = TOIValidationPlan.recommended()

    assert plan.development_years == RECOMMENDED_DEVELOPMENT_YEARS
    assert plan.test_years == RECOMMENDED_TEST_YEARS
    assert plan.development_years[0] == 2015
    assert plan.development_years[-1] == 2022
    assert plan.test_years == (2023, 2024, 2025)
    assert len(plan.development_years) >= 8
    assert plan.criteria.scientific is True
    assert plan.prospective_season


def test_plan_rejects_overlapping_or_backwards_split_years():
    with pytest.raises(TOIValidationError, match="must not overlap"):
        TOIValidationPlan.recommended(
            development_years=(2018, 2019), test_years=(2019, 2020)
        )
    with pytest.raises(TOIValidationError, match="strictly later"):
        TOIValidationPlan.recommended(
            development_years=(2020, 2021), test_years=(2015, 2016)
        )
    with pytest.raises(TOIValidationError, match="case_selection_rules"):
        TOIValidationPlan.recommended(case_selection_rules="ad hoc")


def test_plan_freeze_round_trip_detects_post_freeze_edits(tmp_path):
    plan = TOIValidationPlan.recommended()
    path = Path(plan.save(tmp_path / "plan.json"))

    reloaded = TOIValidationPlan.load(path)
    assert reloaded.plan_hash() == plan.plan_hash()
    assert reloaded.criteria == plan.criteria

    # Quietly shrinking the untouched test period is still a valid split, so
    # only the hash can catch it.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["test_years"] = [2023, 2024]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TOIValidationError, match="edited after freezing"):
        TOIValidationPlan.load(path)

    # Loosening a threshold after freezing is caught the same way.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["test_years"] = list(RECOMMENDED_TEST_YEARS)
    payload["criteria"]["minimum_positive_events"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TOIValidationError, match="edited after freezing"):
        TOIValidationPlan.load(path)


def test_plan_hash_changes_when_a_gate_is_loosened(tmp_path):
    strict = TOIValidationPlan.recommended()
    loosened = TOIValidationPlan.recommended(
        criteria=TOIPromotionCriteria.research_target(minimum_positive_events=2)
    )

    assert strict.plan_hash() != loosened.plan_hash()


# --------------------------------------------------------------------------
# Promotion blockers
# --------------------------------------------------------------------------


def _fold(year: int, *, positive: int, negative: int) -> dict:
    return {
        "verification_year": year,
        "positive_events": positive,
        "negative_events": negative,
    }


def _promotion(**overrides):
    """Call evaluate_promotion with an otherwise-passing research-gate run."""

    improving = {
        "point": 0.05,
        "lower": 0.01,
        "upper": 0.09,
        "improves": True,
    }
    stratified = {
        dimension: {"all": {"cases": 500, "brier_skill_score": 0.2}}
        for dimension in STRATUM_DIMENSIONS
    }
    # The plan must be frozen *before* the prospective season starts, or the
    # season is retrospective. ``frozen_at`` is deliberately outside
    # ``content()``, so pinning it here does not change ``plan_hash()``.
    plan = TOIValidationPlan.recommended(frozen_at="2026-01-15T00:00:00+00:00")
    prospective = TOIProspectiveRecord(
        season_label="2026 spring",
        start_date="2026-03-01",
        end_date="2026-06-30",
        artifact_calibration_version="candidate-v1",
        plan_hash=plan.plan_hash(),
        event_groups=60,
        positive_events=8,
        cases=180,
    )
    values = {
        "criteria": plan.criteria,
        "dataset_kind": "historical",
        "development_years": plan.development_years,
        "test_years": plan.test_years,
        "development_rows": _rows(positive=40, negative=200),
        "test_rows": _rows(positive=12, negative=60, prefix="test"),
        "fold_reports": [
            _fold(year, positive=5, negative=20)
            for year in plan.development_years[1:]
        ],
        "bootstrap": {
            "climatology": dict(improving),
            "public_anchor_transform": dict(improving),
        },
        "stratified": stratified,
        "plan": plan,
        "prospective": prospective,
    }
    values.update(overrides)
    return evaluate_promotion(**values)


class _Row:
    """Minimal row stand-in for gate arithmetic (no extraction required)."""

    def __init__(self, event_id: str, label: int):
        self.event_id = event_id
        self.label = label


def _rows(*, positive: int, negative: int, prefix: str = "dev"):
    return [_Row(f"{prefix}-pos-{index}", 1) for index in range(positive)] + [
        _Row(f"{prefix}-neg-{index}", 0) for index in range(negative)
    ]


def test_a_fully_satisfied_research_gate_promotes():
    # This exercises the gate arithmetic only; the counts here are synthetic
    # stand-ins, which is exactly why dataset_kind still has to be historical.
    decision = _promotion()

    assert decision["blockers"] == ()
    assert decision["validated"] is True
    assert decision["criteria_are_scientific"] is True
    assert decision["observed"]["positive_events"] == 52
    assert decision["plan_hash"]


def test_gate_blocks_too_few_positive_events_despite_a_large_dataset():
    decision = _promotion(
        development_rows=_rows(positive=3, negative=900),
        test_rows=_rows(positive=12, negative=60, prefix="test"),
    )

    assert decision["validated"] is False
    assert any(
        "positive event group" in blocker and "at least 30" in blocker
        for blocker in decision["blockers"]
    )


def test_gate_blocks_a_fold_with_too_few_positives_or_negatives():
    healthy = _fold(2017, positive=5, negative=20)
    thin_positive = _promotion(
        fold_reports=[_fold(2016, positive=0, negative=20), healthy]
    )
    thin_negative = _promotion(
        fold_reports=[_fold(2016, positive=5, negative=1), healthy]
    )

    assert any(
        "fold 2016 has only 0 positive event" in blocker
        for blocker in thin_positive["blockers"]
    )
    assert any(
        "fold 2016 has only 1 negative event" in blocker
        for blocker in thin_negative["blockers"]
    )


def test_gate_blocks_an_unevaluated_fold():
    decision = _promotion(
        fold_reports=[
            _fold(2016, positive=5, negative=20),
            {"verification_year": 2017, "error": "no positives to fit"},
        ]
    )

    assert any(
        "could not be evaluated: 2017" in blocker for blocker in decision["blockers"]
    )


def test_gate_blocks_a_thin_untouched_test_period():
    decision = _promotion(
        test_rows=_rows(positive=2, negative=5, prefix="test"),
    )

    assert any(
        "test period has only 2 positive event" in blocker
        for blocker in decision["blockers"]
    )
    assert any(
        "test period has only 5 negative event" in blocker
        for blocker in decision["blockers"]
    )


def test_gate_requires_bootstrap_intervals_not_point_estimates():
    missing = _promotion(bootstrap=None)
    overlapping = _promotion(
        bootstrap={
            "climatology": {
                "point": 0.02,
                "lower": -0.01,
                "upper": 0.05,
                "improves": False,
            },
            "public_anchor_transform": {
                "point": 0.03,
                "lower": 0.01,
                "upper": 0.06,
                "improves": True,
            },
        }
    )

    assert any(
        "point-estimate improvement is not evidence" in blocker
        for blocker in missing["blockers"]
    )
    assert any(
        "does not show Brier improvement over climatology" in blocker
        for blocker in overlapping["blockers"]
    )
    assert not any(
        "public-anchor" in blocker for blocker in overlapping["blockers"]
    )


def test_gate_requires_stratified_reporting_and_blocks_degradation():
    missing = _promotion(stratified=None)
    incomplete = _promotion(
        stratified={"region": {"southeast": {"cases": 400, "brier_skill_score": 0.1}}}
    )
    degraded = _promotion(
        stratified={
            **{
                dimension: {"all": {"cases": 500, "brier_skill_score": 0.2}}
                for dimension in STRATUM_DIMENSIONS
            },
            "season": {
                "spring": {"cases": 400, "brier_skill_score": 0.2},
                "winter": {"cases": 120, "brier_skill_score": -0.4},
                # Below the reportable floor, so it cannot block on its own.
                "summer": {"cases": 3, "brier_skill_score": -0.9},
            },
        }
    )

    assert any("no stratified report" in b for b in missing["blockers"])
    assert any("missing dimension(s)" in b for b in incomplete["blockers"])
    winter_blockers = [b for b in degraded["blockers"] if "winter" in b]
    assert winter_blockers and "degrades to Brier skill -0.400" in winter_blockers[0]
    assert not any("summer" in b for b in degraded["blockers"])


def test_gate_requires_a_frozen_plan_and_a_matching_split():
    without_plan = _promotion(plan=None, prospective=None)
    moved_split = _promotion(test_years=(2024, 2025))

    assert any("no frozen validation plan" in b for b in without_plan["blockers"])
    assert any(
        "do not match the frozen plan" in b for b in moved_split["blockers"]
    )


def test_gate_blocks_criteria_loosened_after_freezing():
    plan = TOIValidationPlan.recommended()
    loosened = TOIPromotionCriteria.research_target(minimum_positive_events=1)

    decision = _promotion(criteria=loosened, plan=plan)

    assert any(
        "differ from the frozen plan's criteria" in blocker
        for blocker in decision["blockers"]
    )


def test_gate_requires_prospective_shadow_validation():
    missing = _promotion(prospective=None)
    thin = _promotion(
        prospective=TOIProspectiveRecord(
            season_label="2026 spring",
            start_date="2026-03-01",
            end_date="2026-06-30",
            artifact_calibration_version="candidate-v1",
            plan_hash=TOIValidationPlan.recommended().plan_hash(),
            event_groups=5,
            positive_events=1,
            cases=12,
        )
    )
    mismatched = _promotion(
        prospective=TOIProspectiveRecord(
            season_label="2026 spring",
            start_date="2026-03-01",
            end_date="2026-06-30",
            artifact_calibration_version="candidate-v1",
            plan_hash="a-different-plan",
            event_groups=60,
            positive_events=8,
            cases=180,
        )
    )

    assert any(
        "no prospective shadow-validation record" in b for b in missing["blockers"]
    )
    assert any("event group(s); at least 40" in b for b in thin["blockers"])
    assert any(
        "references a different frozen plan" in b for b in mismatched["blockers"]
    )


def test_gate_rejects_a_backdated_prospective_season():
    """A completed historical season cannot be submitted as prospective.

    A matching plan hash proves *which* plan was used, not *when* the season
    happened, so without an ordering check "last year" would satisfy the
    strongest requirement in the gate.
    """
    plan = TOIValidationPlan.recommended(frozen_at="2026-08-06T00:00:00+00:00")
    backdated = _promotion(
        plan=plan,
        prospective=TOIProspectiveRecord(
            season_label="2025 spring",
            start_date="2025-03-01",
            end_date="2025-06-30",
            artifact_calibration_version="candidate-v1",
            plan_hash=plan.plan_hash(),
            event_groups=60,
            positive_events=8,
            cases=180,
        ),
    )

    assert backdated["validated"] is False
    assert any(
        "retrospective, not prospective" in blocker
        for blocker in backdated["blockers"]
    )
    # The hash matched, so the old checks could not have caught this.
    assert not any(
        "references a different frozen plan" in blocker
        for blocker in backdated["blockers"]
    )
    assert backdated["observed"]["prospective_starts_after_freeze"] is False


def test_gate_accepts_a_season_beginning_after_the_freeze():
    decision = _promotion()

    assert decision["observed"]["prospective_starts_after_freeze"] is True
    assert decision["observed"]["plan_frozen_at"] == "2026-01-15T00:00:00+00:00"
    assert decision["observed"]["prospective_start_date"] == "2026-03-01"
    assert not any(
        "retrospective, not prospective" in blocker
        for blocker in decision["blockers"]
    )


def test_gate_rejects_unverifiable_prospective_dates():
    plan = TOIValidationPlan.recommended(frozen_at="2026-01-15T00:00:00+00:00")
    unparseable = _promotion(
        plan=plan,
        prospective=TOIProspectiveRecord(
            season_label="next season",
            start_date="sometime next spring",
            end_date="2027-06-30",
            artifact_calibration_version="candidate-v1",
            plan_hash=plan.plan_hash(),
            event_groups=60,
            positive_events=8,
            cases=180,
        ),
    )
    reversed_range = _promotion(
        plan=plan,
        prospective=TOIProspectiveRecord(
            season_label="2027 spring",
            start_date="2027-06-30",
            end_date="2027-03-01",
            artifact_calibration_version="candidate-v1",
            plan_hash=plan.plan_hash(),
            event_groups=60,
            positive_events=8,
            cases=180,
        ),
    )

    assert any(
        "is not an ISO-8601 date" in blocker for blocker in unparseable["blockers"]
    )
    assert unparseable["observed"]["prospective_starts_after_freeze"] is None
    assert any(
        "end_date precedes start_date" in blocker
        for blocker in reversed_range["blockers"]
    )


def test_gate_blocks_a_non_chronological_test_period():
    decision = evaluate_promotion(
        criteria=TOIPromotionCriteria.research_target(require_frozen_plan=False),
        dataset_kind="historical",
        development_years=(2020, 2021, 2022),
        test_years=(2016, 2017, 2018),
        development_rows=_rows(positive=40, negative=200),
        test_rows=_rows(positive=12, negative=60, prefix="test"),
    )

    assert any(
        "not strictly later than every development year" in blocker
        for blocker in decision["blockers"]
    )


# --------------------------------------------------------------------------
# End-to-end: the hardened gate on a real (synthetic) run
# --------------------------------------------------------------------------


def test_default_training_uses_the_research_gate_not_a_smoke_gate(
    synthetic_dataset,
):
    _artifact_out, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="defaults-v1",
        test_years=(2022,),
        bootstrap_samples=64,
    )
    promotion = metrics["promotion"]
    blockers = " | ".join(promotion["blockers"])

    assert promotion["criteria_version"] == "toi_research_target_v1"
    assert promotion["criteria_are_scientific"] is True
    assert metrics["validated"] is False
    # The four-year fixture fails the real sample-size, plan, and prospective
    # gates, which is the point.
    assert "development year(s); at least 8" in blockers
    assert "independent event group(s); at least 200" in blockers
    assert "positive event group(s); at least 30" in blockers
    assert "no frozen validation plan" in blockers
    assert "no prospective shadow-validation record" in blockers


def test_training_reports_stratified_metrics_and_paired_bootstrap(
    synthetic_dataset,
):
    _artifact_out, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="stratified-v1",
        test_years=(2022,),
        bootstrap_samples=64,
        criteria=SMOKE,
    )
    held_out = metrics["held_out_test"]

    assert set(held_out["stratified"]) == set(STRATUM_DIMENSIONS)
    assert set(held_out["brier_improvement_bootstrap"]) == {
        "climatology",
        "public_anchor_transform",
    }
    for interval in held_out["brier_improvement_bootstrap"].values():
        assert "improves" in interval or "error" in interval
    folds = metrics["cross_validation"]["folds"]
    for fold in folds:
        assert "positive_events" in fold
        assert "negative_events" in fold


def test_training_records_the_frozen_plan_and_criteria_in_its_metrics(
    synthetic_dataset, tmp_path
):
    # A plan whose split matches the fixture, so only the science gates block.
    plan = TOIValidationPlan(
        plan_version="fixture-plan-v1",
        target_definition="high_risk_worthy_proxy_v1",
        case_selection_rules=(
            "Synthetic fixture cases spanning four years with outbreak, severe, "
            "and null classes; pipeline exercise only."
        ),
        development_years=(2019, 2020, 2021),
        test_years=(2022,),
        prospective_season="2026 spring",
        criteria=TOIPromotionCriteria.research_target(),
        validation_scheme="expanding-year",
    )
    plan.save(tmp_path / "plan.json")

    artifact, metrics = train_toi_calibrator(
        synthetic_dataset,
        calibration_version="planned-v1",
        plan=plan,
        bootstrap_samples=64,
    )

    assert metrics["validation_plan"]["plan_hash"] == plan.plan_hash()
    assert metrics["promotion"]["plan_hash"] == plan.plan_hash()
    assert metrics["promotion"]["plan_version"] == "fixture-plan-v1"
    assert metrics["cross_validation"]["scheme"] == "expanding-year"
    assert artifact.validated is False
    # The split matched the plan, so no split-mismatch blocker appears.
    assert not any(
        "do not match the frozen plan" in blocker
        for blocker in metrics["validation_blockers"]
    )
    assert any(
        "synthetic fixture" in blocker
        for blocker in metrics["validation_blockers"]
    )


def test_guidance_cli_freezes_a_plan_and_trains_against_it(tmp_path, capsys):
    from sharpmod.tools.guidance_cli import main

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(synthetic_manifest_payload()), encoding="utf-8")
    dataset_path = tmp_path / "dataset.json"
    plan_path = tmp_path / "plan.json"
    artifact_path = tmp_path / "artifact.json"
    report_path = tmp_path / "train.json"

    assert (
        main(
            [
                "build-toi-dataset",
                "--manifest",
                str(manifest),
                "--output",
                str(dataset_path),
                "--fetcher",
                FIXTURE_FETCHER,
                "--download-dir",
                str(tmp_path),
                "--quiet",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "freeze-toi-plan",
                "--output",
                str(plan_path),
                "--plan-version",
                "cli-plan-v1",
                "--development-years",
                "2019,2020,2021",
                "--test-years",
                "2022",
                "--prospective-season",
                "2026 spring",
            ]
        )
        == 0
    )
    freeze_output = capsys.readouterr().out
    assert "Froze validation plan cli-plan-v1" in freeze_output
    assert "Plan hash:" in freeze_output
    assert "Prospective shadow season: 2026 spring" in freeze_output
    plan = TOIValidationPlan.load(plan_path)
    assert plan.development_years == (2019, 2020, 2021)
    assert plan.test_years == (2022,)
    assert plan.criteria.scientific is True

    assert (
        main(
            [
                "train-toi",
                "--dataset",
                str(dataset_path),
                "--output",
                str(artifact_path),
                "--calibration-version",
                "cli-planned-v1",
                "--plan",
                str(plan_path),
                "--bootstrap",
                "64",
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    train_output = capsys.readouterr().out
    assert "Promotion gate: toi_research_target_v1 (scientific=True)" in train_output
    assert "did NOT promote this artifact" in train_output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["validation_plan"]["plan_hash"] == plan.plan_hash()
    assert report["promotion"]["criteria_are_scientific"] is True
    assert TOICalibrationArtifact.load(artifact_path).validated is False


def test_guidance_cli_warns_that_the_smoke_gate_cannot_promote(tmp_path, capsys):
    from sharpmod.tools.guidance_cli import main

    assert (
        main(
            [
                "freeze-toi-plan",
                "--output",
                str(tmp_path / "smoke-plan.json"),
                "--criteria",
                "pipeline-smoke",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "non-scientific pipeline smoke gate" in output
    assert "can never promote" in output
