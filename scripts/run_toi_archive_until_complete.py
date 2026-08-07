#!/usr/bin/env python
"""Drive resume passes of the parallel TOI archive runner until it is done.

Each shard carries its own per-run budget (4 GiB of transfer and 1800 s by
default), so one invocation of ``run_toi_archive_parallel.py`` stops well short of
a 600-case catalogue with ``time_budget_reached``.  Resume is designed for exactly
this: a rerun skips checkpointed cases and continues.

Rather than leaving that to be run by hand an unknown number of times, this driver
loops until every catalogue case is checkpointed, or until a pass makes no
progress at all - which means the remaining cases are genuinely stuck rather than
merely out of budget, and looping further would only burn requests.

Usage::

    python scripts/run_toi_archive_until_complete.py \\
        --shard-dir archive/shards --work-root archive/toi \\
        --report reports/parallel-timing-v4.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def catalogue_case_count(shard_dir: Path) -> int:
    total = 0
    for shard in sorted(shard_dir.glob("shard-*.json")):
        if shard.name.startswith("shard-summary"):
            continue
        try:
            payload = json.loads(shard.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cases = payload.get("cases")
        if isinstance(cases, list):
            total += len(cases)
    return total


def checkpointed_count(work_root: Path) -> tuple[int, dict[str, int]]:
    """Return (unique checkpointed cases, per-status counts)."""

    keys: set[str] = set()
    statuses: dict[str, int] = {}
    for checkpoint in sorted(work_root.glob("shard-*/checkpoint.jsonl")):
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = record.get("cache_key")
            if not key or key in keys:
                continue
            keys.add(str(key))
            status = str(record.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
    return len(keys), statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-dir", default="archive/shards")
    parser.add_argument("--work-root", default="archive/toi")
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--max-passes",
        type=int,
        default=20,
        help="hard stop so a stuck catalogue cannot loop forever",
    )
    args = parser.parse_args(argv)

    shard_dir = Path(args.shard_dir)
    work_root = Path(args.work_root)
    expected = catalogue_case_count(shard_dir)
    if expected <= 0:
        raise SystemExit(f"no catalogue cases found in {shard_dir}")

    began = time.monotonic()
    passes: list[dict[str, object]] = []
    done, statuses = checkpointed_count(work_root)
    print(f"catalogue holds {expected} case(s); {done} already checkpointed")

    for index in range(1, int(args.max_passes) + 1):
        if done >= expected:
            break
        before = done
        started = time.monotonic()
        print(
            f"\n=== pass {index}: {done}/{expected} done, "
            f"{datetime.now(UTC).isoformat(timespec='seconds')} ==="
        )
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "scripts/run_toi_archive_parallel.py",
                "--shard-dir",
                str(shard_dir),
                "--work-root",
                str(work_root),
                "--allow-failures",
            ],
            cwd=str(REPO_ROOT),
            check=False,
        )
        done, statuses = checkpointed_count(work_root)
        elapsed = time.monotonic() - started
        gained = done - before
        passes.append(
            {
                "pass": index,
                "exit_code": completed.returncode,
                "minutes": round(elapsed / 60.0, 1),
                "checkpointed_after": done,
                "gained": gained,
            }
        )
        print(
            f"pass {index} added {gained} case(s) in {elapsed / 60:.1f} min; "
            f"{done}/{expected} checkpointed"
        )
        if gained <= 0:
            # No budget was the reason to loop; no progress means something else
            # is wrong, and another identical pass cannot fix it.
            print("no progress in this pass; stopping rather than retrying blindly")
            break

    wall = time.monotonic() - began
    summary = {
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "catalogue_cases": expected,
        "checkpointed_cases": done,
        "complete": done >= expected,
        "status_counts": statuses,
        "passes": passes,
        "wall_hours": round(wall / 3600.0, 3),
    }
    print(
        f"\n{done}/{expected} checkpointed in {len(passes)} pass(es), "
        f"{wall / 3600:.2f} h wall"
    )
    print(f"status counts: {statuses}")
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")
    return 0 if done >= expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
