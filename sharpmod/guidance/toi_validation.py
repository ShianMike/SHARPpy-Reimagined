"""Promotion criteria and pre-registration for the experimental TOI calibrator.

The first version of this pipeline promoted an artifact on three complete years,
thirty events, and two point-estimate comparisons.  That is a *pipeline smoke
gate*: it proves the machinery runs, and almost nothing about the science.  A
rare-event calibrator can clear it on a handful of influential outbreaks.

This module separates the two ideas explicitly:

``TOIPromotionCriteria``
    A configurable, serializable gate.  ``pipeline_smoke()`` is marked
    ``scientific=False`` and can never promote anything.  ``research_target()``
    encodes the real bar: roughly 8-10 archived years, hundreds of independent
    event groups, dozens of positive cases, per-fold minimum positive and
    negative counts, grouped-bootstrap improvement over both references, and a
    prospective shadow season.

``TOIValidationPlan``
    A frozen pre-registration.  The target definition, case-selection rules,
    feature schema, development and test years, and criteria are hashed *before*
    anyone looks at held-out results.  Training refuses to award validated
    status without a plan, and refuses it if the realized split disagrees with
    the frozen one.  This is what stops the split from quietly moving until the
    test numbers look good.

``TOIProspectiveRecord``
    A real forward-looking evaluation over a season that did not exist when the
    model was fitted.  Nothing in this repository can synthesize one, which is
    deliberate: it means today's honest answer is always "not validated".

Nothing here makes TOI official SPC guidance, and no gate can be satisfied by
synthetic fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .toi_calibration import TOI_CALIBRATION_FEATURE_SCHEMA, TOI_TARGET_DEFINITIONS
from .toi_evaluation import strict_json_dumps
from .toi_strata import STRATUM_DIMENSIONS

TOI_PROMOTION_CRITERIA_VERSION = "sharpmod_toi_promotion_criteria_v1"
TOI_VALIDATION_PLAN_VERSION = "sharpmod_toi_validation_plan_v1"

#: The chronological development window recommended for HRRR-era TOI work.
#: HRRR archives on NOAA Open Data begin in 2014, so an 8-year development
#: window plus a 3-year untouched test period is about the most a current
#: archive supports.
RECOMMENDED_DEVELOPMENT_YEARS = tuple(range(2015, 2023))
RECOMMENDED_TEST_YEARS = (2023, 2024, 2025)


class TOIValidationError(ValueError):
    """Raised when promotion criteria or a validation plan are unusable."""


def _positive_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise TOIValidationError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TOIValidationError(f"{name} must be an integer") from exc
    if number < minimum:
        raise TOIValidationError(f"{name} must be >= {minimum}")
    return number


def _years(value: Any, name: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise TOIValidationError(f"{name} must be a sequence of years")
    try:
        years = tuple(sorted({int(item) for item in value}))
    except (TypeError, ValueError) as exc:
        raise TOIValidationError(f"{name} must contain integer years") from exc
    return years


@dataclass(frozen=True)
class TOIPromotionCriteria:
    """A configurable, serializable gate for promoting a TOI calibration.

    Defaults are the research target, not a convenience setting.  Every
    threshold is explicit so a reviewer can see exactly what was required, and
    so loosening a gate is a visible, hashed change to a frozen plan rather than
    an untracked edit.
    """

    criteria_version: str = TOI_PROMOTION_CRITERIA_VERSION
    #: ``False`` marks a gate that exists only to exercise the pipeline. Such a
    #: gate can never promote, regardless of the metrics it sees.
    scientific: bool = True

    # --- sample size ---------------------------------------------------- #
    minimum_development_years: int = 8
    minimum_test_years: int = 3
    minimum_event_groups: int = 200
    minimum_positive_events: int = 30
    minimum_negative_events: int = 100

    # --- per-fold and per-test-set floors ------------------------------- #
    minimum_fold_positive_events: int = 3
    minimum_fold_negative_events: int = 10
    minimum_test_positive_events: int = 10
    minimum_test_negative_events: int = 40
    require_every_fold_evaluated: bool = True

    # --- chronology ----------------------------------------------------- #
    require_chronological_test_period: bool = True

    # --- uncertainty ---------------------------------------------------- #
    bootstrap_samples: int = 1000
    bootstrap_confidence: float = 0.95
    require_bootstrap_improvement_over_climatology: bool = True
    require_bootstrap_improvement_over_anchor: bool = True

    # --- stratified behaviour ------------------------------------------- #
    require_stratified_reporting: bool = True
    minimum_stratum_cases: int = 25
    minimum_stratum_brier_skill_score: float = -0.05

    # --- pre-registration and prospective use --------------------------- #
    require_frozen_plan: bool = True
    require_prospective_evaluation: bool = True
    minimum_prospective_positive_events: int = 5
    minimum_prospective_event_groups: int = 40

    def __post_init__(self) -> None:
        version = " ".join(str(self.criteria_version).split())
        if not version:
            raise TOIValidationError("criteria_version must not be empty")
        object.__setattr__(self, "criteria_version", version)
        for name in (
            "minimum_development_years",
            "minimum_test_years",
            "minimum_event_groups",
            "minimum_positive_events",
            "minimum_negative_events",
            "minimum_fold_positive_events",
            "minimum_fold_negative_events",
            "minimum_test_positive_events",
            "minimum_test_negative_events",
            "minimum_stratum_cases",
            "minimum_prospective_positive_events",
            "minimum_prospective_event_groups",
        ):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        object.__setattr__(
            self,
            "bootstrap_samples",
            _positive_int(self.bootstrap_samples, "bootstrap_samples", minimum=2),
        )
        confidence = float(self.bootstrap_confidence)
        if not 0.0 < confidence < 1.0:
            raise TOIValidationError(
                "bootstrap_confidence must be strictly between 0 and 1"
            )
        object.__setattr__(self, "bootstrap_confidence", confidence)
        skill = float(self.minimum_stratum_brier_skill_score)
        if skill > 1.0:
            raise TOIValidationError(
                "minimum_stratum_brier_skill_score cannot exceed 1"
            )
        object.__setattr__(self, "minimum_stratum_brier_skill_score", skill)
        for name in (
            "scientific",
            "require_every_fold_evaluated",
            "require_chronological_test_period",
            "require_bootstrap_improvement_over_climatology",
            "require_bootstrap_improvement_over_anchor",
            "require_stratified_reporting",
            "require_frozen_plan",
            "require_prospective_evaluation",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TOIValidationError(f"{name} must be boolean")
        if self.minimum_positive_events > self.minimum_event_groups:
            raise TOIValidationError(
                "minimum_positive_events cannot exceed minimum_event_groups"
            )

    @classmethod
    def research_target(cls, **overrides: Any) -> TOIPromotionCriteria:
        """The scientific bar: multi-year archive plus prospective evidence."""

        return cls(criteria_version="toi_research_target_v1", **overrides)

    @classmethod
    def pipeline_smoke(cls, **overrides: Any) -> TOIPromotionCriteria:
        """A deliberately weak gate that exercises the pipeline only.

        ``scientific`` is ``False``, so this gate always blocks promotion. It
        exists so tests and fixtures can drive the full code path without ever
        being able to certify a calibration.
        """

        defaults: dict[str, Any] = {
            "criteria_version": "toi_pipeline_smoke_v1",
            "scientific": False,
            "minimum_development_years": 2,
            "minimum_test_years": 1,
            "minimum_event_groups": 4,
            "minimum_positive_events": 1,
            "minimum_negative_events": 1,
            "minimum_fold_positive_events": 0,
            "minimum_fold_negative_events": 0,
            "minimum_test_positive_events": 0,
            "minimum_test_negative_events": 0,
            "require_every_fold_evaluated": False,
            "bootstrap_samples": 64,
            "require_bootstrap_improvement_over_climatology": False,
            "require_bootstrap_improvement_over_anchor": False,
            "require_stratified_reporting": False,
            "minimum_stratum_cases": 5,
            "require_frozen_plan": False,
            "require_prospective_evaluation": False,
            "minimum_prospective_positive_events": 0,
            "minimum_prospective_event_groups": 0,
        }
        defaults.update(overrides)
        return cls(**defaults)

    def to_mapping(self) -> dict[str, Any]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOIPromotionCriteria:
        if not isinstance(payload, Mapping):
            raise TOIValidationError("promotion criteria must be an object")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload).difference(known))
        if unknown:
            raise TOIValidationError(
                "unknown promotion criteria field(s): " + ", ".join(unknown)
            )
        return cls(**{key: value for key, value in payload.items()})


@dataclass(frozen=True)
class TOIProspectiveRecord:
    """One prospective shadow-validation season, evaluated after freezing.

    A prospective record must describe cases the fitted model never saw and
    that did not exist when it was frozen.  It is supplied from outside; the
    pipeline cannot manufacture one.
    """

    season_label: str
    start_date: str
    end_date: str
    artifact_calibration_version: str
    plan_hash: str
    event_groups: int
    positive_events: int
    cases: int
    metrics: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("season_label", "start_date", "end_date", "plan_hash"):
            text = " ".join(str(getattr(self, name)).split())
            if not text:
                raise TOIValidationError(f"prospective {name} must not be empty")
            object.__setattr__(self, name, text)
        for name in ("event_groups", "positive_events", "cases"):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        if self.positive_events > self.event_groups:
            raise TOIValidationError(
                "prospective positive_events cannot exceed event_groups"
            )
        object.__setattr__(
            self,
            "artifact_calibration_version",
            " ".join(str(self.artifact_calibration_version).split()),
        )
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "notes", " ".join(str(self.notes).split()))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "season_label": self.season_label,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "artifact_calibration_version": self.artifact_calibration_version,
            "plan_hash": self.plan_hash,
            "event_groups": self.event_groups,
            "positive_events": self.positive_events,
            "cases": self.cases,
            "metrics": dict(self.metrics),
            "notes": self.notes,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOIProspectiveRecord:
        if not isinstance(payload, Mapping):
            raise TOIValidationError("prospective record must be an object")
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in known})

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TOIProspectiveRecord:
        source = os.path.abspath(os.fspath(path))
        try:
            with open(source, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise TOIValidationError(
                f"could not read prospective record: {exc}"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TOIValidationError(
                f"invalid prospective record JSON: {exc}"
            ) from exc
        return cls.from_mapping(payload)


@dataclass(frozen=True)
class TOIValidationPlan:
    """A frozen pre-registration of everything that must precede test results."""

    plan_version: str
    target_definition: str
    case_selection_rules: str
    development_years: tuple[int, ...]
    test_years: tuple[int, ...]
    prospective_season: str
    criteria: TOIPromotionCriteria = field(default_factory=TOIPromotionCriteria)
    feature_schema: tuple[str, ...] = TOI_CALIBRATION_FEATURE_SCHEMA
    weighting: str = "population"
    l2_penalty: float = 1.0
    validation_scheme: str = "expanding-year"
    notes: str = ""
    frozen_at: str = ""
    schema_version: str = TOI_VALIDATION_PLAN_VERSION

    def __post_init__(self) -> None:
        if self.target_definition not in TOI_TARGET_DEFINITIONS:
            known = ", ".join(sorted(TOI_TARGET_DEFINITIONS))
            raise TOIValidationError(f"target_definition must be one of: {known}")
        rules = " ".join(str(self.case_selection_rules).split())
        if len(rules) < 20:
            raise TOIValidationError(
                "case_selection_rules must describe how cases were chosen; a "
                "frozen plan with no stated selection rule cannot be audited"
            )
        development = _years(self.development_years, "development_years")
        test = _years(self.test_years, "test_years")
        if not development:
            raise TOIValidationError("development_years must not be empty")
        if not test:
            raise TOIValidationError("test_years must not be empty")
        overlap = set(development) & set(test)
        if overlap:
            raise TOIValidationError(
                "development_years and test_years must not overlap: "
                + ",".join(str(year) for year in sorted(overlap))
            )
        if min(test) <= max(development):
            raise TOIValidationError(
                "the test period must be strictly later than every development "
                "year; a non-chronological split leaks future information"
            )
        if not isinstance(self.criteria, TOIPromotionCriteria):
            raise TOIValidationError("criteria must be a TOIPromotionCriteria")
        schema = tuple(str(name) for name in self.feature_schema)
        if schema != TOI_CALIBRATION_FEATURE_SCHEMA:
            raise TOIValidationError(
                f"feature_schema must be {TOI_CALIBRATION_FEATURE_SCHEMA}"
            )
        season = " ".join(str(self.prospective_season).split())
        if not season:
            raise TOIValidationError(
                "prospective_season must name the future season reserved for "
                "shadow validation"
            )
        if self.weighting not in {"natural", "population"}:
            raise TOIValidationError("weighting must be 'natural' or 'population'")
        object.__setattr__(self, "case_selection_rules", rules)
        object.__setattr__(self, "development_years", development)
        object.__setattr__(self, "test_years", test)
        object.__setattr__(self, "feature_schema", schema)
        object.__setattr__(self, "prospective_season", season)
        object.__setattr__(self, "l2_penalty", float(self.l2_penalty))
        object.__setattr__(self, "notes", " ".join(str(self.notes).split()))
        object.__setattr__(
            self, "plan_version", " ".join(str(self.plan_version).split())
        )
        if not self.plan_version:
            raise TOIValidationError("plan_version must not be empty")
        object.__setattr__(
            self,
            "frozen_at",
            str(self.frozen_at) or datetime.now(UTC).isoformat(timespec="seconds"),
        )

    @classmethod
    def recommended(cls, **overrides: Any) -> TOIValidationPlan:
        """The recommended HRRR-era plan: 2015-2022 development, 2023-2025 test."""

        defaults: dict[str, Any] = {
            "plan_version": "toi_hrrr_2015_2022_v1",
            "target_definition": "high_risk_worthy_proxy_v1",
            "case_selection_rules": (
                "Outbreak, ordinary severe, and null/control CONUS dates drawn "
                "from an NCEI Storm Events export, anchored only on information "
                "available at the model cycle, with explicit sampling weights "
                "restoring the documented population base rate."
            ),
            "development_years": RECOMMENDED_DEVELOPMENT_YEARS,
            "test_years": RECOMMENDED_TEST_YEARS,
            "prospective_season": "next full spring severe-weather season",
            "criteria": TOIPromotionCriteria.research_target(),
        }
        defaults.update(overrides)
        return cls(**defaults)

    def content(self) -> dict[str, Any]:
        """Return the hashable pre-registration content, excluding timestamps."""

        return {
            "plan_version": self.plan_version,
            "schema_version": self.schema_version,
            "target_definition": self.target_definition,
            "case_selection_rules": self.case_selection_rules,
            "feature_schema": list(self.feature_schema),
            "development_years": list(self.development_years),
            "test_years": list(self.test_years),
            "prospective_season": self.prospective_season,
            "weighting": self.weighting,
            "l2_penalty": self.l2_penalty,
            "validation_scheme": self.validation_scheme,
            "criteria": self.criteria.to_mapping(),
        }

    def plan_hash(self) -> str:
        """Hash the frozen content so an artifact can prove its provenance."""

        serialized = json.dumps(
            self.content(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def to_mapping(self) -> dict[str, Any]:
        payload = self.content()
        payload.update(
            {
                "frozen_at": self.frozen_at,
                "notes": self.notes,
                "plan_hash": self.plan_hash(),
                "experimental_not_official": True,
            }
        )
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOIValidationPlan:
        if not isinstance(payload, Mapping):
            raise TOIValidationError("validation plan must be an object")
        criteria_payload = payload.get("criteria")
        criteria = (
            TOIPromotionCriteria.from_mapping(criteria_payload)
            if isinstance(criteria_payload, Mapping)
            else TOIPromotionCriteria()
        )
        plan = cls(
            plan_version=payload.get("plan_version", ""),
            target_definition=str(payload.get("target_definition", "")),
            case_selection_rules=payload.get("case_selection_rules", ""),
            development_years=payload.get("development_years") or (),
            test_years=payload.get("test_years") or (),
            prospective_season=payload.get("prospective_season", ""),
            criteria=criteria,
            feature_schema=tuple(
                payload.get("feature_schema") or TOI_CALIBRATION_FEATURE_SCHEMA
            ),
            weighting=payload.get("weighting", "population"),
            l2_penalty=payload.get("l2_penalty", 1.0),
            validation_scheme=payload.get("validation_scheme", "expanding-year"),
            notes=payload.get("notes", ""),
            frozen_at=payload.get("frozen_at", ""),
        )
        stored = str(payload.get("plan_hash", ""))
        if stored and stored != plan.plan_hash():
            raise TOIValidationError(
                "validation plan hash does not match its content; the frozen "
                "pre-registration was edited after freezing"
            )
        return plan

    def save(self, path: str | os.PathLike[str]) -> str:
        target = os.path.abspath(os.fspath(path))
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(strict_json_dumps(self.to_mapping(), indent=2))
            handle.write("\n")
        return target

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TOIValidationPlan:
        source = os.path.abspath(os.fspath(path))
        try:
            with open(source, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise TOIValidationError(
                f"could not read validation plan: {exc}"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TOIValidationError(f"invalid validation plan JSON: {exc}") from exc
        return cls.from_mapping(payload)


def _moment(value: Any) -> datetime | None:
    """Parse an ISO-8601 date or timestamp, returning ``None`` if unusable.

    Used only for the prospective ordering check.  An unparseable value is
    reported as its own blocker rather than silently treated as acceptable.
    """

    text = " ".join(str(value or "").split())
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _event_counts(rows: Sequence[Any]) -> tuple[int, int]:
    """Return ``(positive_events, negative_events)`` counted by event id."""

    positive: set[str] = set()
    negative: set[str] = set()
    for row in rows:
        (positive if row.label == 1 else negative).add(row.event_id)
    # An event with any positive case is a positive event group.
    return len(positive), len(negative - positive)


def _bootstrap_blocker(
    label: str, interval: Mapping[str, Any] | None, criteria: TOIPromotionCriteria
) -> str | None:
    if not isinstance(interval, Mapping):
        return (
            f"no grouped bootstrap interval for Brier improvement over {label}; "
            "a point-estimate improvement is not evidence of skill"
        )
    if "error" in interval:
        return f"bootstrap interval over {label} unavailable: {interval['error']}"
    lower = interval.get("lower")
    point = interval.get("point")
    if lower is None or not interval.get("improves", False):
        return (
            f"grouped bootstrap does not show Brier improvement over {label} at "
            f"{criteria.bootstrap_confidence:.0%} confidence "
            f"(point {point!r}, lower bound {lower!r} is not above zero)"
        )
    return None


def evaluate_promotion(
    *,
    criteria: TOIPromotionCriteria,
    dataset_kind: str,
    development_years: Sequence[int],
    test_years: Sequence[int],
    development_rows: Sequence[Any],
    test_rows: Sequence[Any],
    fold_reports: Sequence[Mapping[str, Any]] = (),
    bootstrap: Mapping[str, Any] | None = None,
    stratified: Mapping[str, Any] | None = None,
    plan: TOIValidationPlan | None = None,
    prospective: TOIProspectiveRecord | None = None,
) -> dict[str, Any]:
    """Return the promotion decision plus every blocker, in one auditable place."""

    if not isinstance(criteria, TOIPromotionCriteria):
        raise TOIValidationError("criteria must be a TOIPromotionCriteria")
    blockers: list[str] = []
    development = tuple(sorted({int(year) for year in development_years}))
    held_out = tuple(sorted({int(year) for year in test_years}))

    if not criteria.scientific:
        blockers.append(
            f"promotion criteria {criteria.criteria_version!r} are marked "
            "non-scientific (pipeline smoke gate) and can never certify a "
            "calibration"
        )
    if dataset_kind != "historical":
        blockers.append(
            "dataset is declared a synthetic fixture, which validates the "
            "pipeline but not the science"
        )

    # --- pre-registration ---------------------------------------------- #
    if criteria.require_frozen_plan and plan is None:
        blockers.append(
            "no frozen validation plan was supplied; the target, case-selection "
            "rules, feature schema, split years, and criteria must be frozen "
            "before held-out results are examined"
        )
    if plan is not None:
        if tuple(plan.development_years) != development:
            blockers.append(
                "realized development years "
                f"{','.join(map(str, development))} do not match the frozen plan "
                f"({','.join(map(str, plan.development_years))})"
            )
        if tuple(plan.test_years) != held_out:
            blockers.append(
                "realized test years "
                f"{','.join(map(str, held_out))} do not match the frozen plan "
                f"({','.join(map(str, plan.test_years))})"
            )
        if plan.criteria.to_mapping() != criteria.to_mapping():
            blockers.append(
                "the criteria used differ from the frozen plan's criteria; "
                "loosening a gate after freezing invalidates the test period"
            )

    # --- sample size ---------------------------------------------------- #
    if len(development) < criteria.minimum_development_years:
        blockers.append(
            f"only {len(development)} development year(s); at least "
            f"{criteria.minimum_development_years} are required"
        )
    if len(held_out) < criteria.minimum_test_years:
        blockers.append(
            f"only {len(held_out)} untouched test year(s); at least "
            f"{criteria.minimum_test_years} are required"
        )
    if (
        criteria.require_chronological_test_period
        and development
        and held_out
        and min(held_out) <= max(development)
    ):
        blockers.append(
            "the test period is not strictly later than every development "
            f"year (test starts {min(held_out)}, development ends "
            f"{max(development)})"
        )

    all_rows = tuple(development_rows) + tuple(test_rows)
    event_groups = len({row.event_id for row in all_rows})
    positive_events, negative_events = _event_counts(all_rows)
    if event_groups < criteria.minimum_event_groups:
        blockers.append(
            f"only {event_groups} independent event group(s); at least "
            f"{criteria.minimum_event_groups} are required"
        )
    if positive_events < criteria.minimum_positive_events:
        blockers.append(
            f"only {positive_events} positive event group(s); at least "
            f"{criteria.minimum_positive_events} are required. A nominally "
            "large dataset with few positives cannot support a rare-event "
            "calibration"
        )
    if negative_events < criteria.minimum_negative_events:
        blockers.append(
            f"only {negative_events} negative event group(s); at least "
            f"{criteria.minimum_negative_events} are required"
        )

    # --- per-fold floors ------------------------------------------------ #
    evaluated = [fold for fold in fold_reports if "error" not in fold]
    if criteria.require_every_fold_evaluated:
        failed = [
            str(fold.get("verification_year"))
            for fold in fold_reports
            if "error" in fold
        ]
        if failed:
            blockers.append(
                "cross-validation fold(s) could not be evaluated: "
                + ",".join(failed)
            )
    if not evaluated:
        blockers.append("no cross-validation fold produced out-of-sample forecasts")
    for fold in evaluated:
        year = fold.get("verification_year")
        fold_positive = int(fold.get("positive_events", 0))
        fold_negative = int(fold.get("negative_events", 0))
        if fold_positive < criteria.minimum_fold_positive_events:
            blockers.append(
                f"fold {year} has only {fold_positive} positive event group(s); "
                f"at least {criteria.minimum_fold_positive_events} are required"
            )
        if fold_negative < criteria.minimum_fold_negative_events:
            blockers.append(
                f"fold {year} has only {fold_negative} negative event group(s); "
                f"at least {criteria.minimum_fold_negative_events} are required"
            )

    # --- untouched test period ----------------------------------------- #
    if not test_rows:
        blockers.append("no untouched test period was held out")
    else:
        test_positive, test_negative = _event_counts(test_rows)
        if test_positive < criteria.minimum_test_positive_events:
            blockers.append(
                f"the test period has only {test_positive} positive event "
                f"group(s); at least {criteria.minimum_test_positive_events} "
                "are required"
            )
        if test_negative < criteria.minimum_test_negative_events:
            blockers.append(
                f"the test period has only {test_negative} negative event "
                f"group(s); at least {criteria.minimum_test_negative_events} "
                "are required"
            )

    # --- uncertainty ---------------------------------------------------- #
    intervals = bootstrap or {}
    if criteria.require_bootstrap_improvement_over_climatology:
        blocker = _bootstrap_blocker(
            "climatology", intervals.get("climatology"), criteria
        )
        if blocker:
            blockers.append(blocker)
    if criteria.require_bootstrap_improvement_over_anchor:
        blocker = _bootstrap_blocker(
            "the public-anchor transform",
            intervals.get("public_anchor_transform"),
            criteria,
        )
        if blocker:
            blockers.append(blocker)

    # --- stratified behaviour ------------------------------------------- #
    if criteria.require_stratified_reporting:
        if not isinstance(stratified, Mapping) or not stratified:
            blockers.append(
                "no stratified report was produced; calibration and skill must "
                "be reported by region, season, forecast lead, and HRRR era"
            )
        else:
            missing = [
                dimension
                for dimension in STRATUM_DIMENSIONS
                if dimension not in stratified
            ]
            if missing:
                blockers.append(
                    "stratified report is missing dimension(s): "
                    + ", ".join(missing)
                )
            for dimension, strata in stratified.items():
                if not isinstance(strata, Mapping):
                    continue
                for label, report in strata.items():
                    if not isinstance(report, Mapping):
                        continue
                    if int(report.get("cases", 0)) < criteria.minimum_stratum_cases:
                        continue
                    skill = report.get("brier_skill_score")
                    if skill is None:
                        blockers.append(
                            f"{dimension} stratum {label!r} has "
                            f"{report.get('cases')} cases but no Brier skill "
                            "score"
                        )
                    elif float(skill) < criteria.minimum_stratum_brier_skill_score:
                        blockers.append(
                            f"{dimension} stratum {label!r} degrades to Brier "
                            f"skill {float(skill):.3f} over "
                            f"{report.get('cases')} cases, below the allowed "
                            f"{criteria.minimum_stratum_brier_skill_score:.3f}"
                        )

    # --- prospective shadow validation --------------------------------- #
    if criteria.require_prospective_evaluation:
        if prospective is None:
            blockers.append(
                "no prospective shadow-validation record; a reserved future "
                "severe-weather season must be evaluated with the frozen "
                "artifact before it can be called validated"
            )
        else:
            if plan is not None and prospective.plan_hash != plan.plan_hash():
                blockers.append(
                    "the prospective record references a different frozen plan "
                    "than the one used for training"
                )
            # A matching plan hash proves *which* plan was used, not *when* the
            # season happened.  Without this ordering check a completed
            # historical season could be submitted as prospective evidence,
            # which is exactly the leakage the requirement exists to prevent:
            # every threshold and rule chosen after that data existed is
            # unquantifiably contaminated by having seen it.
            if plan is not None:
                frozen = _moment(plan.frozen_at)
                started = _moment(prospective.start_date)
                if started is None:
                    blockers.append(
                        "prospective start_date "
                        f"{prospective.start_date!r} is not an ISO-8601 date, "
                        "so it cannot be shown to postdate the frozen plan"
                    )
                elif frozen is None:
                    blockers.append(
                        f"frozen plan timestamp {plan.frozen_at!r} is not an "
                        "ISO-8601 timestamp, so prospective ordering cannot be "
                        "verified"
                    )
                elif started < frozen:
                    blockers.append(
                        "the prospective season starts "
                        f"{started.date().isoformat()} but the plan was frozen "
                        f"{frozen.date().isoformat()}; a season that had "
                        "already begun is retrospective, not prospective, and "
                        "cannot satisfy shadow validation"
                    )
                ended = _moment(prospective.end_date)
                if ended is not None and started is not None and ended < started:
                    blockers.append(
                        "prospective end_date precedes start_date"
                    )
            if (
                prospective.event_groups
                < criteria.minimum_prospective_event_groups
            ):
                blockers.append(
                    f"prospective season has only {prospective.event_groups} "
                    "event group(s); at least "
                    f"{criteria.minimum_prospective_event_groups} are required"
                )
            if (
                prospective.positive_events
                < criteria.minimum_prospective_positive_events
            ):
                blockers.append(
                    f"prospective season has only {prospective.positive_events} "
                    "positive event group(s); at least "
                    f"{criteria.minimum_prospective_positive_events} are required"
                )

    return {
        "validated": not blockers,
        "blockers": tuple(blockers),
        "criteria_version": criteria.criteria_version,
        "criteria_are_scientific": criteria.scientific,
        "plan_hash": plan.plan_hash() if plan is not None else None,
        "plan_version": plan.plan_version if plan is not None else None,
        "observed": {
            "development_years": list(development),
            "test_years": list(held_out),
            "event_groups": event_groups,
            "positive_events": positive_events,
            "negative_events": negative_events,
            "evaluated_folds": len(evaluated),
            "prospective_season": (
                prospective.season_label if prospective is not None else None
            ),
            "prospective_start_date": (
                prospective.start_date if prospective is not None else None
            ),
            "plan_frozen_at": plan.frozen_at if plan is not None else None,
            "prospective_starts_after_freeze": _starts_after_freeze(
                plan, prospective
            ),
        },
    }


def _starts_after_freeze(plan: Any, prospective: Any) -> bool | None:
    """Auditable record of the prospective ordering check.

    ``None`` means the comparison could not be made, which is itself a blocker.
    """

    if plan is None or prospective is None:
        return None
    frozen = _moment(getattr(plan, "frozen_at", ""))
    started = _moment(getattr(prospective, "start_date", ""))
    if frozen is None or started is None:
        return None
    return started >= frozen


__all__ = [
    "RECOMMENDED_DEVELOPMENT_YEARS",
    "RECOMMENDED_TEST_YEARS",
    "TOI_PROMOTION_CRITERIA_VERSION",
    "TOI_VALIDATION_PLAN_VERSION",
    "TOIPromotionCriteria",
    "TOIProspectiveRecord",
    "TOIValidationError",
    "TOIValidationPlan",
    "evaluate_promotion",
]
