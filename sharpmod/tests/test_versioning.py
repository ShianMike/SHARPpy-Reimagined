"""Package metadata and visible application labels share one version."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sharpmod
from sharpmod import gui, render


ROOT = Path(__file__).resolve().parents[2]


def test_every_runtime_surface_uses_package_version():
    from sharpmod._version import __version__ as package_version

    assert package_version == "2.0.0"
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


def test_v2_changelog_has_a_release_date():
    headings = [
        line.strip()
        for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]

    assert headings[0] == "## [v2.0.0] - 2026-09-26"


def test_readme_feature_summary_names_the_current_version():
    """Do not ship a 2.x feature tour under the previous release's heading."""

    from sharpmod._version import __version__ as package_version

    headings = [
        line.strip()
        for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## What's new in ")
    ]
    assert headings == [f"## What's new in {package_version}"]
