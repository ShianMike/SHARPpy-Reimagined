"""Transparent experimental TOI scorecard and probability transform.

The public OMEGA Project paper documents the Tornado Outbreak Indicator (TOI)
inputs, categorical bins, qualitative weighting, and several numerical anchors,
but it does not publish the operational score weights or probability equation.
This module therefore implements a SHARPpy-specific reconstruction rather than
claiming official SPC compatibility.

The reconstruction follows the public description:

* jet translation is the primary discriminator below roughly 45 kt;
* jet position relative to the risk centroid matters more for faster jets;
* a maximum jet near 90 kt is favoured;
* July receives a small seasonal penalty; and
* peak STP is used by the probability transform, not the TOI score itself.

The scorecard is anchored so the public 40-kt discriminator is close to TOI 4
for favourable geometry.  The probability transform is anchored to the paper's
27 April 2024 example (TOI 4.35, peak STP in the 8-or-9 bin, probability 87%).
It is not an independently fitted or officially calibrated model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .schemas import TOIFeatures


TOI_SCORECARD_VERSION = "sharpmod_toi_public_bins_experimental_v1"
TOI_PROBABILITY_VERSION = "sharpmod_toi_public_anchor_probability_v1"
TOI_PUBLIC_METHOD_REFERENCE = (
    "https://www.spc.noaa.gov/publications/broyles/omega.pdf"
)

#: Version of the measured-skill statement below, so a reader can tell which
#: evaluation produced it and a later evaluation can supersede it.
TOI_MEASURED_SKILL_VERSION = "sharpmod_toi_measured_skill_2015_2025_v2"

#: MEASURED, not asserted.  Verified archive of 339 cases built from versioned
#: NOAA NCEI Storm Events exports, development years 2015-2022, test years
#: 2023-2025 (105 cases, 18 positive event groups), event-blocked and
#: population-weighted, against the same pre-registered plan and feature schema.
#:
#: v2 supersedes v1.  v1 was measured with two defects in the feature method: a
#: risk anchor that could fall outside the CONUS land domain, and jet-object
#: association bounded in distance but not speed.  Both are fixed, the archive
#: was recollected, and the numbers moved substantially, which is exactly why
#: this statement is versioned.
#:
#: v1 -> v2 on the same held-out years: this transform's Brier skill went from
#: -0.561 to -0.118 and its false-alarm ratio from 0.905 to 0.678, so most of
#: its apparent badness was the defects rather than the transform.  A
#: regularized logistic calibration fitted on the same development years scored
#: -0.010 (v1: -0.013) and still does not beat climatology: grouped-bootstrap
#: improvement -0.0008 with a 95% interval of [-0.0046, 0.0034].  It no longer
#: beats this transform either (+0.0088, interval [-0.0060, 0.0264], where under
#: v1 that margin was significant), so nothing was promoted and this transform
#: remains the default.
#:
#: Stating it plainly is the point: a probability shown to a forecaster that is
#: measurably worse than the base rate must say so where the forecaster looks.
#: Must stay within the provenance schema's per-value text limit.  A warning
#: that gets truncated mid-sentence is worse than no warning, so a test asserts
#: this fits rather than trusting the author to remember.
TOI_MEASURED_SKILL_NOTE = (
    "MEASURED (339-case archive, test 2023-2025): Brier skill -0.118 vs "
    "climatology, FAR 0.678, so measurably WORSE than the base rate. No fitted "
    "alternative beat climatology either. An experimental score, NOT a "
    "calibrated probability."
)

MILES_PER_KILOMETRE = 0.621371192237334

# Public anchor: score 4.35 and STP category 8-or-9 map to 87%, while a
# score of 4 and representative STP of 5 map to 50%.  Keeping the coefficients
# explicit makes the non-official reconstruction easy to audit and replace.
_PROBABILITY_SCORE_COEFFICIENT = 2.0
_PROBABILITY_STP_COEFFICIENT = 0.3431310744167206
_MIN_PROBABILITY = 0.01
_MAX_PROBABILITY = 0.99


@runtime_checkable
class TOIProbabilityCalibrator(Protocol):
    """Seam for an explicitly selected offline TOI probability calibration."""

    calibration_version: str

    def probability(self, score: float, maximum_stp: float) -> float: ...


@dataclass(frozen=True)
class ExperimentalTOIResult:
    """One explainable result from the public-bin experimental reconstruction."""

    score: float
    high_risk_probability: float
    calibration_version: str
    translation_component: float
    location_component: float
    maximum_jet_component: float
    translation_weight: float
    location_weight: float
    maximum_jet_weight: float
    stp_bin_value: float
    seasonal_adjustment: float


def _bounded(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, float(value)))


def _translation_component(speed_kt: float) -> float:
    """Map translation speed onto the published 0--5 TOI scale.

    Ten knots per score unit places the published 40-kt discriminator at a
    component value of 4 and caps very fast translation at 5.
    """

    return _bounded(float(speed_kt) / 10.0, 0.0, 5.0)


def _maximum_jet_component(speed_kt: float) -> float:
    """Return a rating for the six maximum-jet bins shown in the paper."""

    speed = float(speed_kt)
    if speed < 55.0:
        return 1.5
    if speed < 65.0:
        return 2.5
    if speed < 75.0:
        return 3.5
    if speed < 85.0:
        return 4.25
    if speed < 95.0:
        return 5.0
    return 4.25


def _total_distance_component(distance_miles: float) -> float:
    """Rate the nine published jet-to-centroid total-distance bins."""

    distance = max(0.0, float(distance_miles))
    if distance <= 50.0:
        return 5.0
    if distance <= 100.0:
        return 4.75
    if distance <= 150.0:
        return 4.5
    if distance <= 200.0:
        return 4.0
    if distance <= 250.0:
        return 3.4
    if distance <= 300.0:
        return 2.7
    if distance <= 350.0:
        return 2.0
    if distance <= 400.0:
        return 1.2
    return 0.5


def _direction_component(bearing_deg: float) -> float:
    """Rate the direction sectors shown by the published TOI calculator."""

    bearing = float(bearing_deg) % 360.0
    if bearing >= 350.0 or bearing < 10.0:
        return 3.5  # N
    if bearing < 30.0:
        return 2.5  # NNE
    if bearing < 60.0:
        return 1.5  # NE
    if bearing < 80.0:
        return 0.75  # ENE
    if bearing < 100.0:
        return 0.5  # E
    if bearing < 170.0:
        return 0.0  # ESE through SSE were not favoured/publicly tabulated
    if bearing < 190.0:
        return 0.0  # S
    if bearing < 210.0:
        return 0.5  # SSW
    if bearing < 240.0:
        return 1.5  # SW
    if bearing < 260.0:
        return 2.5  # WSW
    if bearing < 280.0:
        return 3.25  # W
    if bearing < 300.0:
        return 4.0  # WNW
    if bearing < 330.0:
        return 5.0  # NW; the paper identifies about 325 degrees as preferred
    return 4.25  # NNW


def _east_west_component(distance_miles: float, bearing_deg: float) -> float:
    """Rate the calculator's signed east/west-distance bins.

    Negative displacement is west of the risk centroid and positive is east.
    """

    displacement = float(distance_miles) * math.sin(math.radians(bearing_deg))
    if displacement < -200.0:
        return 3.5
    if displacement < -160.0:
        return 4.5
    if displacement < -120.0:
        return 5.0
    if displacement < -80.0:
        return 5.0
    if displacement < -40.0:
        return 4.5
    if displacement < 0.0:
        return 3.5
    if displacement <= 40.0:
        return 2.5
    if displacement <= 80.0:
        return 1.5
    if displacement <= 120.0:
        return 0.75
    if displacement <= 160.0:
        return 0.25
    return 0.0


def _location_component(features: TOIFeatures) -> float:
    distance_miles = features.jet_to_risk_distance_km * MILES_PER_KILOMETRE
    return (
        _total_distance_component(distance_miles)
        + _direction_component(features.jet_to_risk_bearing_deg)
        + _east_west_component(distance_miles, features.jet_to_risk_bearing_deg)
    ) / 3.0


def _score_weights(translation_speed_kt: float) -> tuple[float, float, float]:
    """Return translation, location, and maximum-jet weights.

    Below 45 kt, location contributes progressively less as translation slows.
    At 45 kt and above, the published description says direction and distance
    become the main conditional discriminators, so location receives 40%.
    """

    speed = float(translation_speed_kt)
    if speed < 45.0:
        location = 0.10 * _bounded((speed - 30.0) / 14.0, 0.0, 1.0)
        maximum_jet = 0.15
        translation = 1.0 - location - maximum_jet
        return translation, location, maximum_jet
    return 0.45, 0.40, 0.15


def published_stp_bin_value(maximum_stp: float) -> float:
    """Return a representative value for the paper's peak-STP categories."""

    rounded = int(math.floor(max(0.0, float(maximum_stp)) + 0.5))
    if rounded < 2:
        return 1.0
    if rounded == 2:
        return 2.0
    if rounded == 3:
        return 3.0
    if rounded <= 5:
        return 4.5
    if rounded <= 7:
        return 6.5
    if rounded <= 9:
        return 8.5
    if rounded <= 11:
        return 10.5
    return 12.0


def experimental_toi_score(features: TOIFeatures) -> float:
    """Compute the versioned, non-official TOI score."""

    if not isinstance(features, TOIFeatures):
        raise TypeError("features must be a TOIFeatures object")
    translation = _translation_component(features.translation_speed_kt)
    location = _location_component(features)
    maximum_jet = _maximum_jet_component(features.maximum_jet_speed_kt)
    translation_weight, location_weight, maximum_jet_weight = _score_weights(
        features.translation_speed_kt
    )
    seasonal_adjustment = -0.25 if features.month == 7 else 0.0
    score = (
        translation_weight * translation
        + location_weight * location
        + maximum_jet_weight * maximum_jet
        + seasonal_adjustment
    )
    return round(_bounded(score, 0.0, 5.0), 2)


def experimental_toi_probability(score: float, maximum_stp: float) -> float:
    """Map the experimental score and public STP category to a probability.

    This is a versioned anchor transform, not the unpublished SPC calibration.
    """

    numeric_score = float(score)
    numeric_stp = float(maximum_stp)
    if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 5.0:
        raise ValueError("experimental TOI score must be finite and between 0 and 5")
    if not math.isfinite(numeric_stp) or numeric_stp < 0.0:
        raise ValueError("maximum_stp must be a non-negative finite value")
    stp_bin = published_stp_bin_value(numeric_stp)
    log_odds = _PROBABILITY_SCORE_COEFFICIENT * (
        numeric_score - 4.0
    ) + _PROBABILITY_STP_COEFFICIENT * (stp_bin - 5.0)
    probability = 1.0 / (1.0 + math.exp(-log_odds))
    return round(_bounded(probability, _MIN_PROBABILITY, _MAX_PROBABILITY), 4)


def compute_experimental_toi(
    features: TOIFeatures, *, calibrator: TOIProbabilityCalibrator | None = None
) -> ExperimentalTOIResult:
    """Compute an explainable experimental TOI score and probability.

    ``calibrator`` optionally replaces the public-anchor probability transform
    with an offline-fitted artifact.  The default stays the shipped transform:
    a calibrator has to be selected explicitly, and the artifact records
    whether it ever passed held-out historical validation.
    """

    if not isinstance(features, TOIFeatures):
        raise TypeError("features must be a TOIFeatures object")
    translation = _translation_component(features.translation_speed_kt)
    location = _location_component(features)
    maximum_jet = _maximum_jet_component(features.maximum_jet_speed_kt)
    weights = _score_weights(features.translation_speed_kt)
    seasonal_adjustment = -0.25 if features.month == 7 else 0.0
    score = round(
        _bounded(
            weights[0] * translation
            + weights[1] * location
            + weights[2] * maximum_jet
            + seasonal_adjustment,
            0.0,
            5.0,
        ),
        2,
    )
    stp_bin = published_stp_bin_value(features.maximum_stp)
    if calibrator is None:
        probability = experimental_toi_probability(score, features.maximum_stp)
        calibration_version = TOI_PROBABILITY_VERSION
    else:
        probability = float(calibrator.probability(score, features.maximum_stp))
        if not 0.0 <= probability <= 1.0:
            raise ValueError("calibrator returned a probability outside 0-1")
        calibration_version = str(calibrator.calibration_version)
    return ExperimentalTOIResult(
        score=score,
        high_risk_probability=probability,
        calibration_version=calibration_version,
        translation_component=round(translation, 4),
        location_component=round(location, 4),
        maximum_jet_component=round(maximum_jet, 4),
        translation_weight=round(weights[0], 4),
        location_weight=round(weights[1], 4),
        maximum_jet_weight=round(weights[2], 4),
        stp_bin_value=stp_bin,
        seasonal_adjustment=seasonal_adjustment,
    )


__all__ = [
    "ExperimentalTOIResult",
    "TOIProbabilityCalibrator",
    "TOI_PROBABILITY_VERSION",
    "TOI_MEASURED_SKILL_NOTE",
    "TOI_MEASURED_SKILL_VERSION",
    "TOI_PUBLIC_METHOD_REFERENCE",
    "TOI_SCORECARD_VERSION",
    "compute_experimental_toi",
    "experimental_toi_probability",
    "experimental_toi_score",
    "published_stp_bin_value",
]
