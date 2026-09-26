"""Comparison provenance, compatibility, and vertical profile differences.

Scalar diagnostic alignment and visual interpolation are intentionally kept
apart.  The former may be compared only at an exact valid time and under
compatible calculation conventions; the latter is a display aid over the
common observed column and never feeds a parcel or shear calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from types import MappingProxyType
from typing import Iterable, Mapping

import numpy as np

from sharpmod.sharptab.vertical_interpolation import prepare_vertical_interpolator


_PARCEL_KEYS = frozenset(
    {
        "sbcape",
        "sbcin",
        "mlcape",
        "mlcin",
        "mucape",
        "mucin",
        "stp_fixed",
        "stp_cin",
        "scp",
        "ehi_1km",
        "ehi_3km",
    }
)
_STORM_MOTION_KEYS = frozenset(
    {"srh_500m", "srh_1km", "srh_3km", "stp_fixed", "stp_cin", "scp"}
)
_VERTICAL_FIELDS = ("tmpc", "dwpc", "u", "v")


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        try:
            value = collection._meta.get(key, default)
        except (AttributeError, TypeError):
            return default
    return default if value is None else value


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        if np.ma.is_masked(value):
            return None
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > -9998.0 else None


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _values(profile, field: str) -> np.ndarray:
    try:
        array = np.ma.asarray(getattr(profile, field), dtype=float)
    except (AttributeError, TypeError, ValueError):
        return np.asarray([], dtype=float)
    values = np.asarray(array.filled(np.nan), dtype=float).reshape(-1)
    values[~np.isfinite(values)] = np.nan
    values[values <= -9998.0] = np.nan
    return values


def _profile_at(collection, valid_time: datetime | None):
    dates = tuple(getattr(collection, "_dates", ()) or ())
    if valid_time is None:
        index = int(getattr(collection, "_prof_idx", 0) or 0)
    else:
        try:
            index = dates.index(valid_time)
        except ValueError:
            return None, None, None
    if not 0 <= index < len(dates):
        return None, None, None
    profiles = getattr(collection, "_profs", {}) or {}
    member = getattr(collection, "_highlight", None)
    if member not in profiles:
        member = next(iter(profiles), None)
    if member is None:
        return index, None, None
    try:
        profile = profiles[member][index]
    except (IndexError, KeyError, TypeError):
        profile = None
    return index, str(member), profile


def _terrain(profile) -> float | None:
    if profile is None:
        return None
    pressure = _values(profile, "pres")
    height = _values(profile, "hght")
    size = min(pressure.size, height.size)
    valid = np.flatnonzero(
        np.isfinite(pressure[:size]) & np.isfinite(height[:size])
    )
    if not valid.size:
        return None
    index = int(valid[np.argmax(pressure[valid])])
    return float(height[index])


def _grid_spacing(collection) -> float | None:
    stored = _finite(_meta(collection, "grid_spacing_km"))
    if stored is not None and stored > 0.0:
        return stored
    model_key = _meta(collection, "model_key")
    if not model_key:
        return None
    try:
        from sharpmod.tools.model_extract import grid_spacing_km

        spacing = float(grid_spacing_km(str(model_key)))
    except Exception:
        return None
    return spacing if math.isfinite(spacing) and spacing > 0.0 else None


def _coordinates(collection, prefix: str) -> tuple[float | None, float | None]:
    latitude = _finite(_meta(collection, f"{prefix}_lat"))
    longitude = _finite(_meta(collection, f"{prefix}_lon"))
    if prefix == "requested" and latitude is None:
        latitude = _finite(_meta(collection, "lat"))
    if prefix == "requested" and longitude is None:
        longitude = _finite(_meta(collection, "lon"))
    if prefix == "selected" and latitude is None:
        latitude = _finite(_meta(collection, "lat"))
    if prefix == "selected" and longitude is None:
        longitude = _finite(_meta(collection, "lon"))
    if latitude is not None and not -90.0 <= latitude <= 90.0:
        latitude = None
    if longitude is not None and not -180.0 <= longitude <= 360.0:
        longitude = None
    if longitude is not None:
        longitude = ((longitude + 180.0) % 360.0) - 180.0
    return latitude, longitude


def _edited(collection, index: int | None) -> bool | None:
    if index is None:
        return None
    flags = []
    for name in ("_mod_therm", "_mod_wind", "_interp"):
        try:
            flags.append(bool(getattr(collection, name)[index]))
        except (AttributeError, IndexError, TypeError):
            pass
    return bool(_meta(collection, "profile_edited", False) or any(flags))


@dataclass(frozen=True)
class ComparisonContext:
    """Everything needed to explain what one comparison row represents."""

    collection_index: int
    label: str
    requested_time: datetime | None
    selected_time: datetime | None
    member: str | None
    requested_lat: float | None
    requested_lon: float | None
    selected_lat: float | None
    selected_lon: float | None
    grid_spacing_km: float | None
    terrain_elevation_m: float | None
    run_time: datetime | None
    lead_hours: int | None
    parcel_convention: str | None
    storm_motion_convention: str | None
    profile_edited: bool | None
    def as_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class CompatibilityIssue:
    field: str
    severity: str
    message: str
    reference: object = None
    candidate: object = None


@dataclass(frozen=True)
class ComparisonBasis:
    """Relationship between a candidate and the selected reference."""

    location_relation: str
    issues: tuple[CompatibilityIssue, ...]
    incompatible_metrics: Mapping[str, str]

    @property
    def compatible(self) -> bool:
        return not any(item.severity == "error" for item in self.issues)


@dataclass(frozen=True)
class VerticalDifferenceLevel:
    height_msl_m: float
    pressure_hpa: float | None
    temperature_c: float | None
    dewpoint_c: float | None
    u_wind_kt: float | None
    v_wind_kt: float | None


@dataclass(frozen=True)
class VerticalDifference:
    reference_label: str
    candidate_label: str
    levels: tuple[VerticalDifferenceLevel, ...]
    coverage_bottom_m: float | None
    coverage_top_m: float | None
    reason: str | None = None


def context_for_collection(
    collection,
    collection_index: int,
    *,
    requested_time: datetime | None,
    selected_time: datetime | None = None,
    label: str | None = None,
) -> ComparisonContext:
    """Extract a comparison context without changing collection selection."""
    exact_time = selected_time if selected_time is not None else requested_time
    index, member, profile = _profile_at(collection, exact_time)
    requested_lat, requested_lon = _coordinates(collection, "requested")
    selected_lat, selected_lon = _coordinates(collection, "selected")
    lead = _meta(collection, "fxx")
    try:
        lead = int(lead) if lead is not None else None
    except (TypeError, ValueError):
        lead = None
    stored_terrain = _finite(_meta(collection, "terrain_elevation_m"))
    return ComparisonContext(
        collection_index=int(collection_index),
        label=str(label or _meta(collection, "model") or _meta(collection, "loc") or f"Sounding {int(collection_index) + 1}"),
        requested_time=_time(requested_time),
        selected_time=_time(exact_time),
        member=member,
        requested_lat=requested_lat,
        requested_lon=requested_lon,
        selected_lat=selected_lat,
        selected_lon=selected_lon,
        grid_spacing_km=_grid_spacing(collection),
        terrain_elevation_m=(
            stored_terrain if stored_terrain is not None else _terrain(profile)
        ),
        run_time=_time(_meta(collection, "run")),
        lead_hours=lead,
        parcel_convention=(
            str(_meta(collection, "parcel_convention"))
            if _meta(collection, "parcel_convention") not in (None, "")
            else None
        ),
        storm_motion_convention=(
            str(_meta(collection, "storm_motion_convention"))
            if _meta(collection, "storm_motion_convention") not in (None, "")
            else None
        ),
        profile_edited=_edited(collection, index),
    )


def _distance_km(left: ComparisonContext, right: ComparisonContext, prefix: str):
    lat1 = getattr(left, f"{prefix}_lat")
    lon1 = getattr(left, f"{prefix}_lon")
    lat2 = getattr(right, f"{prefix}_lat")
    lon2 = getattr(right, f"{prefix}_lon")
    if None in (lat1, lon1, lat2, lon2):
        return None
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(((lon2 - lon1 + 180.0) % 360.0) - 180.0)
    term = math.sin(dphi / 2.0) ** 2 + (
        math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 6371.0088 * 2.0 * math.asin(min(1.0, math.sqrt(term)))


def assess_compatibility(
    reference: ComparisonContext,
    candidate: ComparisonContext,
    metric_keys: Iterable[str] = (),
) -> ComparisonBasis:
    """Explain compatibility while keeping deliberate spatial rows usable."""
    issues = []
    if reference.selected_time is None or candidate.selected_time is None:
        issues.append(
            CompatibilityIssue(
                "valid_time", "error", "valid time is unknown", reference.selected_time, candidate.selected_time
            )
        )
    elif reference.selected_time != candidate.selected_time:
        issues.append(
            CompatibilityIssue(
                "valid_time",
                "error",
                "no exact shared valid time; nearest-time substitution is disabled",
                reference.selected_time,
                candidate.selected_time,
            )
        )

    requested_distance = _distance_km(reference, candidate, "requested")
    if requested_distance is None:
        location_relation = "unknown"
        issues.append(
            CompatibilityIssue(
                "requested_coordinates",
                "unknown",
                "requested coordinates are unavailable for one or both soundings",
            )
        )
    elif requested_distance <= 0.1:
        location_relation = "same-location"
    else:
        location_relation = "intentional-spatial"
        issues.append(
            CompatibilityIssue(
                "requested_coordinates",
                "info",
                f"intentional spatial comparison: requested points differ by {requested_distance:.1f} km",
                (reference.requested_lat, reference.requested_lon),
                (candidate.requested_lat, candidate.requested_lon),
            )
        )

    selected_distance = _distance_km(reference, candidate, "selected")
    if selected_distance is None:
        issues.append(
            CompatibilityIssue(
                "selected_coordinates",
                "unknown",
                "selected grid coordinates are unavailable for one or both soundings",
            )
        )
    elif selected_distance > 0.1:
        issues.append(
            CompatibilityIssue(
                "selected_coordinates",
                "info" if location_relation == "intentional-spatial" else "warning",
                f"selected source points differ by {selected_distance:.1f} km",
                (reference.selected_lat, reference.selected_lon),
                (candidate.selected_lat, candidate.selected_lon),
            )
        )

    if reference.terrain_elevation_m is None or candidate.terrain_elevation_m is None:
        issues.append(
            CompatibilityIssue(
                "terrain_elevation_m",
                "unknown",
                "terrain elevation is unavailable for one or both soundings",
            )
        )
    else:
        terrain_delta = candidate.terrain_elevation_m - reference.terrain_elevation_m
        if abs(terrain_delta) >= 25.0:
            issues.append(
                CompatibilityIssue(
                    "terrain_elevation_m",
                    "info" if location_relation == "intentional-spatial" else "warning",
                    f"terrain differs by {terrain_delta:+.0f} m",
                    reference.terrain_elevation_m,
                    candidate.terrain_elevation_m,
                )
            )

    for field, label in (
        ("grid_spacing_km", "grid spacing"),
        ("parcel_convention", "parcel convention"),
        ("storm_motion_convention", "storm-motion convention"),
    ):
        left = getattr(reference, field)
        right = getattr(candidate, field)
        if left is None or right is None:
            issues.append(
                CompatibilityIssue(
                    field,
                    "unknown",
                    f"{label} is unknown for one or both soundings",
                    left,
                    right,
                )
            )
        elif left != right:
            issues.append(
                CompatibilityIssue(
                    field,
                    "warning" if field == "grid_spacing_km" else "error",
                    f"{label} differs",
                    left,
                    right,
                )
            )

    incompatible = {}
    keys = tuple(str(item) for item in metric_keys)
    if (
        reference.parcel_convention is not None
        and candidate.parcel_convention is not None
        and reference.parcel_convention != candidate.parcel_convention
    ):
        for key in keys:
            if key in _PARCEL_KEYS:
                incompatible[key] = "parcel conventions differ"
    if (
        reference.storm_motion_convention is not None
        and candidate.storm_motion_convention is not None
        and reference.storm_motion_convention != candidate.storm_motion_convention
    ):
        for key in keys:
            if key in _STORM_MOTION_KEYS:
                incompatible[key] = "storm-motion conventions differ"
    return ComparisonBasis(
        location_relation,
        tuple(issues),
        MappingProxyType(incompatible),
    )


def _interpolate_at(
    heights: np.ndarray,
    values: np.ndarray,
    target: float,
    *,
    max_gap_m: float,
) -> float | None:
    return prepare_vertical_interpolator(
        heights,
        values,
        max_gap_m=max_gap_m,
    )(target)


def vertical_profile_difference(
    reference_profile,
    candidate_profile,
    *,
    reference_label: str = "Reference",
    candidate_label: str = "Candidate",
    max_gap_m: float = 1500.0,
) -> VerticalDifference:
    """Return candidate-minus-reference values on reference height levels.

    Each field is interpolated independently, only between real bracketing
    levels, and gaps wider than ``max_gap_m`` remain missing.  These values are
    for the vertical-difference display only.
    """
    if reference_profile is None or candidate_profile is None:
        return VerticalDifference(
            str(reference_label),
            str(candidate_label),
            (),
            None,
            None,
            "one or both profiles are unavailable",
        )
    reference_height = _values(reference_profile, "hght")
    candidate_height = _values(candidate_profile, "hght")
    finite_candidate = candidate_height[np.isfinite(candidate_height)]
    finite_reference = reference_height[np.isfinite(reference_height)]
    if not finite_candidate.size or not finite_reference.size:
        return VerticalDifference(
            str(reference_label),
            str(candidate_label),
            (),
            None,
            None,
            "one or both profiles have no valid heights",
        )
    bottom = max(float(np.min(finite_reference)), float(np.min(finite_candidate)))
    top = min(float(np.max(finite_reference)), float(np.max(finite_candidate)))
    if top < bottom:
        return VerticalDifference(
            str(reference_label),
            str(candidate_label),
            (),
            None,
            None,
            "profiles have no common vertical coverage",
        )

    reference_columns = {field: _values(reference_profile, field) for field in _VERTICAL_FIELDS}
    candidate_columns = {field: _values(candidate_profile, field) for field in _VERTICAL_FIELDS}
    candidate_interpolators = {
        field: prepare_vertical_interpolator(
            candidate_height,
            candidate_columns[field],
            max_gap_m=max_gap_m,
        )
        for field in _VERTICAL_FIELDS
    }
    pressure = _values(reference_profile, "pres")
    rows = []
    for index, height in enumerate(reference_height):
        if not math.isfinite(height) or not bottom <= height <= top:
            continue
        differences = {}
        for field in _VERTICAL_FIELDS:
            ref_values = reference_columns[field]
            reference_value = (
                float(ref_values[index])
                if index < ref_values.size and math.isfinite(ref_values[index])
                else None
            )
            candidate_value = candidate_interpolators[field](float(height))
            differences[field] = (
                candidate_value - reference_value
                if candidate_value is not None and reference_value is not None
                else None
            )
        rows.append(
            VerticalDifferenceLevel(
                float(height),
                (
                    float(pressure[index])
                    if index < pressure.size and math.isfinite(pressure[index])
                    else None
                ),
                differences["tmpc"],
                differences["dwpc"],
                differences["u"],
                differences["v"],
            )
        )
    return VerticalDifference(
        str(reference_label),
        str(candidate_label),
        tuple(rows),
        bottom,
        top,
        None if rows else "no common reference levels are usable",
    )


def exact_profile(collection, valid_time: datetime | None):
    """Return the selected member's exact-time profile, never a nearby time."""
    _index, _member_name, profile = _profile_at(collection, valid_time)
    return profile


def rendered_profile(collection, valid_time: datetime | None):
    """Return the exact-time profile with the derived fields a chart needs.

    A decoded collection stores plain ``Profile`` objects and upgrades one to the
    collection's target type (normally ``ConvectiveProfile``) only when
    ``getHighlightedProf`` is asked for it -- and that asks for whichever time the
    collection is currently sitting on, which is not necessarily the comparison's
    reference time. Reading ``_profs`` directly, as :func:`exact_profile` does,
    therefore hands back a profile with no ``vtmp``, ``wetbulb``, ``srwind`` or
    ``mean_lcl_el``, which the scientific renderers require.

    This performs the same upgrade the collection performs, at the right index,
    and caches it back into the collection so the derivation happens once and the
    chart and the rest of the application share one object. Scalar differences
    keep using :func:`exact_profile`; nothing here changes their inputs.

    Returns ``None`` when there is no profile at that time, and returns the
    original profile unchanged when it cannot be upgraded, leaving the caller to
    report which chart is unavailable.
    """
    index, member, profile = _profile_at(collection, valid_time)
    if profile is None:
        return None
    target = getattr(collection, "_target_type", None)
    if target is None or isinstance(profile, target) and type(profile) is target:
        return profile
    profiles = getattr(collection, "_profs", None)
    try:
        upgraded = target.copy(profile)
    except Exception:  # noqa: BLE001 - an incomplete profile stays as it is
        return profile
    if isinstance(profiles, dict) and member in profiles:
        try:
            profiles[member][index] = upgraded
        except (IndexError, KeyError, TypeError):
            pass
    return upgraded


__all__ = [
    "ComparisonBasis",
    "ComparisonContext",
    "CompatibilityIssue",
    "VerticalDifference",
    "VerticalDifferenceLevel",
    "assess_compatibility",
    "context_for_collection",
    "exact_profile",
    "rendered_profile",
    "vertical_profile_difference",
]
