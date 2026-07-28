"""Console-stream safeguards for Windows command-line and GUI runtimes."""

from __future__ import annotations

import os
import sys
from typing import Any


def configure_windows_unicode_streams(
    stdout: Any = None,
    stderr: Any = None,
) -> tuple[str, ...]:
    """Make Windows text streams safe for third-party Unicode status output.

    Herbie and several scientific dependencies print symbols such as checkmarks
    even when their verbose mode is disabled.  A redirected Windows stream may
    still use ``cp1252`` and raise :class:`UnicodeEncodeError` before a model
    download begins.  Reconfigure real text streams to UTF-8 with a
    non-throwing error policy, while quietly tolerating frozen/windowed
    processes where ``sys.stdout`` or ``sys.stderr`` is absent.

    The returned tuple names the streams that were reconfigured.  Supplying
    explicit streams is primarily useful for focused tests.
    """

    if not sys.platform.startswith("win"):
        return ()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8:backslashreplace")
    streams = {
        "stdout": sys.stdout if stdout is None else stdout,
        "stderr": sys.stderr if stderr is None else stderr,
    }
    changed = []
    for name, stream in streams.items():
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            continue
        changed.append(name)
    return tuple(changed)
