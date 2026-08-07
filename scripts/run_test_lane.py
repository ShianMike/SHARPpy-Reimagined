#!/usr/bin/env python3
"""Run a documented SHARPpy Reimagined test lane and check its timing budget."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from . import check_test_performance as performance
else:
    import check_test_performance as performance


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / ".test-results"


@dataclass(frozen=True)
class Lane:
    """Stable execution policy for one local/CI test lane."""

    marker: str | None
    hypothesis_profile: str
    parallel: bool
    timeout_seconds: int
    distribution: str = "loadgroup"
    targets: tuple[str, ...] = ()
    live_providers: bool = False


LANES = {
    # Full deterministic coverage on the newest supported interpreter.
    "fast": Lane(
        marker="not property and not live_provider",
        hypothesis_profile="fast",
        parallel=True,
        timeout_seconds=300,
    ),
    # Cheap all-offline smoke, including ten Hypothesis examples, on 3.11/3.12.
    "compatibility": Lane(
        marker="not live_provider",
        hypothesis_profile="fast",
        parallel=True,
        timeout_seconds=300,
    ),
    # The complete >=100-example scientific/property contract on Python 3.13.
    "property": Lane(
        marker="property and not live_provider",
        hypothesis_profile="full",
        parallel=True,
        timeout_seconds=900,
        distribution="load",
    ),
    # Exact non-parallel release gate; no correctness work is hidden by xdist.
    "serial-release": Lane(
        marker="not live_provider",
        hypothesis_profile="full",
        parallel=False,
        timeout_seconds=900,
    ),
    "windows-wrf": Lane(
        marker=None,
        hypothesis_profile="fast",
        parallel=False,
        timeout_seconds=300,
        targets=(
            "sharpmod/tests/test_gui_reanalysis_wrf.py",
            "sharpmod/tests/test_model_fetch_packaging.py",
        ),
    ),
    "live-provider": Lane(
        marker="live_provider",
        hypothesis_profile="full",
        parallel=False,
        timeout_seconds=180,
        live_providers=True,
    ),
}


def default_workers() -> int:
    """Bound parallelism to protect Qt and scientific-array memory usage."""

    configured = os.environ.get("SHARPMOD_TEST_WORKERS")
    if configured:
        try:
            workers = int(configured)
        except ValueError as exc:
            raise ValueError("SHARPMOD_TEST_WORKERS must be an integer") from exc
        if workers < 1:
            raise ValueError("SHARPMOD_TEST_WORKERS must be at least one")
        return workers
    return min(4, max(1, os.cpu_count() or 1))


def build_pytest_command(
    lane_name: str,
    *,
    junit: Path,
    workers: int,
    coverage: bool = False,
    coverage_xml: Path | None = None,
    extra_args: tuple[str, ...] = (),
) -> tuple[list[str], dict[str, str]]:
    """Build the auditable command/environment pair for one lane."""

    if lane_name not in LANES:
        raise ValueError(f"unknown lane: {lane_name}")
    if workers < 1:
        raise ValueError("workers must be at least one")
    if coverage and lane_name != "fast":
        raise ValueError("coverage is collected only by the Python 3.13 fast lane")

    lane = LANES[lane_name]
    command = [sys.executable, "-m", "pytest", "-q"]
    command.extend(lane.targets)
    if lane.marker:
        command.extend(("-m", lane.marker))
    command.append(f"--timeout={lane.timeout_seconds}")
    command.extend(("--durations=25", "--durations-min=0.20"))
    command.append(f"--junitxml={junit}")
    if lane.parallel:
        command.extend(
            (
                "-n",
                str(workers),
                f"--dist={lane.distribution}",
                "--max-worker-restart=0",
            )
        )
    if coverage:
        output = coverage_xml or ROOT / "coverage.xml"
        command.extend(
            (
                "--cov=sharpmod",
                "--cov-report=term-missing:skip-covered",
                f"--cov-report=xml:{output}",
            )
        )
    command.extend(extra_args)

    environment = os.environ.copy()
    environment["SHARPMOD_HYPOTHESIS_PROFILE"] = lane.hypothesis_profile
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.setdefault("SHARPMOD_GEOCODER_URL", "off")
    if lane.live_providers:
        environment["SHARPMOD_RUN_LIVE_PROVIDER_TESTS"] = "1"
    return command, environment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=tuple(LANES))
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--performance-json", type=Path)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--coverage-xml", type=Path)
    parser.add_argument(
        "--no-performance-budget",
        action="store_true",
        help="produce JUnit durations without enforcing the checked budget",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args, pytest_args = _parser().parse_known_args(argv)
    junit = args.junit or DEFAULT_RESULTS / f"{args.lane}.xml"
    report = args.performance_json or DEFAULT_RESULTS / f"{args.lane}-timing.json"
    junit.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    extra_args = tuple(pytest_args)
    if extra_args[:1] == ("--",):
        extra_args = extra_args[1:]
    command, environment = build_pytest_command(
        args.lane,
        junit=junit,
        workers=args.workers,
        coverage=args.coverage,
        coverage_xml=args.coverage_xml,
        extra_args=extra_args,
    )
    print("Test lane command:", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if completed.returncode:
        return completed.returncode
    checker_args = [
        str(junit),
        "--suite",
        args.lane,
        "--json-out",
        str(report),
    ]
    if args.no_performance_budget:
        checker_args.append("--no-enforce")
    return performance.main(checker_args)


if __name__ == "__main__":
    raise SystemExit(main())
