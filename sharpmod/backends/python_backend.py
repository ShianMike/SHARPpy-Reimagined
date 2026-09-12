"""Authoritative NumPy implementation of optional backend operations."""

from __future__ import annotations

import numpy as np

from ._common import (
    missing_mask,
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
from .kinematics import profile_kinematics_to_raw
from .parcels import (
    convective_workspace_to_raw,
    downdraft_to_raw,
    parcel_workspace_to_raw,
)
from .protocol import (
    DEFAULT_BATCH_PARALLEL_THRESHOLD,
    DEFAULT_BATCH_THREADS,
    BatchProfileAnalysis,
    QualityControlResult,
)
from .grib import (
    decode_grib_point as _decode_grib_point,
    decode_grib_points as _decode_grib_points,
)
from .kinematics import compute_profile_kinematics
from .parcels import (
    compute_lift_parcel,
    compute_profile_convective_parcels,
    compute_profile_dcape,
    compute_profile_parcels,
    compute_profile_thermodynamics,
    profile_thermodynamics_buffers_from_raw,
    profile_thermodynamics_to_raw,
)


_CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
_HEADER = tuple(_CORE_FIELDS)


class PythonBackend:
    """Reference backend. Its behavior is authoritative for Rust equivalence."""

    name = "python"

    def decode_grib_point(self, path, lat, lon, *, missing=-9999.0):
        return _decode_grib_point(path, lat, lon, missing=missing)

    def decode_grib_points(self, path, points, *, missing=-9999.0):
        return _decode_grib_points(path, points, missing=missing)

    def wind_to_components(self, direction, speed, *, missing=None):
        direction_data, speed_data, shape = prepare_broadcast_pair(
            direction, speed, missing=missing)
        radians = np.deg2rad(direction_data)
        u = -speed_data * np.sin(radians)
        v = -speed_data * np.cos(radians)
        return restore_pair(u, v, shape)

    def components_to_wind(self, u, v, *, missing=None):
        u_data, v_data, shape = prepare_broadcast_pair(u, v, missing=missing)
        speed = np.hypot(u_data, v_data)
        direction = (
            270.0 - np.degrees(np.arctan2(v_data, u_data))) % 360.0
        return restore_pair(direction, speed, shape)

    def interpolate_1d(
        self, target, coordinate, values, *, missing=None, log=False,
    ):
        targets, coordinates, fields, target_shape = prepare_interpolation(
            target, coordinate, values, missing=missing)
        good = np.isfinite(coordinates) & np.isfinite(fields)
        x = coordinates[good]
        y = fields[good]
        if x.size < 2:
            result = np.full(targets.shape, np.nan, dtype=np.float64)
        else:
            order = np.argsort(x, kind="stable")
            result = np.interp(
                targets, x[order], y[order], left=np.nan, right=np.nan)
        if log:
            if target_shape == ():
                # Preserve the legacy scalar operation exactly.  NumPy's
                # vector power loop can differ by one ULP across platforms;
                # that is enough to move a pressure-layer boundary across a
                # reported level in upstream SHARPpy's CAPE integrator.
                result[0] = 10.0 ** result[0]
            else:
                result = np.power(10.0, result)
        return restore_array(result, target_shape)

    def pressure_sort_dedup_indices(self, pressure, *, missing=-9999.0):
        values = prepare_1d(pressure, name="pressure")
        valid = (~missing_mask(values, missing)) & (values > 0.0)
        candidates = np.flatnonzero(valid)
        if candidates.size == 0:
            return np.array([], dtype=np.intp)
        order = np.argsort(-values[candidates], kind="stable")
        sorted_indices = candidates[order]
        keep = []
        seen = set()
        for index in sorted_indices:
            pressure_value = float(values[index])
            if pressure_value in seen:
                continue
            seen.add(pressure_value)
            keep.append(int(index))
        return np.asarray(keep, dtype=np.intp)

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
        return compute_profile_kinematics(*columns, sfc=index)

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
        return compute_profile_parcels(*columns, sfc=index)

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
        return compute_profile_convective_parcels(*columns, sfc=index)

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
        return compute_lift_parcel(
            *columns,
            float(parcel_pressure),
            float(parcel_temperature),
            float(parcel_dewpoint),
            sfc=index,
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
        return compute_profile_dcape(*columns, sfc=index)

    def _profile_thermodynamics_result(
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
        return compute_profile_thermodynamics(*columns, sfc=index)

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
        return self._profile_thermodynamics_result(
            pres,
            hght,
            tmpc,
            dwpc,
            sfc=sfc,
            missing=missing,
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
        result = self._profile_thermodynamics_result(
            pres,
            hght,
            tmpc,
            dwpc,
            sfc=sfc,
            missing=missing,
        )
        return profile_thermodynamics_buffers_from_raw(
            profile_thermodynamics_to_raw(result),
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
        """Reference serial implementation of the native batch contract."""
        profiles = tuple(profiles)
        if isinstance(max_threads, (bool, np.bool_)) or int(max_threads) < 1:
            raise ValueError("max_threads must be at least 1")
        if (
            isinstance(parallel_threshold, (bool, np.bool_))
            or int(parallel_threshold) < 2
        ):
            raise ValueError("parallel_threshold must be at least 2")
        layer_tops = prepare_1d(layer_tops_agl, name="layer_tops_agl")
        if np.any(~np.isfinite(layer_tops)) or np.any(layer_tops < 0.0):
            raise ValueError(
                "layer_tops_agl must contain finite, non-negative heights"
            )

        parcel_rows = []
        convective_rows = []
        bounds_rows = []
        downdraft_rows = []
        storm_rows = []
        layer_rows = []
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
            sfc = columns[6] if len(columns) == 7 else 0
            thermodynamics = self.profile_thermodynamics(
                *columns[:4], sfc=sfc, missing=missing,
            )
            u, v = self.wind_to_components(
                columns[4], columns[5], missing=missing,
            )
            kinematics = self.profile_kinematics(
                columns[0],
                columns[1],
                u,
                v,
                layer_tops,
                sfc=sfc,
                missing=missing,
            )
            convective_raw = convective_workspace_to_raw(
                thermodynamics.convective,
            )
            downdraft_raw = downdraft_to_raw(thermodynamics.downdraft)
            storm_raw, layers_raw = profile_kinematics_to_raw(kinematics)
            parcel_rows.append(parcel_workspace_to_raw(thermodynamics.parcels))
            convective_rows.append(convective_raw[0])
            bounds_rows.append(convective_raw[1])
            downdraft_rows.append(downdraft_raw[0])
            storm_rows.append(storm_raw)
            layer_rows.append(layers_raw)

        profile_count = len(profiles)
        layer_count = layer_tops.size

        def stack(rows, shape):
            array = (
                np.ascontiguousarray(np.asarray(rows, dtype=np.float64))
                if rows
                else np.empty(shape, dtype=np.float64)
            )
            array = array.reshape(shape)
            array.setflags(write=False)
            return array

        return BatchProfileAnalysis(
            parcels=stack(parcel_rows, (profile_count, 3, 14)),
            convective_parcels=stack(
                convective_rows, (profile_count, 5, 14),
            ),
            effective_bounds=stack(bounds_rows, (profile_count, 2)),
            downdraft=stack(downdraft_rows, (profile_count, 3)),
            storm_motion=stack(storm_rows, (profile_count, 4)),
            kinematic_layers=stack(
                layer_rows, (profile_count, layer_count, 15),
            ),
            execution_mode=(
                "serial_below_threshold"
                if profile_count < int(parallel_threshold)
                else "serial_single_thread"
            ),
            worker_count=1,
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
        pres, hght, tmpc, dwpc, wdir, wspd = prepare_qc_columns(
            (pres, hght, tmpc, dwpc, wdir, wspd), _CORE_FIELDS)
        p_missing = missing_mask(pres, missing)
        h_missing = missing_mask(hght, missing)
        t_missing = missing_mask(tmpc, missing)
        td_missing = missing_mask(dwpc, missing)
        wd_missing = missing_mask(wdir, missing)
        ws_missing = missing_mask(wspd, missing)
        valid_pressure = pres[~p_missing]
        valid_height = hght[~h_missing]

        issues = []
        if valid_pressure.size < 2:
            issues.append("too_few_levels")
        if np.any(p_missing):
            issues.append("missing_pressure")
        if np.any(valid_pressure <= 0.0):
            issues.append("nonpositive_pressure")
        if valid_pressure.size >= 2 and np.any(np.diff(valid_pressure) >= 0.0):
            issues.append("pressure_not_strictly_decreasing")
        if valid_height.size < 2:
            issues.append("insufficient_height")
        if valid_height.size >= 2 and np.any(np.diff(valid_height) <= 0.0):
            issues.append("height_not_strictly_increasing")
        if np.any(tmpc[~t_missing] <= -273.15):
            issues.append("temperature_below_absolute_zero")
        if np.any(dwpc[~td_missing] <= -273.15):
            issues.append("dewpoint_below_absolute_zero")
        paired_thermodynamics = ~t_missing & ~td_missing
        if np.any(dwpc[paired_thermodynamics] > tmpc[paired_thermodynamics]):
            issues.append("dewpoint_above_temperature")
        valid_direction = wdir[~wd_missing]
        if np.any((valid_direction < 0.0) | (valid_direction > 360.0)):
            issues.append("wind_direction_out_of_range")
        if np.any(wspd[~ws_missing] < 0.0):
            issues.append("negative_wind_speed")

        valid_level_count = int(np.count_nonzero(~p_missing & ~h_missing))
        return QualityControlResult(
            valid=not issues,
            valid_level_count=valid_level_count,
            issues=tuple(issues),
        )

    def parse_sounding_rows(self, text: str, *, missing=-9999.0):
        if not isinstance(text, str):
            raise TypeError("sounding rows must be supplied as text")
        missing_value = np.nan if missing is None else float(missing)
        rows = []
        header_seen = False
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";", "//")):
                continue
            tokens = (
                [token.strip() for token in line.split(",")]
                if "," in line else line.split()
            )
            normalized = tuple(token.lower() for token in tokens)
            if not rows and not header_seen and normalized == _HEADER:
                header_seen = True
                continue
            if len(tokens) != 6:
                raise ValueError(
                    f"line {line_number}: expected 6 columns, got {len(tokens)}")

            row = []
            for token in tokens:
                if token == "":
                    row.append(missing_value)
                    continue
                try:
                    value = float(token)
                except ValueError as exc:
                    raise ValueError(
                        f"line {line_number}: nonnumeric value {token!r}") from exc
                if (
                    not np.isfinite(value)
                    or (missing is not None and value == missing_value)
                ):
                    value = missing_value
                row.append(value)
            rows.append(row)

        if not rows:
            raise ValueError("no sounding rows were found")
        matrix = np.asarray(rows, dtype=np.float64)
        return tuple(np.ascontiguousarray(matrix[:, index]) for index in range(6))
