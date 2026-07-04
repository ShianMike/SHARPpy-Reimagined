"""Kinematic (wind) derived parameters for SharpTab.

Successor to ``sharppy.sharptab.winds``. This module provides the low-level
kinematic building blocks -- storm-relative helicity (SRH), bulk wind shear, the
pressure-weighted layer mean wind, and the Bunkers storm-motion estimate -- and
the layer helper :func:`sfc_500m_kinematics` that packages the SFC-to-500 m AGL
quantities required by Requirement 1.

Conventions follow upstream SHARPpy:

* Winds are carried in knots; SRH is accumulated in SI (m s^-1) and reported in
  m^2 s^-2.
* The layer bottom is the surface (the lowest valid reported level, ``prof.sfc``)
  and heights are specified above ground level (AGL).
* Storm-relative helicity for **every** standard layer (SFC-1 km, SFC-3 km, and
  the new SFC-500 m layer) uses the *same* Bunkers right-mover storm-motion
  vector (Requirement 1.5). :func:`storm_motion` is the single source of that
  vector: it reuses a storm motion already cached on the ``Profile``
  (``prof.srwind`` / ``prof.bunkers``) when present, and otherwise computes the
  Bunkers (2000) non-parcel estimate -- so the SFC-500 m SRH cannot diverge from
  the storm motion used by the existing SFC-1 km / SFC-3 km SRH.

Every routine follows the SHARPpy Reimagined design principle *missing data propagates,
never crashes*: when winds/height data are absent or the profile does not span
the requested layer, the routine returns
:data:`~sharpmod.sharptab.constants.MISSING` (or a tuple of it) rather than
raising.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from . import interp
from .constants import MISSING, is_missing, KTS_PER_MS

__all__ = [
    "kts2ms",
    "ms2kts",
    "mag",
    "wind_shear",
    "mean_wind",
    "mean_wind_npw",
    "helicity",
    "non_parcel_bunkers_motion",
    "storm_motion",
    "sfc_500m_kinematics",
]


# ---------------------------------------------------------------------------
# Small unit helpers (local, to avoid a utils dependency)
# ---------------------------------------------------------------------------

def kts2ms(value):
    """Convert knots to metres per second."""
    return value / KTS_PER_MS


def ms2kts(value):
    """Convert metres per second to knots."""
    return value * KTS_PER_MS


def mag(u, v):
    """Return the magnitude of the ``(u, v)`` vector."""
    return np.hypot(u, v)


def _has_winds(prof) -> bool:
    """Return ``True`` when the profile carries at least one valid wind level."""
    wdir = getattr(prof, "wdir", None)
    if wdir is None:
        return False
    try:
        return int(ma.asanyarray(wdir).count()) > 0
    except (TypeError, ValueError):
        return False


def _sfc_index(prof) -> int:
    idx = getattr(prof, "sfc", 0)
    if idx is None or is_missing(idx):
        return 0
    return int(idx)


def _sfc_pres(prof):
    """Return the surface pressure (hPa) or :data:`MISSING`."""
    pres = ma.asanyarray(prof.pres)
    val = pres[_sfc_index(prof)]
    if val is ma.masked or is_missing(val):
        return MISSING
    return float(val)


# ---------------------------------------------------------------------------
# Shear / mean wind
# ---------------------------------------------------------------------------

def wind_shear(prof, pbot, ptop):
    """Return the ``(u, v)`` shear (kt) between pressures ``pbot`` and ``ptop``.

    Returns ``(MISSING, MISSING)`` when winds are absent or either level lies
    outside the reported profile.
    """
    if not _has_winds(prof) or is_missing(pbot) or is_missing(ptop):
        return MISSING, MISSING
    ubot, vbot = interp.components(prof, pbot)
    utop, vtop = interp.components(prof, ptop)
    if is_missing(ubot) or is_missing(utop):
        return MISSING, MISSING
    return utop - ubot, vtop - vbot


def mean_wind(prof, pbot, ptop, dp=-1, stu=0, stv=0):
    """Return the pressure-weighted mean ``(u, v)`` wind (kt) over ``pbot``->``ptop``.

    Integrates the interpolated wind over the layer at a ``dp`` hPa increment and
    weights each level by its pressure. ``stu``/``stv`` optionally subtract a
    storm-motion vector. Returns ``(MISSING, MISSING)`` when winds are absent or
    the layer cannot be resolved.
    """
    if not _has_winds(prof) or is_missing(pbot) or is_missing(ptop):
        return MISSING, MISSING
    if dp > 0:
        dp = -dp
    ps = np.arange(pbot, ptop + dp, dp)
    if ps.size == 0:
        return MISSING, MISSING
    u, v = interp.components(prof, ps)
    if is_missing(u) or is_missing(v):
        return MISSING, MISSING
    mnu = ma.average(u, weights=ps) - stu
    mnv = ma.average(v, weights=ps) - stv
    if is_missing(mnu) or is_missing(mnv):
        return MISSING, MISSING
    return float(mnu), float(mnv)


def mean_wind_npw(prof, pbot, ptop, dp=-1, stu=0, stv=0):
    """Return the non-pressure-weighted mean ``(u, v)`` wind (kt) over the layer.

    Used by the Bunkers storm-motion estimate. Returns ``(MISSING, MISSING)``
    when winds are absent or the layer cannot be resolved.
    """
    if not _has_winds(prof) or is_missing(pbot) or is_missing(ptop):
        return MISSING, MISSING
    if dp > 0:
        dp = -dp
    ps = np.arange(pbot, ptop + dp, dp)
    if ps.size == 0:
        return MISSING, MISSING
    u, v = interp.components(prof, ps)
    if is_missing(u) or is_missing(v):
        return MISSING, MISSING
    mnu = u.mean() - stu
    mnv = v.mean() - stv
    if is_missing(mnu) or is_missing(mnv):
        return MISSING, MISSING
    return float(mnu), float(mnv)


# ---------------------------------------------------------------------------
# Storm-relative helicity
# ---------------------------------------------------------------------------

def helicity(prof, lower, upper, stu=0, stv=0):
    """Compute storm-relative helicity (m^2/s^2) over the layer ``lower``->``upper``.

    Parameters
    ----------
    prof:
        Profile-like object exposing ``pres``/``hght``/``u``/``v``/``wdir``.
    lower, upper:
        Bottom and top of the layer in metres AGL.
    stu, stv:
        Storm-motion components (kt). With ``stu == stv == 0`` this reduces to
        ground-relative helicity.

    Returns
    -------
    tuple
        ``(total, positive, negative)`` helicity in m^2/s^2, or
        ``(MISSING, MISSING, MISSING)`` when winds/height data are absent or the
        layer falls outside the reported profile. ``total`` is the combined
        (positive + negative) helicity used as the reported SRH.
    """
    if not _has_winds(prof) or is_missing(lower) or is_missing(upper) \
            or is_missing(stu) or is_missing(stv):
        return MISSING, MISSING, MISSING

    if lower == upper:
        return 0.0, 0.0, 0.0

    lower_msl = interp.to_msl(prof, lower)
    upper_msl = interp.to_msl(prof, upper)
    plower = interp.pres(prof, lower_msl)
    pupper = interp.pres(prof, upper_msl)
    if is_missing(plower) or is_missing(pupper):
        return MISSING, MISSING, MISSING

    pres = ma.asanyarray(prof.pres)
    u_arr = ma.asanyarray(prof.u)
    v_arr = ma.asanyarray(prof.v)

    # Interior reported levels strictly within [plower, pupper] (pres decreasing).
    at_or_above_bottom = np.where(plower >= pres)[0]
    at_or_below_top = np.where(pupper <= pres)[0]
    if at_or_above_bottom.size == 0 or at_or_below_top.size == 0:
        return MISSING, MISSING, MISSING
    ind1 = int(at_or_above_bottom.min())
    ind2 = int(at_or_below_top.max())

    u1, v1 = interp.components(prof, plower)
    u2, v2 = interp.components(prof, pupper)
    if is_missing(u1) or is_missing(u2):
        return MISSING, MISSING, MISSING

    u = np.concatenate([[u1], u_arr[ind1:ind2 + 1].compressed(), [u2]])
    v = np.concatenate([[v1], v_arr[ind1:ind2 + 1].compressed(), [v2]])

    sru = kts2ms(u - stu)
    srv = kts2ms(v - stv)
    layers = (sru[1:] * srv[:-1]) - (sru[:-1] * srv[1:])
    phel = float(layers[layers > 0].sum())
    nhel = float(layers[layers < 0].sum())
    return phel + nhel, phel, nhel


# ---------------------------------------------------------------------------
# Bunkers storm motion
# ---------------------------------------------------------------------------

def non_parcel_bunkers_motion(prof):
    """Compute the Bunkers (2000) non-parcel storm motion.

    Returns ``(rstu, rstv, lstu, lstv)`` -- the right- and left-mover
    storm-motion components (kt) -- or a 4-tuple of :data:`MISSING` when winds
    are absent or the profile does not span the SFC-6 km layer.
    """
    if not _has_winds(prof):
        return MISSING, MISSING, MISSING, MISSING

    d = ms2kts(7.5)  # 7.5 m/s empirical deviation
    psfc = _sfc_pres(prof)
    p6km = interp.pres_at_hght_agl(prof, 6000.)
    if is_missing(psfc) or is_missing(p6km):
        return MISSING, MISSING, MISSING, MISSING

    mnu6, mnv6 = mean_wind_npw(prof, psfc, p6km)
    shru, shrv = wind_shear(prof, psfc, p6km)
    if is_missing(mnu6) or is_missing(shru):
        return MISSING, MISSING, MISSING, MISSING

    shr_mag = mag(shru, shrv)
    if is_missing(shr_mag) or float(shr_mag) == 0.0:
        return MISSING, MISSING, MISSING, MISSING
    tmp = d / float(shr_mag)

    rstu = mnu6 + (tmp * shrv)
    rstv = mnv6 - (tmp * shru)
    lstu = mnu6 - (tmp * shrv)
    lstv = mnv6 + (tmp * shru)
    return float(rstu), float(rstv), float(lstu), float(lstv)


def storm_motion(prof):
    """Return the ``(rstu, rstv, lstu, lstv)`` storm motion (kt) used for SRH.

    Single source of the Bunkers storm-motion vector so that every SRH layer
    (SFC-500 m, SFC-1 km, SFC-3 km) shares the identical convention
    (Requirement 1.5). If the ``Profile`` already exposes a storm-motion vector
    (``prof.srwind`` -- as ``(rstu, rstv, lstu, lstv)`` -- or ``prof.bunkers``),
    that vector is reused verbatim; otherwise the Bunkers (2000) non-parcel
    estimate is computed.

    Returns a 4-tuple of :data:`MISSING` when the storm motion cannot be
    determined.
    """
    for attr in ("srwind", "bunkers"):
        cached = getattr(prof, attr, None)
        if cached is None:
            continue
        try:
            vec = tuple(cached)
        except TypeError:
            continue
        if len(vec) >= 4:
            return vec[0], vec[1], vec[2], vec[3]
        if len(vec) == 2:
            return vec[0], vec[1], MISSING, MISSING
    return non_parcel_bunkers_motion(prof)


# ---------------------------------------------------------------------------
# SFC-500 m layer kinematics (Requirement 1)
# ---------------------------------------------------------------------------

def sfc_500m_kinematics(prof):
    """Compute the SFC-to-500 m AGL layer kinematics (Requirement 1).

    The layer bottom is the surface (the lowest valid reported level) and the
    layer top is 500 m AGL (Requirement 1.4). Quantities are computed
    independently, so a term whose inputs are unavailable is returned as
    :data:`MISSING` without masking the others (Requirement 1.6).

    Returns
    -------
    tuple
        ``(srh, shear_kt, mean_wind_uv, srw_uv)`` where

        * ``srh`` -- storm-relative helicity over SFC->500 m AGL in m^2/s^2,
          computed with the shared Bunkers right-mover storm motion
          (Requirements 1.1, 1.5);
        * ``shear_kt`` -- bulk shear ``|V(500 m) - V(sfc)|`` in knots
          (Requirement 1.2);
        * ``mean_wind_uv`` -- the pressure-weighted mean wind over the layer as a
          ``(u, v)`` vector in knots (Requirement 1.3);
        * ``srw_uv`` -- the pressure-weighted storm-relative mean wind over the
          layer as a ``(u, v)`` vector in knots, i.e. the layer mean wind with
          the shared Bunkers right-mover storm motion subtracted.

        Any component is :data:`MISSING` when winds/height data are absent or the
        profile does not extend to 500 m AGL (Requirement 1.6).
    """
    top_agl = 500.0

    if not _has_winds(prof):
        return MISSING, MISSING, MISSING, MISSING

    # Reject profiles that do not span the SFC->500 m AGL layer.
    hght = ma.asanyarray(prof.hght)
    sfc = _sfc_index(prof)
    valid_h = hght[~ma.getmaskarray(hght)] if ma.isMaskedArray(hght) else hght
    if valid_h.size < 2:
        return MISSING, MISSING, MISSING, MISSING
    depth_agl = float(np.max(valid_h)) - float(hght[sfc])
    if not np.isfinite(depth_agl) or depth_agl < top_agl:
        return MISSING, MISSING, MISSING, MISSING

    # --- bulk shear: |V(500 m) - V(sfc)| (kt) -------------------------------
    u_sfc, v_sfc = interp.components_at_hght_agl(prof, 0.0)
    u_top, v_top = interp.components_at_hght_agl(prof, top_agl)
    if is_missing(u_sfc) or is_missing(u_top):
        shear_kt = MISSING
    else:
        shear_kt = float(mag(u_top - u_sfc, v_top - v_sfc))

    # --- pressure-weighted mean wind (kt), returned as a (u, v) vector -------
    psfc = _sfc_pres(prof)
    ptop = interp.pres_at_hght_agl(prof, top_agl)
    mnu, mnv = mean_wind(prof, psfc, ptop)
    mean_wind_uv = MISSING if is_missing(mnu) else (mnu, mnv)

    # --- storm motion (shared Bunkers right-mover) --------------------------
    rstu, rstv, _lstu, _lstv = storm_motion(prof)
    have_motion = not (is_missing(rstu) or is_missing(rstv))

    # --- storm-relative helicity (m^2/s^2), shared Bunkers storm motion ------
    if not have_motion:
        srh = MISSING
    else:
        total, _phel, _nhel = helicity(prof, 0.0, top_agl, stu=rstu, stv=rstv)
        srh = MISSING if is_missing(total) else float(total)

    # --- storm-relative mean wind (kt), returned as a (u, v) vector ---------
    # The layer mean wind with the shared Bunkers right-mover motion removed.
    if not have_motion:
        srw_uv = MISSING
    else:
        sru, srv = mean_wind(prof, psfc, ptop, stu=rstu, stv=rstv)
        srw_uv = MISSING if is_missing(sru) else (sru, srv)

    return srh, shear_kt, mean_wind_uv, srw_uv
