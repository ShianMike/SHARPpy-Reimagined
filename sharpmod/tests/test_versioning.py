"""Package metadata and visible application labels share one version."""

from __future__ import annotations

import json
import importlib.util
import os
import tomllib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sharpmod
from sharpmod import render


ROOT = Path(__file__).resolve().parents[2]


def test_every_runtime_surface_uses_package_version():
    from sharpmod._version import __version__ as package_version

    assert sharpmod.__version__ == package_version
    assert render.application_label() == (
        f"SHARPpy Reimagined v{package_version}")


def test_pyproject_reads_the_version_attribute():
    document = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" not in document["project"]
    assert "version" in document["project"]["dynamic"]
    assert document["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "sharpmod._version.__version__",
    }


def test_current_package_retires_the_legacy_gui():
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "sharpmod-gui" not in document["project"].get("gui-scripts", {})
    for package in ("ui", "core", "state"):
        assert not any(name == f"sharpmod.{package}" or name.startswith(f"sharpmod.{package}.")
                       for name in document["tool"]["setuptools"]["packages"])
        assert not (ROOT / "sharpmod" / package).exists()
    assert not list((ROOT / "sharpmod/_compat").glob("gui_*.py"))
    for module in (
        "gui", "gui_picker", "gui_viewer", "local_time", "collection_identity",
        "recent_destinations", "run_updates", "saved_box_regions", "saved_locations",
        "session_merge", "sessions", "map_layers", "map_legends", "map_time",
    ):
        name = f"sharpmod.{module}"
        assert importlib.util.find_spec(name) is None, name


def test_unused_legacy_aliases_are_retired():
    for module in (
        "analysis.box_analysis_core",
        "analysis.box_analysis_fast",
        "providers.eccc_geomet_runtime",
        "maps.map_field_inspection",
        "models.model_transfer",
        "providers.openmeteo_runtime",
        "maps.overlay_hatch",
        "analysis.profile_metrics_engine",
    ):
        alias = f"sharpmod.{module.rsplit('.', 1)[1]}"
        canonical = f"sharpmod.{module}"
        assert importlib.util.find_spec(alias) is None, alias
        assert importlib.util.find_spec(canonical) is not None, canonical


def test_release_manifests_and_health_fixture_share_package_version():
    from sharpmod._version import __version__ as package_version

    for path in (ROOT / "src-tauri/contracts/public").glob("health*.json"):
        example = json.loads(path.read_text(encoding="utf-8"))
        assert example["app"]["version"] == package_version, path.name
        if example["python"] is not None:
            assert example["python"]["sharpmodVersion"] == package_version, path.name

    for relative, table in (
        ("rust/sharpmod-rs/Cargo.toml", "package"),
        ("rust/sharpmod-rs/pyproject.toml", "project"),
        ("src-tauri/Cargo.toml", "package"),
    ):
        document = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))
        assert document[table]["version"] == package_version, relative

    for relative, name in (
        ("rust/sharpmod-rs/Cargo.lock", "sharpmod-rs"),
        ("src-tauri/Cargo.lock", "sharppy-reimagined"),
    ):
        document = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))
        assert [
            package["version"] for package in document["package"]
            if package["name"] == name
        ] == [package_version], relative

    frontend = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert frontend["version"] == lock["version"] == lock["packages"][""]["version"] == package_version
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert config.get("version", package_version) == package_version

    health = json.loads(
        (ROOT / "src-tauri/contracts/public/health.ok.json").read_text(encoding="utf-8")
    )
    hello = json.loads(
        (ROOT / "src-tauri/contracts/bridge/hello.result.json").read_text(encoding="utf-8")
    )
    backend = json.loads(
        (ROOT / "src-tauri/contracts/bridge/health.result.ok.json").read_text(encoding="utf-8")
    )
    assert {
        health["app"]["version"], health["python"]["sharpmodVersion"],
        health["scientificBackend"]["rustVersion"],
        hello["sharpmod_version"], backend["scientific_backend"]["rust_version"],
    } == {package_version}
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert f'        default: "v{package_version}"' in workflow


def test_changelog_preserves_the_dated_legacy_release():
    from sharpmod._version import __version__ as package_version

    headings = [
        line.strip()
        for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## [v")
    ]

    assert headings[0].startswith(f"## [v{package_version}] - ") or (
        f"Target version: **{package_version}** (not published)."
        in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## [v", 1)[0]
        and f"## What's new in {package_version} (Unreleased)"
        in (ROOT / "README.md").read_text(encoding="utf-8")
    )
    assert "## [v2.0.0 Legacy] - 2026-09-26" in headings

    release_url = (
        "https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0"
    )
    for name in ("CHANGELOG.md", ".github/workflows/release.yml"):
        assert release_url in (ROOT / name).read_text(encoding="utf-8"), name


def test_readme_feature_summary_names_the_current_version():
    """Keep the README focused on the current release."""

    from sharpmod._version import __version__ as package_version

    headings = [
        line.strip()
        for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## What's new in ")
    ]
    assert [heading.removesuffix(" (Unreleased)") for heading in headings] == [
        f"## What's new in {package_version}",
    ]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released_versions = [
        line.split("[v", 1)[1].split("]", 1)[0].removesuffix(" Legacy")
        for line in changelog.splitlines() if line.startswith("## [v")
    ]
    for released in released_versions:
        if released != package_version:
            assert f"v{released}" not in readme, released
