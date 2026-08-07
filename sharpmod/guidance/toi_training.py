"""Year-blocked training and validation for the experimental TOI calibrator.

Splitting is always by complete year *and* by event, never by random row: two
forecast cycles from one outbreak, or two neighbouring cases on consecutive
days, are not independent samples, and mixing them across a split inflates
apparent skill.

The blocking unit is the dataset's ``event_year``, which assigns one validation
year to a whole event id.  A case series that crosses a New Year boundary
therefore stays inside a single fold instead of straddling train and test.

Every fit is compared against two references on the same cases:

* the existing public-anchor transform shipped in :mod:`toi_scorecard`; and
* sample climatology.

A fitted artifact is only marked ``validated`` when it came from a declared
historical multi-year dataset *and* beat both references on an untouched test
period.  Synthetic fixtures exercise this pipeline but can never set that flag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .toi_calibration import (
    TOI_CALIBRATION_FEATURE_SCHEMA,
    TOICalibrationArtifact,
    TOICalibrationError,
    calibrated_probabilities,
    fit_logistic_calibrator,
    toi_feature_vector,
)
from .toi_dataset import TOIDataset, TOIDatasetError
from .toi_evaluation import (
    DEFAULT_DECISION_THRESHOLD,
    TOIEvaluationError,
    bootstrap_brier_difference,
    climatology_probabilities,
    compare_reports,
    evaluate_probabilities,
    expanding_year_folds,
    leave_one_year_out_folds,
)
from .toi_strata import STRATUM_DIMENSIONS, group_rows_by_stratum
from .toi_validation import (
    TOIPromotionCriteria,
    TOIProspectiveRecord,
    TOIValidationPlan,
    evaluate_promotion,
)

TOI_TRAINING_METHOD_VERSION = "sharpmod_toi_year_blocked_training_v2"
VALIDATION_SCHEMES = ("leave-one-year-out", "expanding-year")


def _split_rows(dataset: TOIDataset, test_years: Sequence[int]):
    """Split on ``event_year`` so no event can appear on both sides."""

    held_out = {int(year) for year in test_years}
    unknown = held_out.difference(dataset.years)
    if unknown:
        raise TOIDatasetError(
            "test years absent from the dataset: "
            + ",".join(str(year) for year in sorted(unknown))
        )
    train = tuple(row for row in dataset.rows if row.event_year not in held_out)
    test = tuple(row for row in dataset.rows if row.event_year in held_out)
    if not train:
        raise TOIDatasetError("every dataset year was assigned to the test period")
    shared = {row.event_id for row in train} & {row.event_id for row in test}
    if shared:  # pragma: no cover - TOIDataset already enforces one event_year
        raise TOIDatasetError(
            "event(s) appear in both the training and test periods: "
            + ", ".join(sorted(shared)[:5])
        )
    return train, test


def _design(rows) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    features = np.asarray(
        [
            toi_feature_vector(row["experimental_score"], row["maximum_stp"])
            for row in rows
        ],
        dtype=float,
    )
    labels = np.asarray([row.label for row in rows], dtype=float)
    weights = np.asarray([row.sample_weight for row in rows], dtype=float)
    return features, labels, weights


def _public_anchor_probabilities(rows) -> np.ndarray:
    """Read the shipped public-anchor probability already stored per row."""

    values = [row["public_anchor_probability"] for row in rows]
    if any(value is None for value in values):
        raise TOIDatasetError(
            "dataset rows are missing public_anchor_probability, so the new "
            "calibrator cannot be compared against the shipped transform"
        )
    return np.asarray(values, dtype=float)


def _fit(rows, *, l2_penalty: float) -> dict[str, Any]:
    features, labels, weights = _design(rows)
    intercept, coefficients, means, scales = fit_logistic_calibrator(
        features, labels, sample_weights=weights, l2_penalty=l2_penalty
    )
    return {
        "intercept": intercept,
        "coefficients": coefficients,
        "feature_means": means,
        "feature_scales": scales,
        "base_rate": float(np.sum(weights * labels) / np.sum(weights)),
    }


def _artifact_from_fit(
    parameters: dict[str, Any],
    *,
    calibration_version: str,
    dataset: TOIDataset,
    training_years: Sequence[int],
    test_years: Sequence[int],
    rows,
    l2_penalty: float,
    metrics: dict[str, Any],
    validated: bool,
    notes: str,
) -> TOICalibrationArtifact:
    return TOICalibrationArtifact(
        calibration_version=calibration_version,
        intercept=parameters["intercept"],
        coefficients=parameters["coefficients"],
        feature_means=parameters["feature_means"],
        feature_scales=parameters["feature_scales"],
        feature_schema=TOI_CALIBRATION_FEATURE_SCHEMA,
        training_years=tuple(training_years),
        test_years=tuple(test_years),
        target_definition=dataset.target_definition,
        base_rate=parameters["base_rate"],
        l2_penalty=l2_penalty,
        sample_count=len(rows),
        event_count=len({row.event_id for row in rows}),
        dataset_kind=dataset.dataset_kind,
        validated=validated,
        metrics=metrics,
        data_hash=dataset.data_hash(),
        notes=notes,
    )


def cross_validate(
    dataset: TOIDataset,
    *,
    scheme: str = "leave-one-year-out",
    l2_penalty: float = 1.0,
    threshold: float = DEFAULT_DECISION_THRESHOLD,
    rows=None,
) -> dict[str, Any]:
    """Run blocked-year cross-validation and pool the out-of-sample forecasts."""

    if scheme not in VALIDATION_SCHEMES:
        raise TOIDatasetError(f"scheme must be one of {VALIDATION_SCHEMES}")
    pool = tuple(rows if rows is not None else dataset.rows)
    # Fold on the event's single blocking year, never on a row's own calendar
    # year, so a year-spanning event stays inside one fold.
    years = [row.event_year for row in pool]
    folds = (
        leave_one_year_out_folds(years)
        if scheme == "leave-one-year-out"
        else expanding_year_folds(years)
    )
    predicted: list[float] = []
    observed: list[float] = []
    weights: list[float] = []
    groups: list[str] = []
    fold_reports: list[dict[str, Any]] = []
    for verification_year, training_years in folds:
        training_rows = tuple(
            row for row in pool if row.event_year in set(training_years)
        )
        verification_rows = tuple(
            row for row in pool if row.event_year == verification_year
        )
        if not training_rows or not verification_rows:
            continue
        overlap = {row.event_id for row in training_rows} & {
            row.event_id for row in verification_rows
        }
        if overlap:  # pragma: no cover - TOIDataset enforces one event_year
            raise TOIDatasetError(
                f"fold {verification_year} shares event(s) with its training "
                "years: " + ", ".join(sorted(overlap)[:5])
            )
        try:
            parameters = _fit(training_rows, l2_penalty=l2_penalty)
        except TOICalibrationError as exc:
            fold_reports.append(
                {"verification_year": verification_year, "error": str(exc)}
            )
            continue
        artifact = TOICalibrationArtifact(
            calibration_version=f"fold-{verification_year}",
            intercept=parameters["intercept"],
            coefficients=parameters["coefficients"],
            feature_means=parameters["feature_means"],
            feature_scales=parameters["feature_scales"],
            training_years=tuple(training_years),
            target_definition=dataset.target_definition,
            base_rate=parameters["base_rate"],
            data_hash=dataset.data_hash(),
            dataset_kind=dataset.dataset_kind,
        )
        fold_probability = calibrated_probabilities(
            artifact,
            [row["experimental_score"] for row in verification_rows],
            [row["maximum_stp"] for row in verification_rows],
        )
        predicted.extend(float(value) for value in fold_probability)
        observed.extend(float(row.label) for row in verification_rows)
        weights.extend(float(row.sample_weight) for row in verification_rows)
        groups.extend(row.event_id for row in verification_rows)
        positive_events = {
            row.event_id for row in verification_rows if row.label == 1
        }
        all_events = {row.event_id for row in verification_rows}
        fold_reports.append(
            {
                "verification_year": verification_year,
                "training_years": list(training_years),
                "cases": len(verification_rows),
                "positives": sum(row.label for row in verification_rows),
                "event_groups": len(all_events),
                "positive_events": len(positive_events),
                "negative_events": len(all_events - positive_events),
                "training_event_groups": len(
                    {row.event_id for row in training_rows}
                ),
                "training_positive_events": len(
                    {row.event_id for row in training_rows if row.label == 1}
                ),
                "verification_events": sorted(all_events),
            }
        )
    if not predicted:
        raise TOIDatasetError(
            "blocked cross-validation produced no out-of-sample forecasts; the "
            "dataset needs more complete years with both outcomes"
        )
    try:
        pooled = evaluate_probabilities(
            predicted, observed, weights, threshold=threshold
        )
    except TOIEvaluationError as exc:
        raise TOIDatasetError(f"cross-validation metrics unavailable: {exc}") from exc
    return {
        "scheme": scheme,
        "folds": fold_reports,
        "pooled_out_of_sample": pooled,
        "pooled_event_groups": sorted(set(groups)),
    }


def stratified_reports(
    rows,
    candidate_probabilities,
    *,
    threshold: float = DEFAULT_DECISION_THRESHOLD,
    minimum_cases: int = 1,
) -> dict[str, dict[str, Any]]:
    """Report calibration and skill by region, season, lead, and HRRR era.

    Every stratum carries its own sample counts so a favourable number over six
    cases is not mistaken for evidence.  Strata below ``minimum_cases`` are still
    listed, with ``reportable`` false, rather than silently dropped.
    """

    pool = tuple(rows)
    if not pool:
        raise TOIDatasetError("no rows to stratify")
    candidate = np.asarray(candidate_probabilities, dtype=float)
    if candidate.size != len(pool):
        raise TOIDatasetError("candidate probabilities must match the row count")
    anchor = _public_anchor_probabilities(pool)
    labels = np.asarray([float(row.label) for row in pool], dtype=float)
    weights = np.asarray([float(row.sample_weight) for row in pool], dtype=float)

    result: dict[str, dict[str, Any]] = {
        dimension: {} for dimension in STRATUM_DIMENSIONS
    }
    for dimension, strata in group_rows_by_stratum(pool).items():
        for label, indexes in strata.items():
            picked = np.asarray(indexes, dtype=int)
            events = {pool[index].event_id for index in picked}
            positive_events = {
                pool[index].event_id for index in picked if pool[index].label == 1
            }
            entry: dict[str, Any] = {
                "cases": int(picked.size),
                "event_groups": len(events),
                "positive_events": len(positive_events),
                "negative_events": len(events - positive_events),
                "reportable": bool(picked.size >= int(minimum_cases)),
            }
            try:
                candidate_report = evaluate_probabilities(
                    candidate[picked],
                    labels[picked],
                    weights[picked],
                    threshold=threshold,
                )
            except TOIEvaluationError as exc:
                entry["error"] = str(exc)
                result[dimension][label] = entry
                continue
            entry.update(
                {
                    "base_rate": candidate_report["base_rate"],
                    "brier_score": candidate_report["brier_score"],
                    "brier_skill_score": candidate_report["brier_skill_score"],
                    "calibration_intercept": candidate_report[
                        "calibration_intercept"
                    ],
                    "calibration_slope": candidate_report["calibration_slope"],
                    "contingency": candidate_report["contingency"],
                }
            )
            try:
                anchor_report = evaluate_probabilities(
                    anchor[picked],
                    labels[picked],
                    weights[picked],
                    threshold=threshold,
                )
                entry["public_anchor_brier_score"] = anchor_report["brier_score"]
                entry["brier_score_change_vs_anchor"] = (
                    anchor_report["brier_score"] - candidate_report["brier_score"]
                )
            except TOIEvaluationError as exc:  # pragma: no cover - same guards
                entry["public_anchor_error"] = str(exc)
            result[dimension][label] = entry
    return result


def evaluate_dataset(
    dataset: TOIDataset,
    *,
    artifact: TOICalibrationArtifact | None = None,
    rows=None,
    threshold: float = DEFAULT_DECISION_THRESHOLD,
    bootstrap_samples: int = 0,
    bootstrap_confidence: float = 0.95,
    stratify: bool = False,
    minimum_stratum_cases: int = 1,
) -> dict[str, Any]:
    """Score the shipped transform, climatology, and one optional artifact."""

    pool = tuple(rows if rows is not None else dataset.rows)
    if not pool:
        raise TOIDatasetError("no rows to evaluate")
    labels = [float(row.label) for row in pool]
    weights = [float(row.sample_weight) for row in pool]
    groups = [row.event_id for row in pool]
    reports: dict[str, Any] = {
        "public_anchor_transform": evaluate_probabilities(
            _public_anchor_probabilities(pool),
            labels,
            weights,
            threshold=threshold,
            bootstrap_samples=bootstrap_samples,
            bootstrap_groups=groups,
        ),
        "climatology": evaluate_probabilities(
            climatology_probabilities(labels, weights),
            labels,
            weights,
            threshold=threshold,
        ),
    }
    result: dict[str, Any] = {
        "cases": len(pool),
        "events": len(set(groups)),
        "positive_events": len({row.event_id for row in pool if row.label == 1}),
        "years": sorted({row.event_year for row in pool}),
        "calendar_years": sorted({row.year for row in pool}),
    }
    if artifact is None:
        result["reports"] = reports
        result["comparison"] = compare_reports(reports)
        return result

    candidate = calibrated_probabilities(
        artifact,
        [row["experimental_score"] for row in pool],
        [row["maximum_stp"] for row in pool],
    )
    reports["calibrated_logistic"] = evaluate_probabilities(
        candidate,
        labels,
        weights,
        threshold=threshold,
        bootstrap_samples=bootstrap_samples,
        bootstrap_groups=groups,
    )
    result["reports"] = reports
    result["comparison"] = compare_reports(reports)

    # Paired, event-blocked bootstrap of the improvement itself.  Two separate
    # intervals that happen not to overlap is a weaker claim than one interval
    # on the difference, and a point-estimate gain is no claim at all.
    if int(bootstrap_samples) > 1:
        differences: dict[str, Any] = {}
        for name, reference in (
            ("climatology", climatology_probabilities(labels, weights)),
            ("public_anchor_transform", _public_anchor_probabilities(pool)),
        ):
            try:
                differences[name] = bootstrap_brier_difference(
                    candidate,
                    reference,
                    labels,
                    weights,
                    groups=groups,
                    samples=int(bootstrap_samples),
                    confidence=float(bootstrap_confidence),
                )
            except TOIEvaluationError as exc:
                differences[name] = {"error": str(exc)}
        result["brier_improvement_bootstrap"] = differences

    if stratify:
        result["stratified"] = stratified_reports(
            pool,
            candidate,
            threshold=threshold,
            minimum_cases=int(minimum_stratum_cases),
        )
    return result


def _point_estimate_blockers(test_report: Mapping[str, Any] | None) -> list[str]:
    """Keep the original point-estimate checks as a necessary-but-weak floor."""

    if not test_report:
        return []
    reports = test_report.get("reports", {})
    calibrated = reports.get("calibrated_logistic", {})
    anchor = reports.get("public_anchor_transform", {})
    blockers: list[str] = []
    skill = calibrated.get("brier_skill_score")
    if skill is None or float(skill) <= 0.0:
        blockers.append(
            f"held-out Brier skill against climatology is not positive ({skill!r})"
        )
    calibrated_brier = calibrated.get("brier_score")
    anchor_brier = anchor.get("brier_score")
    if (
        calibrated_brier is None
        or anchor_brier is None
        or float(calibrated_brier) >= float(anchor_brier)
    ):
        blockers.append(
            "held-out Brier score does not improve on the shipped public-anchor "
            f"transform ({calibrated_brier!r} vs {anchor_brier!r})"
        )
    return blockers


def train_toi_calibrator(
    dataset: TOIDataset,
    *,
    calibration_version: str,
    test_years: Sequence[int] = (),
    l2_penalty: float = 1.0,
    scheme: str = "leave-one-year-out",
    threshold: float = DEFAULT_DECISION_THRESHOLD,
    bootstrap_samples: int = 0,
    criteria: TOIPromotionCriteria | None = None,
    plan: TOIValidationPlan | None = None,
    prospective: TOIProspectiveRecord | None = None,
) -> tuple[TOICalibrationArtifact, dict[str, Any]]:
    """Fit, validate, and package one TOI probability calibrator.

    ``criteria`` selects the promotion gate and defaults to the research target,
    so an unconfigured run is held to the strict bar rather than a smoke gate.
    ``plan`` supplies the frozen pre-registration and ``prospective`` the
    shadow-season record; both are required by the research criteria.  A frozen
    plan also supplies the split, penalty, and scheme, so the realized run can
    be compared against what was registered.
    """

    gate = criteria or (plan.criteria if plan is not None else None)
    if gate is None:
        gate = TOIPromotionCriteria.research_target()
    if plan is not None:
        if not test_years:
            test_years = plan.test_years
        scheme = plan.validation_scheme
        l2_penalty = plan.l2_penalty
    if scheme not in VALIDATION_SCHEMES:
        raise TOIDatasetError(f"scheme must be one of {VALIDATION_SCHEMES}")
    bootstrap_samples = int(bootstrap_samples) or gate.bootstrap_samples

    train_rows, test_rows = _split_rows(dataset, test_years)
    training_years = sorted({row.event_year for row in train_rows})
    metrics: dict[str, Any] = {
        "training_method_version": TOI_TRAINING_METHOD_VERSION,
        "promotion_criteria": gate.to_mapping(),
        "validation_plan": plan.to_mapping() if plan is not None else None,
        "prospective": (
            prospective.to_mapping() if prospective is not None else None
        ),
        "split": {
            "training_years": training_years,
            "test_years": sorted({int(year) for year in test_years}),
            "training_cases": len(train_rows),
            "training_events": len({row.event_id for row in train_rows}),
            "test_cases": len(test_rows),
            "test_events": len({row.event_id for row in test_rows}),
            "training_calendar_years": sorted({row.year for row in train_rows}),
            "test_calendar_years": sorted({row.year for row in test_rows}),
            "blocking": (
                "complete event_year (one validation year per event id); "
                "never random rows and never a split event"
            ),
        },
        "weighting": dataset.weighting,
    }

    if len(training_years) >= 2:
        try:
            metrics["cross_validation"] = cross_validate(
                dataset,
                scheme=scheme,
                l2_penalty=l2_penalty,
                threshold=threshold,
                rows=train_rows,
            )
        except (TOIDatasetError, TOIEvaluationError) as exc:
            metrics["cross_validation"] = {"scheme": scheme, "error": str(exc)}
    else:
        metrics["cross_validation"] = {
            "scheme": scheme,
            "error": "blocked cross-validation needs at least two training years",
        }

    parameters = _fit(train_rows, l2_penalty=l2_penalty)
    provisional = _artifact_from_fit(
        parameters,
        calibration_version=calibration_version,
        dataset=dataset,
        training_years=training_years,
        test_years=sorted({int(year) for year in test_years}),
        rows=train_rows,
        l2_penalty=l2_penalty,
        metrics={},
        validated=False,
        notes="",
    )
    metrics["in_sample"] = evaluate_dataset(
        dataset, artifact=provisional, rows=train_rows, threshold=threshold
    )
    test_report = None
    if test_rows:
        test_report = evaluate_dataset(
            dataset,
            artifact=provisional,
            rows=test_rows,
            threshold=threshold,
            bootstrap_samples=bootstrap_samples,
            bootstrap_confidence=gate.bootstrap_confidence,
            stratify=True,
            minimum_stratum_cases=gate.minimum_stratum_cases,
        )
        metrics["held_out_test"] = test_report

    fold_reports = metrics["cross_validation"].get("folds", ())
    decision = evaluate_promotion(
        criteria=gate,
        dataset_kind=dataset.dataset_kind,
        development_years=training_years,
        test_years=sorted({int(year) for year in test_years}),
        development_rows=train_rows,
        test_rows=test_rows,
        fold_reports=fold_reports,
        bootstrap=(test_report or {}).get("brier_improvement_bootstrap"),
        stratified=(test_report or {}).get("stratified"),
        plan=plan,
        prospective=prospective,
    )
    # The original point-estimate comparisons remain a necessary floor; they are
    # simply no longer sufficient on their own.
    blockers = list(decision["blockers"]) + _point_estimate_blockers(test_report)
    validated = bool(decision["validated"] and not blockers)
    metrics["promotion"] = {**decision, "blockers": list(blockers)}
    metrics["validated"] = validated
    metrics["validation_blockers"] = list(blockers)
    notes = (
        "Experimental SHARPpy calibration; not official SPC guidance. "
        + (
            "Passed the frozen, pre-registered promotion criteria "
            f"{gate.criteria_version}."
            if validated
            else "NOT eligible to replace the shipped public-anchor transform: "
            + "; ".join(blockers)
        )
    )
    return (
        _artifact_from_fit(
            parameters,
            calibration_version=calibration_version,
            dataset=dataset,
            training_years=training_years,
            test_years=sorted({int(year) for year in test_years}),
            rows=train_rows,
            l2_penalty=l2_penalty,
            metrics=metrics,
            validated=validated,
            notes=notes,
        ),
        metrics,
    )


__all__ = [
    "TOI_TRAINING_METHOD_VERSION",
    "VALIDATION_SCHEMES",
    "cross_validate",
    "evaluate_dataset",
    "stratified_reports",
    "train_toi_calibrator",
]
