"""Contracts for checked pytest duration budgets."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import check_test_performance as performance


def _write_junit(path: Path, *, suite_seconds: float, test_seconds: float) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" time="{suite_seconds}">
    <testcase classname="sharpmod.tests.test_example" name="test_slow"
              time="{test_seconds}" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )


def _write_baseline(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "defaults": {
                    "relative_tolerance": 0.5,
                    "absolute_tolerance_seconds": 1.0,
                },
                "suites": {
                    "fast": {
                        "baseline_seconds": 10.0,
                        "maximum_seconds": 20.0,
                    }
                },
                "tests": {
                    "sharpmod.tests.test_example::test_slow": {
                        "baseline_seconds": 2.0,
                        "maximum_seconds": 4.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_performance_checker_writes_passing_machine_report(tmp_path, capsys):
    junit = tmp_path / "junit.xml"
    baseline = tmp_path / "baseline.json"
    report = tmp_path / "report.json"
    _write_junit(junit, suite_seconds=12.0, test_seconds=3.0)
    _write_baseline(baseline)

    result = performance.main(
        [
            str(junit),
            "--suite",
            "fast",
            "--baseline",
            str(baseline),
            "--json-out",
            str(report),
        ]
    )

    assert result == 0
    assert "Test performance: fast - PASS" in capsys.readouterr().out
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["tracked_tests_present"] == 1
    assert payload["slowest_tests"][0]["display_name"] == (
        "sharpmod/tests/test_example.py::test_slow"
    )


def test_performance_checker_fails_suite_and_test_regressions(tmp_path, capsys):
    junit = tmp_path / "junit.xml"
    baseline = tmp_path / "baseline.json"
    _write_junit(junit, suite_seconds=21.0, test_seconds=4.5)
    _write_baseline(baseline)

    result = performance.main(
        [str(junit), "--suite", "fast", "--baseline", str(baseline)]
    )

    assert result == 1
    output = capsys.readouterr().out
    assert "Test performance: fast - FAIL" in output
    assert "suite:fast" in output
    assert "sharpmod/tests/test_example.py::test_slow" in output


def test_relative_budget_is_used_when_no_explicit_maximum():
    violations, tracked = performance.evaluate(
        suite_name="property",
        suite_seconds=14.0,
        durations=(
            performance.TestDuration(
                key="sample::test_value",
                display_name="sample.py::test_value",
                seconds=7.1,
            ),
        ),
        baseline={
            "defaults": {
                "relative_tolerance": 0.5,
                "absolute_tolerance_seconds": 1.0,
            },
            "suites": {"property": {"baseline_seconds": 10.0}},
            "tests": {"sample::test_value": {"baseline_seconds": 4.0}},
        },
    )

    assert tracked == 1
    assert [item.name for item in violations] == ["sample.py::test_value"]


def test_parallel_lane_can_override_a_serial_test_baseline():
    violations, tracked = performance.evaluate(
        suite_name="property",
        suite_seconds=12.0,
        durations=(
            performance.TestDuration(
                key="sample::test_value",
                display_name="sample.py::test_value",
                seconds=8.0,
            ),
        ),
        baseline={
            "defaults": {},
            "suites": {
                "property": {
                    "baseline_seconds": 10.0,
                    "maximum_seconds": 15.0,
                }
            },
            "tests": {
                "sample::test_value": {
                    "baseline_seconds": 4.0,
                    "maximum_seconds": 6.0,
                    "suite_overrides": {
                        "property": {
                            "baseline_seconds": 8.0,
                            "maximum_seconds": 10.0,
                        }
                    },
                }
            },
        },
    )

    assert tracked == 1
    assert violations == ()
