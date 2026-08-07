"""Rare-event verification metrics for offline TOI calibration.

Everything here is deliberately dependency-light (NumPy only) so evaluation can
run in the same environments as the renderer.  The metric set follows the
verification design in the research notes: probabilistic skill and reliability,
deterministic contingency scores at a decision threshold, discrimination, and
bootstrap confidence intervals, always compared against both climatology and
the existing public-anchor transform.

Validation is blocked by complete year and by event, never by random rows:
successive forecast cycles and neighbouring grid cells inside one outbreak are
not independent samples.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_RELIABILITY_BINS = (0.0, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60, 1.0)
DEFAULT_DECISION_THRESHOLD = 0.50
DEFAULT_BOOTSTRAP_SAMPLES = 1000


class TOIEvaluationError(ValueError):
    """Raised when an evaluation request is not statistically meaningful."""


def strict_json_dumps(payload: Any, *, indent: int | None = None) -> str:
    """Serialize a payload as strict, portable JSON.

    ``allow_nan=False`` rejects the ``NaN``/``Infinity`` extensions that make a
    document unreadable by conforming parsers.  Undefined metrics and empty
    reliability bins therefore have to be ``None``, not ``NaN``.
    """

    try:
        return json.dumps(
            payload, indent=indent, sort_keys=True, allow_nan=False, default=str
        )
    except ValueError as exc:
        raise TOIEvaluationError(
            "refusing to write non-finite JSON: undefined values must be "
            f"serialized as null ({exc})"
        ) from exc


def _validated(
    probabilities: Sequence[float] | np.ndarray,
    labels: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    probability = np.asarray(probabilities, dtype=float).ravel()
    label = np.asarray(labels, dtype=float).ravel()
    if probability.size == 0:
        raise TOIEvaluationError("no cases to evaluate")
    if probability.size != label.size:
        raise TOIEvaluationError("probabilities and labels must have equal length")
    if not np.all(np.isfinite(probability)) or not np.all(np.isfinite(label)):
        raise TOIEvaluationError("probabilities and labels must be finite")
    if np.any(probability < 0) or np.any(probability > 1):
        raise TOIEvaluationError("probabilities must lie between 0 and 1")
    if not np.all(np.isin(label, (0.0, 1.0))):
        raise TOIEvaluationError("labels must be binary 0/1 values")
    if weights is None:
        weight = np.ones(label.size, dtype=float)
    else:
        weight = np.asarray(weights, dtype=float).ravel()
        if weight.size != label.size:
            raise TOIEvaluationError("weights must match the case count")
        if not np.all(np.isfinite(weight)) or np.any(weight < 0) or weight.sum() <= 0:
            raise TOIEvaluationError(
                "weights must be finite, non-negative, and sum > 0"
            )
    return probability, label, weight


def brier_score(probabilities, labels, weights=None) -> float:
    probability, label, weight = _validated(probabilities, labels, weights)
    return float(np.sum(weight * (probability - label) ** 2) / np.sum(weight))


def base_rate(labels, weights=None) -> float:
    _probability, label, weight = _validated(
        np.zeros(len(labels)), labels, weights
    )
    return float(np.sum(weight * label) / np.sum(weight))


def brier_skill_score(probabilities, labels, weights=None) -> float:
    """Brier skill against the sample climatology reference forecast."""

    probability, label, weight = _validated(probabilities, labels, weights)
    climatology = float(np.sum(weight * label) / np.sum(weight))
    reference = float(np.sum(weight * (climatology - label) ** 2) / np.sum(weight))
    if reference <= 0.0:
        raise TOIEvaluationError(
            "Brier skill is undefined when every case shares one outcome"
        )
    return float(1.0 - brier_score(probability, label, weight) / reference)


@dataclass(frozen=True)
class ReliabilityBin:
    """One reliability-diagram bin.

    An empty bin carries ``None`` rather than ``NaN`` for its forecast and
    observed values, so every report and artifact stays strict, portable JSON.
    """

    lower: float
    upper: float
    count: float
    mean_forecast: float | None
    observed_frequency: float | None

    def to_mapping(self) -> dict[str, float | None]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "count": self.count,
            "mean_forecast": self.mean_forecast,
            "observed_frequency": self.observed_frequency,
        }


def reliability_bins(
    probabilities,
    labels,
    weights=None,
    *,
    edges: Sequence[float] = DEFAULT_RELIABILITY_BINS,
) -> tuple[ReliabilityBin, ...]:
    """Return weighted reliability bins using SPC-style outlook thresholds."""

    probability, label, weight = _validated(probabilities, labels, weights)
    bounds = tuple(float(edge) for edge in edges)
    if len(bounds) < 2 or list(bounds) != sorted(bounds):
        raise TOIEvaluationError(
            "reliability edges must be sorted and have >= 2 values"
        )
    result: list[ReliabilityBin] = []
    for index in range(len(bounds) - 1):
        lower, upper = bounds[index], bounds[index + 1]
        last = index == len(bounds) - 2
        inside = (probability >= lower) & (
            probability <= upper if last else probability < upper
        )
        total = float(np.sum(weight[inside]))
        if total <= 0:
            result.append(ReliabilityBin(lower, upper, 0.0, None, None))
            continue
        result.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=total,
                mean_forecast=float(
                    np.sum(weight[inside] * probability[inside]) / total
                ),
                observed_frequency=float(
                    np.sum(weight[inside] * label[inside]) / total
                ),
            )
        )
    return tuple(result)


def calibration_intercept_slope(
    probabilities, labels, weights=None
) -> tuple[float, float]:
    """Fit ``logit(p_obs) = a + b * logit(p_forecast)`` (Cox calibration).

    A perfectly calibrated forecast gives intercept 0 and slope 1. The fit is a
    two-parameter weighted logistic regression solved with Newton steps.
    """

    probability, label, weight = _validated(probabilities, labels, weights)
    clipped = np.clip(probability, 1e-6, 1.0 - 1e-6)
    logit = np.log(clipped / (1.0 - clipped))
    design = np.column_stack((np.ones(logit.size), logit))
    beta = np.zeros(2, dtype=float)
    for _iteration in range(100):
        fitted = 1.0 / (1.0 + np.exp(-np.clip(design @ beta, -35.0, 35.0)))
        variance = weight * np.clip(fitted * (1.0 - fitted), 1e-10, None)
        gradient = design.T @ (weight * (label - fitted))
        hessian = design.T @ (design * variance[:, None])
        # Tiny ridge keeps a degenerate single-value logit column solvable.
        hessian += np.eye(2) * 1e-8
        step = np.linalg.solve(hessian, gradient)
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def contingency_metrics(
    probabilities,
    labels,
    weights=None,
    *,
    threshold: float = DEFAULT_DECISION_THRESHOLD,
) -> dict[str, float | None]:
    """Return POD, FAR, CSI, and frequency bias at one decision threshold.

    A score whose denominator is zero is reported as ``None`` rather than
    ``NaN`` so reports remain strict, portable JSON.
    """

    probability, label, weight = _validated(probabilities, labels, weights)
    cut = float(threshold)
    if not 0.0 < cut < 1.0:
        raise TOIEvaluationError("threshold must be strictly between 0 and 1")
    forecast = probability >= cut
    observed = label > 0.5
    hits = float(np.sum(weight[forecast & observed]))
    misses = float(np.sum(weight[~forecast & observed]))
    false_alarms = float(np.sum(weight[forecast & ~observed]))
    correct_negatives = float(np.sum(weight[~forecast & ~observed]))

    def _ratio(numerator: float, denominator: float) -> float | None:
        return float(numerator / denominator) if denominator > 0 else None

    return {
        "threshold": cut,
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "pod": _ratio(hits, hits + misses),
        "far": _ratio(false_alarms, hits + false_alarms),
        "csi": _ratio(hits, hits + misses + false_alarms),
        "frequency_bias": _ratio(hits + false_alarms, hits + misses),
    }


def roc_auc(probabilities, labels, weights=None) -> float:
    """Weighted ROC area computed from the rank-based Mann-Whitney identity."""

    probability, label, weight = _validated(probabilities, labels, weights)
    positive = label > 0.5
    positive_weight = float(np.sum(weight[positive]))
    negative_weight = float(np.sum(weight[~positive]))
    if positive_weight <= 0 or negative_weight <= 0:
        raise TOIEvaluationError("ROC area needs both positive and negative cases")
    concordant = 0.0
    for value, case_weight in zip(
        probability[positive], weight[positive], strict=True
    ):
        higher = probability[~positive] < value
        tied = probability[~positive] == value
        concordant += case_weight * (
            float(np.sum(weight[~positive][higher]))
            + 0.5 * float(np.sum(weight[~positive][tied]))
        )
    return float(concordant / (positive_weight * negative_weight))


def average_precision(probabilities, labels, weights=None) -> float:
    """Weighted area under the precision-recall curve (average precision).

    Cases sharing a forecast probability are one operating point: a decision
    threshold cannot separate them.  Collapsing tied probabilities into a single
    group makes the result permutation-invariant, and makes a completely
    uninformative forecast score exactly the weighted event base rate.
    """

    probability, label, weight = _validated(probabilities, labels, weights)
    order = np.argsort(-probability, kind="stable")
    ordered_probability = probability[order]
    ordered_label = label[order]
    ordered_weight = weight[order]
    total_positive = float(np.sum(ordered_weight * ordered_label))
    if total_positive <= 0:
        raise TOIEvaluationError("average precision needs at least one positive case")
    true_positive = np.cumsum(ordered_weight * ordered_label)
    predicted = np.cumsum(ordered_weight)
    # Keep only the last row of each tied-probability run, so every retained
    # index is an achievable threshold rather than an arbitrary row boundary.
    last_of_group = np.ones(ordered_probability.size, dtype=bool)
    last_of_group[:-1] = ordered_probability[:-1] != ordered_probability[1:]
    precision = true_positive[last_of_group] / predicted[last_of_group]
    recall = true_positive[last_of_group] / total_positive
    previous_recall = 0.0
    result = 0.0
    for group_precision, group_recall in zip(precision, recall, strict=True):
        result += (float(group_recall) - previous_recall) * float(group_precision)
        previous_recall = float(group_recall)
    return float(result)


def bootstrap_interval(
    metric,
    probabilities,
    labels,
    weights=None,
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    confidence: float = 0.95,
    seed: int = 20240427,
    groups: Sequence[Any] | None = None,
) -> dict[str, float]:
    """Return a percentile bootstrap interval for one metric.

    ``groups`` enables a block bootstrap that resamples whole events or years
    instead of individual rows, so temporally dependent cases are not split.
    """

    probability, label, weight = _validated(probabilities, labels, weights)
    count = int(samples)
    if count < 2:
        raise TOIEvaluationError("bootstrap samples must be at least two")
    if not 0.0 < confidence < 1.0:
        raise TOIEvaluationError("confidence must be strictly between 0 and 1")
    generator = np.random.default_rng(int(seed))
    blocks = _blocks(label.size, groups)
    estimates: list[float] = []
    for _draw in range(count):
        picked = generator.integers(0, len(blocks), size=len(blocks))
        indexes = np.concatenate([blocks[choice] for choice in picked])
        try:
            estimates.append(
                float(metric(probability[indexes], label[indexes], weight[indexes]))
            )
        except TOIEvaluationError:
            # A resample can be degenerate (all one outcome); skip it rather
            # than pretending the metric exists for that draw.
            continue
    if len(estimates) < max(2, count // 10):
        raise TOIEvaluationError(
            "too few valid bootstrap resamples; the sample is too small or too "
            "imbalanced for a stable interval"
        )
    tail = (1.0 - float(confidence)) / 2.0
    array = np.asarray(estimates, dtype=float)
    return {
        "point": float(metric(probability, label, weight)),
        "lower": float(np.quantile(array, tail)),
        "upper": float(np.quantile(array, 1.0 - tail)),
        "confidence": float(confidence),
        "valid_resamples": len(estimates),
    }


def _blocks(
    count: int, groups: Sequence[Any] | None
) -> tuple[np.ndarray, ...]:
    """Return resampling blocks: whole groups when given, else single rows."""

    if groups is None:
        return tuple(np.array([index]) for index in range(count))
    keys = [str(key) for key in groups]
    if len(keys) != count:
        raise TOIEvaluationError("groups must match the case count")
    ordered: dict[str, list[int]] = {}
    for index, key in enumerate(keys):
        ordered.setdefault(key, []).append(index)
    return tuple(
        np.asarray(indexes) for _key, indexes in sorted(ordered.items())
    )


def bootstrap_brier_difference(
    candidate,
    reference,
    labels,
    weights=None,
    *,
    groups: Sequence[Any] | None = None,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    confidence: float = 0.95,
    seed: int = 20240427,
) -> dict[str, Any]:
    """Grouped paired bootstrap of ``reference_brier - candidate_brier``.

    A positive difference means the candidate improved on the reference.  Both
    forecasts are resampled together on the *same* blocks, so the interval
    describes the paired improvement rather than two independent estimates
    whose intervals happen to overlap.  ``improves`` is only true when the whole
    interval lies above zero, which is the evidence a promotion gate needs; a
    point estimate alone cannot distinguish skill from sampling noise.
    """

    candidate_probability, label, weight = _validated(candidate, labels, weights)
    reference_probability, _label, _weight = _validated(reference, labels, weights)
    if reference_probability.size != candidate_probability.size:
        raise TOIEvaluationError("candidate and reference must have equal length")
    count = int(samples)
    if count < 2:
        raise TOIEvaluationError("bootstrap samples must be at least two")
    if not 0.0 < confidence < 1.0:
        raise TOIEvaluationError("confidence must be strictly between 0 and 1")

    def difference(indexes: np.ndarray) -> float:
        subset_weight = weight[indexes]
        subset_label = label[indexes]
        total = float(np.sum(subset_weight))
        reference_brier = float(
            np.sum(
                subset_weight * (reference_probability[indexes] - subset_label) ** 2
            )
            / total
        )
        candidate_brier = float(
            np.sum(
                subset_weight * (candidate_probability[indexes] - subset_label) ** 2
            )
            / total
        )
        return reference_brier - candidate_brier

    blocks = _blocks(label.size, groups)
    generator = np.random.default_rng(int(seed))
    estimates: list[float] = []
    for _draw in range(count):
        picked = generator.integers(0, len(blocks), size=len(blocks))
        indexes = np.concatenate([blocks[choice] for choice in picked])
        if float(np.sum(weight[indexes])) <= 0:  # pragma: no cover - guarded
            continue
        estimates.append(difference(indexes))
    if len(estimates) < max(2, count // 10):
        raise TOIEvaluationError(
            "too few valid bootstrap resamples for a paired Brier interval"
        )
    tail = (1.0 - float(confidence)) / 2.0
    array = np.asarray(estimates, dtype=float)
    lower = float(np.quantile(array, tail))
    upper = float(np.quantile(array, 1.0 - tail))
    return {
        "point": difference(np.arange(label.size)),
        "lower": lower,
        "upper": upper,
        "confidence": float(confidence),
        "blocks": len(blocks),
        "valid_resamples": len(estimates),
        "improves": bool(lower > 0.0),
    }


def evaluate_probabilities(
    probabilities,
    labels,
    weights=None,
    *,
    threshold: float = DEFAULT_DECISION_THRESHOLD,
    bootstrap_samples: int = 0,
    bootstrap_groups: Sequence[Any] | None = None,
    reliability_edges: Sequence[float] = DEFAULT_RELIABILITY_BINS,
) -> dict[str, Any]:
    """Return the complete metric block for one forecast/outcome pairing."""

    probability, label, weight = _validated(probabilities, labels, weights)
    intercept, slope = calibration_intercept_slope(probability, label, weight)
    report: dict[str, Any] = {
        "cases": int(label.size),
        "base_rate": base_rate(label, weight),
        "mean_forecast": float(np.sum(weight * probability) / np.sum(weight)),
        "brier_score": brier_score(probability, label, weight),
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "reliability_bins": [
            item.to_mapping()
            for item in reliability_bins(
                probability, label, weight, edges=reliability_edges
            )
        ],
        "contingency": contingency_metrics(
            probability, label, weight, threshold=threshold
        ),
    }
    for name, function in (
        ("brier_skill_score", brier_skill_score),
        ("roc_auc", roc_auc),
        ("average_precision", average_precision),
    ):
        try:
            report[name] = float(function(probability, label, weight))
        except TOIEvaluationError as exc:
            report[name] = None
            report.setdefault("undefined_metrics", {})[name] = str(exc)
    if int(bootstrap_samples) > 1:
        intervals: dict[str, Any] = {}
        for name, function in (
            ("brier_score", brier_score),
            ("brier_skill_score", brier_skill_score),
            ("roc_auc", roc_auc),
        ):
            try:
                intervals[name] = bootstrap_interval(
                    function,
                    probability,
                    label,
                    weight,
                    samples=int(bootstrap_samples),
                    groups=bootstrap_groups,
                )
            except TOIEvaluationError as exc:
                intervals[name] = {"error": str(exc)}
        report["bootstrap"] = intervals
    return report


def climatology_probabilities(labels, weights=None) -> np.ndarray:
    """Return the constant climatology reference forecast for a sample."""

    _probability, label, weight = _validated(np.zeros(len(labels)), labels, weights)
    rate = float(np.sum(weight * label) / np.sum(weight))
    return np.full(label.size, rate, dtype=float)


def year_blocks(years: Sequence[int]) -> tuple[int, ...]:
    """Return the sorted unique complete years present in a dataset."""

    unique = sorted({int(year) for year in years})
    if not unique:
        raise TOIEvaluationError("no years available for blocked validation")
    return tuple(unique)


def leave_one_year_out_folds(
    years: Sequence[int],
) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Yield ``(held_out_year, training_years)`` for every complete year."""

    blocks = year_blocks(years)
    if len(blocks) < 2:
        raise TOIEvaluationError(
            "leave-one-year-out validation needs at least two complete years"
        )
    return tuple(
        (year, tuple(other for other in blocks if other != year)) for year in blocks
    )


def expanding_year_folds(
    years: Sequence[int], *, minimum_training_years: int = 2
) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """Yield ``(verification_year, prior_training_years)`` in time order."""

    blocks = year_blocks(years)
    minimum = max(1, int(minimum_training_years))
    if len(blocks) <= minimum:
        raise TOIEvaluationError(
            "expanding-year validation needs more years than the minimum "
            f"training window of {minimum}"
        )
    return tuple(
        (blocks[index], blocks[:index]) for index in range(minimum, len(blocks))
    )


def compare_reports(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize competing forecast systems on the same cases."""

    summary: dict[str, Any] = {}
    for name, report in reports.items():
        summary[name] = {
            "brier_score": report.get("brier_score"),
            "brier_skill_score": report.get("brier_skill_score"),
            "roc_auc": report.get("roc_auc"),
            "calibration_intercept": report.get("calibration_intercept"),
            "calibration_slope": report.get("calibration_slope"),
            "pod": report.get("contingency", {}).get("pod"),
            "far": report.get("contingency", {}).get("far"),
            "csi": report.get("contingency", {}).get("csi"),
            "frequency_bias": report.get("contingency", {}).get("frequency_bias"),
        }
    return summary


__all__ = [
    "DEFAULT_BOOTSTRAP_SAMPLES",
    "DEFAULT_DECISION_THRESHOLD",
    "DEFAULT_RELIABILITY_BINS",
    "ReliabilityBin",
    "TOIEvaluationError",
    "average_precision",
    "base_rate",
    "bootstrap_brier_difference",
    "bootstrap_interval",
    "brier_score",
    "brier_skill_score",
    "calibration_intercept_slope",
    "climatology_probabilities",
    "compare_reports",
    "contingency_metrics",
    "evaluate_probabilities",
    "expanding_year_folds",
    "leave_one_year_out_folds",
    "reliability_bins",
    "roc_auc",
    "strict_json_dumps",
    "year_blocks",
]
