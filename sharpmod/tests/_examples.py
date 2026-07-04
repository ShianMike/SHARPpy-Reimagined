"""Shared locator for the bundled example sounding inputs used by the tests.

The example inputs (SPC ``.OAX`` / ``.spc``, BUFKIT ``.buf``, HRRR ``.npz``)
live in the repository under ``examples/soundings``. This helper resolves that
directory robustly regardless of where the checkout lives, with fallbacks to
the historical ``Sounding Plots`` layout and the ``attic`` archive so the tests
keep working across reorganizations.
"""

from __future__ import annotations

from pathlib import Path

# One representative file that must exist in a valid examples directory.
_SENTINEL = "14061619.OAX"


def _repo_root() -> Path:
    # tests -> sharpmod -> <repo root>
    return Path(__file__).resolve().parents[2]


def _candidates():
    root = _repo_root()
    yield root / "examples" / "soundings"
    yield root / "examples"
    yield root / "Sounding Plots"
    yield root / "attic" / "sounding_plots_legacy"
    # Upward search for a legacy "Sounding Plots" directory (portability).
    here = Path(__file__).resolve()
    for base in (here, *here.parents):
        yield base / "Sounding Plots"
        yield base.parent / "Sounding Plots"


def examples_dir() -> Path:
    """Return the first existing directory that contains the example inputs.

    Falls back to ``<repo>/examples/soundings`` (which is where the inputs are
    committed) even if the sentinel is absent, so callers get a stable path.
    """
    seen = set()
    for cand in _candidates():
        if cand in seen:
            continue
        seen.add(cand)
        try:
            if (cand / _SENTINEL).is_file():
                return cand
        except OSError:
            continue
    return _repo_root() / "examples" / "soundings"
