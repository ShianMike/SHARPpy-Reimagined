"""Live experimental HRRR inputs for regional TOI guidance.

The public Tornado Outbreak Indicator description exposes regional jet-motion
and peak-STP ingredients, but not the official scorecard or calibration. This
module combines reproducible HRRR features with SHARPpy's explicitly versioned
public-bin reconstruction. It never labels that output as official SPC
guidance or turns a point sounding into regional TOI.
"""

from __future__ import annotations

import math
import os
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from sharpmod.model_transport import (
    DownloadCancelled,
    OptimizedTransportUnavailable,
)

from .schemas import (
    GuidanceGrid,
    GuidanceState,
    RegionalGuidance,
    TOIGuidance,
)
from .toi import DEFAULT_MAX_JET_TRANSLATION_KT, extract_toi_features
from .toi_scorecard import (
    TOI_MEASURED_SKILL_NOTE,
    TOI_MEASURED_SKILL_VERSION,
    TOI_PUBLIC_METHOD_REFERENCE,
    TOI_SCORECARD_VERSION,
    TOIProbabilityCalibrator,
    compute_experimental_toi,
)


#: v4 bounds jet-object association by a kinematic ceiling over the actual
#: inter-frame gap instead of a fixed distance radius, which changes the
#: extracted translation speed and therefore every downstream feature.  The
#: version is part of the archive cache key, so v3 case files cannot be resumed
#: or compiled into a v4 dataset.
HRRR_TOI_METHOD_VERSION = "sharpmod_hrrr_toi_experimental_v4"
HRRR_STP_PROXY_VERSION = "sharpmod_fixed_layer_stp_proxy_v1"
HRRR_SOURCE_NAME = "NOAA High-Resolution Rapid Refresh (HRRR) regional grids"
DEFAULT_REGION_RADIUS_KM = 1400.0
DEFAULT_GRID_STRIDE = 4
DEFAULT_RISK_STP_THRESHOLD = 0.5
DEFAULT_MAX_RISK_DISTANCE_KM = 900.0
DEFAULT_JET_THRESHOLD_KT = 50.0
DEFAULT_MAX_JET_MATCH_DISTANCE_KM = 1800.0

# Published TOI evaluates midlevel-jet translation across an 18-hour window
# (nominally 06Z the day before through 00Z on the event day).  Three-hourly
# sampling gives seven frames across that window, which is dense enough for
# deterministic jet-object association without adding unbounded downloads.
TOI_WINDOW_HOURS = 18
TOI_SAMPLING_INTERVAL_HOURS = 3
# Seven planned window frames, plus at most one off-interval requested hour.
TOI_MAXIMUM_FRAMES = 8
REGIONAL_GUIDANCE_DOWNLOAD_DIRNAME = "regional-guidance"
# Feature extraction needs two times; below half the published window the
# translation speed becomes a short-baseline extrapolation rather than a
# measurement of the 18-hour jet evolution.
TOI_MINIMUM_FRAMES = 2
TOI_MINIMUM_COVERAGE_HOURS = 9.0
# Two sampling intervals.  A wider hole between used frames still yields a
# usable track, but object association is no longer 3-hourly, so the result is
# reported as degraded rather than complete.
TOI_DEGRADED_GAP_HOURS = 6.0

@dataclass(frozen=True)
class HrrrRegionalFrame:
    """One bounded HRRR regional snapshot used by the TOI feature workflow."""

    run_time: datetime
    valid_time: datetime
    forecast_hour: int
    pressure_level_hpa: int
    latitude: np.ndarray
    longitude: np.ndarray
    u_wind_mps: np.ndarray
    v_wind_mps: np.ndarray
    surface_cape_jkg: np.ndarray
    temperature_2m_k: np.ndarray
    dewpoint_2m_k: np.ndarray
    srh_1km_m2s2: np.ndarray
    shear_u_6km_mps: np.ndarray
    shear_v_6km_mps: np.ndarray
    source_url: str
    shear_interpretation: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_time, datetime) or not isinstance(
            self.valid_time, datetime
        ):
            raise ValueError("HRRR run_time and valid_time must be datetimes")
        if int(self.forecast_hour) < 0:
            raise ValueError("HRRR forecast_hour must be non-negative")
        if int(self.pressure_level_hpa) not in {300, 500}:
            raise ValueError("HRRR TOI jet level must be 300 or 500 hPa")

        latitude = np.asarray(self.latitude, dtype=float)
        longitude = np.asarray(self.longitude, dtype=float)
        if latitude.ndim != 2 or longitude.shape != latitude.shape:
            raise ValueError("HRRR regional coordinates must be matching 2-D grids")
        if latitude.size == 0:
            raise ValueError("HRRR regional grid must not be empty")
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)

        for name in (
            "u_wind_mps",
            "v_wind_mps",
            "surface_cape_jkg",
            "temperature_2m_k",
            "dewpoint_2m_k",
            "srh_1km_m2s2",
            "shear_u_6km_mps",
            "shear_v_6km_mps",
        ):
            values = np.asarray(getattr(self, name), dtype=float)
            if values.shape != latitude.shape:
                raise ValueError(
                    f"{name} has shape {values.shape}; expected {latitude.shape}"
                )
            object.__setattr__(self, name, values)
        object.__setattr__(self, "forecast_hour", int(self.forecast_hour))
        object.__setattr__(
            self, "pressure_level_hpa", int(self.pressure_level_hpa)
        )
        object.__setattr__(self, "source_url", str(self.source_url).strip())
        object.__setattr__(
            self,
            "shear_interpretation",
            " ".join(str(self.shear_interpretation).split()),
        )


@dataclass(frozen=True)
class ObjectiveRiskRegion:
    """An explicitly objective, non-SPC risk mask derived from proxy STP."""

    mask: np.ndarray
    nearest_point_distance_km: float
    peak_stp: float
    grid_point_count: int

    def __post_init__(self) -> None:
        mask = np.asarray(self.mask, dtype=bool)
        if mask.ndim != 2 or not np.any(mask):
            raise ValueError("objective risk mask must select a 2-D region")
        object.__setattr__(self, "mask", mask)
        object.__setattr__(self, "grid_point_count", int(self.grid_point_count))


def normalize_hrrr_bulk_shear_components(
    u_values: np.ndarray,
    v_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Normalize HRRR's ambiguous VUCSH/VVCSH diagnostic representation.

    NOAA's GRIB table declares these fields in inverse seconds, while current
    operational HRRR values have the magnitude of layer component differences.
    A small-valued future/source representation is treated as an actual rate
    and integrated over 6000 m; present operational values are retained as the
    component delta in m/s.  The selected interpretation is always recorded.
    """

    u_values = np.asarray(u_values, dtype=float)
    v_values = np.asarray(v_values, dtype=float)
    if u_values.shape != v_values.shape:
        raise ValueError("HRRR bulk-shear components must have matching shapes")
    magnitude = np.hypot(u_values, v_values)
    finite = magnitude[np.isfinite(magnitude)]
    if finite.size == 0:
        raise ValueError("HRRR 0-6 km bulk-shear diagnostic is entirely missing")
    percentile_95 = float(np.percentile(finite, 95))
    if percentile_95 <= 0.25:
        return (
            u_values * 6000.0,
            v_values * 6000.0,
            "VUCSH/VVCSH inverse-second rates integrated over 6000 m",
        )
    return (
        u_values,
        v_values,
        "VUCSH/VVCSH operational component deltas treated as m/s",
    )


def fixed_layer_stp_proxy(frame: HrrrRegionalFrame) -> np.ndarray:
    """Vectorized SHARPpy fixed-layer STP equation for one HRRR snapshot.

    Surface-parcel LCL height is estimated with the Bolton LCL-temperature
    approximation and dry-adiabatic displacement.  This is a transparent
    proxy input for regional feature extraction, not an official HRRR/SPC STP
    product.
    """

    temperature = np.asarray(frame.temperature_2m_k, dtype=float)
    dewpoint = np.minimum(np.asarray(frame.dewpoint_2m_k, dtype=float), temperature)
    valid_thermo = (
        np.isfinite(temperature)
        & np.isfinite(dewpoint)
        & (temperature > 150.0)
        & (temperature < 350.0)
        & (dewpoint > 150.0)
    )
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        lcl_temperature = 1.0 / (
            1.0 / (dewpoint - 56.0)
            + np.log(temperature / dewpoint) / 800.0
        ) + 56.0
        # cp / g converts the dry-adiabatic temperature decrease to metres.
        lcl_height_m = (1004.0 / 9.80665) * np.maximum(
            temperature - lcl_temperature, 0.0
        )

    cape = np.maximum(np.asarray(frame.surface_cape_jkg, dtype=float), 0.0)
    srh = np.maximum(np.asarray(frame.srh_1km_m2s2, dtype=float), 0.0)
    bulk_shear = np.hypot(frame.shear_u_6km_mps, frame.shear_v_6km_mps)
    lcl_term = np.clip((2000.0 - lcl_height_m) / 1000.0, 0.0, 1.0)
    shear_term = np.where(
        bulk_shear < 12.5,
        0.0,
        np.minimum(bulk_shear, 30.0) / 20.0,
    )
    with np.errstate(invalid="ignore", over="ignore"):
        result = (cape / 1500.0) * lcl_term * (srh / 150.0) * shear_term
    valid = (
        valid_thermo
        & np.isfinite(cape)
        & np.isfinite(srh)
        & np.isfinite(bulk_shear)
    )
    return np.where(valid, np.clip(result, 0.0, 100.0), np.nan)


def _great_circle_grid_km(
    latitude: np.ndarray,
    longitude: np.ndarray,
    point_latitude: float,
    point_longitude: float,
) -> np.ndarray:
    phi = np.radians(np.asarray(latitude, dtype=float))
    point_phi = math.radians(float(point_latitude))
    dphi = phi - point_phi
    dlambda = np.radians(np.asarray(longitude, dtype=float) - point_longitude)
    value = (
        np.sin(dphi / 2.0) ** 2
        + np.cos(phi) * math.cos(point_phi) * np.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * 6371.0088 * np.arcsin(np.minimum(1.0, np.sqrt(value)))


def _connected_components(mask: np.ndarray) -> tuple[tuple[tuple[int, int], ...], ...]:
    mask = np.asarray(mask, dtype=bool)
    visited = np.zeros(mask.shape, dtype=bool)
    rows, cols = mask.shape
    result: list[tuple[tuple[int, int], ...]] = []
    for row in range(rows):
        for col in range(cols):
            if not mask[row, col] or visited[row, col]:
                continue
            stack = [(row, col)]
            visited[row, col] = True
            component: list[tuple[int, int]] = []
            while stack:
                current_row, current_col = stack.pop()
                component.append((current_row, current_col))
                for row_delta in (-1, 0, 1):
                    for col_delta in (-1, 0, 1):
                        if row_delta == 0 and col_delta == 0:
                            continue
                        next_row = current_row + row_delta
                        next_col = current_col + col_delta
                        if not (0 <= next_row < rows and 0 <= next_col < cols):
                            continue
                        if mask[next_row, next_col] and not visited[
                            next_row, next_col
                        ]:
                            visited[next_row, next_col] = True
                            stack.append((next_row, next_col))
            result.append(tuple(component))
    return tuple(result)


def select_objective_risk_region(
    stp_series: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    point_latitude: float,
    point_longitude: float,
    *,
    threshold: float = DEFAULT_RISK_STP_THRESHOLD,
    minimum_grid_points: int = 4,
    maximum_point_distance_km: float = DEFAULT_MAX_RISK_DISTANCE_KM,
) -> ObjectiveRiskRegion:
    """Select the nearest connected proxy-STP region to the sounding point."""

    stp = np.asarray(stp_series, dtype=float)
    latitude = np.asarray(latitude, dtype=float)
    longitude = np.asarray(longitude, dtype=float)
    if stp.ndim != 3 or stp.shape[1:] != latitude.shape:
        raise ValueError("proxy STP must have time, row, and column dimensions")
    if longitude.shape != latitude.shape:
        raise ValueError("risk-region coordinates must have matching shapes")
    finite = np.isfinite(stp)
    peak = np.max(np.where(finite, stp, -np.inf), axis=0)
    peak[~np.any(finite, axis=0)] = np.nan
    candidate = np.isfinite(peak) & (peak >= float(threshold))
    distances = _great_circle_grid_km(
        latitude, longitude, point_latitude, point_longitude
    )

    ranked = []
    for component in _connected_components(candidate):
        if len(component) < int(minimum_grid_points):
            continue
        rows = np.fromiter((point[0] for point in component), dtype=int)
        cols = np.fromiter((point[1] for point in component), dtype=int)
        nearest = float(np.min(distances[rows, cols]))
        component_peak = float(np.max(peak[rows, cols]))
        ranked.append((nearest, -component_peak, -len(component), rows, cols))
    if not ranked:
        raise ValueError(
            f"no connected fixed-layer STP proxy region reached {threshold:g}"
        )
    nearest, negative_peak, negative_size, rows, cols = min(
        ranked, key=lambda item: item[:3]
    )
    if nearest > float(maximum_point_distance_km):
        raise ValueError(
            "nearest fixed-layer STP proxy region is "
            f"{nearest:.0f} km from the sounding (limit "
            f"{maximum_point_distance_km:.0f} km)"
        )
    mask = np.zeros(latitude.shape, dtype=bool)
    mask[rows, cols] = True
    return ObjectiveRiskRegion(
        mask=mask,
        nearest_point_distance_km=nearest,
        peak_stp=-negative_peak,
        grid_point_count=-negative_size,
    )


def unavailable_hrrr_guidance(
    reason: str,
    *,
    run_time: datetime | None = None,
    provenance: dict[str, str] | None = None,
) -> RegionalGuidance:
    """Return a failure-soft HRRR payload with an explicit TOI state."""

    details = {
        "status": "experimental-not-official-SPC-guidance",
        "method_version": HRRR_TOI_METHOD_VERSION,
    }
    if run_time is not None:
        details["run"] = run_time.isoformat()
    details.update(provenance or {})
    return RegionalGuidance(
        toi=TOIGuidance.unavailable(str(reason)),
        source=HRRR_SOURCE_NAME,
        experimental_not_official=True,
        provenance=details,
    )


def build_hrrr_guidance_from_frames(
    frames: tuple[HrrrRegionalFrame, ...] | list[HrrrRegionalFrame],
    point_latitude: float,
    point_longitude: float,
    *,
    risk_threshold: float = DEFAULT_RISK_STP_THRESHOLD,
    maximum_risk_distance_km: float = DEFAULT_MAX_RISK_DISTANCE_KM,
    region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
    grid_stride: int = DEFAULT_GRID_STRIDE,
    sampling: TOITemporalSampling | None = None,
    calibrator: TOIProbabilityCalibrator | None = None,
) -> RegionalGuidance:
    """Build experimental TOI guidance from every fetched HRRR frame.

    All frames are used in valid-time order.  ``sampling`` carries the realized
    temporal-sampling audit from the live producer; when omitted it is derived
    from the supplied frames, which are then by definition the requested ones.
    """

    frames = tuple(sorted(frames, key=lambda frame: frame.valid_time))
    if len(frames) < TOI_MINIMUM_FRAMES:
        raise ValueError("experimental TOI requires at least two regional times")
    reference = frames[0]
    for frame in frames[1:]:
        if frame.pressure_level_hpa != reference.pressure_level_hpa:
            raise ValueError("HRRR TOI frames use inconsistent jet levels")
        if frame.latitude.shape != reference.latitude.shape or not np.allclose(
            frame.latitude, reference.latitude, equal_nan=True
        ):
            raise ValueError("HRRR TOI frames use inconsistent latitude grids")
        if not np.allclose(
            frame.longitude, reference.longitude, equal_nan=True
        ):
            raise ValueError("HRRR TOI frames use inconsistent longitude grids")

    stp = np.stack([fixed_layer_stp_proxy(frame) for frame in frames])
    risk = select_objective_risk_region(
        stp,
        reference.latitude,
        reference.longitude,
        point_latitude,
        point_longitude,
        threshold=risk_threshold,
        maximum_point_distance_km=maximum_risk_distance_km,
    )
    level = reference.pressure_level_hpa
    grid = GuidanceGrid(
        model="HRRR",
        cycle=reference.run_time,
        valid_times=tuple(frame.valid_time for frame in frames),
        latitude=reference.latitude,
        longitude=reference.longitude,
        fields={
            f"u_wind_{level}_hpa": np.stack(
                [frame.u_wind_mps for frame in frames]
            ),
            f"v_wind_{level}_hpa": np.stack(
                [frame.v_wind_mps for frame in frames]
            ),
            "stp": stp,
        },
        units={
            f"u_wind_{level}_hpa": "m/s",
            f"v_wind_{level}_hpa": "m/s",
            "stp": "1",
        },
        provenance={
            "stp_proxy": HRRR_STP_PROXY_VERSION,
            "risk_mask": "nearest connected objective proxy-STP region",
        },
    )
    features = extract_toi_features(
        grid,
        risk.mask,
        pressure_level_hpa=level,
        jet_threshold_kt=DEFAULT_JET_THRESHOLD_KT,
        min_grid_points=4,
        maximum_match_distance_km=DEFAULT_MAX_JET_MATCH_DISTANCE_KM,
        maximum_translation_kt=DEFAULT_MAX_JET_TRANSLATION_KT,
    )
    toi_result = compute_experimental_toi(features, calibrator=calibrator)
    sources = ";".join(
        dict.fromkeys(frame.source_url for frame in frames if frame.source_url)
    )
    shear_methods = "; ".join(
        dict.fromkeys(
            frame.shear_interpretation
            for frame in frames
            if frame.shear_interpretation
        )
    )
    if sampling is None:
        sampling = summarize_toi_sampling(
            [frame.forecast_hour for frame in frames], frames
        )
    duration_hours = (
        frames[-1].valid_time - frames[0].valid_time
    ).total_seconds() / 3600.0
    provenance = {
        "status": "experimental-not-official-SPC-guidance",
        "run": reference.run_time.isoformat(),
        "forecast_hours": ",".join(str(frame.forecast_hour) for frame in frames),
        "duration_hours": f"{duration_hours:g}",
        "region_radius_km": f"{float(region_radius_km):g}",
        "grid_sampling": f"every {int(grid_stride)} HRRR points (~12 km)",
        "jet_threshold": f"{DEFAULT_JET_THRESHOLD_KT:g} kt",
        # Object association is bounded in speed, not just distance, so the
        # reported translation cannot exceed this ceiling.
        "jet_match_radius_km": f"{DEFAULT_MAX_JET_MATCH_DISTANCE_KM:g}",
        "jet_translation_ceiling_kt": f"{DEFAULT_MAX_JET_TRANSLATION_KT:g}",
        "risk_mask": (
            f"nearest connected {risk_threshold:g}+ fixed-layer STP proxy "
            "region to sounding"
        ),
        "risk_distance_from_sounding_km": (
            f"{risk.nearest_point_distance_km:.1f}"
        ),
        "risk_grid_points": str(risk.grid_point_count),
        "stp_proxy": (
            f"{HRRR_STP_PROXY_VERSION}; SHARPpy fixed-layer equation with "
            "HRRR SBCAPE, 0-1 km SRH, 0-6 km shear, and Bolton surface LCL"
        ),
        "toi_feature_method": "regional jet-object tracking and objective STP mask",
        "toi_scorecard": TOI_SCORECARD_VERSION,
        "toi_probability": toi_result.calibration_version,
        "toi_public_method": TOI_PUBLIC_METHOD_REFERENCE,
        "toi_probability_status": (
            "experimental public-anchor transform; not official SPC calibration"
            if calibrator is None
            else "explicitly selected offline calibration; not official SPC "
            "calibration"
        ),
        # Honest disclosure of measured performance, shown wherever provenance
        # is shown.  Only the shipped transform has been evaluated this way; a
        # selected offline artifact carries its own validation state instead.
        **(
            {
                "toi_measured_skill": TOI_MEASURED_SKILL_NOTE,
                "toi_measured_skill_version": TOI_MEASURED_SKILL_VERSION,
            }
            if calibrator is None
            else {}
        ),
        "toi_components": (
            f"translation={toi_result.translation_component:g}*"
            f"{toi_result.translation_weight:g};"
            f"location={toi_result.location_component:g}*"
            f"{toi_result.location_weight:g};"
            f"maximum_jet={toi_result.maximum_jet_component:g}*"
            f"{toi_result.maximum_jet_weight:g};"
            f"season={toi_result.seasonal_adjustment:g};"
            f"stp_bin={toi_result.stp_bin_value:g}"
        ),
        "shear_interpretation": shear_methods,
    }
    provenance.update(sampling.to_provenance())
    if calibrator is not None and hasattr(calibrator, "provenance"):
        provenance.update(calibrator.provenance())
    if sources:
        provenance["source_urls"] = sources
    return RegionalGuidance(
        toi=TOIGuidance(
            state=GuidanceState.EXPERIMENTAL,
            features=features,
            score=toi_result.score,
            high_risk_probability=toi_result.high_risk_probability,
            method_version=HRRR_TOI_METHOD_VERSION,
            calibration_version=toi_result.calibration_version,
            reason=(
                "experimental public-method reconstruction; official SPC TOI "
                "weights and probability calibration are unpublished"
            ),
        ),
        valid_start=frames[0].valid_time,
        valid_end=frames[-1].valid_time,
        source=HRRR_SOURCE_NAME,
        experimental_not_official=True,
        provenance=provenance,
    )


def _hrrr_search(pressure_level_hpa: int) -> str:
    return (
        rf":(?:UGRD|VGRD):{int(pressure_level_hpa)} mb:"
        r"|:(?:TMP|DPT):2 m above ground:"
        r"|:CAPE:surface:"
        r"|:HLCY:1000-0 m above ground:"
        r"|:(?:VUCSH|VVCSH):0-6000 m above ground:"
    )


def _emit_progress(callback, stage: str, total_bytes: int = 0) -> None:
    if callback is not None:
        callback(str(stage), max(0, int(total_bytes or 0)))


def _cancel_if_requested(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled("regional HRRR guidance download cancelled")


def _message_values(eccodes, gid, shape, slices, regional_mask) -> np.ndarray:
    values = np.asarray(eccodes.codes_get_array(gid, "values"), dtype=float)
    values = values.reshape(shape)[slices].copy()
    try:
        missing = float(eccodes.codes_get(gid, "missingValue"))
    except Exception:
        missing = math.nan
    if math.isfinite(missing):
        values[np.isclose(values, missing, rtol=0.0, atol=0.0)] = np.nan
    values[~regional_mask] = np.nan
    return values


def decode_hrrr_regional_frame(
    path: str | os.PathLike[str],
    *,
    run_time: datetime,
    forecast_hour: int,
    point_latitude: float,
    point_longitude: float,
    pressure_level_hpa: int,
    source_url: str,
    region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
    grid_stride: int = DEFAULT_GRID_STRIDE,
) -> HrrrRegionalFrame:
    """Decode only a downsampled bounded region from a compact HRRR subset."""

    try:
        import eccodes
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("live HRRR guidance requires ecCodes") from exc

    path = Path(path).expanduser().resolve(strict=True)
    fields: dict[str, np.ndarray] = {}
    latitude = longitude = None
    slices = regional_mask = None
    valid_time = None
    shape = None
    with path.open("rb") as handle:
        while True:
            gid = eccodes.codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                ny = int(eccodes.codes_get(gid, "Ny"))
                nx = int(eccodes.codes_get(gid, "Nx"))
                current_shape = (ny, nx)
                if shape is None:
                    shape = current_shape
                    full_latitude = np.asarray(
                        eccodes.codes_get_array(gid, "latitudes"), dtype=float
                    ).reshape(shape)
                    full_longitude = (
                        np.asarray(
                            eccodes.codes_get_array(gid, "longitudes"),
                            dtype=float,
                        ).reshape(shape)
                        + 180.0
                    ) % 360.0 - 180.0
                    distance = _great_circle_grid_km(
                        full_latitude,
                        full_longitude,
                        point_latitude,
                        point_longitude,
                    )
                    inside = np.isfinite(distance) & (
                        distance <= float(region_radius_km)
                    )
                    if not np.any(inside):
                        raise ValueError("sounding point does not intersect HRRR grid")
                    rows, cols = np.where(inside)
                    stride = max(1, int(grid_stride))
                    slices = (
                        slice(int(rows.min()), int(rows.max()) + 1, stride),
                        slice(int(cols.min()), int(cols.max()) + 1, stride),
                    )
                    regional_mask = inside[slices]
                    latitude = full_latitude[slices].copy()
                    longitude = full_longitude[slices].copy()
                elif current_shape != shape:
                    raise ValueError("HRRR subset messages use inconsistent grids")

                short_name = str(eccodes.codes_get(gid, "shortName"))
                level_type = str(eccodes.codes_get(gid, "typeOfLevel"))
                level = int(eccodes.codes_get(gid, "level"))
                field_name = None
                if (
                    short_name in {"u", "v"}
                    and level_type == "isobaricInhPa"
                    and level == int(pressure_level_hpa)
                ):
                    field_name = f"{short_name}_wind"
                elif short_name == "2t" and level_type == "heightAboveGround":
                    field_name = "temperature_2m"
                elif short_name == "2d" and level_type == "heightAboveGround":
                    field_name = "dewpoint_2m"
                elif short_name == "cape" and level_type == "surface":
                    field_name = "surface_cape"
                elif short_name == "hlcy" and level_type == "heightAboveGroundLayer":
                    try:
                        layer_top = int(eccodes.codes_get(gid, "topLevel"))
                        layer_bottom = int(eccodes.codes_get(gid, "bottomLevel"))
                    except Exception:
                        layer_top, layer_bottom = level, 0
                    if {layer_top, layer_bottom} == {0, 1000}:
                        field_name = "srh_1km"
                elif short_name in {"vucsh", "vvcsh"} and level_type == (
                    "heightAboveGroundLayer"
                ):
                    try:
                        layer_top = int(eccodes.codes_get(gid, "topLevel"))
                        layer_bottom = int(eccodes.codes_get(gid, "bottomLevel"))
                    except Exception:
                        layer_top, layer_bottom = 0, 6000
                    if {layer_top, layer_bottom} == {0, 6000}:
                        field_name = "shear_u" if short_name == "vucsh" else "shear_v"
                if field_name is not None:
                    fields[field_name] = _message_values(
                        eccodes, gid, shape, slices, regional_mask
                    )
                    if valid_time is None:
                        date_value = int(eccodes.codes_get(gid, "validityDate"))
                        time_value = int(eccodes.codes_get(gid, "validityTime"))
                        valid_time = datetime.strptime(
                            f"{date_value:08d}{time_value:04d}", "%Y%m%d%H%M"
                        ).replace(tzinfo=timezone.utc)
            finally:
                eccodes.codes_release(gid)

    required = {
        "u_wind",
        "v_wind",
        "temperature_2m",
        "dewpoint_2m",
        "surface_cape",
        "srh_1km",
        "shear_u",
        "shear_v",
    }
    missing = sorted(required.difference(fields))
    if missing:
        raise ValueError("HRRR regional subset is missing: " + ", ".join(missing))
    if valid_time is None or latitude is None or longitude is None:
        raise ValueError("HRRR regional subset contains no usable messages")
    expected_valid = run_time.astimezone(timezone.utc) + timedelta(
        hours=int(forecast_hour)
    )
    if valid_time != expected_valid:
        raise ValueError(
            "HRRR regional valid time does not match requested forecast hour"
        )
    shear_u, shear_v, shear_interpretation = (
        normalize_hrrr_bulk_shear_components(fields["shear_u"], fields["shear_v"])
    )
    return HrrrRegionalFrame(
        run_time=run_time,
        valid_time=valid_time,
        forecast_hour=forecast_hour,
        pressure_level_hpa=pressure_level_hpa,
        latitude=latitude,
        longitude=longitude,
        u_wind_mps=fields["u_wind"],
        v_wind_mps=fields["v_wind"],
        surface_cape_jkg=fields["surface_cape"],
        temperature_2m_k=fields["temperature_2m"],
        dewpoint_2m_k=fields["dewpoint_2m"],
        srh_1km_m2s2=fields["srh_1km"],
        shear_u_6km_mps=shear_u,
        shear_v_6km_mps=shear_v,
        source_url=source_url,
        shear_interpretation=shear_interpretation,
    )


def fetch_hrrr_regional_frame(
    run_time: datetime,
    forecast_hour: int,
    point_latitude: float,
    point_longitude: float,
    pressure_level_hpa: int,
    *,
    download_dir: str | os.PathLike[str] | None = None,
    progress_callback=None,
    cancelled: Callable[[], bool] | None = None,
    region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
    grid_stride: int = DEFAULT_GRID_STRIDE,
) -> HrrrRegionalFrame:
    """Fetch one compact HRRR sfc-field subset and decode a bounded region."""

    # Lazy imports keep the validated guidance contracts usable without the
    # optional GRIB/Herbie stack installed.
    from sharpmod.model_sources import select_herbie_provider
    from sharpmod.model_transport import (
        download_herbie_subset,
        download_herbie_subset_fallback,
        range_worker_count,
    )
    from sharpmod.tools import model_extract

    _cancel_if_requested(cancelled)
    model_extract.require_runtime_dependencies()
    Herbie = model_extract._load_herbie_class()
    H = model_extract._create_herbie(
        Herbie,
        run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        model="hrrr",
        product="sfc",
        fxx=int(forecast_hour),
        verbose=False,
    )
    if H.grib is None:
        raise RuntimeError(f"HRRR F{int(forecast_hour):03d} is unavailable")
    select_herbie_provider(H)
    search = _hrrr_search(pressure_level_hpa)
    inventory = H.inventory(search).copy()
    if len(inventory) < 8:
        raise RuntimeError(
            f"HRRR F{int(forecast_hour):03d} lacks required regional TOI inputs"
        )
    expected_bytes = model_extract._subset_download_bytes(inventory)
    _emit_progress(progress_callback, "regional_downloading", expected_bytes)
    _cancel_if_requested(cancelled)
    try:
        path, _planned = download_herbie_subset(
            H,
            search,
            inventory=inventory,
            save_dir=download_dir,
            cancelled=cancelled,
            workers=range_worker_count(default=4),
        )
        local_path = model_extract._local_grib_path(path)
    except OptimizedTransportUnavailable:
        downloaded, _transferred = download_herbie_subset_fallback(
            H,
            search,
            inventory=inventory,
            save_dir=download_dir,
            cancelled=cancelled,
        )
        local_path = model_extract._local_grib_path(downloaded)
    if local_path is None:
        raise RuntimeError("HRRR regional subset download is incomplete")
    _cancel_if_requested(cancelled)
    _emit_progress(progress_callback, "regional_decoding", expected_bytes)
    return decode_hrrr_regional_frame(
        local_path,
        run_time=run_time,
        forecast_hour=forecast_hour,
        point_latitude=point_latitude,
        point_longitude=point_longitude,
        pressure_level_hpa=pressure_level_hpa,
        source_url=str(H.grib),
        region_radius_km=region_radius_km,
        grid_stride=grid_stride,
    )


def _forecast_window_hours(
    forecast_hour: int, *, window_hours: int = TOI_WINDOW_HOURS
) -> tuple[int, int]:
    forecast_hour = max(0, int(forecast_hour))
    window = max(1, int(window_hours))
    if forecast_hour <= window:
        return 0, window
    return forecast_hour - window, forecast_hour


def toi_sampling_hours(
    forecast_hour: int,
    *,
    interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    window_hours: int = TOI_WINDOW_HOURS,
    maximum_frames: int = TOI_MAXIMUM_FRAMES,
) -> tuple[int, ...]:
    """Plan the bounded forecast-hour sample for one 18-hour TOI window.

    Hours are requested every ``interval_hours`` across the applicable window,
    both window endpoints are always included, the caller's requested forecast
    hour is added, duplicates are removed, and the result is sorted ascending.
    The frame count is capped so a sampling change can never turn into an
    unbounded download plan.
    """

    interval = int(interval_hours)
    if interval < 1:
        raise ValueError("interval_hours must be at least one hour")
    window = int(window_hours)
    if window < interval:
        raise ValueError("window_hours must be at least one sampling interval")
    start, end = _forecast_window_hours(forecast_hour, window_hours=window)
    hours = list(range(start, end + 1, interval))
    hours.append(end)
    hours.append(max(0, int(forecast_hour)))
    ordered = tuple(sorted(dict.fromkeys(hours)))
    if len(ordered) > int(maximum_frames):
        raise ValueError(
            f"TOI sampling plan of {len(ordered)} frames exceeds the bounded "
            f"maximum of {int(maximum_frames)}"
        )
    return ordered


@dataclass(frozen=True)
class TOITemporalSampling:
    """Realized temporal sampling behind one experimental TOI result."""

    requested_hours: tuple[int, ...]
    successful_hours: tuple[int, ...]
    failed_hours: tuple[int, ...]
    interval_hours: int
    coverage_hours: float
    maximum_gap_hours: float
    minimum_coverage_hours: float = TOI_MINIMUM_COVERAGE_HOURS
    minimum_frames: int = TOI_MINIMUM_FRAMES

    def __post_init__(self) -> None:
        for name in ("requested_hours", "successful_hours", "failed_hours"):
            object.__setattr__(
                self, name, tuple(int(hour) for hour in getattr(self, name))
            )
        object.__setattr__(self, "interval_hours", int(self.interval_hours))
        object.__setattr__(self, "minimum_frames", int(self.minimum_frames))
        for name in (
            "coverage_hours",
            "maximum_gap_hours",
            "minimum_coverage_hours",
        ):
            object.__setattr__(self, name, float(getattr(self, name)))

    @property
    def frame_count(self) -> int:
        return len(self.successful_hours)

    @property
    def complete(self) -> bool:
        """True when every requested hour decoded at the planned interval."""

        return not self.failed_hours and not self.degraded_reasons

    @property
    def sufficient(self) -> bool:
        """True when the realized sampling still supports jet tracking."""

        return (
            self.frame_count >= self.minimum_frames
            and self.coverage_hours >= self.minimum_coverage_hours
        )

    @property
    def degraded_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.failed_hours:
            reasons.append(
                "missing forecast hours "
                + ",".join(str(hour) for hour in self.failed_hours)
            )
        if self.maximum_gap_hours > TOI_DEGRADED_GAP_HOURS:
            reasons.append(
                f"largest used gap {self.maximum_gap_hours:g} h exceeds "
                f"{TOI_DEGRADED_GAP_HOURS:g} h"
            )
        if self.coverage_hours < float(TOI_WINDOW_HOURS):
            reasons.append(
                f"time coverage {self.coverage_hours:g} h is shorter than the "
                f"published {TOI_WINDOW_HOURS} h window"
            )
        return tuple(reasons)

    @property
    def insufficient_reason(self) -> str:
        """Return the exact reason this sampling cannot support TOI."""

        if self.frame_count < self.minimum_frames:
            return (
                f"only {self.frame_count} of {len(self.requested_hours)} "
                f"requested HRRR frames decoded; at least "
                f"{self.minimum_frames} are required for jet tracking"
            )
        if self.coverage_hours < self.minimum_coverage_hours:
            return (
                f"decoded frames span only {self.coverage_hours:g} h of the "
                f"{TOI_WINDOW_HOURS} h window; at least "
                f"{self.minimum_coverage_hours:g} h are required for a "
                "jet-translation measurement"
            )
        return ""

    def to_provenance(self) -> dict[str, str]:
        """Return the auditable sampling fields recorded in TOI provenance."""

        provenance = {
            "toi_requested_forecast_hours": ",".join(
                str(hour) for hour in self.requested_hours
            ),
            "toi_successful_forecast_hours": ",".join(
                str(hour) for hour in self.successful_hours
            ),
            "toi_failed_forecast_hours": (
                ",".join(str(hour) for hour in self.failed_hours) or "none"
            ),
            "toi_frame_count": str(self.frame_count),
            "toi_time_coverage_hours": f"{self.coverage_hours:g}",
            "toi_sampling_interval_hours": f"{self.interval_hours:g}",
            "toi_maximum_sampling_gap_hours": f"{self.maximum_gap_hours:g}",
            "toi_sampling_status": "complete" if self.complete else "degraded",
        }
        reasons = self.degraded_reasons
        if reasons:
            provenance["toi_sampling_degraded_reason"] = "; ".join(reasons)
        return provenance


def summarize_toi_sampling(
    requested_hours: tuple[int, ...] | list[int],
    frames: tuple[HrrrRegionalFrame, ...] | list[HrrrRegionalFrame],
    *,
    interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    minimum_coverage_hours: float = TOI_MINIMUM_COVERAGE_HOURS,
    minimum_frames: int = TOI_MINIMUM_FRAMES,
) -> TOITemporalSampling:
    """Summarize which planned hours were used, in valid-time order."""

    requested = tuple(sorted(dict.fromkeys(int(hour) for hour in requested_hours)))
    ordered = tuple(sorted(frames, key=lambda frame: frame.valid_time))
    successful = tuple(frame.forecast_hour for frame in ordered)
    failed = tuple(hour for hour in requested if hour not in set(successful))
    if len(ordered) >= 2:
        span = (
            ordered[-1].valid_time - ordered[0].valid_time
        ).total_seconds() / 3600.0
        gaps = [
            (later.valid_time - earlier.valid_time).total_seconds() / 3600.0
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ]
        maximum_gap = max(gaps)
    else:
        span = 0.0
        maximum_gap = 0.0
    return TOITemporalSampling(
        requested_hours=requested,
        successful_hours=successful,
        failed_hours=failed,
        interval_hours=interval_hours,
        coverage_hours=max(0.0, span),
        maximum_gap_hours=max(0.0, maximum_gap),
        minimum_coverage_hours=minimum_coverage_hours,
        minimum_frames=minimum_frames,
    )


def build_live_hrrr_guidance(
    run_time: datetime,
    forecast_hour: int,
    point_latitude: float,
    point_longitude: float,
    *,
    download_dir: str | os.PathLike[str] | None = None,
    progress_callback=None,
    cancelled: Callable[[], bool] | None = None,
    fetcher=None,
    region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
    grid_stride: int = DEFAULT_GRID_STRIDE,
    sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    calibrator: TOIProbabilityCalibrator | None = None,
) -> RegionalGuidance:
    """Build failure-soft live HRRR feature guidance for a point sounding.

    Frames are requested every ``sampling_interval_hours`` across the applicable
    18-hour window (normally seven frames) plus the requested forecast hour.
    Every successfully decoded frame is used in valid-time order.  Partial
    sampling still produces TOI when enough temporal coverage remains, marked
    ``degraded`` in provenance; otherwise TOI is returned unavailable with the
    exact reason.
    """

    if run_time.tzinfo is None:
        run_time = run_time.replace(tzinfo=timezone.utc)
    else:
        run_time = run_time.astimezone(timezone.utc)
    start_hour, _end_hour = _forecast_window_hours(forecast_hour)
    requested_hours = toi_sampling_hours(
        forecast_hour, interval_hours=sampling_interval_hours
    )
    pressure_level = (
        300
        if (run_time + timedelta(hours=start_hour)).month in {6, 7, 8}
        else 500
    )
    fetch = fetcher or fetch_hrrr_regional_frame
    failures: list[str] = []
    frames: list[HrrrRegionalFrame] = []
    seen_valid_times: set[datetime] = set()
    owned_temp = download_dir is None
    context = (
        tempfile.TemporaryDirectory(prefix="sharpmod-regional-")
        if owned_temp
        else nullcontext(os.fspath(download_dir))
    )
    try:
        with context as active_download_dir:
            # Keep supplemental fields outside the reusable point-GRIB
            # namespace. Otherwise an HRRR Zarr F000 entry (which has no
            # primary GRIB) can be misclassified as reusable solely because
            # one of these regional-only frames is present.
            regional_download_dir = (
                Path(active_download_dir)
                / REGIONAL_GUIDANCE_DOWNLOAD_DIRNAME
            )
            regional_download_dir.mkdir(parents=True, exist_ok=True)
            # One sequential request per planned hour: the sample is denser but
            # the plan stays bounded and concurrency is unchanged.
            for candidate in requested_hours:
                _cancel_if_requested(cancelled)
                try:
                    frame = fetch(
                        run_time,
                        candidate,
                        point_latitude,
                        point_longitude,
                        pressure_level,
                        download_dir=os.fspath(regional_download_dir),
                        progress_callback=progress_callback,
                        cancelled=cancelled,
                        region_radius_km=region_radius_km,
                        grid_stride=grid_stride,
                    )
                except DownloadCancelled:
                    raise
                except Exception as exc:  # failure-soft supplemental product
                    failures.append(
                        f"F{candidate:03d} {type(exc).__name__}: {exc}"
                    )
                    continue
                if frame.valid_time in seen_valid_times:
                    failures.append(
                        f"F{candidate:03d} duplicate valid time "
                        f"{frame.valid_time.isoformat()} discarded"
                    )
                    continue
                seen_valid_times.add(frame.valid_time)
                frames.append(frame)
        sampling = summarize_toi_sampling(
            requested_hours, frames, interval_hours=sampling_interval_hours
        )
        if not sampling.sufficient:
            detail = sampling.insufficient_reason
            if failures:
                detail = f"{detail} ({'; '.join(failures)})"
            return unavailable_hrrr_guidance(
                "live experimental TOI unavailable: " + detail,
                run_time=run_time,
                provenance=sampling.to_provenance(),
            )
        result = build_hrrr_guidance_from_frames(
            frames,
            point_latitude,
            point_longitude,
            region_radius_km=region_radius_km,
            grid_stride=grid_stride,
            sampling=sampling,
            calibrator=calibrator,
        )
        if failures:
            payload = result.to_mapping()
            payload.setdefault("provenance", {})["partial_fetch_failures"] = "; ".join(
                failures
            )
            result = RegionalGuidance.from_mapping(payload)
        return result
    except DownloadCancelled:
        raise
    except Exception as exc:  # failure-soft supplemental product
        return unavailable_hrrr_guidance(
            "live experimental TOI unavailable: "
            f"{type(exc).__name__}: {exc}",
            run_time=run_time,
            provenance={
                "toi_requested_forecast_hours": ",".join(map(str, requested_hours)),
                "toi_frame_count": str(len(frames)),
                "fetch_failures": "; ".join(failures),
            },
        )


__all__ = [
    "DEFAULT_GRID_STRIDE",
    "DEFAULT_REGION_RADIUS_KM",
    "DEFAULT_RISK_STP_THRESHOLD",
    "HRRR_STP_PROXY_VERSION",
    "HRRR_TOI_METHOD_VERSION",
    "HrrrRegionalFrame",
    "ObjectiveRiskRegion",
    "REGIONAL_GUIDANCE_DOWNLOAD_DIRNAME",
    "TOI_DEGRADED_GAP_HOURS",
    "TOI_MAXIMUM_FRAMES",
    "TOI_MINIMUM_COVERAGE_HOURS",
    "TOI_MINIMUM_FRAMES",
    "TOI_SAMPLING_INTERVAL_HOURS",
    "TOI_WINDOW_HOURS",
    "TOITemporalSampling",
    "build_hrrr_guidance_from_frames",
    "build_live_hrrr_guidance",
    "decode_hrrr_regional_frame",
    "fetch_hrrr_regional_frame",
    "fixed_layer_stp_proxy",
    "normalize_hrrr_bulk_shear_components",
    "select_objective_risk_region",
    "summarize_toi_sampling",
    "toi_sampling_hours",
    "unavailable_hrrr_guidance",
]
