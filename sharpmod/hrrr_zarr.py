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
from sharpmod.model_surface import merge_surface_level
from sharpmod.model_transport import DownloadCancelled


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
    """Discover every published pressure level and a non-duplicated field set."""
    available: dict[float, dict[str, tuple[str, dict]]] = {}
    for key, value in metadata.items():
        match = _ARRAY_PATTERN.match(str(key))
        if match is None:
            continue
        level_label, level_value, field = match.group(1, 2, 3)
        level = float(level_value)
        array_path = str(key).removesuffix("/.zarray")
        available.setdefault(level, {})[field] = (array_path, dict(value))
    if not available:
        raise ZarrBackendUnavailable(
            "HRRR Zarr metadata contains no pressure-level arrays"
        )

    levels = tuple(sorted(available, reverse=True))
    required = ("HGT", "TMP", "UGRD", "VGRD")
    for level in levels:
        missing = [field for field in required if field not in available[level]]
        if missing:
            raise ZarrBackendUnavailable(
                f"HRRR Zarr level {level:g} mb is missing {', '.join(missing)}"
            )
    fields = list(required)
    if all("RH" in available[level] for level in levels):
        fields.append("RH")
    elif all("SPFH" in available[level] for level in levels):
        fields.append("SPFH")
    else:
        raise ZarrBackendUnavailable(
            "HRRR Zarr pressure levels lack one complete humidity field"
        )
    for optional in ("VVEL", "ABSV"):
        if all(optional in available[level] for level in levels):
            fields.append(optional)

    arrays = {
        (level, field): available[level][field]
        for level in levels
        for field in fields
    }
    return PressurePlan(levels, tuple(fields), arrays)


def discover_surface_plan(metadata: dict) -> SurfacePlan:
    """Return the verified HRRR surface arrays or fail the point backend."""
    arrays = {}
    missing = []
    for field, metadata_key in _SURFACE_ARRAY_PATHS.items():
        value = metadata.get(metadata_key)
        if value is None:
            missing.append(field)
            continue
        arrays[field] = (
            metadata_key.removesuffix("/.zarray"),
            dict(value),
        )
    if missing:
        raise ZarrBackendUnavailable(
            "HRRR Zarr metadata is missing verified surface arrays: "
            + ", ".join(missing)
        )
    return SurfacePlan(arrays)


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
    """Decode one compressed Zarr v2 chunk and select its global point."""
    if metadata.get("filters") not in (None, []):
        raise ZarrBackendUnavailable("filtered HRRR Zarr arrays are unsupported")
    try:
        from numcodecs import get_codec
        compressor = metadata.get("compressor")
        raw = get_codec(compressor).decode(payload) if compressor else payload
        dtype = np.dtype(metadata["dtype"])
        shape = tuple(int(value) for value in metadata["shape"])
        chunks = tuple(int(value) for value in metadata["chunks"])
        if len(shape) != 2 or len(chunks) != 2:
            raise ValueError("expected a two-dimensional array")
        cy, cx = chunks
        chunk_y = iy // cy
        chunk_x = ix // cx
        local_y = iy - chunk_y * cy
        local_x = ix - chunk_x * cx
        edge_shape = (
            min(cy, shape[0] - chunk_y * cy),
            min(cx, shape[1] - chunk_x * cx),
        )
        values = np.frombuffer(raw, dtype=dtype)
        if values.size == cy * cx:
            decoded_shape = (cy, cx)
        elif values.size == edge_shape[0] * edge_shape[1]:
            decoded_shape = edge_shape
        else:
            raise ValueError("decoded chunk has an unexpected size")
        array = values.reshape(decoded_shape, order=metadata.get("order", "C"))
        return float(array[local_y, local_x])
    except ZarrBackendUnavailable:
        raise
    except Exception as exc:
        raise ZarrBackendUnavailable(
            "could not decode an HRRR Zarr chunk: %s" % exc
        ) from exc


def _root_url(run_dt: datetime) -> str:
    return run_dt.strftime(
        HRRR_ZARR_BASE + "/prs/%Y%m%d/%Y%m%d_%Hz_anl.zarr"
    )


def _point_dataset_from_columns(
    levels, columns, selected_lat, selected_lon, run_dt,
    requested_lat, requested_lon, surface=None,
):
    """Normalize raw HRRR arrays directly into the shared compact matrix."""
    levels = np.asarray(levels, dtype=np.float64)
    order = np.argsort(-levels)
    levels = levels[order]
    missing = -9999.0
    matrix = np.full(
        (len(GRIB_COLUMN_NAMES), levels.size), missing, dtype=np.float64
    )
    matrix[0] = levels

    def values(name):
        value = columns.get(name)
        if value is None:
            return None
        result = np.asarray(value, dtype=np.float64)[order]
        result = np.array(result, copy=True)
        result[~np.isfinite(result)] = missing
        return result

    height = values("HGT")
    temperature = values("TMP")
    u_wind = values("UGRD")
    v_wind = values("VGRD")
    if any(value is None for value in (
            height, temperature, u_wind, v_wind)):
        raise ZarrBackendUnavailable(
            "HRRR Zarr point is missing a required sounding column"
        )
    matrix[1] = height
    good_temperature = temperature != missing
    matrix[2, good_temperature] = temperature[good_temperature] - 273.15
    matrix[7] = u_wind
    matrix[8] = v_wind

    humidity = values("RH")
    if humidity is not None:
        good = good_temperature & (humidity != missing)
        temp_c = matrix[2, good]
        rh = np.clip(humidity[good], 1.0e-3, 100.0)
        gamma = np.log(rh / 100.0) + 17.625 * temp_c / (243.04 + temp_c)
        matrix[3, good] = 243.04 * gamma / (17.625 - gamma)
    else:
        humidity = values("SPFH")
        if humidity is None:
            raise ZarrBackendUnavailable(
                "HRRR Zarr point is missing RH and specific humidity"
            )
        good = good_temperature & (humidity != missing)
        q = humidity[good]
        vapor_pressure = q * levels[good] / (0.622 + 0.378 * q)
        logarithm = np.log(np.clip(vapor_pressure, 1.0e-6, None) / 6.112)
        matrix[3, good] = 243.04 * logarithm / (17.625 - logarithm)

    good_wind = (u_wind != missing) & (v_wind != missing)
    matrix[4, good_wind] = (
        270.0 - np.degrees(np.arctan2(
            v_wind[good_wind], u_wind[good_wind]
        ))
    ) % 360.0
    matrix[5, good_wind] = np.hypot(
        u_wind[good_wind], v_wind[good_wind]
    ) * 1.94384449

    omega = values("VVEL")
    if omega is not None:
        matrix[6] = omega

    surface = {} if surface is None else surface
    surface_merge = merge_surface_level(
        {
            name: matrix[index]
            for index, name in enumerate(GRIB_COLUMN_NAMES)
        },
        {
            "pres": (
                float(surface["PRES"]) / 100.0
                if "PRES" in surface else None
            ),
            "hght": surface.get("HGT"),
            "tmpc": (
                float(surface["TMP"]) - 273.15
                if "TMP" in surface else None
            ),
            "dwpc": (
                float(surface["DPT"]) - 273.15
                if "DPT" in surface else None
            ),
            "u": surface.get("UGRD"),
            "v": surface.get("VGRD"),
        },
        missing=missing,
    )
    if surface_merge is not None:
        matrix = np.vstack([
            surface_merge.columns[name] for name in GRIB_COLUMN_NAMES
        ])

    surface_vorticity = None
    absolute_vorticity = values("ABSV")
    if absolute_vorticity is not None:
        coriolis = (
            2.0 * 7.2921159e-5 * np.sin(np.radians(float(selected_lat)))
        )
        for pressure, value in zip(levels, absolute_vorticity):
            if (
                surface_merge is not None
                and pressure > surface_merge.surface_pressure
            ):
                continue
            if np.isfinite(value) and value != missing:
                surface_vorticity = float(value - coriolis)
                break

    decoded = DecodedPoint(
        matrix,
        float(selected_lat),
        float(selected_lon),
        surface_vorticity,
        surface_merge is not None,
        0 if surface_merge is None else surface_merge.removed_levels,
    )
    run_utc = run_dt
    if run_utc.tzinfo is None:
        run_utc = run_utc.replace(tzinfo=timezone.utc)
    else:
        run_utc = run_utc.astimezone(timezone.utc)
    return HrrrZarrPointDataset(
        decoded=decoded,
        requested_lat=float(requested_lat),
        requested_lon=float(requested_lon),
        run_time=run_utc,
        valid_time=run_utc,
    )


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
    cancelled=None,
    max_workers: int = 16,
):
    """Fetch every published HRRR pressure level for one native grid point."""
    if int(fxx) != 0:
        raise ZarrBackendUnavailable(
            "the public HRRR Zarr archive currently exposes analyses, not lead times"
        )
    root_url = _root_url(run_dt)
    cached = _load_cached_point(
        _cache_path(cache_dir), run_dt, float(lat), float(lon), root_url
    )
    if cached is not None:
        return cached

    session = None
    if get_bytes is None:
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
        except ImportError as exc:
            raise ZarrBackendUnavailable(
                "HRRR Zarr HTTPS access requires requests"
            ) from exc
        session = requests.Session()
        retries = Retry(
            total=2, connect=2, read=2, backoff_factor=0.2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount(
            "https://",
            HTTPAdapter(max_retries=retries, pool_connections=max_workers,
                        pool_maxsize=max_workers),
        )

        def get_bytes(url):
            with session.get(url, timeout=(5, 30)) as response:
                response.raise_for_status()
                return response.content

    downloaded = 0
    progress_lock = threading.Lock()

    def load(url):
        nonlocal downloaded
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        payload = get_bytes(url)
        with progress_lock:
            downloaded += len(payload)
            if progress is not None:
                progress(downloaded, 0)
        return payload

    try:
        metadata_payload = load(root_url + "/.zmetadata")
        consolidated = json.loads(metadata_payload.decode("utf-8"))
        plan = discover_pressure_plan(consolidated["metadata"])
        surface_plan = discover_surface_plan(consolidated["metadata"])
        iy, ix, selected_lat, selected_lon = hrrr_grid_index(lat, lon)
        columns = {
            field: np.full(len(plan.levels), np.nan, dtype=float)
            for field in plan.fields
        }
        surface = {}
        tasks = {}
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            for level_index, level in enumerate(plan.levels):
                for field in plan.fields:
                    array_path, metadata = plan.arrays[(level, field)]
                    chunks = tuple(int(value) for value in metadata["chunks"])
                    chunk_id = f"{iy // chunks[0]}.{ix // chunks[1]}"
                    url = f"{root_url}/{array_path}/{chunk_id}"
                    future = executor.submit(load, url)
                    tasks[future] = (
                        "pressure",
                        level_index,
                        field,
                        metadata,
                    )
            for field, (array_path, metadata) in surface_plan.arrays.items():
                chunks = tuple(int(value) for value in metadata["chunks"])
                chunk_id = f"{iy // chunks[0]}.{ix // chunks[1]}"
                url = f"{root_url}/{array_path}/{chunk_id}"
                future = executor.submit(load, url)
                tasks[future] = ("surface", None, field, metadata)
            for future in as_completed(tasks):
                kind, level_index, field, metadata = tasks[future]
                value = decode_zarr_point(
                    future.result(), metadata, iy=iy, ix=ix)
                if kind == "pressure":
                    columns[field][level_index] = value
                else:
                    surface[field] = value
        dataset = _point_dataset_from_columns(
            plan.levels, columns, selected_lat, selected_lon, run_dt,
            lat, lon, surface,
        )
        _write_cached_point(
            _cache_path(cache_dir), run_dt, float(lat), float(lon),
            selected_lat,
            selected_lon,
            plan,
            surface_plan,
            columns,
            surface,
        )
        source = HrrrZarrSource(
            root_url,
            root_url,
            plan.fields + tuple(surface_plan.arrays),
            downloaded_bytes=downloaded,
        )
        return dataset, source
    except (DownloadCancelled, ZarrBackendUnavailable):
        raise
    except Exception as exc:
        raise ZarrBackendUnavailable(
            "HRRR Zarr point retrieval failed: %s" % exc
        ) from exc
    finally:
        if session is not None:
            session.close()
