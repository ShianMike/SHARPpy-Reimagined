"""Interpolation helpers for SharpTab profiles.

Successor to ``sharppy.sharptab.interp``. This module resolves the value of a
profile field (temperature, pressure, height, wind components, ...) at a level
that is **not** necessarily one of the reported levels, by interpolating between
the two bracketing reported levels.

Two families of level lookups are provided on top of the standard
pressure/height interpolators, and are reused by the layer-derived parameters:

* **Height-AGL lookup** -- resolve a field (or pressure) at a target height above
  ground level. Used by the SFC-1 km lapse rate (T at 1000 m AGL), the SFC-500 m
  kinematics (winds/pressure at 500 m AGL), and 6CAPE (pressure at 6000 m AGL).
* **Isotherm lookup** -- resolve the pressure/height of a target isotherm (e.g.
  -10 degrees C and -30 degrees C) by interpolating between the two bracketing
  reported levels. Used by the hail-growth-zone (HGZ) CAPE integral.

Conventions follow upstream SHARPpy: pressure is interpolated in log-pressure
space, height fields are interpolated linearly in height. Every helper follows
the SHARPpy Reimagined design principle *missing data propagates, never crashes* -- when a
required input is absent/masked, the profile is too short to reach the target, or
the target isotherm is never crossed, the helper returns
:data:`~sharpmod.sharptab.constants.MISSING` rather than raising.

The functions accept any profile-like object exposing the standard reported-level
arrays:

* ``prof.pres`` -- pressure (hPa), monotonically decreasing with index.
* ``prof.hght`` -- geopotential height (m MSL), monotonically increasing.
* ``prof.tmpc`` -- temperature (degrees C).
* ``prof.dwpc`` -- dewpoint temperature (degrees C), optional.
* ``prof.u`` / ``prof.v`` -- wind components (kt), optional.
* ``prof.logp`` -- ``log10(pres)`` (optional; derived from ``prof.pres`` if
  absent).
* ``prof.sfc`` -- integer index of the surface level (optional; defaults to 0).
* ``prof.wetbulb`` -- wetbulb temperature (degrees C), optional.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from .constants import MISSING, is_missing

__all__ = [
    # standard level interpolation
    "pres",
    "hght",
    "temp",
    "dwpt",
    "components",
    "vec",
    "to_agl",
    "to_msl",
    # height-AGL lookup (task 3.1)
    "interp_hght_agl",
    "pres_at_hght_agl",
    "temp_at_hght_agl",
    "dwpt_at_hght_agl",
    "components_at_hght_agl",
    # isotherm lookup (task 3.1)
    "pres_at_isotherm",
    "hght_at_isotherm",
    # generic building blocks
    "generic_interp_hght",
    "generic_interp_pres",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sfc_index(prof) -> int:
    """Return the surface-level index, defaulting to 0 when ``prof.sfc`` is absent."""
    idx = getattr(prof, "sfc", 0)
    if idx is None or is_missing(idx):
        return 0
    return int(idx)


def _logp(prof):
    """Return ``log10(pressure)`` for ``prof``.

    Uses the cached ``prof.logp`` when present; otherwise derives it from
    ``prof.pres``. Returns a masked array so masked pressures propagate.
    """
    logp = getattr(prof, "logp", None)
    if logp is not None:
        return ma.asanyarray(logp)
    pres_arr = ma.asanyarray(prof.pres)
    return ma.log10(pres_arr)


def _valid_pairs(x, y):
    """Return finite, unmasked ``(x, y)`` pairs as plain float arrays.

    Combines the masks of both inputs and drops any NaNs so downstream
    ``np.interp`` sees only usable data points.
    """
    x = ma.asanyarray(x, dtype=float)
    y = ma.asanyarray(y, dtype=float)
    mask = ma.getmaskarray(x) | ma.getmaskarray(y)
    xf = np.asarray(x.filled(np.nan), dtype=float)
    yf = np.asarray(y.filled(np.nan), dtype=float)
    good = (~mask) & np.isfinite(xf) & np.isfinite(yf)
    return xf[good], yf[good]


def _finalize(result, log):
    """Convert a raw ``np.interp`` result (NaN == out of range) to the public form.

    Scalars become a Python ``float`` (optionally exponentiated when ``log`` is
    set) or :data:`MISSING` when NaN. Arrays become masked arrays with NaNs
    masked out.
    """
    arr = np.asarray(result, dtype=float)
    if arr.ndim == 0:
        value = arr[()]
        if np.isnan(value):
            return MISSING
        return float(10.0 ** value) if log else float(value)
    out = ma.masked_invalid(arr)
    if log:
        out = 10.0 ** out
    return out


# ---------------------------------------------------------------------------
# Generic interpolation routines
# ---------------------------------------------------------------------------

def generic_interp_hght(h, hght, field, log=False):
    """Interpolate ``field`` (indexed by ``hght``) to the target height ``h`` (m MSL).

    Parameters
    ----------
    h : float or array-like
        Target height(s) in metres MSL.
    hght : array-like
        Reported-level heights (m MSL); may be masked.
    field : array-like
        The field being interpolated; may be masked. When ``log`` is set the
        field is assumed to already be in ``log10`` space and the result is
        exponentiated before returning.
    log : bool
        Whether ``field`` is in ``log10`` space (used for pressure).

    Returns
    -------
    Interpolated value(s), or :data:`MISSING` when the height falls outside the
    reported range or fewer than two usable levels exist.
    """
    x, y = _valid_pairs(hght, field)
    if x.size < 2:
        return MISSING
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    result = np.interp(h, x, y, left=np.nan, right=np.nan)
    return _finalize(result, log)


def generic_interp_pres(p, pres, field, log=False):
    """Interpolate ``field`` (indexed by ``pres``) to the target pressure ``p``.

    Interpolation is performed in ``log10`` pressure space, consistent with
    upstream SHARPpy. ``p`` and ``pres`` are supplied as ``log10`` pressure.

    Returns
    -------
    Interpolated value(s), or :data:`MISSING` when the pressure falls outside the
    reported range or fewer than two usable levels exist.
    """
    x, y = _valid_pairs(pres, field)
    if x.size < 2:
        return MISSING
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    result = np.interp(p, x, y, left=np.nan, right=np.nan)
    return _finalize(result, log)


# ---------------------------------------------------------------------------
# Height <-> MSL/AGL conversions
# ---------------------------------------------------------------------------

def to_agl(prof, h):
    """Convert a height from mean sea level (MSL) to above ground level (AGL)."""
    return h - ma.asanyarray(prof.hght)[_sfc_index(prof)]


def to_msl(prof, h):
    """Convert a height from above ground level (AGL) to mean sea level (MSL)."""
    return h + ma.asanyarray(prof.hght)[_sfc_index(prof)]


# ---------------------------------------------------------------------------
# Standard level interpolation (by pressure / by height)
# ---------------------------------------------------------------------------

def pres(prof, h):
    """Interpolate the pressure (hPa) at the given height ``h`` (m MSL)."""
    return generic_interp_hght(h, prof.hght, _logp(prof), log=True)


def hght(prof, p):
    """Interpolate the height (m MSL) at the given pressure ``p`` (hPa)."""
    return generic_interp_pres(ma.log10(p), _logp(prof), prof.hght)


def temp(prof, p):
    """Interpolate the temperature (degrees C) at the given pressure ``p`` (hPa)."""
    return generic_interp_pres(ma.log10(p), _logp(prof), prof.tmpc)


def dwpt(prof, p):
    """Interpolate the dewpoint (degrees C) at the given pressure ``p`` (hPa)."""
    return generic_interp_pres(ma.log10(p), _logp(prof), prof.dwpc)


def components(prof, p):
    """Interpolate the (u, v) wind components (kt) at the given pressure ``p`` (hPa).

    Returns ``(MISSING, MISSING)`` when the profile carries no wind data.
    """
    u_arr = getattr(prof, "u", None)
    v_arr = getattr(prof, "v", None)
    if u_arr is None or v_arr is None:
        return MISSING, MISSING
    lp = ma.log10(p)
    logp = _logp(prof)
    u = generic_interp_pres(lp, logp, u_arr)
    v = generic_interp_pres(lp, logp, v_arr)
    return u, v


def vec(prof, p):
    """Interpolate the wind direction (deg) and speed (kt) at pressure ``p`` (hPa).

    Returns ``(MISSING, MISSING)`` when wind data is unavailable at the level.
    """
    u, v = components(prof, p)
    if is_missing(u) or is_missing(v):
        return MISSING, MISSING
    speed = float(np.hypot(u, v))
    direction = float((270.0 - np.degrees(np.arctan2(v, u))) % 360.0)
    return direction, speed


# ---------------------------------------------------------------------------
# Height-AGL lookup (task 3.1)
# ---------------------------------------------------------------------------

def interp_hght_agl(prof, field, h_agl, log=False):
    """Interpolate an arbitrary ``field`` to a target height ``h_agl`` (m AGL).

    The target AGL height is converted to MSL using the surface height and the
    field is interpolated between the two bracketing reported levels.

    Returns :data:`MISSING` when the profile does not reach ``h_agl`` or lacks
    enough usable levels.
    """
    return generic_interp_hght(to_msl(prof, h_agl), prof.hght, field, log=log)


def pres_at_hght_agl(prof, h_agl):
    """Return the pressure (hPa) at a target height ``h_agl`` (m AGL).

    Interpolates in log-pressure space between the two bracketing reported
    levels. Returns :data:`MISSING` for profiles that do not reach ``h_agl``.
    """
    return interp_hght_agl(prof, _logp(prof), h_agl, log=True)


def temp_at_hght_agl(prof, h_agl):
    """Return the temperature (degrees C) at a target height ``h_agl`` (m AGL).

    Used by the SFC-1 km lapse rate to obtain the 1000 m AGL temperature.
    Returns :data:`MISSING` for profiles that do not reach ``h_agl``.
    """
    return interp_hght_agl(prof, prof.tmpc, h_agl)


def dwpt_at_hght_agl(prof, h_agl):
    """Return the dewpoint (degrees C) at a target height ``h_agl`` (m AGL)."""
    return interp_hght_agl(prof, prof.dwpc, h_agl)


def components_at_hght_agl(prof, h_agl):
    """Return the (u, v) wind components (kt) at a target height ``h_agl`` (m AGL).

    Used by the SFC-500 m kinematics helpers. Returns ``(MISSING, MISSING)``
    when wind data is absent or the profile does not reach ``h_agl``.
    """
    u_arr = getattr(prof, "u", None)
    v_arr = getattr(prof, "v", None)
    if u_arr is None or v_arr is None:
        return MISSING, MISSING
    h_msl = to_msl(prof, h_agl)
    u = generic_interp_hght(h_msl, prof.hght, u_arr)
    v = generic_interp_hght(h_msl, prof.hght, v_arr)
    return u, v


# ---------------------------------------------------------------------------
# Isotherm lookup (task 3.1)
# ---------------------------------------------------------------------------

def _isotherm_pres_and_hght(prof, temp_c, wetbulb=False):
    """Resolve (pressure hPa, height m MSL) of the first ``temp_c`` isotherm crossing.

    Scans from the surface upward for the first level pair whose temperature
    brackets ``temp_c`` and interpolates in log-pressure/height space between
    them. Returns ``(MISSING, MISSING)`` when the isotherm is never reached or
    the profile is too short.
    """
    if wetbulb:
        profile = getattr(prof, "wetbulb", None)
        if profile is None:
            return MISSING, MISSING
    else:
        profile = prof.tmpc

    tmp = ma.asanyarray(profile, dtype=float)
    logp = _logp(prof)
    hgt = ma.asanyarray(prof.hght, dtype=float)

    mask = ma.getmaskarray(tmp) | ma.getmaskarray(logp) | ma.getmaskarray(hgt)
    tmpf = np.asarray(tmp.filled(np.nan), dtype=float)
    logpf = np.asarray(logp.filled(np.nan), dtype=float)
    hgtf = np.asarray(hgt.filled(np.nan), dtype=float)
    good = (~mask) & np.isfinite(tmpf) & np.isfinite(logpf) & np.isfinite(hgtf)

    tmpf = tmpf[good]
    logpf = logpf[good]
    hgtf = hgtf[good]
    if tmpf.size < 2:
        return MISSING, MISSING

    diff = tmpf - temp_c

    # Exact hit on a reported level: return it directly.
    exact = np.where(diff == 0.0)[0]
    if exact.size:
        i = int(exact[0])
        return float(10.0 ** logpf[i]), float(hgtf[i])

    if not np.any(diff <= 0) or not np.any(diff >= 0):
        # Isotherm never crossed within the profile.
        return MISSING, MISSING

    # First index where consecutive temperatures straddle the isotherm.
    cross = np.where((diff[:-1] * diff[1:]) < 0)[0]
    if cross.size == 0:
        return MISSING, MISSING
    i = int(cross.min())

    # Interpolate against temperature (x must be ascending for np.interp).
    x = [tmpf[i + 1], tmpf[i]]
    logp_i = np.interp(temp_c, x, [logpf[i + 1], logpf[i]])
    hght_i = np.interp(temp_c, x, [hgtf[i + 1], hgtf[i]])
    return float(10.0 ** logp_i), float(hght_i)


def pres_at_isotherm(prof, temp_c, wetbulb=False):
    """Return the pressure (hPa) of the first ``temp_c`` isotherm crossing.

    Interpolates in log-pressure space between the two bracketing reported
    levels. Used by the hail-growth-zone CAPE integral (-10 / -30 degrees C).
    Returns :data:`MISSING` when the isotherm is never reached or the profile is
    too short.
    """
    pres_val, _ = _isotherm_pres_and_hght(prof, temp_c, wetbulb=wetbulb)
    return pres_val


def hght_at_isotherm(prof, temp_c, wetbulb=False):
    """Return the height (m MSL) of the first ``temp_c`` isotherm crossing.

    Returns :data:`MISSING` when the isotherm is never reached or the profile is
    too short.
    """
    _, hght_val = _isotherm_pres_and_hght(prof, temp_c, wetbulb=wetbulb)
    return hght_val
