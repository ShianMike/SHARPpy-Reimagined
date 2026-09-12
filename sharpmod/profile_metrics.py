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
import csv
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence
import weakref

import numpy as np

from sharpmod import box_analysis


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


class ProfileMetricsEngine:
    """Tier-lazy, bounded-LRU analysis of profiles and collections."""

    def __init__(
        self,
        max_entries: int = 512,
        fast_analyzer: Callable[[object], Mapping[str, object]] | None = None,
        summary_analyzer: Callable[[object], Mapping[str, object]] | None = None,
        composite_analyzer: Callable[[object], Mapping[str, object]] | None = None,
    ):
        if int(max_entries) < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries = int(max_entries)
        self._uses_default_fast_analyzer = fast_analyzer is None
        self._uses_default_summary_analyzer = (
            fast_analyzer is None and summary_analyzer is None
        )
        self._fast_analyzer = fast_analyzer or box_analysis.fast_values
        self._summary_analyzer = summary_analyzer or (
            fast_analyzer if fast_analyzer is not None else box_analysis.summary_values
        )
        self._composite_analyzer = composite_analyzer or box_analysis.composite_values
        self._cache: OrderedDict[int, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def cache_size(self) -> int:
        """Number of live or not-yet-evicted entries in the bounded cache."""
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def invalidate(self, profile=None) -> None:
        """Invalidate one profile, or the whole cache when omitted."""
        with self._lock:
            if profile is None:
                self._cache.clear()
            else:
                self._cache.pop(id(profile), None)

    def _entry(self, profile) -> _CacheEntry:
        signature = _profile_signature(profile)
        cache_key = id(profile)
        entry = self._cache.get(cache_key)
        if entry is not None and entry.matches(profile, signature):
            self._cache.move_to_end(cache_key)
            return entry
        try:
            profile_ref = weakref.ref(profile)
            strong_profile = None
        except TypeError:
            profile_ref = lambda: None
            strong_profile = profile
        entry = _CacheEntry(profile_ref, strong_profile, signature, {})
        self._cache[cache_key] = entry
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return entry

    def values(
        self,
        profile,
        keys: Iterable[str] | str = DEFAULT_METRIC_KEYS,
    ) -> Mapping[str, float | None]:
        """Return requested values, evaluating each needed tier at most once."""
        wanted = _normalize_keys(keys)
        if profile is None:
            return _empty_values(wanted)
        tiers = _requested_tiers(wanted)
        analyzers = {
            box_analysis.FAST_TIER: self._fast_analyzer,
            _SUMMARY_TIER: self._summary_analyzer,
            box_analysis.COMPOSITE_TIER: self._composite_analyzer,
        }
        with self._lock:
            entry = self._entry(profile)
            for tier in tiers:
                if tier in entry.tiers:
                    continue
                try:
                    analyzed = analyzers[tier](profile)
                except Exception as exc:
                    raise ProfileMetricsError(
                        f"{tier} profile analysis failed: {exc}"
                    ) from exc
                entry.tiers[tier] = _readonly(
                    {
                        str(key): number
                        for key, value in analyzed.items()
                        if (number := _finite(value)) is not None
                    }
                )
                if tier == _SUMMARY_TIER:
                    entry.tiers.setdefault(box_analysis.FAST_TIER, entry.tiers[tier])
            result = {}
            for key in wanted:
                tier = _metric_tier(key)
                result[key] = entry.tiers[tier].get(key)
            return _readonly(result)

    def _can_batch(self, keys: Sequence[str]) -> bool:
        """Whether the requested built-in tiers have a batch-safe mapping."""
        tiers = _requested_tiers(keys)
        if box_analysis.COMPOSITE_TIER in tiers:
            return False
        if (
            box_analysis.FAST_TIER in tiers
            and not self._uses_default_fast_analyzer
        ):
            return False
        if _SUMMARY_TIER in tiers and not self._uses_default_summary_analyzer:
            return False
        return bool(tiers)

    def _batch_values(
        self,
        profiles: Sequence[object],
        keys: Sequence[str],
    ) -> tuple[Mapping[str, float | None], ...]:
        """Analyze missing built-in fast/summary cache entries as one batch."""
        tiers = _requested_tiers(keys)
        include_effective_stp = _SUMMARY_TIER in tiers
        with self._lock:
            entries = [self._entry(profile) for profile in profiles]
            for entry in entries:
                if _SUMMARY_TIER in entry.tiers:
                    entry.tiers.setdefault(
                        box_analysis.FAST_TIER,
                        entry.tiers[_SUMMARY_TIER],
                    )
            missing = [
                index
                for index, entry in enumerate(entries)
                if any(tier not in entry.tiers for tier in tiers)
            ]
            if missing:
                rows = box_analysis.fast_values_many(
                    (profiles[index] for index in missing),
                    include_effective_stp=include_effective_stp,
                )
                if len(rows) != len(missing):
                    raise ProfileMetricsError(
                        "batch profile analysis returned the wrong profile count"
                    )
                for index, analyzed in zip(missing, rows):
                    normalized = _readonly(
                        {
                            str(key): number
                            for key, value in analyzed.items()
                            if (number := _finite(value)) is not None
                        }
                    )
                    entry = entries[index]
                    if include_effective_stp:
                        entry.tiers[_SUMMARY_TIER] = normalized
                        entry.tiers.setdefault(box_analysis.FAST_TIER, normalized)
                    else:
                        entry.tiers[box_analysis.FAST_TIER] = normalized

            return tuple(
                _readonly(
                    {
                        key: entry.tiers[_metric_tier(key)].get(key)
                        for key in keys
                    }
                )
                for entry in entries
            )

    def timeline(
        self,
        collection,
        keys: Iterable[str] | str = DEFAULT_METRIC_KEYS,
        member: str | None = None,
    ) -> tuple[MetricSample, ...]:
        """Return one sample for every collection date, including gaps."""
        wanted = _normalize_keys(keys)
        dates = _collection_dates(collection)
        profiles = _profiles_by_member(collection)
        selected = (
            str(member)
            if member is not None
            else _highlighted_member(collection, profiles)
        )
        if selected is None:
            return tuple(
                MetricSample(
                    valid_time=date,
                    member=None,
                    values=_empty_values(wanted),
                    available=False,
                    reason="collection has no profile members",
                )
                for date in dates
            )
        samples = []
        for index, date in enumerate(dates):
            profile = _profile_at(profiles, selected, index)
            if profile is None:
                samples.append(
                    MetricSample(
                        valid_time=date,
                        member=selected,
                        values=_empty_values(wanted),
                        available=False,
                        reason="member has no profile at this valid time",
                    )
                )
                continue
            try:
                values = self.values(profile, wanted)
            except ProfileMetricsError as exc:
                samples.append(
                    MetricSample(
                        valid_time=date,
                        member=selected,
                        values=_empty_values(wanted),
                        available=False,
                        reason=str(exc),
                    )
                )
            else:
                samples.append(MetricSample(date, selected, values, True))
        return tuple(samples)

    def compare(
        self,
        collections: Iterable[object],
        keys: Iterable[str] | str = DEFAULT_METRIC_KEYS,
        valid_time: datetime | None = None,
    ) -> tuple[ComparisonSample, ...]:
        """Compare collections only at an exact shared valid datetime."""
        wanted = _normalize_keys(keys)
        collections = tuple(collections)
        requested = valid_time
        if requested is None and collections:
            requested = _current_date(collections[0])
        rows = []
        for collection_index, collection in enumerate(collections):
            label = _collection_label(collection, collection_index)
            dates = _collection_dates(collection)
            profiles = _profiles_by_member(collection)
            index = _date_index(dates, requested)
            current = _current_date(collection)
            if index is None:
                rows.append(
                    ComparisonSample(
                        collection_index,
                        label,
                        requested,
                        current,
                        None,
                        _empty_values(wanted),
                        False,
                        False,
                        "requested valid time is unavailable",
                    )
                )
                continue
            member = _highlighted_member(collection, profiles)
            profile = _profile_at(profiles, member, index)
            if profile is None:
                member, profile = _first_profile_at(profiles, index)
            if profile is None:
                rows.append(
                    ComparisonSample(
                        collection_index,
                        label,
                        requested,
                        dates[index],
                        member,
                        _empty_values(wanted),
                        False,
                        True,
                        "collection has no profile at this valid time",
                    )
                )
                continue
            try:
                values = self.values(profile, wanted)
            except ProfileMetricsError as exc:
                rows.append(
                    ComparisonSample(
                        collection_index,
                        label,
                        requested,
                        dates[index],
                        member,
                        _empty_values(wanted),
                        False,
                        True,
                        str(exc),
                    )
                )
            else:
                rows.append(
                    ComparisonSample(
                        collection_index,
                        label,
                        requested,
                        dates[index],
                        member,
                        values,
                        True,
                        True,
                    )
                )
        return tuple(rows)

    def ensemble(
        self,
        collection,
        keys: Iterable[str] | str = DEFAULT_METRIC_KEYS,
        valid_time: datetime | None = None,
        pressure_levels: Iterable[float] | None = None,
    ) -> EnsembleSummary:
        """Summarize all members at one time without changing selection."""
        wanted = _normalize_keys(keys)
        dates = _collection_dates(collection)
        profiles = _profiles_by_member(collection)
        member_names = tuple(str(member) for member in profiles)
        requested = valid_time if valid_time is not None else _current_date(collection)
        index = _date_index(dates, requested)
        if index is None:
            return EnsembleSummary(
                requested,
                len(member_names),
                0,
                (),
                member_names,
                _readonly({key: _distribution(()) for key in wanted}),
                (),
                False,
                "requested valid time is unavailable",
            )

        if pressure_levels is None:
            levels = DEFAULT_PRESSURE_LEVELS
        else:
            levels = tuple(
                dict.fromkeys(
                    number
                    for level in pressure_levels
                    if (number := _finite(level)) is not None and number > 0.0
                )
            )
            if not levels:
                raise ValueError("pressure_levels must contain a positive value")

        candidates = []
        for member_name in member_names:
            profile = _profile_at(profiles, member_name, index)
            if profile is None:
                continue
            candidates.append((member_name, profile))

        batched = None
        if candidates and self._can_batch(wanted):
            try:
                batched = self._batch_values(
                    tuple(profile for _member_name, profile in candidates),
                    wanted,
                )
            except ProfileMetricsError:
                batched = None
            except Exception:
                # Batch packing and native execution are optional optimizations;
                # the established per-profile path retains failure isolation.
                batched = None

        available_names = []
        missing_names = [
            member_name
            for member_name in member_names
            if _profile_at(profiles, member_name, index) is None
        ]
        metric_rows = []
        temperature_rows = []
        dewpoint_rows = []
        for candidate_index, (member_name, profile) in enumerate(candidates):
            try:
                metrics = (
                    batched[candidate_index]
                    if batched is not None
                    else self.values(profile, wanted)
                )
            except ProfileMetricsError:
                missing_names.append(member_name)
                continue
            available_names.append(member_name)
            metric_rows.append(metrics)
            temperature_rows.append(_interpolate_column(profile, "tmpc", levels))
            dewpoint_rows.append(_interpolate_column(profile, "dwpc", levels))

        scalar = _readonly(
            {key: _distribution(row.get(key) for row in metric_rows) for key in wanted}
        )
        bands = tuple(
            ThermodynamicBand(
                pressure_hpa=level,
                temperature=_distribution(row[level_index] for row in temperature_rows),
                dewpoint=_distribution(row[level_index] for row in dewpoint_rows),
            )
            for level_index, level in enumerate(levels)
        )
        available = bool(available_names)
        return EnsembleSummary(
            valid_time=dates[index],
            member_count=len(member_names),
            available_member_count=len(available_names),
            members=tuple(available_names),
            missing_members=tuple(missing_names),
            scalar=scalar,
            bands=bands,
            available=available,
            reason=None if available else "no ensemble members are available",
        )


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
    ) + metric_keys
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for sample in samples:
            row = {
                "collection_index": sample.collection_index,
                "label": sample.label,
                "requested_time": _csv_time(sample.requested_time),
                "valid_time": _csv_time(sample.valid_time),
                "member": sample.member or "",
                "available": sample.available,
                "aligned": sample.aligned,
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
