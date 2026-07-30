"""Typed parcel-workspace conversion and authoritative Python behavior.

The portable implementation intentionally delegates the ascent to the pinned
SHARPpy compatibility package. That implementation remains the scientific
oracle while the Rust backend owns an independent translation of the same
thermodynamic formulas and parcel-selection conventions.
"""

from __future__ import annotations

from dataclasses import astuple

import numpy as np
import numpy.ma as ma

from .protocol import (
    ConvectiveParcelWorkspace,
    DowndraftDiagnostics,
    ParcelAscent,
    ParcelDiagnostics,
    ParcelTrace,
    ParcelWorkspace,
)
from sharpmod.upstream_warnings import known_sharppy_numerical_warnings


PARCEL_FIELDS = tuple(ParcelDiagnostics.__dataclass_fields__)
PARCEL_WIDTH = len(PARCEL_FIELDS)
PARCEL_KINDS = ("surface", "most_unstable", "mixed_layer")
_PARCEL_FLAGS = (1, 3, 4)
CONVECTIVE_PARCEL_KINDS = (
    "surface",
    "forecast",
    "most_unstable",
    "mixed_layer",
    "effective",
)


def _number(value):
    if value is None or ma.is_masked(value):
        return np.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def _diagnostics(sp_profile, parcel, sp_interp):
    start_pressure = _number(getattr(parcel, "pres", None))
    start_height = np.nan
    if np.isfinite(start_pressure):
        start_height = _number(
            sp_interp.to_agl(
                sp_profile,
                sp_interp.hght(sp_profile, start_pressure),
            ),
        )
    return ParcelDiagnostics(
        start_pressure=start_pressure,
        start_height=start_height,
        start_temperature=_number(getattr(parcel, "tmpc", None)),
        start_dewpoint=_number(getattr(parcel, "dwpc", None)),
        lcl_pressure=_number(getattr(parcel, "lclpres", None)),
        lcl_height=_number(getattr(parcel, "lclhght", None)),
        lfc_pressure=_number(getattr(parcel, "lfcpres", None)),
        lfc_height=_number(getattr(parcel, "lfchght", None)),
        el_pressure=_number(getattr(parcel, "elpres", None)),
        el_height=_number(getattr(parcel, "elhght", None)),
        cape=_number(getattr(parcel, "bplus", None)),
        cin=_number(getattr(parcel, "bminus", None)),
        cape_3km=_number(getattr(parcel, "b3km", None)),
        cape_6km=_number(getattr(parcel, "b6km", None)),
    )


def _trace(parcel):
    pressure = ma.asanyarray(
        getattr(parcel, "ptrace", ma.masked), dtype=np.float64,
    ).reshape(-1)
    temperature = ma.asanyarray(
        getattr(parcel, "ttrace", ma.masked), dtype=np.float64,
    ).reshape(-1)
    if pressure.size != temperature.size:
        return ParcelTrace((), ())
    valid = (
        ~ma.getmaskarray(pressure)
        & ~ma.getmaskarray(temperature)
        & np.isfinite(pressure.filled(np.nan))
        & np.isfinite(temperature.filled(np.nan))
    )
    return ParcelTrace(
        tuple(float(value) for value in pressure.data[valid]),
        tuple(float(value) for value in temperature.data[valid]),
    )


def _ascent(sp_profile, parcel, sp_interp):
    return ParcelAscent(
        diagnostics=_diagnostics(sp_profile, parcel, sp_interp),
        trace=_trace(parcel),
    )


def _missing_ascent():
    return ParcelAscent(
        ParcelDiagnostics(*([np.nan] * PARCEL_WIDTH)),
        ParcelTrace((), ()),
    )


def _profile(pres, hght, tmpc, dwpc, sfc):
    from sharppy.sharptab import profile as sp_profile

    zeros = np.zeros(pres.shape, dtype=np.float64)
    profile = sp_profile.create_profile(
        profile="default",
        pres=pres,
        hght=hght,
        tmpc=tmpc,
        dwpc=dwpc,
        wdir=zeros,
        wspd=zeros,
        missing=-9999.0,
        strictQC=False,
    )
    profile.sfc = int(sfc)
    return profile


@known_sharppy_numerical_warnings()
def compute_profile_parcels(pres, hght, tmpc, dwpc, *, sfc=0):
    """Compute SB/MU/ML parcel summaries through the Python oracle."""
    if pres.size < 3:
        missing = ParcelDiagnostics(*([np.nan] * PARCEL_WIDTH))
        return ParcelWorkspace(missing, missing, missing)

    from sharppy.sharptab import interp as sp_interp
    from sharppy.sharptab import params as sp_params
    profile = _profile(pres, hght, tmpc, dwpc, sfc)
    results = tuple(
        _diagnostics(
            profile,
            sp_params.parcelx(profile, flag=flag),
            sp_interp,
        )
        for flag in _PARCEL_FLAGS
    )
    return ParcelWorkspace(*results)


@known_sharppy_numerical_warnings()
def compute_profile_convective_parcels(
    pres, hght, tmpc, dwpc, *, sfc=0,
):
    """Compute full standard parcel ascents through the Python oracle."""
    if pres.size < 3:
        missing = _missing_ascent()
        return ConvectiveParcelWorkspace(
            missing,
            missing,
            missing,
            missing,
            missing,
            np.nan,
            np.nan,
        )

    from sharppy.sharptab import interp as sp_interp
    from sharppy.sharptab import params as sp_params
    from sharppy.sharptab import thermo as sp_thermo

    profile = _profile(pres, hght, tmpc, dwpc, sfc)
    mu_parcel = sp_params.parcelx(profile, flag=3)
    if _number(mu_parcel.pres) == _number(profile.pres[profile.sfc]):
        surface_parcel = mu_parcel
    else:
        surface_parcel = sp_params.parcelx(profile, flag=1)
    forecast_parcel = sp_params.parcelx(profile, flag=2)
    mixed_layer_parcel = sp_params.parcelx(profile, flag=4)

    effective_bottom, effective_top = sp_params.effective_inflow_layer(
        profile,
        mupcl=mu_parcel,
    )
    bottom = _number(effective_bottom)
    top = _number(effective_top)
    if np.isfinite(bottom) and np.isfinite(top):
        mean_theta = sp_params.mean_theta(profile, bottom, top)
        mean_mixratio = sp_params.mean_mixratio(profile, bottom, top)
        effective_pressure = (bottom + top) / 2.0
        effective_temperature = sp_thermo.theta(
            1000.0,
            mean_theta,
            effective_pressure,
        )
        effective_dewpoint = sp_thermo.temp_at_mixrat(
            mean_mixratio,
            effective_pressure,
        )
        effective_parcel = sp_params.parcelx(
            profile,
            flag=5,
            pres=effective_pressure,
            tmpc=effective_temperature,
            dwpc=effective_dewpoint,
        )
    else:
        effective_parcel = surface_parcel

    return ConvectiveParcelWorkspace(
        _ascent(profile, surface_parcel, sp_interp),
        _ascent(profile, forecast_parcel, sp_interp),
        _ascent(profile, mu_parcel, sp_interp),
        _ascent(profile, mixed_layer_parcel, sp_interp),
        _ascent(profile, effective_parcel, sp_interp),
        bottom,
        top,
    )


@known_sharppy_numerical_warnings()
def compute_lift_parcel(
    pres,
    hght,
    tmpc,
    dwpc,
    parcel_pressure,
    parcel_temperature,
    parcel_dewpoint,
    *,
    sfc=0,
):
    """Lift one explicit parcel through the Python oracle."""
    if pres.size < 3:
        return _missing_ascent()

    from sharppy.sharptab import interp as sp_interp
    from sharppy.sharptab import params as sp_params

    profile = _profile(pres, hght, tmpc, dwpc, sfc)
    parcel = sp_params.parcelx(
        profile,
        flag=5,
        pres=float(parcel_pressure),
        tmpc=float(parcel_temperature),
        dwpc=float(parcel_dewpoint),
    )
    return _ascent(profile, parcel, sp_interp)


@known_sharppy_numerical_warnings()
def compute_profile_dcape(pres, hght, tmpc, dwpc, *, sfc=0):
    """Compute DCAPE and its trace through the Python oracle."""
    if pres.size < 3:
        return DowndraftDiagnostics(
            np.nan,
            np.nan,
            np.nan,
            ParcelTrace((), ()),
        )

    from sharppy.sharptab import params as sp_params

    profile = _profile(pres, hght, tmpc, dwpc, sfc)
    cape, temperature_trace, pressure_trace = sp_params.dcape(profile)
    trace = _trace(type(
        "_Trace",
        (),
        {"ptrace": pressure_trace, "ttrace": temperature_trace},
    )())
    source = trace.pressure[0] if trace.pressure else np.nan
    downrush = trace.temperature[-1] if trace.temperature else np.nan
    return DowndraftDiagnostics(_number(cape), source, downrush, trace)


def parcel_workspace_from_raw(matrix):
    """Validate a native parcel matrix and restore the typed result."""
    matrix = np.asarray(matrix, dtype=np.float64)
    expected = (len(PARCEL_KINDS), PARCEL_WIDTH)
    if matrix.shape != expected:
        raise RuntimeError(
            "sharpmod_rs.profile_parcels returned invalid shape "
            f"{matrix.shape}; expected {expected}"
        )
    return ParcelWorkspace(*(
        ParcelDiagnostics(*(float(value) for value in row))
        for row in matrix
    ))


def _ascent_from_raw(row, pressure_trace, temperature_trace):
    row = np.asarray(row, dtype=np.float64)
    if row.shape != (PARCEL_WIDTH,):
        raise RuntimeError(
            "native parcel diagnostics returned invalid shape "
            f"{row.shape}; expected {(PARCEL_WIDTH,)}"
        )
    pressure = np.asarray(pressure_trace, dtype=np.float64)
    temperature = np.asarray(temperature_trace, dtype=np.float64)
    if pressure.ndim != 1 or pressure.shape != temperature.shape:
        raise RuntimeError(
            "native parcel trace returned mismatched shapes "
            f"{pressure.shape} and {temperature.shape}"
        )
    return ParcelAscent(
        ParcelDiagnostics(*(float(value) for value in row)),
        ParcelTrace(
            tuple(float(value) for value in pressure),
            tuple(float(value) for value in temperature),
        ),
    )


def parcel_ascent_from_raw(raw):
    """Validate and restore one explicit native parcel ascent."""
    try:
        row, pressure_trace, temperature_trace = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs.lift_parcel returned an invalid result",
        ) from exc
    return _ascent_from_raw(row, pressure_trace, temperature_trace)


def convective_workspace_from_raw(raw):
    """Validate and restore a native full convective parcel workspace."""
    try:
        matrix, bounds, pressure_traces, temperature_traces = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs.profile_convective_parcels returned an invalid result",
        ) from exc
    matrix = np.asarray(matrix, dtype=np.float64)
    expected = (len(CONVECTIVE_PARCEL_KINDS), PARCEL_WIDTH)
    if matrix.shape != expected:
        raise RuntimeError(
            "sharpmod_rs.profile_convective_parcels returned invalid shape "
            f"{matrix.shape}; expected {expected}"
        )
    bounds = np.asarray(bounds, dtype=np.float64)
    if bounds.shape != (2,):
        raise RuntimeError(
            "native effective-layer bounds returned invalid shape "
            f"{bounds.shape}; expected {(2,)}"
        )
    if not (
        len(pressure_traces)
        == len(temperature_traces)
        == len(CONVECTIVE_PARCEL_KINDS)
    ):
        raise RuntimeError("native convective parcel trace count is invalid")
    ascents = tuple(
        _ascent_from_raw(row, pressure_trace, temperature_trace)
        for row, pressure_trace, temperature_trace in zip(
            matrix,
            pressure_traces,
            temperature_traces,
        )
    )
    return ConvectiveParcelWorkspace(
        *ascents,
        float(bounds[0]),
        float(bounds[1]),
    )


def downdraft_from_raw(raw):
    """Validate and restore native DCAPE diagnostics."""
    try:
        summary, pressure_trace, temperature_trace = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs.profile_dcape returned an invalid result",
        ) from exc
    summary = np.asarray(summary, dtype=np.float64)
    if summary.shape != (3,):
        raise RuntimeError(
            "native DCAPE summary returned invalid shape "
            f"{summary.shape}; expected {(3,)}"
        )
    trace = _ascent_from_raw(
        np.full(PARCEL_WIDTH, np.nan),
        pressure_trace,
        temperature_trace,
    ).trace
    return DowndraftDiagnostics(
        cape=float(summary[0]),
        source_pressure=float(summary[1]),
        downrush_temperature=float(summary[2]),
        trace=trace,
    )


def parcel_workspace_to_raw(result):
    """Return the fixed matrix representation used by tests and benchmarks."""
    return np.asarray(
        [
            astuple(result.surface),
            astuple(result.most_unstable),
            astuple(result.mixed_layer),
        ],
        dtype=np.float64,
    )


def parcel_ascent_to_raw(result):
    """Return the raw row-and-trace representation used by tests."""
    return (
        np.asarray(astuple(result.diagnostics), dtype=np.float64),
        np.asarray(result.trace.pressure, dtype=np.float64),
        np.asarray(result.trace.temperature, dtype=np.float64),
    )


def convective_workspace_to_raw(result):
    """Return the matrix-and-traces representation used by tests."""
    ascents = tuple(
        result.parcel(kind) for kind in CONVECTIVE_PARCEL_KINDS
    )
    return (
        np.asarray(
            [astuple(ascent.diagnostics) for ascent in ascents],
            dtype=np.float64,
        ),
        np.asarray(
            [
                result.effective_bottom_pressure,
                result.effective_top_pressure,
            ],
            dtype=np.float64,
        ),
        tuple(
            np.asarray(ascent.trace.pressure, dtype=np.float64)
            for ascent in ascents
        ),
        tuple(
            np.asarray(ascent.trace.temperature, dtype=np.float64)
            for ascent in ascents
        ),
    )


def downdraft_to_raw(result):
    """Return the summary-and-trace representation used by tests."""
    return (
        np.asarray(
            (
                result.cape,
                result.source_pressure,
                result.downrush_temperature,
            ),
            dtype=np.float64,
        ),
        np.asarray(result.trace.pressure, dtype=np.float64),
        np.asarray(result.trace.temperature, dtype=np.float64),
    )


__all__ = [
    "CONVECTIVE_PARCEL_KINDS",
    "PARCEL_FIELDS",
    "PARCEL_KINDS",
    "PARCEL_WIDTH",
    "compute_lift_parcel",
    "compute_profile_convective_parcels",
    "compute_profile_dcape",
    "compute_profile_parcels",
    "convective_workspace_from_raw",
    "convective_workspace_to_raw",
    "downdraft_from_raw",
    "downdraft_to_raw",
    "parcel_ascent_from_raw",
    "parcel_ascent_to_raw",
    "parcel_workspace_from_raw",
    "parcel_workspace_to_raw",
]
