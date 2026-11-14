"""Normalize supported GRIB grid-relative winds before profile assembly."""

from __future__ import annotations

# cfgrib otherwise omits some projection/flag keys on individual variables.
GRIB_WIND_KEYS = (
    "uvRelativeToGrid", "LoVInDegrees", "Latin1InDegrees", "Latin2InDegrees",
    "orientationOfTheGridInDegrees", "projectionCentreFlag",
    "resolutionAndComponentFlags",
)


def wind_rotation(get):
    """Return (cone constant, central meridian), or None for true-north winds."""
    import sharpmod_rs

    return sharpmod_rs.grib_wind_reference(get)


def rotate_winds(u, v, longitude, rotation, *, missing=-9999.0):
    """NOAA convention: grid U/V -> east/north U/V, preserving speed."""
    import sharpmod_rs

    return sharpmod_rs.rotate_model_winds(u, v, longitude, rotation, missing)
