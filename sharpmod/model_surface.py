"""Verified ground-level merge for forecast-model pressure soundings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


PROFILE_COLUMN_NAMES = (
    "pres",
    "hght",
    "tmpc",
    "dwpc",
    "wdir",
    "wspd",
    "omeg",
    "u",
    "v",
)

SURFACE_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SurfaceMergeResult:
    """One aligned profile after inserting a verified model surface row."""

    columns: dict[str, np.ndarray]
    surface_pressure: float
    removed_levels: int


def _usable_scalar(value, missing):
    if value is None:
        return None
    try:
        scalar = float(np.asarray(value, dtype=float).reshape(-1)[0])
    except (IndexError, TypeError, ValueError):
        return None
    if not np.isfinite(scalar) or scalar == missing:
        return None
    return scalar


def merge_surface_level(
    columns,
    surface,
    *,
    missing=-9999.0,
) -> SurfaceMergeResult | None:
    """Remove sub-surface isobars and prepend one verified ground row.

    ``columns`` must contain the nine aligned point-sounding columns in
    :data:`PROFILE_COLUMN_NAMES`. ``surface`` supplies normalized hPa, metre,
    degree-Celsius, and metre-per-second values under the same short names.
    Pressure, height, 2-m temperature, and 2-m dewpoint are mandatory. The
    10-m wind components are mandatory so the inserted row is a complete
    thermodynamic/kinematic surface. Surface omega remains missing because
    there is no equivalent model field.
    """
    missing = float(missing)
    normalized = {}
    size = None
    for name in PROFILE_COLUMN_NAMES:
        if name not in columns:
            return None
        values = np.asarray(columns[name], dtype=float).reshape(-1)
        if size is None:
            size = values.size
        elif values.size != size:
            return None
        normalized[name] = values
    if size is None:
        return None

    pressure = _usable_scalar(surface.get("pres"), missing)
    height = _usable_scalar(surface.get("hght"), missing)
    temperature = _usable_scalar(surface.get("tmpc"), missing)
    dewpoint = _usable_scalar(surface.get("dwpc"), missing)
    if (
        pressure is None
        or height is None
        or temperature is None
        or dewpoint is None
        or not 100.0 <= pressure <= 1100.0
        or not -1000.0 <= height <= 10_000.0
        or not -120.0 <= temperature <= 70.0
        or not -150.0 <= dewpoint <= temperature + 1.0e-6
    ):
        return None

    # The inserted ground row owns its exact pressure. An isobar with equal
    # pressure is replaced as well as every isobar below ground.
    isobaric_pressure = normalized["pres"]
    keep = (
        np.isfinite(isobaric_pressure)
        & (isobaric_pressure != missing)
        & (isobaric_pressure > 0.0)
        & (isobaric_pressure < pressure)
    )
    removed = int(size - np.count_nonzero(keep))

    u_wind = _usable_scalar(surface.get("u"), missing)
    v_wind = _usable_scalar(surface.get("v"), missing)
    if (
        u_wind is None
        or v_wind is None
        or abs(u_wind) > 200.0
        or abs(v_wind) > 200.0
    ):
        return None
    wind_direction = (
        270.0 - np.degrees(np.arctan2(v_wind, u_wind))
    ) % 360.0
    wind_speed = float(np.hypot(u_wind, v_wind) * 1.94384449)

    ground = {
        "pres": pressure,
        "hght": height,
        "tmpc": temperature,
        "dwpc": dewpoint,
        "wdir": wind_direction,
        "wspd": wind_speed,
        "omeg": missing,
        "u": u_wind,
        "v": v_wind,
    }
    merged = {
        name: np.ascontiguousarray(
            np.concatenate(([ground[name]], normalized[name][keep])),
            dtype=np.float64,
        )
        for name in PROFILE_COLUMN_NAMES
    }
    return SurfaceMergeResult(merged, pressure, removed)


__all__ = [
    "PROFILE_COLUMN_NAMES",
    "SURFACE_CONTRACT_VERSION",
    "SurfaceMergeResult",
    "merge_surface_level",
]
