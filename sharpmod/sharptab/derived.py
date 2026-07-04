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

import numpy as np
import numpy.ma as ma

from . import interp
from . import winds
from .constants import MISSING, is_missing

__all__ = [
    "dcp",
    "normalized_cape_cin",
    "ehi",
    "large_hail_parameter",
    "hail_possibility_index",
    "peskov_index",
    "mcs_index",
]


# ---------------------------------------------------------------------------
# DCP normalization constants (Evans & Doswell 2001 / SPC Mesoanalysis)
# ---------------------------------------------------------------------------
_DCAPE_NORM = 980.0     # J/kg
_MUCAPE_NORM = 2000.0   # J/kg
_SHEAR_NORM = 20.0      # kt (0-6 km bulk shear)
_MNWIND_NORM = 16.0     # kt (0-6 km mean wind speed)

_SFC_TOP_AGL = 6000.0   # SFC->6 km AGL layer top


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

def _dcape_mucape(pres, hght, tmpc, dwpc, wdir, wspd):
    """Return ``(dcape, mucape)`` (J/kg) for the profile via ``sharppy``.

    DCAPE comes from ``sharppy.sharptab.params.dcape`` and MUCAPE from the
    most-unstable parcel ascent (``parcelx`` ``flag=3``), both computed from the
    *same* profile so the DCP terms are internally consistent (Requirement 2.2).

    Returns ``None`` on any failure or if either quantity is masked/non-finite.
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return None

    try:
        prof = sp_profile.create_profile(
            profile="default",
            pres=np.asarray(pres, dtype=float),
            hght=np.asarray(hght, dtype=float),
            tmpc=np.asarray(tmpc, dtype=float),
            dwpc=np.asarray(dwpc, dtype=float),
            wdir=np.asarray(wdir, dtype=float),
            wspd=np.asarray(wspd, dtype=float),
            missing=-9999.0,
            strictQC=False,
        )

        mupcl = sp_params.parcelx(prof, flag=3)  # most-unstable parcel
        mucape = getattr(mupcl, "bplus", None)
        if mucape is None or is_missing(mucape) or not np.isfinite(mucape):
            return None

        dres = sp_params.dcape(prof)
        # sharppy's dcape returns (dcape, ttrace, ptrace); accept a bare scalar too.
        dcape = dres[0] if isinstance(dres, (tuple, list)) else dres
        if dcape is None or is_missing(dcape) or not np.isfinite(dcape):
            return None

        return float(dcape), float(mucape)
    except Exception:
        return None


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

def dcp(prof):
    """Compute the Derecho Composite Parameter (DCP, unitless) for ``prof``.

    ``DCP = (DCAPE/980)*(MUCAPE/2000)*(shear_0_6km/20)*(mean_wind_0_6km/16)``,
    all four terms drawn from the same analyzed ``prof`` (Evans & Doswell 2001).

    Parameters
    ----------
    prof:
        Any profile-like object exposing the reported-level arrays ``pres``
        (hPa), ``hght`` (m MSL), ``tmpc`` (deg C), ``dwpc`` (deg C), ``wdir``
        (deg), ``wspd`` (kt) -- and, for the kinematic terms, ``u`` / ``v`` (kt)
        -- optionally with a surface index ``sfc``.

    Returns
    -------
    float or MISSING
        The unitless DCP value; exactly ``0.0`` when every input term is valid
        and the computed DCAPE or MUCAPE is zero (Requirement 2.5); and
        :data:`~sharpmod.sharptab.constants.MISSING` when any input term is
        missing/masked or the profile lacks valid wind/height data spanning the
        SFC->6 km AGL layer (Requirement 2.4). Never raises.
    """
    try:
        return _dcp_impl(prof)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING


def _dcp_impl(prof):
    # --- 0-6 km kinematic terms (layer-scoped: the interp/winds routines
    # resolve these from the valid levels spanning the SFC->6 km AGL layer and
    # tolerate masked levels elsewhere in the column) -----------------------
    shear06 = _shear_0_6km(prof)
    mnwind06 = _mean_wind_0_6km(prof)
    if is_missing(shear06) or is_missing(mnwind06):
        return MISSING
    if not (np.isfinite(shear06) and np.isfinite(mnwind06)):
        return MISSING

    # --- DCAPE + MUCAPE via the shared, layer-scoped column oracle ----------
    # ``_profile_columns`` carries masked levels as the -9999 sentinel to the
    # sharppy parcel-ascent oracle, so a missing datum outside the parcel path
    # (e.g. a missing top-of-sounding wind) no longer disqualifies DCP.
    arrays = _profile_columns(prof)
    if arrays is None:
        return MISSING
    buoyancy = _dcape_mucape(*arrays)
    if buoyancy is None:
        return MISSING
    dcape, mucape = buoyancy

    # Requirement 2.5: a zero buoyancy factor makes DCP exactly zero (not MISSING).
    if dcape == 0.0 or mucape == 0.0:
        return 0.0

    value = (
        (dcape / _DCAPE_NORM)
        * (mucape / _MUCAPE_NORM)
        * (shear06 / _SHEAR_NORM)
        * (mnwind06 / _MNWIND_NORM)
    )
    if not np.isfinite(value):
        return MISSING
    return float(value)


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


def _mu_parcel_terms(pres, hght, tmpc, dwpc, wdir, wspd):
    """Return most-unstable parcel terms for NCAPE/NCIN via the sharppy oracle.

    Returns a 5-tuple ``(mucape, cin, lfc_agl, el_agl, mu_start_agl)`` where any
    element that ``sharppy`` cannot resolve is ``None`` (heights are metres AGL;
    energies are J/kg with ``cin <= 0``). Returns ``None`` outright only when the
    parcel ascent itself cannot be run (``sharppy`` missing / unexpected error);
    this routine never raises.
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
        from sharppy.sharptab import interp as sp_interp
    except Exception:
        return None

    def _num(value):
        """Coerce a sharppy attribute to a finite float, or ``None``."""
        if value is None or is_missing(value):
            return None
        try:
            fval = float(value)
        except (TypeError, ValueError):
            return None
        return fval if np.isfinite(fval) else None

    try:
        sp_prof = sp_profile.create_profile(
            profile="default",
            pres=np.asarray(pres, dtype=float),
            hght=np.asarray(hght, dtype=float),
            tmpc=np.asarray(tmpc, dtype=float),
            dwpc=np.asarray(dwpc, dtype=float),
            wdir=np.asarray(wdir, dtype=float),
            wspd=np.asarray(wspd, dtype=float),
            missing=-9999.0,
            strictQC=False,
        )
        mupcl = sp_params.parcelx(sp_prof, flag=3)  # most-unstable parcel

        mucape = _num(getattr(mupcl, "bplus", None))
        cin = _num(getattr(mupcl, "bminus", None))
        lfc_agl = _num(getattr(mupcl, "lfchght", None))
        el_agl = _num(getattr(mupcl, "elhght", None))

        # MU-parcel starting level height, converted to metres AGL to match the
        # sharppy LFC/EL convention. ``mupcl.pres`` is the lifted-parcel-level
        # pressure of the most-unstable parcel.
        mu_start_agl = None
        lpl_pres = _num(getattr(mupcl, "pres", None))
        if lpl_pres is not None:
            try:
                mu_start_agl = _num(
                    sp_interp.to_agl(sp_prof, sp_interp.hght(sp_prof, lpl_pres))
                )
            except Exception:
                mu_start_agl = None

        return mucape, cin, lfc_agl, el_agl, mu_start_agl
    except Exception:
        return None


def normalized_cape_cin(prof):
    """Compute the normalized CAPE and CIN ``(ncape, ncin)`` for ``prof``.

    After Blanchard (1998). Both values are in J/kg per metre:

    * ``ncape = MUCAPE / (EL_AGL - LFC_AGL)`` -- the most-unstable CAPE divided by
      the depth of the buoyant layer (LFC -> EL) (Requirements 4.1, 4.3).
    * ``ncin  = CIN / (LFC_AGL - MU_start_AGL)`` -- the convective inhibition
      divided by the depth of the inhibiting layer (MU-parcel start -> LFC)
      (Requirement 4.2). ``CIN <= 0`` so ``ncin <= 0``.

    All terms are drawn from the same most-unstable parcel ascent (Requirement
    4.3).

    Returns
    -------
    tuple
        ``(ncape, ncin)``. Each element is a float, or
        :data:`~sharpmod.sharptab.constants.MISSING` when that variant's buoyancy
        term is missing or its layer depth is ``<= 0`` / undefined -- computed
        independently so one missing variant never masks the other (Requirements
        4.4, 4.5). Both are :data:`MISSING` when the profile lacks the pressure /
        temperature / moisture data needed to run the ascent. Never raises.
    """
    try:
        return _normalized_cape_cin_impl(prof)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING, MISSING


def _normalized_cape_cin_impl(prof):
    arrays = _profile_columns(prof)
    if arrays is None:
        return MISSING, MISSING

    terms = _mu_parcel_terms(*arrays)
    if terms is None:
        return MISSING, MISSING
    mucape, cin, lfc_agl, el_agl, mu_start_agl = terms

    # --- NCAPE: MUCAPE / (EL - LFC) ----------------------------------------
    ncape = MISSING
    if mucape is not None and lfc_agl is not None and el_agl is not None:
        depth = el_agl - lfc_agl
        if depth > 0.0:                     # Req 4.4: guard non-positive depth
            value = mucape / depth
            if np.isfinite(value):
                ncape = float(value)

    # --- NCIN: CIN / (LFC - MU start) --------------------------------------
    ncin = MISSING
    if cin is not None and lfc_agl is not None and mu_start_agl is not None:
        depth = lfc_agl - mu_start_agl
        if depth > 0.0:                     # Req 4.5: guard non-positive depth
            value = cin / depth
            if np.isfinite(value):
                ncin = float(value)

    return ncape, ncin


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


def _sfc_cape(pres, hght, tmpc, dwpc, wdir, wspd):
    """Return surface-based CAPE (J/kg) via ``sharppy`` (``parcelx`` ``flag=1``).

    Returns ``None`` when ``sharppy`` is unavailable or the CAPE is masked /
    non-finite; never raises.
    """
    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
    except Exception:
        return None

    try:
        sp_prof = sp_profile.create_profile(
            profile="default",
            pres=np.asarray(pres, dtype=float),
            hght=np.asarray(hght, dtype=float),
            tmpc=np.asarray(tmpc, dtype=float),
            dwpc=np.asarray(dwpc, dtype=float),
            wdir=np.asarray(wdir, dtype=float),
            wspd=np.asarray(wspd, dtype=float),
            missing=-9999.0,
            strictQC=False,
        )
        sbpcl = sp_params.parcelx(sp_prof, flag=1)  # surface-based parcel
        cape = getattr(sbpcl, "bplus", None)
        if cape is None or is_missing(cape) or not np.isfinite(cape):
            return None
        return float(cape)
    except Exception:
        return None


def _layer_top_agl(layer):
    """Resolve the ``ehi`` ``layer`` argument to a SFC-> ``top`` m AGL layer.

    Accepts the layer top height in metres AGL (e.g. ``1000`` or ``3000``), a
    ``(bottom, top)`` pair in metres AGL, or the strings ``"0-1km"`` / ``"0-3km"``
    (and ``"1km"`` / ``"3km"``). Returns ``(bottom, top)`` in metres AGL, or
    ``None`` when the argument cannot be interpreted.
    """
    if isinstance(layer, str):
        key = layer.strip().lower().replace(" ", "")
        mapping = {
            "0-1km": (0.0, 1000.0), "1km": (0.0, 1000.0), "01km": (0.0, 1000.0),
            "0-3km": (0.0, 3000.0), "3km": (0.0, 3000.0), "03km": (0.0, 3000.0),
        }
        return mapping.get(key)
    if isinstance(layer, (tuple, list)) and len(layer) == 2:
        try:
            bottom, top = float(layer[0]), float(layer[1])
        except (TypeError, ValueError):
            return None
        if np.isfinite(bottom) and np.isfinite(top):
            return bottom, top
        return None
    try:
        top = float(layer)
    except (TypeError, ValueError):
        return None
    if np.isfinite(top):
        return 0.0, top
    return None


def ehi(prof, layer):
    """Compute the Energy Helicity Index (EHI, unitless) for ``prof`` over ``layer``.

    ``EHI = (SBCAPE * SRH_layer) / 160000`` (Hart & Korotky / SPC), with the
    surface-based CAPE and the layer storm-relative helicity drawn from the same
    ``prof`` (Requirement 18.3). The SRH uses the shared Bunkers right-mover
    storm motion so the 0-1 km and 0-3 km variants share an identical motion.

    Parameters
    ----------
    prof:
        Profile-like object exposing ``pres`` / ``hght`` / ``tmpc`` / ``dwpc``
        and wind (``wdir`` / ``wspd`` or ``u`` / ``v``).
    layer:
        The SRH layer. Accepts the layer-top height in metres AGL (``1000`` for
        the 0-1 km variant, ``3000`` for the 0-3 km variant), a ``(bottom, top)``
        metres-AGL pair, or the strings ``"0-1km"`` / ``"0-3km"``.

    Returns
    -------
    float or MISSING
        The unitless EHI value, or
        :data:`~sharpmod.sharptab.constants.MISSING` when the CAPE term or the
        SRH term for this layer is missing/masked, when the storm motion cannot
        be resolved, or when ``layer`` is uninterpretable (Requirement 18.5).
        Never raises.
    """
    try:
        return _ehi_impl(prof, layer)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING


def _ehi_impl(prof, layer):
    bounds = _layer_top_agl(layer)
    if bounds is None:
        return MISSING
    bottom, top = bounds

    # --- storm-relative helicity over the layer (shared Bunkers motion) -----
    rstu, rstv, _lstu, _lstv = winds.storm_motion(prof)
    if is_missing(rstu) or is_missing(rstv):
        return MISSING
    total, _phel, _nhel = winds.helicity(prof, bottom, top, stu=rstu, stv=rstv)
    if is_missing(total) or not np.isfinite(total):
        return MISSING
    srh = float(total)

    # --- surface-based CAPE (same Profile) ----------------------------------
    arrays = _profile_columns(prof)
    if arrays is None:
        return MISSING
    cape = _sfc_cape(*arrays)
    if cape is None or not np.isfinite(cape):
        return MISSING

    value = (cape * srh) / _EHI_NORM
    if not np.isfinite(value):
        return MISSING
    return float(value)


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


def _oracle_profile(prof):
    """Build the shared ``sharppy`` "default" Profile oracle for ``prof``.

    Returns a ``sharppy`` profile augmented with the convective attributes the
    SPC/AMS routines (``lhp``, ``ship``, ``mmp``, ...) read -- ``mupcl``,
    ``sfc_6km_shear``, ``lapserate_700_500``, ``srwind`` -- or ``None`` when the
    analyzed columns are missing/masked (via :func:`_profile_columns`) or when
    ``sharppy`` is unavailable / the ascent cannot be run. Never raises.
    """
    arrays = _profile_columns(prof)
    if arrays is None:
        return None
    pres, hght, tmpc, dwpc, wdir, wspd = arrays

    try:
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import params as sp_params
        from sharppy.sharptab import winds as sp_winds
        from sharppy.sharptab import interp as sp_interp
    except Exception:
        return None

    try:
        sp = sp_profile.create_profile(
            profile="default",
            pres=pres, hght=hght, tmpc=tmpc, dwpc=dwpc, wdir=wdir, wspd=wspd,
            missing=-9999.0, strictQC=False,
        )
        # Augment with the attributes the convective routines expect.
        sp.mupcl = sp_params.parcelx(sp, flag=3)  # most-unstable parcel
        sfcp = sp.pres[sp.sfc]
        p6km = sp_interp.pres(sp, sp_interp.to_msl(sp, 6000.0))
        sp.sfc_6km_shear = sp_winds.wind_shear(sp, pbot=sfcp, ptop=p6km)
        sp.lapserate_700_500 = sp_params.lapse_rate(sp, 700.0, 500.0, pres=True)
        sp.srwind = sp_winds.non_parcel_bunkers_motion(sp)
        return sp
    except Exception:
        return None


def _finite_or_none(value):
    """Return ``float(value)`` when it is present and finite, else ``None``."""
    if value is None or is_missing(value):
        return None
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None
    return fval if np.isfinite(fval) else None


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
    sp = _oracle_profile(prof)
    if sp is None:
        return MISSING

    from sharppy.sharptab import params as sp_params
    from sharppy.sharptab import interp as sp_interp
    from . import params as sm_params

    # Hail-growth-zone CAPE (-10 -> -30 degrees C). Returns MISSING when the
    # profile does not span both isotherms.
    hgz_cape = _finite_or_none(sm_params.layer_cape_isotherm(prof, -10, -30))
    if hgz_cape is None:
        return MISSING

    # Wet-bulb-zero height (m AGL): the melting level (Fawbush-Miller predictor).
    try:
        wbz_pres = sp_params.temp_lvl(sp, 0, wetbulb=True)
        wbz_agl = _finite_or_none(
            sp_interp.to_agl(sp, sp_interp.hght(sp, wbz_pres))
        )
    except Exception:
        return MISSING
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
    sbcape = _finite_or_none(getattr(sp_params.parcelx(sp, flag=1), "bplus", None))
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


def _mmp_linear_predictor(sp):
    """Return the Coniglio et al. (2006) MMP logistic linear predictor, or ``None``.

    Computes the same input terms as ``sharppy.sharptab.params.mmp`` (MUCAPE, the
    maximum bulk shear between the lowest 1 km and the 6-10 km layer, the 3-8 km
    lapse rate, and the 3-12 km mean wind) and combines them with the published
    regression coefficients, returning the linear predictor so that
    ``MMP = 1 / (1 + exp(value))``. Returns ``None`` when any required term is
    missing/masked or the profile lacks the low-level or 6-10 km levels.
    """
    from sharppy.sharptab import interp as sp_interp
    from sharppy.sharptab import winds as sp_winds
    from sharppy.sharptab import params as sp_params
    from sharppy.sharptab import utils as sp_utils

    mucape = _finite_or_none(getattr(sp.mupcl, "bplus", None))
    if mucape is None:
        return None

    agl = sp_interp.to_agl(sp, sp.hght)
    lowest_idx = np.where(np.asarray(agl) <= 1000.0)[0]
    highest_idx = np.where((np.asarray(agl) >= 6000.0) & (np.asarray(agl) < 10000.0))[0]
    if len(lowest_idx) == 0 or len(highest_idx) == 0:
        return None

    pbots = np.atleast_1d(sp_interp.pres(sp, sp.hght[lowest_idx]))
    ptops = np.atleast_1d(sp_interp.pres(sp, sp.hght[highest_idx]))

    max_shear = None
    for pbot in pbots:
        for ptop in ptops:
            u_shr, v_shr = sp_winds.wind_shear(sp, pbot=pbot, ptop=ptop)
            mag = _finite_or_none(sp_utils.mag(u_shr, v_shr))
            if mag is None:
                continue
            if max_shear is None or mag > max_shear:
                max_shear = mag
    if max_shear is None:
        return None
    max_bulk_shear = float(sp_utils.KTS2MS(max_shear))  # m/s

    lr38 = _finite_or_none(sp_params.lapse_rate(sp, 3000.0, 8000.0, pres=False))
    if lr38 is None:
        return None

    plower = sp_interp.pres(sp, sp_interp.to_msl(sp, 3000.0))
    pupper = sp_interp.pres(sp, sp_interp.to_msl(sp, 12000.0))
    mnu, mnv = sp_winds.mean_wind(sp, pbot=plower, ptop=pupper)
    mnwind = _finite_or_none(sp_utils.mag(mnu, mnv))
    if mnwind is None:
        return None
    mnwind_ms = float(sp_utils.KTS2MS(mnwind))  # m/s

    value = (
        _MMP_A0
        + _MMP_A1 * max_bulk_shear
        + _MMP_A2 * lr38
        + _MMP_A3 * mucape
        + _MMP_A4 * mnwind_ms
    )
    return float(value) if np.isfinite(value) else None
