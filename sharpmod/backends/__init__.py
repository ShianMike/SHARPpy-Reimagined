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
    decode_grib_points as _decode_python_grib_points,
    decode_grib_wind_vorticities,
    decode_grib_wind_vorticity,
    grib_cache_info as _python_grib_cache_info,
)
from .protocol import (
    Backend,
    BatchProfileAnalysis,
    ConvectiveParcelWorkspace,
    DEFAULT_BATCH_PARALLEL_THRESHOLD,
    DEFAULT_BATCH_THREADS,
    DowndraftDiagnostics,
    KinematicLayer,
    ParcelAscent,
    ParcelDiagnostics,
    ParcelTrace,
    ParcelWorkspace,
    ProfileKinematics,
    ProfileThermodynamics,
    QualityControlResult,
)
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


def profile_kinematics(
    pres,
    hght,
    u,
    v,
    layer_tops_agl,
    *,
    sfc=0,
    missing=-9999.0,
) -> ProfileKinematics:
    """Compute shared surface-layer wind diagnostics in one backend call."""
    return get_backend().profile_kinematics(
        pres,
        hght,
        u,
        v,
        layer_tops_agl,
        sfc=sfc,
        missing=missing,
    )


def profile_parcels(
    pres,
    hght,
    tmpc,
    dwpc,
    *,
    sfc=0,
    missing=-9999.0,
) -> ParcelWorkspace:
    """Compute cached SB/MU/ML parcel diagnostics in one backend call."""
    return get_backend().profile_parcels(
        pres,
        hght,
        tmpc,
        dwpc,
        sfc=sfc,
        missing=missing,
    )


def profile_convective_parcels(
    pres,
    hght,
    tmpc,
    dwpc,
    *,
    sfc=0,
    missing=-9999.0,
) -> ConvectiveParcelWorkspace:
    """Compute standard parcel summaries, traces, and effective bounds."""
    return get_backend().profile_convective_parcels(
        pres,
        hght,
        tmpc,
        dwpc,
        sfc=sfc,
        missing=missing,
    )


def lift_parcel(
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
) -> ParcelAscent:
    """Lift one explicitly defined parcel and return its plotting trace."""
    return get_backend().lift_parcel(
        pres,
        hght,
        tmpc,
        dwpc,
        parcel_pressure,
        parcel_temperature,
        parcel_dewpoint,
        sfc=sfc,
        missing=missing,
    )


def profile_dcape(
    pres,
    hght,
    tmpc,
    dwpc,
    *,
    sfc=0,
    missing=-9999.0,
) -> DowndraftDiagnostics:
    """Compute DCAPE and its descending parcel trace."""
    return get_backend().profile_dcape(
        pres,
        hght,
        tmpc,
        dwpc,
        sfc=sfc,
        missing=missing,
    )


def profile_thermodynamics(
    pres,
    hght,
    tmpc,
    dwpc,
    *,
    sfc=0,
    missing=-9999.0,
) -> ProfileThermodynamics:
    """Compute all thermodynamic workspaces from one prepared profile."""
    return get_backend().profile_thermodynamics(
        pres,
        hght,
        tmpc,
        dwpc,
        sfc=sfc,
        missing=missing,
    )


def _profile_thermodynamics_buffers(
    pres,
    hght,
    tmpc,
    dwpc,
    *,
    sfc=0,
    missing=-9999.0,
):
    """Return internal array-backed traces without public tuple conversion."""
    return get_backend().profile_thermodynamics_buffers(
        pres,
        hght,
        tmpc,
        dwpc,
        sfc=sfc,
        missing=missing,
    )


def profile_batch_analysis(
    profiles,
    layer_tops_agl,
    *,
    missing=-9999.0,
    max_threads=DEFAULT_BATCH_THREADS,
    parallel_threshold=DEFAULT_BATCH_PARALLEL_THRESHOLD,
) -> BatchProfileAnalysis:
    """Compute ordered fixed-width diagnostics for complete profiles."""
    return get_backend().profile_batch_analysis(
        profiles,
        layer_tops_agl,
        missing=missing,
        max_threads=max_threads,
        parallel_threshold=parallel_threshold,
    )


def decode_grib_point(path, lat, lon, *, missing=-9999.0) -> DecodedPoint:
    """Decode one nearest-grid-point pressure sounding with the active backend."""
    return get_backend().decode_grib_point(
        path, lat, lon, missing=missing,
    )


def decode_grib_points(
    path, points, *, missing=-9999.0,
) -> tuple[DecodedPoint, ...]:
    """Decode several nearest-grid-point soundings with the active backend."""
    backend = get_backend()
    decode_many = getattr(backend, "decode_grib_points", None)
    if callable(decode_many):
        return decode_many(path, points, missing=missing)
    # Compatibility for third-party backend objects implementing the older
    # scalar-only protocol. Supported native modules are capability-checked.
    return _decode_python_grib_points(path, points, missing=missing)


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
        clear(
            inventory=inventory,
            points=points,
            reset_stats=reset_stats,
        )


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
    "BatchProfileAnalysis",
    "ConvectiveParcelWorkspace",
    "DecodedPoint",
    "DowndraftDiagnostics",
    "GribDecodeError",
    "KinematicLayer",
    "ParcelAscent",
    "ParcelDiagnostics",
    "ParcelTrace",
    "ParcelWorkspace",
    "ProfileKinematics",
    "ProfileThermodynamics",
    "QualityControlResult",
    "backend_info",
    "basic_sounding_qc",
    "clear_grib_caches",
    "components_to_wind",
    "decode_grib_point",
    "decode_grib_points",
    "decode_grib_wind_vorticities",
    "decode_grib_wind_vorticity",
    "get_backend",
    "grib_cache_info",
    "interpolate_1d",
    "lift_parcel",
    "parse_sounding_rows",
    "profile_convective_parcels",
    "profile_dcape",
    "profile_kinematics",
    "profile_batch_analysis",
    "profile_parcels",
    "profile_thermodynamics",
    "pressure_sort_dedup_indices",
    "reset_backend_cache",
    "wind_to_components",
]
