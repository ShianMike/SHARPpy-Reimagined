"""Tier-lazy, bounded-LRU engine for profile and collection metrics.

It reuses calculations and comparison/ensemble context from the analysis modules so GUI
requests can cache fast, summary, and composite work independently."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
from sharpmod.analysis import box_analysis
from sharpmod.analysis.comparison import assess_compatibility
from sharpmod.analysis.comparison import context_for_collection
from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from typing import Callable
from typing import Iterable
from typing import Mapping
from typing import Sequence
import threading
import weakref
from sharpmod.analysis import profile_metrics as _api


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
        self._cache: OrderedDict[int, _api._CacheEntry] = OrderedDict()
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

    def _entry(self, profile) -> _api._CacheEntry:
        signature = _api._profile_signature(profile)
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
        entry = _api._CacheEntry(profile_ref, strong_profile, signature, {})
        self._cache[cache_key] = entry
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return entry

    def values(
        self,
        profile,
        keys: Iterable[str] | str = _api.DEFAULT_METRIC_KEYS,
    ) -> Mapping[str, float | None]:
        """Return requested values, evaluating each needed tier at most once."""
        wanted = _api._normalize_keys(keys)
        if profile is None:
            return _api._empty_values(wanted)
        tiers = _api._requested_tiers(wanted)
        analyzers = {
            box_analysis.FAST_TIER: self._fast_analyzer,
            _api._SUMMARY_TIER: self._summary_analyzer,
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
                    raise _api.ProfileMetricsError(
                        f"{tier} profile analysis failed: {exc}"
                    ) from exc
                entry.tiers[tier] = _api._readonly(
                    {
                        str(key): number
                        for key, value in analyzed.items()
                        if (number := _api._finite(value)) is not None
                    }
                )
                if tier == _api._SUMMARY_TIER:
                    entry.tiers.setdefault(box_analysis.FAST_TIER, entry.tiers[tier])
            result = {}
            for key in wanted:
                tier = _api._metric_tier(key)
                result[key] = entry.tiers[tier].get(key)
            return _api._readonly(result)

    def _can_batch(self, keys: Sequence[str]) -> bool:
        """Whether the requested built-in tiers have a batch-safe mapping."""
        tiers = _api._requested_tiers(keys)
        if box_analysis.COMPOSITE_TIER in tiers:
            return False
        if (
            box_analysis.FAST_TIER in tiers
            and not self._uses_default_fast_analyzer
        ):
            return False
        if _api._SUMMARY_TIER in tiers and not self._uses_default_summary_analyzer:
            return False
        return bool(tiers)

    def _batch_values(
        self,
        profiles: Sequence[object],
        keys: Sequence[str],
    ) -> tuple[Mapping[str, float | None], ...]:
        """Analyze missing built-in fast/summary cache entries as one batch."""
        tiers = _api._requested_tiers(keys)
        include_effective_stp = _api._SUMMARY_TIER in tiers
        with self._lock:
            entries = [self._entry(profile) for profile in profiles]
            for entry in entries:
                if _api._SUMMARY_TIER in entry.tiers:
                    entry.tiers.setdefault(
                        box_analysis.FAST_TIER,
                        entry.tiers[_api._SUMMARY_TIER],
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
                    raise _api.ProfileMetricsError(
                        "batch profile analysis returned the wrong profile count"
                    )
                for index, analyzed in zip(missing, rows):
                    normalized = _api._readonly(
                        {
                            str(key): number
                            for key, value in analyzed.items()
                            if (number := _api._finite(value)) is not None
                        }
                    )
                    entry = entries[index]
                    if include_effective_stp:
                        entry.tiers[_api._SUMMARY_TIER] = normalized
                        entry.tiers.setdefault(box_analysis.FAST_TIER, normalized)
                    else:
                        entry.tiers[box_analysis.FAST_TIER] = normalized

            return tuple(
                _api._readonly(
                    {
                        key: entry.tiers[_api._metric_tier(key)].get(key)
                        for key in keys
                    }
                )
                for entry in entries
            )

    def values_many(
        self,
        profiles: Iterable[object],
        keys: Iterable[str] | str = _api.DEFAULT_METRIC_KEYS,
    ) -> tuple[Mapping[str, float | None], ...]:
        """Return ordered metrics for several profiles, batching when supported.

        A caller can optimistically use this method without giving up the
        established analyzer contract: custom analyzers and unsupported tiers
        keep the serial path, while the built-in fast/summary tiers share one
        native batch operation and populate the same per-profile cache.
        """

        items = tuple(profiles)
        wanted = _api._normalize_keys(keys)
        if not items:
            return ()
        if self._can_batch(wanted) and all(profile is not None for profile in items):
            return self._batch_values(items, wanted)
        return tuple(self.values(profile, wanted) for profile in items)

    def timeline(
        self,
        collection,
        keys: Iterable[str] | str = _api.DEFAULT_METRIC_KEYS,
        member: str | None = None,
        *,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        resume: Iterable[_api.MetricSample] = (),
    ) -> tuple[_api.MetricSample, ...]:
        """Return exact-time outcomes; resume only failed/withheld diagnostics."""
        wanted = _api._normalize_keys(keys)
        dates = _api._collection_dates(collection)
        profiles = _api._profiles_by_member(collection)
        selected = (
            str(member)
            if member is not None
            else _api._highlighted_member(collection, profiles)
        )
        inputs = tuple(_api._profile_at(profiles, selected, index) for index in range(len(dates)))
        request_key = (
            id(collection), wanted, selected, dates,
            tuple((id(profile), _api._profile_signature(profile)) for profile in inputs),
        )
        saved = tuple(resume)
        if saved and (
            len(saved) != len(dates)
            or any(
                sample._request_key != request_key or sample.valid_time != date
                for sample, date in zip(saved, dates)
            )
        ):
            raise ValueError("timeline resume belongs to another saved request")
        samples = []
        done = 0
        for index, (date, profile) in enumerate(zip(dates, inputs)):
            previous = saved[index] if saved else None
            if previous is not None and previous.outcome in {"completed", "unavailable"}:
                sample = previous
            elif profile is None:
                sample = _api.MetricSample(
                    date, selected, _api._empty_values(wanted), False,
                    "collection has no profile members" if selected is None
                    else "member has no profile at this valid time",
                    "unavailable", request_key,
                )
            elif cancel is not None and cancel():
                sample = _api.MetricSample(
                    date, selected, _api._empty_values(wanted), False,
                    "diagnostic withheld by cancellation", "cancelled", request_key,
                )
            else:
                try:
                    values = self.values(profile, wanted)
                except _api.ProfileMetricsError as exc:
                    sample = _api.MetricSample(
                        date, selected, _api._empty_values(wanted), False, str(exc),
                        "failed", request_key,
                    )
                else:
                    sample = _api.MetricSample(
                        date, selected, values, True, outcome="completed", _request_key=request_key,
                    )
            samples.append(sample)
            if sample.outcome != "cancelled":
                done += 1
                if progress is not None:
                    progress(done, len(dates))
        return tuple(samples)

    def compare(
        self,
        collections: Iterable[object],
        keys: Iterable[str] | str = _api.DEFAULT_METRIC_KEYS,
        valid_time: datetime | None = None,
        reference_index: int = 0,
        *,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        resume: Iterable[_api.ComparisonSample] = (),
    ) -> tuple[_api.ComparisonSample, ...]:
        """Compare collections only at an exact shared valid datetime.

        Values remain available for an intentional spatial comparison.  The
        attached basis says which scalar deltas are incompatible because the
        calculation conventions differ, and preserves unknown provenance as
        unknown instead of assuming equivalence.
        """
        wanted = _api._normalize_keys(keys)
        collections = tuple(collections)
        requested = valid_time
        if requested is None and collections:
            requested = _api._current_date(collections[0])
        try:
            reference_index = int(reference_index)
        except (TypeError, ValueError):
            reference_index = 0
        if not 0 <= reference_index < len(collections):
            reference_index = 0
        inputs, contexts = [], []
        for collection_index, collection in enumerate(collections):
            label = _api._collection_label(collection, collection_index)
            dates = _api._collection_dates(collection)
            profiles = _api._profiles_by_member(collection)
            index = _api._date_index(dates, requested)
            current = _api._current_date(collection)
            member = _api._highlighted_member(collection, profiles) if index is not None else None
            profile = _api._profile_at(profiles, member, index) if index is not None else None
            if profile is None and index is not None:
                member, profile = _api._first_profile_at(profiles, index)
            selected_time = dates[index] if index is not None else current
            inputs.append((label, selected_time, member, profile, index is not None))
            contexts.append(context_for_collection(
                collection, collection_index, requested_time=requested,
                selected_time=selected_time, label=label,
            ))
        request_key = (
            tuple(id(collection) for collection in collections), wanted, requested,
            reference_index, tuple(contexts),
            tuple((member, id(profile), _api._profile_signature(profile)) for _, _, member, profile, _ in inputs),
        )
        saved = tuple(resume)
        if saved and (
            len(saved) != len(collections)
            or any(row._request_key != request_key or row.collection_index != index
                   for index, row in enumerate(saved))
        ):
            raise ValueError("comparison resume belongs to another saved request")
        rows, done = [], 0
        for collection_index, (label, selected_time, member, profile, aligned) in enumerate(inputs):
            previous = saved[collection_index] if saved else None
            if previous is not None and previous.outcome in {"completed", "unavailable"}:
                row = previous
            else:
                values, available = _api._empty_values(wanted), False
                if not aligned:
                    outcome, reason = "unavailable", "requested valid time is unavailable"
                elif profile is None:
                    outcome, reason = "unavailable", "collection has no profile at this valid time"
                elif cancel is not None and cancel():
                    outcome, reason = "cancelled", "diagnostic withheld by cancellation"
                else:
                    try:
                        values = self.values(profile, wanted)
                    except _api.ProfileMetricsError as exc:
                        outcome, reason = "failed", str(exc)
                    else:
                        outcome, reason, available = "completed", None, True
                row = _api.ComparisonSample(
                    collection_index, label, requested, selected_time, member,
                    values, available, aligned, reason, outcome=outcome,
                    _request_key=request_key,
                )
            rows.append(row)
            if row.outcome != "cancelled":
                done += 1
                if progress is not None:
                    progress(done, len(collections))
        if not rows:
            return ()
        reference_context = contexts[reference_index]
        return tuple(
            replace(
                row,
                context=context,
                basis=assess_compatibility(reference_context, context, wanted),
            )
            for row, context in zip(rows, contexts)
        )

    def ensemble(
        self,
        collection,
        keys: Iterable[str] | str = _api.DEFAULT_METRIC_KEYS,
        valid_time: datetime | None = None,
        pressure_levels: Iterable[float] | None = None,
        *,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        resume: _api.EnsembleSummary | None = None,
    ) -> _api.EnsembleSummary:
        """Summarize all members at one time without changing selection."""
        wanted = _api._normalize_keys(keys)
        dates = _api._collection_dates(collection)
        profiles = _api._profiles_by_member(collection)
        member_names = tuple(str(member) for member in profiles)
        acquisition = EnsembleAcquisition.from_collection(collection)
        requested_names = acquisition.requested_members or member_names
        loaded_names = tuple(
            member for member in acquisition.loaded_members if member in profiles
        )
        # Old or hand-built collections may not carry the new ledger.  The
        # in-memory profile map remains authoritative for what is actually
        # loaded; never turn an omitted metadata field into a missing member.
        for member in member_names:
            if member not in loaded_names:
                loaded_names += (member,)
        requested_count = len(requested_names)
        loaded_count = len(loaded_names)
        requested = valid_time if valid_time is not None else _api._current_date(collection)
        index = _api._date_index(dates, requested)
        if pressure_levels is None:
            levels = _api.DEFAULT_PRESSURE_LEVELS
        else:
            levels = tuple(
                dict.fromkeys(
                    number
                    for level in pressure_levels
                    if (number := _api._finite(level)) is not None and number > 0.0
                )
            )
            if not levels:
                raise ValueError("pressure_levels must contain a positive value")

        scope = tuple(dict.fromkeys((*loaded_names, *requested_names)))
        inputs = tuple((member, _api._profile_at(profiles, member, index)
                        if index is not None and member in loaded_names else None)
                       for member in scope)
        request_key = (
            id(collection), wanted, requested, levels, scope, loaded_names,
            tuple((item.member, item.status, item.reason) for item in acquisition.failures),
            tuple(str(_api._metadata(collection, key)) for key in ("model", "loc", "run")),
            tuple((member, id(profile), _api._profile_signature(profile) if profile is not None else None)
                  for member, profile in inputs),
        )
        if resume is not None and (
            not isinstance(resume, _api.EnsembleSummary) or resume._request_key != request_key
            or tuple(row.member for row in resume.member_samples) != scope
        ):
            raise ValueError("ensemble outcomes do not match the saved request")
        saved = {row.member: row for row in resume.member_samples} if resume is not None else {}
        candidates = tuple((member, profile) for member, profile in inputs
                           if profile is not None and (member not in saved
                           or saved[member].outcome not in {"completed", "unavailable"}))

        batched = None
        if candidates and self._can_batch(wanted) and not (cancel is not None and cancel()):
            try:
                rows = self._batch_values(
                    tuple(profile for _member_name, profile in candidates),
                    wanted,
                )
                # An in-flight native call cannot be interrupted. Keep every
                # completed contribution even if Cancel arrived during it.
                batched = dict(zip((member for member, _profile in candidates), rows))
            except _api.ProfileMetricsError:
                batched = None
            except Exception:
                # Batch packing and native execution are optional optimizations;
                # the established per-profile path retains failure isolation.
                batched = None

        samples, done = [], 0
        for member_name, profile in inputs:
            previous = saved.get(member_name)
            if previous is not None and previous.outcome in {"completed", "unavailable"}:
                row = previous
            elif profile is None:
                failure = acquisition.failure_for(member_name) if member_name else None
                reason = ("requested valid time is unavailable" if index is None
                          else "member profile is absent at the requested valid time" if member_name in loaded_names
                          else f"member is not loaded ({failure.status}: {failure.reason or 'no detail'})" if failure
                          else "member is not loaded")
                row = _api.EnsembleMemberSample(member_name, "unavailable", reason=reason)
            elif (batched is None or member_name not in batched) and cancel is not None and cancel():
                row = _api.EnsembleMemberSample(member_name, "cancelled", reason="diagnostic withheld by cancellation")
            else:
                try:
                    metrics = batched[member_name] if batched is not None else self.values(profile, wanted)
                    row = _api.EnsembleMemberSample(
                        member_name, "completed", _api._readonly(metrics),
                        _api._interpolate_column(profile, "tmpc", levels),
                        _api._interpolate_column(profile, "dwpc", levels),
                    )
                except _api.ProfileMetricsError as exc:
                    row = _api.EnsembleMemberSample(member_name, "failed", reason=str(exc))
            samples.append(row)
            if row.outcome != "cancelled":
                done += 1
                if progress is not None:
                    progress(done, len(scope))

        usable = tuple(row for row in samples if row.outcome == "completed")
        available_names = tuple(row.member for row in usable)
        missing_names = tuple(row.member for row in samples if row.outcome != "completed")
        unusable_names = tuple(row.member for row in samples
                              if row.member in loaded_names and row.outcome in {"failed", "unavailable"})
        metric_rows = tuple(row.values for row in usable)
        temperature_rows = tuple(row.temperature for row in usable)
        dewpoint_rows = tuple(row.dewpoint for row in usable)

        scalar = _api._readonly(
            {key: _api._distribution(row.get(key) for row in metric_rows) for key in wanted}
        )
        bands = tuple(
            _api.ThermodynamicBand(
                pressure_hpa=level,
                temperature=_api._distribution(row[level_index] for row in temperature_rows),
                dewpoint=_api._distribution(row[level_index] for row in dewpoint_rows),
            )
            for level_index, level in enumerate(levels)
        )
        available = bool(available_names)
        distribution_available = len(available_names) >= 2
        if index is None:
            reason = "requested valid time is unavailable"
        elif not available:
            reason = "no ensemble members are usable for the requested diagnostics"
        elif not distribution_available:
            reason = (
                "one member is usable; values are retained but at least two "
                "usable members are required for an ensemble distribution"
            )
        else:
            reason = None
        return _api.EnsembleSummary(
            valid_time=dates[index] if index is not None else requested,
            member_count=requested_count,
            available_member_count=len(available_names),
            members=tuple(available_names),
            missing_members=missing_names,
            scalar=scalar,
            bands=bands if index is not None else (),
            available=available,
            reason=reason,
            requested_member_count=requested_count,
            loaded_member_count=loaded_count,
            requested_members=requested_names,
            loaded_members=loaded_names,
            failed_members=acquisition.failed_members,
            cancelled_members=acquisition.cancelled_members,
            unusable_members=tuple(dict.fromkeys(unusable_names)),
            distribution_available=distribution_available,
            member_samples=tuple(samples),
            _request_key=request_key,
        )
