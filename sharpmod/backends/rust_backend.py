"""Python adapter around the optional :mod:`sharpmod_rs` extension."""

from __future__ import annotations

from collections import OrderedDict
from functools import cache
import importlib
import os
from pathlib import Path
import threading

import numpy as np

from ._common import (
    prepare_1d,
    prepare_broadcast_pair,
    prepare_interpolation,
    prepare_qc_columns,
    restore_array,
    restore_pair,
)
from .grib import DecodedPoint, GribDecodeError, load_eccodes
from .protocol import QualityControlResult


_CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
_GRIB_POINT_CACHE_MAX = 128


@cache
def _eccodes_library_path() -> str:
    """Return the absolute ecCodes library selected by the Python binding."""
    eccodes = load_eccodes()
    get_path = getattr(eccodes, "codes_get_library_path", None)
    if get_path is None:
        raise GribDecodeError(
            "the installed ecCodes Python binding does not expose "
            "codes_get_library_path"
        )
    try:
        path = Path(os.fsdecode(get_path())).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise GribDecodeError(
            f"could not locate the ecCodes shared library: {exc}"
        ) from exc
    if not path.is_file():
        raise GribDecodeError(
            f"ecCodes shared library path is not a file: {path}"
        )
    return os.fspath(path)


class RustBackend:
    """Backend implemented by the separately built ``sharpmod_rs`` module."""

    name = "rust"

    def __init__(self, module=None):
        self._module = module or importlib.import_module("sharpmod_rs")
        self._grib_point_cache = OrderedDict()
        self._grib_cache_lock = threading.RLock()
        self._grib_cache_hits = 0
        self._grib_cache_misses = 0

    @property
    def version(self):
        return getattr(self._module, "__version__", None)

    def wind_to_components(self, direction, speed, *, missing=None):
        direction_data, speed_data, shape = prepare_broadcast_pair(
            direction, speed, missing=missing)
        u, v = self._module.wind_to_components(
            direction_data, speed_data, missing)
        return restore_pair(u, v, shape)

    def components_to_wind(self, u, v, *, missing=None):
        u_data, v_data, shape = prepare_broadcast_pair(u, v, missing=missing)
        direction, speed = self._module.components_to_wind(
            u_data, v_data, missing)
        return restore_pair(direction, speed, shape)

    def interpolate_1d(
        self, target, coordinate, values, *, missing=None, log=False,
    ):
        targets, coordinates, fields, target_shape = prepare_interpolation(
            target, coordinate, values, missing=missing)
        scalar_log = bool(log) and target_shape == ()
        result = self._module.interpolate_1d(
            targets, coordinates, fields, missing,
            False if scalar_log else bool(log))
        if scalar_log:
            # Match the legacy/Python scalar exponentiation path exactly while
            # keeping interpolation in the native kernel and retaining a
            # single Python/Rust call.
            result[0] = 10.0 ** result[0]
        return restore_array(result, target_shape)

    def pressure_sort_dedup_indices(self, pressure, *, missing=-9999.0):
        values = prepare_1d(pressure, name="pressure")
        result = self._module.pressure_sort_dedup_indices(values, missing)
        return np.asarray(result, dtype=np.intp)

    def basic_sounding_qc(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        wdir,
        wspd,
        *,
        missing=-9999.0,
    ) -> QualityControlResult:
        columns = prepare_qc_columns(
            (pres, hght, tmpc, dwpc, wdir, wspd), _CORE_FIELDS)
        raw = self._module.basic_sounding_qc(*columns, missing)
        if isinstance(raw, dict):
            valid = raw["valid"]
            valid_level_count = raw["valid_level_count"]
            issues = raw["issues"]
        else:
            valid, valid_level_count, issues = raw
        return QualityControlResult(
            valid=bool(valid),
            valid_level_count=int(valid_level_count),
            issues=tuple(str(issue) for issue in issues),
        )

    def parse_sounding_rows(self, text: str, *, missing=-9999.0):
        if not isinstance(text, str):
            raise TypeError("sounding rows must be supplied as text")
        native_missing = None if missing is None else float(missing)
        matrix = np.asarray(
            self._module.parse_sounding_rows(text, native_missing),
            dtype=np.float64,
        )
        if matrix.ndim != 2 or matrix.shape[1] != 6:
            raise RuntimeError(
                "sharpmod_rs.parse_sounding_rows returned an invalid shape "
                f"{matrix.shape}")
        return tuple(np.ascontiguousarray(matrix[:, index]) for index in range(6))

    def decode_grib_point(
        self, path, lat, lon, *, missing=-9999.0,
    ) -> DecodedPoint:
        """Decode one GRIB point through one native call and one matrix return."""
        missing_value = float(missing)
        resolved = Path(path).expanduser().resolve(strict=True)
        stat = resolved.stat()
        longitude = ((float(lon) + 180.0) % 360.0) - 180.0
        cache_key = (
            os.path.normcase(os.fspath(resolved)),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            float(lat),
            longitude,
            missing_value,
        )
        with self._grib_cache_lock:
            cached = self._grib_point_cache.get(cache_key)
            if cached is not None:
                self._grib_point_cache.move_to_end(cache_key)
                self._grib_cache_hits += 1
                return cached
            self._grib_cache_misses += 1
        raw = self._module.decode_grib_point(
            os.fsdecode(os.fspath(resolved)),
            _eccodes_library_path(),
            float(lat),
            longitude,
            missing_value,
        )
        try:
            matrix, selected_lat, selected_lon, vorticity = raw
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "sharpmod_rs.decode_grib_point returned an invalid result"
            ) from exc

        matrix = np.asarray(matrix, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[0] != 9:
            raise RuntimeError(
                "sharpmod_rs.decode_grib_point returned an invalid shape "
                f"{matrix.shape}"
            )
        if not matrix.flags.c_contiguous:
            matrix = np.ascontiguousarray(matrix)

        selected_lat = float(selected_lat)
        selected_lon = float(selected_lon)
        if not np.isfinite(selected_lat) or not np.isfinite(selected_lon):
            raise RuntimeError(
                "sharpmod_rs.decode_grib_point returned non-finite coordinates"
            )
        if vorticity is not None:
            vorticity = float(vorticity)
            if not np.isfinite(vorticity) or vorticity == missing_value:
                vorticity = None
        decoded = DecodedPoint(
            matrix,
            selected_lat,
            selected_lon,
            vorticity,
        )
        with self._grib_cache_lock:
            existing = self._grib_point_cache.get(cache_key)
            if existing is not None:
                self._grib_point_cache.move_to_end(cache_key)
                return existing
            self._grib_point_cache[cache_key] = decoded
            while len(self._grib_point_cache) > _GRIB_POINT_CACHE_MAX:
                self._grib_point_cache.popitem(last=False)
        return decoded

    def clear_grib_cache(self, *, points=True, reset_stats=True):
        """Drop this adapter's bounded decoded-point cache."""
        with self._grib_cache_lock:
            if points:
                self._grib_point_cache.clear()
            if reset_stats:
                self._grib_cache_hits = 0
                self._grib_cache_misses = 0

    def grib_cache_info(self):
        with self._grib_cache_lock:
            return {
                "size": len(self._grib_point_cache),
                "max_size": _GRIB_POINT_CACHE_MAX,
                "hits": self._grib_cache_hits,
                "misses": self._grib_cache_misses,
            }
