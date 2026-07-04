"""Resolve bundled TTF fonts via :mod:`importlib.resources`.

The renderer needs real filesystem paths to hand to Qt
(``QtGui.QFontDatabase.addApplicationFont`` and ``QT_QPA_FONTDIR``). This
module resolves the fonts that are bundled as package data under
``sharpmod/resources/fonts/`` without ever hard-coding an absolute path.

For the common source / unpacked-wheel install the fonts already live on the
filesystem and their real paths are returned directly. For zip-imported or
otherwise non-filesystem installs the fonts are materialized into a temporary
directory whose lifetime is tied to the interpreter process.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from contextlib import ExitStack
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

#: Import path of the subpackage that holds the bundled TTF files.
FONTS_PACKAGE = "sharpmod.resources.fonts"

# Manages any temporary files/directories extracted from a non-filesystem
# package. Closed automatically at interpreter exit so extracted fonts remain
# valid for the whole process lifetime.
_file_manager = ExitStack()
atexit.register(_file_manager.close)


def _fonts_root() -> Traversable:
    """Return the traversable root of the bundled-fonts subpackage."""
    return resources.files(FONTS_PACKAGE)


def font_names() -> list[str]:
    """Return the sorted names of every bundled ``.ttf`` file."""
    return sorted(
        entry.name
        for entry in _fonts_root().iterdir()
        if entry.is_file() and entry.name.lower().endswith(".ttf")
    )


def font_path(name: str) -> Path:
    """Return a real filesystem path to the bundled font ``name``.

    Raises:
        FileNotFoundError: if no bundled font with that name exists.
    """
    resource = _fonts_root() / name
    if not resource.is_file():
        raise FileNotFoundError(f"bundled font not found: {name!r}")
    return _file_manager.enter_context(resources.as_file(resource))


def fonts_dir() -> Path:
    """Return a real filesystem directory containing all bundled fonts.

    Suitable for use as ``QT_QPA_FONTDIR``.
    """
    root = _fonts_root()

    # Fast path: the resource is already on the filesystem.
    try:
        candidate = Path(os.fspath(root))
    except TypeError:
        candidate = None
    if candidate is not None and candidate.is_dir():
        return candidate

    # Fallback: materialize each font into a process-lifetime temp directory.
    tmp_dir = Path(_file_manager.enter_context(tempfile.TemporaryDirectory()))
    for name in font_names():
        src = _file_manager.enter_context(resources.as_file(_fonts_root() / name))
        shutil.copy2(src, tmp_dir / name)
    return tmp_dir
