"""Regional Tornado Outbreak Indicator guidance for SHARPpy Reimagined.

TOI is deliberately separate from sounding parameters because it requires
regional, temporal inputs that cannot be recovered from one vertical profile.
A regional workflow may attach a validated :class:`RegionalGuidance` payload
to a portable sounding for the GUI and headless renderer.

The ``toi_dataset``, ``toi_calibration``, ``toi_training``, and
``toi_evaluation`` modules add an offline, reproducible calibration pipeline.
They are imported lazily by the ``sharpmod-guidance`` CLI and are never needed
to render a sounding.
"""

from .hrrr import (
    HRRR_STP_PROXY_VERSION,
    HRRR_TOI_METHOD_VERSION,
    TOI_MINIMUM_COVERAGE_HOURS,
    TOI_SAMPLING_INTERVAL_HOURS,
    TOI_WINDOW_HOURS,
    HrrrRegionalFrame,
    TOITemporalSampling,
    build_hrrr_guidance_from_frames,
    build_live_hrrr_guidance,
    fixed_layer_stp_proxy,
    summarize_toi_sampling,
    toi_sampling_hours,
)
from .schemas import (
    REGIONAL_GUIDANCE_META_KEY,
    REGIONAL_GUIDANCE_SCHEMA_VERSION,
    GuidanceGrid,
    GuidanceState,
    RegionalGuidance,
    TOIFeatures,
    TOIGuidance,
    coerce_regional_guidance,
    guidance_from_collection,
    load_regional_guidance_json,
)
from .toi import (
    DEFAULT_MAX_JET_TRANSLATION_KT,
    JetObject,
    JetTrack,
    extract_toi_features,
)
from .toi_calibration import (
    TOI_CALIBRATION_FEATURE_SCHEMA,
    TOI_CALIBRATION_METHOD_VERSION,
    TOI_TARGET_DEFINITIONS,
    TOICalibrationArtifact,
    TOICalibrationError,
)
from .toi_archive import (
    ArchiveRunner,
    CaseEstimate,
    RunBudget,
    TOIArchiveError,
    archive_source_record,
    audit_local_resources,
    default_case_estimate,
)
from .toi_catalog import CatalogPlan, TOICatalogError, build_case_catalog
from .toi_evaluation import TOIEvaluationError, strict_json_dumps
from .toi_strata import STRATUM_DIMENSIONS, TOIStratumError
from .toi_validation import (
    RECOMMENDED_DEVELOPMENT_YEARS,
    RECOMMENDED_TEST_YEARS,
    TOIPromotionCriteria,
    TOIProspectiveRecord,
    TOIValidationError,
    TOIValidationPlan,
)
from .toi_scorecard import (
    ExperimentalTOIResult,
    TOI_PROBABILITY_VERSION,
    TOI_PUBLIC_METHOD_REFERENCE,
    TOI_SCORECARD_VERSION,
    TOIProbabilityCalibrator,
    compute_experimental_toi,
    experimental_toi_probability,
    experimental_toi_score,
    published_stp_bin_value,
)

__all__ = [
    "RECOMMENDED_DEVELOPMENT_YEARS",
    "RECOMMENDED_TEST_YEARS",
    "REGIONAL_GUIDANCE_META_KEY",
    "REGIONAL_GUIDANCE_SCHEMA_VERSION",
    "STRATUM_DIMENSIONS",
    "ArchiveRunner",
    "CaseEstimate",
    "CatalogPlan",
    "GuidanceGrid",
    "RunBudget",
    "TOIArchiveError",
    "TOICatalogError",
    "archive_source_record",
    "audit_local_resources",
    "build_case_catalog",
    "default_case_estimate",
    "GuidanceState",
    "HRRR_STP_PROXY_VERSION",
    "HRRR_TOI_METHOD_VERSION",
    "HrrrRegionalFrame",
    "ExperimentalTOIResult",
    "JetObject",
    "JetTrack",
    "RegionalGuidance",
    "TOIFeatures",
    "TOIGuidance",
    "TOI_CALIBRATION_FEATURE_SCHEMA",
    "TOI_CALIBRATION_METHOD_VERSION",
    "TOI_MINIMUM_COVERAGE_HOURS",
    "TOI_PROBABILITY_VERSION",
    "TOI_PUBLIC_METHOD_REFERENCE",
    "TOI_SAMPLING_INTERVAL_HOURS",
    "TOI_SCORECARD_VERSION",
    "TOI_TARGET_DEFINITIONS",
    "TOI_WINDOW_HOURS",
    "TOICalibrationArtifact",
    "TOICalibrationError",
    "TOIEvaluationError",
    "TOIProbabilityCalibrator",
    "TOIPromotionCriteria",
    "TOIProspectiveRecord",
    "TOIStratumError",
    "TOITemporalSampling",
    "TOIValidationError",
    "TOIValidationPlan",
    "build_hrrr_guidance_from_frames",
    "build_live_hrrr_guidance",
    "coerce_regional_guidance",
    "compute_experimental_toi",
    "DEFAULT_MAX_JET_TRANSLATION_KT",
    "experimental_toi_probability",
    "experimental_toi_score",
    "extract_toi_features",
    "fixed_layer_stp_proxy",
    "guidance_from_collection",
    "load_regional_guidance_json",
    "published_stp_bin_value",
    "strict_json_dumps",
    "summarize_toi_sampling",
    "toi_sampling_hours",
]
