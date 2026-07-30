"""Lightweight validation for cached portable point-sounding pairs.

The full decoder constructs SHARPpy profile objects and imports the rendering
stack.  Cache discovery only needs to know whether an ``.npz`` and its adjacent
JSON sidecar are safe and structurally decodable, so this module keeps that
contract small and Qt/SHARPpy independent.
"""

from __future__ import annotations

import json
import math
import os
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_MAX_NPZ_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PROFILE_LEVELS = 20_000
DEFAULT_MAX_SIDECAR_BYTES = 1 * 1024 * 1024
_PROFILE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg")
_SCALAR_FIELDS = ("valid", "run", "loc", "lat")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S")
_MISSING = -9999.0


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if value > 0 else default


def _scalar(data, key: str):
    if key not in data:
        raise ValueError(f"portable sounding is missing required field {key!r}")
    value = np.asarray(data[key])
    if value.dtype.hasobject or value.size != 1:
        raise ValueError(
            f"portable sounding field {key!r} must be one safe scalar"
        )
    return value.reshape(-1)[0].item()


def _profile_array(data, key: str, expected_levels: int | None) -> int:
    if key not in data:
        raise ValueError(f"portable sounding is missing required field {key!r}")
    value = np.asarray(data[key])
    if value.dtype.hasobject or value.dtype.kind not in "fiu" or value.ndim != 1:
        raise ValueError(
            f"portable sounding field {key!r} must be a numeric 1-D array"
        )
    level_count = int(value.size)
    max_levels = _positive_env_int(
        "SHARPMOD_MAX_PROFILE_LEVELS", DEFAULT_MAX_PROFILE_LEVELS
    )
    if not 2 <= level_count <= max_levels:
        raise ValueError(
            f"portable sounding field {key!r} has an invalid level count"
        )
    if expected_levels is not None and level_count != expected_levels:
        raise ValueError(
            f"portable sounding field {key!r} has {level_count} levels; "
            f"expected {expected_levels}"
        )
    return level_count


def _validate_datetime(value, key: str) -> None:
    text = str(value).strip()
    if not any(
        _datetime_matches(text, pattern) for pattern in _DATETIME_FORMATS
    ):
        raise ValueError(
            f"portable sounding field {key!r} is not a supported date/time"
        )


def _datetime_matches(value: str, pattern: str) -> bool:
    try:
        datetime.strptime(value, pattern)
    except ValueError:
        return False
    return True


def _physical_profile_issues(columns) -> tuple[str, ...]:
    """Return stable physical issue codes for a structurally safe profile."""
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
    """Validate and return a cached ``.npz`` path or raise ``ValueError``."""
    path = Path(npz_path).expanduser()
    sidecar = path.with_suffix(".json")
    if path.suffix.casefold() != ".npz":
        raise ValueError("portable sounding cache payload must be an .npz file")

    npz_limit = _positive_env_int(
        "SHARPMOD_MAX_NPZ_BYTES", DEFAULT_MAX_NPZ_BYTES
    )
    sidecar_limit = _positive_env_int(
        "SHARPMOD_MAX_SIDECAR_BYTES", DEFAULT_MAX_SIDECAR_BYTES
    )
    try:
        if not path.is_file() or path.stat().st_size > npz_limit:
            raise ValueError("portable sounding archive is missing or oversized")
        if not sidecar.is_file() or sidecar.stat().st_size > sidecar_limit:
            raise ValueError("portable sounding sidecar is missing or oversized")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 64:
                raise ValueError("portable sounding contains too many arrays")
            if sum(member.file_size for member in members) > npz_limit:
                raise ValueError("portable sounding expands beyond the safety limit")
            if any(
                member.file_size > npz_limit
                or not member.filename.endswith(".npy")
                for member in members
            ):
                raise ValueError("portable sounding has an invalid array member")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid portable sounding archive: {exc}") from exc

    try:
        with np.load(path, allow_pickle=False) as data:
            level_count = None
            profile_columns = {}
            for field in _PROFILE_FIELDS:
                level_count = _profile_array(data, field, level_count)
                profile_columns[field] = np.asarray(data[field], dtype=float)
            issues = _physical_profile_issues(profile_columns)
            if issues:
                raise ValueError(
                    "portable sounding failed physical quality control: "
                    + ", ".join(issues)
                )
            values = {field: _scalar(data, field) for field in _SCALAR_FIELDS}
            _validate_datetime(values["valid"], "valid")
            _validate_datetime(values["run"], "run")
            latitude = float(values["lat"])
            if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
                raise ValueError("portable sounding latitude is out of range")
            if "lon" in data:
                longitude = float(_scalar(data, "lon"))
                if (
                    not math.isfinite(longitude)
                    or not -180.0 <= longitude <= 180.0
                ):
                    raise ValueError("portable sounding longitude is out of range")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid portable sounding pair: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("portable sounding sidecar must contain a JSON object")
    return path


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
