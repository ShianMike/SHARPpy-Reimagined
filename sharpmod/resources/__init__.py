"""Package-relative resources for SHARPpy Reimagined (bundled fonts, etc.).

All resource access is performed through :mod:`importlib.resources` so that
resources resolve correctly regardless of where the package is installed
(source checkout, wheel, or zip import) and never rely on absolute filesystem
paths.
"""

from __future__ import annotations

from sharpmod.resources.font_resolver import (
    FONTS_PACKAGE,
    font_names,
    font_path,
    fonts_dir,
)

__all__ = ["FONTS_PACKAGE", "font_names", "font_path", "fonts_dir"]
