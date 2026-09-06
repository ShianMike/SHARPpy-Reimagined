"""The installation reference has to agree with the packaging metadata.

``installation.txt`` is what someone reads before they have a working
environment, so a wrong pin or a missing command there costs more than the same
mistake in code: it is spent on somebody who cannot yet run anything to find out
they were misled. It is written by hand and nothing generates it, so it drifted
-- a stale eccodes floor, a `[dev]` list missing the parallel and timeout
plugins the documented test lanes need, no mention of the `[quality]` extra
`CONTRIBUTING.md` tells contributors to install, and four of the ten console
commands absent, among them the forecast-model and area-sounding entry points.

These tests hold the document to ``pyproject.toml``, which is authoritative.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALL_PATH = ROOT / "installation.txt"
PYPROJECT_PATH = ROOT / "pyproject.toml"
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"


@pytest.fixture(scope="module")
def install_text():
    if not INSTALL_PATH.exists():
        pytest.skip("installation.txt is not present in this checkout")
    return INSTALL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project():
    if not PYPROJECT_PATH.exists():
        pytest.skip("pyproject.toml is not present in this checkout")
    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)["project"]


def _entry_points(project):
    points = dict(project.get("scripts") or {})
    points.update(project.get("gui-scripts") or {})
    return points


def _section(text, start, end):
    """The body of one numbered section of the reference."""
    assert start in text, "section %r is gone from installation.txt" % start
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end in body else body


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #
def test_every_core_requirement_is_quoted_exactly(install_text, project):
    """A pin the reader retypes has to be the pin that resolves."""
    for spec in project["dependencies"]:
        assert spec in install_text, (
            "installation.txt does not carry the core requirement %r" % spec)


def test_every_optional_requirement_is_quoted_exactly(install_text, project):
    for extra, specs in project["optional-dependencies"].items():
        for spec in specs:
            assert spec in install_text, (
                "installation.txt does not carry %r from [%s]" % (spec, extra))


def test_every_extra_is_explained(install_text, project):
    """A summary block is not documentation; each extra needs its purpose."""
    section = _section(install_text, "5. OPTIONAL EXTRAS",
                       "6. RUST-PRIMARY BACKEND")
    for extra in project["optional-dependencies"]:
        assert "[%s]" % extra in section, (
            "section 5 does not explain what [%s] is for" % extra)


def test_the_install_commands_offer_every_extra(install_text, project):
    section = _section(install_text, "2. QUICK INSTALL",
                       "3. CORE RUNTIME LIBRARIES")
    for extra in project["optional-dependencies"]:
        assert '".[%s]"' % extra in section, (
            "section 2 never shows how to install [%s]" % extra)


# --------------------------------------------------------------------------- #
# Console commands
# --------------------------------------------------------------------------- #
def test_every_console_command_is_listed(install_text, project):
    section = _section(install_text, "8. CONSOLE COMMANDS INSTALLED",
                       "9. VERIFY THE INSTALL")
    for name in _entry_points(project):
        assert name in section, (
            "section 8 does not list the %r command, so an installed entry "
            "point is undiscoverable" % name)


def test_no_command_is_invented(install_text, project):
    """A promised command that does not exist is worse than an absent one."""
    section = _section(install_text, "8. CONSOLE COMMANDS INSTALLED",
                       "9. VERIFY THE INSTALL")
    known = set(_entry_points(project))
    listed = set(re.findall(r"^\s{2}([a-z][a-z0-9-]+)\s{2,}\S", section, re.M))
    assert listed <= known, (
        "section 8 lists commands this project does not install: %s"
        % ", ".join(sorted(listed - known)))


def test_the_task_table_names_every_extra_it_relies_on(install_text, project):
    """The per-task table is how a reader decides which extras to install."""
    section = _section(install_text, "7. WHAT YOU NEED FOR EACH TASK",
                       "8. CONSOLE COMMANDS")
    for extra in ("era5", "wrf", "dev", "quality", "rust-build"):
        if extra in project["optional-dependencies"]:
            assert "[%s]" % extra in section, (
                "the task table never says what [%s] is needed for" % extra)


# --------------------------------------------------------------------------- #
# Versions stated in prose
# --------------------------------------------------------------------------- #
def test_the_supported_python_range_matches(install_text, project):
    requires = project["requires-python"].replace(" ", "")
    floor = re.search(r">=(\d+\.\d+)", requires)
    ceiling = re.search(r"<(\d+\.\d+)", requires)
    assert floor and floor.group(1) in install_text, (
        "installation.txt does not state the supported Python floor %s"
        % (floor.group(1) if floor else "?"))
    if ceiling:
        assert ceiling.group(1) in install_text, (
            "installation.txt does not state the Python ceiling %s"
            % ceiling.group(1))


def test_the_rust_toolchain_floor_matches_cargo(install_text):
    cargo = ROOT / "rust" / "sharpmod-rs" / "Cargo.toml"
    if not cargo.exists():
        pytest.skip("the Rust crate is not present in this checkout")
    stated = re.search(r'rust-version\s*=\s*"([^"]+)"',
                       cargo.read_text(encoding="utf-8"))
    if stated is None:
        pytest.skip("the crate does not declare rust-version")
    assert stated.group(1) in install_text, (
        "installation.txt promises a different Rust floor than Cargo.toml (%s)"
        % stated.group(1))


# --------------------------------------------------------------------------- #
# CONTRIBUTING.md
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def contributing_text():
    if not CONTRIBUTING_PATH.exists():
        pytest.skip("CONTRIBUTING.md is not present in this checkout")
    return CONTRIBUTING_PATH.read_text(encoding="utf-8")


def test_every_promised_test_lane_exists(contributing_text):
    runner = ROOT / "scripts" / "run_test_lane.py"
    if not runner.exists():
        pytest.skip("the lane runner is not present in this checkout")
    source = runner.read_text(encoding="utf-8")
    lanes = set(re.findall(r"run_test_lane\.py\s+([a-z][a-z-]*)",
                           contributing_text))
    assert lanes, "CONTRIBUTING.md no longer documents any test lane"
    for lane in sorted(lanes):
        assert '"%s"' % lane in source or "'%s'" % lane in source, (
            "CONTRIBUTING.md tells contributors to run the %r lane, which the "
            "runner does not define" % lane)


def test_every_promised_extra_is_installable(contributing_text, project):
    """The setup line must not ask for an extra that does not exist."""
    known = set(project["optional-dependencies"])
    for group in re.findall(r'"\.\[([a-z,\s-]+)\]"', contributing_text):
        for extra in (item.strip() for item in group.split(",")):
            assert extra in known, (
                "CONTRIBUTING.md installs the %r extra, which pyproject.toml "
                "does not define" % extra)


def test_every_referenced_file_exists(contributing_text):
    named = set(re.findall(r"`([\w./-]+\.(?:py|json|toml|txt|md))`",
                           contributing_text))
    assert named, "CONTRIBUTING.md references no project files at all"
    for relative in sorted(named):
        assert (ROOT / relative).exists(), (
            "CONTRIBUTING.md points at %s, which does not exist" % relative)


def test_the_upstream_patch_convention_matches_the_module(contributing_text):
    """The documented contract has to be the one the module actually offers."""
    from sharpmod import upstream_patches

    assert "upstream_patches.py" in contributing_text
    assert upstream_patches.HERBIE_SOURCE_FALLBACK_ISSUE.startswith("https://")
    assert upstream_patches.HERBIE_SOURCE_FALLBACK_PR.startswith("https://")
    # The self-retiring promise is the load-bearing part of the convention.
    assert upstream_patches.apply_herbie_source_fallback(None) is False
