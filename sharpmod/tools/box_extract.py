"""CLI for area ("box") forecast soundings and their derived-parameter fields.

Samples a lat/lon rectangle on the model's own grid, extracts every point from a
single model-hour download, and reports the resulting scalar fields. The whole
pipeline is the Qt-independent core, so this and the desktop workspace cannot
disagree about what a box contains.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from sharpmod.box_analysis import (
    COMPOSITE_TIER,
    FAST_TIER,
    PARAMETERS,
    BoxAnalysisError,
    analyze_box,
    analyze_box_sequence,
    describe_criteria,
    parameter,
    screen,
    screen_names,
)
from sharpmod.box_export import write_box_csv, write_box_geojson
from sharpmod.box_sounding import (
    MAX_BOX_HOURS,
    MAX_BOX_POINTS,
    BoxRegion,
    BoxSoundingError,
    box_requests,
    box_sequence_requests,
    describe_plan,
    normalize_hours,
    plan_box_samples,
)


def _valid_time(value):
    """Parse an ISO 8601 run time, assuming UTC when no offset is given."""
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid run time {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="box-extract",
        description=(
            "Extract every forecast-model grid point inside a lat/lon box from "
            "one download, then report derived-parameter fields across it."
        ),
    )
    parser.add_argument("model", nargs="?", help="model key, e.g. hrrr")
    parser.add_argument(
        "lat0", nargs="?", type=float, help="one corner latitude")
    parser.add_argument(
        "lon0", nargs="?", type=float, help="one corner longitude")
    parser.add_argument(
        "lat1", nargs="?", type=float, help="opposite corner latitude")
    parser.add_argument(
        "lon1", nargs="?", type=float, help="opposite corner longitude")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="directory for the extracted .npz soundings (required unless "
             "--dry-run or --list-fields)",
    )
    parser.add_argument(
        "--run", type=_valid_time, default=None,
        help="model run/cycle in ISO 8601 (default: most recent published)",
    )
    parser.add_argument("--fxx", type=int, default=0, help="forecast hour")
    parser.add_argument(
        "--hours", type=int, default=1, metavar=f"1-{MAX_BOX_HOURS}",
        help=(
            "sample this many forecast hours starting at --fxx, so the field "
            "can be compared across time (each hour is its own download)"
        ),
    )
    parser.add_argument(
        "--hour-step", type=int, default=1, metavar="H",
        help="spacing between sampled forecast hours (default: 1)",
    )
    parser.add_argument("--member", default=None, help="ensemble member")
    parser.add_argument(
        "--loc", default=None, help="location label prefix for each point")
    sampling = parser.add_mutually_exclusive_group()
    sampling.add_argument(
        "--target-points", type=int, default=None,
        help="aim for roughly this many soundings",
    )
    sampling.add_argument(
        "--spacing-km", type=float, default=None,
        help="request this sample spacing; rounded up to the model grid",
    )
    parser.add_argument(
        "--max-points", type=int, default=None,
        metavar=f"1-{MAX_BOX_POINTS}",
        help=f"hard cap on sampled points (ceiling {MAX_BOX_POINTS})",
    )
    parser.add_argument(
        "--composites", action="store_true",
        help="also compute the SPC composite indices (about 0.4 s per point)",
    )
    parser.add_argument(
        "--field", action="append", default=None, metavar="KEY",
        help="print this field as a grid; repeatable",
    )
    parser.add_argument(
        "--csv", type=Path, default=None,
        help="write per-point values to a CSV file",
    )
    parser.add_argument(
        "--geojson", type=Path, default=None,
        help=(
            "write the sampled cells to GeoJSON for GIS, one cell-shaped "
            "feature per sounding plus the box outline"
        ),
    )
    parser.add_argument(
        "--screen", default=None, metavar="NAME",
        help=(
            "report how much of the box satisfies a named ingredient screen, "
            "and tag exported rows/features with the verdict "
            "(see --list-screens)"
        ),
    )
    parser.add_argument(
        "--list-screens", action="store_true",
        help="list the available ingredient screens and exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="resolve and print the sample plan without downloading",
    )
    parser.add_argument(
        "--list-fields", action="store_true",
        help="list every available field key and exit",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="re-extract every point instead of reusing valid artifacts",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only print the final summary")
    return parser


def _print_fields() -> int:
    groups: dict[str, list] = {}
    for item in PARAMETERS:
        groups.setdefault(item.group, []).append(item)
    for group, items in groups.items():
        print(f"\n{group}")
        for item in items:
            units = f" [{item.units}]" if item.units else ""
            marker = "*" if item.tier == COMPOSITE_TIER else " "
            print(f"  {marker} {item.key:18s} {item.label}{units}")
    print("\n* needs --composites")
    return 0


def _print_grid(analysis, key) -> None:
    item = parameter(key)
    units = f" [{item.units}]" if item.units else ""
    print(f"\n{item.label}{units}   (row 0 = north edge)")
    for row in analysis.field(item.key):
        cells = [
            item.format(value).rjust(9) if value is not None
            else "".rjust(9)
            for value in row
        ]
        print("  " + " ".join(cells))


def _sequence_hours(plan, run_dt, args) -> tuple[int, ...] | None:
    """Resolve the forecast hours to sample, or ``None`` after reporting why not.

    Hours are taken from the model's own published cadence rather than a bare
    arithmetic series, so a request cannot ask for an hour the product does not
    publish.
    """
    from sharpmod.tools import model_extract

    count = max(1, int(args.hours))
    if count == 1:
        return (int(args.fxx),)
    step = max(1, int(args.hour_step))
    published = model_extract.forecast_hours(plan.model_key, run_dt.hour)
    later = [
        hour for hour in sorted(published)
        if hour >= int(args.fxx) and (hour - int(args.fxx)) % step == 0
    ]
    chosen = tuple(later[:count])
    if len(chosen) < 2:
        print(
            f"ERROR: {plan.model_label} publishes no further forecast hour at "
            f"or after F{int(args.fxx):03d} with a {step}-hour step",
            file=sys.stderr,
        )
        return None
    try:
        return normalize_hours(chosen)
    except BoxSoundingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None





def _print_screens() -> int:
    print(
        "Ingredient screens: sets of thresholds that must hold together.\n"
        "These narrow attention; they are not official products or forecasts.\n"
    )
    for name in screen_names():
        print(f"  {name}")
        print(f"      {describe_criteria(screen(name))}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.list_fields:
        return _print_fields()
    if args.list_screens:
        return _print_screens()

    required = (args.model, args.lat0, args.lon0, args.lat1, args.lon1)
    if any(value is None for value in required):
        print(
            "ERROR: model and four box corners are required "
            "(see --help, or --list-fields)",
            file=sys.stderr,
        )
        return 2

    try:
        region = BoxRegion.from_corners(
            args.lat0, args.lon0, args.lat1, args.lon1)
        plan = plan_box_samples(
            args.model, region,
            spacing_km=args.spacing_km,
            target_points=args.target_points,
            max_points=args.max_points,
        )
    except BoxSoundingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        # KeyError's str() re-quotes its argument, which would double the
        # quoting in an already-complete message.
        message = exc.args[0] if exc.args else exc
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    print(describe_plan(plan))
    if args.dry_run:
        return 0

    if args.output_dir is None:
        print(
            "ERROR: --output-dir is required unless --dry-run is used",
            file=sys.stderr,
        )
        return 2

    from sharpmod.batch_extract import BatchExtractError, BatchExtractor
    from sharpmod.tools import model_extract

    config = model_extract.get_config(plan.model_key)
    # Resolve the cycle exactly the way batch_extract does, so an omitted --run
    # means the same thing here as it does everywhere else.
    run_dt = model_extract._run_datetime(args.run, config)

    def progress(event) -> None:
        kind = event.get("event")
        if kind in {"completed", "failed", "cancelled"}:
            request_id = event.get("request_id") or ""
            print(f"{kind} {request_id}")

    hours = _sequence_hours(plan, run_dt, args)
    if hours is None:
        return 2
    try:
        if len(hours) == 1:
            requests = box_requests(
                plan, run_time=run_dt, fxx=hours[0],
                member=args.member, loc=args.loc,
            )
        else:
            requests = box_sequence_requests(
                plan, run_time=run_dt, hours=hours,
                member=args.member, loc=args.loc,
            )
            print(
                f"Hours      {len(hours)} "
                f"(F{hours[0]:03d} to F{hours[-1]:03d}), "
                f"{len(requests)} soundings, {len(hours)} downloads"
            )
        extractor = BatchExtractor(
            progress_callback=None if args.quiet else progress)
        result = extractor.run(
            requests,
            output_dir=args.output_dir,
            # One worker per concurrently held model hour: a single-hour box
            # gains nothing from more, and a sequence is capped at two so it
            # cannot hold several large field subsets at once.
            max_workers=1 if len(hours) == 1 else 2,
            resume=not args.no_resume,
        )
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except (BatchExtractError, BoxSoundingError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Requests carry an hour-prefixed id for a sequence, so file each completed
    # artifact back under its own hour keyed by the plain lattice id.
    by_id = {item.id: item for item in requests}
    outputs_by_hour: dict[int, dict[str, str]] = {
        int(hour): {} for hour in hours
    }
    for item in result.items:
        if item.status != "completed":
            continue
        request = by_id.get(item.id)
        if request is None:
            continue
        node_id = (
            request.output.replace("\\", "/").rsplit("/", 1)[-1][:-4]
        )
        outputs_by_hour.setdefault(int(request.fxx), {})[node_id] = str(
            item.output_path)

    print(
        f"\ncompleted={result.completed} failed={result.failed} "
        f"cancelled={result.cancelled} resumed={result.skipped}"
    )
    print(f"manifest={result.manifest_path}")
    if not any(outputs_by_hour.values()):
        print("ERROR: no point in the box could be extracted", file=sys.stderr)
        return 1

    tiers = (FAST_TIER, COMPOSITE_TIER) if args.composites else (FAST_TIER,)
    try:
        if len(hours) == 1:
            analysis = analyze_box(
                plan, outputs_by_hour[hours[0]], tiers=tiers,
                run_time=run_dt, fxx=hours[0])
            sequence = None
        else:
            sequence = analyze_box_sequence(
                plan, outputs_by_hour, tiers=tiers, run_time=run_dt)
            # Report the peak hour's field values, since that is the hour a
            # reader would otherwise have to hunt for.
            analysis = sequence.at(sequence.hours[-1])
    except BoxAnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if sequence is not None:
        for key in (args.field or ["mucape"]):
            try:
                print()
                print(sequence.summary(key))
            except BoxAnalysisError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        peak = sequence.peak_hour(args.field[0] if args.field else "mucape")
        if peak is not None:
            analysis = sequence.at(peak)
            print(f"\nFields below are for the peak hour, F{peak:03d}.")

    print()
    print(analysis.summary())
    for key in args.field or ():
        try:
            _print_grid(analysis, key)
        except BoxAnalysisError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    # Coverage is reported per hour for a sequence, since "when" is the point.
    if args.screen:
        try:
            if sequence is not None:
                print()
                print(f"Ingredient overlap: {args.screen}")
                for hour, coverage in sequence.coverage_series(args.screen):
                    text = (
                        "no data" if coverage is None else coverage.describe()
                    )
                    print(f"  F{hour:03d}  {text}")
            else:
                coverage = analysis.coverage(args.screen)
                print()
                print(f"Ingredient overlap: {args.screen}")
                print(f"  {describe_criteria(coverage.criteria)}")
                print(f"  {coverage.describe()}")
        except BoxAnalysisError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    # Exports carry the whole sequence when there is one, so a single file holds
    # every hour rather than the reader having to stitch them together.
    exported = sequence if sequence is not None else analysis
    if args.csv is not None:
        rows = write_box_csv(exported, args.csv, criteria=args.screen)
        print(f"\ncsv={args.csv} rows={rows}")
    if args.geojson is not None:
        features = write_box_geojson(
            exported, args.geojson, criteria=args.screen)
        print(f"geojson={args.geojson} features={features}")
    if analysis.failures:
        print(f"\n{len(analysis.failures)} point(s) produced no values")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
