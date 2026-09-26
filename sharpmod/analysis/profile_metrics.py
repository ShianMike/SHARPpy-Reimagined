"""Cached profile metrics for trends, comparisons, and ensembles.

The GUI features built on this module all ask the same two questions of a
profile: "what are these scalar parameters?" and "what does its thermodynamic
column look like on a common pressure grid?"  Keeping those questions here has
two useful properties:

* the expensive composite tier is only evaluated when a requested key needs
  it, and each tier is cached once per unchanged profile; and
* timeline and ensemble inspection reads ``ProfCollection`` storage directly,
  so gathering data never changes the selected time or highlighted member.

There is deliberately no Qt dependency.  The records are small immutable
objects which are cheap to build on a worker and safe to hand back to the UI.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import csv
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence
import weakref

import numpy as np

from sharpmod.analysis import box_analysis
from sharpmod.analysis.comparison import (
    ComparisonBasis,
    ComparisonContext,
    assess_compatibility,
    context_for_collection,
)
from sharpmod.analysis.ensemble_members import EnsembleAcquisition


DEFAULT_METRIC_KEYS = (
    "mlcape",
    "mlcin",
    "shear_6km",
    "srh_1km",
    "stp_cin",
)

# Effective STP normally belongs to the expensive composite tier, but the
# comparison workspace has a focused implementation that reuses the fast
# parcel workspace and evaluates only STP. Other composite parameters remain
# explicitly requested and use the full oracle.
_SUMMARY_TIER = "summary"
_SUMMARY_KEYS = frozenset({"stp_cin"})

# A fixed ladder makes envelopes comparable between refreshes.  Values outside
# an individual member's observed pressure range stay missing; they are never
# extrapolated.
DEFAULT_PRESSURE_LEVELS = tuple(float(value) for value in range(1000, 99, -25))


class ProfileMetricsError(Exception):
    """A profile or collection could not supply the requested metrics."""


@dataclass(frozen=True)
class MetricSample:
    """Metrics for one member at one valid time."""

    valid_time: datetime | None
    member: str | None
    values: Mapping[str, float | None]
    available: bool
    reason: str | None = None
    outcome: str | None = None
    _request_key: tuple = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class CollectionSnapshot:
    """Saved containers/selection; large SHARPpy profiles are shared read-only."""

    _dates: tuple
    _profs: Mapping[str, tuple]
    _meta: Mapping
    current_date: object = None
    _highlight: str | None = None
    _mod_therm: tuple[bool, ...] = ()
    _mod_wind: tuple[bool, ...] = ()
    _interp: tuple[bool, ...] = ()

    def getCurrentDate(self):  # noqa: N802 - profile collection API
        return self.current_date

    def getMeta(self, key):  # noqa: N802 - profile collection API
        return self._meta[key]


def freeze_collection(collection) -> CollectionSnapshot:
    """Freeze the existing worker boundary without copying backend profiles."""
    profiles = {}
    for member, values in _profiles_by_member(collection).items():
        try:
            profiles[str(member)] = tuple(values) if values is not None else ()
        except TypeError:
            profiles[str(member)] = (values,)
    raw_meta = getattr(collection, "_meta", {}) or {}
    metadata = {}
    for key, value in raw_meta.items() if isinstance(raw_meta, Mapping) else ():
        try:
            metadata[key] = deepcopy(value)
        except Exception:  # noqa: BLE001 - optional provider handles can be opaque
            metadata[key] = value
    flags = {}
    for name in ("_mod_therm", "_mod_wind", "_interp"):
        try:
            flags[name] = tuple(bool(value) for value in getattr(collection, name, ()))
        except (TypeError, ValueError):
            flags[name] = ()
    return CollectionSnapshot(
        _collection_dates(collection), _readonly(profiles), _readonly(metadata),
        _current_date(collection), _highlighted_member(collection, _profiles_by_member(collection)),
        **flags,
    )


@dataclass(frozen=True)
class ComparisonSample:
    """One collection's contribution to a valid-time comparison."""

    collection_index: int
    label: str
    requested_time: datetime | None
    valid_time: datetime | None
    member: str | None
    values: Mapping[str, float | None]
    available: bool
    aligned: bool
    reason: str | None = None
    context: ComparisonContext | None = None
    basis: ComparisonBasis | None = None
    outcome: str | None = None
    _request_key: tuple = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class MetricDistribution:
    """Finite-value distribution; percentiles are ``None`` when empty."""

    count: int
    p10: float | None
    median: float | None
    p90: float | None
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True)
class ThermodynamicBand:
    """Temperature and dewpoint distributions at one pressure level."""

    pressure_hpa: float
    temperature: MetricDistribution
    dewpoint: MetricDistribution


@dataclass(frozen=True)
class EnsembleMemberSample:
    """One saved member's diagnostic and envelope contribution."""

    member: str
    outcome: str
    values: Mapping[str, float | None] = field(default_factory=lambda: _readonly({}))
    temperature: tuple = ()
    dewpoint: tuple = ()
    reason: str | None = None


@dataclass(frozen=True)
class EnsembleSummary:
    """Scalar and thermodynamic spread for one ensemble valid time."""

    valid_time: datetime | None
    member_count: int
    available_member_count: int
    members: tuple[str, ...]
    missing_members: tuple[str, ...]
    scalar: Mapping[str, MetricDistribution]
    bands: tuple[ThermodynamicBand, ...]
    available: bool
    reason: str | None = None
    requested_member_count: int | None = None
    loaded_member_count: int | None = None
    requested_members: tuple[str, ...] = ()
    loaded_members: tuple[str, ...] = ()
    failed_members: tuple[str, ...] = ()
    cancelled_members: tuple[str, ...] = ()
    unusable_members: tuple[str, ...] = ()
    distribution_available: bool = True
    member_samples: tuple[EnsembleMemberSample, ...] = ()
    _request_key: tuple = field(default=(), repr=False, compare=False)


@dataclass
class _CacheEntry:
    """One bounded-LRU entry without unnecessarily retaining a profile."""

    profile_ref: Callable[[], object | None]
    strong_profile: object | None
    signature: tuple[object, ...]
    tiers: dict[str, Mapping[str, float]]

    def matches(self, profile, signature) -> bool:
        existing = (
            self.strong_profile
            if self.strong_profile is not None
            else self.profile_ref()
        )
        return existing is profile and self.signature == signature


def _readonly(values: Mapping) -> Mapping:
    """Copy a mapping into a read-only, insertion-ordered view."""
    return MappingProxyType(dict(values))


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        if np.ma.is_masked(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= -9998.0:
        return None
    return number


def _column_signature(profile, name: str) -> tuple[object, ...]:
    """Return a cheap content signature that catches in-place column edits."""
    raw = getattr(profile, name, None)
    if raw is None:
        return (name, None)
    try:
        column = np.ma.asarray(raw)
        data = np.ascontiguousarray(np.ma.getdata(column))
        mask = np.ascontiguousarray(np.ma.getmaskarray(column))
        return (
            name,
            data.shape,
            data.dtype.str,
            hash(data.tobytes()),
            hash(mask.tobytes()),
        )
    except (TypeError, ValueError):
        return (name, id(raw), repr(raw))


def _profile_signature(profile) -> tuple[object, ...]:
    # ``ProfCollection.modify`` replaces the object, but callers can also edit
    # raw arrays in place.  These small soundings are cheap to fingerprint and
    # doing so prevents an apparently valid cache hit after such an edit.
    columns = (
        "pres",
        "hght",
        "tmpc",
        "dwpc",
        "wdir",
        "wspd",
        "u",
        "v",
    )
    revision = getattr(profile, "_sharpmod_metrics_revision", None)
    return (revision,) + tuple(_column_signature(profile, name) for name in columns)


def _normalize_keys(keys: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(keys, str):
        keys = (keys,)
    unique = tuple(dict.fromkeys(str(key) for key in keys))
    for key in unique:
        try:
            box_analysis.parameter(key)
        except box_analysis.BoxAnalysisError as exc:
            raise ProfileMetricsError(str(exc)) from exc
    return unique


def _metric_tier(key: str) -> str:
    return _SUMMARY_TIER if key in _SUMMARY_KEYS else box_analysis.parameter(key).tier


def _requested_tiers(keys: Sequence[str]) -> tuple[str, ...]:
    """Return the minimum analyzer tiers needed for an ordered key set."""
    tiers = tuple(dict.fromkeys(_metric_tier(key) for key in keys))
    if _SUMMARY_TIER in tiers and box_analysis.FAST_TIER in tiers:
        return (_SUMMARY_TIER,) + tuple(
            tier
            for tier in tiers
            if tier not in {_SUMMARY_TIER, box_analysis.FAST_TIER}
        )
    return tiers


def _empty_values(keys: Sequence[str]) -> Mapping[str, float | None]:
    return _readonly({key: None for key in keys})


def _collection_dates(collection) -> tuple[datetime, ...]:
    dates = getattr(collection, "_dates", ())
    try:
        return tuple(dates)
    except TypeError:
        return ()


def _current_date(collection) -> datetime | None:
    dates = _collection_dates(collection)
    index = getattr(collection, "_prof_idx", None)
    if isinstance(index, int) and 0 <= index < len(dates):
        return dates[index]
    getter = getattr(collection, "getCurrentDate", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _profiles_by_member(collection) -> Mapping:
    profiles = getattr(collection, "_profs", None)
    return profiles if isinstance(profiles, Mapping) else {}


def _highlighted_member(collection, profiles: Mapping) -> str | None:
    member = getattr(collection, "_highlight", None)
    if member in profiles:
        return str(member)
    getter = getattr(collection, "getHighlightedMemberName", None)
    if callable(getter):
        try:
            member = getter()
        except Exception:
            member = None
        if member in profiles:
            return str(member)
    if profiles:
        return str(next(iter(profiles)))
    return None


def _member_key(profiles: Mapping, member: str | None):
    """Recover the original mapping key after normalising labels to strings."""
    if member in profiles:
        return member
    for key in profiles:
        if str(key) == member:
            return key
    return None


def _profile_at(profiles: Mapping, member: str | None, index: int):
    key = _member_key(profiles, member)
    if key is None:
        return None
    sequence = profiles.get(key)
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return None


def _first_profile_at(profiles: Mapping, index: int):
    for key in profiles:
        profile = _profile_at(profiles, str(key), index)
        if profile is not None:
            return str(key), profile
    return None, None


def _date_index(dates: Sequence[datetime], valid_time: datetime | None):
    if valid_time is None:
        return None
    for index, candidate in enumerate(dates):
        if candidate == valid_time:
            return index
    return None


def _metadata(collection, key: str):
    metadata = getattr(collection, "_meta", None)
    if isinstance(metadata, Mapping) and key in metadata:
        return metadata[key]
    getter = getattr(collection, "getMeta", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            return None
    return None


def _collection_label(collection, index: int) -> str:
    parts = []
    for key in ("model", "loc"):
        value = _metadata(collection, key)
        if value not in (None, ""):
            parts.append(str(value))
    run = _metadata(collection, "run")
    if isinstance(run, datetime):
        parts.append(run.strftime("%Y-%m-%d %HZ"))
    elif run not in (None, ""):
        parts.append(str(run))
    return " · ".join(parts) if parts else f"Sounding {index + 1}"


def _distribution(values: Iterable[object]) -> MetricDistribution:
    finite = [number for value in values if (number := _finite(value)) is not None]
    if not finite:
        return MetricDistribution(0, None, None, None, None, None)
    array = np.asarray(finite, dtype=float)
    p10, median, p90 = np.percentile(array, (10.0, 50.0, 90.0))
    return MetricDistribution(
        count=int(array.size),
        p10=float(p10),
        median=float(median),
        p90=float(p90),
        minimum=float(np.min(array)),
        maximum=float(np.max(array)),
    )


def _interpolate_column(profile, field: str, pressure_levels) -> tuple:
    raw_pressure = getattr(profile, "pres", None)
    raw_values = getattr(profile, field, None)
    if raw_pressure is None or raw_values is None:
        return tuple(None for _ in pressure_levels)
    try:
        pressure = np.ma.asarray(raw_pressure, dtype=float)
        values = np.ma.asarray(raw_values, dtype=float)
        length = min(pressure.size, values.size)
        pressure = pressure[:length]
        values = values[:length]
        mask = (
            np.ma.getmaskarray(pressure)
            | np.ma.getmaskarray(values)
            | ~np.isfinite(np.ma.filled(pressure, np.nan))
            | ~np.isfinite(np.ma.filled(values, np.nan))
            | (np.ma.filled(pressure, -9999.0) <= 0.0)
            | (np.ma.filled(pressure, -9999.0) <= -9998.0)
            | (np.ma.filled(values, -9999.0) <= -9998.0)
        )
        clean_pressure = np.asarray(pressure.data[~mask], dtype=float)
        clean_values = np.asarray(values.data[~mask], dtype=float)
    except (TypeError, ValueError):
        return tuple(None for _ in pressure_levels)
    if clean_pressure.size < 2:
        return tuple(None for _ in pressure_levels)

    # Sort low-to-high in log pressure and collapse duplicate observations.
    order = np.argsort(clean_pressure)
    clean_pressure = clean_pressure[order]
    clean_values = clean_values[order]
    clean_pressure, unique_index = np.unique(clean_pressure, return_index=True)
    clean_values = clean_values[unique_index]
    if clean_pressure.size < 2:
        return tuple(None for _ in pressure_levels)
    log_pressure = np.log(clean_pressure)
    lower = float(clean_pressure[0])
    upper = float(clean_pressure[-1])
    result = []
    for level in pressure_levels:
        if level < lower or level > upper:
            result.append(None)
            continue
        result.append(float(np.interp(math.log(level), log_pressure, clean_values)))
    return tuple(result)




def _csv_keys(samples, keys) -> tuple[str, ...]:
    if keys is not None:
        return _normalize_keys(keys)
    discovered = []
    for sample in samples:
        for key in sample.values:
            if key not in discovered:
                discovered.append(key)
    return tuple(discovered)


def _csv_time(value) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def export_timeline_csv(
    path,
    samples: Iterable[MetricSample],
    keys: Iterable[str] | str | None = None,
) -> Path:
    """Write timeline samples to a stable, spreadsheet-friendly CSV."""
    samples = tuple(samples)
    metric_keys = _csv_keys(samples, keys)
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("valid_time", "member", "available", "reason") + metric_keys,
        )
        writer.writeheader()
        for sample in samples:
            row = {
                "valid_time": _csv_time(sample.valid_time),
                "member": sample.member or "",
                "available": sample.available,
                "reason": sample.reason or "",
            }
            row.update(
                {
                    key: "" if sample.values.get(key) is None else sample.values[key]
                    for key in metric_keys
                }
            )
            writer.writerow(row)
    return destination


def export_timeline_series_csv(
    path,
    series: Iterable[tuple[str, Iterable[MetricSample]]],
    keys: Iterable[str] | str | None = None,
) -> Path:
    """Write several labelled timelines to one CSV.

    ``series`` is an iterable of ``(label, samples)``. A leading ``sounding``
    column names which timeline each row belongs to, which
    :func:`export_timeline_csv` has no need of; the header therefore describes
    the data it is given, the same way the metric columns already do.
    """
    groups = tuple((str(label), tuple(samples)) for label, samples in series)
    flattened = tuple(
        sample for _label, samples in groups for sample in samples
    )
    metric_keys = _csv_keys(flattened, keys)
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("sounding", "valid_time", "member", "available", "reason")
            + metric_keys,
        )
        writer.writeheader()
        for label, samples in groups:
            for sample in samples:
                row = {
                    "sounding": label,
                    "valid_time": _csv_time(sample.valid_time),
                    "member": sample.member or "",
                    "available": sample.available,
                    "reason": sample.reason or "",
                }
                row.update(
                    {
                        key: (
                            ""
                            if sample.values.get(key) is None
                            else sample.values[key]
                        )
                        for key in metric_keys
                    }
                )
                writer.writerow(row)
    return destination


def export_comparison_csv(
    path,
    samples: Iterable[ComparisonSample],
    keys: Iterable[str] | str | None = None,
) -> Path:
    """Write valid-time comparison rows to CSV, retaining mismatch status."""
    samples = tuple(samples)
    metric_keys = _csv_keys(samples, keys)
    destination = Path(path)
    fields = (
        "collection_index",
        "label",
        "requested_time",
        "valid_time",
        "member",
        "available",
        "aligned",
        "reason",
        "requested_lat",
        "requested_lon",
        "selected_lat",
        "selected_lon",
        "grid_spacing_km",
        "terrain_elevation_m",
        "run_time",
        "lead_hours",
        "parcel_convention",
        "storm_motion_convention",
        "profile_edited",
        "location_relation",
        "compatibility_issues",
        "incompatible_metrics",
    ) + metric_keys
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            context = sample.context
            basis = sample.basis
            row = {
                "collection_index": sample.collection_index,
                "label": sample.label,
                "requested_time": _csv_time(sample.requested_time),
                "valid_time": _csv_time(sample.valid_time),
                "member": sample.member or "",
                "available": sample.available,
                "aligned": sample.aligned,
                "reason": sample.reason or "",
                "requested_lat": getattr(context, "requested_lat", None),
                "requested_lon": getattr(context, "requested_lon", None),
                "selected_lat": getattr(context, "selected_lat", None),
                "selected_lon": getattr(context, "selected_lon", None),
                "grid_spacing_km": getattr(context, "grid_spacing_km", None),
                "terrain_elevation_m": getattr(
                    context, "terrain_elevation_m", None
                ),
                "run_time": _csv_time(getattr(context, "run_time", None)),
                "lead_hours": getattr(context, "lead_hours", None),
                "parcel_convention": getattr(
                    context, "parcel_convention", None
                ),
                "storm_motion_convention": getattr(
                    context, "storm_motion_convention", None
                ),
                "profile_edited": getattr(context, "profile_edited", None),
                "location_relation": getattr(
                    basis, "location_relation", "unknown"
                ),
                "compatibility_issues": json.dumps(
                    [
                        {
                            "field": issue.field,
                            "severity": issue.severity,
                            "message": issue.message,
                        }
                        for issue in getattr(basis, "issues", ())
                    ],
                    separators=(",", ":"),
                ),
                "incompatible_metrics": json.dumps(
                    dict(getattr(basis, "incompatible_metrics", {})),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
            row.update(
                {
                    key: "" if sample.values.get(key) is None else sample.values[key]
                    for key in metric_keys
                }
            )
            writer.writerow(row)
    return destination


def export_ensemble_csv(
    path,
    summary: EnsembleSummary,
    acquisition: EnsembleAcquisition | None = None,
) -> Path:
    """Atomically export ensemble distributions and acquisition membership.

    Diagnostic rows carry their own usable denominator. Member rows distinguish
    an unavailable download from a loaded-but-unusable profile; neither state is
    serialized as a zero or a negative threshold outcome.
    """

    if not isinstance(summary, EnsembleSummary):
        raise TypeError("summary must be an EnsembleSummary")
    summary_loaded = tuple(summary.loaded_members or summary.members)
    summary_requested = tuple(
        summary.requested_members
        or dict.fromkeys((*summary_loaded, *summary.missing_members))
    )
    acquisition = acquisition or EnsembleAcquisition(
        summary_requested,
        summary_loaded,
    )
    requested = acquisition.requested_members or tuple(summary.requested_members)
    loaded = set(acquisition.loaded_members or summary.loaded_members)
    unusable = set(summary.unusable_members)
    outcomes = {row.member: row for row in summary.member_samples}
    fields = (
        "record_type",
        "valid_time",
        "metric",
        "member",
        "status",
        "reason",
        "requested_count",
        "loaded_count",
        "summary_usable_count",
        "diagnostic_usable_count",
        "minimum",
        "p10",
        "median",
        "p90",
        "maximum",
        "distribution_available",
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    common = {
        "valid_time": _csv_time(summary.valid_time),
        "requested_count": summary.requested_member_count
        if summary.requested_member_count is not None
        else len(requested),
        "loaded_count": summary.loaded_member_count
        if summary.loaded_member_count is not None
        else len(loaded),
        "summary_usable_count": summary.available_member_count,
        "distribution_available": summary.distribution_available,
    }
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for metric, distribution in summary.scalar.items():
                writer.writerow(
                    {
                        **common,
                        "record_type": "diagnostic",
                        "metric": metric,
                        "diagnostic_usable_count": distribution.count,
                        "minimum": ""
                        if distribution.minimum is None
                        else distribution.minimum,
                        "p10": "" if distribution.p10 is None else distribution.p10,
                        "median": ""
                        if distribution.median is None
                        else distribution.median,
                        "p90": "" if distribution.p90 is None else distribution.p90,
                        "maximum": ""
                        if distribution.maximum is None
                        else distribution.maximum,
                    }
                )
            for member in requested:
                failure = acquisition.failure_for(member)
                if member in loaded:
                    status = "loaded-unusable" if member in unusable else "loaded"
                    reason = (
                        "profile was not usable for the requested summary"
                        if member in unusable
                        else ""
                    )
                    diagnostic = outcomes.get(member)
                    if diagnostic is not None and diagnostic.outcome in {"failed", "cancelled"}:
                        status = f"diagnostic-{diagnostic.outcome}"
                    if diagnostic is not None and diagnostic.outcome != "completed":
                        reason = diagnostic.reason
                else:
                    status = failure.status if failure is not None else "unavailable"
                    reason = failure.reason if failure is not None else ""
                writer.writerow(
                    {
                        **common,
                        "record_type": "member",
                        "member": member,
                        "status": status,
                        "reason": reason or "",
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


from sharpmod.analysis.profile_metrics_engine import (  # noqa: E402
    ProfileMetricsEngine
)
