"""Authoritative NumPy implementation of optional backend operations."""

from __future__ import annotations

import numpy as np

from ._common import (
    missing_mask,
    prepare_1d,
    prepare_broadcast_pair,
    prepare_interpolation,
    prepare_qc_columns,
    restore_array,
    restore_pair,
)
from .protocol import QualityControlResult
from .grib import decode_grib_point as _decode_grib_point


_CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
_HEADER = tuple(_CORE_FIELDS)


class PythonBackend:
    """Reference backend. Its behavior is authoritative for Rust equivalence."""

    name = "python"

    def decode_grib_point(self, path, lat, lon, *, missing=-9999.0):
        return _decode_grib_point(path, lat, lon, missing=missing)

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
