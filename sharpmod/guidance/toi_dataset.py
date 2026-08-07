"""Offline historical TOI case dataset construction.

The dataset builder calls the *operational* live producer
(:func:`sharpmod.guidance.hrrr.build_live_hrrr_guidance`) for every historical
case, so archived feature extraction and temporal sampling are literally the
same code paths the GUI uses.  No parallel "training-only" feature
implementation exists, which removes the most common source of train/serve
skew.

Three scientific rules are enforced rather than documented:

1. **No manufactured official labels.** Outcomes come either verbatim from a
   documented label manifest, or from a clearly named, versioned SHARPpy proxy
   screen whose rule is stated in the open. SHARPpy does define that proxy, but
   official Risk Impact Value is never computed or claimed.
2. **No observation leakage.** Each case declares how its forecast anchor point
   was chosen, and anchors derived from observed tornado locations are
   rejected. The risk region itself is always the objective forecast proxy-STP
   region the live producer derives at issuance.
3. **Honest event frequency.** Manifests must contain outbreak, ordinary
   severe, and null/control cases. Either the natural sample frequency is
   preserved, or explicit sampling weights restore a documented population base
   rate so fitted probabilities are not inflated.
4. **Indivisible events.** Every row of one event id carries the same
   ``event_year``, so blocked validation cannot split a case series that
   crosses a New Year boundary across training and test.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .hrrr import (
    HRRR_STP_PROXY_VERSION,
    HRRR_TOI_METHOD_VERSION,
    TOI_SAMPLING_INTERVAL_HOURS,
    build_live_hrrr_guidance,
)
from .schemas import GuidanceState, RegionalGuidance
from .toi_calibration import TOI_TARGET_DEFINITIONS
from .toi_evaluation import strict_json_dumps
from .toi_scorecard import (
    TOI_PROBABILITY_VERSION,
    TOI_SCORECARD_VERSION,
    published_stp_bin_value,
)

TOI_DATASET_SCHEMA_VERSION = 1
TOI_DATASET_BUILDER_VERSION = "sharpmod_toi_dataset_builder_v1"

#: Every case must be one of these classes so a manifest cannot quietly become
#: an all-outbreak sample with a meaningless base rate.
TOI_CASE_CLASSES = ("outbreak", "severe", "null")

#: Forecast-time-available ways of choosing the case anchor point.  Anything
#: derived from what was later observed is rejected outright.
TOI_ALLOWED_ANCHOR_SOURCES = {
    "spc_outlook_centroid_at_issuance": (
        "Centroid of the SPC convective outlook area valid at, and issued no "
        "later than, the model cycle used for the case."
    ),
    "model_forecast_maximum_stp": (
        "Location of the forecast maximum proxy STP from the same or an earlier "
        "model cycle."
    ),
    "fixed_domain_grid": (
        "A fixed, case-independent geographic grid or station list chosen "
        "before any outcome was known."
    ),
    "climatological_region_centroid": (
        "A fixed climatological region centroid that does not depend on the "
        "case outcome."
    ),
}

#: Anchors that would leak the verifying observations into the predictors.
TOI_FORBIDDEN_ANCHOR_SOURCES = {
    "observed_tornado_locations",
    "observed_tornado_centroid",
    "storm_report_centroid",
    "verified_damage_survey",
}

TOI_DATASET_COLUMNS = (
    "event_id",
    "case_class",
    "issuance_time",
    "year",
    "event_year",
    "forecast_hour",
    "latitude",
    "longitude",
    "anchor_source",
    "pressure_level_hpa",
    "translation_speed_kt",
    "maximum_jet_speed_kt",
    "jet_to_risk_distance_km",
    "jet_to_risk_bearing_deg",
    "maximum_stp",
    "peak_stp_bin",
    "month",
    "experimental_score",
    "public_anchor_probability",
    "label",
    "label_source",
    "sample_weight",
    "model_version",
    "provider_version",
    "stp_proxy_version",
    "scorecard_version",
    "public_anchor_probability_version",
    "sampling_status",
    "sampling_interval_hours",
    "time_coverage_hours",
    "frame_count",
    "requested_forecast_hours",
    "successful_forecast_hours",
    "risk_region_source",
)


class TOIDatasetError(ValueError):
    """Raised when a label manifest or case extraction is not usable."""


def high_risk_worthy_proxy_v1(observed: Mapping[str, Any]) -> int:
    """SHARPpy's named high-end tornado-day screen; not official RIV.

    The rule is a coarse, transparent screen over NCEI Storm Events tornado
    fields available for any archived day.  A case is positive when the
    observed day reached any of:

    * one or more EF4+ tornadoes;
    * two or more EF3+ tornadoes;
    * twenty or more tornadoes including at least one EF2+; or
    * an EF2+ tornado with a path length of 40 miles or more.

    This intentionally does not reproduce the official Risk Impact Value, whose
    weights, impact terms, and event-separation rules are unpublished.
    """

    if not isinstance(observed, Mapping):
        raise TOIDatasetError(
            "high_risk_worthy_proxy_v1 requires an 'observed' object per case"
        )

    def _count(name: str) -> float:
        value = observed.get(name, 0)
        if isinstance(value, bool):
            raise TOIDatasetError(f"observed.{name} must be numeric")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise TOIDatasetError(f"observed.{name} must be numeric") from exc
        if number < 0:
            raise TOIDatasetError(f"observed.{name} must be non-negative")
        return number

    tornado_count = _count("tornado_count")
    ef2_plus = _count("ef2_plus_count")
    ef3_plus = _count("ef3_plus_count")
    ef4_plus = _count("ef4_plus_count")
    longest_ef2_path = _count("longest_ef2_plus_path_miles")
    if ef3_plus < ef4_plus or ef2_plus < ef3_plus or tornado_count < ef2_plus:
        raise TOIDatasetError(
            "observed tornado counts must be nested: "
            "tornado_count >= ef2_plus_count >= ef3_plus_count >= ef4_plus_count"
        )
    positive = (
        ef4_plus >= 1
        or ef3_plus >= 2
        or (tornado_count >= 20 and ef2_plus >= 1)
        or longest_ef2_path >= 40.0
    )
    return 1 if positive else 0


@dataclass(frozen=True)
class TOICase:
    """One independent historical forecast case awaiting feature extraction."""

    event_id: str
    case_class: str
    run_time: datetime
    forecast_hour: int
    latitude: float
    longitude: float
    anchor_source: str
    label: int | None = None
    observed: Mapping[str, Any] = field(default_factory=dict)
    sample_weight: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        event_id = " ".join(str(self.event_id).split())
        if not event_id:
            raise TOIDatasetError("each case needs a non-empty event_id")
        if self.case_class not in TOI_CASE_CLASSES:
            raise TOIDatasetError(
                f"case_class must be one of {TOI_CASE_CLASSES}; got "
                f"{self.case_class!r}"
            )
        if not isinstance(self.run_time, datetime):
            raise TOIDatasetError("run_time must be a datetime")
        run_time = (
            self.run_time.replace(tzinfo=timezone.utc)
            if self.run_time.tzinfo is None
            else self.run_time.astimezone(timezone.utc)
        )
        forecast_hour = int(self.forecast_hour)
        if forecast_hour < 0:
            raise TOIDatasetError("forecast_hour must be non-negative")
        latitude, longitude = float(self.latitude), float(self.longitude)
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise TOIDatasetError("case latitude/longitude are out of range")
        anchor = str(self.anchor_source).strip()
        if anchor in TOI_FORBIDDEN_ANCHOR_SOURCES:
            raise TOIDatasetError(
                f"anchor_source {anchor!r} would leak verifying observations "
                "into the predictors; every input must be available at "
                "forecast issuance"
            )
        if anchor not in TOI_ALLOWED_ANCHOR_SOURCES:
            known = ", ".join(sorted(TOI_ALLOWED_ANCHOR_SOURCES))
            raise TOIDatasetError(
                f"anchor_source must be one of: {known}; got {anchor!r}"
            )
        if self.label is not None and int(self.label) not in {0, 1}:
            raise TOIDatasetError("case label must be 0 or 1 when supplied")
        if self.sample_weight is not None:
            weight = float(self.sample_weight)
            if weight <= 0:
                raise TOIDatasetError("sample_weight must be positive when supplied")
            object.__setattr__(self, "sample_weight", weight)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "run_time", run_time)
        object.__setattr__(self, "forecast_hour", forecast_hour)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "anchor_source", anchor)
        object.__setattr__(
            self, "label", None if self.label is None else int(self.label)
        )
        object.__setattr__(self, "observed", dict(self.observed))
        object.__setattr__(self, "notes", " ".join(str(self.notes).split()))

    @property
    def year(self) -> int:
        return self.run_time.year

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOICase:
        if not isinstance(payload, Mapping):
            raise TOIDatasetError("each manifest case must be an object")
        raw_time = payload.get("run_time")
        if isinstance(raw_time, datetime):
            run_time = raw_time
        else:
            text = str(raw_time or "").strip().replace("Z", "+00:00")
            try:
                run_time = datetime.fromisoformat(text)
            except ValueError as exc:
                raise TOIDatasetError(
                    f"case run_time must be an ISO-8601 datetime; got {raw_time!r}"
                ) from exc
        return cls(
            event_id=payload.get("event_id", ""),
            case_class=payload.get("case_class", ""),
            run_time=run_time,
            forecast_hour=payload.get("forecast_hour", 0),
            latitude=payload.get("latitude", 0.0),
            longitude=payload.get("longitude", 0.0),
            anchor_source=payload.get("anchor_source", ""),
            label=payload.get("label"),
            observed=payload.get("observed") or {},
            sample_weight=payload.get("sample_weight"),
            notes=payload.get("notes", ""),
        )


@dataclass(frozen=True)
class TOILabelManifest:
    """A documented, external description of cases and their outcomes."""

    target_definition: str
    label_source: str
    cases: tuple[TOICase, ...]
    population_base_rate: float | None = None
    #: Defaults to the safe value: nothing claims to be a real historical
    #: archive unless the manifest says so explicitly.
    dataset_kind: str = "synthetic-fixture"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.dataset_kind not in {"historical", "synthetic-fixture"}:
            raise TOIDatasetError(
                "dataset_kind must be 'historical' or 'synthetic-fixture'; "
                "synthetic fixtures may validate the pipeline but never the "
                "science"
            )
        if self.target_definition not in TOI_TARGET_DEFINITIONS:
            known = ", ".join(sorted(TOI_TARGET_DEFINITIONS))
            raise TOIDatasetError(
                f"target_definition must be one of: {known}. Official Risk "
                "Impact Value labels cannot be manufactured by SHARPpy."
            )
        source = " ".join(str(self.label_source).split())
        if not source:
            raise TOIDatasetError(
                "label_source must document where the outcomes came from "
                "(for example an NCEI Storm Events export and its version)"
            )
        cases = tuple(self.cases)
        if not cases:
            raise TOIDatasetError("a label manifest must contain at least one case")
        seen = Counter(
            (case.event_id, case.run_time, case.forecast_hour) for case in cases
        )
        duplicates = sorted(str(key) for key, count in seen.items() if count > 1)
        if duplicates:
            raise TOIDatasetError(
                "manifest cases must be independent; duplicate "
                "event_id/run_time/forecast_hour entries found"
            )
        present = {case.case_class for case in cases}
        missing = [name for name in TOI_CASE_CLASSES if name not in present]
        if missing:
            raise TOIDatasetError(
                "a calibration manifest must include outbreak, ordinary severe, "
                "and null/control cases so the base rate is meaningful; missing: "
                + ", ".join(missing)
            )
        if self.target_definition == "manifest_label_v1":
            unlabeled = [case.event_id for case in cases if case.label is None]
            if unlabeled:
                raise TOIDatasetError(
                    "manifest_label_v1 requires an explicit label for every "
                    "case; missing: " + ", ".join(sorted(set(unlabeled))[:5])
                )
        if self.population_base_rate is not None:
            rate = float(self.population_base_rate)
            if not 0.0 < rate < 1.0:
                raise TOIDatasetError(
                    "population_base_rate must be strictly between 0 and 1"
                )
            object.__setattr__(self, "population_base_rate", rate)
        object.__setattr__(self, "label_source", source)
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "notes", " ".join(str(self.notes).split()))

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted({case.year for case in self.cases}))

    def label_for(self, case: TOICase) -> int:
        """Return the outcome for one case under the documented target."""

        if self.target_definition == "manifest_label_v1":
            if case.label is None:
                raise TOIDatasetError(f"case {case.event_id} has no manifest label")
            return int(case.label)
        proxy = high_risk_worthy_proxy_v1(case.observed)
        if case.label is not None and int(case.label) != proxy:
            raise TOIDatasetError(
                f"case {case.event_id} supplies label {case.label} but "
                f"high_risk_worthy_proxy_v1 computes {proxy}; resolve the "
                "disagreement instead of silently overriding the named proxy"
            )
        return proxy

    def digest(self) -> str:
        """Return a stable content hash of the manifest inputs."""

        payload = {
            "target_definition": self.target_definition,
            "label_source": self.label_source,
            "population_base_rate": self.population_base_rate,
            "dataset_kind": self.dataset_kind,
            "cases": [
                {
                    "event_id": case.event_id,
                    "case_class": case.case_class,
                    "run_time": case.run_time.isoformat(),
                    "forecast_hour": case.forecast_hour,
                    "latitude": case.latitude,
                    "longitude": case.longitude,
                    "anchor_source": case.anchor_source,
                    "label": case.label,
                    "observed": dict(case.observed),
                    "sample_weight": case.sample_weight,
                }
                for case in self.cases
            ],
        }
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOILabelManifest:
        if not isinstance(payload, Mapping):
            raise TOIDatasetError("label manifest must be a JSON object")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
            raise TOIDatasetError("label manifest must contain a 'cases' array")
        return cls(
            target_definition=str(payload.get("target_definition", "")),
            label_source=payload.get("label_source", ""),
            cases=tuple(TOICase.from_mapping(item) for item in raw_cases),
            population_base_rate=payload.get("population_base_rate"),
            dataset_kind=str(payload.get("dataset_kind", "synthetic-fixture")),
            notes=payload.get("notes", ""),
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TOILabelManifest:
        source = os.path.abspath(os.fspath(path))
        try:
            with open(source, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise TOIDatasetError(f"could not read label manifest: {exc}") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TOIDatasetError(f"invalid label manifest JSON: {exc}") from exc
        return cls.from_mapping(payload)


@dataclass(frozen=True)
class TOICaseRow:
    """One extracted dataset row.

    ``year`` is the row's own issuance year.  ``event_year`` is the single
    blocking year assigned to the whole event, so a case series that crosses a
    New Year boundary still validates as one indivisible unit.
    """

    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        missing = [name for name in TOI_DATASET_COLUMNS if name not in self.values]
        if missing:
            raise TOIDatasetError(
                "dataset row is missing column(s): " + ", ".join(missing)
            )
        object.__setattr__(
            self, "values", {name: self.values[name] for name in TOI_DATASET_COLUMNS}
        )

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    @property
    def year(self) -> int:
        """The row's own issuance year; use :attr:`event_year` for blocking."""

        return int(self.values["year"])

    @property
    def event_year(self) -> int:
        """The event's single validation year, shared by all of its rows."""

        return int(self.values["event_year"])

    @property
    def label(self) -> int:
        return int(self.values["label"])

    @property
    def sample_weight(self) -> float:
        return float(self.values["sample_weight"])

    @property
    def event_id(self) -> str:
        return str(self.values["event_id"])


@dataclass(frozen=True)
class TOIDataset:
    """An extracted, hashable historical TOI dataset."""

    rows: tuple[TOICaseRow, ...]
    target_definition: str
    label_source: str
    manifest_digest: str
    weighting: str
    dataset_kind: str = "synthetic-fixture"
    skipped: tuple[Mapping[str, str], ...] = ()
    builder_version: str = TOI_DATASET_BUILDER_VERSION
    schema_version: int = TOI_DATASET_SCHEMA_VERSION
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.rows:
            raise TOIDatasetError("dataset contains no usable cases")
        if self.dataset_kind not in {"historical", "synthetic-fixture"}:
            raise TOIDatasetError(
                "dataset_kind must be 'historical' or 'synthetic-fixture'"
            )
        rows = tuple(self.rows)
        # One event must resolve to exactly one blocking year, otherwise a
        # year-spanning event could be split across a train/test boundary.
        by_event: dict[str, set[int]] = {}
        for row in rows:
            by_event.setdefault(row.event_id, set()).add(row.event_year)
        straddling = sorted(
            event for event, years in by_event.items() if len(years) > 1
        )
        if straddling:
            raise TOIDatasetError(
                "each event must have exactly one event_year so blocked "
                "validation cannot split it; offending event(s): "
                + ", ".join(straddling[:5])
            )
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "skipped", tuple(dict(item) for item in self.skipped))

    @property
    def years(self) -> tuple[int, ...]:
        """The blocking years: one per event, never a row's raw calendar year."""

        return tuple(sorted({row.event_year for row in self.rows}))

    @property
    def calendar_years(self) -> tuple[int, ...]:
        """Every issuance year present, which may exceed the blocking years."""

        return tuple(sorted({row.year for row in self.rows}))

    @property
    def event_years(self) -> dict[str, int]:
        """Map each event id to its single validation year."""

        return {row.event_id: row.event_year for row in self.rows}

    @property
    def is_multi_year(self) -> bool:
        return len(self.years) >= 2

    @property
    def event_count(self) -> int:
        return len({row.event_id for row in self.rows})

    @property
    def positive_count(self) -> int:
        return sum(1 for row in self.rows if row.label == 1)

    def data_hash(self) -> str:
        """Hash the exact extracted feature/label content, not just inputs."""

        serialized = json.dumps(
            [row.values for row in self.rows],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def column(self, name: str) -> tuple[Any, ...]:
        if name not in TOI_DATASET_COLUMNS:
            raise TOIDatasetError(f"unknown dataset column {name!r}")
        return tuple(row[name] for row in self.rows)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "builder_version": self.builder_version,
            "target_definition": self.target_definition,
            "target_description": TOI_TARGET_DEFINITIONS[self.target_definition],
            "label_source": self.label_source,
            "manifest_digest": self.manifest_digest,
            "weighting": self.weighting,
            "dataset_kind": self.dataset_kind,
            "columns": list(TOI_DATASET_COLUMNS),
            "blocking_unit": "event_year (one validation year per event id)",
            "years": list(self.years),
            "calendar_years": list(self.calendar_years),
            "scorecard_version": TOI_SCORECARD_VERSION,
            "public_anchor_probability_version": TOI_PROBABILITY_VERSION,
            "case_count": len(self.rows),
            "event_count": self.event_count,
            "positive_count": self.positive_count,
            "data_hash": self.data_hash(),
            "skipped": [dict(item) for item in self.skipped],
            "notes": self.notes,
            "experimental_not_official": True,
            "rows": [dict(row.values) for row in self.rows],
        }

    def save_json(self, path: str | os.PathLike[str]) -> str:
        target = os.path.abspath(os.fspath(path))
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(strict_json_dumps(self.to_mapping(), indent=2))
            handle.write("\n")
        return target

    def save_csv(self, path: str | os.PathLike[str]) -> str:
        target = os.path.abspath(os.fspath(path))
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(TOI_DATASET_COLUMNS))
            writer.writeheader()
            for row in self.rows:
                writer.writerow(dict(row.values))
        return target

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOIDataset:
        if not isinstance(payload, Mapping):
            raise TOIDatasetError("dataset must be a JSON object")
        if int(payload.get("schema_version", 0)) != TOI_DATASET_SCHEMA_VERSION:
            raise TOIDatasetError(
                f"unsupported dataset schema_version {payload.get('schema_version')!r}"
            )
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            raise TOIDatasetError("dataset must contain a 'rows' array")
        dataset = cls(
            rows=tuple(TOICaseRow(dict(item)) for item in raw_rows),
            target_definition=str(payload.get("target_definition", "")),
            label_source=str(payload.get("label_source", "")),
            manifest_digest=str(payload.get("manifest_digest", "")),
            weighting=str(payload.get("weighting", "natural")),
            dataset_kind=str(payload.get("dataset_kind", "synthetic-fixture")),
            skipped=tuple(payload.get("skipped") or ()),
            builder_version=str(
                payload.get("builder_version", TOI_DATASET_BUILDER_VERSION)
            ),
            notes=str(payload.get("notes", "")),
        )
        if dataset.target_definition not in TOI_TARGET_DEFINITIONS:
            raise TOIDatasetError("dataset target_definition is not recognized")
        stored = str(payload.get("data_hash", ""))
        if stored and stored != dataset.data_hash():
            raise TOIDatasetError(
                "dataset data_hash does not match its rows; the file was "
                "modified after extraction"
            )
        return dataset

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TOIDataset:
        source = os.path.abspath(os.fspath(path))
        try:
            with open(source, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise TOIDatasetError(f"could not read TOI dataset: {exc}") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TOIDatasetError(f"invalid TOI dataset JSON: {exc}") from exc
        return cls.from_mapping(payload)


def _sample_weights(
    manifest: TOILabelManifest, labels: Sequence[int], weighting: str
) -> tuple[float, ...]:
    """Return per-case weights under an explicit, documented policy."""

    if weighting == "natural":
        return tuple(
            float(case.sample_weight or 1.0) for case in manifest.cases[: len(labels)]
        )
    if weighting != "population":
        raise TOIDatasetError("weighting must be 'natural' or 'population'")
    if manifest.population_base_rate is None:
        raise TOIDatasetError(
            "population weighting requires a documented population_base_rate "
            "in the manifest so the sampled frequency can be corrected"
        )
    positives = sum(1 for label in labels if int(label) == 1)
    negatives = len(labels) - positives
    if not positives or not negatives:
        raise TOIDatasetError(
            "population weighting needs both positive and negative cases"
        )
    rate = float(manifest.population_base_rate)
    # Rescale each class so the weighted base rate equals the documented
    # population frequency while the total weight stays at the case count.
    positive_weight = rate * len(labels) / positives
    negative_weight = (1.0 - rate) * len(labels) / negatives
    return tuple(
        positive_weight if int(label) == 1 else negative_weight for label in labels
    )


def extract_toi_case(
    case: TOICase,
    *,
    fetcher: Callable[..., Any] | None = None,
    download_dir: str | os.PathLike[str] | None = None,
    sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    **producer_kwargs: Any,
) -> RegionalGuidance:
    """Run the operational live producer against one archived case.

    Reusing :func:`build_live_hrrr_guidance` guarantees the archived case sees
    exactly the operational feature code, jet tracking, objective risk region,
    scorecard, and three-hourly temporal sampling.
    """

    return build_live_hrrr_guidance(
        case.run_time,
        case.forecast_hour,
        case.latitude,
        case.longitude,
        download_dir=download_dir,
        fetcher=fetcher,
        sampling_interval_hours=sampling_interval_hours,
        **producer_kwargs,
    )


def build_toi_dataset(
    manifest: TOILabelManifest,
    *,
    fetcher: Callable[..., Any] | None = None,
    download_dir: str | os.PathLike[str] | None = None,
    weighting: str = "natural",
    require_complete_sampling: bool = False,
    provider_version: str = "",
    sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    progress: Callable[[str], None] | None = None,
    **producer_kwargs: Any,
) -> TOIDataset:
    """Extract one row per independent forecast case in the manifest."""

    if weighting not in {"natural", "population"}:
        raise TOIDatasetError("weighting must be 'natural' or 'population'")
    accepted: list[tuple[TOICase, RegionalGuidance, int]] = []
    skipped: list[dict[str, str]] = []
    for case in manifest.cases:
        if progress is not None:
            progress(
                f"{case.event_id} {case.run_time.isoformat()} "
                f"F{case.forecast_hour:03d}"
            )
        guidance = extract_toi_case(
            case,
            fetcher=fetcher,
            download_dir=download_dir,
            sampling_interval_hours=sampling_interval_hours,
            **producer_kwargs,
        )
        product = guidance.toi
        if product.state is GuidanceState.UNAVAILABLE or product.features is None:
            skipped.append(
                {
                    "event_id": case.event_id,
                    "run_time": case.run_time.isoformat(),
                    "reason": product.reason or "TOI features unavailable",
                }
            )
            continue
        status = guidance.provenance.get("toi_sampling_status", "unknown")
        if require_complete_sampling and status != "complete":
            skipped.append(
                {
                    "event_id": case.event_id,
                    "run_time": case.run_time.isoformat(),
                    "reason": (
                        f"sampling status {status}: "
                        + guidance.provenance.get(
                            "toi_sampling_degraded_reason", ""
                        )
                    ),
                }
            )
            continue
        accepted.append((case, guidance, manifest.label_for(case)))

    if not accepted:
        raise TOIDatasetError(
            "no manifest case produced usable TOI features; "
            + (skipped[0]["reason"] if skipped else "no cases were attempted")
        )
    labels = [label for _case, _guidance, label in accepted]
    if weighting == "natural":
        weights = tuple(
            float(case.sample_weight or 1.0) for case, _guidance, _label in accepted
        )
    else:
        weights = _sample_weights(manifest, labels, weighting)

    # Assign one blocking year per event id, taken from its earliest issuance,
    # so a case series crossing a New Year boundary stays in a single fold.
    event_year: dict[str, int] = {}
    for case, _guidance, _label in accepted:
        current = event_year.get(case.event_id)
        if current is None or case.year < current:
            event_year[case.event_id] = case.year

    rows: list[TOICaseRow] = []
    for (case, guidance, label), weight in zip(accepted, weights, strict=True):
        features = guidance.toi.features
        provenance = guidance.provenance
        rows.append(
            TOICaseRow(
                {
                    "event_id": case.event_id,
                    "case_class": case.case_class,
                    "issuance_time": case.run_time.isoformat(),
                    "year": case.year,
                    "event_year": event_year[case.event_id],
                    "forecast_hour": case.forecast_hour,
                    "latitude": case.latitude,
                    "longitude": case.longitude,
                    "anchor_source": case.anchor_source,
                    "pressure_level_hpa": features.pressure_level_hpa,
                    "translation_speed_kt": features.translation_speed_kt,
                    "maximum_jet_speed_kt": features.maximum_jet_speed_kt,
                    "jet_to_risk_distance_km": features.jet_to_risk_distance_km,
                    "jet_to_risk_bearing_deg": features.jet_to_risk_bearing_deg,
                    "maximum_stp": features.maximum_stp,
                    "peak_stp_bin": published_stp_bin_value(features.maximum_stp),
                    "month": features.month,
                    "experimental_score": guidance.toi.score,
                    "public_anchor_probability": guidance.toi.high_risk_probability,
                    "label": int(label),
                    "label_source": manifest.label_source,
                    "sample_weight": float(weight),
                    "model_version": guidance.toi.method_version
                    or HRRR_TOI_METHOD_VERSION,
                    "provider_version": provider_version
                    or provenance.get("source_urls", "")[:120]
                    or guidance.source,
                    "stp_proxy_version": HRRR_STP_PROXY_VERSION,
                    "scorecard_version": TOI_SCORECARD_VERSION,
                    "public_anchor_probability_version": TOI_PROBABILITY_VERSION,
                    "sampling_status": provenance.get("toi_sampling_status", "unknown"),
                    "sampling_interval_hours": provenance.get(
                        "toi_sampling_interval_hours", str(sampling_interval_hours)
                    ),
                    "time_coverage_hours": provenance.get(
                        "toi_time_coverage_hours", ""
                    ),
                    "frame_count": provenance.get("toi_frame_count", ""),
                    "requested_forecast_hours": provenance.get(
                        "toi_requested_forecast_hours", ""
                    ),
                    "successful_forecast_hours": provenance.get(
                        "toi_successful_forecast_hours", ""
                    ),
                    "risk_region_source": provenance.get(
                        "risk_mask", "objective forecast proxy-STP region"
                    ),
                }
            )
        )
    return TOIDataset(
        rows=tuple(rows),
        target_definition=manifest.target_definition,
        label_source=manifest.label_source,
        manifest_digest=manifest.digest(),
        weighting=weighting,
        dataset_kind=manifest.dataset_kind,
        skipped=tuple(skipped),
        notes=manifest.notes,
    )


__all__ = [
    "TOI_ALLOWED_ANCHOR_SOURCES",
    "TOI_CASE_CLASSES",
    "TOI_DATASET_BUILDER_VERSION",
    "TOI_DATASET_COLUMNS",
    "TOI_DATASET_SCHEMA_VERSION",
    "TOI_FORBIDDEN_ANCHOR_SOURCES",
    "TOICase",
    "TOICaseRow",
    "TOIDataset",
    "TOIDatasetError",
    "TOILabelManifest",
    "build_toi_dataset",
    "extract_toi_case",
    "high_risk_worthy_proxy_v1",
]
