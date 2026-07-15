"""Frozen-app entry point for the SHARPpy Reimagined GUI.

PyInstaller freezes THIS module as the executable's entry script. It simply
delegates to :func:`sharpmod.gui.main`, but keeping a dedicated launcher (rather
than pointing PyInstaller at ``sharpmod/gui.py`` directly) gives the bundle a
stable, import-safe ``__main__`` that never runs as part of the package.
"""

from __future__ import annotations

import multiprocessing
import json
import sys
from pathlib import Path


def _model_fetch_runtime_check(output_path: str) -> int:
    """Verify lazy GRIB dependencies inside a frozen release bundle."""
    result = {
        "ok": False,
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        from logging.handlers import RotatingFileHandler

        import cdsapi
        import cfgrib
        import eccodes
        import herbie
        import numcodecs
        import pyproj
        import xarray

        from sharpmod.gui import main as gui_main
        from sharpmod.tools import model_extract

        result.update(
            cdsapi=bool(cdsapi.Client),
            cfgrib=cfgrib.__version__,
            eccodes=eccodes.codes_get_api_version(),
            herbie=herbie.__version__,
            numcodecs=numcodecs.__version__,
            pyproj=pyproj.__version__,
            xarray=xarray.__version__,
            configured_models=len(model_extract.available_models()),
            logging_handlers=bool(RotatingFileHandler),
            gui_entrypoint=callable(gui_main),
            ok=True,
        )
    except BaseException as exc:  # noqa: BLE001 - diagnostics must be recorded
        result["error"] = f"{type(exc).__name__}: {exc}"

    Path(output_path).write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0 if result["ok"] else 1


def _run() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--model-fetch-runtime-check":
        return _model_fetch_runtime_check(sys.argv[2])
    from sharpmod.gui import main
    return main(sys.argv)


if __name__ == "__main__":
    # Safe no-op when unfrozen; required so a bundled child process (some Qt /
    # scientific libs may spawn one) re-runs this launcher instead of the app.
    multiprocessing.freeze_support()
    raise SystemExit(_run())
