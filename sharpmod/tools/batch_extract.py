"""CLI for resumable multi-point/multi-hour forecast extraction."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument(
        "--json-progress",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cache-max-bytes",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cache-max-age-hours",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def _progress(event) -> None:
    kind = event.get("event")
    request_id = event.get("request_id")
    if kind in {"running", "completed", "failed", "cancelled"}:
        suffix = f" {request_id}" if request_id else ""
        print(f"{kind}{suffix}")


def _json_progress(event) -> None:
    """Emit a framed JSON event for the desktop process boundary."""
    print(
        "SHARPMOD_EVENT " + json.dumps(event, separators=(",", ":"), default=str),
        flush=True,
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    hour_cache = None
    try:
        if args.cache_root is not None:
            from sharpmod.model_disk_cache import ModelDiskCache
            from sharpmod.model_hour_cache import ModelHourCache

            disk_cache = ModelDiskCache(
                args.cache_root,
                max_bytes=args.cache_max_bytes,
                max_age_hours=args.cache_max_age_hours,
            )
            hour_cache = ModelHourCache(
                max_entries=args.workers,
                directory_factory=disk_cache.directory_for,
                directory_protector=disk_cache.protect,
                metadata_writer=disk_cache.annotate,
                delete_download_dirs=False,
            )
        requests = load_batch_spec(args.spec)
        extractor = BatchExtractor(
            progress_callback=(
                _json_progress
                if args.json_progress
                else (None if args.quiet else _progress)
            ),
        )
        result = extractor.run(
            requests,
            output_dir=args.output_dir,
            manifest_path=args.manifest,
            max_workers=args.workers,
            resume=not args.no_resume,
            model_hour_cache=hour_cache,
        )
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except BatchExtractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if hour_cache is not None:
            hour_cache.clear()

    print(
        f"completed={result.completed} failed={result.failed} "
        f"cancelled={result.cancelled} resumed={result.skipped}"
    )
    print(f"manifest={result.manifest_path}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
