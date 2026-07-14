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

from sharpmod.model_transport import DownloadCancelled


HRRR_ZARR_BASE = "https://hrrrzarr.s3.amazonaws.com"
HRRR_SHAPE = (1059, 1799)
HRRR_X0 = -2697520.1425219304
HRRR_Y0 = -1587306.1525566636
HRRR_SPACING = 3000.0


class ZarrBackendUnavailable(RuntimeError):
    """The HRRR point Zarr backend is unavailable for this request."""


@dataclass(frozen=True)
class PressurePlan:
    levels: tuple[float, ...]
    fields: tuple[str, ...]
    arrays: dict[tuple[float, str], tuple[str, dict]]


@dataclass
class HrrrZarrSource:
    grib: str
    _sharpmod_source_url: str
    _sharpmod_fields: tuple[str, ...]
    _sharpmod_transport: str = "hrrr-zarr-point"
    downloaded_bytes: int = 0


_ARRAY_PATTERN = re.compile(
    r"^((\d+(?:\.\d+)?)mb)/([A-Z0-9]+)/(\1)/(\3)/\.zarray$"
)


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


def _dataset_from_columns(levels, columns, selected_lat, selected_lon, run_dt):
    try:
        import xarray as xr
    except ImportError as exc:
        raise ZarrBackendUnavailable("HRRR Zarr requires xarray") from exc
    dims = ("isobaricInhPa", "latitude", "longitude")
    names = {
        "HGT": "gh",
        "TMP": "t",
        "RH": "r",
        "SPFH": "q",
        "UGRD": "u",
        "VGRD": "v",
        "VVEL": "w",
        "ABSV": "absv",
    }
    data_vars = {
        names[field]: (dims, np.asarray(values, dtype=float)[:, None, None])
        for field, values in columns.items()
    }
    run_utc = run_dt
    if run_utc.tzinfo is not None:
        run_utc = run_utc.astimezone(timezone.utc).replace(tzinfo=None)
    return xr.Dataset(
        data_vars=data_vars,
        coords={
            "isobaricInhPa": np.asarray(levels, dtype=float),
            "latitude": np.asarray([selected_lat], dtype=float),
            "longitude": np.asarray([selected_lon], dtype=float),
            "time": np.datetime64(run_utc),
        },
        attrs={"model": "hrrr", "description": "HRRR Zarr point sounding"},
    )


def _cache_path(cache_dir) -> Path | None:
    return None if cache_dir is None else Path(cache_dir) / "hrrr-zarr-point.npz"


def _load_cached_point(path, run_dt, requested_lat, requested_lon, root_url):
    if path is None or not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as cached:
            if str(cached["run"]) != run_dt.isoformat():
                return None
            if abs(float(cached["requested_lat"]) - requested_lat) > 1e-6:
                return None
            if abs(float(cached["requested_lon"]) - requested_lon) > 1e-6:
                return None
            fields = tuple(str(value) for value in cached["fields"])
            columns = {field: cached[field] for field in fields}
            dataset = _dataset_from_columns(
                cached["levels"], columns,
                float(cached["selected_lat"]),
                float(cached["selected_lon"]), run_dt,
            )
        return dataset, HrrrZarrSource(root_url, root_url, fields)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cached_point(
    path, run_dt, requested_lat, requested_lon, selected_lat, selected_lon,
    plan, columns,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        payload = {
            "run": run_dt.isoformat(),
            "requested_lat": float(requested_lat),
            "requested_lon": float(requested_lon),
            "selected_lat": float(selected_lat),
            "selected_lon": float(selected_lon),
            "levels": np.asarray(plan.levels, dtype=float),
            "fields": np.asarray(plan.fields),
            **columns,
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
        iy, ix, selected_lat, selected_lon = hrrr_grid_index(lat, lon)
        columns = {
            field: np.full(len(plan.levels), np.nan, dtype=float)
            for field in plan.fields
        }
        tasks = {}
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            for level_index, level in enumerate(plan.levels):
                for field in plan.fields:
                    array_path, metadata = plan.arrays[(level, field)]
                    chunks = tuple(int(value) for value in metadata["chunks"])
                    chunk_id = f"{iy // chunks[0]}.{ix // chunks[1]}"
                    url = f"{root_url}/{array_path}/{chunk_id}"
                    future = executor.submit(load, url)
                    tasks[future] = (level_index, field, metadata)
            for future in as_completed(tasks):
                level_index, field, metadata = tasks[future]
                columns[field][level_index] = decode_zarr_point(
                    future.result(), metadata, iy=iy, ix=ix
                )
        dataset = _dataset_from_columns(
            plan.levels, columns, selected_lat, selected_lon, run_dt
        )
        _write_cached_point(
            _cache_path(cache_dir), run_dt, float(lat), float(lon),
            selected_lat, selected_lon, plan, columns,
        )
        source = HrrrZarrSource(
            root_url, root_url, plan.fields, downloaded_bytes=downloaded
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
