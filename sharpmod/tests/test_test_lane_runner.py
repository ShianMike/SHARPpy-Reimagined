"""Execution-policy contracts for optimized local and CI test lanes."""

from __future__ import annotations

import json

import pytest

from scripts import check_test_performance as performance
from scripts import run_test_lane as runner


def _job_timeout_seconds() -> dict[str, int]:
    """Return each ``tests.yml`` job's ``timeout-minutes``, in seconds."""

    yaml = pytest.importorskip(
        "yaml", reason="PyYAML is required only for workflow structure checks"
    )
    # BaseLoader keeps the YAML 1.1 word ``on`` a string instead of reading it
    # as a boolean; the cost is that every scalar arrives as text.
    workflow = yaml.load(
        (runner.ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        ),
        Loader=yaml.BaseLoader,
    )
    return {
        name: int(job["timeout-minutes"]) * 60
        for name, job in workflow["jobs"].items()
        if "timeout-minutes" in job
    }


def _command(lane, tmp_path, **kwargs):
    return runner.build_pytest_command(
        lane,
        junit=tmp_path / f"{lane}.xml",
        workers=4,
        **kwargs,
    )


def _marker(command):
    marker_index = command.index("-m", 3)
    return command[marker_index + 1]


def test_fast_lane_is_file_parallel_and_can_collect_the_only_coverage(tmp_path):
    command, environment = _command(
        "fast",
        tmp_path,
        coverage=True,
        coverage_xml=tmp_path / "coverage.xml",
    )

    assert command[0:3] == [runner.sys.executable, "-m", "pytest"]
    assert _marker(command) == "not property and not live_provider"
    assert command[command.index("-n") + 1] == "4"
    assert "--dist=loadfile" in command
    assert "--max-worker-restart=0" in command
    assert "--cov=sharpmod" in command
    assert f"--cov-report=xml:{tmp_path / 'coverage.xml'}" in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "fast"


def test_compatibility_lane_smokes_properties_without_coverage(tmp_path):
    command, environment = _command("compatibility", tmp_path)

    assert _marker(command) == "not live_provider"
    assert "-n" in command
    assert "--dist=loadfile" in command
    assert "--cov=sharpmod" not in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "fast"


def test_property_lane_keeps_full_profile_and_parallelizes_by_safe_group(tmp_path):
    command, environment = _command("property", tmp_path)

    assert _marker(command) == "property and not live_provider"
    assert command[command.index("-n") + 1] == "4"
    assert "--dist=loadgroup" in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "full"


def test_no_parallel_lane_scatters_tests_that_asked_to_stay_together():
    """A grouping marker that the lane ignores is worse than no marker.

    ``conftest`` groups the Qt-heavy files because they share process-global
    state, and three of them carry property tests. Bare ``--dist=load``
    distributes individual tests and honors neither ``xdist_group`` nor file
    cohesion, so the guarantee those markers advertise would quietly not hold.
    ``loadfile`` keeps each file on one worker, which covers the file-scoped
    groups used here; ``loadgroup`` honors the marker directly.
    """
    for name, lane in runner.LANES.items():
        if not lane.parallel:
            continue
        assert lane.distribution in ("loadfile", "loadgroup"), (
            f"the {name!r} lane distributes by {lane.distribution!r}, which "
            "ignores the grouping its tests rely on"
        )


def test_release_lane_is_complete_serial_without_repeating_property_depth(tmp_path):
    command, environment = _command("serial-release", tmp_path)

    assert _marker(command) == "not live_provider"
    assert "-n" not in command
    assert "--dist=loadgroup" not in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "fast"


def test_coverage_is_rejected_outside_the_single_fast_lane(tmp_path):
    with pytest.raises(ValueError, match="only by the Python 3.13 fast lane"):
        _command("property", tmp_path, coverage=True)


def test_run_context_records_interpreter_backend_and_machine(tmp_path):
    """Timing numbers need their conditions to be comparable across runs."""
    junit = tmp_path / "tiny.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite name="pytest" tests="1" time="0.01">'
        '<testcase classname="t" name="case" time="0.01"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    baseline = {
        "schema_version": 1,
        "suites": {"fast": {"baseline_seconds": 1.0, "maximum_seconds": 60.0}},
        "tests": {},
        "defaults": {},
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    report_path = tmp_path / "report.json"
    assert performance.main([
        str(junit), "--suite", "fast", "--baseline", str(baseline_path),
        "--json-out", str(report_path), "--no-enforce", "--workers", "3",
    ]) == 0
    context = json.loads(report_path.read_text(encoding="utf-8"))["run_context"]
    assert context["workers"] == 3
    assert context["python_version"].count(".") == 2
    assert context["active_backend"] in {"python", "rust"}
    assert context["cpu_count"] >= 1
    assert context["platform"]
    # The SHA and the dirty state are separate fields: a clean CI checkout
    # reports ``worktree_dirty: False`` next to the bare SHA, while this dirty
    # local tree must say dirty instead of stamping a clean SHA it never ran.
    assert isinstance(context["worktree_dirty"], bool)
    truth = performance.collect_run_context(workers=3)
    assert context["commit"] == truth["commit"]
    assert context["worktree_dirty"] == truth["worktree_dirty"]
    assert context["worktree_dirty_files"] == truth["worktree_dirty_files"]


def test_run_context_reports_clean_and_dirty_worktrees(monkeypatch):
    """Both checkout states must be representable (clean CI must not fail)."""
    from unittest import mock

    def fake_git(args):
        if args == ["status", "--porcelain"]:
            return fake_git.status
        if args == ["rev-parse", "--short=12", "HEAD"]:
            return "abc123def456"
        raise AssertionError(args)

    fake_git.status = ""
    monkeypatch.setattr(performance, "_git_output", fake_git)
    clean = performance.collect_run_context(workers=1)
    assert clean["commit"] == "abc123def456"
    assert clean["worktree_dirty"] is False
    assert clean["worktree_dirty_files"] == 0

    fake_git.status = " M sharpmod/a.py\n?? sharpmod/b.py\n"
    dirty = performance.collect_run_context(workers=1)
    assert dirty["commit"] == "abc123def456"
    assert dirty["worktree_dirty"] is True
    assert dirty["worktree_dirty_files"] == 2

    # No git binary at all: the report still builds, minus the VCS fields.
    monkeypatch.setattr(performance, "_git_output", mock.Mock(return_value=None))
    nogit = performance.collect_run_context(workers=1)
    assert "commit" not in nogit
    assert nogit["worktree_dirty"] is False
    assert "worktree_dirty_files" not in nogit


def test_git_probe_preserves_successful_empty_status(monkeypatch):
    """A clean checkout returns empty output, which still means zero dirty files."""
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        performance.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess([], 0, stdout="\n"),
    )
    assert performance._git_output(["status", "--porcelain"]) == ""


@pytest.mark.parametrize("lane", sorted(runner.LANES))
def test_each_lane_budget_can_fail_before_github_kills_the_job(lane):
    """An overrunning lane has to be reported rather than silently destroyed.

    The job timeout lives in ``tests.yml`` and the timing budget lives in the
    checked baseline, so nothing otherwise notices when the two drift apart.
    ``serial-release`` allowed 4200s on hosted runners while its job was
    destroyed at 2700s: the budget could never fail, and because the runner
    tears the step down, no timing artifact was written either -- an overrun was
    indistinguishable from an infrastructure failure.

    Every job checks the tree out, installs the Qt system libraries, sets up
    Python and installs the package with its extras before pytest starts, so a
    lane cannot be budgeted for the whole of ``timeout-minutes``.
    """
    setup_reserve_seconds = 240
    timeouts = _job_timeout_seconds()
    assert lane in timeouts, f"no tests.yml job runs the {lane!r} lane"

    overrun = timeouts[lane] - setup_reserve_seconds
    violations, _tracked = performance.evaluate(
        suite_name=lane,
        suite_seconds=overrun,
        durations=(),
        baseline=performance.read_baseline(performance.DEFAULT_BASELINE),
        environment_profile="github-actions",
    )

    assert any(item.name == f"suite:{lane}" for item in violations), (
        f"the {lane!r} budget still passes at {overrun}s, but its job is "
        f"destroyed at {timeouts[lane]}s, so the overrun would go unreported"
    )
