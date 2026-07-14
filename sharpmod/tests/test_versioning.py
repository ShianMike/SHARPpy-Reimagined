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
