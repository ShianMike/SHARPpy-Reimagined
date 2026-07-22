"""CLI for resumable multi-point/multi-hour forecast extraction."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sharpmod.batch_extract import (
    MAX_CONCURRENCY,
    BatchExtractError,
    BatchExtractor,
    load_batch_spec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model-batch-extract",
        description=(
            "Extract heterogeneous forecast-model points/hours from a JSON "
            "job. Requests sharing a model hour reuse one download."
        ),
    )
    parser.add_argument("spec", type=Path, help="version-1 JSON job spec")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="root for relative per-request output paths",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="manifest path (default: OUTPUT_DIR/batch-manifest.json)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, MAX_CONCURRENCY + 1),
        default=2,
        metavar=f"1-{MAX_CONCURRENCY}",
        help="concurrent model-hour downloads (default: 2)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="rerun every request instead of validating completed artifacts",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only print the final summary"
    )
    return parser


def _progress(event) -> None:
    kind = event.get("event")
    request_id = event.get("request_id")
    if kind in {"running", "completed", "failed", "cancelled"}:
        suffix = f" {request_id}" if request_id else ""
        print(f"{kind}{suffix}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        requests = load_batch_spec(args.spec)
        extractor = BatchExtractor(
            progress_callback=None if args.quiet else _progress
        )
        result = extractor.run(
            requests,
            output_dir=args.output_dir,
            manifest_path=args.manifest,
            max_workers=args.workers,
            resume=not args.no_resume,
        )
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except BatchExtractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"completed={result.completed} failed={result.failed} "
        f"cancelled={result.cancelled} resumed={result.skipped}"
    )
    print(f"manifest={result.manifest_path}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
