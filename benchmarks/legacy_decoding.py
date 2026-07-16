"""Frozen pre-optimization cfgrib/xarray model-point decoder.

This module exists only for performance comparisons.  It intentionally owns a
copy of the model-point algorithm that was in production before the decoding
optimization work began: cfgrib opens every compatible hypercube, xarray
merges the pressure-level datasets, NumPy scans the horizontal grid, and the
selected column is materialized field by field.

Do not import production extraction helpers here.  Keeping this implementation
self-contained prevents later production improvements from silently changing
the benchmark labelled ``old-python`` or ``old-rust``.  The old Rust label is
the same cfgrib/xarray decoder with the then-current native wind-conversion
kernel; the native extension did not yet own full GRIB iteration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import warnings

import numpy as np
import numpy.ma as ma


MISSING = -9999.0
G0 = 9.80665
EARTH_RADIUS_M = 6_371_008.8
EARTH_ROTATION_RATE = 7.2921159e-5

_LEVEL_COORDS = (
    "isobaricInhPa",
    "level",
    "pressure_level",
    "plev",
    "levels",
)
_TIME_COORDS = ("time", "valid_time", "forecast_time")
_LAT_COORDS = ("latitude", "lat")
_LON_COORDS = ("longitude", "lon")

_VAR_TEMP = ("t", "temperature", "tmp")
_VAR_GEOPOTENTIAL = ("z", "geopotential")
_VAR_GEOPOTENTIAL_HEIGHT = (
    "gh",
    "geopotential_height",
    "hgt",
)
_VAR_RH = ("r", "relative_humidity", "rh")
_VAR_Q = ("q", "specific_humidity", "spfh")
_VAR_U = ("u", "u_component_of_wind", "ugrd")
_VAR_V = ("v", "v_component_of_wind", "vgrd")
_VAR_W = ("w", "vertical_velocity", "vvel", "dzdt")
_VAR_RELATIVE_VORTICITY = (
    "vo",
    "vort",
    "relative_vorticity",
    "relv",
)
_VAR_ABSOLUTE_VORTICITY = ("absv", "absolute_vorticity")

ARRAY_FIELDS = (
    "pres",
    "hght",
    "tmpc",
    "dwpc",
    "wdir",
    "wspd",
    "omeg",
    "uwnd",
    "vwnd",
)


class LegacyDecodingError(RuntimeError):
    """The frozen decoder cannot read or normalize the requested fixture."""


class LegacyBackendUnavailable(LegacyDecodingError):
    """The requested old backend cannot be loaded in this environment."""


@dataclass(frozen=True)
class LegacyPoint:
    """One decoded pressure-level point sounding."""

    pres: np.ndarray
    hght: np.ndarray
    tmpc: np.ndarray
    dwpc: np.ndarray
    wdir: np.ndarray
    wspd: np.ndarray
    omeg: np.ndarray
    uwnd: np.ndarray
    vwnd: np.ndarray
    requested_lat: float
    requested_lon: float
    selected_lat: float
    selected_lon: float
    selected_valid: datetime | None
    backend: str
    surface_relative_vorticity: float | None = None

    @property
    def arrays(self) -> dict[str, np.ndarray]:
        """Return the scientific columns under the portable NPZ field names."""

        return {name: np.asarray(getattr(self, name)) for name in ARRAY_FIELDS}

    @property
    def metadata(self) -> dict[str, Any]:
        valid = self.selected_valid
        return {
            "requested_lat": self.requested_lat,
            "requested_lon": self.requested_lon,
            "selected_lat": self.selected_lat,
            "selected_lon": self.selected_lon,
            "selected_valid": (
                None if valid is None else valid.strftime("%Y-%m-%d %H:%M")
            ),
            "levels": int(self.pres.size),
            "backend": self.backend,
            "surface_relative_vorticity": self.surface_relative_vorticity,
        }


def _first_present(container: Any, candidates: Sequence[str]) -> str | None:
    for name in candidates:
        if name in container:
            return name
    return None


def _coord_values(ds: Any, candidates: Sequence[str]) -> tuple[str | None, Any]:
    name = _first_present(ds.coords, candidates)
    if name is None:
        name = _first_present(ds, candidates)
    if name is None:
        return None, None
    return name, np.asarray(ds[name].values)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, np.datetime64):
        nanoseconds = value.astype("datetime64[ns]").astype("int64")
        return datetime.fromtimestamp(nanoseconds / 1e9, tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(
        tzinfo=timezone.utc
    )


def _timestamp(value: Any) -> float:
    parsed = _as_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _select_time(ds: Any, valid_time: datetime | None) -> tuple[Any, datetime | None]:
    name, values = _coord_values(ds, _TIME_COORDS)
    if name is None or values is None or values.size == 0:
        return ds, valid_time
    flattened = values.reshape(-1)
    if values.ndim == 0 or flattened.size == 1:
        return ds, _as_datetime(flattened[0])
    if valid_time is None:
        index = 0
    else:
        target = _timestamp(valid_time)
        index = int(
            np.argmin([abs(_timestamp(value) - target) for value in flattened])
        )
    return ds.isel({name: index}), _as_datetime(flattened[index])


def _great_circle_distance_km(
    lat1: float,
    lon1: float,
    lat2: Any,
    lon2: Any,
) -> np.ndarray:
    radius_km = 6371.0088
    lat1r = np.radians(lat1)
    lat2r = np.radians(lat2)
    dlat = np.radians(np.asarray(lat2) - lat1)
    dlon = np.radians(np.asarray(lon2) - lon1)
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_km * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _select_nearest_grid_point(
    lats: Any,
    lons: Any,
    requested_lat: float,
    requested_lon: float,
) -> tuple[tuple[int, int], float, float]:
    lat_values = np.asarray(lats, dtype=float)
    lon_values = np.asarray(lons, dtype=float)
    if lat_values.ndim == 0 and lon_values.ndim == 0:
        return (0, 0), float(lat_values), float(lon_values)
    if lat_values.ndim == 1 and lon_values.ndim == 1:
        lat_grid, lon_grid = np.meshgrid(
            lat_values, lon_values, indexing="ij"
        )
        distance = _great_circle_distance_km(
            requested_lat, requested_lon, lat_grid, lon_grid
        )
        iy, ix = np.unravel_index(np.argmin(distance), distance.shape)
        return (int(iy), int(ix)), float(lat_values[iy]), float(lon_values[ix])
    distance = _great_circle_distance_km(
        requested_lat, requested_lon, lat_values, lon_values
    )
    iy, ix = np.unravel_index(np.argmin(distance), distance.shape)
    return (
        (int(iy), int(ix)),
        float(lat_values[iy, ix]),
        float(lon_values[iy, ix]),
    )


def _dewpoint_from_rh(tmpc: Any, rh: Any) -> np.ndarray:
    a, b = 17.625, 243.04
    humidity = np.clip(np.asarray(rh, dtype=float), 1e-3, 100.0)
    temperature = np.asarray(tmpc, dtype=float)
    gamma = np.log(humidity / 100.0) + (a * temperature) / (b + temperature)
    return (b * gamma) / (a - gamma)


def _dewpoint_from_specific_humidity(q: Any, pressure: Any) -> np.ndarray:
    humidity = np.asarray(q, dtype=float)
    pressure_hpa = np.asarray(pressure, dtype=float)
    vapor_pressure = (humidity * pressure_hpa) / (0.622 + 0.378 * humidity)
    vapor_pressure = np.clip(vapor_pressure, 1e-6, None)
    a, b = 17.625, 243.04
    logarithm = np.log(vapor_pressure / 6.112)
    return (b * logarithm) / (a - logarithm)


def _components_to_wind_numpy(u: Any, v: Any) -> tuple[np.ndarray, np.ndarray]:
    u_values = np.asarray(u, dtype=np.float64)
    v_values = np.asarray(v, dtype=np.float64)
    speed = np.hypot(u_values, v_values)
    direction = (
        270.0 - np.degrees(np.arctan2(v_values, u_values))
    ) % 360.0
    return direction, speed


def _components_to_wind_rust(u: Any, v: Any) -> tuple[np.ndarray, np.ndarray]:
    try:
        import sharpmod_rs
    except (ImportError, OSError) as exc:
        raise LegacyBackendUnavailable(
            "old-rust requires the sharpmod_rs extension"
        ) from exc
    function = getattr(sharpmod_rs, "components_to_wind", None)
    if not callable(function):
        raise LegacyBackendUnavailable(
            "sharpmod_rs does not expose components_to_wind"
        )
    u_values = np.ascontiguousarray(u, dtype=np.float64).reshape(-1)
    v_values = np.ascontiguousarray(v, dtype=np.float64).reshape(-1)
    direction, speed = function(u_values, v_values, None)
    return np.asarray(direction, dtype=float), np.asarray(speed, dtype=float)


def _components_to_wind(
    u: Any,
    v: Any,
    backend: str,
) -> tuple[np.ndarray, np.ndarray]:
    if backend == "python":
        return _components_to_wind_numpy(u, v)
    if backend == "rust":
        return _components_to_wind_rust(u, v)
    raise ValueError("legacy backend must be 'python' or 'rust'")


def _column_reader(ds: Any, iy: int, ix: int):
    def get(candidates: Sequence[str]) -> np.ndarray | None:
        name = _first_present(ds, candidates)
        if name is None:
            return None
        values = np.squeeze(np.asarray(ds[name].values, dtype=float))
        if values.ndim == 1:
            return values
        if values.ndim == 3:
            return values[:, iy, ix]
        if values.ndim == 2:
            return values[:, ix]
        return None

    return get


def _mark_missing(values: Any, levels: int) -> np.ndarray:
    if values is None:
        return np.full(levels, MISSING, dtype=float)
    result = np.asarray(values, dtype=float).copy()
    result[~np.isfinite(result)] = MISSING
    return result


def _first_surface_value(values: Any) -> float | None:
    if values is None:
        return None
    for value in np.asarray(values, dtype=float).reshape(-1):
        if np.isfinite(value) and value != MISSING:
            return float(value)
    return None


def _coriolis_parameter(latitude: float) -> float:
    return float(
        2.0 * EARTH_ROTATION_RATE * np.sin(np.radians(float(latitude)))
    )


def _surface_vorticity_from_column(
    values: Any,
    levels: np.ndarray,
    latitude: float | None,
    *,
    absolute: bool = False,
) -> float | None:
    if values is None:
        return None
    data = np.asarray(values, dtype=float)
    if absolute:
        if latitude is None:
            return None
        data = data - _coriolis_parameter(latitude)
    if data.size != levels.size:
        return None
    order = np.argsort(-levels)
    return _first_surface_value(_mark_missing(data, data.size)[order])


def _neighbor_pair(index: int, size: int) -> tuple[int, int] | None:
    if size < 2:
        return None
    if index <= 0:
        return 0, 1
    if index >= size - 1:
        return size - 2, size - 1
    return index - 1, index + 1


def _wrapped_lon_delta(lon1: float, lon2: float) -> float:
    return ((float(lon2) - float(lon1) + 180.0) % 360.0) - 180.0


def _east_west_distance_m(lat: float, lon1: float, lon2: float) -> float:
    return float(
        EARTH_RADIUS_M
        * np.cos(np.radians(float(lat)))
        * np.radians(_wrapped_lon_delta(lon1, lon2))
    )


def _north_south_distance_m(lat1: float, lat2: float) -> float:
    return float(EARTH_RADIUS_M * np.radians(float(lat2) - float(lat1)))


def _surface_vorticity_from_grid(
    ds: Any,
    index_tuple: tuple[int, int],
    levels: np.ndarray,
) -> float | None:
    u_name = _first_present(ds, _VAR_U)
    v_name = _first_present(ds, _VAR_V)
    if u_name is None or v_name is None:
        return None
    try:
        u = np.squeeze(np.asarray(ds[u_name].values, dtype=float))
        v = np.squeeze(np.asarray(ds[v_name].values, dtype=float))
    except Exception:
        return None
    if u.ndim != 3 or v.ndim != 3 or u.shape != v.shape:
        return None
    _, lats = _coord_values(ds, _LAT_COORDS)
    _, lons = _coord_values(ds, _LON_COORDS)
    if lats is None or lons is None:
        return None
    iy, ix = index_tuple
    if iy < 0 or ix < 0 or iy >= u.shape[1] or ix >= u.shape[2]:
        return None
    try:
        level_index = int(np.nanargmax(levels))
    except Exception:
        return None
    u2d = u[level_index]
    v2d = v[level_index]
    x_pair = _neighbor_pair(ix, u2d.shape[1])
    y_pair = _neighbor_pair(iy, u2d.shape[0])
    if x_pair is None or y_pair is None:
        return None
    x0, x1 = x_pair
    y0, y1 = y_pair
    lat_values = np.asarray(lats, dtype=float)
    lon_values = np.asarray(lons, dtype=float)
    if lat_values.ndim == 1 and lon_values.ndim == 1:
        dx = _east_west_distance_m(lat_values[iy], lon_values[x0], lon_values[x1])
        dy = _north_south_distance_m(lat_values[y0], lat_values[y1])
    else:
        if lat_values.shape != u2d.shape or lon_values.shape != u2d.shape:
            return None
        dx = _east_west_distance_m(
            lat_values[iy, ix], lon_values[iy, x0], lon_values[iy, x1]
        )
        dy = _north_south_distance_m(lat_values[y0, ix], lat_values[y1, ix])
    if not (np.isfinite(dx) and np.isfinite(dy)) or abs(dx) < 1.0 or abs(dy) < 1.0:
        return None
    value = (
        (float(v2d[iy, x1]) - float(v2d[iy, x0])) / dx
        - (float(u2d[y1, ix]) - float(u2d[y0, ix])) / dy
    )
    return float(value) if np.isfinite(value) else None


def _build_columns(
    ds: Any,
    index_tuple: tuple[int, int],
    latitude: float,
    backend: str,
) -> tuple[dict[str, Any], int]:
    _, levels = _coord_values(ds, _LEVEL_COORDS)
    if levels is None or levels.size == 0:
        raise LegacyDecodingError("GRIB has no pressure-level coordinate")
    pressure = np.asarray(levels, dtype=float).reshape(-1)
    if np.nanmax(pressure) > 2000.0:
        pressure = pressure / 100.0
    iy, ix = index_tuple
    get = _column_reader(ds, iy, ix)

    temperature_raw = get(_VAR_TEMP)
    tmpc = None if temperature_raw is None else temperature_raw - 273.15
    height = get(_VAR_GEOPOTENTIAL_HEIGHT)
    if height is None:
        geopotential = get(_VAR_GEOPOTENTIAL)
        height = None if geopotential is None else geopotential / G0

    dewpoint = None
    rh = get(_VAR_RH)
    if rh is not None and tmpc is not None:
        dewpoint = _dewpoint_from_rh(tmpc, rh)
    else:
        specific_humidity = get(_VAR_Q)
        if specific_humidity is not None:
            dewpoint = _dewpoint_from_specific_humidity(
                specific_humidity, pressure
            )

    u = get(_VAR_U)
    v = get(_VAR_V)
    if u is None or v is None:
        direction = speed = None
    else:
        direction, speed = _components_to_wind(u, v, backend)
        speed = np.asarray(speed, dtype=float) * 1.94384449

    omega = get(_VAR_W)
    relative_vorticity = _surface_vorticity_from_column(
        get(_VAR_RELATIVE_VORTICITY), pressure, latitude
    )
    if relative_vorticity is None:
        relative_vorticity = _surface_vorticity_from_column(
            get(_VAR_ABSOLUTE_VORTICITY),
            pressure,
            latitude,
            absolute=True,
        )
    if relative_vorticity is None:
        relative_vorticity = _surface_vorticity_from_grid(
            ds, index_tuple, pressure
        )

    count = pressure.size
    columns: dict[str, Any] = {
        "pres": _mark_missing(pressure, count),
        "hght": _mark_missing(height, count),
        "tmpc": _mark_missing(tmpc, count),
        "dwpc": _mark_missing(dewpoint, count),
        "wdir": _mark_missing(direction, count),
        "wspd": _mark_missing(speed, count),
        "omeg": _mark_missing(omega, count),
        "uwnd": _mark_missing(u, count),
        "vwnd": _mark_missing(v, count),
        "surface_relative_vorticity": relative_vorticity,
    }
    order = np.argsort(-columns["pres"])
    for name in ARRAY_FIELDS:
        columns[name] = columns[name][order]
    return columns, count


def _merge_pressure_datasets(datasets: Sequence[Any]) -> Any:
    try:
        import xarray as xr
    except ImportError as exc:
        raise LegacyBackendUnavailable(
            "legacy decoding requires xarray"
        ) from exc
    pressure_datasets = [
        dataset
        for dataset in datasets
        if any(name in dataset.coords for name in _LEVEL_COORDS)
    ]
    if not pressure_datasets:
        raise LegacyDecodingError(
            "cfgrib returned no pressure-level dataset"
        )
    return xr.merge(
        pressure_datasets,
        compat="override",
        join="outer",
    )


class LegacyDataset:
    """An opened and merged legacy cfgrib/xarray inventory."""

    def __init__(
        self,
        path: Path,
        datasets: Sequence[Any],
        merged: Any,
        backend: str,
    ) -> None:
        self.path = path
        self.datasets = tuple(datasets)
        self.dataset = merged
        self.backend = backend
        self._closed = False

    def load(self) -> "LegacyDataset":
        self.dataset.load()
        return self

    def decode_point(
        self,
        lat: float,
        lon: float,
        valid_time: datetime | None = None,
    ) -> LegacyPoint:
        if self._closed:
            raise LegacyDecodingError("legacy dataset is already closed")
        requested_lat = float(lat)
        requested_lon = float(lon)
        selected_ds, selected_time = _select_time(self.dataset, valid_time)
        _, lats = _coord_values(selected_ds, _LAT_COORDS)
        _, lons = _coord_values(selected_ds, _LON_COORDS)
        if lats is None or lons is None:
            raise LegacyDecodingError(
                "GRIB dataset is missing latitude/longitude coordinates"
            )
        longitude_for_search = requested_lon
        try:
            if np.nanmin(lons) >= 0.0 and requested_lon < 0.0:
                longitude_for_search = requested_lon + 360.0
        except Exception:
            pass
        indexes, selected_lat, selected_lon = _select_nearest_grid_point(
            lats,
            lons,
            requested_lat,
            longitude_for_search,
        )
        selected_lon = ((selected_lon + 180.0) % 360.0) - 180.0
        columns, _ = _build_columns(
            selected_ds,
            indexes,
            selected_lat,
            self.backend,
        )
        return LegacyPoint(
            pres=columns["pres"],
            hght=columns["hght"],
            tmpc=columns["tmpc"],
            dwpc=columns["dwpc"],
            wdir=columns["wdir"],
            wspd=columns["wspd"],
            omeg=columns["omeg"],
            uwnd=columns["uwnd"],
            vwnd=columns["vwnd"],
            requested_lat=requested_lat,
            requested_lon=requested_lon,
            selected_lat=selected_lat,
            selected_lon=selected_lon,
            selected_valid=selected_time,
            backend=self.backend,
            surface_relative_vorticity=columns[
                "surface_relative_vorticity"
            ],
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        objects = (self.dataset, *self.datasets)
        seen: set[int] = set()
        for value in objects:
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            close = getattr(value, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def __enter__(self) -> "LegacyDataset":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def open_grib(
    grib_path: str | Path,
    *,
    backend: str = "python",
    indexpath: str = "",
    load: bool = False,
) -> LegacyDataset:
    """Open and merge one local GRIB through the frozen legacy path."""

    path = Path(grib_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if backend not in {"python", "rust"}:
        raise ValueError("backend must be 'python' or 'rust'")
    if backend == "rust":
        # Fail before an expensive GRIB scan if the extension is unavailable.
        _components_to_wind_rust(
            np.asarray([0.0], dtype=np.float64),
            np.asarray([0.0], dtype=np.float64),
        )
    try:
        import cfgrib
    except ImportError as exc:
        raise LegacyBackendUnavailable(
            "legacy decoding requires cfgrib"
        ) from exc
    backend_kwargs: Mapping[str, Any] = {"indexpath": str(indexpath)}
    source_datasets: list[Any] = []
    try:
        # xarray currently warns once for every cfgrib hypercube about a future
        # merge default.  Terminal I/O is not decoder work and can dominate a
        # cold sample, so keep that third-party advisory outside measurements.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=FutureWarning,
                module=r"cfgrib\.xarray_store",
            )
            source_datasets = list(
                cfgrib.open_datasets(
                    str(path),
                    backend_kwargs=dict(backend_kwargs),
                )
            )
        merged = _merge_pressure_datasets(source_datasets)
        opened = LegacyDataset(path, source_datasets, merged, backend)
        return opened.load() if load else opened
    except BaseException:
        for dataset in source_datasets:
            close = getattr(dataset, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise


def decode_grib_point(
    grib_path: str | Path,
    lat: float,
    lon: float,
    *,
    backend: str = "python",
    valid_time: datetime | None = None,
    indexpath: str = "",
) -> LegacyPoint:
    """Decode one point and close all cfgrib/xarray objects afterward."""

    with open_grib(
        grib_path,
        backend=backend,
        indexpath=indexpath,
        load=False,
    ) as opened:
        return opened.decode_point(lat, lon, valid_time)


def build_profile(point: LegacyPoint) -> Any:
    """Construct the real Profile from an already-decoded point."""

    from sharpmod.sharptab.profile import Profile

    def masked(values: Any) -> ma.MaskedArray:
        array = ma.masked_invalid(ma.asarray(values, dtype=float))
        return ma.masked_where(np.asarray(array) == MISSING, array)

    return Profile(
        masked(point.pres),
        masked(point.hght),
        masked(point.tmpc),
        masked(point.dwpc),
        masked(point.wdir),
        masked(point.wspd),
        omeg=masked(point.omeg),
        meta=point.metadata,
    )


def point_digest(point: LegacyPoint) -> tuple[Any, ...]:
    """Return a cheap value used to keep timed results observable."""

    first = tuple(
        float(values[0]) if values.size else float("nan")
        for values in point.arrays.values()
    )
    return (
        point.pres.size,
        point.selected_lat,
        point.selected_lon,
        *first,
    )
