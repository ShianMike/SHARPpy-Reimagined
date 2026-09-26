"""SHARPpy Reimagined: a standalone, modernized fork of the SHARPpy sounding toolkit.

The top-level package exposes the derived-parameter library (``sharptab``), the
input decoders (``io``), the Qt6/PySide6 rendering widgets (``viz``), and the
data-extraction tools (``tools``). It targets Python >= 3.11, NumPy >= 1.24, and
PySide6 (Qt6) with no legacy compatibility shims.
"""

from pathlib import Path as _Path

# Keep imports like ``sharpmod.ui.features.gui_maps`` working while their implementations
# live in the role-based packages. Compatibility files live in one directory
# so the package root only contains its entry points and package metadata.
__path__.append(str(_Path(__file__).resolve().parent / "_compat"))

from ._version import __version__
from .console import configure_windows_unicode_streams

# Configure this before model/provider modules are imported.  In particular,
# Herbie may emit Unicode status glyphs to a redirected CP1252 Windows stream.
configure_windows_unicode_streams()

__all__ = [
    "__version__",
    "configure_windows_unicode_streams",
    "io",
    "sharptab",
    "viz",
    "tools",
]
