"""Validated data contracts for regional Tornado Outbreak Indicator guidance.

TOI is not a sounding parameter. These objects carry a result calculated from
regional grids into the profile-oriented GUI/render path without implying that
a point sounding can reproduce it.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np

REGIONAL_GUIDANCE_META_KEY = "regional_guidance"
REGIONAL_GUIDANCE_SCHEMA_VERSION = 2
DEFAULT_UNAVAILABLE_REASON = "regional grid guidance not embedded"
MAX_GUIDANCE_JSON_BYTES = 1 * 1024 * 1024
_MAX_TEXT_LENGTH = 240


class GuidanceState(StrEnum):
    """Provenance state for a regional guidance product."""

    UNAVAILABLE = "unavailable"
    EXTERNAL = "external"
    PROXY = "proxy"
    EXPERIMENTAL = "experimental"
    OFFICIAL = "official"


def _short_text(value: Any, *, default: str = "") -> str:
    text = " ".join(str(value if value is not None else default).split())
    return text[:_MAX_TEXT_LENGTH]


def _state(value: Any, *, default: GuidanceState) -> GuidanceState:
    if isinstance(value, GuidanceState):
        return value
    try:
        return GuidanceState(str(value).strip().casefold())
    except ValueError as exc:
        choices = ", ".join(state.value for state in GuidanceState)
        raise ValueError(f"guidance state must be one of: {choices}") from exc


def _finite_number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return number


def _optional_datetime(value: Any, name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 datetime")
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _safe_provenance(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("provenance must be an object")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            key = _short_text(raw_key)
            if key:
                result[key] = _short_text(raw_value)
    return result


@dataclass(frozen=True)
class GuidanceGrid:
    """Common regional-grid input contract.

    Each field must either match the two-dimensional latitude/longitude shape
    or have a leading time dimension matching ``valid_times``.
    """

    model: str
    cycle: datetime
    valid_times: tuple[datetime, ...]
    latitude: np.ndarray
    longitude: np.ndarray
    fields: Mapping[str, np.ndarray]
    units: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model = _short_text(self.model)
        if not model:
            raise ValueError("model must not be empty")
        if not isinstance(self.cycle, datetime):
            raise ValueError("cycle must be a datetime")
        times = tuple(self.valid_times)
        if not times or any(not isinstance(value, datetime) for value in times):
            raise ValueError("valid_times must contain datetimes")
        if tuple(sorted(times)) != times or len(set(times)) != len(times):
            raise ValueError("valid_times must be unique and increasing")

        latitude = np.asarray(self.latitude, dtype=float)
        longitude = np.asarray(self.longitude, dtype=float)
        if latitude.ndim != 2 or longitude.shape != latitude.shape:
            raise ValueError("latitude and longitude must be matching 2-D grids")
        if latitude.size == 0:
            raise ValueError("latitude and longitude grids must not be empty")
        if np.any(np.isfinite(latitude) & ((latitude < -90) | (latitude > 90))):
            raise ValueError("latitude grid contains an out-of-range value")
        if np.any(np.isfinite(longitude) & ((longitude < -180) | (longitude > 180))):
            raise ValueError("longitude grid contains an out-of-range value")

        fields: dict[str, np.ndarray] = {}
        for raw_name, raw_values in self.fields.items():
            name = _short_text(raw_name)
            if not name:
                raise ValueError("field names must not be empty")
            values = np.asarray(raw_values, dtype=float)
            if values.shape not in {
                latitude.shape,
                (len(times), *latitude.shape),
            }:
                raise ValueError(
                    f"field {name!r} has shape {values.shape}; expected "
                    f"{latitude.shape} or {(len(times), *latitude.shape)}"
                )
            fields[name] = values
        if not fields:
            raise ValueError("fields must not be empty")

        object.__setattr__(self, "model", model)
        object.__setattr__(self, "valid_times", times)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "fields", fields)
        object.__setattr__(
            self,
            "units",
            {_short_text(k): _short_text(v) for k, v in self.units.items()},
        )
        object.__setattr__(self, "provenance", _safe_provenance(self.provenance))


@dataclass(frozen=True)
class TOIFeatures:
    """Regional inputs used by an experimental TOI reconstruction."""

    pressure_level_hpa: int
    translation_speed_kt: float
    maximum_jet_speed_kt: float
    jet_to_risk_distance_km: float
    jet_to_risk_bearing_deg: float
    maximum_stp: float
    month: int

    def __post_init__(self) -> None:
        level = int(
            _finite_number(
                self.pressure_level_hpa,
                "pressure_level_hpa",
                minimum=100,
                maximum=1000,
            )
        )
        month = int(_finite_number(self.month, "month", minimum=1, maximum=12))
        object.__setattr__(self, "pressure_level_hpa", level)
        object.__setattr__(self, "month", month)
        for name, minimum, maximum in (
            ("translation_speed_kt", 0.0, 300.0),
            ("maximum_jet_speed_kt", 0.0, 400.0),
            ("jet_to_risk_distance_km", 0.0, 20_050.0),
            ("jet_to_risk_bearing_deg", 0.0, 360.0),
            ("maximum_stp", 0.0, 100.0),
        ):
            number = _finite_number(
                getattr(self, name), name, minimum=minimum, maximum=maximum
            )
            if name == "jet_to_risk_bearing_deg" and number == 360.0:
                number = 0.0
            object.__setattr__(self, name, number)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TOIFeatures:
        if not isinstance(payload, Mapping):
            raise ValueError("TOI features must be an object")
        return cls(**{name: payload.get(name) for name in cls.__dataclass_fields__})

    def to_mapping(self) -> dict[str, int | float]:
        return {
            "pressure_level_hpa": self.pressure_level_hpa,
            "translation_speed_kt": self.translation_speed_kt,
            "maximum_jet_speed_kt": self.maximum_jet_speed_kt,
            "jet_to_risk_distance_km": self.jet_to_risk_distance_km,
            "jet_to_risk_bearing_deg": self.jet_to_risk_bearing_deg,
            "maximum_stp": self.maximum_stp,
            "month": self.month,
        }


@dataclass(frozen=True)
class TOIGuidance:
    """A validated TOI result, or an explicit unavailable state."""

    state: GuidanceState
    features: TOIFeatures | None = None
    score: float | None = None
    high_risk_probability: float | None = None
    method_version: str = ""
    calibration_version: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        state = _state(self.state, default=GuidanceState.UNAVAILABLE)
        if self.features is not None and not isinstance(self.features, TOIFeatures):
            raise ValueError("TOI features must be a TOIFeatures object")
        score = _finite_number(self.score, "TOI score", optional=True)
        probability = _finite_number(
            self.high_risk_probability,
            "TOI high_risk_probability",
            minimum=0,
            maximum=1,
            optional=True,
        )
        if state is GuidanceState.UNAVAILABLE and any(
            value is not None for value in (self.features, score, probability)
        ):
            raise ValueError("unavailable TOI guidance cannot carry a result")
        if state is not GuidanceState.UNAVAILABLE and all(
            value is None for value in (self.features, score, probability)
        ):
            raise ValueError("available TOI guidance must carry features or a result")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "high_risk_probability", probability)
        object.__setattr__(self, "method_version", _short_text(self.method_version))
        object.__setattr__(
            self, "calibration_version", _short_text(self.calibration_version)
        )
        object.__setattr__(self, "reason", _short_text(self.reason))

    @property
    def available(self) -> bool:
        return self.state is not GuidanceState.UNAVAILABLE

    @classmethod
    def unavailable(cls, reason: str = DEFAULT_UNAVAILABLE_REASON) -> TOIGuidance:
        return cls(GuidanceState.UNAVAILABLE, reason=reason)

    @classmethod
    def from_mapping(cls, payload: Any) -> TOIGuidance:
        if payload is None:
            return cls.unavailable()
        if not isinstance(payload, Mapping):
            raise ValueError("TOI guidance must be an object")
        state = _state(
            payload.get("state", "experimental"), default=GuidanceState.EXPERIMENTAL
        )
        if state is GuidanceState.UNAVAILABLE:
            return cls.unavailable(
                _short_text(payload.get("reason"), default=DEFAULT_UNAVAILABLE_REASON)
            )
        features_payload = payload.get("features")
        features = (
            TOIFeatures.from_mapping(features_payload)
            if features_payload is not None
            else None
        )
        return cls(
            state=state,
            features=features,
            score=payload.get("score"),
            high_risk_probability=payload.get("high_risk_probability"),
            method_version=payload.get("method_version", ""),
            calibration_version=payload.get("calibration_version", ""),
            reason=payload.get("reason", ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state.value}
        if self.features is not None:
            payload["features"] = self.features.to_mapping()
        if self.score is not None:
            payload["score"] = self.score
        if self.high_risk_probability is not None:
            payload["high_risk_probability"] = self.high_risk_probability
        if self.method_version:
            payload["method_version"] = self.method_version
        if self.calibration_version:
            payload["calibration_version"] = self.calibration_version
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class RegionalGuidance:
    """Portable regional TOI guidance and provenance."""

    toi: TOIGuidance = field(default_factory=TOIGuidance.unavailable)
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    source: str = ""
    experimental_not_official: bool = True
    provenance: Mapping[str, str] = field(default_factory=dict)
    schema_version: int = REGIONAL_GUIDANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.toi, TOIGuidance):
            raise ValueError("toi must be a TOIGuidance object")
        if int(self.schema_version) != REGIONAL_GUIDANCE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported regional guidance schema_version {self.schema_version!r}"
            )
        if self.valid_start is not None and not isinstance(self.valid_start, datetime):
            raise ValueError("valid_start must be a datetime")
        if self.valid_end is not None and not isinstance(self.valid_end, datetime):
            raise ValueError("valid_end must be a datetime")
        if (
            self.valid_start is not None
            and self.valid_end is not None
            and self.valid_end < self.valid_start
        ):
            raise ValueError("valid_end must not precede valid_start")
        if not isinstance(self.experimental_not_official, bool):
            raise ValueError("experimental_not_official must be boolean")
        object.__setattr__(self, "source", _short_text(self.source))
        object.__setattr__(self, "provenance", _safe_provenance(self.provenance))
        object.__setattr__(self, "schema_version", REGIONAL_GUIDANCE_SCHEMA_VERSION)

    @property
    def has_values(self) -> bool:
        return self.toi.available

    @classmethod
    def unavailable(cls, reason: str = DEFAULT_UNAVAILABLE_REASON) -> RegionalGuidance:
        reason = _short_text(reason, default=DEFAULT_UNAVAILABLE_REASON)
        return cls(toi=TOIGuidance.unavailable(reason))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> RegionalGuidance:
        if not isinstance(payload, Mapping):
            raise ValueError("regional guidance must be a JSON object")
        version = payload.get("schema_version", REGIONAL_GUIDANCE_SCHEMA_VERSION)
        if isinstance(version, bool):
            raise ValueError("schema_version must be an integer")
        try:
            version = int(version)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("schema_version must be an integer") from exc
        if version not in {1, REGIONAL_GUIDANCE_SCHEMA_VERSION}:
            raise ValueError(
                f"unsupported regional guidance schema_version {version!r}"
            )
        return cls(
            toi=TOIGuidance.from_mapping(payload.get("toi")),
            valid_start=_optional_datetime(payload.get("valid_start"), "valid_start"),
            valid_end=_optional_datetime(payload.get("valid_end"), "valid_end"),
            source=payload.get("source", ""),
            experimental_not_official=payload.get("experimental_not_official", True),
            provenance=_safe_provenance(payload.get("provenance")),
            schema_version=REGIONAL_GUIDANCE_SCHEMA_VERSION,
        )

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": REGIONAL_GUIDANCE_SCHEMA_VERSION,
            "experimental_not_official": self.experimental_not_official,
            "toi": self.toi.to_mapping(),
        }
        if self.valid_start is not None:
            payload["valid_start"] = _datetime_text(self.valid_start)
        if self.valid_end is not None:
            payload["valid_end"] = _datetime_text(self.valid_end)
        if self.source:
            payload["source"] = self.source
        if self.provenance:
            payload["provenance"] = dict(self.provenance)
        return payload


def coerce_regional_guidance(value: Any) -> RegionalGuidance:
    """Coerce a validated object or mapping into :class:`RegionalGuidance`."""

    if isinstance(value, RegionalGuidance):
        return value
    if isinstance(value, Mapping):
        nested = value.get(REGIONAL_GUIDANCE_META_KEY)
        if isinstance(nested, Mapping):
            value = nested
        return RegionalGuidance.from_mapping(value)
    raise ValueError("regional guidance must be a RegionalGuidance or mapping")


def load_regional_guidance_json(path: str | os.PathLike[str]) -> RegionalGuidance:
    """Load one bounded UTF-8 JSON guidance document."""

    source = os.path.abspath(os.fspath(path))
    try:
        if os.path.getsize(source) > MAX_GUIDANCE_JSON_BYTES:
            raise ValueError("regional guidance JSON exceeds the 1 MiB limit")
        with open(source, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise ValueError(f"could not read regional guidance JSON: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid regional guidance JSON: {exc}") from exc
    return coerce_regional_guidance(payload)


def guidance_from_collection(prof_collection: Any) -> RegionalGuidance:
    """Read regional guidance metadata without ever breaking a base render."""

    if prof_collection is None:
        return RegionalGuidance.unavailable()
    try:
        raw = prof_collection.getMeta(REGIONAL_GUIDANCE_META_KEY)
    except Exception:
        raw = getattr(prof_collection, "_meta", {}).get(REGIONAL_GUIDANCE_META_KEY)
    if raw is None:
        return RegionalGuidance.unavailable()
    try:
        return coerce_regional_guidance(raw)
    except (TypeError, ValueError) as exc:
        reason = _short_text(f"invalid embedded guidance: {exc}")
        return RegionalGuidance.unavailable(reason)


__all__ = [
    "DEFAULT_UNAVAILABLE_REASON",
    "REGIONAL_GUIDANCE_META_KEY",
    "REGIONAL_GUIDANCE_SCHEMA_VERSION",
    "GuidanceGrid",
    "GuidanceState",
    "RegionalGuidance",
    "TOIFeatures",
    "TOIGuidance",
    "coerce_regional_guidance",
    "guidance_from_collection",
    "load_regional_guidance_json",
]
