"""Low-level HRRR analysis Zarr point sounding backend."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading

import numpy as np

from sharpmod.backends.grib import DecodedPoint, GRIB_COLUMN_NAMES
from sharpmod.models.wind_reference import rotate_winds
from sharpmod.models.lambert_grid import HRRR_GRID
from sharpmod.models.model_surface import merge_surface_level
from sharpmod.models.model_transport import DownloadCancelled


HRRR_ZARR_BASE = "https://hrrrzarr.s3.amazonaws.com"
HRRR_SHAPE = (1059, 1799)
HRRR_X0 = -2697520.1425219304
HRRR_Y0 = -1587306.1525566636
HRRR_SPACING = 3000.0
_POINT_CACHE_SCHEMA = 2


class ZarrBackendUnavailable(RuntimeError):
    """The HRRR point Zarr backend is unavailable for this request."""


@dataclass(frozen=True)
class PressurePlan:
    levels: tuple[float, ...]
    fields: tuple[str, ...]
    arrays: dict[tuple[float, str], tuple[str, dict]]


@dataclass(frozen=True)
class SurfacePlan:
    """Exact HRRR ground and near-ground arrays required for a surface row."""

    arrays: dict[str, tuple[str, dict]]


@dataclass
class HrrrZarrSource:
    grib: str
    _sharpmod_source_url: str
    _sharpmod_fields: tuple[str, ...]
    _sharpmod_transport: str = "hrrr-zarr-point"
    downloaded_bytes: int = 0


@dataclass
class HrrrZarrPointDataset:
    """Normalized point profile that avoids constructing an xarray dataset."""

    decoded: DecodedPoint
    requested_lat: float
    requested_lon: float
    run_time: datetime
    valid_time: datetime

    def close(self):
        """Match the model-hour cache dataset protocol (there is no handle)."""


_ARRAY_PATTERN = re.compile(
    r"^((\d+(?:\.\d+)?)mb)/([A-Z0-9]+)/(\1)/(\3)/\.zarray$"
)
_SURFACE_ARRAY_PATHS = {
    "PRES": "surface/PRES/surface/PRES/.zarray",
    "HGT": "surface/HGT/surface/HGT/.zarray",
    "TMP": "2m_above_ground/TMP/2m_above_ground/TMP/.zarray",
    "DPT": "2m_above_ground/DPT/2m_above_ground/DPT/.zarray",
    "UGRD": "10m_above_ground/UGRD/10m_above_ground/UGRD/.zarray",
    "VGRD": "10m_above_ground/VGRD/10m_above_ground/VGRD/.zarray",
}


def discover_pressure_plan(metadata: dict) -> PressurePlan:
    from sharpmod_rs import hrrr_pressure_plan

    return hrrr_pressure_plan(metadata)


def discover_surface_plan(metadata: dict) -> SurfacePlan:
    from sharpmod_rs import hrrr_surface_plan

    return hrrr_surface_plan(metadata)


def _hrrr_transformers():
    try:
        from pyproj import CRS, Transformer
    except ImportError as exc:
        raise ZarrBackendUnavailable(
            "HRRR Zarr point access requires pyproj"
        ) from exc
    crs = CRS.from_proj4(
        "+proj=lcc +lat_0=38.5 +lon_0=262.5 +lat_1=38.5 "
        "+lat_2=38.5 +R=6371229 +units=m +no_defs"
    )
    return (
        Transformer.from_crs("EPSG:4326", crs, always_xy=True),
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True),
    )


def hrrr_grid_index(lat: float, lon: float) -> tuple[int, int, float, float]:
    """Map a latitude/longitude to the nearest native 3-km HRRR grid point."""
    forward, inverse = _hrrr_transformers()
    x, y = forward.transform(float(lon), float(lat))
    ix = int(round((x - HRRR_X0) / HRRR_SPACING))
    iy = int(round((y - HRRR_Y0) / HRRR_SPACING))
    if not (0 <= iy < HRRR_SHAPE[0] and 0 <= ix < HRRR_SHAPE[1]):
        raise ZarrBackendUnavailable("requested point is outside the HRRR Zarr grid")
    selected_x = HRRR_X0 + ix * HRRR_SPACING
    selected_y = HRRR_Y0 + iy * HRRR_SPACING
    selected_lon, selected_lat = inverse.transform(selected_x, selected_y)
    selected_lon = ((float(selected_lon) + 180.0) % 360.0) - 180.0
    return iy, ix, float(selected_lat), selected_lon


def decode_zarr_point(payload: bytes, metadata: dict, *, iy: int, ix: int) -> float:
    from sharpmod_rs import decode_hrrr_zarr_point

    return decode_hrrr_zarr_point(payload, metadata, iy, ix)


def _root_url(run_dt: datetime) -> str:
    return run_dt.strftime(
        HRRR_ZARR_BASE + "/prs/%Y%m%d/%Y%m%d_%Hz_anl.zarr"
    )


def _point_dataset_from_columns(
    levels, columns, selected_lat, selected_lon, run_dt,
    requested_lat, requested_lon, surface=None,
):
    from sharpmod_rs import hrrr_zarr_point_dataset

    return hrrr_zarr_point_dataset(levels, columns, selected_lat, selected_lon,
                                   run_dt, requested_lat, requested_lon, surface)


def _cache_path(cache_dir) -> Path | None:
    return None if cache_dir is None else Path(cache_dir) / "hrrr-zarr-point.npz"


def _load_cached_point(path, run_dt, requested_lat, requested_lon, root_url):
    if path is None or not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as cached:
            if int(cached["schema_version"]) != _POINT_CACHE_SCHEMA:
                return None
            if str(cached["run"]) != run_dt.isoformat():
                return None
            if abs(float(cached["requested_lat"]) - requested_lat) > 1e-6:
                return None
            if abs(float(cached["requested_lon"]) - requested_lon) > 1e-6:
                return None
            fields = tuple(str(value) for value in cached["fields"])
            columns = {field: cached[field] for field in fields}
            surface_fields = tuple(
                str(value) for value in cached["surface_fields"]
            )
            surface = {
                field: float(cached[f"surface_{field}"])
                for field in surface_fields
            }
            dataset = _point_dataset_from_columns(
                cached["levels"], columns,
                float(cached["selected_lat"]),
                float(cached["selected_lon"]), run_dt,
                requested_lat, requested_lon, surface,
            )
        return dataset, HrrrZarrSource(
            root_url,
            root_url,
            fields + surface_fields,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cached_point(
    path, run_dt, requested_lat, requested_lon, selected_lat, selected_lon,
    plan, surface_plan, columns, surface,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        payload = {
            "schema_version": _POINT_CACHE_SCHEMA,
            "run": run_dt.isoformat(),
            "requested_lat": float(requested_lat),
            "requested_lon": float(requested_lon),
            "selected_lat": float(selected_lat),
            "selected_lon": float(selected_lon),
            "levels": np.asarray(plan.levels, dtype=float),
            "fields": np.asarray(plan.fields),
            "surface_fields": np.asarray(tuple(surface_plan.arrays)),
            **columns,
            **{
                f"surface_{field}": float(value)
                for field, value in surface.items()
            },
        }
        with os.fdopen(fd, "wb") as handle:
            np.savez(handle, **payload)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def fetch_hrrr_zarr_point(
    run_dt: datetime,
    fxx: int,
    lat: float,
    lon: float,
    *,
    cache_dir=None,
    get_bytes=None,
    progress=None,
    on_bytes=None,
    cancelled=None,
    max_workers: int = 16,
):
    from sharpmod_rs import fetch_hrrr_zarr_native

    return fetch_hrrr_zarr_native(run_dt, fxx, lat, lon, locals())
