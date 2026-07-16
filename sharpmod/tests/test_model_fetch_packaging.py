"""Frozen-app packaging contracts for live forecast-model support."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_installs_model_fetch_dependencies():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'python -m pip install -e ".[render,era5]"' in workflow
    assert "--model-fetch-runtime-check" in workflow
    assert "Verify frozen single-file runtime" in workflow
    assert workflow.count("--model-fetch-runtime-check") >= 2
    assert workflow.count("backend_kernel_ok") >= 2
    assert 'SHARPMOD_REQUIRE_RUST: "1"' in workflow
    assert 'SHARPMOD_BACKEND: "rust"' in workflow
    assert workflow.count('requested_backend -ne "rust"') >= 2
    assert workflow.count('active_backend -ne "rust"') >= 2


def test_pyinstaller_bundles_model_fetch_runtime():
    spec = (ROOT / "packaging" / "sharpmod_gui.spec").read_text(
        encoding="utf-8"
    )
    collection_block = spec.split("a = Analysis", 1)[0]
    for package in (
        "xarray", "herbie", "cfgrib", "eccodes", "cdsapi", "numcodecs",
        "pyproj",
    ):
        assert f'"{package}"' in collection_block

    excludes_block = spec.split("excludes=", 1)[1].split("]", 1)[0]
    assert '"cfgrib"' not in excludes_block
    assert '"herbie"' not in excludes_block

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
    assert "import pyproj" in launcher
    assert "from logging.handlers import RotatingFileHandler" in launcher
    assert "from sharpmod.backends import backend_info, wind_to_components" in launcher
    assert "backend_kernel_ok=backend_kernel_ok" in launcher
    assert "logging_handlers=bool(RotatingFileHandler)" in launcher
    assert "gui_entrypoint=callable(gui_main)" in launcher
