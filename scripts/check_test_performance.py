#!/usr/bin/env python3
"""Check pytest JUnit durations against the repository performance budget.

The checker deliberately consumes JUnit XML instead of parsing pytest's human
output.  That keeps the report portable across local runs, GitHub Actions, and
parallel xdist lanes while preserving one checked baseline in version control.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "constraints" / "test-performance-baseline.json"


class PerformanceBudgetError(ValueError):
    """A timing artifact or checked baseline is invalid."""


@dataclass(frozen=True)
class TestDuration:
    """One test duration read from pytest's JUnit report."""

    key: str
    display_name: str
    seconds: float


@dataclass(frozen=True)
class BudgetViolation:
    """One suite or test that exceeded its allowed wall time."""

    name: str
    actual_seconds: float
    limit_seconds: float
    baseline_seconds: float


def _positive_float(raw: object, description: str, *, allow_zero: bool = True) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise PerformanceBudgetError(f"{description} must be numeric") from exc
    minimum_ok = value >= 0 if allow_zero else value > 0
    if not minimum_ok:
        comparator = "non-negative" if allow_zero else "positive"
        raise PerformanceBudgetError(f"{description} must be {comparator}")
    return value


def _display_name(classname: str, test_name: str) -> str:
    """Convert pytest's dotted JUnit classname to a familiar node id."""

    prefix = "sharpmod.tests."
    if classname.startswith(prefix):
        module_and_class = classname[len(prefix):].split(".")
        module = module_and_class[0]
        class_suffix = "::".join(module_and_class[1:])
        result = f"sharpmod/tests/{module}.py"
        if class_suffix:
            result += f"::{class_suffix}"
        return f"{result}::{test_name}"
    return f"{classname}::{test_name}" if classname else test_name


def read_junit(path: Path) -> tuple[float, tuple[TestDuration, ...]]:
    """Return suite wall time and individual test durations from JUnit XML."""

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PerformanceBudgetError(f"cannot read JUnit report {path}: {exc}") from exc

    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        suites = list(root.findall(".//testsuite"))
    if not suites:
        raise PerformanceBudgetError(f"JUnit report {path} has no testsuite")

    suite_seconds = sum(
        _positive_float(suite.get("time", 0), "JUnit testsuite time")
        for suite in suites
    )
    durations: list[TestDuration] = []
    for case in root.findall(".//testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "<unnamed>")
        seconds = _positive_float(case.get("time", 0), f"duration for {name}")
        key = f"{classname}::{name}" if classname else name
        durations.append(
            TestDuration(
                key=key,
                display_name=_display_name(classname, name),
                seconds=seconds,
            )
        )
    if not durations:
        raise PerformanceBudgetError(f"JUnit report {path} has no testcases")
    return suite_seconds, tuple(durations)


def read_baseline(path: Path) -> dict:
    """Read and minimally validate the checked JSON baseline."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceBudgetError(f"cannot read baseline {path}: {exc}") from exc
    if data.get("schema_version") != 1:
        raise PerformanceBudgetError("performance baseline schema_version must be 1")
    if not isinstance(data.get("suites"), dict) or not isinstance(
        data.get("tests"), dict
    ):
        raise PerformanceBudgetError("performance baseline needs suites and tests maps")
    return data


def _limit(entry: dict, defaults: dict, suite_name: str) -> tuple[float, float]:
    override = entry.get("suite_overrides", {}).get(suite_name, {})
    if override:
        entry = {**entry, **override}
    baseline = _positive_float(entry.get("baseline_seconds"), "baseline_seconds")
    if "maximum_seconds" in entry:
        maximum = _positive_float(
            entry["maximum_seconds"], "maximum_seconds", allow_zero=False
        )
    else:
        relative = _positive_float(
            entry.get(
                "relative_tolerance", defaults.get("relative_tolerance", 0.5)
            ),
            "relative_tolerance",
        )
        absolute = _positive_float(
            entry.get(
                "absolute_tolerance_seconds",
                defaults.get("absolute_tolerance_seconds", 1.0),
            ),
            "absolute_tolerance_seconds",
        )
        maximum = max(baseline * (1.0 + relative), baseline + absolute)
    if maximum < baseline:
        raise PerformanceBudgetError("maximum_seconds cannot be below baseline_seconds")
    return baseline, maximum


def evaluate(
    *,
    suite_name: str,
    suite_seconds: float,
    durations: tuple[TestDuration, ...],
    baseline: dict,
) -> tuple[tuple[BudgetViolation, ...], int]:
    """Compare one run with its suite and per-test budgets."""

    try:
        suite_entry = baseline["suites"][suite_name]
    except KeyError as exc:
        known = ", ".join(sorted(baseline["suites"]))
        raise PerformanceBudgetError(
            f"unknown suite {suite_name!r}; expected one of: {known}"
        ) from exc
    defaults = baseline.get("defaults", {})
    suite_baseline, suite_limit = _limit(suite_entry, defaults, suite_name)
    violations: list[BudgetViolation] = []
    if suite_seconds > suite_limit:
        violations.append(
            BudgetViolation(
                name=f"suite:{suite_name}",
                actual_seconds=suite_seconds,
                limit_seconds=suite_limit,
                baseline_seconds=suite_baseline,
            )
        )

    tracked = 0
    test_entries = baseline["tests"]
    for duration in durations:
        entry = test_entries.get(duration.key)
        if entry is None:
            continue
        lanes = entry.get("suites")
        if lanes is not None and suite_name not in lanes:
            continue
        tracked += 1
        test_baseline, test_limit = _limit(entry, defaults, suite_name)
        if duration.seconds > test_limit:
            violations.append(
                BudgetViolation(
                    name=duration.display_name,
                    actual_seconds=duration.seconds,
                    limit_seconds=test_limit,
                    baseline_seconds=test_baseline,
                )
            )
    violations.sort(
        key=lambda item: item.actual_seconds / item.limit_seconds,
        reverse=True,
    )
    return tuple(violations), tracked


def _seconds(value: float) -> str:
    return f"{value:.2f}s"


def _markdown_summary(
    suite_name: str,
    suite_seconds: float,
    top: tuple[TestDuration, ...],
    violations: tuple[BudgetViolation, ...],
) -> str:
    status = "PASS" if not violations else "FAIL"
    lines = [
        f"### Test performance: {suite_name} - {status}",
        "",
        f"Suite wall time: **{_seconds(suite_seconds)}**",
        "",
        "| Slowest test | Time |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{item.display_name}` | {_seconds(item.seconds)} |" for item in top
    )
    if violations:
        lines.extend(("", "Budget violations:"))
        lines.extend(
            (
                f"- `{item.name}` took {_seconds(item.actual_seconds)}; "
                f"limit {_seconds(item.limit_seconds)} "
                f"(baseline {_seconds(item.baseline_seconds)})"
            )
            for item in violations
        )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path, help="pytest --junitxml output")
    parser.add_argument("--suite", required=True, help="checked lane name")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json-out", type=Path, help="write machine-readable report")
    parser.add_argument("--top", type=int, default=15, help="slow tests to report")
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help="report regressions but return success",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.top < 1:
        raise PerformanceBudgetError("--top must be at least one")
    suite_seconds, durations = read_junit(args.junit)
    baseline = read_baseline(args.baseline)
    violations, tracked = evaluate(
        suite_name=args.suite,
        suite_seconds=suite_seconds,
        durations=durations,
        baseline=baseline,
    )
    top = tuple(
        sorted(durations, key=lambda item: item.seconds, reverse=True)[: args.top]
    )
    summary = _markdown_summary(args.suite, suite_seconds, top, violations)
    print(summary, end="")
    print(f"Tracked tests present in this lane: {tracked}")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "suite": args.suite,
        "suite_seconds": suite_seconds,
        "tracked_tests_present": tracked,
        "slowest_tests": [asdict(item) for item in top],
        "violations": [asdict(item) for item in violations],
        "passed": not violations,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    return 0 if args.no_enforce or not violations else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PerformanceBudgetError as exc:
        print(f"test-performance configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
