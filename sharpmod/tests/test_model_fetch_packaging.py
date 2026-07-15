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


def test_frozen_runtime_check_imports_cds_client():
    launcher = (ROOT / "packaging" / "sharpmod_gui_launcher.py").read_text(
        encoding="utf-8"
    )

    assert "import cdsapi" in launcher
    assert "import numcodecs" in launcher
    assert "import pyproj" in launcher
    assert "from logging.handlers import RotatingFileHandler" in launcher
    assert "logging_handlers=bool(RotatingFileHandler)" in launcher
    assert "gui_entrypoint=callable(gui_main)" in launcher
