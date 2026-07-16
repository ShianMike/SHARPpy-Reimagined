"""Numerical backend facade with Rust-primary automatic selection.

The default ``auto`` mode uses the supported :mod:`sharpmod_rs` backend when its
versioned contract validates and the fully functional Python fallback
otherwise. Set ``SHARPMOD_BACKEND=python`` to force Python or
``SHARPMOD_BACKEND=rust`` to require Rust.
"""

from __future__ import annotations

from .grib import (
    DecodedPoint,
    GribDecodeError,
    clear_grib_caches as _clear_python_grib_caches,
    grib_cache_info as _python_grib_cache_info,
)
from .protocol import Backend, QualityControlResult
from .selector import (
    BackendUnavailableError,
    backend_info,
    get_backend,
    reset_backend_cache,
)


def wind_to_components(direction, speed, *, missing=None):
    return get_backend().wind_to_components(direction, speed, missing=missing)


def components_to_wind(u, v, *, missing=None):
    return get_backend().components_to_wind(u, v, missing=missing)


def interpolate_1d(target, coordinate, values, *, missing=None, log=False):
    return get_backend().interpolate_1d(
        target, coordinate, values, missing=missing, log=log)


def basic_sounding_qc(
    pres, hght, tmpc, dwpc, wdir, wspd, *, missing=-9999.0,
):
    return get_backend().basic_sounding_qc(
        pres, hght, tmpc, dwpc, wdir, wspd, missing=missing)


def pressure_sort_dedup_indices(pressure, *, missing=-9999.0):
    return get_backend().pressure_sort_dedup_indices(
        pressure, missing=missing)


def parse_sounding_rows(text: str, *, missing=-9999.0):
    return get_backend().parse_sounding_rows(text, missing=missing)


def decode_grib_point(path, lat, lon, *, missing=-9999.0) -> DecodedPoint:
    """Decode one nearest-grid-point pressure sounding with the active backend."""
    return get_backend().decode_grib_point(
        path, lat, lon, missing=missing,
    )


def clear_grib_caches(
    *, inventory=True, nearest=True, points=True, reset_stats=True,
):
    """Clear Python decoder LRUs and the selected Rust point cache, if any."""
    _clear_python_grib_caches(
        inventory=inventory,
        nearest=nearest,
        points=points,
        reset_stats=reset_stats,
    )
    backend = get_backend()
    clear = getattr(backend, "clear_grib_cache", None)
    if callable(clear):
        clear(points=points, reset_stats=reset_stats)


def grib_cache_info():
    """Return Python LRUs plus active-Rust point-cache diagnostics."""
    info = _python_grib_cache_info()
    backend = get_backend()
    native_info = getattr(backend, "grib_cache_info", None)
    if callable(native_info):
        info["rust_points"] = native_info()
    return info


__all__ = [
    "Backend",
    "BackendUnavailableError",
    "DecodedPoint",
    "GribDecodeError",
    "QualityControlResult",
    "backend_info",
    "basic_sounding_qc",
    "clear_grib_caches",
    "components_to_wind",
    "decode_grib_point",
    "get_backend",
    "grib_cache_info",
    "interpolate_1d",
    "parse_sounding_rows",
    "pressure_sort_dedup_indices",
    "reset_backend_cache",
    "wind_to_components",
]
