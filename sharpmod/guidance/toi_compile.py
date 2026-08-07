"""Compile verified archive case files into a training-ready :class:`TOIDataset`.

The 43 GiB HRRR collection is a *one-time* cost.  Once
:class:`~sharpmod.guidance.toi_archive.ArchiveRunner` has written its compact
per-case JSON, every subsequent dataset build, refit, and re-evaluation reads
those files and performs **zero** network fetches.  This module is that bridge.

It refuses to compile anything it has not verified: the archive verifier must
pass first, so a tampered, truncated, orphaned, duplicated, or hash-mismatched
case can never reach a training set.  Skip reasons are preserved rather than
dropped, so a dataset records why cases are absent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .hrrr import HRRR_STP_PROXY_VERSION, HRRR_TOI_METHOD_VERSION
from .schemas import TOIFeatures
from .toi_archive import ArchiveRunner
from .toi_calibration import TOI_TARGET_DEFINITIONS
from .toi_dataset import (
    TOI_DATASET_COLUMNS,
    TOICaseRow,
    TOIDataset,
    high_risk_worthy_proxy_v1,
)
from .toi_scorecard import (
    TOI_PROBABILITY_VERSION,
    TOI_SCORECARD_VERSION,
    published_stp_bin_value,
)
from .toi_strata import hrrr_era, row_strata

TOI_COMPILE_VERSION = "sharpmod_toi_compile_v1"


class TOICompileError(ValueError):
    """Raised when verified archive output cannot be compiled into a dataset."""


def _label_for(
    payload: Mapping[str, Any],
    target_definition: str,
    manifest_labels: Mapping[str, int],
) -> int:
    case = payload["case"]
    event_id = str(case.get("event_id", ""))
    if target_definition == "manifest_label_v1":
        if event_id not in manifest_labels:
            raise TOICompileError(
                f"manifest_label_v1 requires an explicit label for {event_id!r}"
            )
        return int(manifest_labels[event_id])
    observed = case.get("observed")
    if not isinstance(observed, Mapping) or not observed:
        raise TOICompileError(
            f"case {event_id!r} has no observed counts, so "
            "high_risk_worthy_proxy_v1 cannot be computed"
        )
    return high_risk_worthy_proxy_v1(observed)


def resolve_work_dirs(
    work_dir: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
) -> list[Path]:
    """Normalise one or many archive work directories into an ordered list.

    A parallel shard run produces one work directory per shard, so compilation
    must be able to consume a set of them.  Passing a parent directory that
    contains ``shard-*`` subdirectories expands to those shards, which keeps the
    common case a single argument.
    """

    if isinstance(work_dir, (str, os.PathLike)):
        candidates = [Path(os.fspath(work_dir))]
    else:
        candidates = [Path(os.fspath(item)) for item in work_dir]
    if not candidates:
        raise TOICompileError("at least one archive work directory is required")

    resolved: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            raise TOICompileError(f"archive work directory not found: {candidate}")
        if (candidate / "cases").is_dir():
            resolved.append(candidate)
            continue
        shards = sorted(
            child
            for child in candidate.iterdir()
            if child.is_dir() and (child / "cases").is_dir()
        )
        if not shards:
            raise TOICompileError(
                f"{candidate} contains neither a cases/ directory nor any shard "
                "subdirectory with one; run the archive runner first"
            )
        resolved.extend(shards)

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in resolved:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def compile_archive_dataset(
    work_dir: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    *,
    target_definition: str = "high_risk_worthy_proxy_v1",
    label_source: str,
    dataset_kind: str = "historical",
    weighting: str = "natural",
    population_base_rate: float | None = None,
    manifest_labels: Mapping[str, int] | None = None,
    require_verified: bool = True,
    notes: str = "",
) -> tuple[TOIDataset, dict[str, Any]]:
    """Build a :class:`TOIDataset` from verified archive case files.

    Performs no network access: every value comes from the extracted JSON that
    the archive runner already wrote.
    """

    if target_definition not in TOI_TARGET_DEFINITIONS:
        known = ", ".join(sorted(TOI_TARGET_DEFINITIONS))
        raise TOICompileError(f"target_definition must be one of: {known}")
    if weighting not in {"natural", "population"}:
        raise TOICompileError("weighting must be 'natural' or 'population'")
    if weighting == "population" and population_base_rate is None:
        raise TOICompileError(
            "population weighting requires a documented population_base_rate"
        )

    work_dirs = resolve_work_dirs(work_dir)

    # Each shard is an independent run with its own checkpoint, so each is
    # verified on its own terms and the results are merged.  Duplicate detection
    # stays *global*: the same cache key appearing in two shards means the
    # event-indivisible split overlapped, which must fail loudly rather than
    # double-weight a case in training.
    labels = dict(manifest_labels or {})
    payloads: list[Mapping[str, Any]] = []
    seen: dict[str, Path] = {}
    verified_cases: list[Mapping[str, Any]] = []
    skipped_records: list[Mapping[str, Any]] = []
    merged_sources: dict[str, Any] = {}
    case_files = 0
    verified_all = True
    for directory in work_dirs:
        runner = ArchiveRunner(output_directory=directory)
        verification = runner.verify(expected_feature_method=HRRR_TOI_METHOD_VERSION)
        verified_all = verified_all and bool(verification["verified"])
        if require_verified and not verification["verified"]:
            first = verification["failures"][0]
            raise TOICompileError(
                f"refusing to compile unverified archive output in {directory}: "
                f"{verification['failure_count']} failure(s); first "
                f"{first['check']} at {first['path']}: {first['detail']}"
            )
        case_files += int(verification["case_files"])
        skipped_records.extend(verification["skipped"])
        sources = verification.get("sources")
        if isinstance(sources, Mapping):
            merged_sources.update(sources)
        for record in verification["cases"]:
            key = str(record["cache_key"])
            previous = seen.get(key)
            if previous is not None:
                raise TOICompileError(
                    f"duplicate cache_key {key} in verified output: present in "
                    f"both {previous} and {directory}; shards must not overlap"
                )
            seen[key] = directory
            verified_cases.append(record)
            path = runner.cases_dir / f"{key}.json"
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
    if not payloads:
        raise TOICompileError(
            "no verified case files were found; run the archive runner first"
        )
    verification = {
        "verified": verified_all,
        "cases": verified_cases,
        "skipped": skipped_records,
        "sources": merged_sources,
        "case_files": case_files,
        "work_dirs": [str(item) for item in work_dirs],
    }

    resolved = [
        (payload, _label_for(payload, target_definition, labels))
        for payload in payloads
    ]

    # One blocking year per event id, from its earliest issuance.
    event_year: dict[str, int] = {}
    for payload, _label in resolved:
        case = payload["case"]
        year = datetime.fromisoformat(str(case["run_time"])).year
        event_id = str(case["event_id"])
        current = event_year.get(event_id)
        if current is None or year < current:
            event_year[event_id] = year

    if weighting == "population":
        positives = sum(1 for _payload, label in resolved if label == 1)
        negatives = len(resolved) - positives
        if not positives or not negatives:
            raise TOICompileError(
                "population weighting needs both positive and negative cases"
            )
        rate = float(population_base_rate)
        positive_weight = rate * len(resolved) / positives
        negative_weight = (1.0 - rate) * len(resolved) / negatives
    else:
        positive_weight = negative_weight = 1.0

    rows: list[TOICaseRow] = []
    provenance_rows: list[dict[str, Any]] = []
    for payload, label in resolved:
        case = payload["case"]
        guidance = payload["guidance"]
        toi = guidance.get("toi", {})
        feature_payload = toi.get("features")
        if not isinstance(feature_payload, Mapping):
            raise TOICompileError(
                f"case {case.get('event_id')!r} has no operational TOI features"
            )
        # Round-trip through the validated contract so a compiled dataset can
        # never carry features the operational schema would reject.
        features = TOIFeatures.from_mapping(feature_payload)
        cycle = datetime.fromisoformat(str(case["run_time"]))
        event_id = str(case["event_id"])
        weight = positive_weight if label == 1 else negative_weight
        if case.get("sample_weight") is not None and weighting == "natural":
            weight = float(case["sample_weight"])
        provenance = guidance.get("provenance", {})
        values: dict[str, Any] = {
            "event_id": event_id,
            "case_class": case.get("case_class", "severe"),
            "issuance_time": cycle.isoformat(),
            "year": cycle.year,
            "event_year": event_year[event_id],
            "forecast_hour": int(case["forecast_hour"]),
            "latitude": float(case["resolved_latitude"]),
            "longitude": float(case["resolved_longitude"]),
            "anchor_source": case.get("anchor_source", ""),
            "pressure_level_hpa": features.pressure_level_hpa,
            "translation_speed_kt": features.translation_speed_kt,
            "maximum_jet_speed_kt": features.maximum_jet_speed_kt,
            "jet_to_risk_distance_km": features.jet_to_risk_distance_km,
            "jet_to_risk_bearing_deg": features.jet_to_risk_bearing_deg,
            "maximum_stp": features.maximum_stp,
            "peak_stp_bin": published_stp_bin_value(features.maximum_stp),
            "month": features.month,
            "experimental_score": toi.get("score"),
            "public_anchor_probability": toi.get("high_risk_probability"),
            "label": int(label),
            "label_source": label_source,
            "sample_weight": float(weight),
            "model_version": toi.get("method_version", HRRR_TOI_METHOD_VERSION),
            "provider_version": payload.get("sources", {}).get(
                "hrrr_bucket", "unknown"
            ),
            "stp_proxy_version": HRRR_STP_PROXY_VERSION,
            "scorecard_version": TOI_SCORECARD_VERSION,
            "public_anchor_probability_version": TOI_PROBABILITY_VERSION,
            "sampling_status": provenance.get("toi_sampling_status", "unknown"),
            "sampling_interval_hours": provenance.get(
                "toi_sampling_interval_hours", ""
            ),
            "time_coverage_hours": provenance.get("toi_time_coverage_hours", ""),
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
        missing = [name for name in TOI_DATASET_COLUMNS if name not in values]
        if missing:  # pragma: no cover - guarded by TOICaseRow as well
            raise TOICompileError(
                "compiled row is missing column(s): " + ", ".join(missing)
            )
        rows.append(TOICaseRow(values))
        strata = row_strata(values)
        provenance_rows.append(
            {
                "event_id": event_id,
                "cache_key": payload["cache_key"],
                "scientific_content_sha256": payload["scientific_content_sha256"],
                "event_year": event_year[event_id],
                "hrrr_era": hrrr_era(cycle),
                **strata,
                "anchor_selection": payload.get("anchor", {}).get(
                    "anchor_selection_method"
                ),
                "anchor_land_fraction": payload.get("anchor", {}).get(
                    "anchor_selected_land_fraction"
                ),
                "sources": payload.get("sources", {}),
            }
        )

    dataset = TOIDataset(
        rows=tuple(rows),
        target_definition=target_definition,
        label_source=label_source,
        manifest_digest=verification["cases"][0]["scientific_content_sha256"][:16]
        if verification["cases"]
        else "",
        weighting=weighting,
        dataset_kind=dataset_kind,
        skipped=tuple(
            {
                "event_id": str(item.get("event_id") or ""),
                "run_time": str(item.get("run_time") or ""),
                "reason": str(item.get("reason") or ""),
            }
            for item in verification["skipped"]
        ),
        notes=notes
        or f"Compiled from verified archive output by {TOI_COMPILE_VERSION}",
    )
    report = {
        "compile_version": TOI_COMPILE_VERSION,
        "network_fetches": 0,
        "verified": verification["verified"],
        "case_files": verification["case_files"],
        "work_dirs": verification["work_dirs"],
        "shard_count": len(work_dirs),
        "compiled_rows": len(rows),
        "skipped_cases": len(verification["skipped"]),
        "event_years": sorted(set(event_year.values())),
        "weighting": weighting,
        "population_base_rate": population_base_rate,
        "provenance": provenance_rows,
        "sources": verification["sources"],
        "experimental_not_official": True,
    }
    return dataset, report


def compile_from_manifest_labels(
    work_dir: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    **kwargs: Any,
) -> tuple[TOIDataset, dict[str, Any]]:
    """Compile using explicit labels and weights from a documented manifest."""

    from .toi_dataset import TOILabelManifest

    manifest = TOILabelManifest.load(manifest_path)
    labels = {
        case.event_id: manifest.label_for(case) for case in manifest.cases
    }
    kwargs.setdefault("target_definition", manifest.target_definition)
    kwargs.setdefault("label_source", manifest.label_source)
    kwargs.setdefault("dataset_kind", manifest.dataset_kind)
    if manifest.population_base_rate is not None:
        kwargs.setdefault("population_base_rate", manifest.population_base_rate)
    return compile_archive_dataset(work_dir, manifest_labels=labels, **kwargs)


def save_compile_report(
    report: Mapping[str, Any], path: str | os.PathLike[str]
) -> str:
    from .toi_evaluation import strict_json_dumps

    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(strict_json_dumps(report, indent=2))
        handle.write("\n")
    return target


__all__ = [
    "TOI_COMPILE_VERSION",
    "TOICompileError",
    "compile_archive_dataset",
    "compile_from_manifest_labels",
    "resolve_work_dirs",
    "save_compile_report",
]
