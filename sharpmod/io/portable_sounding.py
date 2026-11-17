"""Lightweight validation for cached portable point-sounding pairs.

The full decoder constructs SHARPpy profile objects and imports the rendering
stack.  Cache discovery only needs to know whether an ``.npz`` and its adjacent
JSON sidecar are safe and structurally decodable, so this module keeps that
contract small and Qt/SHARPpy independent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

DEFAULT_MAX_NPZ_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PROFILE_LEVELS = 20_000
DEFAULT_MAX_SIDECAR_BYTES = 1 * 1024 * 1024
_PROFILE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg")
_MISSING = -9999.0


#: Physical issues that describe a *contaminated* sounding rather than an
#: unusable one, and so must not stop it being loaded.
#:
#: A dewpoint above the temperature is real observed data. It usually means the
#: sounding is contaminated, and it commonly appears near the top of the
#: troposphere -- around 10 km -- where the humidity sensor is least reliable.
#: Refusing the whole archive over it meant a sounding a forecaster wanted to
#: look at could not be opened at all, which hides the contamination instead of
#: showing it. The decoder still reports the advisory issue so consumers can
#: explain the contamination alongside the sounding.
#:
#: Everything else in :func:`_physical_profile_issues` stays fatal: a
#: non-decreasing pressure column or a negative wind speed is not contaminated
#: data, it is data no parcel or shear calculation can consume.
ADVISORY_ISSUES = frozenset({"dewpoint_above_temperature"})


def fatal_issues(issues) -> tuple[str, ...]:
    """Return only those ``issues`` that make a sounding unusable."""
    return tuple(issue for issue in issues if issue not in ADVISORY_ISSUES)


def _physical_profile_issues(columns) -> tuple[str, ...]:
    """Return stable physical issue codes for a structurally safe profile."""
    from sharpmod import backends
    try:
        use_rust = backends.get_backend().name == "rust"
    except (backends.BackendUnavailableError, ValueError):
        use_rust = False
    if use_rust:
        import sharpmod_rs
        native = getattr(sharpmod_rs, "portable_profile_issues", None)
        if native is not None:
            return native(columns)
    values = {
        name: np.asarray(column, dtype=float)
        for name, column in columns.items()
    }
    missing = {
        name: ~np.isfinite(column) | (column == _MISSING)
        for name, column in values.items()
    }
    issues = []
    pressure = values["pres"][~missing["pres"]]
    height = values["hght"][~missing["hght"]]
    if pressure.size < 2:
        issues.append("too_few_levels")
    if pressure.size != values["pres"].size:
        issues.append("missing_pressure")
    if np.any(pressure <= 0.0):
        issues.append("nonpositive_pressure")
    if pressure.size >= 2 and np.any(np.diff(pressure) >= 0.0):
        issues.append("pressure_not_strictly_decreasing")
    if height.size < 2:
        issues.append("insufficient_height")
    elif np.any(np.diff(height) <= 0.0):
        issues.append("height_not_strictly_increasing")
    paired = ~missing["tmpc"] & ~missing["dwpc"]
    if np.any(values["dwpc"][paired] > values["tmpc"][paired]):
        issues.append("dewpoint_above_temperature")
    direction = values["wdir"][~missing["wdir"]]
    if np.any((direction < 0.0) | (direction > 360.0)):
        issues.append("wind_direction_out_of_range")
    speed = values["wspd"][~missing["wspd"]]
    if np.any(speed < 0.0):
        issues.append("negative_wind_speed")
    return tuple(issues)


def validate_portable_sounding_pair(npz_path) -> Path:
    """Validate a portable pair through the shared native boundary."""
    from sharpmod_rs import validate_portable_pair

    return validate_portable_pair(npz_path)


def portable_sounding_pair_valid(npz_path) -> bool:
    """Return whether an ``.npz`` and adjacent sidecar are safely decodable."""
    try:
        validate_portable_sounding_pair(npz_path)
    except (OSError, TypeError, ValueError):
        return False
    return True


__all__ = [
    "portable_sounding_pair_valid",
    "validate_portable_sounding_pair",
]
