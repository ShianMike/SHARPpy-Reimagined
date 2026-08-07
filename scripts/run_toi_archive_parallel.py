#!/usr/bin/env python
"""Run TOI archive shards as isolated parallel processes.

Why processes rather than threads: the sequential runner already guarantees
correctness through a single-writer ``run.lock``, atomic ``.partial`` writes, and
a per-run JSONL checkpoint.  Threading inside one runner would put every one of
those guarantees onto shared mutable state.  Sharding instead gives each worker
its own catalogue, work directory, checkpoint, and lock, so N workers are simply
N independent proven-correct runs.  Event-indivisible sharding means no event is
split across workers, so downstream year/event-blocked validation is unaffected.

Each shard keeps its own request interval, so total request concurrency is
``--workers`` streams against the NOAA Open Data bucket on S3, which is built for
concurrent access.  Budgets are **per shard**, not global: ``--max-cases 3``
means three cases in each shard.

Usage::

    # measure the speedup on a small bounded sample first
    python scripts/run_toi_archive_parallel.py --shard-dir archive/shards \\
        --work-root archive/toi --max-cases 3 --max-transfer-gib 0.5

    # then the full run
    python scripts/run_toi_archive_parallel.py --shard-dir archive/shards \\
        --work-root archive/toi

Re-running resumes: completed cases are skipped from each shard's checkpoint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Worker:
    """One running shard process and the log handle it writes to."""

    shard: Path
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: TextIO
    began: float


def discover_shards(shard_dir: Path) -> list[Path]:
    shards = sorted(
        path
        for path in shard_dir.glob("shard-*.json")
        if not path.name.startswith("shard-summary")
    )
    if not shards:
        raise SystemExit(f"no shard catalogues found in {shard_dir}")
    return shards


def shard_case_count(shard: Path) -> int:
    try:
        payload = json.loads(shard.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    cases = payload.get("cases")
    return len(cases) if isinstance(cases, list) else 0


def build_command(
    shard: Path, work_dir: Path, args: argparse.Namespace
) -> list[str]:
    # The runner's own case budget defaults to 12, so leaving --max-cases unset
    # silently stops each shard after 12 cases with ``case_budget_reached``.
    # An unset limit here must mean "the whole shard", not "the runner default".
    max_cases = args.max_cases
    if max_cases is None:
        max_cases = max(1, shard_case_count(shard))
    command = [
        sys.executable,
        "-m",
        "sharpmod.tools.guidance_cli",
        "run-toi-archive",
        "--catalog",
        str(shard),
        "--work-dir",
        str(work_dir),
        "--report",
        str(work_dir / "run-report.json"),
        "--request-interval",
        str(args.request_interval),
        "--min-free-gib",
        str(args.min_free_gib),
        "--quiet",
    ]
    command += ["--max-cases", str(int(max_cases))]
    if args.max_transfer_gib is not None:
        command += ["--max-transfer-gib", str(args.max_transfer_gib)]
    if args.max_seconds is not None:
        command += ["--max-seconds", str(args.max_seconds)]
    if args.allow_failures:
        command.append("--allow-failures")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-dir", default="archive/shards")
    parser.add_argument("--work-root", default="archive/toi")
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="max concurrent shards; 0 runs every shard at once",
    )
    parser.add_argument("--max-cases", type=int, default=None,
                        help="per shard, not global; default is the whole shard")
    parser.add_argument("--max-transfer-gib", type=float, default=None,
                        help="per shard, not global")
    parser.add_argument("--max-seconds", type=int, default=None,
                        help="per shard, not global")
    parser.add_argument("--request-interval", type=float, default=0.2)
    parser.add_argument("--min-free-gib", type=float, default=20.0)
    parser.add_argument("--allow-failures", action="store_true")
    parser.add_argument("--report", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    shard_dir = Path(args.shard_dir)
    work_root = Path(args.work_root)
    shards = discover_shards(shard_dir)
    limit = args.workers if args.workers > 0 else len(shards)

    print(f"{len(shards)} shard(s), up to {limit} running concurrently")
    for shard in shards:
        print(f"  {shard.name} -> {work_root / shard.stem}")
    if args.dry_run:
        print("\nDRY RUN: no process was started. Commands:")
        for shard in shards:
            command = build_command(shard, work_root / shard.stem, args)
            print("  " + " ".join(command))
        return 0

    started = time.monotonic()
    pending = list(shards)
    # The log handle must outlive the launch call because the child writes to it
    # for its whole lifetime, so a `with` block cannot own it.  It is carried
    # alongside the process and closed when that process exits.
    running: list[_Worker] = []
    finished: list[dict[str, object]] = []

    try:
        while pending or running:
            while pending and len(running) < limit:
                shard = pending.pop(0)
                work_dir = work_root / shard.stem
                work_dir.mkdir(parents=True, exist_ok=True)
                log_path = work_dir / "worker.log"
                handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
                try:
                    handle.write(
                        f"\n=== "
                        f"{datetime.now(UTC).isoformat(timespec='seconds')} ===\n"
                    )
                    handle.flush()
                    process = subprocess.Popen(  # noqa: S603
                        build_command(shard, work_dir, args),
                        cwd=str(REPO_ROOT),
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                except BaseException:
                    handle.close()
                    raise
                running.append(
                    _Worker(shard, process, log_path, handle, time.monotonic())
                )
                print(f"started {shard.name} (pid {process.pid})")

            time.sleep(2.0)
            for worker in list(running):
                code = worker.process.poll()
                if code is None:
                    continue
                running.remove(worker)
                worker.log_handle.close()
                elapsed = time.monotonic() - worker.began
                status = "ok" if code == 0 else f"exit {code}"
                print(
                    f"finished {worker.shard.name}: {status} "
                    f"in {elapsed / 60:.1f} min"
                )
                finished.append(
                    {
                        "shard": worker.shard.name,
                        "exit_code": code,
                        "seconds": round(elapsed, 1),
                        "log": str(worker.log_path),
                    }
                )
    except KeyboardInterrupt:
        print("\ninterrupted; terminating workers (each run is resumable)")
        for worker in running:
            worker.process.terminate()
        raise
    finally:
        for worker in running:
            worker.log_handle.close()

    wall = time.monotonic() - started
    failures = [item for item in finished if item["exit_code"] != 0]

    cases = 0
    transferred = 0.0
    for shard in shards:
        report = work_root / shard.stem / "run-report.json"
        if not report.exists():
            continue
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        totals = payload.get("totals") or payload
        for key in ("succeeded", "success", "cases_succeeded"):
            if isinstance(totals.get(key), int):
                cases += totals[key]
                break
        for key in ("transfer_mib", "transferred_mib", "total_transfer_mib"):
            value = totals.get(key)
            if isinstance(value, (int, float)):
                transferred += float(value)
                break

    summary = {
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "shards": finished,
        "wall_seconds": round(wall, 1),
        "wall_hours": round(wall / 3600.0, 3),
        "concurrency": limit,
        "succeeded_cases": cases,
        "transfer_mib": round(transferred, 1),
        "sequential_equivalent_hours": (
            round(sum(float(i["seconds"]) for i in finished) / 3600.0, 3)
        ),
        "failures": [item["shard"] for item in failures],
    }
    print(
        f"\nwall {wall / 60:.1f} min across {limit} worker(s); "
        f"summed shard time {summary['sequential_equivalent_hours']:.2f} h"
    )
    if cases:
        print(f"succeeded cases {cases}, transfer {transferred:.0f} MiB")
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")
    if failures:
        print(f"FAILED shard(s): {', '.join(summary['failures'])}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
