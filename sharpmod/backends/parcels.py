"""Typed parcel-workspace conversion and authoritative Python behavior.

The portable implementation intentionally delegates the ascent to the pinned
SHARPpy compatibility package. That implementation remains the scientific
oracle while the Rust backend owns an independent translation of the same
thermodynamic formulas and parcel-selection conventions.
"""

from __future__ import annotations

from dataclasses import astuple, dataclass

import numpy as np
import numpy.ma as ma

from .protocol import (
    ConvectiveParcelWorkspace,
    DowndraftDiagnostics,
    ParcelAscent,
    ParcelDiagnostics,
    ParcelTrace,
    ParcelWorkspace,
    ProfileThermodynamics,
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


@dataclass(frozen=True)
class BufferedParcelTrace:
    """Internal zero-copy view over native pressure/temperature buffers."""

    pressure: np.ndarray
    temperature: np.ndarray


@dataclass(frozen=True)
class BufferedParcelAscent:
    """Internal parcel result retaining NumPy trace views."""

    diagnostics: ParcelDiagnostics
    trace: BufferedParcelTrace


@dataclass(frozen=True)
class BufferedConvectiveParcelWorkspace:
    """Internal convective workspace backed by contiguous native buffers."""

    surface: BufferedParcelAscent
    forecast: BufferedParcelAscent
    most_unstable: BufferedParcelAscent
    mixed_layer: BufferedParcelAscent
    effective: BufferedParcelAscent
    effective_bottom_pressure: float
    effective_top_pressure: float


@dataclass(frozen=True)
class BufferedDowndraftDiagnostics:
    """Internal downdraft result retaining NumPy trace views."""

    cape: float
    source_pressure: float
    downrush_temperature: float
    trace: BufferedParcelTrace


@dataclass(frozen=True)
class BufferedProfileThermodynamics:
    """Internal shared result used by array-oriented application hot paths."""

    parcels: ParcelWorkspace
    convective: BufferedConvectiveParcelWorkspace
    downdraft: BufferedDowndraftDiagnostics


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


def _compute_profile_convective_prepared(profile):
    """Compute the convective workspace from one existing oracle profile."""
    from sharppy.sharptab import interp as sp_interp
    from sharppy.sharptab import params as sp_params
    from sharppy.sharptab import thermo as sp_thermo

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

    return _compute_profile_convective_prepared(
        _profile(pres, hght, tmpc, dwpc, sfc),
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


def _compute_profile_dcape_prepared(profile):
    """Compute DCAPE from one existing oracle profile."""
    from sharppy.sharptab import params as sp_params

    cape, temperature_trace, pressure_trace = sp_params.dcape(profile)
    trace = _trace(type(
        "_Trace",
        (),
        {"ptrace": pressure_trace, "ttrace": temperature_trace},
    )())
    source = trace.pressure[0] if trace.pressure else np.nan
    downrush = trace.temperature[-1] if trace.temperature else np.nan
    return DowndraftDiagnostics(_number(cape), source, downrush, trace)


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

    return _compute_profile_dcape_prepared(
        _profile(pres, hght, tmpc, dwpc, sfc),
    )


@known_sharppy_numerical_warnings()
def compute_profile_thermodynamics(pres, hght, tmpc, dwpc, *, sfc=0):
    """Compute parcel and downdraft workspaces from one oracle profile."""
    if pres.size < 3:
        missing_diagnostics = ParcelDiagnostics(*([np.nan] * PARCEL_WIDTH))
        missing_ascent = _missing_ascent()
        return ProfileThermodynamics(
            ParcelWorkspace(
                missing_diagnostics,
                missing_diagnostics,
                missing_diagnostics,
            ),
            ConvectiveParcelWorkspace(
                missing_ascent,
                missing_ascent,
                missing_ascent,
                missing_ascent,
                missing_ascent,
                np.nan,
                np.nan,
            ),
            DowndraftDiagnostics(
                np.nan,
                np.nan,
                np.nan,
                ParcelTrace((), ()),
            ),
        )

    profile = _profile(pres, hght, tmpc, dwpc, sfc)
    convective = _compute_profile_convective_prepared(profile)
    parcels = ParcelWorkspace(
        convective.surface.diagnostics,
        convective.most_unstable.diagnostics,
        convective.mixed_layer.diagnostics,
    )
    return ProfileThermodynamics(
        parcels,
        convective,
        _compute_profile_dcape_prepared(profile),
    )


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


def _diagnostics_from_raw(row):
    row = np.asarray(row, dtype=np.float64)
    if row.shape != (PARCEL_WIDTH,):
        raise RuntimeError(
            "native parcel diagnostics returned invalid shape "
            f"{row.shape}; expected {(PARCEL_WIDTH,)}"
        )
    return ParcelDiagnostics(*(float(value) for value in row))


def _trace_arrays_from_raw(pressure_trace, temperature_trace):
    pressure = np.asarray(pressure_trace, dtype=np.float64)
    temperature = np.asarray(temperature_trace, dtype=np.float64)
    if pressure.ndim != 1 or pressure.shape != temperature.shape:
        raise RuntimeError(
            "native parcel trace returned mismatched shapes "
            f"{pressure.shape} and {temperature.shape}"
        )
    pressure = pressure.view()
    temperature = temperature.view()
    pressure.setflags(write=False)
    temperature.setflags(write=False)
    return BufferedParcelTrace(pressure, temperature)


def _buffered_ascent_from_raw(row, pressure_trace, temperature_trace):
    return BufferedParcelAscent(
        _diagnostics_from_raw(row),
        _trace_arrays_from_raw(pressure_trace, temperature_trace),
    )


def _public_trace(trace):
    return ParcelTrace(
        tuple(float(value) for value in trace.pressure),
        tuple(float(value) for value in trace.temperature),
    )


def _public_ascent(ascent):
    return ParcelAscent(
        ascent.diagnostics,
        _public_trace(ascent.trace),
    )


def _ascent_from_raw(row, pressure_trace, temperature_trace):
    return _public_ascent(
        _buffered_ascent_from_raw(row, pressure_trace, temperature_trace),
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


def buffered_convective_workspace_from_raw(raw):
    """Validate native contiguous traces without creating Python floats."""
    try:
        matrix, bounds, pressure_buffer, temperature_buffer, offsets = raw
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
    pressure_buffer = np.asarray(pressure_buffer, dtype=np.float64)
    temperature_buffer = np.asarray(temperature_buffer, dtype=np.float64)
    if (
        pressure_buffer.ndim != 1
        or pressure_buffer.shape != temperature_buffer.shape
    ):
        raise RuntimeError(
            "native convective parcel trace buffers have mismatched shapes "
            f"{pressure_buffer.shape} and {temperature_buffer.shape}"
        )
    offsets = np.asarray(offsets)
    expected_offsets = (len(CONVECTIVE_PARCEL_KINDS) + 1,)
    if offsets.shape != expected_offsets or not np.issubdtype(
        offsets.dtype, np.integer,
    ):
        raise RuntimeError(
            "native convective parcel trace offsets are invalid; expected "
            f"an integer array with shape {expected_offsets}"
        )
    if (
        int(offsets[0]) != 0
        or int(offsets[-1]) != pressure_buffer.size
        or np.any(offsets[1:] < offsets[:-1])
    ):
        raise RuntimeError(
            "native convective parcel trace offsets do not span the buffers"
        )
    ascents = tuple(
        _buffered_ascent_from_raw(
            row,
            pressure_buffer[int(start):int(stop)],
            temperature_buffer[int(start):int(stop)],
        )
        for row, start, stop in zip(matrix, offsets[:-1], offsets[1:])
    )
    return BufferedConvectiveParcelWorkspace(
        *ascents,
        float(bounds[0]),
        float(bounds[1]),
    )


def convective_workspace_from_raw(raw):
    """Validate and restore the public convective parcel workspace."""
    buffered = buffered_convective_workspace_from_raw(raw)
    return ConvectiveParcelWorkspace(
        *(
            _public_ascent(getattr(buffered, kind))
            for kind in CONVECTIVE_PARCEL_KINDS
        ),
        buffered.effective_bottom_pressure,
        buffered.effective_top_pressure,
    )


def buffered_downdraft_from_raw(raw):
    """Validate native DCAPE data while retaining array trace buffers."""
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
    trace = _trace_arrays_from_raw(
        pressure_trace,
        temperature_trace,
    )
    return BufferedDowndraftDiagnostics(
        cape=float(summary[0]),
        source_pressure=float(summary[1]),
        downrush_temperature=float(summary[2]),
        trace=trace,
    )


def downdraft_from_raw(raw):
    """Validate and restore public native DCAPE diagnostics."""
    buffered = buffered_downdraft_from_raw(raw)
    return DowndraftDiagnostics(
        buffered.cape,
        buffered.source_pressure,
        buffered.downrush_temperature,
        _public_trace(buffered.trace),
    )


def profile_thermodynamics_buffers_from_raw(raw):
    """Restore one shared native result while retaining all trace buffers."""
    try:
        parcel_matrix, convective_raw, downdraft_raw = raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "sharpmod_rs.profile_thermodynamics returned an invalid result",
        ) from exc
    return BufferedProfileThermodynamics(
        parcel_workspace_from_raw(parcel_matrix),
        buffered_convective_workspace_from_raw(convective_raw),
        buffered_downdraft_from_raw(downdraft_raw),
    )


def profile_thermodynamics_from_buffers(buffered):
    """Convert internal array views to the stable public tuple contract."""
    convective = buffered.convective
    downdraft = buffered.downdraft
    return ProfileThermodynamics(
        buffered.parcels,
        ConvectiveParcelWorkspace(
            *(
                _public_ascent(getattr(convective, kind))
                for kind in CONVECTIVE_PARCEL_KINDS
            ),
            convective.effective_bottom_pressure,
            convective.effective_top_pressure,
        ),
        DowndraftDiagnostics(
            downdraft.cape,
            downdraft.source_pressure,
            downdraft.downrush_temperature,
            _public_trace(downdraft.trace),
        ),
    )


def profile_thermodynamics_from_raw(raw):
    """Validate and restore the public shared thermodynamic workspace."""
    return profile_thermodynamics_from_buffers(
        profile_thermodynamics_buffers_from_raw(raw),
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
    """Return the contiguous native-compatible representation."""
    ascents = tuple(
        getattr(result, kind) for kind in CONVECTIVE_PARCEL_KINDS
    )
    pressure_traces = tuple(
        np.asarray(ascent.trace.pressure, dtype=np.float64)
        for ascent in ascents
    )
    temperature_traces = tuple(
        np.asarray(ascent.trace.temperature, dtype=np.float64)
        for ascent in ascents
    )
    offsets = np.empty(len(ascents) + 1, dtype=np.uintp)
    offsets[0] = 0
    for index, trace in enumerate(pressure_traces, start=1):
        offsets[index] = offsets[index - 1] + trace.size
    pressure_buffer = (
        np.concatenate(pressure_traces)
        if int(offsets[-1])
        else np.empty(0, dtype=np.float64)
    )
    temperature_buffer = (
        np.concatenate(temperature_traces)
        if int(offsets[-1])
        else np.empty(0, dtype=np.float64)
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
        pressure_buffer,
        temperature_buffer,
        offsets,
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


def profile_thermodynamics_to_raw(result):
    """Return the nested native-compatible shared-workspace representation."""
    return (
        parcel_workspace_to_raw(result.parcels),
        convective_workspace_to_raw(result.convective),
        downdraft_to_raw(result.downdraft),
    )


__all__ = [
    "CONVECTIVE_PARCEL_KINDS",
    "PARCEL_FIELDS",
    "PARCEL_KINDS",
    "PARCEL_WIDTH",
    "BufferedProfileThermodynamics",
    "buffered_convective_workspace_from_raw",
    "buffered_downdraft_from_raw",
    "compute_lift_parcel",
    "compute_profile_convective_parcels",
    "compute_profile_dcape",
    "compute_profile_parcels",
    "compute_profile_thermodynamics",
    "convective_workspace_from_raw",
    "convective_workspace_to_raw",
    "downdraft_from_raw",
    "downdraft_to_raw",
    "parcel_ascent_from_raw",
    "parcel_ascent_to_raw",
    "parcel_workspace_from_raw",
    "parcel_workspace_to_raw",
    "profile_thermodynamics_buffers_from_raw",
    "profile_thermodynamics_from_buffers",
    "profile_thermodynamics_from_raw",
    "profile_thermodynamics_to_raw",
]
