"""Portable, offline-fitted probability calibration for the experimental TOI.

The public OMEGA Project paper documents TOI's inputs and several numerical
anchors but not its operational probability equation.  :mod:`toi_scorecard`
therefore ships a transparent public-anchor transform.  This module adds the
*other* honest option: a regularized logistic calibrator that can be fitted
offline against a documented historical dataset and then exported as a small
JSON artifact.

Two properties are deliberate:

* **Runtime inference needs only the standard library and NumPy.** The artifact
  stores standardization statistics plus coefficients, so scikit-learn is never
  required to evaluate a calibrated probability.
* **Nothing is trusted by default.** An artifact records whether it was fitted
  on a real multi-year dataset and whether it passed held-out validation. The
  live producer keeps using the public-anchor transform unless a validated
  artifact is selected explicitly.

Neither the score nor the probability is official SPC guidance, and no label
here is an official Risk Impact Value.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .schemas import TOIFeatures
from .toi_scorecard import published_stp_bin_value

TOI_CALIBRATION_SCHEMA_VERSION = 1
TOI_CALIBRATION_METHOD_VERSION = "sharpmod_toi_logistic_l2_v1"
TOI_CALIBRATION_FEATURE_SCHEMA = ("toi_score", "peak_stp_bin")

#: Documented outcome definitions.  ``riv`` is intentionally absent: the
#: official Risk Impact Value weights and event-separation rules are not
#: public, so no artifact here may claim to predict official RIV.
TOI_TARGET_DEFINITIONS = {
    "manifest_label_v1": (
        "Binary outcome supplied verbatim by an external, documented label "
        "manifest; SHARPpy does not reinterpret or recompute it."
    ),
    "high_risk_worthy_proxy_v1": (
        "SHARPpy-defined high-end tornado-day screen derived from NCEI Storm "
        "Events tornado counts, intensities, and path lengths. It is a named "
        "proxy, not the official SPC Risk Impact Value."
    ),
}

_MIN_PROBABILITY = 0.001
_MAX_PROBABILITY = 0.999


class TOICalibrationError(ValueError):
    """Raised when a calibration dataset or artifact is unusable."""


def toi_feature_vector(score: float, maximum_stp: float) -> tuple[float, float]:
    """Return the calibrator's feature vector for one case.

    ``peak_stp_bin`` reuses the operational published-bin helper so the
    offline pipeline and the live producer cannot drift apart.
    """

    numeric_score = float(score)
    numeric_stp = float(maximum_stp)
    if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 5.0:
        raise TOICalibrationError("TOI score must be finite and between 0 and 5")
    if not math.isfinite(numeric_stp) or numeric_stp < 0.0:
        raise TOICalibrationError("maximum_stp must be a non-negative finite value")
    return numeric_score, published_stp_bin_value(numeric_stp)


def _finite_array(values: Any, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        raise TOICalibrationError(f"{name} must not be empty")
    if not np.all(np.isfinite(array)):
        raise TOICalibrationError(f"{name} must be finite")
    return array


def fit_logistic_calibrator(
    features: Any,
    labels: Any,
    *,
    sample_weights: Any = None,
    l2_penalty: float = 1.0,
    maximum_iterations: int = 200,
    tolerance: float = 1e-9,
) -> tuple[float, tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Fit an L2-regularized logistic model with weighted Newton/IRLS steps.

    Returns ``(intercept, coefficients, feature_means, feature_scales)``.
    Features are standardized with weighted statistics so the ridge penalty is
    scale-invariant and the exported coefficients stay small and auditable.
    The intercept is never penalized, which keeps the fitted base rate honest
    for a rare-event target.
    """

    design = _finite_array(features, "features")
    if design.ndim != 2:
        raise TOICalibrationError("features must be a 2-D sample-by-feature array")
    target = _finite_array(labels, "labels").ravel()
    if target.size != design.shape[0]:
        raise TOICalibrationError("features and labels must have matching lengths")
    if not np.all(np.isin(target, (0.0, 1.0))):
        raise TOICalibrationError("labels must be binary 0/1 values")
    if target.min() == target.max():
        raise TOICalibrationError(
            "logistic calibration needs both positive and negative cases"
        )
    penalty = float(l2_penalty)
    if not math.isfinite(penalty) or penalty < 0.0:
        raise TOICalibrationError("l2_penalty must be a non-negative finite value")

    if sample_weights is None:
        weights = np.ones(target.size, dtype=float)
    else:
        weights = _finite_array(sample_weights, "sample_weights").ravel()
        if weights.size != target.size:
            raise TOICalibrationError("sample_weights must match the sample count")
        if np.any(weights < 0) or weights.sum() <= 0:
            raise TOICalibrationError("sample_weights must be non-negative and sum > 0")

    total = weights.sum()
    means = (design * weights[:, None]).sum(axis=0) / total
    centered = design - means
    variance = (centered**2 * weights[:, None]).sum(axis=0) / total
    scales = np.sqrt(variance)
    # A constant predictor carries no information; scale 1 keeps it at zero
    # instead of producing a division warning or an infinite coefficient.
    scales[scales <= 1e-12] = 1.0
    standardized = centered / scales

    augmented = np.column_stack((np.ones(target.size), standardized))
    penalties = np.concatenate(([0.0], np.full(design.shape[1], penalty)))
    beta = np.zeros(augmented.shape[1], dtype=float)
    for _iteration in range(int(maximum_iterations)):
        logits = np.clip(augmented @ beta, -35.0, 35.0)
        probability = 1.0 / (1.0 + np.exp(-logits))
        variance_weights = weights * np.clip(
            probability * (1.0 - probability), 1e-10, None
        )
        gradient = augmented.T @ (weights * (target - probability)) - penalties * beta
        hessian = (
            augmented.T @ (augmented * variance_weights[:, None]) + np.diag(penalties)
        )
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:  # pragma: no cover - guarded by ridge
            raise TOICalibrationError(
                "logistic calibration failed to converge (singular Hessian)"
            ) from exc
        beta = beta + step
        if float(np.max(np.abs(step))) < float(tolerance):
            break
    else:
        raise TOICalibrationError(
            "logistic calibration did not converge within the iteration limit"
        )

    return (
        float(beta[0]),
        tuple(float(value) for value in beta[1:]),
        tuple(float(value) for value in means),
        tuple(float(value) for value in scales),
    )


@dataclass(frozen=True)
class TOICalibrationArtifact:
    """A portable, self-describing TOI probability calibration."""

    calibration_version: str
    intercept: float
    coefficients: tuple[float, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    training_years: tuple[int, ...]
    target_definition: str
    base_rate: float
    data_hash: str
    feature_schema: tuple[str, ...] = TOI_CALIBRATION_FEATURE_SCHEMA
    method_version: str = TOI_CALIBRATION_METHOD_VERSION
    l2_penalty: float = 1.0
    test_years: tuple[int, ...] = ()
    sample_count: int = 0
    event_count: int = 0
    dataset_kind: str = "synthetic-fixture"
    validated: bool = False
    metrics: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""
    created_at: str = ""
    schema_version: int = TOI_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if int(self.schema_version) != TOI_CALIBRATION_SCHEMA_VERSION:
            raise TOICalibrationError(
                f"unsupported calibration schema_version {self.schema_version!r}"
            )
        version = " ".join(str(self.calibration_version).split())
        if not version:
            raise TOICalibrationError("calibration_version must not be empty")
        if self.target_definition not in TOI_TARGET_DEFINITIONS:
            known = ", ".join(sorted(TOI_TARGET_DEFINITIONS))
            raise TOICalibrationError(
                f"target_definition must be one of: {known}; official RIV is "
                "not a supported target because its weights are unpublished"
            )
        schema = tuple(str(name) for name in self.feature_schema)
        if schema != TOI_CALIBRATION_FEATURE_SCHEMA:
            raise TOICalibrationError(
                "feature_schema must be "
                f"{TOI_CALIBRATION_FEATURE_SCHEMA}; got {schema}"
            )
        coefficients = tuple(float(value) for value in self.coefficients)
        means = tuple(float(value) for value in self.feature_means)
        scales = tuple(float(value) for value in self.feature_scales)
        if not (len(coefficients) == len(means) == len(scales) == len(schema)):
            raise TOICalibrationError(
                "coefficients, feature_means, and feature_scales must match the "
                "feature schema length"
            )
        if any(not math.isfinite(value) for value in coefficients + means + scales):
            raise TOICalibrationError("calibration parameters must be finite")
        if any(value == 0.0 for value in scales):
            raise TOICalibrationError("feature_scales must not contain zero")
        intercept = float(self.intercept)
        if not math.isfinite(intercept):
            raise TOICalibrationError("intercept must be finite")
        base_rate = float(self.base_rate)
        if not math.isfinite(base_rate) or not 0.0 < base_rate < 1.0:
            raise TOICalibrationError("base_rate must be strictly between 0 and 1")
        if self.dataset_kind not in {"historical", "synthetic-fixture"}:
            raise TOICalibrationError(
                "dataset_kind must be 'historical' or 'synthetic-fixture'"
            )
        if bool(self.validated) and self.dataset_kind != "historical":
            raise TOICalibrationError(
                "only a historical dataset may produce a validated artifact; "
                "synthetic fixtures validate the pipeline, not the science"
            )
        years = tuple(sorted({int(year) for year in self.training_years}))
        if not years:
            raise TOICalibrationError("training_years must not be empty")
        test_years = tuple(sorted({int(year) for year in self.test_years}))
        if set(years) & set(test_years):
            raise TOICalibrationError("training_years and test_years must not overlap")
        if not str(self.data_hash).strip():
            raise TOICalibrationError("data_hash must not be empty")

        object.__setattr__(self, "calibration_version", version)
        object.__setattr__(self, "intercept", intercept)
        object.__setattr__(self, "coefficients", coefficients)
        object.__setattr__(self, "feature_means", means)
        object.__setattr__(self, "feature_scales", scales)
        object.__setattr__(self, "feature_schema", schema)
        object.__setattr__(self, "training_years", years)
        object.__setattr__(self, "test_years", test_years)
        object.__setattr__(self, "base_rate", base_rate)
        object.__setattr__(self, "l2_penalty", float(self.l2_penalty))
        object.__setattr__(self, "sample_count", int(self.sample_count))
        object.__setattr__(self, "event_count", int(self.event_count))
        object.__setattr__(self, "validated", bool(self.validated))
        object.__setattr__(self, "data_hash", str(self.data_hash).strip())
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "notes", " ".join(str(self.notes).split()))
        object.__setattr__(
            self,
            "created_at",
            str(self.created_at) or datetime.now(UTC).isoformat(timespec="seconds"),
        )
        object.__setattr__(self, "method_version", str(self.method_version))
        object.__setattr__(self, "schema_version", TOI_CALIBRATION_SCHEMA_VERSION)

    @property
    def calibration_years(self) -> str:
        """Return a compact, displayable training-year range."""

        years = self.training_years
        if len(years) == 1:
            return str(years[0])
        contiguous = years == tuple(range(years[0], years[-1] + 1))
        if contiguous:
            return f"{years[0]}-{years[-1]}"
        return ",".join(str(year) for year in years)

    @property
    def target_description(self) -> str:
        return TOI_TARGET_DEFINITIONS[self.target_definition]

    def probability(self, score: float, maximum_stp: float) -> float:
        """Return the calibrated probability; NumPy is the only dependency."""

        vector = toi_feature_vector(score, maximum_stp)
        log_odds = self.intercept
        for value, mean, scale, coefficient in zip(
            vector,
            self.feature_means,
            self.feature_scales,
            self.coefficients,
            strict=True,
        ):
            log_odds += coefficient * (value - mean) / scale
        probability = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, log_odds))))
        return round(min(_MAX_PROBABILITY, max(_MIN_PROBABILITY, probability)), 4)

    def probability_from_features(self, features: TOIFeatures, score: float) -> float:
        if not isinstance(features, TOIFeatures):
            raise TOICalibrationError("features must be a TOIFeatures object")
        return self.probability(score, features.maximum_stp)

    def provenance(self) -> dict[str, str]:
        """Return the fields surfaced in TOI provenance and the details UI."""

        return {
            "toi_calibration_version": self.calibration_version,
            "toi_calibration_method": self.method_version,
            "toi_calibration_years": self.calibration_years,
            "toi_calibration_target": self.target_definition,
            "toi_calibration_base_rate": f"{self.base_rate:.4f}",
            "toi_calibration_dataset": self.dataset_kind,
            "toi_calibration_validated": "yes" if self.validated else "no",
            "toi_calibration_data_hash": self.data_hash[:32],
        }

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "calibration_version": self.calibration_version,
            "method_version": self.method_version,
            "feature_schema": list(self.feature_schema),
            "intercept": self.intercept,
            "coefficients": list(self.coefficients),
            "feature_means": list(self.feature_means),
            "feature_scales": list(self.feature_scales),
            "l2_penalty": self.l2_penalty,
            "training_years": list(self.training_years),
            "test_years": list(self.test_years),
            "target_definition": self.target_definition,
            "target_description": self.target_description,
            "base_rate": self.base_rate,
            "sample_count": self.sample_count,
            "event_count": self.event_count,
            "dataset_kind": self.dataset_kind,
            "validated": self.validated,
            "metrics": dict(self.metrics),
            "data_hash": self.data_hash,
            "notes": self.notes,
            "created_at": self.created_at,
            "experimental_not_official": True,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOICalibrationArtifact:
        if not isinstance(payload, Mapping):
            raise TOICalibrationError("calibration artifact must be a JSON object")
        known = set(cls.__dataclass_fields__)
        supplied = {key: value for key, value in payload.items() if key in known}
        missing = {
            "calibration_version",
            "intercept",
            "coefficients",
            "feature_means",
            "feature_scales",
            "training_years",
            "target_definition",
            "base_rate",
            "data_hash",
        }.difference(supplied)
        if missing:
            raise TOICalibrationError(
                "calibration artifact is missing: " + ", ".join(sorted(missing))
            )
        return cls(**supplied)

    def save(self, path: str | os.PathLike[str]) -> str:
        target = os.path.abspath(os.fspath(path))
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Strict JSON: an undefined metric must be null, never NaN, so the
        # artifact stays readable by any conforming parser.
        try:
            document = json.dumps(
                self.to_mapping(),
                indent=2,
                sort_keys=True,
                allow_nan=False,
                default=str,
            )
        except ValueError as exc:
            raise TOICalibrationError(
                "refusing to write a non-finite calibration artifact; undefined "
                f"metrics must be null rather than NaN ({exc})"
            ) from exc
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.write("\n")
        return target

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TOICalibrationArtifact:
        source = os.path.abspath(os.fspath(path))
        try:
            with open(source, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise TOICalibrationError(
                f"could not read TOI calibration artifact: {exc}"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TOICalibrationError(
                f"invalid TOI calibration artifact JSON: {exc}"
            ) from exc
        return cls.from_mapping(payload)


def calibrated_probabilities(
    artifact: TOICalibrationArtifact,
    scores: Sequence[float],
    peak_stp: Sequence[float],
) -> np.ndarray:
    """Vectorized helper used by the offline evaluation commands."""

    if len(scores) != len(peak_stp):
        raise TOICalibrationError("scores and peak_stp must have matching lengths")
    return np.asarray(
        [
            artifact.probability(score, stp)
            for score, stp in zip(scores, peak_stp, strict=True)
        ],
        dtype=float,
    )


__all__ = [
    "TOI_CALIBRATION_FEATURE_SCHEMA",
    "TOI_CALIBRATION_METHOD_VERSION",
    "TOI_CALIBRATION_SCHEMA_VERSION",
    "TOI_TARGET_DEFINITIONS",
    "TOICalibrationArtifact",
    "TOICalibrationError",
    "calibrated_probabilities",
    "fit_logistic_calibrator",
    "toi_feature_vector",
]
