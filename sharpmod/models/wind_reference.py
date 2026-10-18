"""Normalize supported GRIB grid-relative winds before profile assembly."""

from __future__ import annotations

import math
import numpy as np

# cfgrib otherwise omits some projection/flag keys on individual variables.
GRIB_WIND_KEYS = (
    "uvRelativeToGrid", "LoVInDegrees", "Latin1InDegrees", "Latin2InDegrees",
    "orientationOfTheGridInDegrees", "projectionCentreFlag",
    "resolutionAndComponentFlags",
)


def wind_rotation(get):
    """Return (cone constant, central meridian), or None for true-north winds."""
    relative = get("uvRelativeToGrid")
    if relative is None and get("resolutionAndComponentFlags") is not None:
        relative = int(bool(int(get("resolutionAndComponentFlags")) & 8))
    grid = get("gridType")
    if relative is None and grid in {"regular_ll", "regular_gg", "reduced_ll", "reduced_gg"}:
        return None
    if relative == 0:
        return None
    if relative != 1:
        raise ValueError("GRIB wind-reference flag is missing or invalid")
    if grid in {"regular_ll", "regular_gg", "reduced_ll", "reduced_gg", "mercator"}:
        return None
    if grid == "lambert":
        first, second = (
            math.radians(float(get(key)))
            for key in ("Latin1InDegrees", "Latin2InDegrees")
        )
        if abs(first) >= math.pi / 2 or abs(second) >= math.pi / 2:
            raise ValueError("invalid Lambert standard parallels")
        cone = (
            math.sin(first) if abs(first - second) < 1e-12 else
            math.log(math.cos(first) / math.cos(second)) /
            math.log(math.tan(math.pi / 4 + second / 2) /
                     math.tan(math.pi / 4 + first / 2))
        )
        origin = float(get("LoVInDegrees"))
    elif grid == "polar_stereographic":
        cone = -1.0 if int(get("projectionCentreFlag")) & 128 else 1.0
        origin = float(get("orientationOfTheGridInDegrees"))
    else:
        raise ValueError(f"unsupported grid-relative wind projection: {grid!r}")
    if not math.isfinite(cone) or not math.isfinite(origin) or abs(cone) < 1e-12:
        raise ValueError("invalid GRIB wind projection metadata")
    return cone, origin


def rotate_winds(u, v, longitude, rotation, *, missing=-9999.0):
    """NOAA convention: grid U/V -> east/north U/V, preserving speed."""
    if rotation is None:
        return u, v
    cone, origin = rotation
    angle = np.radians(cone * ((np.asarray(longitude) - origin + 180) % 360 - 180))
    cosine, sine = np.cos(angle), np.sin(angle)
    u, v = np.asarray(u), np.asarray(v)
    good = np.isfinite(u) & np.isfinite(v) & (u != missing) & (v != missing)
    u, v = np.where(good, u, 0.0), np.where(good, v, 0.0)
    return (np.where(good, cosine * u + sine * v, missing),
            np.where(good, -sine * u + cosine * v, missing))
