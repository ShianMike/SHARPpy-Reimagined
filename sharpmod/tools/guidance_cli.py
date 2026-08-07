#!/usr/bin/env python3
"""``sharpmod-guidance``: reproducible offline TOI calibration commands.

Three subcommands cover the whole offline pipeline:

``build-toi-dataset``
    Extract one row per independent historical forecast case using the exact
    operational TOI feature code and three-hourly temporal sampling.

``train-toi``
    Fit a regularized logistic calibrator on TOI score and peak-STP bin with
    year-blocked validation and an untouched test period.

``evaluate-toi``
    Score the shipped public-anchor transform, climatology, and an optional
    artifact on the same cases, with bootstrap confidence intervals.

Nothing here produces official SPC guidance, and no command computes an
official Risk Impact Value.  Archived HRRR input comes from the NOAA Open Data
archive (2014 onward) through the same Herbie-backed fetcher the live producer
uses; verified outcomes must be supplied by a documented label manifest built
from NCEI Storm Events / SPC reports.
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from sharpmod.guidance.toi_calibration import (
    TOI_TARGET_DEFINITIONS,
    TOICalibrationArtifact,
    TOICalibrationError,
)
from sharpmod.guidance.toi_dataset import (
    TOI_SAMPLING_INTERVAL_HOURS,
    TOIDataset,
    TOIDatasetError,
    TOILabelManifest,
    build_toi_dataset,
)
from sharpmod.guidance.toi_evaluation import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_DECISION_THRESHOLD,
    TOIEvaluationError,
    strict_json_dumps,
)
from sharpmod.guidance.toi_training import (
    VALIDATION_SCHEMES,
    evaluate_dataset,
    train_toi_calibrator,
)
from sharpmod.guidance.toi_validation import (
    RECOMMENDED_DEVELOPMENT_YEARS,
    RECOMMENDED_TEST_YEARS,
    TOIPromotionCriteria,
    TOIProspectiveRecord,
    TOIValidationError,
    TOIValidationPlan,
)

#: Named promotion gates.  ``pipeline-smoke`` is marked non-scientific and can
#: never promote an artifact; it exists to exercise the pipeline.
CRITERIA_PRESETS = {
    "research-target": TOIPromotionCriteria.research_target,
    "pipeline-smoke": TOIPromotionCriteria.pipeline_smoke,
}

DISCLAIMER = (
    "EXPERIMENTAL SHARPpy reconstruction - not official SPC guidance, and not "
    "an official Risk Impact Value."
)

DEFAULT_CACHE_HINTS = (
    "~/data",
    "~/AppData/Local/sharpmod",
    "~/.cache/herbie",
)


def _gib(value: float) -> str:
    return f"{value / 1024**3:.2f} GiB"


def audit_archive_command(args: argparse.Namespace) -> int:
    """Report disk headroom, caches, documented sources, and run estimates."""

    from sharpmod.guidance.toi_archive import (
        archive_source_record,
        audit_local_resources,
        default_case_estimate,
    )

    audit = audit_local_resources(
        working_directory=args.work_dir,
        cache_directories=[Path(hint).expanduser() for hint in DEFAULT_CACHE_HINTS],
    )
    estimate = default_case_estimate(
        forecast_hour=args.forecast_hour,
        sampling_interval_hours=args.sampling_interval_hours,
        seconds_per_frame=args.seconds_per_frame,
        resolve_anchor=not args.no_resolve_anchor,
    )
    scales = [args.cases] if args.cases else [12, 200, 600, 900]
    payload = {
        "disclaimer": DISCLAIMER,
        "resources": audit,
        "sources": archive_source_record(),
        "per_case_estimate": estimate.to_mapping(),
        "scenarios": {str(count): estimate.for_cases(count) for count in scales},
    }

    print(DISCLAIMER, flush=True)
    print(
        f"Disk: {audit['disk_free_gib']} GiB free of {audit['disk_total_gib']} GiB "
        f"at {audit['working_directory']}",
        flush=True,
    )
    for cache in audit["caches"]:
        if cache["present"]:
            print(f"  cache {cache['path']}: {cache['mib']} MiB", flush=True)
        else:
            print(f"  cache {cache['path']}: absent", flush=True)
    print(
        f"Per case: {estimate.frames_per_case} sampled frames "
        f"+ {estimate.anchor_frames_per_case} anchor frame(s), "
        f"{estimate.transfer_mib_per_case:.1f} MiB, "
        f"{estimate.requests_per_case} requests, "
        f"~{estimate.seconds_per_case:.0f}s",
        flush=True,
    )
    for count, scenario in payload["scenarios"].items():
        print(
            f"  {count:>4} cases -> {scenario['transfer_gib']} GiB transfer, "
            f"{scenario['requests']} requests, ~{scenario['wall_hours']} h, "
            f"{scenario['retained_mib']} MiB retained",
            flush=True,
        )
    if args.report:
        print(f"Wrote audit report to {_write_json(Path(args.report), payload)}")
    return 0


def build_catalog_command(args: argparse.Namespace) -> int:
    """Generate the 2015-2025 stratified case catalogue from NCEI outcomes."""

    from sharpmod.guidance.toi_catalog import (
        CatalogPlan,
        CatalogSummary,
        build_case_catalog,
        load_tornado_days,
        ncei_detail_urls,
        save_catalog,
    )

    plan = CatalogPlan(
        positive_cases=args.positive_cases,
        severe_cases=args.severe_cases,
        null_cases=args.null_cases,
        first_year=args.first_year,
        last_year=args.last_year,
    )
    print(DISCLAIMER, flush=True)
    if args.outcomes_dir:
        directory = Path(args.outcomes_dir).expanduser().resolve()
        # Match the documented NCEI name rather than splitting on "_d": the
        # literal "StormEvents_details" already contains "_d", so a naive split
        # parses "etai" as the year.  When NCEI has republished a year, keep the
        # newest creation date so a stale revision cannot silently win.
        pattern = re.compile(
            r"^StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d{8})\.csv(?:\.gz)?$"
        )
        newest: dict[int, tuple[str, Path]] = {}
        for path in sorted(directory.glob("StormEvents_details*")):
            match = pattern.match(path.name)
            if match is None:
                continue
            year, created = int(match.group(1)), match.group(2)
            current = newest.get(year)
            if current is None or created > current[0]:
                newest[year] = (created, path)
        urls = {
            year: path.as_uri() for year, (_created, path) in sorted(newest.items())
        }
        if not urls:
            raise GuidanceCommandError(
                f"no StormEvents_details files found in {directory}"
            )
        wanted = set(plan.years)
        missing = sorted(wanted.difference(urls))
        if missing:
            raise GuidanceCommandError(
                f"{directory} is missing StormEvents_details files for year(s): "
                + ",".join(str(year) for year in missing)
            )
        urls = {year: url for year, url in urls.items() if year in wanted}
        print(f"Using {len(urls)} local outcome file(s) from {directory}", flush=True)
    else:
        print(
            f"Resolving NCEI Storm Events detail files for {plan.years[0]}-"
            f"{plan.years[-1]} ...",
            flush=True,
        )
        urls = ncei_detail_urls(plan.years)
        for year, url in sorted(urls.items()):
            print(f"  {year}: {url.rsplit('/', 1)[-1]}", flush=True)

    days, sources = load_tornado_days(urls)
    payload = build_case_catalog(days, plan=plan, sources=sources)
    output = save_catalog(payload, args.output)
    summary = CatalogSummary.from_payload(payload)
    population = payload["catalog_population"]

    print(f"Wrote catalogue to {output}", flush=True)
    print(
        f"Observed tornado days {population['observed_tornado_days']} | "
        f"high-end {population['high_end_days']} | "
        f"ordinary severe {population['ordinary_severe_days']} | "
        f"null {population['null_days']}",
        flush=True,
    )
    print(
        f"Population base rate {population['population_base_rate']} | "
        f"catalogue {summary.total_cases} cases {dict(summary.counts)}",
        flush=True,
    )
    for dimension, counts in summary.strata.items():
        print(f"  {dimension}: {counts}", flush=True)
    return 0


def compile_dataset_command(args: argparse.Namespace) -> int:
    """Compile verified archive case files into a trainable dataset, offline."""

    from sharpmod.guidance.toi_compile import (
        compile_archive_dataset,
        compile_from_manifest_labels,
        resolve_work_dirs,
        save_compile_report,
    )

    print(DISCLAIMER, flush=True)
    work_dirs = resolve_work_dirs(args.archive_work_dir)
    print(
        f"Compiling from {len(work_dirs)} archive work director"
        f"{'y' if len(work_dirs) == 1 else 'ies'} "
        "(no network access; the archive collection is one-time)",
        flush=True,
    )
    for directory in work_dirs:
        print(f"  {directory}", flush=True)
    shared = {
        "dataset_kind": args.dataset_kind,
        "weighting": args.weights,
        "population_base_rate": args.population_base_rate,
        "require_verified": not args.allow_unverified,
        "notes": args.notes or "",
    }
    if args.catalog:
        # ``compile_from_manifest_labels`` performs the whole compile using the
        # manifest's own documented target, label source, and base rate, so it
        # replaces the plain call rather than supplying labels to it.
        print(f"Using documented labels from {args.catalog}", flush=True)
        dataset, report = compile_from_manifest_labels(
            work_dirs,
            args.catalog,
            label_source=args.label_source,
            **shared,
        )
    else:
        dataset, report = compile_archive_dataset(
            work_dirs,
            target_definition=args.target,
            label_source=args.label_source,
            **shared,
        )
    output = dataset.save_json(args.output)
    print(f"Wrote dataset to {output}", flush=True)
    print(
        f"Cases {len(dataset.rows)} | events {dataset.event_count} | "
        f"positives {dataset.positive_count} | years "
        + ",".join(str(year) for year in dataset.years)
        + f" | weighting {dataset.weighting}",
        flush=True,
    )
    print(f"Dataset hash {dataset.data_hash()[:16]}", flush=True)
    for skipped in dataset.skipped:
        print(
            f"  skipped {skipped.get('event_id')}: {skipped.get('reason')}",
            flush=True,
        )
    if args.report:
        print(f"Wrote compile report to {save_compile_report(report, args.report)}")
    if args.csv:
        print(f"Wrote CSV rows to {dataset.save_csv(args.csv)}", flush=True)
    return 0


def _load_cases(path: str | os.PathLike[str]):
    from sharpmod.guidance.toi_dataset import TOILabelManifest

    manifest = TOILabelManifest.load(path)
    return manifest, list(manifest.cases)


def _budget_from_args(args: argparse.Namespace):
    from sharpmod.guidance.toi_archive import RunBudget

    return RunBudget(
        maximum_transfer_bytes=int(args.max_transfer_gib * 1024**3),
        maximum_cases=args.max_cases,
        maximum_seconds=args.max_seconds,
        minimum_free_bytes=int(args.min_free_gib * 1024**3),
        maximum_concurrent_cases=1,
        minimum_request_interval_seconds=args.request_interval,
        discard_raw_after_extract=not args.keep_raw,
    )


def run_archive_command(args: argparse.Namespace) -> int:
    """Run (or resume) a bounded batch of archived case extractions."""

    from sharpmod.guidance.toi_archive import ArchiveRunner

    _manifest, cases = _load_cases(args.catalog)
    if args.classes:
        wanted = {name.strip() for name in args.classes.split(",") if name.strip()}
        cases = [case for case in cases if case.case_class in wanted]
    if args.limit:
        cases = cases[: int(args.limit)]
    budget = _budget_from_args(args)
    runner = ArchiveRunner(
        output_directory=args.work_dir,
        budget=budget,
        resolve_anchor=not args.no_resolve_anchor,
        fetcher=_load_callable(args.fetcher) if args.fetcher else None,
    )
    print(DISCLAIMER, flush=True)
    print(
        f"Cases queued {len(cases)} | budget {_gib(budget.maximum_transfer_bytes)} "
        f"transfer, {budget.maximum_cases} cases, {budget.maximum_seconds:.0f}s, "
        f"floor {_gib(budget.minimum_free_bytes)} free",
        flush=True,
    )
    already = runner.completed_keys()
    if already:
        print(f"Resuming: {len(already)} case(s) already checkpointed", flush=True)

    progress = None
    if not args.quiet:

        def progress(message: str) -> None:
            print(f"  {message}", flush=True)

    report = runner.run(cases, progress=progress, resume=not args.no_resume)
    summary = report.to_mapping()
    print(
        f"Stop reason: {summary['stop_reason']} | attempted "
        f"{summary['cases_attempted']} | success {summary['cases_succeeded']} | "
        f"skipped {summary['cases_skipped']} | failed {summary['cases_failed']}",
        flush=True,
    )
    print(
        f"Transferred {summary['transfer_mib']} MiB in {summary['seconds']}s "
        f"(per case: {summary['measured_mib_per_case']} MiB, "
        f"{summary['measured_seconds_per_case']}s)",
        flush=True,
    )
    for outcome in report.failed[:10]:
        print(f"  FAILED {outcome.event_id}: {outcome.reason[:140]}", flush=True)
    for outcome in report.skipped[:10]:
        print(f"  SKIPPED {outcome.event_id}: {outcome.reason[:140]}", flush=True)
    print(f"Run report: {Path(args.work_dir) / 'run-report.json'}", flush=True)
    if args.report:
        _write_json(Path(args.report), summary)
    return 0 if not report.failed or args.allow_failures else 1


def verify_archive_command(args: argparse.Namespace) -> int:
    """Verify extracted case files and summarize them into a manifest."""

    from sharpmod.guidance.toi_archive import ArchiveRunner

    runner = ArchiveRunner(output_directory=args.work_dir)
    manifest = runner.collect_manifest()
    print(DISCLAIMER, flush=True)
    # ``collect_manifest`` reports ``case_files``/``verified_cases``; the older
    # ``case_count`` key never existed, so this command used to raise KeyError
    # before printing anything.
    print(
        f"Extracted case files: {manifest['case_files']} in "
        f"{Path(args.work_dir) / 'cases'}",
        flush=True,
    )
    print(
        f"Verified cases: {manifest['verified_cases']}; "
        f"failures: {manifest['failure_count']}",
        flush=True,
    )
    checkpoint = runner.checkpoint_records()
    statuses: dict[str, int] = {}
    for record in checkpoint.values():
        status = str(record.get("status", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
    print(f"Checkpoint entries: {len(checkpoint)} {statuses}", flush=True)
    for failure in manifest["failures"][:10]:
        print(
            f"  FAIL {failure.get('check')}: {failure.get('path')} "
            f"- {failure.get('detail')}",
            flush=True,
        )
    if args.output:
        print(f"Wrote manifest to {_write_json(Path(args.output), manifest)}")
    # A verification command that cannot fail is not a verification command.
    return 0 if manifest["verified"] else 1


class GuidanceCommandError(RuntimeError):
    """A user-facing CLI failure with a clear, actionable message."""


def _load_callable(reference: str) -> Callable[..., Any]:
    """Import ``module:attribute`` for an injected archive fetcher."""

    module_name, separator, attribute = str(reference).partition(":")
    if not separator or not module_name or not attribute:
        raise GuidanceCommandError(
            "--fetcher must look like 'package.module:function'"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise GuidanceCommandError(f"could not import {module_name!r}: {exc}") from exc
    try:
        target = getattr(module, attribute)
    except AttributeError as exc:
        raise GuidanceCommandError(
            f"{module_name!r} has no attribute {attribute!r}"
        ) from exc
    if not callable(target):
        raise GuidanceCommandError(f"{reference} is not callable")
    return target


def _years(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    values: list[int] = []
    for token in str(raw).replace(" ", "").split(","):
        if not token:
            continue
        try:
            values.append(int(token))
        except ValueError as exc:
            raise GuidanceCommandError(
                f"year list must contain integers; got {token!r}"
            ) from exc
    return tuple(sorted(set(values)))


def _write_json(path: Path, payload: Any) -> Path:
    """Write a strict, portable JSON report (no NaN or Infinity tokens)."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(strict_json_dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_dataset_command(args: argparse.Namespace) -> int:
    manifest = TOILabelManifest.load(args.manifest)
    fetcher = _load_callable(args.fetcher) if args.fetcher else None
    progress = None
    if not args.quiet:

        def progress(message: str) -> None:
            print(f"  extracting {message}", flush=True)

    print(DISCLAIMER, flush=True)
    print(
        f"Target: {manifest.target_definition} "
        f"({TOI_TARGET_DEFINITIONS[manifest.target_definition]})",
        flush=True,
    )
    print(f"Dataset kind: {manifest.dataset_kind}", flush=True)
    dataset = build_toi_dataset(
        manifest,
        fetcher=fetcher,
        download_dir=args.download_dir,
        weighting=args.weights,
        require_complete_sampling=args.require_complete_sampling,
        sampling_interval_hours=args.sampling_interval_hours,
        progress=progress,
    )
    output = Path(args.output)
    dataset.save_json(output)
    print(
        f"Wrote {len(dataset.rows)} case rows across years "
        f"{','.join(str(year) for year in dataset.years)} to {output}",
        flush=True,
    )
    print(
        f"Events: {dataset.event_count} | positives: {dataset.positive_count} | "
        f"weighting: {dataset.weighting} | data hash: {dataset.data_hash()[:16]}",
        flush=True,
    )
    if args.csv:
        print(f"Wrote CSV rows to {dataset.save_csv(args.csv)}", flush=True)
    for skipped in dataset.skipped:
        print(
            f"  skipped {skipped.get('event_id')} "
            f"{skipped.get('run_time')}: {skipped.get('reason')}",
            flush=True,
        )
    if not dataset.is_multi_year:
        print(
            "WARNING: a single-year dataset cannot support blocked-year "
            "validation and can never promote a calibration.",
            flush=True,
        )
    return 0


def freeze_plan_command(args: argparse.Namespace) -> int:
    """Freeze the pre-registration before any held-out result is examined."""

    criteria = CRITERIA_PRESETS[args.criteria]()
    plan = TOIValidationPlan.recommended(
        plan_version=args.plan_version,
        target_definition=args.target,
        case_selection_rules=args.case_selection_rules,
        development_years=_years(args.development_years)
        or RECOMMENDED_DEVELOPMENT_YEARS,
        test_years=_years(args.test_years) or RECOMMENDED_TEST_YEARS,
        prospective_season=args.prospective_season,
        criteria=criteria,
        weighting=args.weights,
        l2_penalty=args.l2,
        validation_scheme=args.scheme,
        notes=args.notes or "",
    )
    output = Path(plan.save(args.output))
    print(DISCLAIMER, flush=True)
    print(f"Froze validation plan {plan.plan_version} to {output}", flush=True)
    print(f"Plan hash: {plan.plan_hash()}", flush=True)
    print(
        "Development years: "
        + ",".join(str(year) for year in plan.development_years)
        + " | untouched test years: "
        + ",".join(str(year) for year in plan.test_years),
        flush=True,
    )
    print(f"Prospective shadow season: {plan.prospective_season}", flush=True)
    if not criteria.scientific:
        print(
            "WARNING: these criteria are a non-scientific pipeline smoke gate "
            "and can never promote a calibration.",
            flush=True,
        )
    return 0


def train_command(args: argparse.Namespace) -> int:
    dataset = TOIDataset.load(args.dataset)
    plan = TOIValidationPlan.load(args.plan) if args.plan else None
    prospective = (
        TOIProspectiveRecord.load(args.prospective) if args.prospective else None
    )
    criteria = CRITERIA_PRESETS[args.criteria]() if args.criteria else None
    artifact, metrics = train_toi_calibrator(
        dataset,
        calibration_version=args.calibration_version,
        test_years=_years(args.test_years),
        l2_penalty=args.l2,
        scheme=args.scheme,
        threshold=args.threshold,
        bootstrap_samples=args.bootstrap,
        criteria=criteria,
        plan=plan,
        prospective=prospective,
    )
    output = Path(artifact.save(args.output))
    print(DISCLAIMER, flush=True)
    print(f"Wrote calibration artifact to {output}", flush=True)
    print(
        f"Calibration version: {artifact.calibration_version} | "
        f"training years: {artifact.calibration_years} | "
        f"base rate: {artifact.base_rate:.4f}",
        flush=True,
    )
    print("Runtime inference requires NumPy only (no scikit-learn).", flush=True)
    promotion = metrics.get("promotion", {})
    observed = promotion.get("observed", {})
    print(
        f"Promotion gate: {promotion.get('criteria_version')} "
        f"(scientific={promotion.get('criteria_are_scientific')}) | "
        f"event groups {observed.get('event_groups')} "
        f"(+{observed.get('positive_events')}/-{observed.get('negative_events')}) "
        f"| evaluated folds {observed.get('evaluated_folds')}",
        flush=True,
    )
    if artifact.validated:
        print(
            "Held-out validation PASSED the documented promotion criteria; the "
            "artifact may be selected explicitly.",
            flush=True,
        )
    else:
        print("Held-out validation did NOT promote this artifact:", flush=True)
        for blocker in metrics.get("validation_blockers", ()):
            print(f"  - {blocker}", flush=True)
        print(
            "The shipped public-anchor transform remains the default.",
            flush=True,
        )
    if args.report:
        print(f"Wrote training report to {_write_json(Path(args.report), metrics)}")
    return 0


def evaluate_command(args: argparse.Namespace) -> int:
    dataset = TOIDataset.load(args.dataset)
    artifact = (
        TOICalibrationArtifact.load(args.artifact) if args.artifact else None
    )
    if artifact is not None and artifact.data_hash != dataset.data_hash():
        print(
            "NOTE: the artifact was fitted on a different dataset hash; this is "
            "an out-of-sample evaluation.",
            flush=True,
        )
    report: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "dataset": {
            "target_definition": dataset.target_definition,
            "label_source": dataset.label_source,
            "dataset_kind": dataset.dataset_kind,
            "weighting": dataset.weighting,
            "years": list(dataset.years),
            "cases": len(dataset.rows),
            "events": dataset.event_count,
            "data_hash": dataset.data_hash(),
        },
        "full_sample": evaluate_dataset(
            dataset,
            artifact=artifact,
            threshold=args.threshold,
            bootstrap_samples=args.bootstrap,
        ),
    }
    if args.scheme:
        from sharpmod.guidance.toi_training import cross_validate

        try:
            report["blocked_validation"] = cross_validate(
                dataset,
                scheme=args.scheme,
                l2_penalty=args.l2,
                threshold=args.threshold,
            )
        except (TOIDatasetError, TOIEvaluationError) as exc:
            report["blocked_validation"] = {"scheme": args.scheme, "error": str(exc)}

    print(DISCLAIMER, flush=True)
    comparison = report["full_sample"]["comparison"]
    for name, values in comparison.items():
        print(
            f"{name}: Brier {values['brier_score']!r} | BSS "
            f"{values['brier_skill_score']!r} | POD {values['pod']!r} | FAR "
            f"{values['far']!r} | CSI {values['csi']!r} | bias "
            f"{values['frequency_bias']!r}",
            flush=True,
        )
    if args.report:
        print(f"Wrote evaluation report to {_write_json(Path(args.report), report)}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharpmod-guidance",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser(
        "audit-archive",
        help="report disk, caches, documented sources, and run estimates",
    )
    audit.add_argument("--work-dir", default="archive/toi", help="run directory")
    audit.add_argument("--forecast-hour", type=int, default=6)
    audit.add_argument(
        "--sampling-interval-hours", type=int, default=TOI_SAMPLING_INTERVAL_HOURS
    )
    audit.add_argument(
        "--seconds-per-frame",
        type=float,
        default=9.0,
        help="measured or assumed wall time per frame",
    )
    audit.add_argument(
        "--no-resolve-anchor",
        action="store_true",
        help="assume caller-supplied anchors instead of one extra anchor frame",
    )
    audit.add_argument("--cases", type=int, help="estimate one specific case count")
    audit.add_argument("--report", help="optional audit JSON path")
    audit.set_defaults(handler=audit_archive_command)

    catalog = subparsers.add_parser(
        "build-toi-catalog",
        help="generate the 2015-2025 stratified case catalogue from NCEI",
    )
    catalog.add_argument("--output", required=True, help="catalogue JSON path")
    catalog.add_argument("--first-year", type=int, default=2015)
    catalog.add_argument("--last-year", type=int, default=2025)
    catalog.add_argument("--positive-cases", type=int, default=60)
    catalog.add_argument("--severe-cases", type=int, default=240)
    catalog.add_argument("--null-cases", type=int, default=300)
    catalog.add_argument(
        "--outcomes-dir",
        help="use already-downloaded StormEvents_details files instead of NCEI",
    )
    catalog.set_defaults(handler=build_catalog_command)

    run = subparsers.add_parser(
        "run-toi-archive",
        help="run or resume a bounded batch of archived case extractions",
    )
    run.add_argument("--catalog", required=True, help="catalogue/manifest JSON")
    run.add_argument("--work-dir", required=True, help="resumable run directory")
    run.add_argument("--max-cases", type=int, default=12)
    run.add_argument("--max-transfer-gib", type=float, default=4.0)
    run.add_argument("--max-seconds", type=float, default=1800.0)
    run.add_argument("--min-free-gib", type=float, default=12.0)
    run.add_argument("--request-interval", type=float, default=0.2)
    run.add_argument("--limit", type=int, help="queue only the first N catalogue cases")
    run.add_argument("--classes", help="comma-separated case classes to include")
    run.add_argument(
        "--keep-raw",
        action="store_true",
        help="retain raw GRIB subsets (much larger disk use)",
    )
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--no-resolve-anchor", action="store_true")
    run.add_argument("--allow-failures", action="store_true")
    run.add_argument("--fetcher", help="module:function fetcher override for tests")
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--report", help="optional run-report JSON path")
    run.set_defaults(handler=run_archive_command)

    verify = subparsers.add_parser(
        "verify-toi-archive",
        help="verify extracted case files and emit a compact manifest",
    )
    verify.add_argument("--work-dir", required=True)
    verify.add_argument("--output", help="manifest JSON path")
    verify.set_defaults(handler=verify_archive_command)

    compile_parser = subparsers.add_parser(
        "compile-toi-dataset",
        help="compile verified archive output into a dataset without refetching",
    )
    compile_parser.add_argument(
        "--archive-work-dir",
        required=True,
        action="append",
        help=(
            "verified archive run directory; repeat for a sharded run, or pass "
            "a parent directory containing shard-* subdirectories"
        ),
    )
    compile_parser.add_argument("--output", required=True, help="dataset JSON path")
    compile_parser.add_argument("--csv", help="optional dataset CSV path")
    compile_parser.add_argument(
        "--catalog",
        help="catalogue JSON supplying documented manifest labels and weights",
    )
    compile_parser.add_argument(
        "--target",
        default="high_risk_worthy_proxy_v1",
        choices=sorted(TOI_TARGET_DEFINITIONS),
    )
    compile_parser.add_argument(
        "--label-source",
        required=True,
        help="exact provenance of the outcomes (export name and version)",
    )
    compile_parser.add_argument(
        "--dataset-kind",
        default="historical",
        choices=("historical", "synthetic-fixture"),
    )
    compile_parser.add_argument(
        "--weights", choices=("natural", "population"), default="natural"
    )
    compile_parser.add_argument("--population-base-rate", type=float)
    compile_parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="compile even if verification failed (not recommended)",
    )
    compile_parser.add_argument("--notes")
    compile_parser.add_argument("--report", help="optional compile-report JSON path")
    compile_parser.set_defaults(handler=compile_dataset_command)

    build = subparsers.add_parser(
        "build-toi-dataset",
        help="extract historical TOI cases with the operational feature code",
    )
    build.add_argument("--manifest", required=True, help="documented label manifest")
    build.add_argument("--output", required=True, help="dataset JSON output path")
    build.add_argument("--csv", help="optional dataset CSV output path")
    build.add_argument(
        "--weights",
        choices=("natural", "population"),
        default="natural",
        help="preserve the sampled frequency, or reweight to a documented rate",
    )
    build.add_argument(
        "--fetcher",
        help="module:function override for the archive frame fetcher",
    )
    build.add_argument("--download-dir", help="reusable GRIB download directory")
    build.add_argument(
        "--sampling-interval-hours",
        type=int,
        default=TOI_SAMPLING_INTERVAL_HOURS,
        help="temporal sampling interval shared with the live producer",
    )
    build.add_argument(
        "--require-complete-sampling",
        action="store_true",
        help="drop cases whose temporal sampling was degraded",
    )
    build.add_argument("--quiet", action="store_true", help="suppress per-case output")
    build.set_defaults(handler=build_dataset_command)

    freeze = subparsers.add_parser(
        "freeze-toi-plan",
        help="freeze the pre-registration before looking at held-out results",
    )
    freeze.add_argument("--output", required=True, help="plan JSON output path")
    freeze.add_argument(
        "--plan-version", default="toi_hrrr_2015_2022_v1", help="auditable plan name"
    )
    freeze.add_argument(
        "--target",
        default="high_risk_worthy_proxy_v1",
        choices=sorted(TOI_TARGET_DEFINITIONS),
        help="frozen outcome definition",
    )
    freeze.add_argument(
        "--case-selection-rules",
        default=TOIValidationPlan.recommended().case_selection_rules,
        help="how cases were chosen, frozen for audit",
    )
    freeze.add_argument(
        "--development-years",
        help=(
            "comma-separated chronological development years (default "
            + ",".join(str(year) for year in RECOMMENDED_DEVELOPMENT_YEARS)
            + ")"
        ),
    )
    freeze.add_argument(
        "--test-years",
        help=(
            "comma-separated untouched test years (default "
            + ",".join(str(year) for year in RECOMMENDED_TEST_YEARS)
            + ")"
        ),
    )
    freeze.add_argument(
        "--prospective-season",
        default="next full spring severe-weather season",
        help="the future season reserved for shadow validation",
    )
    freeze.add_argument(
        "--criteria", choices=sorted(CRITERIA_PRESETS), default="research-target"
    )
    freeze.add_argument(
        "--weights", choices=("natural", "population"), default="population"
    )
    freeze.add_argument("--l2", type=float, default=1.0)
    freeze.add_argument(
        "--scheme", choices=VALIDATION_SCHEMES, default="expanding-year"
    )
    freeze.add_argument("--notes", help="optional free-text context")
    freeze.set_defaults(handler=freeze_plan_command)

    train = subparsers.add_parser(
        "train-toi", help="fit the regularized logistic TOI calibrator"
    )
    train.add_argument("--dataset", required=True, help="dataset JSON from build")
    train.add_argument("--output", required=True, help="artifact JSON output path")
    train.add_argument(
        "--calibration-version",
        required=True,
        help="explicit, auditable name for the fitted calibration",
    )
    train.add_argument(
        "--plan",
        help=(
            "frozen validation plan JSON; supplies the split, penalty, scheme, "
            "and promotion criteria, and is required by the research gate"
        ),
    )
    train.add_argument(
        "--criteria",
        choices=sorted(CRITERIA_PRESETS),
        help=(
            "override the promotion gate; defaults to the plan's criteria, "
            "else research-target"
        ),
    )
    train.add_argument(
        "--prospective",
        help="prospective shadow-validation record JSON for a reserved season",
    )
    train.add_argument(
        "--test-years",
        help="comma-separated years held out and never used for fitting",
    )
    train.add_argument("--l2", type=float, default=1.0, help="ridge penalty strength")
    train.add_argument(
        "--scheme", choices=VALIDATION_SCHEMES, default="leave-one-year-out"
    )
    train.add_argument("--threshold", type=float, default=DEFAULT_DECISION_THRESHOLD)
    train.add_argument(
        "--bootstrap",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
        help="event-blocked bootstrap resamples for held-out intervals",
    )
    train.add_argument("--report", help="optional training-report JSON path")
    train.set_defaults(handler=train_command)

    evaluate = subparsers.add_parser(
        "evaluate-toi",
        help="verify the shipped transform, climatology, and one artifact",
    )
    evaluate.add_argument("--dataset", required=True, help="dataset JSON from build")
    evaluate.add_argument("--artifact", help="calibration artifact JSON to score")
    evaluate.add_argument(
        "--scheme",
        choices=VALIDATION_SCHEMES,
        help="also run blocked-year cross-validation with this scheme",
    )
    evaluate.add_argument("--l2", type=float, default=1.0)
    evaluate.add_argument("--threshold", type=float, default=DEFAULT_DECISION_THRESHOLD)
    evaluate.add_argument(
        "--bootstrap", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES
    )
    evaluate.add_argument("--report", help="optional evaluation-report JSON path")
    evaluate.set_defaults(handler=evaluate_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        GuidanceCommandError,
        TOICalibrationError,
        TOIDatasetError,
        TOIEvaluationError,
        TOIValidationError,
    ) as exc:
        print(f"sharpmod-guidance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
