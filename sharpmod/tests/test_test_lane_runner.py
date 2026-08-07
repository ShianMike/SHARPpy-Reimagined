"""Execution-policy contracts for optimized local and CI test lanes."""

from __future__ import annotations

import pytest

from scripts import run_test_lane as runner


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


def test_fast_lane_is_grouped_parallel_and_can_collect_the_only_coverage(tmp_path):
    command, environment = _command(
        "fast",
        tmp_path,
        coverage=True,
        coverage_xml=tmp_path / "coverage.xml",
    )

    assert command[0:3] == [runner.sys.executable, "-m", "pytest"]
    assert _marker(command) == "not property and not live_provider"
    assert command[command.index("-n") + 1] == "4"
    assert "--dist=loadgroup" in command
    assert "--max-worker-restart=0" in command
    assert "--cov=sharpmod" in command
    assert f"--cov-report=xml:{tmp_path / 'coverage.xml'}" in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "fast"


def test_compatibility_lane_smokes_properties_without_coverage(tmp_path):
    command, environment = _command("compatibility", tmp_path)

    assert _marker(command) == "not live_provider"
    assert "-n" in command
    assert "--cov=sharpmod" not in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "fast"


def test_property_lane_keeps_full_profile_and_parallelizes_by_safe_group(tmp_path):
    command, environment = _command("property", tmp_path)

    assert _marker(command) == "property and not live_provider"
    assert command[command.index("-n") + 1] == "4"
    assert "--dist=load" in command
    assert "--dist=loadgroup" not in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "full"


def test_release_lane_is_complete_and_strictly_serial(tmp_path):
    command, environment = _command("serial-release", tmp_path)

    assert _marker(command) == "not live_provider"
    assert "-n" not in command
    assert "--dist=loadgroup" not in command
    assert environment["SHARPMOD_HYPOTHESIS_PROFILE"] == "full"


def test_coverage_is_rejected_outside_the_single_fast_lane(tmp_path):
    with pytest.raises(ValueError, match="only by the Python 3.13 fast lane"):
        _command("property", tmp_path, coverage=True)
