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
    prepare_profile_kinematics,
    prepare_profile_parcels,
    prepare_qc_columns,
    prepare_sfc_index,
    restore_array,
    restore_pair,
)
from .grib import DecodedPoint, GribDecodeError, load_eccodes
from .kinematics import profile_kinematics_from_raw
from .parcels import (
    convective_workspace_from_raw,
    downdraft_from_raw,
    parcel_ascent_from_raw,
    parcel_workspace_from_raw,
    profile_thermodynamics_buffers_from_raw,
    profile_thermodynamics_from_raw,
)
from .protocol import (
    DEFAULT_BATCH_PARALLEL_THRESHOLD,
    DEFAULT_BATCH_THREADS,
    BatchProfileAnalysis,
    QualityControlResult,
)


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


def _grib_request(lat, lon, missing):
    latitude = float(lat)
    longitude = float(lon)
    missing_value = float(missing)
    if not np.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be finite and within [-90, 90]")
    if not np.isfinite(longitude):
        raise ValueError("longitude must be finite")
    if not np.isfinite(missing_value):
        raise ValueError("missing must be a finite numeric sentinel")
    return latitude, ((longitude + 180.0) % 360.0) - 180.0, missing_value


def _grib_file_identity(path):
    resolved = Path(path).expanduser().resolve(strict=True)
    stat = resolved.stat()
    identity = (
        os.path.normcase(os.fspath(resolved)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
        int(stat.st_dev),
        int(stat.st_ino),
    )
    return resolved, identity


def _decoded_point_from_raw(raw, missing_value):
    try:
        (
            matrix,
            selected_lat,
            selected_lon,
            vorticity,
            surface_merged,
            below_ground_levels_removed,
        ) = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs GRIB decoder returned an invalid result"
        ) from exc

    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != 9:
        raise RuntimeError(
            "sharpmod_rs GRIB decoder returned an invalid shape "
            f"{matrix.shape}"
        )
    if not matrix.flags.c_contiguous:
        matrix = np.ascontiguousarray(matrix)

    selected_lat = float(selected_lat)
    selected_lon = float(selected_lon)
    if not np.isfinite(selected_lat) or not np.isfinite(selected_lon):
        raise RuntimeError(
            "sharpmod_rs GRIB decoder returned non-finite coordinates"
        )
    if vorticity is not None:
        vorticity = float(vorticity)
        if not np.isfinite(vorticity) or vorticity == missing_value:
            vorticity = None
    return DecodedPoint(
        matrix,
        selected_lat,
        selected_lon,
        vorticity,
        bool(surface_merged),
        int(below_ground_levels_removed),
    )


def _same_decoded_point(left, right):
    """Return whether two native selections are safe to share by identity.

    Pressure and surface fields may use different grids.  Two requests can
    therefore report the same pressure-grid coordinates while selecting
    different surface cells.  Coordinate equality alone is not a sufficient
    cache key; require the complete immutable result to match before reusing
    one :class:`DecodedPoint` instance.
    """
    return (
        left.selected_lat == right.selected_lat
        and left.selected_lon == right.selected_lon
        and left.surface_relative_vorticity == right.surface_relative_vorticity
        and left.surface_merged == right.surface_merged
        and left.below_ground_levels_removed
        == right.below_ground_levels_removed
        and np.array_equal(left.matrix, right.matrix, equal_nan=True)
    )


def _reuse_selected_grib_point(cache, key, decoded):
    """Reuse an equal selected-cell result without conflating mixed grids."""
    existing = cache.get(key)
    if existing is not None and _same_decoded_point(existing, decoded):
        cache.move_to_end(key)
        return existing
    cache[key] = decoded
    cache.move_to_end(key)
    while len(cache) > _GRIB_POINT_CACHE_MAX:
        cache.popitem(last=False)
    return decoded


def _batch_analysis_from_raw(raw, profile_count, layer_count):
    try:
        (
            parcels,
            convective,
            bounds,
            downdraft,
            storm_motion,
            layers,
            mode,
            worker_count,
        ) = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs.profile_batch_analysis returned an invalid result"
        ) from exc

    expected = (
        ("parcels", parcels, (profile_count, 3 * 14), (profile_count, 3, 14)),
        (
            "convective_parcels",
            convective,
            (profile_count, 5 * 14),
            (profile_count, 5, 14),
        ),
        ("effective_bounds", bounds, (profile_count, 2), None),
        ("downdraft", downdraft, (profile_count, 3), None),
        ("storm_motion", storm_motion, (profile_count, 4), None),
        (
            "kinematic_layers",
            layers,
            (profile_count, layer_count * 15),
            (profile_count, layer_count, 15),
        ),
    )
    arrays = {}
    for name, value, raw_shape, final_shape in expected:
        array = np.asarray(value, dtype=np.float64)
        if array.shape != raw_shape:
            raise RuntimeError(
                "sharpmod_rs.profile_batch_analysis returned invalid "
                f"{name} shape {array.shape}; expected {raw_shape}"
            )
        array = np.ascontiguousarray(array)
        if final_shape is not None:
            array = array.reshape(final_shape)
        array.setflags(write=False)
        arrays[name] = array
    mode_names = {
        0: "serial_below_threshold",
        1: "serial_single_thread",
        2: "serial_nested_rayon",
        3: "bounded_parallel",
    }
    try:
        execution_mode = mode_names[int(mode)]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"sharpmod_rs.profile_batch_analysis returned invalid mode {mode!r}"
        ) from exc
    worker_count = int(worker_count)
    if worker_count < 1 or worker_count > 4:
        raise RuntimeError(
            "sharpmod_rs.profile_batch_analysis returned invalid worker count "
            f"{worker_count}"
        )
    return BatchProfileAnalysis(
        parcels=arrays["parcels"],
        convective_parcels=arrays["convective_parcels"],
        effective_bounds=arrays["effective_bounds"],
        downdraft=arrays["downdraft"],
        storm_motion=arrays["storm_motion"],
        kinematic_layers=arrays["kinematic_layers"],
        execution_mode=execution_mode,
        worker_count=worker_count,
    )


class RustBackend:
    """Backend implemented by the separately built ``sharpmod_rs`` module."""

    name = "rust"

    def __init__(self, module=None):
        self._module = module or importlib.import_module("sharpmod_rs")
        self._grib_point_cache = OrderedDict()
        self._grib_selected_cache = OrderedDict()
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

    def profile_kinematics(
        self,
        pres,
        hght,
        u,
        v,
        layer_tops_agl,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_kinematics(
            pres,
            hght,
            u,
            v,
            layer_tops_agl,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        raw = self._module.profile_kinematics(*columns, index, None)
        try:
            storm_motion, layer_matrix = raw
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "sharpmod_rs.profile_kinematics returned an invalid result"
            ) from exc
        return profile_kinematics_from_raw(storm_motion, layer_matrix)

    def profile_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_parcels(
            pres,
            hght,
            tmpc,
            dwpc,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        return parcel_workspace_from_raw(
            self._module.profile_parcels(*columns, index, None),
        )

    def profile_convective_parcels(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_parcels(
            pres,
            hght,
            tmpc,
            dwpc,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        return convective_workspace_from_raw(
            self._module.profile_convective_parcels(*columns, index, None),
        )

    def lift_parcel(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        parcel_pressure,
        parcel_temperature,
        parcel_dewpoint,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_parcels(
            pres,
            hght,
            tmpc,
            dwpc,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        return parcel_ascent_from_raw(
            self._module.lift_parcel(
                *columns,
                float(parcel_pressure),
                float(parcel_temperature),
                float(parcel_dewpoint),
                index,
                None,
            ),
        )

    def profile_dcape(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_parcels(
            pres,
            hght,
            tmpc,
            dwpc,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        return downdraft_from_raw(
            self._module.profile_dcape(*columns, index, None),
        )

    def _profile_thermodynamics_raw(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        columns = prepare_profile_parcels(
            pres,
            hght,
            tmpc,
            dwpc,
            missing=missing,
        )
        index = prepare_sfc_index(sfc, columns[0].size)
        return self._module.profile_thermodynamics(
            *columns,
            index,
            None,
        )

    def profile_thermodynamics_buffers(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        """Return internal zero-copy trace views for application hot paths."""
        return profile_thermodynamics_buffers_from_raw(
            self._profile_thermodynamics_raw(
                pres,
                hght,
                tmpc,
                dwpc,
                sfc=sfc,
                missing=missing,
            ),
        )

    def profile_thermodynamics(
        self,
        pres,
        hght,
        tmpc,
        dwpc,
        *,
        sfc=0,
        missing=-9999.0,
    ):
        """Return the stable public tuple-backed shared workspace."""
        return profile_thermodynamics_from_raw(
            self._profile_thermodynamics_raw(
                pres,
                hght,
                tmpc,
                dwpc,
                sfc=sfc,
                missing=missing,
            ),
        )

    def profile_batch_analysis(
        self,
        profiles,
        layer_tops_agl,
        *,
        missing=-9999.0,
        max_threads=DEFAULT_BATCH_THREADS,
        parallel_threshold=DEFAULT_BATCH_PARALLEL_THRESHOLD,
    ) -> BatchProfileAnalysis:
        """Compute dense diagnostics for complete profiles in stable order.

        Each item is a six-column sequence followed by an optional relative
        surface index. Columns are flattened once for the native boundary;
        Rust owns each segment before releasing the GIL.
        """
        try:
            profiles = tuple(profiles)
        except TypeError as exc:
            raise TypeError("profiles must be an iterable of sounding columns") from exc

        layer_tops = prepare_1d(layer_tops_agl, name="layer_tops_agl")
        if np.any(~np.isfinite(layer_tops)) or np.any(layer_tops < 0.0):
            raise ValueError(
                "layer_tops_agl must contain finite, non-negative heights"
            )
        native_missing = None if missing is None else float(missing)
        if native_missing is not None and not np.isfinite(native_missing):
            raise ValueError("missing must be a finite numeric sentinel")

        grouped = [[] for _name in _CORE_FIELDS]
        offsets = [0]
        surface_indices = []
        for profile_index, value in enumerate(profiles):
            try:
                columns = tuple(value)
            except TypeError as exc:
                raise TypeError(
                    f"profile {profile_index} must be a column sequence"
                ) from exc
            if len(columns) not in (6, 7):
                raise ValueError(
                    f"profile {profile_index} must contain six columns and "
                    "an optional surface index"
                )
            arrays = prepare_qc_columns(columns[:6], _CORE_FIELDS)
            if native_missing is not None:
                normalized = []
                for array in arrays:
                    if np.any(array == native_missing):
                        array = np.array(array, copy=True, order="C")
                        array[array == native_missing] = np.nan
                    normalized.append(array)
                arrays = tuple(normalized)
            surface = prepare_sfc_index(
                columns[6] if len(columns) == 7 else 0,
                arrays[0].size,
            )
            for destination, array in zip(grouped, arrays):
                destination.append(array)
            offsets.append(offsets[-1] + arrays[0].size)
            surface_indices.append(surface)

        flattened = tuple(
            np.ascontiguousarray(np.concatenate(parts), dtype=np.float64)
            if parts
            else np.empty(0, dtype=np.float64)
            for parts in grouped
        )
        raw = self._module.profile_batch_analysis(
            *flattened,
            np.asarray(offsets, dtype=np.uintp),
            np.asarray(surface_indices, dtype=np.uintp),
            layer_tops,
            None,
            max_threads,
            parallel_threshold,
        )
        return _batch_analysis_from_raw(
            raw,
            profile_count=len(profiles),
            layer_count=layer_tops.size,
        )

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
        latitude, longitude, missing_value = _grib_request(lat, lon, missing)
        resolved, identity = _grib_file_identity(path)
        cache_key = (identity, latitude, longitude, missing_value)
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
            latitude,
            longitude,
            missing_value,
        )
        decoded = _decoded_point_from_raw(raw, missing_value)
        selected_key = (
            identity,
            decoded.selected_lat,
            decoded.selected_lon,
            missing_value,
        )
        with self._grib_cache_lock:
            existing = self._grib_point_cache.get(cache_key)
            if existing is not None:
                self._grib_point_cache.move_to_end(cache_key)
                return existing
            decoded = _reuse_selected_grib_point(
                self._grib_selected_cache, selected_key, decoded,
            )
            self._grib_point_cache[cache_key] = decoded
            while len(self._grib_point_cache) > _GRIB_POINT_CACHE_MAX:
                self._grib_point_cache.popitem(last=False)
        return decoded

    def decode_grib_points(
        self, path, points, *, missing=-9999.0,
    ) -> tuple[DecodedPoint, ...]:
        """Decode uncached requests together through the native vector API."""
        requests = []
        for value in points:
            try:
                latitude, longitude = value
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "each GRIB point must be a (latitude, longitude) pair"
                ) from exc
            latitude, longitude, missing_value = _grib_request(
                latitude, longitude, missing,
            )
            requests.append((latitude, longitude))
        if not requests:
            return ()

        resolved, identity = _grib_file_identity(path)
        results = [None] * len(requests)
        pending = OrderedDict()
        with self._grib_cache_lock:
            for index, (latitude, longitude) in enumerate(requests):
                key = (identity, latitude, longitude, missing_value)
                cached = self._grib_point_cache.get(key)
                if cached is not None:
                    self._grib_point_cache.move_to_end(key)
                    self._grib_cache_hits += 1
                    results[index] = cached
                    continue
                entry = pending.get(key)
                if entry is None:
                    self._grib_cache_misses += 1
                    pending[key] = {
                        "request": (latitude, longitude),
                        "positions": [index],
                    }
                else:
                    entry["positions"].append(index)

        if pending:
            unique_requests = [entry["request"] for entry in pending.values()]
            latitudes = np.ascontiguousarray(
                [value[0] for value in unique_requests], dtype=np.float64,
            )
            longitudes = np.ascontiguousarray(
                [value[1] for value in unique_requests], dtype=np.float64,
            )
            raw_results = self._module.decode_grib_points(
                os.fsdecode(os.fspath(resolved)),
                _eccodes_library_path(),
                latitudes,
                longitudes,
                missing_value,
            )
            try:
                raw_results = tuple(raw_results)
            except TypeError as exc:
                raise RuntimeError(
                    "sharpmod_rs.decode_grib_points returned an invalid result"
                ) from exc
            if len(raw_results) != len(pending):
                raise RuntimeError(
                    "sharpmod_rs.decode_grib_points returned "
                    f"{len(raw_results)} results for {len(pending)} requests"
                )

            with self._grib_cache_lock:
                for (request_key, entry), raw in zip(
                    pending.items(), raw_results,
                ):
                    decoded = _decoded_point_from_raw(raw, missing_value)
                    selected_key = (
                        identity,
                        decoded.selected_lat,
                        decoded.selected_lon,
                        missing_value,
                    )
                    existing = self._grib_point_cache.get(request_key)
                    if existing is not None:
                        decoded = existing
                        self._grib_point_cache.move_to_end(request_key)
                    else:
                        decoded = _reuse_selected_grib_point(
                            self._grib_selected_cache, selected_key, decoded,
                        )
                        self._grib_point_cache[request_key] = decoded
                        while len(self._grib_point_cache) > _GRIB_POINT_CACHE_MAX:
                            self._grib_point_cache.popitem(last=False)
                    for index in entry["positions"]:
                        results[index] = decoded

        if any(result is None for result in results):
            raise RuntimeError("native GRIB result assembly left an empty slot")
        return tuple(results)

    def clear_grib_cache(
        self, *, inventory=True, points=True, reset_stats=True,
    ):
        """Drop selected native inventory and decoded-point caches."""
        with self._grib_cache_lock:
            if points:
                self._grib_point_cache.clear()
                self._grib_selected_cache.clear()
            if reset_stats:
                self._grib_cache_hits = 0
                self._grib_cache_misses = 0
        if inventory:
            self._module.clear_grib_inventory_cache(bool(reset_stats))

    def set_grib_inventory_cache_enabled(self, enabled):
        self._module.set_grib_inventory_cache_enabled(bool(enabled))

    def grib_cache_info(self):
        with self._grib_cache_lock:
            info = {
                "size": len(self._grib_point_cache),
                "max_size": _GRIB_POINT_CACHE_MAX,
                "hits": self._grib_cache_hits,
                "misses": self._grib_cache_misses,
                "selected_size": len(self._grib_selected_cache),
            }
        try:
            (
                enabled,
                size,
                max_size,
                hits,
                misses,
                evictions,
                invalidations,
            ) = self._module.grib_inventory_cache_info()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "sharpmod_rs.grib_inventory_cache_info returned an invalid result"
            ) from exc
        info["inventory"] = {
            "enabled": bool(enabled),
            "size": int(size),
            "max_size": int(max_size),
            "hits": int(hits),
            "misses": int(misses),
            "evictions": int(evictions),
            "invalidations": int(invalidations),
        }
        return info
