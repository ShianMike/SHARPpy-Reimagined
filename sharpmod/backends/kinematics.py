"""Authoritative array-only profile kinematics implementation.

The public SharpTab functions accept rich Profile objects and masked arrays.
This module is the narrower backend contract: one normalized set of reported
pressure, height, and wind-component vectors enters; all requested
surface-to-height layers and the shared Bunkers storm motion leave together.
Keeping this implementation free of Profile and Qt types makes it both the
portable Python fallback and the numerical oracle for the Rust extension.
"""

from __future__ import annotations

from dataclasses import astuple

import numpy as np

from .protocol import KinematicLayer, ProfileKinematics


KINEMATIC_LAYER_FIELDS = tuple(
    KinematicLayer.__dataclass_fields__
)
KINEMATIC_LAYER_WIDTH = len(KINEMATIC_LAYER_FIELDS)
_KTS_PER_MS = 1.9438444924406046
_MAX_PRESSURE_SAMPLES = 5_000
_NAN4 = (np.nan, np.nan, np.nan, np.nan)


def _valid_pairs(coordinate, values):
    coordinate = np.asarray(coordinate, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    good = np.isfinite(coordinate) & np.isfinite(values)
    coordinate = coordinate[good]
    values = values[good]
    if coordinate.size < 2:
        return coordinate, values
    order = np.argsort(coordinate, kind="stable")
    return coordinate[order], values[order]


def _interp_many(target, coordinate, values, *, log_output=False):
    coordinate, values = _valid_pairs(coordinate, values)
    target = np.asarray(target, dtype=np.float64)
    if coordinate.size < 2:
        return np.full(target.shape, np.nan, dtype=np.float64)
    raw = np.interp(
        target,
        coordinate,
        values,
        left=np.nan,
        right=np.nan,
    )
    raw[~np.isfinite(target)] = np.nan
    return np.power(10.0, raw) if log_output else raw


def _interp(target, coordinate, values, *, log_output=False):
    return float(
        _interp_many(
            np.asarray([target], dtype=np.float64),
            coordinate,
            values,
            log_output=log_output,
        )[0]
    )


def _pressure_samples(pbot, ptop):
    if not np.isfinite(pbot) or not np.isfinite(ptop) or pbot < ptop:
        return np.array([], dtype=np.float64)
    if pbot - ptop > _MAX_PRESSURE_SAMPLES:
        raise ValueError(
            "profile kinematics pressure span exceeds the safety limit"
        )
    return np.arange(pbot, ptop - 1.0, -1.0, dtype=np.float64)


def _mean(values, weights=None):
    values = np.asarray(values, dtype=np.float64)
    good = np.isfinite(values)
    if weights is None:
        return float(values[good].mean()) if np.any(good) else np.nan
    weights = np.asarray(weights, dtype=np.float64)
    good &= np.isfinite(weights)
    if not np.any(good):
        return np.nan
    denominator = float(weights[good].sum())
    if denominator == 0.0 or not np.isfinite(denominator):
        return np.nan
    return float(np.sum(values[good] * weights[good]) / denominator)


def _layer_basics(pres, hght, logp, u, v, sfc, top_agl):
    nan = np.nan
    surface_hght = float(hght[sfc])
    surface_pres = float(pres[sfc])
    target_hght = surface_hght + float(top_agl)

    top_pressure = _interp(
        target_hght, hght, logp, log_output=True
    )
    surface_u_pressure = _interp(np.log10(surface_pres), logp, u)
    surface_v_pressure = _interp(np.log10(surface_pres), logp, v)
    surface_u_height = _interp(surface_hght, hght, u)
    surface_v_height = _interp(surface_hght, hght, v)

    if np.isfinite(top_pressure) and top_pressure > 0.0:
        top_logp = np.log10(top_pressure)
        top_u_pressure = _interp(top_logp, logp, u)
        top_v_pressure = _interp(top_logp, logp, v)
    else:
        top_u_pressure = top_v_pressure = nan
    top_u_height = _interp(target_hght, hght, u)
    top_v_height = _interp(target_hght, hght, v)

    pressure_shear_u = (
        top_u_pressure - surface_u_pressure
        if np.isfinite(top_u_pressure) and np.isfinite(surface_u_pressure)
        else nan
    )
    pressure_shear_v = (
        top_v_pressure - surface_v_pressure
        if np.isfinite(top_v_pressure) and np.isfinite(surface_v_pressure)
        else nan
    )
    height_shear_u = (
        top_u_height - surface_u_height
        if np.isfinite(top_u_height) and np.isfinite(surface_u_height)
        else nan
    )
    height_shear_v = (
        top_v_height - surface_v_height
        if np.isfinite(top_v_height) and np.isfinite(surface_v_height)
        else nan
    )

    samples = _pressure_samples(surface_pres, top_pressure)
    if samples.size:
        sample_logp = np.log10(samples)
        sample_u = _interp_many(sample_logp, logp, u)
        sample_v = _interp_many(sample_logp, logp, v)
        mean_u = _mean(sample_u, samples)
        mean_v = _mean(sample_v, samples)
        mean_npw_u = _mean(sample_u)
        mean_npw_v = _mean(sample_v)
    else:
        mean_u = mean_v = mean_npw_u = mean_npw_v = nan

    return {
        "top_agl": float(top_agl),
        "top_pressure": top_pressure,
        "pressure_shear_u": pressure_shear_u,
        "pressure_shear_v": pressure_shear_v,
        "height_shear_u": height_shear_u,
        "height_shear_v": height_shear_v,
        "mean_u": mean_u,
        "mean_v": mean_v,
        "mean_npw_u": mean_npw_u,
        "mean_npw_v": mean_npw_v,
    }


def _bunkers_motion(six_km):
    values = (
        six_km["mean_npw_u"],
        six_km["mean_npw_v"],
        six_km["pressure_shear_u"],
        six_km["pressure_shear_v"],
    )
    if not all(np.isfinite(value) for value in values):
        return _NAN4
    mnu, mnv, shru, shrv = values
    shear_magnitude = float(np.hypot(shru, shrv))
    if shear_magnitude == 0.0 or not np.isfinite(shear_magnitude):
        return _NAN4
    deviation = (7.5 * _KTS_PER_MS) / shear_magnitude
    return (
        float(mnu + deviation * shrv),
        float(mnv - deviation * shru),
        float(mnu - deviation * shrv),
        float(mnv + deviation * shru),
    )


def _helicity(pres, hght, logp, u, v, sfc, top_agl, stu, stv):
    if not all(np.isfinite(value) for value in (top_agl, stu, stv)):
        return np.nan, np.nan, np.nan
    if top_agl == 0.0:
        return 0.0, 0.0, 0.0

    lower_msl = float(hght[sfc])
    upper_msl = lower_msl + float(top_agl)
    plower = _interp(lower_msl, hght, logp, log_output=True)
    pupper = _interp(upper_msl, hght, logp, log_output=True)
    if not np.isfinite(plower) or not np.isfinite(pupper):
        return np.nan, np.nan, np.nan

    valid_pres = np.isfinite(pres)
    lower_candidates = np.flatnonzero(valid_pres & (plower >= pres))
    upper_candidates = np.flatnonzero(valid_pres & (pupper <= pres))
    if lower_candidates.size == 0 or upper_candidates.size == 0:
        return np.nan, np.nan, np.nan
    ind1 = int(lower_candidates.min())
    ind2 = int(upper_candidates.max())
    if ind2 < ind1:
        return np.nan, np.nan, np.nan

    lower_logp = np.log10(plower)
    upper_logp = np.log10(pupper)
    u1 = _interp(lower_logp, logp, u)
    v1 = _interp(lower_logp, logp, v)
    u2 = _interp(upper_logp, logp, u)
    v2 = _interp(upper_logp, logp, v)
    if not all(np.isfinite(value) for value in (u1, v1, u2, v2)):
        return np.nan, np.nan, np.nan

    interior_u = np.asarray(u[ind1:ind2 + 1], dtype=np.float64)
    interior_v = np.asarray(v[ind1:ind2 + 1], dtype=np.float64)
    good = np.isfinite(interior_u) & np.isfinite(interior_v)
    layer_u = np.concatenate(([u1], interior_u[good], [u2]))
    layer_v = np.concatenate(([v1], interior_v[good], [v2]))
    if layer_u.size < 2:
        return np.nan, np.nan, np.nan

    storm_u = (layer_u - stu) / _KTS_PER_MS
    storm_v = (layer_v - stv) / _KTS_PER_MS
    contributions = (
        storm_u[1:] * storm_v[:-1]
        - storm_u[:-1] * storm_v[1:]
    )
    positive = float(contributions[contributions > 0.0].sum())
    negative = float(contributions[contributions < 0.0].sum())
    return positive + negative, positive, negative


def compute_profile_kinematics(
    pres,
    hght,
    u,
    v,
    layer_tops_agl,
    *,
    sfc=0,
):
    """Compute requested surface layers and shared Bunkers motion."""
    pres = np.asarray(pres, dtype=np.float64)
    hght = np.asarray(hght, dtype=np.float64)
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    tops = np.asarray(layer_tops_agl, dtype=np.float64)
    if not (pres.ndim == hght.ndim == u.ndim == v.ndim == tops.ndim == 1):
        raise ValueError("profile kinematics inputs must be one-dimensional")
    if len({pres.size, hght.size, u.size, v.size}) != 1:
        raise ValueError("profile kinematics columns must have the same length")
    if pres.size < 2:
        return ProfileKinematics(_NAN4, tuple(
            KinematicLayer(float(top), *([np.nan] * 14)) for top in tops
        ))
    if isinstance(sfc, bool) or int(sfc) != sfc:
        raise ValueError("sfc must be an integer profile index")
    sfc = int(sfc)
    if not 0 <= sfc < pres.size:
        raise ValueError("sfc is outside the profile")
    if np.any(~np.isfinite(tops)) or np.any(tops < 0.0):
        raise ValueError(
            "layer_tops_agl must contain finite, non-negative heights"
        )

    logp = np.full(pres.shape, np.nan, dtype=np.float64)
    positive = np.isfinite(pres) & (pres > 0.0)
    logp[positive] = np.log10(pres[positive])
    six_km = _layer_basics(pres, hght, logp, u, v, sfc, 6000.0)
    storm_motion = _bunkers_motion(six_km)
    rstu, rstv = storm_motion[:2]

    layers = []
    for top in tops:
        basics = (
            six_km.copy()
            if top == 6000.0
            else _layer_basics(pres, hght, logp, u, v, sfc, float(top))
        )
        srh_total, srh_positive, srh_negative = _helicity(
            pres, hght, logp, u, v, sfc, float(top), rstu, rstv
        )
        mean_u = basics["mean_u"]
        mean_v = basics["mean_v"]
        storm_relative_mean_u = (
            mean_u - rstu
            if np.isfinite(mean_u) and np.isfinite(rstu)
            else np.nan
        )
        storm_relative_mean_v = (
            mean_v - rstv
            if np.isfinite(mean_v) and np.isfinite(rstv)
            else np.nan
        )
        layers.append(KinematicLayer(
            **basics,
            srh_total=srh_total,
            srh_positive=srh_positive,
            srh_negative=srh_negative,
            storm_relative_mean_u=storm_relative_mean_u,
            storm_relative_mean_v=storm_relative_mean_v,
        ))
    return ProfileKinematics(
        tuple(float(value) for value in storm_motion),
        tuple(layers),
    )


def profile_kinematics_from_raw(storm_motion, layer_matrix):
    """Validate a native matrix and restore the typed backend result."""
    storm = np.asarray(storm_motion, dtype=np.float64)
    matrix = np.asarray(layer_matrix, dtype=np.float64)
    if storm.shape != (4,):
        raise RuntimeError(
            "sharpmod_rs.profile_kinematics returned invalid storm motion "
            f"shape {storm.shape}"
        )
    if matrix.ndim != 2 or matrix.shape[1] != KINEMATIC_LAYER_WIDTH:
        raise RuntimeError(
            "sharpmod_rs.profile_kinematics returned invalid layer shape "
            f"{matrix.shape}"
        )
    layers = tuple(
        KinematicLayer(*(float(value) for value in row))
        for row in matrix
    )
    return ProfileKinematics(
        tuple(float(value) for value in storm),
        layers,
    )


def profile_kinematics_to_raw(result):
    """Return matrix-shaped values for benchmarks and contract tests."""
    storm = np.asarray(result.storm_motion, dtype=np.float64)
    matrix = np.asarray(
        [astuple(layer) for layer in result.layers],
        dtype=np.float64,
    ).reshape((-1, KINEMATIC_LAYER_WIDTH))
    return storm, matrix


__all__ = [
    "KINEMATIC_LAYER_FIELDS",
    "KINEMATIC_LAYER_WIDTH",
    "compute_profile_kinematics",
    "profile_kinematics_from_raw",
    "profile_kinematics_to_raw",
]
