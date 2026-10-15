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
    """Use the existing raw WRF extractor, keeping the source dataset untouched."""
    p.keys(params, ("file_path", "time", "latitude", "longitude"))
    path = _wrf_path(params)
    time = p.time(params, "time")
    lat = p.number(params, "latitude", -90.0, 90.0)
    lon = p.number(params, "longitude", -180.0, 180.0)
    from sharpmod.tools import wrf_extract

    entry_id = cache().new_id()
    output = cache().data_path(entry_id, ".npz")
    try:
        domain = wrf_extract.inspect_file(str(path))
        if p.iso(time) not in [p.iso(value) for value in domain["times"]]:
            raise p.invalid("time must be one of the WRF file's forecast times")
        if not wrf_extract.point_in_domain(domain, lat, lon):
            raise p.invalid("the requested point is outside this WRF domain")
        wrf_extract.extract(str(path), lat, lon, str(output), valid_time=time)
        collection, _ = decode(output)
    except (wrf_extract.RetrievalError, wrf_extract.ParameterRangeError, ImportError) as exc:
        output.unlink(missing_ok=True)
        output.with_suffix(".json").unlink(missing_ok=True)
        raise BridgeError("SOUNDING_INVALID", str(exc)[:500], False) from None
    except Exception:
        output.unlink(missing_ok=True)
        output.with_suffix(".json").unlink(missing_ok=True)
        raise
    return register(
        entry_id, output,
        summary(entry_id, "file", collection, title=f"WRF {path.name}",
                file_name=path.name, model="WRF-ARW", provider="local",
                provider_name="Local WRF NetCDF"),
        collection,
    )
