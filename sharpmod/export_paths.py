"""Resolve the one application-local destination for interactive exports.

The directory deliberately follows the application rather than the process:
source checkouts use the project root containing :mod:`sharpmod`, while frozen
builds use the directory containing the installed executable.  Current working
directory and user-profile folders are never candidates.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import Any


EXPORT_DIRECTORY_NAME = "rendered_soundings"

# ``export_dir`` was written by the interactive sounding viewer.  Keeping the
# key would let an older Desktop/Pictures selection return after a restart.
LEGACY_EXPORT_SETTING_KEYS = ("export_dir",)


class ExportDirectoryError(OSError):
    """Raised when the dedicated export directory cannot safely be used."""


def application_root() -> Path:
    """Return the installed application or source-project directory.

    PyInstaller extracts modules beneath ``sys._MEIPASS`` for one-file builds,
    so module location is intentionally ignored when ``sys.frozen`` is set.
    The executable's parent remains stable across launches and is also correct
    for the normal one-folder distribution.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def discard_stale_export_settings(settings: Any | None) -> tuple[str, ...]:
    """Remove remembered export directories from a QSettings-like object.

    Export destination choices are per operation.  They must not influence the
    next dialog or survive an application restart.  Failure to update a
    read-only settings store is harmless because no export code reads the keys.
    """

    if settings is None:
        return ()
    removed: list[str] = []
    try:
        for key in LEGACY_EXPORT_SETTING_KEYS:
            if settings.contains(key):
                settings.remove(key)
                removed.append(key)
        if removed:
            settings.sync()
    except (AttributeError, OSError, RuntimeError, TypeError):
        # The migration is best effort; callers always ignore these keys even
        # when the backing settings file itself cannot be changed.
        return tuple(removed)
    return tuple(removed)


def _assert_writable_directory(directory: Path) -> None:
    """Probe creation and deletion, which ``os.access`` cannot prove on ACLs."""

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".sharpmod-export-write-test-",
            dir=directory,
        ) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError as exc:
        raise ExportDirectoryError(
            f"The export folder is not writable: {directory} ({exc})"
        ) from exc


def export_directory(*, settings: Any | None = None) -> Path:
    """Create, validate, and return ``rendered_soundings``.

    There is intentionally no fallback.  A caller can show this function's
    path-rich exception to the user instead of silently writing somewhere else.
    """

    discard_stale_export_settings(settings)
    directory = application_root() / EXPORT_DIRECTORY_NAME
    if directory.exists() and not directory.is_dir():
        raise ExportDirectoryError(
            f"The export path exists but is not a directory: {directory}"
        )
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportDirectoryError(
            f"The export folder could not be created: {directory} ({exc})"
        ) from exc
    if not directory.is_dir():  # Covers a replacement racing with mkdir().
        raise ExportDirectoryError(
            f"The export path exists but is not a directory: {directory}"
        )
    _assert_writable_directory(directory)
    return directory


def export_file_path(
    default_name: str | os.PathLike[str],
    *,
    settings: Any | None = None,
) -> Path:
    """Return an export-dialog suggestion inside the dedicated directory."""

    name = Path(os.fspath(default_name)).name
    if not name or name in {".", ".."}:
        raise ValueError("an export filename is required")
    return export_directory(settings=settings) / name


__all__ = [
    "EXPORT_DIRECTORY_NAME",
    "ExportDirectoryError",
    "application_root",
    "discard_stale_export_settings",
    "export_directory",
    "export_file_path",
]
