"""Packaging and CI contracts for the optional native backend."""

from __future__ import annotations

import ast
from email.parser import BytesParser
from email.policy import default
import os
from pathlib import Path
import re
import tomllib
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUST_ROOT = ROOT / "rust" / "sharpmod-rs"


def _python_source_version() -> str:
    tree = ast.parse(
        (ROOT / "sharpmod" / "_version.py").read_text(encoding="utf-8")
    )
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                assert isinstance(value, str)
                return value
    raise AssertionError("sharpmod/_version.py does not define __version__")


def _load_workflow(name: str) -> dict:
    yaml = pytest.importorskip(
        "yaml", reason="PyYAML is required only for workflow structure checks"
    )
    # BaseLoader keeps the YAML 1.1 word ``on`` as a string instead of
    # interpreting it as a boolean, while still exercising a real YAML parser.
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(workflow, dict)
    return workflow


def _wheel_candidates() -> list[Path]:
    override = os.environ.get("SHARPMOD_RS_WHEEL")
    if override:
        path = Path(override)
        return sorted(path.glob("*.whl")) if path.is_dir() else [path]
    return sorted((RUST_ROOT / "dist").glob("*.whl"))


def test_native_source_versions_and_numpy_dependency_match():
    python_version = _python_source_version()
    cargo = tomllib.loads((RUST_ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    maturin = tomllib.loads(
        (RUST_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads(
        (RUST_ROOT / "Cargo.lock").read_text(encoding="utf-8")
    )
    lock_packages = [
        package for package in lock["package"]
        if package.get("name") == "sharpmod-rs"
    ]

    assert cargo["package"]["version"] == python_version
    assert cargo["package"]["rust-version"] == "1.88"
    assert cargo["dependencies"]["libloading"] == "0.9"
    assert maturin["project"]["version"] == python_version
    assert len(lock_packages) == 1
    assert lock_packages[0]["version"] == python_version
    assert maturin["project"]["dependencies"] == ["numpy>=1.24,<3"]


def test_native_wheel_metadata_declares_compatible_numpy():
    wheels = _wheel_candidates()
    if not wheels:
        pytest.skip("no native wheel has been built for metadata inspection")

    expected_version = _python_source_version()
    for wheel in wheels:
        assert wheel.is_file(), f"native wheel does not exist: {wheel}"
        with ZipFile(wheel) as archive:
            metadata_paths = [
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            assert len(metadata_paths) == 1, (wheel, metadata_paths)
            metadata = BytesParser(policy=default).parsebytes(
                archive.read(metadata_paths[0])
            )

        assert metadata["Name"] == "sharpmod-rs"
        assert metadata["Version"] == expected_version
        requirements = metadata.get_all("Requires-Dist", [])
        numpy_requirements = [
            requirement for requirement in requirements
            if re.match(r"(?i)^numpy(?=[<>=!~\s(;]|$)", requirement)
        ]
        assert len(numpy_requirements) == 1, (wheel, requirements)
        normalized = numpy_requirements[0].replace(" ", "")
        assert ">=1.24" in normalized, normalized
        assert "<3" in normalized, normalized


def test_rust_workflow_covers_versions_and_numpy_without_frozen_apps():
    workflow = _load_workflow("rust.yml")
    triggers = workflow["on"]
    required_paths = {
        "sharpmod/_version.py",
        "sharpmod/model_transport.py",
        "sharpmod/tools/model_extract.py",
        "sharpmod/tests/test_grib_backend_equivalence.py",
        "sharpmod/tests/test_scalar_pressure_merge.py",
        "packaging/sharpmod_gui.spec",
        "packaging/sharpmod_gui_launcher.py",
        ".github/workflows/release.yml",
    }
    assert required_paths <= set(triggers["pull_request"]["paths"])
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers

    jobs = workflow["jobs"]
    assert "windows-frozen" not in jobs
    rust_steps = "\n".join(
        step.get("run", "") for step in jobs["rust-checks"]["steps"]
    )
    assert "cargo +stable fmt --all -- --check" in rust_steps
    assert (
        "cargo +stable clippy --locked --all-targets --all-features "
        "-- -D warnings"
    ) in rust_steps
    assert "cargo +1.88.0 test --locked --all-targets" in rust_steps
    assert '"pytest-timeout>=2.4,<3"' in rust_steps
    assert (
        "scripts/install_sharppy_compat.py --sharppy-only --skip-pip-check"
        in rust_steps
    )

    numpy_rows = jobs["numpy-compat"]["strategy"]["matrix"]["include"]
    assert {row["numpy"] for row in numpy_rows} == {
        "numpy==1.26.*",
        "numpy>=2,<3",
    }
    numpy_scripts = "\n".join(
        step.get("run", "") for step in jobs["numpy-compat"]["steps"]
    )
    assert '"pytest-timeout>=2.4,<3"' in numpy_scripts
    assert (
        "scripts/install_sharppy_compat.py --sharppy-only --skip-pip-check"
        in numpy_scripts
    )
    numpy_steps = jobs["numpy-compat"]["steps"]
    numpy_venv = next(
        step for step in numpy_steps
        if step.get("name") == "Create Python virtual environment"
    )
    assert 'python -m venv "$RUNNER_TEMP/sharpmod-rust-venv"' in numpy_venv["run"]
    assert 'VIRTUAL_ENV=$RUNNER_TEMP/sharpmod-rust-venv' in numpy_venv["run"]

    wheel_rows = jobs["wheels"]["strategy"]["matrix"]["include"]
    combinations = {
        (row["platform"], row["python-version"]) for row in wheel_rows
    }
    assert combinations == {
        (platform, python)
        for platform in (
            "windows-x86_64",
            "linux-x86_64-manylinux2014",
            "macos-x86_64",
            "macos-arm64",
        )
        for python in ("3.11", "3.12")
    }
    assert all(row["python-tag"] in {"cp311", "cp312"} for row in wheel_rows)
    wheel_steps = jobs["wheels"]["steps"]
    wheel_scripts = "\n".join(
        step.get("run", "") for step in wheel_steps
    )
    assert '"pytest-timeout>=2.4,<3"' in wheel_scripts
    assert (
        "scripts/install_sharppy_compat.py --sharppy-only --skip-pip-check"
        in wheel_scripts
    )
    clean_install = next(
        step for step in wheel_steps
        if step.get("name") == "Resolve and smoke-test wheel in a clean environment"
    )
    assert clean_install["shell"] == "python"
    assert "find_spec('numpy') is None" in clean_install["run"]
    assert '"pip", "install"' in clean_install["run"]
    assert '"pip", "check"' in clean_install["run"]
    assert "sharpmod_rs.wind_to_components" in clean_install["run"]

def test_release_workflow_gates_tag_and_source_versions():
    workflow = _load_workflow("release.yml")
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]["tag"]
    assert dispatch["default"] == f"v{_python_source_version()}"

    jobs = workflow["jobs"]
    resolve = jobs["resolve-release"]
    steps = resolve["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Resolve release tag and source") < names.index(
        "Check out release source"
    )
    assert names.index("Record immutable release source") < names.index(
        "Validate release tag and source versions"
    )

    validation = next(
        step["run"]
        for step in steps
        if step.get("name") == "Validate release tag and source versions"
    )
    for path in (
        "sharpmod/_version.py",
        "rust/sharpmod-rs/Cargo.toml",
        "rust/sharpmod-rs/pyproject.toml",
        "rust/sharpmod-rs/Cargo.lock",
    ):
        assert path in validation
    assert 'expected_tag = f"v{python_version}"' in validation

    test_job = jobs["test-release"]
    assert test_job["uses"] == "./.github/workflows/tests.yml"
    assert test_job["with"]["ref"] == (
        "${{ needs.resolve-release.outputs.source_sha }}"
    )
    build = jobs["build-windows-exe"]
    assert set(build["needs"]) == {"resolve-release", "test-release"}
    checkout = next(
        step for step in build["steps"]
        if step.get("name") == "Check out tested release source"
    )
    assert checkout["with"]["ref"] == (
        "${{ needs.resolve-release.outputs.source_sha }}"
    )


def test_release_builds_installs_and_requires_locked_cp311_rust_wheel():
    workflow = _load_workflow("release.yml")
    job = workflow["jobs"]["build-windows-exe"]
    assert job["env"] == {
        "SHARPMOD_BACKEND": "rust",
        "SHARPMOD_REQUIRE_RUST": "1",
        "SHARPMOD_RELEASE_BUILD": "1",
    }

    steps = job["steps"]
    toolchain = next(
        step for step in steps
        if step.get("name") == "Set up Rust 1.88 toolchain"
    )
    assert toolchain["shell"] == "bash"
    toolchain_script = toolchain["run"]
    assert "set -euo pipefail" in toolchain_script
    assert (
        "rustup toolchain install 1.88.0 --profile minimal"
        in toolchain_script
    )
    assert "rustup default 1.88.0" in toolchain_script
    assert "rustc --version" in toolchain_script
    assert "grep -E '^rustc 1\\.88\\.0 '" in toolchain_script

    build = next(
        step for step in steps
        if step.get("name") == "Build and install locked Rust wheel"
    )
    names = [step.get("name") for step in steps]
    assert names.index("Set up Rust 1.88 toolchain") < names.index(
        "Build and install locked Rust wheel"
    )
    script = build["run"]
    assert "maturin build --release --locked" in script
    assert "--interpreter python" in script
    assert "cp311-cp311-win_amd64.whl" in script
    assert "pip install --force-reinstall --no-deps" in script

    install = next(
        step for step in steps
        if step.get("name") == "Install constrained package and build dependencies"
    )
    assert "constraints/release.txt" in install["run"]
    assert "pip install --upgrade --constraint $constraint pip" in install["run"]
    assert "pyinstaller maturin" in install["run"]

    verify = next(
        step for step in steps
        if step.get("name") == "Verify installed Rust backend"
    )
    assert "requested_backend" in verify["run"]
    assert "active_backend" in verify["run"]
    assert "fallback_reason" in verify["run"]
    assert "wind_to_components" in verify["run"]

    build_names = {
        "Build one-folder executable",
        "Build portable single-file executable",
    }
    assert build_names <= {step.get("name") for step in steps}
    for name in build_names:
        step = next(step for step in steps if step.get("name") == name)
        inherited_env = {**job["env"], **step.get("env", {})}
        assert inherited_env["SHARPMOD_REQUIRE_RUST"] == "1"
        assert inherited_env["SHARPMOD_BACKEND"] == "rust"


def test_release_requires_rust_runtime_reports_and_uploads_wheel():
    workflow = _load_workflow("release.yml")
    steps = workflow["jobs"]["build-windows-exe"]["steps"]

    verifications = [
        step for step in steps
        if step.get("name") in {
            "Verify recommended one-folder artifact",
            "Verify portable single-file artifact",
        }
    ]
    assert len(verifications) == 2
    for step in verifications:
        script = step["run"]
        assert 'requested_backend -ne "rust"' in script
        assert 'active_backend -ne "rust"' in script
        assert "$null -ne $result.backend.fallback_reason" in script
        assert "rust_installed" in script
        assert "rust_version" in script

    stage = next(
        step for step in steps
        if step.get("name") == "Stage clearly labeled release artifacts"
    )
    assert 'Copy-Item "rust/sharpmod-rs/dist/*.whl"' in stage["run"]
    assert "pip freeze --all" in stage["run"]

    upload = next(
        step for step in steps
        if step.get("name") == "Upload tested release artifacts"
    )
    publish_steps = workflow["jobs"]["publish"]["steps"]
    publish = next(
        step for step in publish_steps
        if step.get("name") == "Publish GitHub Release"
    )
    assert upload["with"]["path"] == "release-artifacts/*"
    assert upload["with"]["if-no-files-found"] == "error"
    assert publish["with"]["files"] == "release-artifacts/*"


def test_release_dispatch_pins_existing_and_new_tag_sources():
    workflow = _load_workflow("release.yml")
    jobs = workflow["jobs"]
    steps = jobs["resolve-release"]["steps"]
    names = [step.get("name") for step in steps]

    resolve = next(
        step for step in steps
        if step.get("name") == "Resolve release tag and source"
    )
    script = resolve["run"]
    assert "git check-ref-format" in script
    assert "ls-remote --exit-code --refs" in script
    assert 'checkout_ref="refs/tags/$tag"' in script
    assert 'checkout_ref="$GITHUB_SHA"' in script
    assert "checkout_ref=%s" in script
    assert "tag_exists=%s" in script

    checkout = next(
        step for step in steps if step.get("name") == "Check out release source"
    )
    assert checkout["with"]["ref"] == (
        "${{ steps.release.outputs.checkout_ref }}"
    )

    build_names = [
        step.get("name") for step in jobs["build-windows-exe"]["steps"]
    ]
    publish = next(
        step for step in jobs["publish"]["steps"]
        if step.get("name") == "Publish GitHub Release"
    )
    assert "Build portable single-file executable" in build_names
    assert publish["with"]["tag_name"] == (
        "${{ needs.resolve-release.outputs.tag }}"
    )
    assert publish["with"]["target_commitish"] == (
        "${{ needs.resolve-release.outputs.source_sha }}"
    )


def test_release_write_permission_is_isolated_and_actions_are_immutable():
    workflow = _load_workflow("release.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["build-windows-exe"]["permissions"] == {
        "actions": "read",
        "contents": "read",
    }
    assert workflow["jobs"]["attest-windows-release"]["permissions"] == {
        "actions": "read",
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
    }
    assert workflow["jobs"]["publish"]["permissions"] == {"contents": "write"}

    for job_name, job in workflow["jobs"].items():
        for permission, value in job.get("permissions", {}).items():
            if value == "write":
                assert (job_name, permission) in {
                    ("attest-windows-release", "attestations"),
                    ("attest-windows-release", "id-token"),
                    ("publish", "contents"),
                }

    action_ref = re.compile(r"^[^@]+@[0-9a-f]{40}$")
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses is not None:
                assert action_ref.fullmatch(uses), uses
