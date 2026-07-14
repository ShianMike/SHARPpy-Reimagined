"""SHARPpy Reimagined: a standalone, modernized fork of the SHARPpy sounding toolkit.

The top-level package exposes the derived-parameter library (``sharptab``), the
input decoders (``io``), the Qt6/PySide6 rendering widgets (``viz``), and the
data-extraction tools (``tools``). It targets Python >= 3.11, NumPy >= 1.24, and
PySide6 (Qt6) with no legacy compatibility shims.
"""

from ._version import __version__

__all__ = ["__version__", "io", "sharptab", "viz", "tools"]
