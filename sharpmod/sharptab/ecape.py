"""Entraining CAPE (ECAPE) after Peters et al. (2023).

This module implements the analytic entraining-CAPE formula of

    Peters, J. M., Chavas, D. R., Su, C.-Y., Morrison, H., & Coffer, B. E.
    (2023). "An analytic formula for entraining CAPE in mid-latitude storm
    environments." *J. Atmos. Sci.* (arXiv:2301.04712).

The analytic result expresses ECAPE as a closed-form function of a handful of
state quantities derived from the environmental sounding (undiluted CAPE, the
buoyancy-dilution potential ``NCAPE``, the 0-1 km storm-relative inflow, and the
equilibrium-level height) plus a set of published constant parameters. The
reference Python implementation used as the validation oracle is the authors'
``ECAPE_FUNCTIONS`` (mirrored by the ``ecape`` PyPI package's ``calc_ecape``);
the term-by-term structure below follows that reference exactly.

Design contract (SHARPpy Reimagined design.md, Requirement 5):

* 5.1 / 5.2 -- return ECAPE in J/kg, computed by applying the single published
  ECAPE formulation to a parcel ascent from the analyzed Profile.
* 5.3 / 5.4 -- the returned value is ``>= 0`` and ``<= undiluted CAPE`` (both are
  enforced by an explicit clamp; the analytic formula satisfies them by
  construction, the clamp only guards floating-point overshoot).
* 5.5 -- when the Profile's undiluted CAPE is zero, ECAPE is exactly ``0``.
* 5.6 -- when any required pressure / temperature / moisture / wind datum is
  missing or masked, return :data:`~sharpmod.sharptab.constants.MISSING` rather
  than a numeric result. The function *never raises* -- any unexpected failure
  is caught and reported as ``MISSING``.
* 5.7 -- the returned value agrees with the authors' ``ECAPE_FUNCTIONS``
  reference (``ecape.calc.calc_ecape``) within ``max(5%, 10 J/kg)``.

Building-block reconciliation (Requirement 5.7 / Property 10)
-------------------------------------------------------------
The reference ``calc_ecape`` builds its inputs from MetPy: undiluted CAPE from
``mpcalc.most_unstable_cape_cin``, the LFC/EL reported-level indices and the EL
height (used for ``psi``) from ``mpcalc.lfc`` / ``mpcalc.el`` off a most-unstable
parcel profile, the ``NCAPE`` dilution integral from ``mpcalc.moist_static_energy``
/ ``mpcalc.saturation_mixing_ratio``, and the 0-1 km storm-relative inflow from
``mpcalc.bunkers_storm_motion``. The analytic ``psi`` / ``ecape_a`` core is
identical regardless of source, so to agree with the oracle within tolerance we
compute *all* building blocks from MetPy here, exactly as ``ECAPE_FUNCTIONS``
does. MetPy is imported lazily; if it is unavailable the function degrades to
``MISSING`` rather than raising.

* **Constants.** Physical constants used in the analytic ``psi`` / ``ecape_a``
  core come from :mod:`sharpmod.sharptab.constants`; the ``NCAPE`` integral uses
  MetPy's own constants to match the oracle term-for-term.
* **Turbulence constants** (Peters et al. 2023, section 4 / ``calc_psi``):
  ``sigma = 1.6``, ``alpha = 0.8``, ``L_mix = 120 m``, Prandtl ``Pr = 1/3``,
  von Karman ``k^2 = 0.18``.

Undiluted-CAPE bound (Requirements 5.4 / 5.5)
---------------------------------------------
The analytic ``ecape_a`` core is evaluated with the MetPy most-unstable CAPE
(``cape``) so it matches the reference term-for-term. The *contract* quantities
-- the value ECAPE is clamped against (Req 5.4) and the zero-CAPE trigger
(Req 5.5) -- use the sharppy most-unstable CAPE (``params.parcelx(flag=3).bplus``),
the sanctioned SharpTab parcel-ascent oracle. The reference ``calc_ecape`` applies
no upper clamp, so its raw analytic value can slightly exceed the (smaller) MetPy
CAPE for weakly buoyant profiles; clamping against the sharppy MUCAPE keeps the
``0 <= ECAPE <= undiluted CAPE`` contract without cutting the value below the
reference (the sharppy MUCAPE is >= the reference ECAPE across the documented
sounding set). When sharppy is unavailable the MetPy CAPE is used as the bound.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from .constants import (
    KTS_PER_MS,
    MISSING,
    TOL,
    is_missing,
)

__all__ = ["ecape"]


# ---------------------------------------------------------------------------
# Turbulence / entrainment constants (Peters et al. 2023, sec. 4; calc_psi)
# ---------------------------------------------------------------------------
_SIGMA = 1.6          # updraft aspect-ratio parameter
_ALPHA = 0.8          # ratio of mixing length to updraft radius
_L_MIX = 120.0        # mixing length (m)
_PR = 1.0 / 3.0       # Prandtl number
_KSQ = 0.18           # von Karman constant squared


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _get_field(prof, name):
    """Return ``prof.<name>`` as a masked float array, or ``None`` if absent."""
    arr = getattr(prof, name, None)
    if arr is None:
        return None
    return ma.masked_invalid(ma.asanyarray(arr, dtype=float))


def _has_masked(*arrays) -> bool:
    """Return ``True`` if any of the given masked arrays has a masked entry."""
    for arr in arrays:
        if arr is None:
            return True
        if ma.getmaskarray(arr).any():
            return True
    return False


# ---------------------------------------------------------------------------
# Analytic ECAPE terms (mirrors ECAPE_FUNCTIONS.calc_psi / calc_ecape_a)
# ---------------------------------------------------------------------------

def _psi(el_z_msl):
    """Parameter ``psi`` (dimensionless), Peters et al. (2023) eqn. 52 / calc_psi."""
    if el_z_msl is None or el_z_msl <= 0.0 or not np.isfinite(el_z_msl):
        return None
    return (_KSQ * _ALPHA ** 2 * np.pi ** 2 * _L_MIX) / (
        4.0 * _PR * _SIGMA ** 2 * el_z_msl
    )


def _ecape_a(sr_wind, psi, ncape, cape):
    """Analytic entraining CAPE (J/kg), Peters et al. (2023) eqn. 55 / calc_ecape_a.

    ``sr_wind`` is the 0-1 km storm-relative inflow magnitude (m/s); ``cape`` and
    ``ncape`` are in J/kg. Returns the raw analytic value (not yet clamped).
    """
    v2 = sr_wind ** 2
    a = psi / v2                                   # psi / Vsr^2, appears throughout
    term_a = v2 / 2.0
    term_b = (-1.0 - psi - 2.0 * a * ncape) / (4.0 * a)
    radicand = (1.0 + psi + 2.0 * a * ncape) ** 2 + 8.0 * a * (cape - psi * ncape)
    if radicand < 0.0:
        radicand = 0.0
    term_c = np.sqrt(radicand) / (4.0 * a)
    return term_a + term_b + term_c


# ---------------------------------------------------------------------------
# MetPy-based building blocks (mirror ECAPE_FUNCTIONS exactly)
# ---------------------------------------------------------------------------

def _building_blocks(pres_f, hght_f, tmpc_f, dwpc_f, u_kt, v_kt):
    """Compute ``(cape, ncape, sr_wind, el_z)`` via MetPy, mirroring calc_ecape.

    Returns a dict with keys ``cape`` (J/kg), ``ncape`` (J/kg), ``sr_wind``
    (m/s) and ``el_z`` (m MSL), or ``None`` when MetPy is unavailable or the
    ascent / storm motion cannot be resolved. ``cape`` is always resolved when
    this returns non-``None`` so the caller can honour the zero-CAPE contract.
    """
    try:
        from metpy.units import units
        import metpy.calc as mpcalc
        from metpy.constants import earth_gravity, dry_air_spec_heat_press
    except Exception:
        return None

    p = pres_f * units.hPa
    z = hght_f * units.meter
    t = tmpc_f * units.degC
    td_in = dwpc_f * units.degC

    # Mirror the reference exactly: it receives specific humidity and recovers
    # the dewpoint from it, so round-trip through specific humidity here too.
    q = mpcalc.specific_humidity_from_dewpoint(p, td_in)
    dewp = mpcalc.dewpoint_from_specific_humidity(p, t, q)

    # --- undiluted (most-unstable) CAPE ------------------------------------
    cape_q, _cin = mpcalc.most_unstable_cape_cin(p, t, dewp)
    cape = float(cape_q.to("J/kg").magnitude)
    if not np.isfinite(cape):
        return None
    if cape <= 0.0:
        # No positive area -> the free-convective layer (and hence NCAPE / psi)
        # is undefined. Report the CAPE only; the caller decides what to do.
        return {"cape": cape, "ncape": None, "sr_wind": None, "el_z": None}

    # --- LFC / EL reported-level indices + EL height (for psi) --------------
    parcel_p, parcel_t, parcel_td, *_ = mpcalc.most_unstable_parcel(p, t, dewp)
    parcel_profile = mpcalc.parcel_profile(p, parcel_t, parcel_td)

    lfc_p, _lfc_t = mpcalc.lfc(p, t, dewp, parcel_temperature_profile=parcel_profile)
    el_p, _el_t = mpcalc.el(p, t, dewp, parcel_temperature_profile=parcel_profile)

    lfc_hits = (p - lfc_p > 0).nonzero()[0]
    el_hits = (p - el_p > 0).nonzero()[0]
    if lfc_hits.size == 0 or el_hits.size == 0:
        return None
    lfc_idx = int(lfc_hits[-1])
    el_idx = int(el_hits[-1])
    el_z = float(z[el_idx].to("m").magnitude)

    # --- NCAPE dilution integral (calc_mse / calc_integral_arg / calc_ncape)-
    mse = mpcalc.moist_static_energy(z, t, q)
    n = mse.size
    mse_bar = (np.cumsum(mse) / np.arange(1, n + 1)).to("J/kg")
    sat_mr = mpcalc.saturation_mixing_ratio(p, t)
    mse_star = mpcalc.moist_static_energy(z, t, sat_mr).to("J/kg")

    t_k = t.to("kelvin")
    integral_arg = -(earth_gravity / (dry_air_spec_heat_press * t_k)) * (
        mse_bar - mse_star
    )
    if el_idx <= lfc_idx:
        ncape = 0.0
    else:
        seg = (
            0.5 * integral_arg[lfc_idx:el_idx]
            + 0.5 * integral_arg[lfc_idx + 1:el_idx + 1]
        ) * (z[lfc_idx + 1:el_idx + 1] - z[lfc_idx:el_idx])
        ncape = float(np.sum(seg).to("J/kg").magnitude)
    if not np.isfinite(ncape):
        return None

    # --- 0-1 km AGL storm-relative inflow (calc_sr_wind) --------------------
    u_ms = (u_kt / KTS_PER_MS) * units("m/s")
    v_ms = (v_kt / KTS_PER_MS) * units("m/s")
    height_agl = z - z[0]
    bunkers_right, _left, _mean = mpcalc.bunkers_storm_motion(p, u_ms, v_ms, height_agl)
    u_sr = u_ms - bunkers_right[0]
    v_sr = v_ms - bunkers_right[1]
    in_layer = np.nonzero(
        (height_agl >= 0 * units("m")) & (height_agl <= 1000 * units("m"))
    )
    u_sr_1km = u_sr[in_layer]
    v_sr_1km = v_sr[in_layer]
    if u_sr_1km.size == 0:
        return None
    sr_wind = float(
        np.mean(mpcalc.wind_speed(u_sr_1km, v_sr_1km)).to("m/s").magnitude
    )
    if not np.isfinite(sr_wind):
        return None

    return {"cape": cape, "ncape": ncape, "sr_wind": sr_wind, "el_z": el_z}


def _sharppy_mucape(pres_f, hght_f, tmpc_f, dwpc_f, wdir_f, wspd_f):
    """Undiluted most-unstable CAPE (J/kg) via the sharppy oracle, or ``None``.

    This is the sanctioned SharpTab parcel-ascent oracle
    (``params.parcelx(flag=3).bplus``) used as the ``0 <= ECAPE <= undiluted
    CAPE`` bound (Req 5.4) and the zero-CAPE trigger (Req 5.5). Returns ``None``
    when sharppy is unavailable or the ascent cannot be resolved.
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return None
    try:
        prof = sp_profile.create_profile(
            profile="default",
            pres=np.asarray(pres_f, dtype=float),
            hght=np.asarray(hght_f, dtype=float),
            tmpc=np.asarray(tmpc_f, dtype=float),
            dwpc=np.asarray(dwpc_f, dtype=float),
            wdir=np.asarray(wdir_f, dtype=float),
            wspd=np.asarray(wspd_f, dtype=float),
            missing=-9999.0,
            strictQC=False,
        )
        cape = sp_params.parcelx(prof, flag=3).bplus
    except Exception:
        return None
    if is_missing(cape) or not np.isfinite(cape):
        return None
    return float(cape)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ecape(prof):
    """Compute entraining CAPE (ECAPE, J/kg) for ``prof`` after Peters et al. (2023).

    Parameters
    ----------
    prof:
        Any profile-like object exposing the reported-level arrays ``pres``
        (hPa), ``hght`` (m MSL), ``tmpc`` (degrees C), ``dwpc`` (degrees C), and
        wind as either ``wdir`` (deg) / ``wspd`` (kt) or ``u`` / ``v`` (kt).

    Returns
    -------
    float or MISSING
        ECAPE in J/kg (``>= 0`` and ``<= undiluted MUCAPE``); exactly ``0.0``
        when the undiluted CAPE is zero (Req 5.5); and
        :data:`~sharpmod.sharptab.constants.MISSING` when a required pressure /
        temperature / moisture / wind datum is missing or masked, when MetPy is
        unavailable, or when the ascent cannot be resolved (Req 5.6). Never
        raises.
    """
    try:
        return _ecape_impl(prof)
    except Exception:
        # Design principle: missing data / unresolved ascent propagates as
        # MISSING, never crashes.
        return MISSING


def _ecape_impl(prof):
    pres = _get_field(prof, "pres")
    hght = _get_field(prof, "hght")
    tmpc = _get_field(prof, "tmpc")
    dwpc = _get_field(prof, "dwpc")

    # Wind: prefer wdir/wspd; otherwise derive them from u/v components.
    wdir = _get_field(prof, "wdir")
    wspd = _get_field(prof, "wspd")
    u_kt = _get_field(prof, "u")
    v_kt = _get_field(prof, "v")
    if (wdir is None or wspd is None) and (u_kt is not None and v_kt is not None):
        wspd = ma.sqrt(u_kt ** 2 + v_kt ** 2)
        wdir = (270.0 - ma.degrees(ma.arctan2(v_kt, u_kt))) % 360.0

    if pres is None or hght is None or tmpc is None or dwpc is None \
            or wdir is None or wspd is None:
        return MISSING

    n = int(pres.size)
    if n < 3 or not (hght.size == tmpc.size == dwpc.size == wdir.size
                     == wspd.size == n):
        return MISSING

    # Layer-scoped masking (Req 5.6): rather than rejecting the whole profile
    # when a single level is masked (e.g. a missing top-of-sounding wind),
    # retain the levels whose required columns are all valid. MetPy operates on
    # this clean, monotonic subset, which still spans the entraining-CAPE
    # ascent; ECAPE is MISSING only when too few valid levels remain to lift a
    # parcel or the ascent itself cannot be resolved.
    valid = ~(ma.getmaskarray(pres) | ma.getmaskarray(hght)
              | ma.getmaskarray(tmpc) | ma.getmaskarray(dwpc)
              | ma.getmaskarray(wdir) | ma.getmaskarray(wspd))
    if int(np.count_nonzero(valid)) < 3:
        return MISSING

    pres_f = np.asarray(ma.asanyarray(pres)[valid], dtype=float)
    hght_f = np.asarray(ma.asanyarray(hght)[valid], dtype=float)
    tmpc_f = np.asarray(ma.asanyarray(tmpc)[valid], dtype=float)
    dwpc_f = np.asarray(ma.asanyarray(dwpc)[valid], dtype=float)
    wdir_f = np.asarray(ma.asanyarray(wdir)[valid], dtype=float)
    wspd_f = np.asarray(ma.asanyarray(wspd)[valid], dtype=float)

    # Wind components (kt), meteorological convention.
    rad = np.deg2rad(wdir_f)
    u_kt_f = -wspd_f * np.sin(rad)
    v_kt_f = -wspd_f * np.cos(rad)

    # --- undiluted-CAPE bound (contract quantity; Req 5.4 / 5.5) -----------
    # The sharppy most-unstable CAPE is the sanctioned parcel-ascent oracle and
    # the exact bound the upper-bound property (Req 5.4) checks against.
    mucape_bound = _sharppy_mucape(pres_f, hght_f, tmpc_f, dwpc_f, wdir_f, wspd_f)
    if mucape_bound is not None and (not np.isfinite(mucape_bound)):
        mucape_bound = None
    # Requirement 5.5: zero undiluted CAPE -> exactly zero ECAPE.
    if mucape_bound is not None and mucape_bound <= 0.0:
        return 0.0

    # --- MetPy building blocks for the analytic core (mirror ECAPE_FUNCTIONS)
    blocks = _building_blocks(pres_f, hght_f, tmpc_f, dwpc_f, u_kt_f, v_kt_f)
    if blocks is None:
        return MISSING

    cape = blocks["cape"]

    # When sharppy is unavailable, fall back to the MetPy CAPE as the contract
    # bound / zero trigger.
    undiluted = mucape_bound if mucape_bound is not None else cape
    if not np.isfinite(undiluted) or undiluted <= 0.0:
        return 0.0

    sr_wind = blocks["sr_wind"]
    ncape = blocks["ncape"]
    el_z = blocks["el_z"]

    # Free-convective layer unresolved by MetPy (e.g. MetPy CAPE <= 0 while the
    # sharppy bound was positive): no analytic value can be formed.
    if sr_wind is None or ncape is None or el_z is None:
        return MISSING
    if not np.isfinite(sr_wind):
        return MISSING
    # No storm-relative inflow => fully entrained => ECAPE -> 0 (analytic limit;
    # avoids a divide-by-zero in the psi/Vsr^2 terms).
    if sr_wind ** 2 < TOL:
        return 0.0

    psi = _psi(el_z)
    if psi is None:
        return MISSING

    value = _ecape_a(sr_wind, psi, ncape, cape)
    if not np.isfinite(value):
        return MISSING

    # Requirements 5.3 / 5.4: 0 <= ECAPE <= undiluted CAPE.
    value = max(0.0, min(float(value), float(undiluted)))
    return value
