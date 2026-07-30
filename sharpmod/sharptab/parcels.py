"""Cached coarse-grained parcel diagnostics for SharpTab consumers.

The backend computes the standard surface-based, most-unstable, and
100-hPa mixed-layer parcels together. Real ``Profile`` instances retain that
immutable workspace so derived parameters reuse one ascent bundle. Lightweight
profile-like objects that cannot accept attributes remain supported and simply
recompute. Backend failures return ``None`` so callers can preserve their
existing reference-oracle fallbacks.
"""

from __future__ import annotations

import numpy as np

from sharpmod import backends as _backends

from .constants import is_missing

__all__ = ["parcel", "profile_parcels"]

_PARCEL_CACHE_ATTR = "_sharpmod_profile_parcels"
_PARCEL_CACHE_MISS = object()


def _sfc_index(prof) -> int:
    value = getattr(prof, "sfc", 0)
    if value is None or is_missing(value):
        return 0
    return int(value)


def profile_parcels(prof):
    """Return the cached SB/MU/ML backend workspace, or ``None`` on failure."""
    cached = getattr(prof, _PARCEL_CACHE_ATTR, _PARCEL_CACHE_MISS)
    if cached is not _PARCEL_CACHE_MISS:
        return cached
    try:
        result = _backends.profile_parcels(
            prof.pres,
            prof.hght,
            prof.tmpc,
            prof.dwpc,
            sfc=_sfc_index(prof),
        )
    except Exception:
        result = None
    try:
        setattr(prof, _PARCEL_CACHE_ATTR, result)
    except (AttributeError, TypeError):
        pass
    return result


def parcel(prof, kind):
    """Return one finite standard parcel summary, or ``None`` if unavailable."""
    workspace = profile_parcels(prof)
    if workspace is None:
        return None
    try:
        result = workspace.parcel(kind)
    except (AttributeError, KeyError):
        return None
    values = (
        result.start_pressure,
        result.start_temperature,
        result.start_dewpoint,
        result.cape,
        result.cin,
    )
    if not all(np.isfinite(float(value)) for value in values):
        return None
    return result
