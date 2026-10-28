"""CI caches may reuse dependencies, but never skip verification."""

from pathlib import Path

import pytest


@pytest.fixture
def desktop_job():
    yaml = pytest.importorskip("yaml")
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/frontend.yml"
    # BaseLoader preserves GitHub's `on` key instead of reading it as a boolean.
    return yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)[
        "jobs"
    ]["desktop-bridge"]


def test_cargo_audit_cache_is_exact_and_version_pinned(desktop_job):
    steps = desktop_job["steps"]
    cache = next(step for step in steps if step.get("id") == "cargo-audit")
    install = next(
        step for step in steps
        if step.get("name") == "Install cargo-audit on cache miss"
    )
    assert cache["uses"] == "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    assert cache["with"] == {
        "path": "~/.cargo/bin/cargo-audit.exe",
        "key": "cargo-audit-${{ runner.os }}-${{ runner.arch }}-0.22.2",
    }
    assert install["if"] == "steps.cargo-audit.outputs.cache-hit != 'true'"
    assert install["run"].splitlines() == [
        "cargo install cargo-audit --version 0.22.2 --locked",
        'if ($LASTEXITCODE -ne 0) { throw "Dependency audit tool install failed" }',
    ]
    assert steps.index(cache) < steps.index(install)


def test_both_dependency_audits_run_on_cache_hits_with_fresh_advisories(desktop_job):
    steps = desktop_job["steps"]
    audit = next(
        step for step in steps
        if step.get("name") == "Audit locked desktop and scientific dependencies"
    )
    assert "if" not in audit
    assert "continue-on-error" not in audit
    assert audit["run"].splitlines() == [
        "$auditVersion = cargo audit --version",
        'if ($LASTEXITCODE -ne 0 -or $auditVersion -ne "cargo-audit 0.22.2") '
        '{ throw "Dependency audit tool version mismatch" }',
        "cargo audit --file Cargo.lock",
        'if ($LASTEXITCODE -ne 0) { throw "Desktop dependency audit failed" }',
        "cargo audit --file ../rust/sharpmod-rs/Cargo.lock",
        'if ($LASTEXITCODE -ne 0) { throw "Scientific dependency audit failed" }',
    ]
    install = next(
        step for step in steps
        if step.get("name") == "Install cargo-audit on cache miss"
    )
    assert steps.index(install) < steps.index(audit)
    assert all(
        "advisory-db" not in step["with"]["path"]
        for step in steps if step.get("uses", "").startswith("actions/cache@")
    )


def test_tauri_cache_survives_lock_changes_without_caching_finished_apps(desktop_job):
    cache = next(
        step for step in desktop_job["steps"]
        if step.get("uses", "").startswith("actions/cache@")
        and step.get("id") != "cargo-audit"
    )
    config = cache["with"]
    assert set(config["path"].splitlines()) == {
        "~/.cargo/registry", "~/.cargo/git",
        "src-tauri/target/debug/.fingerprint",
        "src-tauri/target/debug/build", "src-tauri/target/debug/deps",
    }
    prefix = config["restore-keys"].strip()
    assert prefix == "tauri-checks-v1-${{ runner.os }}-${{ runner.arch }}-"
    assert config["key"] == prefix + "${{ hashFiles('src-tauri/Cargo.lock') }}"


def test_desktop_checks_and_real_sidecar_lifecycle_remain_mandatory(desktop_job):
    assert desktop_job["runs-on"] == "windows-latest"
    assert "if" not in desktop_job and "continue-on-error" not in desktop_job
    for command in (
        "rustup update stable --no-self-update && rustup default stable",
        "rustup component add rustfmt clippy",
        "cargo fmt --check",
        "cargo clippy --locked --all-targets -- -D warnings",
        "cargo test --locked",
        "cargo test --locked --test python_sidecar -- --ignored --test-threads=1",
    ):
        step = next(
            step for step in desktop_job["steps"] if step.get("run") == command
        )
        assert "if" not in step and "continue-on-error" not in step
    sidecar = next(
        step for step in desktop_job["steps"]
        if step.get("name") == "Install the real sidecar for lifecycle checks"
    )
    assert "if" not in sidecar and "continue-on-error" not in sidecar
    assert (
        'python -m pip install --constraint constraints/release.txt -e ".[render]"'
        in sidecar["run"]
    )
    assert "python scripts/install_sharppy_compat.py --sharppy-only" in sidecar["run"]
    assert (
        '"SHARPPY_PYTHON=$((Get-Command python).Source)" >> $env:GITHUB_ENV'
        in sidecar["run"]
    )
