"""Package metadata and visible application labels share one version."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sharpmod
from sharpmod import gui, render


ROOT = Path(__file__).resolve().parents[2]


def test_every_runtime_surface_uses_package_version():
    from sharpmod._version import __version__ as package_version

    assert package_version == "3.0.1"
    assert sharpmod.__version__ == package_version
    assert gui.APP_VERSION == package_version
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


def test_release_manifests_and_health_fixture_share_package_version():
    from sharpmod._version import __version__ as package_version

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
    assert {
        health["app"]["version"], health["python"]["sharpmodVersion"],
        health["scientificBackend"]["rustVersion"],
    } == {package_version}
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert f'        default: "v{package_version}"' in workflow


def test_changelog_preserves_the_dated_legacy_release():
    headings = [
        line.strip()
        for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]

    assert headings[:3] == [
        "## [v3.0.1] - 2026-10-15",
        "## [v3.0.0] - 2026-10-15",
        "## [v2.0.0 Legacy] - 2026-09-26",
    ]

    release_url = (
        "https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0"
    )
    for name in ("CHANGELOG.md", "README.md", ".github/workflows/release.yml"):
        assert release_url in (ROOT / name).read_text(encoding="utf-8"), name


def test_readme_feature_summary_names_the_current_version():
    """Keep the current feature summary distinct from the legacy screenshots."""

    from sharpmod._version import __version__ as package_version

    headings = [
        line.strip()
        for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## What's new in ")
    ]
    assert headings == [f"## What's new in {package_version}"]
