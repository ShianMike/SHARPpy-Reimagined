"""Conservative forecast-versus-observed sounding verification.

Forecast classification, exact-time matching, spatial criteria, vertical
coverage, and interpolation bounds are explicit.  Analyses/reanalyses never
enter a forecast aggregate, and no field is extrapolated beyond observed or
forecast support.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Iterable, Mapping

import numpy as np

from sharpmod.vertical_interpolation import prepare_vertical_interpolator


DEFAULT_VERIFICATION_METRICS = ("mlcape", "shear_6km")
_VERTICAL_FIELDS = ("temperature_c", "dewpoint_c", "u_wind_kt", "v_wind_kt")


class VerificationError(ValueError):
    """A pair violates the declared forecast/observation matching rules."""


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value is None else value


def _finite(value) -> float | None:
    try:
        if value is None or np.ma.is_masked(value):
            return None
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > -9998.0 else None


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value) -> str:
    value = _time(value)
    return value.isoformat().replace("+00:00", "Z") if value else ""


def _column(profile, name: str) -> np.ndarray:
    try:
        array = np.ma.asarray(getattr(profile, name), dtype=float).reshape(-1)
    except (AttributeError, TypeError, ValueError):
        return np.asarray([], dtype=float)
    result = np.asarray(array.filled(np.nan), dtype=float)
    result[~np.isfinite(result) | (result <= -9998.0)] = np.nan
    return result


def _profile(collection):
    dates = tuple(getattr(collection, "_dates", ()) or ())
    index = int(getattr(collection, "_prof_idx", 0) or 0)
    if not 0 <= index < len(dates):
        return None, None, None
    profiles = getattr(collection, "_profs", {}) or {}
    member = getattr(collection, "_highlight", None)
    if member not in profiles:
        member = next(iter(profiles), None)
    if member is None:
        return dates[index], None, None
    try:
        sounding = profiles[member][index]
    except (IndexError, KeyError, TypeError):
        sounding = None
    return dates[index], str(member), sounding


def _location(collection, profile) -> tuple[float | None, float | None]:
    latitude = _finite(_meta(collection, "selected_lat"))
    longitude = _finite(_meta(collection, "selected_lon"))
    if latitude is None:
        latitude = _finite(_meta(collection, "lat"))
    if longitude is None:
        longitude = _finite(_meta(collection, "lon"))
    if latitude is None:
        latitude = _finite(getattr(profile, "latitude", None))
    if longitude is not None:
        longitude = ((longitude + 180.0) % 360.0) - 180.0
    return latitude, longitude


def _distance_km(left, right) -> float | None:
    if None in (*left, *right):
        return None
    lat1, lon1 = left
    lat2, lon2 = right
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlon = math.radians(((lon2 - lon1 + 180.0) % 360.0) - 180.0)
    term = math.sin(dphi / 2.0) ** 2 + (
        math.cos(phi1) * math.cos(phi2) * math.sin(dlon / 2.0) ** 2
    )
    return 6371.0088 * 2.0 * math.asin(min(1.0, math.sqrt(term)))


def _terrain(profile) -> float | None:
    pressure = _column(profile, "pres")
    height = _column(profile, "hght")
    size = min(pressure.size, height.size)
    valid = np.flatnonzero(np.isfinite(pressure[:size]) & np.isfinite(height[:size]))
    if not valid.size:
        return None
    return float(height[int(valid[np.argmax(pressure[valid])])])


def _interpolate(
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


@dataclass(frozen=True)
class VerificationRules:
    station_id: str | None = None
    valid_time_tolerance: timedelta = timedelta(0)
    max_distance_km: float = 50.0
    max_vertical_gap_m: float = 750.0
    as_of: datetime | None = None

    def __post_init__(self):
        tolerance = self.valid_time_tolerance
        if not isinstance(tolerance, timedelta) or tolerance < timedelta(0):
            raise VerificationError("valid-time tolerance must be non-negative")
        if tolerance > timedelta(hours=3):
            raise VerificationError("valid-time tolerance cannot exceed three hours")
        if not 0.0 <= float(self.max_distance_km) <= 1000.0:
            raise VerificationError("maximum distance must be within 0-1000 km")
        if not 1.0 <= float(self.max_vertical_gap_m) <= 3000.0:
            raise VerificationError("vertical interpolation gap must be 1-3000 m")
        if self.station_id is not None:
            station = str(self.station_id).strip().upper()
            if not station:
                raise VerificationError("station id must not be empty")
            object.__setattr__(self, "station_id", station)
        object.__setattr__(self, "as_of", _time(self.as_of))

    def as_dict(self) -> dict[str, object]:
        return {
            "station_id": self.station_id,
            "valid_time_tolerance_seconds": self.valid_time_tolerance.total_seconds(),
            "max_distance_km": self.max_distance_km,
            "max_vertical_gap_m": self.max_vertical_gap_m,
            "as_of": _iso(self.as_of),
        }


@dataclass(frozen=True)
class ProfileIdentity:
    kind: str
    label: str
    source: str
    station_id: str | None
    latitude: float | None
    longitude: float | None
    valid_time: datetime | None
    initialization_time: datetime | None
    available_time: datetime | None
    lead_hours: int | None
    terrain_elevation_m: float | None
    member: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "valid_time": _iso(self.valid_time),
            "initialization_time": _iso(self.initialization_time),
            "available_time": _iso(self.available_time),
        }


@dataclass(frozen=True)
class VerificationLevel:
    height_msl_m: float
    observed_height_agl_m: float | None
    observed_pressure_hpa: float | None
    temperature_c: float | None
    dewpoint_c: float | None
    u_wind_kt: float | None
    v_wind_kt: float | None


@dataclass(frozen=True)
class DiagnosticError:
    key: str
    forecast: float | None
    observed: float | None
    error: float | None
    reason: str | None = None


@dataclass(frozen=True)
class VerificationPair:
    forecast: ProfileIdentity
    observation: ProfileIdentity
    rules: VerificationRules
    matched: bool
    levels: tuple[VerificationLevel, ...]
    diagnostics: tuple[DiagnosticError, ...]
    rejection_reasons: tuple[str, ...]
    distance_km: float | None
    observed_coverage_bottom_m: float | None
    observed_coverage_top_m: float | None

    @property
    def lead_hours(self) -> int | None:
        return self.forecast.lead_hours

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "sharpmod-verification-pair",
            "version": 1,
            "forecast": self.forecast.as_dict(),
            "observation": self.observation.as_dict(),
            "rules": self.rules.as_dict(),
            "matched": self.matched,
            "distance_km": self.distance_km,
            "observed_coverage_bottom_m": self.observed_coverage_bottom_m,
            "observed_coverage_top_m": self.observed_coverage_top_m,
            "rejection_reasons": list(self.rejection_reasons),
            "levels": [item.__dict__ for item in self.levels],
            "diagnostics": [item.__dict__ for item in self.diagnostics],
        }


@dataclass(frozen=True)
class AggregateMetric:
    count: int
    bias: float | None
    mae: float | None
    rmse: float | None


@dataclass(frozen=True)
class VerificationAggregate:
    model: str
    lead_hours: int | None
    matched_pair_count: int
    rejected_pair_count: int
    vertical: Mapping[str, AggregateMetric]
    diagnostics: Mapping[str, AggregateMetric]
    matching_rule_summary: str


def identity_for_collection(collection) -> ProfileIdentity:
    valid, member, profile = _profile(collection)
    observed = bool(_meta(collection, "observed", False))
    reanalysis = bool(_meta(collection, "reanalysis", False))
    analysis = bool(_meta(collection, "analysis", False))
    kind = (
        "observation"
        if observed
        else "reanalysis"
        if reanalysis
        else "analysis"
        if analysis
        else "forecast"
    )
    run_time = _time(_meta(collection, "run"))
    lead = _meta(collection, "fxx")
    try:
        lead = int(lead) if lead is not None else None
    except (TypeError, ValueError):
        lead = None
    available = _time(
        _meta(collection, "available_time", _meta(collection, "retrieved_at"))
    )
    if available is None and kind == "forecast":
        available = run_time
    latitude, longitude = _location(collection, profile)
    station = _meta(
        collection,
        "source_station",
        _meta(collection, "requested_station", _meta(collection, "station_id")),
    )
    return ProfileIdentity(
        kind,
        str(_meta(collection, "model", _meta(collection, "loc", kind))),
        str(
            _meta(collection, "source_provider", _meta(collection, "model", "unknown"))
        ),
        str(station).strip().upper() if station not in (None, "") else None,
        latitude,
        longitude,
        _time(valid),
        run_time,
        available,
        lead,
        (
            stored_terrain
            if (stored_terrain := _finite(_meta(collection, "terrain_elevation_m")))
            is not None
            else _terrain(profile)
        ),
        member,
    )


def verify_pair(
    forecast_collection,
    observation_collection,
    engine,
    *,
    metric_keys: Iterable[str] = DEFAULT_VERIFICATION_METRICS,
    rules: VerificationRules | None = None,
) -> VerificationPair:
    """Match and verify one current forecast/observation pair end to end."""
    rules = rules or VerificationRules()
    forecast = identity_for_collection(forecast_collection)
    observation = identity_for_collection(observation_collection)
    reasons = []
    if forecast.kind != "forecast":
        reasons.append(
            f"candidate forecast is classified as {forecast.kind}, not forecast"
        )
    if observation.kind != "observation":
        reasons.append(
            f"verification target is classified as {observation.kind}, not observation"
        )
    if forecast.valid_time is None or observation.valid_time is None:
        reasons.append("one or both valid times are unknown")
    elif abs(forecast.valid_time - observation.valid_time) > rules.valid_time_tolerance:
        reasons.append(
            "valid times do not satisfy the declared tolerance "
            f"({rules.valid_time_tolerance.total_seconds():g} s)"
        )
    if rules.station_id and observation.station_id != rules.station_id:
        reasons.append(
            f"observation station {observation.station_id or 'unknown'} does not "
            f"match requested {rules.station_id}"
        )
    distance = _distance_km(
        (forecast.latitude, forecast.longitude),
        (observation.latitude, observation.longitude),
    )
    if distance is None:
        reasons.append("forecast/observation coordinates are incomplete")
    elif distance > rules.max_distance_km:
        reasons.append(
            f"forecast point is {distance:.1f} km from the observation, beyond "
            f"the {rules.max_distance_km:g} km rule"
        )
    if forecast.initialization_time is None:
        reasons.append("forecast initialization time is unknown")
    elif forecast.valid_time and forecast.initialization_time > forecast.valid_time:
        reasons.append("forecast initialization is later than its valid time")
    if (
        rules.as_of is not None
        and forecast.available_time is not None
        and forecast.available_time > rules.as_of
    ):
        reasons.append("forecast was not available by the declared as-of time")
    if (
        rules.as_of is not None
        and forecast.initialization_time is not None
        and forecast.initialization_time > rules.as_of
    ):
        reasons.append("forecast run initialized after the declared as-of time")

    _forecast_valid, _forecast_member, forecast_profile = _profile(forecast_collection)
    _observed_valid, _observed_member, observed_profile = _profile(
        observation_collection
    )
    if forecast_profile is None or observed_profile is None:
        reasons.append("one or both selected profiles are unavailable")

    levels = ()
    coverage_bottom = coverage_top = None
    if not reasons:
        forecast_height = _column(forecast_profile, "hght")
        observed_height = _column(observed_profile, "hght")
        observed_valid_heights = observed_height[np.isfinite(observed_height)]
        if not observed_valid_heights.size:
            reasons.append("observation has no valid height coverage")
        else:
            coverage_bottom = float(np.min(observed_valid_heights))
            coverage_top = float(np.max(observed_valid_heights))
            common_bottom = max(
                value
                for value in (
                    forecast.terrain_elevation_m,
                    observation.terrain_elevation_m,
                    coverage_bottom,
                )
                if value is not None
            )
            observed_pressure = _column(observed_profile, "pres")
            rows = []
            source_fields = {
                "temperature_c": "tmpc",
                "dewpoint_c": "dwpc",
                "u_wind_kt": "u",
                "v_wind_kt": "v",
            }
            observed_columns = {
                result_name: _column(observed_profile, profile_name)
                for result_name, profile_name in source_fields.items()
            }
            forecast_interpolators = {
                result_name: prepare_vertical_interpolator(
                    forecast_height,
                    _column(forecast_profile, profile_name),
                    max_gap_m=rules.max_vertical_gap_m,
                )
                for result_name, profile_name in source_fields.items()
            }
            for index, height in enumerate(observed_height):
                if not math.isfinite(height) or height < common_bottom:
                    continue
                differences = {}
                for result_name in source_fields:
                    observed_values = observed_columns[result_name]
                    observed_value = (
                        float(observed_values[index])
                        if index < observed_values.size
                        and math.isfinite(observed_values[index])
                        else None
                    )
                    forecast_value = forecast_interpolators[result_name](float(height))
                    differences[result_name] = (
                        forecast_value - observed_value
                        if forecast_value is not None and observed_value is not None
                        else None
                    )
                rows.append(
                    VerificationLevel(
                        float(height),
                        (
                            float(height - observation.terrain_elevation_m)
                            if observation.terrain_elevation_m is not None
                            else None
                        ),
                        (
                            float(observed_pressure[index])
                            if index < observed_pressure.size
                            and math.isfinite(observed_pressure[index])
                            else None
                        ),
                        **differences,
                    )
                )
            levels = tuple(rows)
            if not levels:
                reasons.append(
                    "profiles have no conservatively matched vertical levels"
                )

    wanted = tuple(dict.fromkeys(str(key) for key in metric_keys))
    diagnostics = []
    if not reasons:
        try:
            forecast_values = engine.values(forecast_profile, wanted)
            observed_values = engine.values(observed_profile, wanted)
        except Exception as exc:  # noqa: BLE001 - profile backend boundary
            diagnostics = [
                DiagnosticError(key, None, None, None, str(exc)) for key in wanted
            ]
        else:
            for key in wanted:
                forecast_value = _finite(forecast_values.get(key))
                observed_value = _finite(observed_values.get(key))
                diagnostics.append(
                    DiagnosticError(
                        key,
                        forecast_value,
                        observed_value,
                        (
                            forecast_value - observed_value
                            if forecast_value is not None and observed_value is not None
                            else None
                        ),
                        (
                            None
                            if forecast_value is not None and observed_value is not None
                            else "diagnostic is unavailable for one or both profiles"
                        ),
                    )
                )
    return VerificationPair(
        forecast,
        observation,
        rules,
        not reasons,
        levels,
        tuple(diagnostics),
        tuple(reasons),
        distance,
        coverage_bottom,
        coverage_top,
    )


def _aggregate(values: Iterable[float | None]) -> AggregateMetric:
    finite = np.asarray(
        [value for value in values if value is not None and math.isfinite(value)],
        dtype=float,
    )
    if not finite.size:
        return AggregateMetric(0, None, None, None)
    return AggregateMetric(
        int(finite.size),
        float(np.mean(finite)),
        float(np.mean(np.abs(finite))),
        float(np.sqrt(np.mean(np.square(finite)))),
    )


def aggregate_verification(
    pairs: Iterable[VerificationPair],
) -> tuple[VerificationAggregate, ...]:
    """Aggregate valid cases by model and forecast lead with sample counts."""
    groups: dict[tuple[str, int | None], list[VerificationPair]] = {}
    rejected: dict[tuple[str, int | None], int] = {}
    for pair in pairs:
        key = (pair.forecast.label, pair.forecast.lead_hours)
        if pair.matched:
            groups.setdefault(key, []).append(pair)
        else:
            rejected[key] = rejected.get(key, 0) + 1
            groups.setdefault(key, [])
    results = []
    for (model, lead), matched in sorted(
        groups.items(),
        key=lambda item: (item[0][0], item[0][1] is None, item[0][1] or 0),
    ):
        vertical = MappingProxyType(
            {
                field: _aggregate(
                    getattr(level, field) for pair in matched for level in pair.levels
                )
                for field in _VERTICAL_FIELDS
            }
        )
        diagnostic_keys = tuple(
            dict.fromkeys(item.key for pair in matched for item in pair.diagnostics)
        )
        diagnostics = MappingProxyType(
            {
                key: _aggregate(
                    item.error
                    for pair in matched
                    for item in pair.diagnostics
                    if item.key == key
                )
                for key in diagnostic_keys
            }
        )
        if matched:
            rule = matched[0].rules
            summary = (
                f"station={rule.station_id or 'explicit coordinates'}; "
                f"time tolerance={rule.valid_time_tolerance.total_seconds():g}s; "
                f"distance≤{rule.max_distance_km:g}km; "
                f"vertical gap≤{rule.max_vertical_gap_m:g}m"
            )
        else:
            summary = "no valid pair supplied matching rules"
        results.append(
            VerificationAggregate(
                model,
                lead,
                len(matched),
                rejected.get((model, lead), 0),
                vertical,
                diagnostics,
                summary,
            )
        )
    return tuple(results)


def export_verification_csv(path, pairs: Iterable[VerificationPair]) -> Path:
    """Atomically export pair provenance, vertical errors, and diagnostics."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    fields = (
        "record_type",
        "model",
        "lead_hours",
        "forecast_run",
        "forecast_valid",
        "observation_source",
        "station",
        "observation_valid",
        "matched",
        "rejection_reason",
        "distance_km",
        "height_msl_m",
        "height_agl_m",
        "pressure_hpa",
        "field",
        "forecast_minus_observed",
        "sample_reason",
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for pair in pairs:
                common = {
                    "model": pair.forecast.label,
                    "lead_hours": pair.forecast.lead_hours,
                    "forecast_run": _iso(pair.forecast.initialization_time),
                    "forecast_valid": _iso(pair.forecast.valid_time),
                    "observation_source": pair.observation.source,
                    "station": pair.observation.station_id or "",
                    "observation_valid": _iso(pair.observation.valid_time),
                    "matched": pair.matched,
                    "rejection_reason": "; ".join(pair.rejection_reasons),
                    "distance_km": pair.distance_km,
                }
                if not pair.levels and not pair.diagnostics:
                    writer.writerow({"record_type": "pair", **common})
                for level in pair.levels:
                    for field in _VERTICAL_FIELDS:
                        writer.writerow(
                            {
                                "record_type": "vertical",
                                **common,
                                "height_msl_m": level.height_msl_m,
                                "height_agl_m": level.observed_height_agl_m,
                                "pressure_hpa": level.observed_pressure_hpa,
                                "field": field,
                                "forecast_minus_observed": getattr(level, field),
                            }
                        )
                for diagnostic in pair.diagnostics:
                    writer.writerow(
                        {
                            "record_type": "diagnostic",
                            **common,
                            "field": diagnostic.key,
                            "forecast_minus_observed": diagnostic.error,
                            "sample_reason": diagnostic.reason or "",
                        }
                    )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(handle)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _identity_from_dict(payload: Mapping[str, object]) -> ProfileIdentity:
    return ProfileIdentity(
        kind=str(payload.get("kind", "unknown")),
        label=str(payload.get("label", "unknown")),
        source=str(payload.get("source", "unknown")),
        station_id=(str(payload["station_id"]) if payload.get("station_id") else None),
        latitude=_finite(payload.get("latitude")),
        longitude=_finite(payload.get("longitude")),
        valid_time=_time(payload.get("valid_time")),
        initialization_time=_time(payload.get("initialization_time")),
        available_time=_time(payload.get("available_time")),
        lead_hours=(
            int(payload["lead_hours"])
            if payload.get("lead_hours") is not None
            else None
        ),
        terrain_elevation_m=_finite(payload.get("terrain_elevation_m")),
        member=str(payload["member"]) if payload.get("member") is not None else None,
    )


def _rules_from_dict(payload: Mapping[str, object]) -> VerificationRules:
    try:
        tolerance = timedelta(
            seconds=float(payload.get("valid_time_tolerance_seconds", 0.0))
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise VerificationError("saved valid-time tolerance is invalid") from exc
    return VerificationRules(
        station_id=(str(payload["station_id"]) if payload.get("station_id") else None),
        valid_time_tolerance=tolerance,
        max_distance_km=float(payload.get("max_distance_km", 50.0)),
        max_vertical_gap_m=float(payload.get("max_vertical_gap_m", 750.0)),
        as_of=_time(payload.get("as_of")),
    )


def verification_pair_from_dict(payload: Mapping[str, object]) -> VerificationPair:
    """Restore a data-only verification result written by this module."""
    if payload.get("format") != "sharpmod-verification-pair":
        raise VerificationError("verification pair format is invalid")
    try:
        levels = tuple(
            VerificationLevel(
                height_msl_m=float(item["height_msl_m"]),
                observed_height_agl_m=_finite(item.get("observed_height_agl_m")),
                observed_pressure_hpa=_finite(item.get("observed_pressure_hpa")),
                temperature_c=_finite(item.get("temperature_c")),
                dewpoint_c=_finite(item.get("dewpoint_c")),
                u_wind_kt=_finite(item.get("u_wind_kt")),
                v_wind_kt=_finite(item.get("v_wind_kt")),
            )
            for item in payload.get("levels", ())
        )
        diagnostics = tuple(
            DiagnosticError(
                key=str(item["key"]),
                forecast=_finite(item.get("forecast")),
                observed=_finite(item.get("observed")),
                error=_finite(item.get("error")),
                reason=str(item["reason"]) if item.get("reason") else None,
            )
            for item in payload.get("diagnostics", ())
        )
        reasons = tuple(str(item) for item in payload.get("rejection_reasons", ()))
        return VerificationPair(
            forecast=_identity_from_dict(payload["forecast"]),
            observation=_identity_from_dict(payload["observation"]),
            rules=_rules_from_dict(payload["rules"]),
            matched=bool(payload.get("matched", False)),
            levels=levels,
            diagnostics=diagnostics,
            rejection_reasons=reasons,
            distance_km=_finite(payload.get("distance_km")),
            observed_coverage_bottom_m=_finite(
                payload.get("observed_coverage_bottom_m")
            ),
            observed_coverage_top_m=_finite(payload.get("observed_coverage_top_m")),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise VerificationError(f"verification pair is malformed: {exc}") from exc


def write_verification_json(path, pairs: Iterable[VerificationPair]) -> Path:
    """Atomically save inspectable verification pairs for later reopening."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "sharpmod-verification-results",
        "version": 1,
        "pairs": [pair.as_dict() for pair in pairs],
    }
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_verification_json(path) -> tuple[VerificationPair, ...]:
    """Read saved verification results without loading executable content."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(
            f"verification results could not be read: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "sharpmod-verification-results"
        or payload.get("version") != 1
        or not isinstance(payload.get("pairs"), list)
    ):
        raise VerificationError("unsupported verification results file")
    return tuple(verification_pair_from_dict(item) for item in payload["pairs"])


__all__ = [
    "AggregateMetric",
    "DEFAULT_VERIFICATION_METRICS",
    "DiagnosticError",
    "ProfileIdentity",
    "VerificationAggregate",
    "VerificationError",
    "VerificationLevel",
    "VerificationPair",
    "VerificationRules",
    "aggregate_verification",
    "export_verification_csv",
    "identity_for_collection",
    "read_verification_json",
    "verification_pair_from_dict",
    "verify_pair",
    "write_verification_json",
]
