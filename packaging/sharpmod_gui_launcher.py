"""Frozen-app entry point for the SHARPpy Reimagined GUI.

PyInstaller freezes THIS module as the executable's entry script. It simply
delegates to :func:`sharpmod.gui.main`, but keeping a dedicated launcher (rather
than pointing PyInstaller at ``sharpmod/gui.py`` directly) gives the bundle a
stable, import-safe ``__main__`` that never runs as part of the package.
"""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    from sharpmod.gui import main
    return main(sys.argv)


if __name__ == "__main__":
    # Safe no-op when unfrozen; required so a bundled child process (some Qt /
    # scientific libs may spawn one) re-runs this launcher instead of the app.
    multiprocessing.freeze_support()
    raise SystemExit(_run())
