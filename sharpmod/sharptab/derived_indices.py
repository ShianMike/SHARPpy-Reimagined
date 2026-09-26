"""Convective and kinematic derived-index implementations split from the derived-parameter
facade.

``sharpmod.sharptab.derived`` re-exports these calculations to preserve the established
SHARPpy import names and parameter behavior."""

from __future__ import annotations

from . import parcels
from .constants import KTS_PER_MS
from .constants import MISSING
from .constants import is_missing
from sharpmod.upstream.upstream_warnings import known_sharppy_numerical_warnings
from types import SimpleNamespace
import numpy as np
from sharpmod.sharptab import derived as _api


@known_sharppy_numerical_warnings()
def _oracle_profile(prof):
    """Build the shared ``sharppy`` "default" Profile oracle for ``prof``.

    Returns a ``sharppy`` profile augmented with the convective attributes the
    SPC/AMS routines (``lhp``, ``ship``, ``mmp``, ...) read -- ``mupcl``,
    ``sfc_6km_shear``, ``lapserate_700_500``, ``srwind`` -- or ``None`` when the
    analyzed columns are missing/masked (via :func:`_profile_columns`) or when
    ``sharppy`` is unavailable / the ascent cannot be run. Never raises.
    """
    cache_attr = "_sharpmod_default_oracle"
    cache_miss = _api._ORACLE_CACHE_MISS
    cached = getattr(prof, cache_attr, cache_miss)
    if cached is not cache_miss:
        return cached

    arrays = _api._profile_columns(prof)
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
        mupcl = parcels.parcel(prof, "most_unstable")
        if mupcl is None or not np.isfinite(
            [mupcl.el_pressure, mupcl.start_pressure, mupcl.start_dewpoint],
        ).all():
            sp.mupcl = sp_params.parcelx(sp, flag=3)
        else:
            sp.mupcl = SimpleNamespace(
                bplus=mupcl.cape,
                bminus=mupcl.cin,
                pres=mupcl.start_pressure,
                tmpc=mupcl.start_temperature,
                dwpc=mupcl.start_dewpoint,
                lclpres=mupcl.lcl_pressure,
                lclhght=mupcl.lcl_height,
                lfcpres=mupcl.lfc_pressure,
                lfchght=mupcl.lfc_height,
                elpres=mupcl.el_pressure,
                elhght=mupcl.el_height,
                b3km=mupcl.cape_3km,
                b6km=mupcl.cape_6km,
            )
        sfcp = sp.pres[sp.sfc]
        p6km = sp_interp.pres(sp, sp_interp.to_msl(sp, 6000.0))
        sp.sfc_6km_shear = sp_winds.wind_shear(sp, pbot=sfcp, ptop=p6km)
        sp.lapserate_700_500 = sp_params.lapse_rate(sp, 700.0, 500.0, pres=True)
        sp.srwind = sp_winds.non_parcel_bunkers_motion(sp)
        result = sp
    except Exception:
        result = None

    try:
        setattr(prof, cache_attr, result)
    except (AttributeError, TypeError):
        pass
    return result


@known_sharppy_numerical_warnings()
def _convective_oracle_profile(prof):
    """Return a cached upstream SHARPpy ConvectiveProfile for SPC composites."""
    cache_attr = "_sharpmod_convective_oracle"
    cached = getattr(prof, cache_attr, _api._ORACLE_CACHE_MISS)
    if cached is not _api._ORACLE_CACHE_MISS:
        return cached

    arrays = _api._profile_columns(prof)
    if arrays is None:
        return None
    pres, hght, tmpc, dwpc, wdir, wspd = arrays

    try:
        from sharppy.sharptab import profile as sp_profile
    except Exception:
        return None

    kwargs = dict(
        profile="convective",
        pres=pres,
        hght=hght,
        tmpc=tmpc,
        dwpc=dwpc,
        wdir=wdir,
        wspd=wspd,
        missing=-9999.0,
        strictQC=False,
    )
    lat = _api._metadata_value(prof, "latitude", "lat")
    if lat is not None:
        kwargs["latitude"] = lat
    date = _api._metadata_raw(prof, "date", "valid", "run", "base_time")
    if date is not None:
        kwargs["date"] = date
    location = _api._metadata_raw(prof, "location", "loc", "station", "stn")
    if location is not None:
        kwargs["location"] = str(location)

    omeg = _api._omeg_column_for_oracle(prof, len(pres))
    if omeg is not None:
        kwargs["omeg"] = omeg

    try:
        oracle = sp_profile.create_profile(**kwargs)
    except Exception:
        oracle = None

    try:
        setattr(prof, cache_attr, oracle)
    except Exception:
        pass
    return oracle


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

    mucape = _api._finite_or_none(getattr(sp.mupcl, "bplus", None))
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
            mag = _api._finite_or_none(sp_utils.mag(u_shr, v_shr))
            if mag is None:
                continue
            if max_shear is None or mag > max_shear:
                max_shear = mag
    if max_shear is None:
        return None
    max_bulk_shear = float(sp_utils.KTS2MS(max_shear))  # m/s

    lr38 = _api._finite_or_none(sp_params.lapse_rate(sp, 3000.0, 8000.0, pres=False))
    if lr38 is None:
        return None

    plower = sp_interp.pres(sp, sp_interp.to_msl(sp, 3000.0))
    pupper = sp_interp.pres(sp, sp_interp.to_msl(sp, 12000.0))
    mnu, mnv = sp_winds.mean_wind(sp, pbot=plower, ptop=pupper)
    mnwind = _api._finite_or_none(sp_utils.mag(mnu, mnv))
    if mnwind is None:
        return None
    mnwind_ms = float(sp_utils.KTS2MS(mnwind))  # m/s

    value = (
        _api._MMP_A0
        + _api._MMP_A1 * max_bulk_shear
        + _api._MMP_A2 * lr38
        + _api._MMP_A3 * mucape
        + _api._MMP_A4 * mnwind_ms
    )
    return float(value) if np.isfinite(value) else None


def _left_supercell_composite_impl(prof):
    sp = _api._convective_oracle_profile(prof)
    if sp is None:
        return MISSING

    etop = getattr(sp, "etop", None)
    ebottom = getattr(sp, "ebottom", None)
    if is_missing(etop) or is_missing(ebottom):
        return 0.0

    mucape = _api._finite_or_none(getattr(getattr(sp, "mupcl", None), "bplus", None))
    mucin = _api._finite_or_none(getattr(getattr(sp, "mupcl", None), "bminus", None))
    left_esrh = getattr(sp, "left_esrh", None)
    try:
        esrh = _api._finite_or_none(left_esrh[0])
    except Exception:
        esrh = None
    ebwd_ms = _api._finite_or_none(getattr(sp, "ebwspd", None))
    if ebwd_ms is not None:
        ebwd_ms = ebwd_ms / KTS_PER_MS

    if mucape is None or mucin is None or esrh is None or ebwd_ms is None:
        return MISSING
    if mucape <= 0.0:
        return 0.0

    if ebwd_ms > 20.0:
        ebwd_ms = 20.0
    elif ebwd_ms < 10.0:
        ebwd_ms = 0.0

    if mucin > _api._LSCP_MUCIN_NORM:
        mucin_term = 1.0
    elif mucin < 0.0:
        mucin_term = _api._LSCP_MUCIN_NORM / mucin
    else:
        mucin_term = 1.0

    value = (
        (mucape / _api._LSCP_MUCAPE_NORM)
        * (esrh / _api._LSCP_ESRH_NORM)
        * (ebwd_ms / _api._LSCP_EBWD_NORM)
        * mucin_term
    )
    return float(value) if np.isfinite(value) else MISSING


def _non_supercell_tornado_parameter_impl(prof):
    sp = _api._convective_oracle_profile(prof)
    if sp is None:
        return MISSING

    lr01 = _api._finite_or_none(getattr(prof, "lapserate_sfc_1km", None))
    if lr01 is None:
        from . import params as sm_params
        lr01 = _api._finite_or_none(sm_params.lapse_rate(prof, 0, 1000, agl=True))

    mlpcl = getattr(sp, "mlpcl", None)
    mlcape3 = _api._finite_or_none(getattr(mlpcl, "b3km", None))
    mlcin = _api._finite_or_none(getattr(mlpcl, "bminus", None))
    shear06_ms = _api._vector_mag_ms(getattr(sp, "sfc_6km_shear", None))
    sfc_vort = _api._surface_relative_vorticity(prof)

    if None in (lr01, mlcape3, mlcin, shear06_ms, sfc_vort):
        return MISSING

    value = (
        (lr01 / _api._NSTP_LR_NORM)
        * (mlcape3 / _api._NSTP_MLCAPE3_NORM)
        * ((_api._NSTP_MLCIN_BASE - mlcin) / _api._NSTP_MLCIN_NORM)
        * ((_api._NSTP_SHEAR_BASE_MS - shear06_ms) / _api._NSTP_SHEAR_NORM_MS)
        * (sfc_vort / _api._NSTP_VORT_NORM_S)
    )
    return float(value) if np.isfinite(value) else MISSING


def _modified_sherbe_impl(prof):
    sp = _api._convective_oracle_profile(prof)
    if sp is None:
        return MISSING

    lllr = _api._finite_or_none(getattr(sp, "lapserate_3km", None))
    s15mg = _api._finite_or_none(_api._bulk_shear_ms(prof, _api._SFC_1500M_AGL))
    eshr = _api._finite_or_none(getattr(sp, "ebwspd", None))
    if eshr is not None:
        eshr = eshr / KTS_PER_MS
    maxtevv = _api._max_thetae_vertical_velocity(prof)

    if None in (lllr, s15mg, eshr, maxtevv):
        return MISSING

    value = (
        (((lllr - _api._MOSHE_LLLR_OFFSET) ** 2.0) / _api._MOSHE_LLLR_NORM)
        * ((s15mg - _api._MOSHE_SHEAR_OFFSET_MS) / _api._MOSHE_SHEAR_NORM_MS)
        * ((eshr - _api._MOSHE_SHEAR_OFFSET_MS) / _api._MOSHE_SHEAR_NORM_MS)
        * ((maxtevv + _api._MOSHE_TEVV_OFFSET) / _api._MOSHE_TEVV_NORM)
    )
    return float(value) if np.isfinite(value) else MISSING
