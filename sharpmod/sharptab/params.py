"""Layer-derived thermodynamic parameters for SharpTab.

Successor to ``sharppy.sharptab.params``. This module hosts the layer
thermodynamic computations that operate on an analyzed profile. The first
resident is the surface-to-1 km AGL lapse rate (Requirement 3).

Design principle (SHARPpy Reimagined design.md, "Design Principles"):

    *Missing data propagates, never crashes.* Every computation returns
    :data:`~sharpmod.sharptab.constants.MISSING` rather than raising when a
    required input is absent/masked or the profile is too shallow to define the
    requested layer.

Physical-range clamping of results to :data:`MISSING` (Requirement 14.6) is a
responsibility of the Profile lazy-attribute wiring (task 8.1), *not* of these
raw computations, so :func:`lapse_rate` returns the numeric lapse rate for any
layer it can resolve and reserves :data:`MISSING` for genuinely undefined inputs.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from . import interp
from .constants import MISSING, is_missing

__all__ = ["lapse_rate", "layer_cape_agl", "layer_cape_isotherm"]


def lapse_rate(prof, lower, upper, agl=False):
    """Compute the lapse rate ``(T_lower - T_upper) / depth`` in degrees C/km.

    The lapse rate is the temperature *decrease* across the layer normalised by
    the layer depth, so a normally cooling atmosphere yields a positive value.

    Parameters
    ----------
    prof : profile-like
        Any object exposing the standard reported-level arrays
        (``pres``, ``hght``, ``tmpc``, ...) consumed by
        :mod:`sharpmod.sharptab.interp`.
    lower : float
        Layer bottom. Height in metres AGL when ``agl`` is ``True``; otherwise
        pressure in hPa.
    upper : float
        Layer top. Height in metres AGL when ``agl`` is ``True``; otherwise
        pressure in hPa.
    agl : bool, optional
        When ``True``, ``lower`` / ``upper`` are heights above ground level
        (metres) and the layer-bound temperatures are obtained via
        :func:`interp.temp_at_hght_agl`; the layer depth is the AGL height
        difference. When ``False`` (default), ``lower`` / ``upper`` are pressure
        levels (hPa) and the depth is derived from the interpolated heights of
        those levels.

    Returns
    -------
    float or MISSING
        The lapse rate in degrees C/km, or
        :data:`~sharpmod.sharptab.constants.MISSING` when:

        * the layer bottom and top coincide (zero depth),
        * either layer-bound temperature is missing/masked -- which includes the
          surface temperature or surface height being masked, and the case where
          the highest valid level lies below the requested top (the top
          temperature cannot be interpolated), or
        * (pressure mode) either layer-bound height is missing/masked.

    Notes
    -----
    For the SFC-1 km AGL lapse rate the caller uses
    ``lapse_rate(prof, 0, 1000, agl=True)`` which evaluates
    ``(T_sfc - T_1km) / 1.0 km``. When 1 km AGL is not a reported level its
    temperature is interpolated between the two bracketing reported levels
    (Requirement 3.3); when the profile does not reach 1 km AGL the top
    temperature is :data:`MISSING` and the whole result propagates as
    :data:`MISSING` (Requirement 3.4).
    """
    if lower == upper:
        return MISSING

    if agl:
        t_lower = interp.temp_at_hght_agl(prof, lower)
        t_upper = interp.temp_at_hght_agl(prof, upper)
        depth_km = (upper - lower) / 1000.0
    else:
        t_lower = interp.temp(prof, lower)
        t_upper = interp.temp(prof, upper)
        z_lower = interp.hght(prof, lower)
        z_upper = interp.hght(prof, upper)
        if is_missing(z_lower) or is_missing(z_upper):
            return MISSING
        depth_km = (float(z_upper) - float(z_lower)) / 1000.0

    if is_missing(t_lower) or is_missing(t_upper):
        return MISSING
    if depth_km == 0.0:
        return MISSING

    return (float(t_lower) - float(t_upper)) / depth_km


# ---------------------------------------------------------------------------
# Layer-integrated CAPE (6CAPE and HGZ CAPE) -- task 5.2
# ---------------------------------------------------------------------------
#
# ``6CAPE`` is CAPE integrated over the SFC -> 6 km AGL layer (Requirement 21);
# ``HGZ CAPE`` is CAPE integrated over the hail-growth-zone layer bounded by the
# -10 degrees C and -30 degrees C isotherms (Requirement 19). Both are computed
# by lifting a surface-based parcel and accumulating positive buoyancy only
# between the two layer-bound pressures.
#
# The layer bounds are resolved with :mod:`sharpmod.sharptab.interp` so a bound
# that is not a reported level is interpolated between the two bracketing
# reported levels (Requirements 19.3, 21.3): ``pres_at_hght_agl`` for the 6 km
# AGL top and ``pres_at_isotherm`` for the -10 / -30 degrees C isotherms.
#
# The buoyancy integral itself is delegated to the installed
# ``sharppy.sharptab.params.cape`` -- the sanctioned reference oracle, mirroring
# how :mod:`sharpmod.sharptab.ecape` uses ``sharppy`` for its parcel ascent
# (design.md, Property 10: agreement with the upstream SHARPpy value). ``sharppy``
# is imported lazily; if it is unavailable the helpers degrade to :data:`MISSING`
# rather than raising, honouring the design principle *missing data propagates,
# never crashes*.


def _get_masked_field(prof, name):
    """Return ``prof.<name>`` as a NaN-masked float array, or ``None`` if absent."""
    arr = getattr(prof, name, None)
    if arr is None:
        return None
    return ma.masked_invalid(ma.asanyarray(arr, dtype=float))


def _has_masked(*arrays) -> bool:
    """Return ``True`` if any of the given arrays is absent or has a masked entry."""
    for arr in arrays:
        if arr is None:
            return True
        if ma.getmaskarray(arr).any():
            return True
    return False


def _layer_cape(prof, pbot, ptop):
    """Return the positive buoyancy (CAPE, J/kg) of a SFC parcel between ``pbot``/``ptop``.

    ``pbot`` / ``ptop`` are the (already resolved) layer-bound pressures in hPa
    with ``pbot > ptop`` (pressure decreases upward). The undiluted surface-based
    parcel buoyancy integral is obtained from the installed ``sharppy`` package.

    Returns :data:`MISSING` when the required pressure/height/temperature/moisture
    data is missing or masked, when ``sharppy`` is unavailable, or on any
    unexpected failure -- this function never raises.
    """
    pres = _get_masked_field(prof, "pres")
    hght = _get_masked_field(prof, "hght")
    tmpc = _get_masked_field(prof, "tmpc")
    dwpc = _get_masked_field(prof, "dwpc")

    if pres is None or hght is None or tmpc is None or dwpc is None:
        return MISSING

    n = int(pres.size)
    if n < 2 or hght.size != n or tmpc.size != n or dwpc.size != n:
        return MISSING

    # Layer-scoped masking (Req 19.5 / 21.5): drop levels lacking a usable
    # vertical coordinate (masked pres/hght/tmpc) rather than rejecting the whole
    # profile for a single missing level, and require at least two that remain.
    # A still-masked moisture datum on a retained level is carried as the -9999
    # sentinel, which the sharppy CAPE oracle masks.
    core_valid = ~(ma.getmaskarray(pres) | ma.getmaskarray(hght)
                   | ma.getmaskarray(tmpc))
    m = int(np.count_nonzero(core_valid))
    if m < 2:
        return MISSING

    pres_f = np.asarray(ma.asanyarray(pres)[core_valid].filled(-9999.0), dtype=float)
    hght_f = np.asarray(ma.asanyarray(hght)[core_valid].filled(-9999.0), dtype=float)
    tmpc_f = np.asarray(ma.asanyarray(tmpc)[core_valid].filled(-9999.0), dtype=float)
    dwpc_f = np.asarray(ma.asanyarray(dwpc)[core_valid].filled(-9999.0), dtype=float)

    # Winds are not needed for CAPE but ``create_profile`` requires them; use the
    # profile's winds (on the retained levels) when present, otherwise a benign
    # calm column.
    wdir = _get_masked_field(prof, "wdir")
    wspd = _get_masked_field(prof, "wspd")
    if wdir is None or wspd is None or wdir.size != n or wspd.size != n:
        wdir_f = np.zeros(m, dtype=float)
        wspd_f = np.zeros(m, dtype=float)
    else:
        wdir_f = np.asarray(ma.asanyarray(wdir)[core_valid].filled(0.0), dtype=float)
        wspd_f = np.asarray(ma.asanyarray(wspd)[core_valid].filled(0.0), dtype=float)

    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return MISSING

    try:
        sp_prof = sp_profile.create_profile(
            profile="default",
            pres=pres_f,
            hght=hght_f,
            tmpc=tmpc_f,
            dwpc=dwpc_f,
            wdir=wdir_f,
            wspd=wspd_f,
            missing=-9999.0,
            strictQC=False,
        )
        pcl = sp_params.cape(sp_prof, pbot=float(pbot), ptop=float(ptop))
        bplus = pcl.bplus
    except Exception:
        return MISSING

    if is_missing(bplus):
        return MISSING
    bplus = float(bplus)
    if not np.isfinite(bplus):
        return MISSING
    return bplus


def layer_cape_agl(prof, bottom, top):
    """Compute CAPE (J/kg) integrated over the ``bottom`` -> ``top`` m AGL layer.

    Used for 6CAPE via ``layer_cape_agl(prof, 0, 6000)``: the CAPE of a
    surface-based parcel accumulated between the surface and 6 km AGL
    (Requirement 21).

    Parameters
    ----------
    prof : profile-like
        Any object exposing the standard reported-level arrays (``pres``,
        ``hght``, ``tmpc``, ``dwpc``, ...).
    bottom : float
        Layer bottom height in metres AGL (``0`` for the surface).
    top : float
        Layer top height in metres AGL (``6000`` for 6CAPE).

    Returns
    -------
    float or MISSING
        The layer CAPE in J/kg, or
        :data:`~sharpmod.sharptab.constants.MISSING` when:

        * the layer is degenerate -- its top is at or below its bottom
          (``top <= bottom``) (Requirement 21.4),
        * the profile does not extend to ``top`` m AGL, so the top-bound
          pressure cannot be interpolated (Requirement 21.4),
        * the resolved layer collapses in pressure (``pbot <= ptop``), or
        * required pressure/temperature/moisture data is missing or masked
          (Requirement 21.5).

    Notes
    -----
    The layer-bound pressures are interpolated from the reported levels via
    :func:`interp.pres_at_hght_agl` (Requirement 21.3); the buoyancy integral is
    delegated to the reference ``sharppy`` parcel ascent. Never raises.
    """
    if top <= bottom:
        return MISSING

    pbot = interp.pres_at_hght_agl(prof, bottom)
    ptop = interp.pres_at_hght_agl(prof, top)
    if is_missing(pbot) or is_missing(ptop):
        return MISSING

    pbot = float(pbot)
    ptop = float(ptop)
    if pbot <= ptop:
        # Degenerate: the top is at or below the bottom in pressure terms.
        return MISSING

    return _layer_cape(prof, pbot, ptop)


def layer_cape_isotherm(prof, temp_bottom, temp_top):
    """Compute CAPE (J/kg) integrated over the ``temp_bottom`` -> ``temp_top`` layer.

    Used for HGZ CAPE via ``layer_cape_isotherm(prof, -10, -30)``: the CAPE of a
    surface-based parcel accumulated across the hail-growth-zone layer bounded by
    the -10 degrees C (bottom) and -30 degrees C (top) isotherms (Requirement 19).

    Parameters
    ----------
    prof : profile-like
        Any object exposing the standard reported-level arrays (``pres``,
        ``hght``, ``tmpc``, ``dwpc``, ...).
    temp_bottom : float
        Temperature (degrees C) of the layer bottom isotherm (warmer; ``-10``).
    temp_top : float
        Temperature (degrees C) of the layer top isotherm (colder; ``-30``).

    Returns
    -------
    float or MISSING
        The layer CAPE in J/kg, or
        :data:`~sharpmod.sharptab.constants.MISSING` when:

        * the profile does not span both isotherms, so a bound cannot be
          interpolated (Requirement 19.4),
        * the layer is degenerate -- the top isotherm level is at or below the
          bottom isotherm level (``pbot <= ptop`` in pressure) (Requirement 19.4),
          or
        * required pressure/temperature/moisture data is missing or masked
          (Requirement 19.5).

    Notes
    -----
    The isotherm-bound pressures are interpolated from the reported levels via
    :func:`interp.pres_at_isotherm` (Requirement 19.3); the buoyancy integral is
    delegated to the reference ``sharppy`` parcel ascent. Never raises.
    """
    pbot = interp.pres_at_isotherm(prof, temp_bottom)
    ptop = interp.pres_at_isotherm(prof, temp_top)
    if is_missing(pbot) or is_missing(ptop):
        return MISSING

    pbot = float(pbot)
    ptop = float(ptop)
    if pbot <= ptop:
        # Degenerate: the colder isotherm is not above the warmer isotherm.
        return MISSING

    return _layer_cape(prof, pbot, ptop)
