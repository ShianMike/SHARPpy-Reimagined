"""Packaging + example-rendering smoke tests (task 17.7).

These are deliberately lightweight, environment-facing smoke tests that exercise
the *packaging* and *portability* guarantees of the standalone fork end to end,
rather than any single unit of logic:

* **11.5** -- running the modernized render path on an existing example input
  produces a non-empty output file that decodes as a valid PNG.
* **15.1** -- the top-level ``sharpmod`` package (and its subpackages) import
  cleanly with only the declared dependencies present.
* **15.2** -- bundled resources, in particular the TTF fonts, resolve from the
  installed *package* location via ``importlib.resources`` (package-relative),
  never a hard-coded absolute development path.
* **15.3** -- every runtime dependency in the packaging manifest declares an
  explicit version constraint (minimum, maximum, or bounded range).
* **15.4** -- rendering a bundled example needs **no** manual path
  configuration: the call takes only the input and output paths and the fonts /
  resources resolve themselves.

Portability notes (this suite is intended to also run on a fresh Linux VM):

* Example inputs are located by searching *upward* from this test file for the
  repo's ``Sounding Plots`` directory, so nothing is pinned to an absolute
  Windows path. If the directory cannot be found the input-dependent tests skip.
* No Windows-specific assumption (e.g. ``C:\\Windows\\Fonts``) is ever required:
  the font-resolution assertions check the *package-relative* bundled fonts.
* The full window render depends on the upstream ``sharppy`` widget stack. Its
  vendored (pip-installed) Qt widgets still use Qt5-style *instance* enum access
  (e.g. ``qp.Antialiasing``) which raises ``AttributeError`` under Qt6/PySide6;
  until that vendored port lands, constructing the window fails. The render
  test therefore runs when the path works and **skips** (rather than fails) when
  it hits that documented vendored-Qt6 seam, so it is ready to pass unchanged
  the moment a Qt6-ported ``sharppy`` is installed.

**Validates: Requirements 11.5, 15.1, 15.2, 15.3, 15.4**
"""

from __future__ import annotations

import os

# Headless Qt must be selected before qtpy imports a platform plugin. The
# renderer sets this itself too; setting it here keeps the test independent of
# import order and models the fresh-install invocation.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

# 15.1 -- the package (and subpackages) must import with only declared deps.
import sharpmod
from sharpmod.resources import font_resolver

# The 8-byte PNG file signature (used when Pillow is unavailable).
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Bundled example inputs exercised by the render smoke test (one per supported
# on-disk format present in the repo's "Sounding Plots" directory).
_EXAMPLE_INPUTS = [
    "hrrr_point_36.68N_95.66W_f018.npz",  # custom HRRR .npz point sounding
    "14061619.OAX",                        # SPC tabular observed sounding
    "hrrr_point_36.68N_95.66W_f018.spc",  # SPC tabular (model point)
    "hrrr_kbvo_20260625_06z.buf",         # BUFKIT model sounding
]


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def sounding_plots_dir() -> Path:
    """Locate the repo's example-soundings directory (repo-relative).

    Resolved by :func:`sharpmod.tests._examples.examples_dir` (which searches
    ``examples/soundings`` first, with fallbacks), so the tests are portable to
    any checkout location. Skips the dependent tests when the example inputs
    cannot be found rather than hard-coding a path.
    """
    from sharpmod.tests._examples import examples_dir

    candidate = examples_dir()
    if not candidate.is_dir():
        pytest.skip("example inputs unavailable: examples/soundings not found")
    return candidate


def _decodes_as_png(path: str) -> bool:
    """Return True if ``path`` is a non-empty, decodable PNG.

    Prefers Pillow (a real decode); falls back to verifying the PNG file
    signature when Pillow is not installed so the check still runs on a minimal
    environment.
    """
    if os.path.getsize(path) == 0:
        return False
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as img:
            img.verify()  # full structural decode/verify
        return True
    except ImportError:
        with open(path, "rb") as fh:
            return fh.read(len(_PNG_MAGIC)) == _PNG_MAGIC


# --------------------------------------------------------------------------- #
# 15.1 -- fresh-install importability
# --------------------------------------------------------------------------- #
def test_top_level_package_and_subpackages_importable():
    """``sharpmod`` and its declared subpackages import cleanly (15.1)."""
    import importlib

    assert sharpmod.__name__ == "sharpmod"
    for sub in ("sharpmod.io", "sharpmod.sharptab", "sharpmod.viz",
                "sharpmod.tools", "sharpmod.resources"):
        module = importlib.import_module(sub)
        assert module is not None

    # The render entry point imports without a display / manual configuration.
    from sharpmod import render as render_mod
    assert hasattr(render_mod, "render")


# --------------------------------------------------------------------------- #
# 15.2 -- bundled fonts resolve package-relative (no absolute dev path)
# --------------------------------------------------------------------------- #
def test_fonts_resolve_relative_to_installed_package():
    """The font resolver returns paths derived from the package location (15.2)."""
    names = font_resolver.font_names()
    assert names, "no bundled TTF fonts were discovered"

    package_root = Path(sharpmod.__file__).resolve().parent

    fonts_dir = Path(font_resolver.fonts_dir()).resolve()
    assert fonts_dir.is_dir()

    # Each bundled font resolves to a real file that exists on disk.
    for name in names:
        path = Path(font_resolver.font_path(name)).resolve()
        assert path.is_file(), f"bundled font not resolved to a real file: {name}"

    # For the standard source / unpacked-wheel install the fonts live *inside*
    # the installed package tree -- i.e. resolution is package-relative rather
    # than a hard-coded absolute development path. (In a zip-import install the
    # resolver materializes them to a temp dir instead; that path is not under
    # the package, so this assertion is scoped to the on-disk install case.)
    try:
        fonts_dir.relative_to(package_root)
        under_package = True
    except ValueError:
        under_package = False
    assert under_package, (
        f"fonts dir {fonts_dir} is not package-relative to {package_root}"
    )


def test_font_resolution_does_not_require_system_font_dir(monkeypatch):
    """Font resolution never depends on an absolute system font directory (15.2).

    The bundled fonts resolve purely from the package, so clearing any
    ``QT_QPA_FONTDIR`` override still yields real font paths. This guards the
    portability guarantee that no ``C:\\Windows\\Fonts`` (or other absolute
    system path) is a hard requirement.
    """
    monkeypatch.delenv("QT_QPA_FONTDIR", raising=False)

    names = font_resolver.font_names()
    assert names
    path = Path(font_resolver.font_path(names[0]))
    assert path.is_file()


# --------------------------------------------------------------------------- #
# 15.3 -- packaging manifest declares bounded version constraints
# --------------------------------------------------------------------------- #
def test_runtime_dependencies_declare_version_constraints():
    """Every runtime dependency has an explicit version constraint (15.3)."""
    tomllib = pytest.importorskip(
        "tomllib", reason="tomllib (Python 3.11+) required to parse pyproject")

    # pyproject.toml lives at the repository root (the standard layout: the
    # manifest sits above the ``sharpmod`` package directory). Search upward
    # from the package so the check works from a source tree regardless of how
    # deeply the package is nested.
    pkg_dir = Path(sharpmod.__file__).resolve().parent
    pyproject = None
    for parent in [pkg_dir, *pkg_dir.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            pyproject = candidate
            break
    assert pyproject is not None, (
        f"packaging manifest missing at or above: {pkg_dir}")

    with open(pyproject, "rb") as fh:
        manifest = tomllib.load(fh)

    deps = manifest["project"]["dependencies"]
    assert deps, "no runtime dependencies declared"

    # A constraint is any PEP 508 version specifier operator.
    operators = ("<", ">", "=", "~", "!")
    unconstrained = [
        d for d in deps
        if not any(op in d for op in operators)
    ]
    assert not unconstrained, (
        f"runtime dependencies missing a version constraint: {unconstrained}"
    )


# --------------------------------------------------------------------------- #
# 11.5 / 15.4 -- render a bundled example to a decodable PNG, no path config
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("example_name", _EXAMPLE_INPUTS)
def test_example_input_renders_to_decodable_png(
    example_name, sounding_plots_dir, tmp_path
):
    """A bundled example renders headless to a non-empty, decodable PNG.

    Covers 11.5 (non-empty, decodable PNG for an example input) and 15.4 (no
    manual path configuration -- :func:`sharpmod.render.render` is called with
    only the input and output paths and the bundled resources resolve
    themselves). Rendering runs in-process headless via the Qt ``offscreen``
    platform; the modernized render path composes the vendored ``SPCWindow``
    under PySide6/Qt6 through the ``sharpmod.viz._qt6_compat`` compatibility
    layer (no subprocess isolation and no vendored-Qt6 skip needed).

    **Validates: Requirements 11.5, 15.4**
    """
    # Composing the window needs the upstream widget stack.
    pytest.importorskip("sharppy", reason="upstream sharppy widget stack required")
    from sharpmod.render import render

    infile = sounding_plots_dir / example_name
    if not infile.is_file():
        pytest.skip(f"example input not present in this checkout: {example_name}")

    outfile = tmp_path / (infile.stem + ".png")

    # 15.4: only input/output paths are passed; fonts/resources self-resolve.
    result_path = render(str(infile), str(outfile))

    # 11.5: the output exists, is non-empty, and decodes as a valid PNG. A
    # failed render must never leave a partial file (Req 11.7).
    assert os.path.abspath(result_path) == os.path.abspath(str(outfile))
    assert outfile.is_file(), "render reported success but wrote no file"
    assert outfile.stat().st_size > 0, "render produced an empty PNG"
    assert _decodes_as_png(str(outfile)), "output file is not a decodable PNG"
