"""Frozen-app packaging contracts for live forecast-model support."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_installs_model_fetch_dependencies():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'python -m pip install -e ".[render,era5]"' in workflow
    assert "--model-fetch-runtime-check" in workflow


def test_pyinstaller_bundles_model_fetch_runtime():
    spec = (ROOT / "packaging" / "sharpmod_gui.spec").read_text(
        encoding="utf-8"
    )
    collection_block = spec.split("a = Analysis", 1)[0]
    for package in ("xarray", "herbie", "cfgrib", "eccodes"):
        assert f'"{package}"' in collection_block

    excludes_block = spec.split("excludes=", 1)[1].split("]", 1)[0]
    assert '"cfgrib"' not in excludes_block
    assert '"herbie"' not in excludes_block
