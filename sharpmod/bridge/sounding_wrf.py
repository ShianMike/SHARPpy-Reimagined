"""Local raw WRF imports through the existing NetCDF point extractor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_cache import cache
from sharpmod.bridge.sounding_summary import decode, register, summary


def _wrf_path(params: dict[str, Any]) -> Path:
    path = Path(p.text(params, "file_path", 1, 4096))
    if not path.is_absolute() or not path.is_file():
        raise p.invalid("file_path must name an existing local WRF NetCDF file")
    return path.resolve()


def inspect_wrf(params: dict[str, Any]) -> dict[str, Any]:
    """Read domain coordinates and available times without uploading the dataset."""
    p.keys(params, ("file_path",))
    path = _wrf_path(params)
    from sharpmod.tools import wrf_extract

    try:
        domain = wrf_extract.inspect_file(str(path))
    except (wrf_extract.RetrievalError, ImportError) as exc:
        raise BridgeError("SOUNDING_INVALID", str(exc)[:500], False) from None
    times = [p.iso(time) for time in domain["times"]]
    if not times or any(time is None for time in times):
        raise p.invalid("the WRF file has no valid forecast times")
    return {
        "file_path": str(path), "file_name": path.name, "times": times,
        "latitude": domain["center"][0], "longitude": domain["center"][1],
    }


def open_wrf(params: dict[str, Any]) -> dict[str, Any]:
    """Open a local WRF profile through the native cache transaction."""
    from sharpmod_rs import open_wrf_sounding

    return open_wrf_sounding(params)
