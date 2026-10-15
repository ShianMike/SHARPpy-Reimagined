"""Compatibility patches for the vendored SHARPpy stack, without Qt.

``sharpmod/viz/_qt6_compat.py`` restores the NumPy scalar aliases and the
``pwv_climo`` fix that the vendored ``sharppy`` profile code needs on modern
NumPy. Importing it normally runs ``sharpmod.viz.__init__``, which loads Qt, so
the bridge executes that one file on its own: the same code, and no Qt.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

_LOCK = threading.Lock()
_APPLIED = False


def ensure_profile_compat() -> None:
    """Apply the NumPy-alias and ``pwv_climo`` patches once per process."""
    global _APPLIED
    with _LOCK:
        if _APPLIED:
            return
        import sharpmod

        path = Path(sharpmod.__file__).resolve().parent / "viz" / "_qt6_compat.py"
        spec = importlib.util.spec_from_file_location(
            "sharpmod_bridge_qt6_compat", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._patch_numpy_aliases()  # noqa: SLF001 - the same patch the GUI applies
        module._patch_sharppy_pwv_climo()  # noqa: SLF001
        _APPLIED = True
