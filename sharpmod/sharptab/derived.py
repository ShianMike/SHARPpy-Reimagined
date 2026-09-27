"""Composite derived parameters for SharpTab.

Successor home for the multi-term composite indices that combine several
thermodynamic and kinematic quantities into a single forecast parameter. The
first resident is the Derecho Composite Parameter (DCP, Requirement 2).

Design principle (SHARPpy Reimagined design.md, "Design Principles"):

    *Missing data propagates, never crashes.* Every computation returns
    :data:`~sharpmod.sharptab.constants.MISSING` rather than raising when a
    required input is absent/masked or the profile does not span the layer the
    parameter needs.

Derecho Composite Parameter (DCP)
---------------------------------
After Evans & Doswell (2001), *Wea. Forecasting* 16, 329-342, matching the SPC
Mesoanalysis definition::

    DCP = (DCAPE / 980) * (MUCAPE / 2000)
          * (0-6 km bulk shear / 20 kt) * (0-6 km mean wind / 16 kt)

All four terms are derived from the *same* analyzed Profile (Requirement 2.2):

* **DCAPE** and **MUCAPE** come from a parcel ascent. As with
  :mod:`sharpmod.sharptab.ecape`, the parcel routines of the installed
  ``sharppy.sharptab.params`` package are used as the sanctioned oracle -- the
  most-unstable parcel (``parcelx`` ``flag=3``) supplies MUCAPE and
  ``params.dcape`` supplies DCAPE. ``sharppy`` is imported lazily; if it is
  unavailable the function degrades to :data:`MISSING` rather than raising.
* **0-6 km bulk shear** is the magnitude of the vector wind difference between
  the surface and 6 km AGL, ``|V(6 km) - V(sfc)|`` (kt), via
  :func:`sharpmod.sharptab.winds.wind_shear` using the surface pressure and the
  pressure at 6 km AGL (:func:`sharpmod.sharptab.interp.pres_at_hght_agl`).
* **0-6 km mean wind** is the *speed* (kt) of the pressure-weighted mean wind
  vector over the same SFC->6 km AGL layer, via
  :func:`sharpmod.sharptab.winds.mean_wind`.

Contract (Requirements 2.1, 2.2, 2.4, 2.5):

* 2.4 -- if any input term (DCAPE, MUCAPE, 0-6 km shear, 0-6 km mean wind) is
  missing, or the Profile lacks valid wind/height data spanning the SFC->6 km
  AGL layer, return :data:`MISSING`.
* 2.5 -- when every input term is valid and either the computed DCAPE or MUCAPE
  is zero, return exactly ``0.0`` (not :data:`MISSING`).
* The function *never raises*: any unexpected failure degrades to
  :data:`MISSING`.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import numpy.ma as ma

from sharpmod.upstream.upstream_warnings import known_sharppy_numerical_warnings

from . import interp
from . import parcels
from . import winds
from .constants import KTS_PER_MS, MISSING, is_missing

__all__ = [
    "dcp",
    "normalized_cape_cin",
    "ehi",
    "vorticity_generation_parameter",
    "wet_bulb_zero_height",
    "large_hail_parameter",
    "hail_possibility_index",
    "peskov_index",
    "mcs_index",
    "left_supercell_composite",
    "non_supercell_tornado_parameter",
    "modified_sherbe",
]


# ---------------------------------------------------------------------------
# DCP normalization constants (Evans & Doswell 2001 / SPC Mesoanalysis)
# ---------------------------------------------------------------------------
_DCAPE_NORM = 980.0     # J/kg
_MUCAPE_NORM = 2000.0   # J/kg
_SHEAR_NORM = 20.0      # kt (0-6 km bulk shear)
_MNWIND_NORM = 16.0     # kt (0-6 km mean wind speed)

_SFC_TOP_AGL = 6000.0   # SFC->6 km AGL layer top
_VGP_TOP_AGL = 4000.0   # SFC->4 km AGL layer top for VGP shear
_SFC_1500M_AGL = 1500.0
_ORACLE_CACHE_MISS = object()


# ---------------------------------------------------------------------------
# EHI normalization constant (Hart & Korotky / SPC Energy Helicity Index)
# ---------------------------------------------------------------------------
_EHI_NORM = 160000.0    # (CAPE * SRH) / 160000, unitless


# ---------------------------------------------------------------------------
# Field access helpers (mirror sharpmod.sharptab.ecape)
# ---------------------------------------------------------------------------

def _get_field(prof, name):
    """Return ``prof.<name>`` as a masked float array, or ``None`` if absent."""
    arr = getattr(prof, name, None)
    if arr is None:
        return None
    return ma.masked_invalid(ma.asanyarray(arr, dtype=float))


def _has_masked(*arrays) -> bool:
    """Return ``True`` if any of the given masked arrays is absent or has a
    masked entry."""
    for arr in arrays:
        if arr is None:
            return True
        if ma.getmaskarray(arr).any():
            return True
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
# DCAPE / MUCAPE via the installed sharppy oracle
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 0-6 km kinematic terms (same Profile)
# ---------------------------------------------------------------------------

def _shear_0_6km(prof):
    """Return the SFC->6 km AGL bulk shear magnitude ``|V(6km) - V(sfc)|`` (kt).

    Returns :data:`MISSING` when winds are absent or the profile does not span
    the SFC->6 km AGL layer.
    """
    psfc = _sfc_pres(prof)
    ptop = interp.pres_at_hght_agl(prof, _SFC_TOP_AGL)
    if is_missing(psfc) or is_missing(ptop):
        return MISSING
    du, dv = winds.wind_shear(prof, psfc, ptop)
    if is_missing(du) or is_missing(dv):
        return MISSING
    return float(winds.mag(du, dv))


def _mean_wind_0_6km(prof):
    """Return the SFC->6 km AGL pressure-weighted mean wind *speed* (kt).

    Returns :data:`MISSING` when winds are absent or the profile does not span
    the SFC->6 km AGL layer.
    """
    psfc = _sfc_pres(prof)
    ptop = interp.pres_at_hght_agl(prof, _SFC_TOP_AGL)
    if is_missing(psfc) or is_missing(ptop):
        return MISSING
    mnu, mnv = winds.mean_wind(prof, psfc, ptop)
    if is_missing(mnu) or is_missing(mnv):
        return MISSING
    return float(winds.mag(mnu, mnv))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------





# ===========================================================================
# Normalized CAPE / CIN (NCAPE / NCIN) -- Blanchard (1998) -- task 7.4
# ===========================================================================
#
# After Blanchard (1998), "Assessing the vertical distribution of convective
# available potential energy," *Wea. Forecasting* 13, 870-877:
#
#   NCAPE = MUCAPE / (depth of the buoyant layer, m)
#           buoyant-layer base = LFC, top = EL      (Requirement 4.1)
#   NCIN  = CIN   / (depth of the inhibiting layer, m)
#           inhibiting-layer base = MU-parcel start level, top = LFC (Req 4.2)
#
# MUCAPE, CIN, the LFC/EL heights, and the MU-parcel starting level all come
# from the *same* most-unstable parcel ascent (``parcelx`` ``flag=3``) so the two
# normalizations are internally consistent (Requirement 4.3). As with
# :func:`dcp` and :mod:`sharpmod.sharptab.ecape`, the installed ``sharppy``
# package is the sanctioned parcel-ascent oracle and is imported lazily; if it is
# unavailable both variants degrade to :data:`MISSING` rather than raising.
#
# Division-by-zero / degenerate-layer guards (Requirements 4.4, 4.5): each
# variant returns :data:`MISSING` when its layer depth is <= 0 or undefined, or
# when its buoyancy term (MUCAPE / CIN) is missing -- and it does so
# *independently*, so a missing NCAPE never masks a computable NCIN and vice
# versa.








# ===========================================================================
# Energy Helicity Index (EHI) -- Hart & Korotky / SPC -- task 7.4
# ===========================================================================
#
#   EHI = (CAPE * SRH_layer) / 160000                (Requirements 18.1, 18.2)
#
# The CAPE term is the surface-based CAPE from a surface parcel ascent
# (``parcelx`` ``flag=1``) and the SRH term is the storm-relative helicity over
# the requested SFC-> ``top`` AGL layer, evaluated with the *shared* Bunkers
# right-mover storm motion (:func:`sharpmod.sharptab.winds.storm_motion`) so the
# two EHI variants use an identical storm motion. Both terms are drawn from the
# same analyzed ``prof`` (Requirement 18.3).
#
# The two variants (0-1 km and 0-3 km) are computed by separate calls, so a
# missing term for one layer yields :data:`MISSING` for that variant only,
# without affecting the other (Requirement 18.5).










# ---------------------------------------------------------------------------
# Shared column preparation (mask-guarded plain-float profile arrays)
# ---------------------------------------------------------------------------

def _profile_columns(prof):
    """Return ``(pres, hght, tmpc, dwpc, wdir, wspd)`` as float arrays with
    masked levels carried as the ``-9999`` sentinel (layer-scoped masking).

    Winds are taken from ``wdir`` / ``wspd`` when present, otherwise derived from
    ``u`` / ``v`` components. Rather than rejecting the whole profile when *any*
    single level is masked, masked levels are handed to the downstream
    ``sharppy`` parcel-ascent / kinematic oracle as its ``-9999`` missing
    sentinel: the oracle masks them and resolves each parameter from the valid
    levels spanning that parameter's required layer, so a missing datum outside
    that layer (e.g. a missing top-of-sounding wind) no longer disqualifies the
    parameter. A parameter is MISSING only when its own required layer cannot be
    resolved -- decided by the parameter itself, downstream of this helper.

    Returns ``None`` only when a required column is absent, the columns are
    length-mismatched, or fewer than three levels have all of ``pres`` / ``hght``
    / ``tmpc`` valid (too little data to lift a parcel at all).
    """
    pres = _get_field(prof, "pres")
    hght = _get_field(prof, "hght")
    tmpc = _get_field(prof, "tmpc")
    dwpc = _get_field(prof, "dwpc")

    wdir = _get_field(prof, "wdir")
    wspd = _get_field(prof, "wspd")
    u_kt = _get_field(prof, "u")
    v_kt = _get_field(prof, "v")
    if (wdir is None or wspd is None) and (u_kt is not None and v_kt is not None):
        wspd = ma.sqrt(u_kt ** 2 + v_kt ** 2)
        wdir = (270.0 - ma.degrees(ma.arctan2(v_kt, u_kt))) % 360.0

    if pres is None or hght is None or tmpc is None or dwpc is None \
            or wdir is None or wspd is None:
        return None

    n = int(pres.size)
    if n < 3 or not (hght.size == tmpc.size == dwpc.size == wdir.size
                     == wspd.size == n):
        return None

    # Layer-scoped: drop levels lacking a usable vertical coordinate (masked
    # pres/hght/tmpc) and require at least three that remain, so a parcel can be
    # lifted at all. On the retained levels a still-masked moisture/wind datum
    # is carried as the -9999 sentinel -- the sharppy oracle masks it and
    # resolves each parameter from the valid levels spanning its required layer.
    core_valid = ~(ma.getmaskarray(pres) | ma.getmaskarray(hght)
                   | ma.getmaskarray(tmpc))
    if int(np.count_nonzero(core_valid)) < 3:
        return None

    def _sub(arr):
        return np.asarray(
            ma.asanyarray(arr)[core_valid].filled(-9999.0), dtype=float)

    return (_sub(pres), _sub(hght), _sub(tmpc), _sub(dwpc),
            _sub(wdir), _sub(wspd))


# ---------------------------------------------------------------------------
# Vorticity Generation Parameter (VGP)
# ---------------------------------------------------------------------------

def vorticity_generation_parameter(prof):
    """Compute the Vorticity Generation Parameter (VGP) for ``prof``.

    VGP is evaluated as ``sqrt(SBCAPE) * (0-4 km bulk shear / 4000 m)``, with
    CAPE in ``m^2/s^2`` and the SFC->4 km shear converted from knots to metres
    per second before dividing by layer depth. The result is commonly displayed
    in sounding diagnostics as a compact severe-storm composite. Missing CAPE,
    wind, or height coverage returns :data:`MISSING`; the function never raises.
    """
    try:
        return _vgp_impl(prof)
    except Exception:
        return MISSING


def _vgp_impl(prof):
    pbot = _sfc_pres(prof)
    ptop = interp.pres_at_hght_agl(prof, _VGP_TOP_AGL)
    if is_missing(pbot) or is_missing(ptop):
        return MISSING

    du, dv = winds.wind_shear(prof, pbot, ptop)
    if is_missing(du) or is_missing(dv):
        return MISSING
    shear_kt = float(winds.mag(du, dv))
    if not np.isfinite(shear_kt):
        return MISSING

    arrays = _profile_columns(prof)
    if arrays is None:
        return MISSING
    sbpcl = parcels.parcel(prof, "surface")
    cape = _sfc_cape(*arrays) if sbpcl is None else sbpcl.cape
    if cape is None or not np.isfinite(cape) or cape < 0.0:
        return MISSING
    if cape == 0.0:
        return 0.0

    shear_s = float(winds.kts2ms(shear_kt)) / _VGP_TOP_AGL
    value = math.sqrt(cape) * shear_s
    if not np.isfinite(value):
        return MISSING
    return float(value)


# ===========================================================================
# Hail / thunderstorm / MCS composite indices -- task 7.6
# ===========================================================================
#
# This block adds four composite indices, all built on top of the *same*
# lazy-``sharppy`` parcel-ascent oracle used by :func:`dcp`, :func:`ehi`, and
# :func:`normalized_cape_cin`: a single ``sharppy`` "default" Profile is created
# from the analyzed columns and augmented with the handful of convective
# attributes the sanctioned SPC/AMS routines expect (``mupcl``,
# ``sfc_6km_shear``, ``lapserate_700_500``, ``srwind``). ``sharppy`` is imported
# lazily; if it is unavailable every index degrades to :data:`MISSING` rather
# than raising.
#
# Each index below is computed *independently* -- masking the inputs of one
# index never masks another (Requirements 6.6, 16.3, 17.3) -- and none of them
# ever raises (Design Principle: "missing data propagates, never crashes").
#
# Pinned reference formulas / ranges / tolerances (see the Parameter Registry in
# :mod:`sharpmod.sharptab.constants`):
#
# * **LRGHAIL** (:func:`large_hail_parameter`) -- the SPC Mesoanalysis Large Hail
#   Parameter (``help_lghl``), whose published formulation is Johnson & Sugden
#   (2014), "Evaluation of Sounding-Derived Thermodynamic and Wind-Related
#   Parameters Associated with Large Hail Events," *E-J. Severe Storms Meteor.*
#   9 (5). Delegated verbatim to the sanctioned oracle ``sharppy.sharptab.params.lhp``.
#   Unitless, physical range [0, 20], tolerance max(1%, 0.05).
#
# * **HPI** (:func:`hail_possibility_index`) -- a *non-severe* hail-sizing index,
#   required by Requirement 6.3 to be a distinct quantity that is NOT defined
#   equal to LRGHAIL or SHIP. Pinned to the classical Fawbush & Miller (1953)
#   / Miller (1972, AWS TR-200) hail-sizing framework, in which surface hail size
#   grows with buoyancy available in the hail-growth zone and shrinks as the
#   melting (wet-bulb-zero) level rises:
#
#       HPI = (HGZ_CAPE / 500) * melt_factor
#       melt_factor = clip(1 - max(0, WBZ_AGL - 3350) / 3350, 0, 1)
#
#   where ``HGZ_CAPE`` is the CAPE integrated over the -10 to -30 degrees C layer
#   (:func:`sharpmod.sharptab.params.layer_cape_isotherm`) and ``WBZ_AGL`` is the
#   wet-bulb-zero height in metres AGL (the classic Fawbush-Miller melting-level
#   hail predictor; 3350 m is the commonly cited WBZ ceiling above which surface
#   hail becomes unlikely). Distinct by construction from the Johnson-Sugden
#   LRGHAIL and the SPC SHIP composites. Physical range [0, 25], tolerance
#   max(1%, 0.05).
#
# * **Peskov index** (:func:`peskov_index`) -- a documented thunderstorm-likelihood
#   composite. NOTE (pinning caveat, permitted by the task/design when a single
#   authoritative published formula cannot be confirmed): an authoritative,
#   independently reproducible published formula for the historical "Peskov"
#   thunderstorm index could not be confirmed from the accessible literature. Per
#   the design's instruction to "document the chosen cited source ... and
#   implement that," the Peskov index is pinned here to a documented
#   instability-energy + mid-level-moisture composite consistent with the
#   instability-index thunderstorm-forecast methodology reviewed in Dmitrieva &
#   Peskov style Russian synoptic practice (cf. the review of middle-troposphere
#   instability indices vs. thunderstorm activity, *Russian Meteorology and
#   Hydrology* 39 (5), 2014), combining the George (1960) K-index, the
#   surface-based CAPE "energy of instability," and the 700 hPa dewpoint
#   depression (mid-level moisture deficit):
#
#       Peskov = K_index + (SBCAPE / 1000) - (DD700 / 5)
#
#   All three terms are derived from the same analyzed Profile (Requirement 16.2).
#   Physical range [-60, 60], tolerance max(1%, 0.1).
#
# * **MCS index** (:func:`mcs_index`) -- the Coniglio et al. (2006), "Evaluation of
#   Maintenance Probability of Mesoscale Convective Systems," *Wea. Forecasting*
#   21, 577-592 logistic-regression **linear predictor** (a distinct exposed
#   attribute from ``sharppy``'s existing ``mmp`` *probability*):
#
#       MCS_index = a0 + a1*max_bulk_shear + a2*lr38 + a3*MUCAPE + a4*mnwind_3_12
#       (a0=13.0, a1=-4.59e-2, a2=-1.16, a3=-6.17e-4, a4=-0.17)
#
#   so that the MCS Maintenance Probability is MMP = 1 / (1 + exp(MCS_index)).
#   Terms: ``max_bulk_shear`` = the maximum bulk shear (m/s) between the lowest
#   1 km and the 6-10 km layer; ``lr38`` = the 3-8 km lapse rate (deg C/km);
#   ``MUCAPE`` = most-unstable CAPE (J/kg); ``mnwind_3_12`` = the 3-12 km mean
#   wind speed (m/s) -- all from the same analyzed Profile (Requirement 17.2).
#   Physical range [-20, 20], tolerance max(1%, 0.1).


# --- HPI (Fawbush-Miller hail-sizing) constants ----------------------------
_HPI_CAPE_NORM = 500.0        # J/kg per HPI unit (hail-growth-zone CAPE scaling)
_HPI_WBZ_CEILING = 3350.0     # m AGL: WBZ melting ceiling (Miller 1972, AWS TR-200)

# --- Peskov thunderstorm-likelihood composite constants --------------------
_PESKOV_CAPE_NORM = 1000.0    # J/kg per index unit (instability-energy scaling)
_PESKOV_DD_NORM = 5.0         # deg C per index unit (700 hPa moisture-deficit scaling)
_PESKOV_DD_PRES = 700.0       # hPa level for the mid-level dewpoint depression

# --- Coniglio et al. (2006) MMP logistic-regression coefficients -----------
_MMP_A0 = 13.0                # unitless
_MMP_A1 = -4.59e-2            # per (m/s)
_MMP_A2 = -1.16               # per (deg C/km)
_MMP_A3 = -6.17e-4            # per (J/kg)
_MMP_A4 = -0.17               # per (m/s)

# --- SPC LSCP / NSTP / Modified SHERBE constants ---------------------------
_LSCP_MUCAPE_NORM = 1000.0    # J/kg
_LSCP_ESRH_NORM = 50.0        # m2/s2
_LSCP_EBWD_NORM = 20.0        # m/s
_LSCP_MUCIN_NORM = -40.0      # J/kg; term is 1.0 when muCIN > -40

_NSTP_LR_NORM = 9.0           # C/km
_NSTP_MLCAPE3_NORM = 100.0    # J/kg
_NSTP_MLCIN_BASE = 225.0      # J/kg
_NSTP_MLCIN_NORM = 200.0      # J/kg
_NSTP_SHEAR_BASE_MS = 18.0    # m/s
_NSTP_SHEAR_NORM_MS = 5.0     # m/s
_NSTP_VORT_NORM_S = 8.0e-5    # s^-1

_MOSHE_LLLR_OFFSET = 4.0      # K/km
_MOSHE_LLLR_NORM = 4.0        # K2/km2
_MOSHE_SHEAR_OFFSET_MS = 8.0  # m/s
_MOSHE_SHEAR_NORM_MS = 10.0   # m/s
_MOSHE_TEVV_OFFSET = 10.0     # K Pa km^-1 s^-1
_MOSHE_TEVV_NORM = 9.0        # K Pa km^-1 s^-1
_MOSHE_LAYER_DEPTH_KM = 2.0
_MOSHE_LAYER_TOPS_AGL = tuple(np.arange(2000.0, 6000.0 + 0.1, 500.0))




def _finite_or_none(value):
    """Return ``float(value)`` when it is present and finite, else ``None``."""
    if value is None or is_missing(value):
        return None
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None
    return fval if np.isfinite(fval) else None


def _metadata_value(prof, *names):
    """Return the first finite value found on ``prof`` or ``prof.meta``."""
    for name in names:
        value = _finite_or_none(getattr(prof, name, None))
        if value is not None:
            return value
    meta = getattr(prof, "meta", None)
    if isinstance(meta, dict):
        for name in names:
            value = _finite_or_none(meta.get(name))
            if value is not None:
                return value
    return None


def _metadata_raw(prof, *names):
    """Return the first non-missing raw metadata value."""
    for name in names:
        value = getattr(prof, name, None)
        if value is not None and not is_missing(value):
            return value
    meta = getattr(prof, "meta", None)
    if isinstance(meta, dict):
        for name in names:
            value = meta.get(name)
            if value is not None and not is_missing(value):
                return value
    return None




def _omeg_column_for_oracle(prof, target_len):
    """Return an omega column aligned to the core profile, or ``None``."""
    omeg = _get_field(prof, "omeg")
    if omeg is None:
        return None
    omeg = ma.masked_where(ma.asarray(omeg, dtype=float) <= -9000.0, omeg)
    if int(omeg.size) != int(target_len):
        # If masked core levels were dropped in _profile_columns, do not guess
        # the alignment for a vertical-motion-sensitive composite.
        return None
    if ma.getmaskarray(omeg).all():
        return None
    return np.asarray(ma.asarray(omeg).filled(-9999.0), dtype=float)


def _vector_mag_ms(vector):
    """Magnitude of a kt vector converted to m/s, or ``None``."""
    if not isinstance(vector, (tuple, list, np.ndarray)) or len(vector) < 2:
        return None
    u = _finite_or_none(vector[0])
    v = _finite_or_none(vector[1])
    if u is None or v is None:
        return None
    return float(np.hypot(u, v) / KTS_PER_MS)


def _bulk_shear_ms(prof, top_agl):
    """SFC->``top_agl`` bulk shear magnitude in m/s."""
    pbot = _sfc_pres(prof)
    ptop = interp.pres_at_hght_agl(prof, top_agl)
    if is_missing(pbot) or is_missing(ptop):
        return MISSING
    du, dv = winds.wind_shear(prof, pbot, ptop)
    if is_missing(du) or is_missing(dv):
        return MISSING
    value = float(winds.mag(du, dv) / KTS_PER_MS)
    return value if np.isfinite(value) else MISSING


def _surface_relative_vorticity(prof):
    """Surface relative vertical vorticity in s^-1 from optional source data."""
    value = _metadata_value(
        prof,
        "sfc_relative_vorticity",
        "surface_relative_vorticity",
        "sfc_vorticity",
        "surface_vorticity",
        "vorticity",
    )
    if value is None:
        return None
    # Some gridded products expose this in 10^-5 s^-1 units. Treat small values
    # as SI s^-1 and larger compact values as 10^-5 s^-1.
    if abs(value) >= 1.0e-2:
        return value * 1.0e-5
    return value


def _thetae_at_hght_agl(prof, h_agl):
    """Equivalent potential temperature at ``h_agl`` metres AGL."""
    p = interp.pres_at_hght_agl(prof, h_agl)
    t = interp.temp_at_hght_agl(prof, h_agl)
    td = interp.dwpt_at_hght_agl(prof, h_agl)
    if is_missing(p) or is_missing(t) or is_missing(td):
        return None
    try:
        from sharppy.sharptab import thermo as sp_thermo
        value = sp_thermo.thetae(float(p), float(t), float(td))
    except Exception:
        return None
    return _finite_or_none(value)


def _omeg_at_hght_agl(prof, h_agl):
    """Omega (Pa/s) at ``h_agl`` metres AGL."""
    omeg = _get_field(prof, "omeg")
    if omeg is None:
        return None
    omeg = ma.masked_where(ma.asarray(omeg, dtype=float) <= -9000.0, omeg)
    if ma.getmaskarray(omeg).all():
        return None
    value = interp.interp_hght_agl(prof, omeg, h_agl)
    return _finite_or_none(value)


def _max_thetae_vertical_velocity(prof):
    """Return MOSHE MAXTEVV in K Pa km^-1 s^-1, or ``None``."""
    values = []
    for top in _MOSHE_LAYER_TOPS_AGL:
        bottom = top - (_MOSHE_LAYER_DEPTH_KM * 1000.0)
        thetae_bottom = _thetae_at_hght_agl(prof, bottom)
        thetae_top = _thetae_at_hght_agl(prof, top)
        omega_top = _omeg_at_hght_agl(prof, top)
        if thetae_bottom is None or thetae_top is None or omega_top is None:
            continue
        thetae_decrease = (thetae_bottom - thetae_top) / _MOSHE_LAYER_DEPTH_KM
        upward_motion = -omega_top
        value = thetae_decrease * upward_motion
        if np.isfinite(value):
            values.append(float(value))
    if not values:
        return None
    return max(values)


def wet_bulb_zero_height(prof):
    """Return the wet-bulb-zero height in metres AGL for ``prof``.

    The direct path uses an optional ``prof.wetbulb`` column when a decoder
    supplied one. Most inputs do not carry that column, so the fallback builds
    the same SHARPpy oracle profile used by the hail composites and asks it for
    the first wet-bulb 0 C level. Missing inputs return :data:`MISSING`; the
    function never raises.
    """
    try:
        return _wet_bulb_zero_height_impl(prof)
    except Exception:
        return MISSING


def _wet_bulb_zero_height_impl(prof):
    if getattr(prof, "wetbulb", None) is not None:
        h_msl = interp.hght_at_isotherm(prof, 0, wetbulb=True)
        h_agl = _finite_or_none(interp.to_agl(prof, h_msl))
        if h_agl is not None:
            return h_agl

    sp = _oracle_profile(prof)
    if sp is None:
        return MISSING

    try:
        from sharppy.sharptab import params as sp_params
        from sharppy.sharptab import interp as sp_interp
        wbz_pres = sp_params.temp_lvl(sp, 0, wetbulb=True)
        wbz_agl = _finite_or_none(
            sp_interp.to_agl(sp, sp_interp.hght(sp, wbz_pres))
        )
    except Exception:
        return MISSING

    return MISSING if wbz_agl is None else float(wbz_agl)


# ---------------------------------------------------------------------------
# LRGHAIL -- SPC Large Hail Parameter (Johnson & Sugden 2014 / help_lghl)
# ---------------------------------------------------------------------------

def large_hail_parameter(prof):
    """Compute the Large Hail Parameter (LRGHAIL, unitless) for ``prof``.

    Delegates to the sanctioned oracle ``sharppy.sharptab.params.lhp`` (SPC
    Mesoanalysis Large Hail Parameter, Johnson & Sugden 2014). See the module
    "pinned reference formulas" note for the citation.

    Returns
    -------
    float or MISSING
        The unitless LRGHAIL value, or
        :data:`~sharpmod.sharptab.constants.MISSING` when any required input is
        missing/masked or the oracle result is masked/non-finite (Requirement
        6.6). Never raises.
    """
    try:
        return _large_hail_parameter_impl(prof)
    except Exception:
        return MISSING


def _large_hail_parameter_impl(prof):
    sp = _oracle_profile(prof)
    if sp is None:
        return MISSING
    from sharppy.sharptab import params as sp_params
    value = _finite_or_none(sp_params.lhp(sp))
    if value is None:
        return MISSING
    return float(value)


# ---------------------------------------------------------------------------
# HPI -- Hail Possibility Index (non-severe hail sizing; Fawbush-Miller / WBZ)
# ---------------------------------------------------------------------------

def hail_possibility_index(prof):
    """Compute the Hail Possibility Index (HPI, unitless) for ``prof``.

    A *non-severe* hail-sizing index, distinct by construction from LRGHAIL and
    SHIP (Requirement 6.3). Pinned to the Fawbush & Miller (1953) / Miller (1972)
    hail-sizing framework::

        HPI = (HGZ_CAPE / 500) * melt_factor
        melt_factor = clip(1 - max(0, WBZ_AGL - 3350) / 3350, 0, 1)

    where ``HGZ_CAPE`` is the -10 to -30 degrees C hail-growth-zone CAPE and
    ``WBZ_AGL`` is the wet-bulb-zero height (m AGL). See the module note.

    Returns
    -------
    float or MISSING
        The unitless HPI value, or
        :data:`~sharpmod.sharptab.constants.MISSING` when the hail-growth-zone
        CAPE is undefined (the profile does not span the -10/-30 degrees C layer)
        or the wet-bulb-zero level cannot be resolved, or when any required input
        is missing/masked (Requirement 6.6). Never raises.
    """
    try:
        return _hail_possibility_index_impl(prof)
    except Exception:
        return MISSING


def _hail_possibility_index_impl(prof):
    from . import params as sm_params

    # Hail-growth-zone CAPE (-10 -> -30 degrees C). Returns MISSING when the
    # profile does not span both isotherms.
    hgz_cape = _finite_or_none(sm_params.layer_cape_isotherm(prof, -10, -30))
    if hgz_cape is None:
        return MISSING

    # Wet-bulb-zero height (m AGL): the melting level (Fawbush-Miller predictor).
    wbz_agl = _finite_or_none(wet_bulb_zero_height(prof))
    if wbz_agl is None:
        return MISSING

    melt_factor = 1.0 - max(0.0, wbz_agl - _HPI_WBZ_CEILING) / _HPI_WBZ_CEILING
    if melt_factor < 0.0:
        melt_factor = 0.0
    elif melt_factor > 1.0:
        melt_factor = 1.0

    value = (hgz_cape / _HPI_CAPE_NORM) * melt_factor
    if not np.isfinite(value):
        return MISSING
    return float(value)


# ---------------------------------------------------------------------------
# Peskov index -- documented thunderstorm-likelihood composite
# ---------------------------------------------------------------------------

def peskov_index(prof):
    """Compute the Peskov thunderstorm-likelihood index for ``prof``.

    Documented instability-energy + mid-level-moisture composite (see the module
    note for the pinning caveat and citation)::

        Peskov = K_index + (SBCAPE / 1000) - (DD700 / 5)

    with the George (1960) K-index, the surface-based CAPE, and the 700 hPa
    dewpoint depression all drawn from the same analyzed Profile (Requirement
    16.2).

    Returns
    -------
    float or MISSING
        The Peskov index, or
        :data:`~sharpmod.sharptab.constants.MISSING` when the K-index, the
        surface-based CAPE, or the 700 hPa temperature/dewpoint (mid-level
        moisture deficit) is missing/masked -- e.g. a shallow profile that does
        not reach 700 hPa (Requirement 16.3). Never raises.
    """
    try:
        return _peskov_index_impl(prof)
    except Exception:
        return MISSING


def _peskov_index_impl(prof):
    sp = _oracle_profile(prof)
    if sp is None:
        return MISSING

    from sharppy.sharptab import params as sp_params
    from sharppy.sharptab import interp as sp_interp

    # George (1960) K-index (thunderstorm-likelihood thermodynamic index).
    kidx = _finite_or_none(sp_params.k_index(sp))
    if kidx is None:
        return MISSING

    # Surface-based CAPE ("energy of instability").
    sbpcl = parcels.parcel(prof, "surface")
    if sbpcl is None:
        sbcape = _finite_or_none(
            getattr(sp_params.parcelx(sp, flag=1), "bplus", None),
        )
    else:
        sbcape = _finite_or_none(sbpcl.cape)
    if sbcape is None:
        return MISSING

    # 700 hPa dewpoint depression (mid-level moisture deficit).
    t700 = _finite_or_none(sp_interp.temp(sp, _PESKOV_DD_PRES))
    td700 = _finite_or_none(sp_interp.dwpt(sp, _PESKOV_DD_PRES))
    if t700 is None or td700 is None:
        return MISSING
    dd700 = t700 - td700

    value = kidx + (sbcape / _PESKOV_CAPE_NORM) - (dd700 / _PESKOV_DD_NORM)
    if not np.isfinite(value):
        return MISSING
    return float(value)


# ---------------------------------------------------------------------------
# MCS index -- Coniglio et al. (2006) MMP logistic linear predictor
# ---------------------------------------------------------------------------

def mcs_index(prof):
    """Compute the MCS Maintenance Index for ``prof``.

    The Coniglio et al. (2006) MMP logistic-regression **linear predictor** (a
    distinct exposed attribute from ``sharppy``'s ``mmp`` *probability*)::

        MCS_index = a0 + a1*max_bulk_shear + a2*lr38 + a3*MUCAPE + a4*mnwind_3_12
        MMP = 1 / (1 + exp(MCS_index))

    All terms are drawn from the same analyzed Profile (Requirement 17.2). See
    the module note for coefficients and the citation.

    Returns
    -------
    float or MISSING
        The MCS index, or
        :data:`~sharpmod.sharptab.constants.MISSING` when MUCAPE, the 3-8 km
        lapse rate, the 3-12 km mean wind, or the low-level / 6-10 km levels
        required for the maximum bulk shear are missing/masked -- e.g. a profile
        that does not reach the 6-10 km band (Requirement 17.3). Never raises.
    """
    try:
        return _mcs_index_impl(prof)
    except Exception:
        return MISSING


def _mcs_index_impl(prof):
    sp = _oracle_profile(prof)
    if sp is None:
        return MISSING
    value = _mmp_linear_predictor(sp)
    if value is None or not np.isfinite(value):
        return MISSING
    return float(value)




# ---------------------------------------------------------------------------
# LSCP / NSTP / Modified SHERBE (SPC mesoanalysis composites)
# ---------------------------------------------------------------------------

def left_supercell_composite(prof):
    """Compute the Left-moving Supercell Composite Parameter (LSCP)."""
    try:
        return _left_supercell_composite_impl(prof)
    except Exception:
        return MISSING




def non_supercell_tornado_parameter(prof):
    """Compute the Non-Supercell Tornado Parameter (NSTP)."""
    try:
        return _non_supercell_tornado_parameter_impl(prof)
    except Exception:
        return MISSING




def modified_sherbe(prof):
    """Compute the SPC Modified SHERBE / MOSHE composite."""
    try:
        return _modified_sherbe_impl(prof)
    except Exception:
        return MISSING


from sharpmod.sharptab.derived_thermo import (  # noqa: E402
    _dcape_mucape,
    dcp,
    _dcp_impl,
    _mu_parcel_terms,
    normalized_cape_cin,
    _normalized_cape_cin_impl,
    _sfc_cape,
    _layer_top_agl,
    ehi,
    _ehi_impl
)


from sharpmod.sharptab.derived_indices import (  # noqa: E402
    _oracle_profile,
    _convective_oracle_profile,
    _mmp_linear_predictor,
    _left_supercell_composite_impl,
    _non_supercell_tornado_parameter_impl,
    _modified_sherbe_impl
)
