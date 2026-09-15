"""Member-wise ensemble ingredient threshold evaluation.

Missing values are never treated as failed conditions.  A member belongs to
exactly one of three groups for a definition at a valid time: qualifying,
nonqualifying, or unusable.  Fractions use only the common usable subset while
coverage remains relative to the originally requested ensemble.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Callable, Iterable, Mapping

from sharpmod.ensemble_members import EnsembleAcquisition


FORMAT = "sharpmod-ensemble-thresholds"
VERSION = 1
OPERATORS = frozenset({">", ">=", "<", "<=", "==", "between"})
DISCLAIMER = (
    "Ensemble ingredient agreement; not a calibrated severe-weather probability."
)


class ThresholdError(ValueError):
    """A threshold definition or ensemble collection is invalid."""


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > -9998.0 else None


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else bool(cancel)


@dataclass(frozen=True)
class ThresholdCondition:
    metric: str
    operator: str
    value: float
    upper: float | None = None
    units: str = ""

    def __post_init__(self):
        metric = str(self.metric).strip()
        operator = str(self.operator).strip()
        value = _finite(self.value)
        upper = _finite(self.upper)
        if not metric:
            raise ThresholdError("a threshold metric is required")
        if operator not in OPERATORS:
            raise ThresholdError(f"unsupported threshold operator: {operator}")
        if value is None:
            raise ThresholdError("threshold value must be finite")
        if operator == "between":
            if upper is None or upper < value:
                raise ThresholdError(
                    "between requires an upper value at least as large"
                )
        elif upper is not None:
            raise ThresholdError("upper is only valid with the between operator")
        object.__setattr__(self, "metric", metric)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "units", str(self.units or ""))

    def evaluate(self, candidate) -> bool | None:
        number = _finite(candidate)
        if number is None:
            return None
        if self.operator == ">":
            return number > self.value
        if self.operator == ">=":
            return number >= self.value
        if self.operator == "<":
            return number < self.value
        if self.operator == "<=":
            return number <= self.value
        if self.operator == "==":
            return math.isclose(number, self.value, rel_tol=1e-9, abs_tol=1e-9)
        return self.value <= number <= self.upper

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "value": self.value,
            "upper": self.upper,
            "units": self.units,
        }


@dataclass(frozen=True)
class ThresholdDefinition:
    name: str
    conditions: tuple[ThresholdCondition, ...]

    def __post_init__(self):
        name = str(self.name).strip()
        conditions = tuple(self.conditions)
        if not name:
            raise ThresholdError("a threshold name is required")
        if not conditions:
            raise ThresholdError("at least one threshold condition is required")
        if len({condition.metric for condition in conditions}) != len(conditions):
            raise ThresholdError("a metric may appear only once in a joint threshold")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "conditions", conditions)

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(condition.metric for condition in self.conditions)

    @property
    def joint(self) -> bool:
        return len(self.conditions) > 1

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "conditions": [condition.as_dict() for condition in self.conditions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        try:
            conditions = tuple(
                ThresholdCondition(**dict(item))
                for item in payload.get("conditions", ())
            )
        except (TypeError, ValueError) as exc:
            raise ThresholdError(f"threshold conditions are malformed: {exc}") from exc
        return cls(str(payload.get("name", "")), conditions)


@dataclass(frozen=True)
class ThresholdMemberResult:
    member: str
    loaded: bool
    usable: bool
    satisfied: bool | None
    values: Mapping[str, float | None]
    reason: str | None = None


@dataclass(frozen=True)
class ThresholdResult:
    definition: ThresholdDefinition
    valid_time: datetime | None
    requested_member_count: int
    loaded_member_count: int
    usable_member_count: int
    qualifying_member_count: int
    members: tuple[ThresholdMemberResult, ...]
    cancelled: bool = False

    @property
    def agreement_fraction(self) -> float | None:
        if not self.usable_member_count:
            return None
        return self.qualifying_member_count / self.usable_member_count

    @property
    def requested_coverage(self) -> float | None:
        if not self.requested_member_count:
            return None
        return self.usable_member_count / self.requested_member_count

    @property
    def qualifying_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.members if item.satisfied is True)

    @property
    def nonqualifying_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.members if item.satisfied is False)

    @property
    def unusable_members(self) -> tuple[str, ...]:
        return tuple(item.member for item in self.members if not item.usable)

    def as_dict(self) -> dict[str, object]:
        return {
            "definition": self.definition.as_dict(),
            "valid_time": _iso(self.valid_time),
            "requested_member_count": self.requested_member_count,
            "loaded_member_count": self.loaded_member_count,
            "usable_member_count": self.usable_member_count,
            "qualifying_member_count": self.qualifying_member_count,
            "agreement_fraction": self.agreement_fraction,
            "requested_coverage": self.requested_coverage,
            "cancelled": self.cancelled,
            "label": DISCLAIMER,
            "members": [
                {
                    "member": item.member,
                    "loaded": item.loaded,
                    "usable": item.usable,
                    "satisfied": item.satisfied,
                    "values": dict(item.values),
                    "reason": item.reason,
                }
                for item in self.members
            ],
        }


def _date_index(collection, valid_time):
    dates = tuple(getattr(collection, "_dates", ()) or ())
    if valid_time is None:
        try:
            valid_time = collection.getCurrentDate()
        except (AttributeError, IndexError, TypeError, ValueError):
            valid_time = dates[0] if dates else None
    try:
        return valid_time, dates.index(valid_time)
    except ValueError:
        return valid_time, None


def evaluate_threshold(collection, definition, engine, *, valid_time=None):
    """Evaluate one single or joint definition on the common member subset."""
    if not isinstance(definition, ThresholdDefinition):
        definition = ThresholdDefinition.from_dict(definition)
    valid_time, index = _date_index(collection, valid_time)
    acquisition = EnsembleAcquisition.from_collection(collection)
    profiles = getattr(collection, "_profs", {}) or {}
    loaded = tuple(acquisition.loaded_members) or tuple(str(key) for key in profiles)
    requested = tuple(acquisition.requested_members) or loaded
    # Hand-built/legacy collections remain honest even when their metadata did
    # not know about a profile present in memory.
    loaded = tuple(dict.fromkeys((*loaded, *(str(key) for key in profiles))))
    requested = tuple(dict.fromkeys((*requested, *loaded)))
    loaded_set = set(loaded)
    available_profiles = {}
    for member in requested:
        if member not in loaded_set or member not in profiles:
            continue
        try:
            profile = profiles[member][index] if index is not None else None
        except (IndexError, KeyError, TypeError):
            profile = None
        if profile is not None:
            available_profiles[member] = profile

    batched_values = {}
    values_many = getattr(engine, "values_many", None)
    if callable(values_many) and available_profiles:
        try:
            rows = tuple(values_many(available_profiles.values(), definition.metrics))
            if len(rows) == len(available_profiles):
                batched_values = dict(zip(available_profiles, rows, strict=True))
        except Exception:  # noqa: BLE001 - serial fallback preserves isolation
            batched_values = {}

    members = []
    for member in requested:
        is_loaded = member in loaded_set and member in profiles
        if not is_loaded:
            members.append(
                ThresholdMemberResult(
                    member,
                    False,
                    False,
                    None,
                    MappingProxyType({key: None for key in definition.metrics}),
                    "member was not loaded",
                )
            )
            continue
        profile = available_profiles.get(member)
        if profile is None:
            members.append(
                ThresholdMemberResult(
                    member,
                    True,
                    False,
                    None,
                    MappingProxyType({key: None for key in definition.metrics}),
                    "member has no profile at the exact valid time",
                )
            )
            continue
        try:
            computed = (
                batched_values[member]
                if member in batched_values
                else engine.values(profile, definition.metrics)
            )
        except Exception as exc:  # noqa: BLE001 - backend isolation per member
            members.append(
                ThresholdMemberResult(
                    member,
                    True,
                    False,
                    None,
                    MappingProxyType({key: None for key in definition.metrics}),
                    f"diagnostic calculation failed: {exc}",
                )
            )
            continue
        values = MappingProxyType(
            {key: _finite(computed.get(key)) for key in definition.metrics}
        )
        outcomes = tuple(
            condition.evaluate(values[condition.metric])
            for condition in definition.conditions
        )
        usable = all(outcome is not None for outcome in outcomes)
        members.append(
            ThresholdMemberResult(
                member,
                True,
                usable,
                all(outcomes) if usable else None,
                values,
                None if usable else "one or more required diagnostics are unavailable",
            )
        )
    usable_count = sum(item.usable for item in members)
    qualifying_count = sum(item.satisfied is True for item in members)
    return ThresholdResult(
        definition,
        valid_time,
        len(requested),
        sum(member in loaded_set for member in requested),
        usable_count,
        qualifying_count,
        tuple(members),
    )


def evaluate_threshold_timeline(
    collection,
    definition,
    engine,
    *,
    valid_times: Iterable[datetime] | None = None,
    cancel: Callable[[], bool] | object | None = None,
) -> tuple[ThresholdResult, ...]:
    """Evaluate ordered exact times, preserving all results before cancel."""
    times = tuple(valid_times or getattr(collection, "_dates", ()) or ())
    results = []
    for valid_time in times:
        if _cancelled(cancel):
            break
        results.append(
            evaluate_threshold(collection, definition, engine, valid_time=valid_time)
        )
    return tuple(results)


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_threshold_results(path, definition, results=()) -> Path:
    """Atomically persist a definition and any computed timeline results."""
    if not isinstance(definition, ThresholdDefinition):
        definition = ThresholdDefinition.from_dict(definition)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "definition": definition.as_dict(),
        "results": [result.as_dict() for result in results],
    }
    return _atomic_json(Path(path), payload)


def read_threshold_definition(path) -> ThresholdDefinition:
    """Load a threshold definition without trusting executable content."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThresholdError(f"threshold file could not be read: {exc}") from exc
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ThresholdError("unsupported ensemble threshold file")
    return ThresholdDefinition.from_dict(payload.get("definition", {}))


def export_threshold_csv(path, results: Iterable[ThresholdResult]) -> Path:
    """Export summary plus member classifications with explicit denominators."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    fields = (
        "definition",
        "valid_time",
        "member",
        "loaded",
        "usable",
        "satisfied",
        "reason",
        "qualifying_count",
        "usable_count",
        "loaded_count",
        "requested_count",
        "agreement_fraction",
        "requested_coverage",
        "diagnostic_values",
        "interpretation",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for result in results:
                for member in result.members:
                    writer.writerow(
                        {
                            "definition": result.definition.name,
                            "valid_time": _iso(result.valid_time),
                            "member": member.member,
                            "loaded": member.loaded,
                            "usable": member.usable,
                            "satisfied": (
                                "" if member.satisfied is None else member.satisfied
                            ),
                            "reason": member.reason or "",
                            "qualifying_count": result.qualifying_member_count,
                            "usable_count": result.usable_member_count,
                            "loaded_count": result.loaded_member_count,
                            "requested_count": result.requested_member_count,
                            "agreement_fraction": result.agreement_fraction,
                            "requested_coverage": result.requested_coverage,
                            "diagnostic_values": json.dumps(
                                dict(member.values), sort_keys=True
                            ),
                            "interpretation": DISCLAIMER,
                        }
                    )
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


__all__ = [
    "DISCLAIMER",
    "OPERATORS",
    "ThresholdCondition",
    "ThresholdDefinition",
    "ThresholdError",
    "ThresholdMemberResult",
    "ThresholdResult",
    "evaluate_threshold",
    "evaluate_threshold_timeline",
    "export_threshold_csv",
    "read_threshold_definition",
    "write_threshold_results",
]
