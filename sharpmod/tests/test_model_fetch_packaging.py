"""Frozen-app packaging contracts for live forecast-model support."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_supported_python_and_wrf_dependencies_are_bounded():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["requires-python"] == ">=3.11,<3.14"
    assert project["license"] == "BSD-3-Clause"
    assert project["license-files"] == ["LICENSE"]
    classifiers = set(project["classifiers"])
    for minor in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {minor}" in classifiers
    assert project["optional-dependencies"]["wrf"] == [
        "xarray>=2024.7,<2027.0",
        "netCDF4>=1.7,<2.0",
    ]


def test_ci_covers_supported_python_and_windows_wrf_runtime():
    yaml = pytest.importorskip(
        "yaml", reason="PyYAML is required only for workflow structure checks"
    )
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        ),
        Loader=yaml.BaseLoader,
    )

    triggers = workflow["on"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["schedule"] == [{"cron": "17 10 * * 2"}]
    assert "workflow_call" in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["workflow_call"]["inputs"]["run_serial_release"][
        "default"
    ] == "false"
    jobs = workflow["jobs"]
    assert jobs["compatibility"]["strategy"]["matrix"][
        "python-version"
    ] == ["3.11", "3.12"]
    for lane in ("compatibility", "fast", "property", "serial-release"):
        python_job = jobs[lane]
        lane_scripts = "\n".join(
            step.get("run", "") for step in python_job["steps"]
        )
        assert "libegl1" in lane_scripts
        checkout = next(
            step for step in python_job["steps"]
            if step.get("name") == "Check out tested source"
        )
        assert checkout["with"]["ref"] == "${{ inputs.ref || github.sha }}"

        assert "run_test_lane.py" in lane_scripts

    compatibility_scripts = "\n".join(
        step.get("run", "") for step in jobs["compatibility"]["steps"]
    )
    assert "run_test_lane.py compatibility --workers 4" in compatibility_scripts
    fast_scripts = "\n".join(step.get("run", "") for step in jobs["fast"]["steps"])
    assert "run_test_lane.py fast" in fast_scripts
    assert "--coverage" in fast_scripts
    property_scripts = "\n".join(
        step.get("run", "") for step in jobs["property"]["steps"]
    )
    assert "run_test_lane.py property --workers 4" in property_scripts
    serial_scripts = "\n".join(
        step.get("run", "") for step in jobs["serial-release"]["steps"]
    )
    assert "run_test_lane.py serial-release" in serial_scripts
    assert jobs["serial-release"]["if"] == (
        "${{ inputs.run_serial_release == true || "
        "github.ref == 'refs/heads/main' }}"
    )
    assert "test-timing" in str(workflow)

    windows_job = jobs["windows-wrf"]
    assert windows_job["runs-on"] == "windows-latest"
    assert windows_job["timeout-minutes"] == "25"
    scripts = "\n".join(
        step.get("run", "") for step in windows_job["steps"]
    )
    assert 'python -m pip install -e ".[dev,wrf,render]"' in scripts
    assert "scripts/install_sharppy_compat.py --sharppy-only" in scripts
    assert "run_test_lane.py windows-wrf" in scripts

    quality_scripts = "\n".join(
        step.get("run", "") for step in jobs["quality"]["steps"]
    )
    assert "--constraint constraints/release.txt" in quality_scripts
    assert "pip setuptools wheel" in quality_scripts
    assert "ruff check sharpmod scripts packaging" in quality_scripts
    assert "pip_audit --skip-editable" in quality_scripts
    live_scripts = "\n".join(
        step.get("run", "") for step in jobs["live-provider"]["steps"]
    )
    assert jobs["live-provider"]["if"] == (
        "${{ inputs.run_live_providers == true || "
        "github.event_name == 'schedule' }}"
    )
    assert jobs["live-provider"]["timeout-minutes"] == "20"
    assert "SHARPMOD_RUN_LIVE_PROVIDER_TESTS" in str(
        (ROOT / "scripts" / "run_test_lane.py").read_text(encoding="utf-8")
    )
    assert "run_test_lane.py live-provider" in live_scripts


def test_release_installs_model_fetch_dependencies():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert '".[render,era5,wrf]"' in workflow
    assert '-e ".[render,era5,wrf]"' not in workflow
    assert "constraints/release.txt" in workflow
    assert "python scripts/install_sharppy_compat.py --sharppy-only" in workflow
    assert 'pip install --no-deps "SHARPpy' not in workflow
    assert "--model-fetch-runtime-check" in workflow
    assert "Verify portable single-file artifact" in workflow
    assert workflow.count("--model-fetch-runtime-check") >= 2
    assert workflow.count("backend_kernel_ok") >= 2
    assert workflow.count("wrf_runtime_ok") >= 2
    assert 'SHARPMOD_REQUIRE_RUST: "1"' in workflow
    assert 'SHARPMOD_BACKEND: "rust"' in workflow
    assert workflow.count('requested_backend -ne "rust"') >= 2
    assert workflow.count('active_backend -ne "rust"') >= 2
    assert workflow.count("version_consistent") >= 2
    assert "uses: ./.github/workflows/tests.yml" in workflow
    assert "run_serial_release: true" in workflow
    assert "needs: [resolve-release, test-release]" in workflow
    assert workflow.count("contents: write") == 1


def test_test_profiles_timeouts_and_quality_tools_are_configured():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)
    pytest_options = config["tool"]["pytest"]["ini_options"]
    assert pytest_options["timeout"] == 180
    assert "property: Hypothesis-powered scientific/property coverage" in (
        pytest_options["markers"]
    )
    assert "serial: test that must remain in the non-parallel release gate" in (
        pytest_options["markers"]
    )
    assert "xdist_group(name): keep tests sharing process-global state on one worker" in (
        pytest_options["markers"]
    )
    assert "pytest-xdist>=3.6,<4.0" in config["project"][
        "optional-dependencies"
    ]["dev"]
    quality_requirements = config["project"]["optional-dependencies"]["quality"]
    assert all(requirement.count("==") == 1 for requirement in quality_requirements)
    quality_pins = dict(requirement.split("==", 1) for requirement in quality_requirements)
    assert set(quality_pins) == {"pip-audit", "pytest-cov", "ruff"}
    assert all(version for version in quality_pins.values())
    conftest = (ROOT / "sharpmod" / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert "FULL_MAX_EXAMPLES = 100" in conftest
    assert "FAST_MAX_EXAMPLES = 10" in conftest
    assert "is_hypothesis_test" in conftest
    assert "SHARPMOD_HYPOTHESIS_PROFILE" in conftest


def test_pyinstaller_bundles_model_fetch_runtime():
    spec = (ROOT / "packaging" / "sharpmod_gui.spec").read_text(
        encoding="utf-8"
    )
    collection_block = spec.split("a = Analysis", 1)[0]
    for package in (
        "xarray", "herbie", "cfgrib", "eccodes", "cdsapi", "numcodecs",
        "pyproj", "netCDF4", "cftime",
    ):
        assert f'"{package}"' in collection_block

    excludes_block = spec.split("excludes=", 1)[1].split("]", 1)[0]
    assert '"cfgrib"' not in excludes_block
    assert '"herbie"' not in excludes_block
    assert '"netCDF4"' not in excludes_block

    # The checkout lives inside a wrapper folder.  Analysis must use the
    # repository root resolved by the spec, not a relative parent directory,
    # or the editable ``sharpmod`` package is absent on other machines.
    assert "pathex=[_REPO]" in spec
    assert 'pathex=[".."]' not in spec


def test_pyinstaller_requires_rust_only_for_official_release_builds():
    spec = (ROOT / "packaging" / "sharpmod_gui.spec").read_text(
        encoding="utf-8"
    )
    collection_block = spec.split("a = Analysis", 1)[0]
    always_collected = collection_block.split("for pkg in (", 1)[1].split(
        "):", 1
    )[0]
    rust_block = collection_block.split("# Rust release contract", 1)[1]

    assert '"sharpmod_rs"' not in always_collected
    assert 'os.environ.get("SHARPMOD_REQUIRE_RUST", "0") == "1"' in rust_block
    assert 'find_spec("sharpmod_rs")' in rust_block
    assert 'find_spec("sharpmod_rs.sharpmod_rs")' in rust_block
    assert '"sharpmod_rs.sharpmod_rs"' in rust_block
    assert 'collect_all("sharpmod_rs")' in rust_block
    assert "release requires the sharpmod_rs native extension" in rust_block
    assert "release requires collecting sharpmod_rs" in rust_block
    assert "building a Python-only " in rust_block
    assert 'f"bundle (' in rust_block


def test_frozen_runtime_check_imports_cds_client():
    launcher = (ROOT / "packaging" / "sharpmod_gui_launcher.py").read_text(
        encoding="utf-8"
    )

    assert "import cdsapi" in launcher
    assert "import numcodecs" in launcher
    assert "import netCDF4" in launcher
    assert "import pyproj" in launcher
    assert "from logging.handlers import RotatingFileHandler" in launcher
    assert "from sharpmod.backends import backend_info, wind_to_components" in launcher
    assert "backend_kernel_ok=backend_kernel_ok" in launcher
    assert "wrf_runtime_ok=wrf_runtime_ok" in launcher
    assert "logging_handlers=bool(RotatingFileHandler)" in launcher
    assert "gui_entrypoint=callable(gui_main)" in launcher
