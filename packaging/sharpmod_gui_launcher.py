"""Frozen-app entry point for the SHARPpy Reimagined GUI.

PyInstaller freezes THIS module as the executable's entry script. It delegates
directly to :func:`sharpmod.gui_picker.main`, while keeping a dedicated launcher
that gives the bundle a stable, import-safe ``__main__`` and avoids loading the
compatibility facade before the picker appears.
"""

from __future__ import annotations

import importlib.metadata
import json
import multiprocessing
import os
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version


_FROZEN_DLL_DIRECTORIES = []
_FROZEN_DLL_HANDLES = []


def _prepare_frozen_dll_search() -> None:
    """Make sibling PySide6/shiboken6 DLL directories visible on Windows."""
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return
    meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for directory in (meipass, meipass / "PySide6", meipass / "shiboken6"):
        if not directory.is_dir():
            continue
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        try:
            _FROZEN_DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
        except (AttributeError, OSError):
            # Older Windows/Python combinations use PATH alone.
            pass
    # Load the ABI-bearing libraries from the bundle explicitly. This prevents
    # Windows from satisfying QtCore.pyd's imports with an unrelated Qt copy
    # that happens to be resident in the host process search path.
    try:
        import ctypes

        for library in (
            meipass / "shiboken6" / "shiboken6.abi3.dll",
            meipass / "PySide6" / "Qt6Core.dll",
            meipass / "PySide6" / "pyside6.abi3.dll",
        ):
            if library.is_file():
                _FROZEN_DLL_HANDLES.append(ctypes.WinDLL(str(library)))
    except (AttributeError, OSError):
        # The extension import below records the detailed loader error.
        pass


_prepare_frozen_dll_search()


def _versions_consistent(versions: dict[str, str]) -> bool:
    """Accept PEP 440-normalized metadata for one source release version."""
    try:
        return len({Version(value) for value in versions.values()}) == 1
    except InvalidVersion:
        return False


def _model_fetch_runtime_check(output_path: str) -> int:
    """Verify lazy GRIB dependencies inside a frozen release bundle."""
    result = {
        "ok": False,
        "frozen": bool(getattr(sys, "frozen", False)),
        "version_consistent": False,
    }
    try:
        # Load the real GUI entry point first, matching normal application
        # startup.  Importing the scientific DLL stack before Qt can make the
        # frozen Windows process resolve an incompatible shared library before
        # PySide6 has initialized its own runtime.
        import PySide6
        from PySide6 import QtCore

        from sharpmod.gui_picker import main as gui_main

        from logging.handlers import RotatingFileHandler

        import cdsapi
        import cfgrib
        import eccodes
        import herbie
        import netCDF4
        import numcodecs
        import pyproj
        import sharpmod
        import sharpmod_rs
        import xarray

        from sharpmod.backends import backend_info, wind_to_components
        from sharpmod.tools import model_extract, wrf_extract

        backend = backend_info()
        runtime_versions = {
            "sharpmod": sharpmod.__version__,
            "sharpmod_metadata": importlib.metadata.version("sharpmod"),
            "sharpmod_rs": sharpmod_rs.__version__,
            "sharpmod_rs_metadata": importlib.metadata.version("sharpmod-rs"),
            "backend_rust": str(backend["rust_version"]),
        }
        if not _versions_consistent(runtime_versions):
            raise RuntimeError(
                f"frozen runtime versions do not match: {runtime_versions}"
            )
        u_component, v_component = wind_to_components(270.0, 10.0)
        backend_kernel_ok = (
            abs(u_component - 10.0) <= 1.0e-12 and abs(v_component) <= 1.0e-12
        )
        if not backend_kernel_ok:
            raise RuntimeError(
                "backend wind_to_components smoke check returned "
                f"u={u_component!r}, v={v_component!r}"
            )
        wrf_runtime_ok = wrf_extract.require_runtime_dependencies()

        result.update(
            backend=backend,
            versions=runtime_versions,
            version_consistent=True,
            backend_kernel_ok=backend_kernel_ok,
            cdsapi=bool(cdsapi.Client),
            cfgrib=cfgrib.__version__,
            eccodes=eccodes.codes_get_api_version(),
            herbie=herbie.__version__,
            netcdf4=netCDF4.__version__,
            numcodecs=numcodecs.__version__,
            pyproj=pyproj.__version__,
            xarray=xarray.__version__,
            configured_models=len(model_extract.available_models()),
            wrf_runtime_ok=wrf_runtime_ok,
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
    # Import the picker directly so the frozen startup path does not load the
    # compatibility facade (and its viewer stack) before the first window.
    from sharpmod.gui_picker import main

    return main(sys.argv)


if __name__ == "__main__":
    # Safe no-op when unfrozen; required so a bundled child process (some Qt /
    # scientific libs may spawn one) re-runs this launcher instead of the app.
    multiprocessing.freeze_support()
    raise SystemExit(_run())
