"""Installed acceptance selects and forwards versions without installing locally."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packaging/verify_native_acceptance.ps1"


@pytest.fixture
def pwsh():
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is needed for native acceptance script checks")
    return executable


@pytest.fixture
def native_job():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.load(
        (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["workflow_dispatch"]["inputs"]["native_candidate_run"]["default"] == ""
    return workflow["jobs"]["native-acceptance"]


@pytest.mark.parametrize(("candidate", "previous"), [
    ("3.4.0", "3.3.0"), ("3.2.1", "3.2.0"), ("3.11.0", "3.10.0"),
])
def test_workflow_selects_the_latest_preceding_stable_release(pwsh, native_job, tmp_path, candidate, previous):
    result, output = _resolve_versions(pwsh, native_job, tmp_path, candidate)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [f"version={candidate}", f"previous={previous}"]


def _resolve_versions(pwsh, native_job, tmp_path, candidate="3.4.0", **run_changes):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text(json.dumps({"version": candidate}), encoding="utf-8")
    output = tmp_path / "outputs.txt"
    selection = next(step["run"] for step in native_job["steps"] if step.get("id") == "candidate")
    run = {"status": "completed", "conclusion": "success", "head_sha": "source-sha",
           "path": ".github/workflows/release.yml", **run_changes}
    # Execute the workflow body with only GitHub and Git lookups replaced.
    command = """
function gh {
  $global:LASTEXITCODE = 0
  if ($args[1] -like '*/actions/runs/*') { return $env:ACCEPTANCE_TEST_RUN }
  'v3.2.0','v3.3.0','v3.10.0','v3.2.1','v3.4.0'
}
function git { $global:LASTEXITCODE = 0; 'source-sha' }
""" + selection
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", command], cwd=tmp_path,
        env={**os.environ, "CANDIDATE_RUN": "123", "GITHUB_OUTPUT": str(output),
             "GITHUB_REPOSITORY": "owner/repository", "ACCEPTANCE_TEST_RUN": json.dumps(run)},
        capture_output=True, text=True, timeout=20,
    )
    return result, output


@pytest.mark.parametrize("run_changes", [
    {"status": "in_progress"}, {"conclusion": "failure"},
    {"head_sha": "other-source"}, {"path": ".github/workflows/other.yml"},
])
def test_workflow_rejects_an_unverified_or_different_candidate(pwsh, native_job, tmp_path, run_changes):
    result, output = _resolve_versions(pwsh, native_job, tmp_path, **run_changes)
    assert result.returncode != 0
    assert "successful Release run for the exact checked-out source" in result.stderr
    assert not output.exists()


def test_workflow_forwards_both_versions_to_the_script_and_desktop_test(native_job):
    download = next(step["with"] for step in native_job["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["name"] == "windows-release-v${{ steps.candidate.outputs.version }}"
    assert download["run-id"] == "${{ inputs.native_candidate_run }}"
    exercise = next(step for step in native_job["steps"] if step.get("name") == "Exercise the actual installed desktop")
    script = SCRIPT.read_text(encoding="utf-8")
    desktop = (ROOT / "frontend/e2e/desktop/acceptance.spec.ts").read_text(encoding="utf-8")
    for parameter, output, variable in (
        ("PreviousVersion", "previous", "PREVIOUS_VERSION"),
        ("ExpectedVersion", "version", "EXPECTED_VERSION"),
    ):
        assert exercise["env"][variable] == "${{ steps.candidate.outputs." + output + " }}"
        assert f"-{parameter} $env:{variable}" in exercise["run"]
        assert f"$env:SHARPPY_ACCEPTANCE_{variable} = ${parameter}" in script
        assert f"process.env.SHARPPY_ACCEPTANCE_{variable}" in desktop
    assert 'download "v$PreviousVersion"' in script
    assert "Verify-Installer $previous $PreviousVersion" in script
    assert "Verify-Installer $artifactsRoot $ExpectedVersion" in script


@pytest.mark.parametrize(("versions", "error"), [
    ([], "only on a disposable hosted runner"),
    (["3.3.0", "3.4.0"], "only on a disposable hosted runner"),
    (["3.4.0", "3.4.0"], "ExpectedVersion must be newer"),
    (["3.4.0", "3.3.0"], "ExpectedVersion must be newer"),
])
def test_script_rejects_downgrades_and_non_disposable_installation(pwsh, tmp_path, versions, error):
    report = tmp_path / "not-created"
    arguments = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
                 "-Artifacts", str(tmp_path), "-ReportDirectory", str(report)]
    if versions:
        arguments += ["-PreviousVersion", versions[0], "-ExpectedVersion", versions[1]]
    result = subprocess.run(
        arguments, env={**os.environ, "GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "local"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert error in result.stderr
    assert not report.exists()
