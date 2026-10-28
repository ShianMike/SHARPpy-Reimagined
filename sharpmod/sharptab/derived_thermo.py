"""Thermodynamic derived-parameter implementations split from the derived-parameter facade.

``sharpmod.sharptab.derived`` re-exports these routines so existing SHARPpy imports keep
their names, input conventions, and missing-value behavior."""

from __future__ import annotations

from . import parcels
from . import winds
from .constants import MISSING
from .constants import is_missing
import numpy as np
from sharpmod import backends
from sharpmod.sharptab import derived as _api


def _dcape_mucape(pres, hght, tmpc, dwpc, wdir, wspd, *, mucape=None):
    """Return finite DCAPE/MUCAPE through the shared parcel backend."""
    try:
        if mucape is None:
            mucape = backends.get_backend().profile_parcels(pres, hght, tmpc, dwpc).most_unstable.cape
        if mucape is None or is_missing(mucape) or not np.isfinite(mucape):
            return None

        dcape = backends.profile_dcape(pres, hght, tmpc, dwpc).cape
        if dcape is None or is_missing(dcape) or not np.isfinite(dcape):
            return None

        return float(dcape), float(mucape)
    except Exception:
        return None


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
        return _api._dcp_impl(prof)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING


def _dcp_impl(prof):
    # --- 0-6 km kinematic terms (layer-scoped: the interp/winds routines
    # resolve these from the valid levels spanning the SFC->6 km AGL layer and
    # tolerate masked levels elsewhere in the column) -----------------------
    shear06 = _api._shear_0_6km(prof)
    mnwind06 = _api._mean_wind_0_6km(prof)
    if is_missing(shear06) or is_missing(mnwind06):
        return MISSING
    if not (np.isfinite(shear06) and np.isfinite(mnwind06)):
        return MISSING

    # The backend resolves parcel paths from valid levels, preserving layer-scoped masks.
    arrays = _api._profile_columns(prof)
    if arrays is None:
        return MISSING
    mupcl = parcels.parcel(prof, "most_unstable")
    mucape = None if mupcl is None else mupcl.cape
    buoyancy = _api._dcape_mucape(*arrays, mucape=mucape)
    if buoyancy is None:
        return MISSING
    dcape, mucape = buoyancy

    # Requirement 2.5: a zero buoyancy factor makes DCP exactly zero (not MISSING).
    if dcape == 0.0 or mucape == 0.0:
        return 0.0

    value = (
        (dcape / _api._DCAPE_NORM)
        * (mucape / _api._MUCAPE_NORM)
        * (shear06 / _api._SHEAR_NORM)
        * (mnwind06 / _api._MNWIND_NORM)
    )
    if not np.isfinite(value):
        return MISSING
    return float(value)


def _mu_parcel_terms(pres, hght, tmpc, dwpc, wdir, wspd):
    """Return MU CAPE/CIN and AGL heights through the shared parcel backend."""
    try:
        parcel = backends.get_backend().profile_parcels(pres, hght, tmpc, dwpc).most_unstable
        return (
            _api._finite_or_none(parcel.cape),
            _api._finite_or_none(parcel.cin),
            _api._finite_or_none(parcel.lfc_height),
            _api._finite_or_none(parcel.el_height),
            _api._finite_or_none(parcel.start_height),
        )
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
        return _api._normalized_cape_cin_impl(prof)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING, MISSING


def _normalized_cape_cin_impl(prof):
    arrays = _api._profile_columns(prof)
    if arrays is None:
        return MISSING, MISSING

    mupcl = parcels.parcel(prof, "most_unstable")
    if mupcl is None:
        terms = _api._mu_parcel_terms(*arrays)
        if terms is None:
            return MISSING, MISSING
        mucape, cin, lfc_agl, el_agl, mu_start_agl = terms
    else:
        mucape = _api._finite_or_none(mupcl.cape)
        cin = _api._finite_or_none(mupcl.cin)
        lfc_agl = _api._finite_or_none(mupcl.lfc_height)
        el_agl = _api._finite_or_none(mupcl.el_height)
        mu_start_agl = _api._finite_or_none(mupcl.start_height)

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


def _sfc_cape(pres, hght, tmpc, dwpc, wdir, wspd):
    """Return finite surface-based CAPE through the shared parcel backend."""
    try:
        return _api._finite_or_none(backends.get_backend().profile_parcels(pres, hght, tmpc, dwpc).surface.cape)
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
        return _api._ehi_impl(prof, layer)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return MISSING


def _ehi_impl(prof, layer):
    bounds = _api._layer_top_agl(layer)
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
    arrays = _api._profile_columns(prof)
    if arrays is None:
        return MISSING
    sbpcl = parcels.parcel(prof, "surface")
    cape = _api._sfc_cape(*arrays) if sbpcl is None else sbpcl.cape
    if cape is None or not np.isfinite(cape):
        return MISSING

    value = (cape * srh) / _api._EHI_NORM
    if not np.isfinite(value):
        return MISSING
    return float(value)
