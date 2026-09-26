"""Bounded analysis jobs, request snapshots, and cancellation state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Event

from qtpy.QtCore import QObject, QRunnable, QThreadPool, Signal

from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.analysis.profile_metrics import CollectionSnapshot
from sharpmod.ui.analysis.charts import _get

_SHARED_POOL = None

def _analysis_pool():
    """Return one process-wide, single-worker analysis queue.

    The pool outlives individual windows. Closing a sounding therefore never
    waits for an already-running composite calculation, while every analysis
    workspace together still has a hard concurrency bound of one.
    """
    global _SHARED_POOL
    if _SHARED_POOL is None:
        _SHARED_POOL = QThreadPool()
        _SHARED_POOL.setMaxThreadCount(1)
        _SHARED_POOL.setExpiryTimeout(15_000)
    return _SHARED_POOL



@dataclass(frozen=True)
class _TrendRequest:
    collections: tuple[CollectionSnapshot, ...]
    source_ids: tuple[int, ...]
    keys: tuple[str, ...]
    labels: tuple[str, ...]
    shorts: tuple[str, ...]
    affected_input: str
    native: bool
    resume: _TrendRun | None = None

    @property
    def total(self):
        return sum(len(item._dates) for item in self.collections) if self.native else len(self.collections)


@dataclass(frozen=True)
class _TrendRun:
    request: _TrendRequest
    results: tuple
    outcomes: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def counts(self):
        tally = Counter()
        for index, (outcome, entry) in enumerate(zip(self.outcomes, self.results)):
            if not self.request.native:
                tally[outcome] += 1
            elif outcome == "failed":
                # A boundary failure is one failure, not invented per-time failures.
                tally["failed"] += min(1, len(self.request.collections[index]._dates))
            else:
                for sample in entry[3]:
                    tally[_get(sample, "outcome") or (
                        "completed" if _get(sample, "available", False) else "unavailable"
                    )] += 1
        return JobCounts(
            **{key: tally[key] for key in ("completed", "failed", "cancelled", "unavailable")},
            requested=self.request.total,
        )


def _compute_trends(engine, request, cancel, progress):
    """Coordinate existing timelines; do not reproduce metric calculations."""
    results, outcomes, errors = [], [], []
    done = 0
    for index, collection in enumerate(request.collections):
        previous = request.resume
        saved = previous.results[index][3] if previous is not None else ()
        samples = ()
        outcome, error = "completed", ""
        try:
            if request.native:
                samples = engine.timeline(
                    collection, request.keys, cancel=cancel,
                    progress=lambda count, _total, offset=done: progress(offset + count, request.total),
                    resume=saved if previous is not None and previous.outcomes[index] != "failed" else (),
                )
                done += sum(_get(sample, "outcome") != "cancelled" for sample in samples)
                error = next((
                    str(_get(sample, "reason", "Diagnostic failed"))
                    for sample in samples if _get(sample, "outcome") == "failed"
                ), "")
            elif previous is not None and previous.outcomes[index] in {"completed", "unavailable"}:
                samples, outcome = saved, previous.outcomes[index]
                done += 1
            elif cancel():
                outcome = "cancelled"
            else:
                # Custom/test engines retain their established signature. Their
                # bounded unit is a whole timeline, not an inferred date count.
                samples = tuple(engine.timeline(collection, request.keys) or ())
                outcome = "completed" if samples else "unavailable"
                done += 1
        except Exception as exc:  # noqa: BLE001 - per-timeline boundary
            outcome, error = "failed", str(exc)
            done += 1
        results.append((index, request.labels[index], request.shorts[index], tuple(samples)))
        outcomes.append(outcome)
        errors.append(error)
        progress(min(done, request.total), request.total)
    return _TrendRun(request, tuple(results), tuple(outcomes), tuple(errors))


@dataclass(frozen=True)
class _ComparisonRequest:
    collections: tuple[CollectionSnapshot, ...]
    source_ids: tuple[int, ...]
    profile_ids: tuple
    keys: tuple[str, ...]
    valid_time: object
    reference: int
    affected_input: str
    native: bool
    resume: _ComparisonRun | None = None

    @property
    def total(self):
        return len(self.collections) if self.native else 1


@dataclass(frozen=True)
class _ComparisonRun:
    request: _ComparisonRequest
    samples: tuple
    cancelled: bool = False

    @property
    def counts(self):
        if not self.request.native:
            return JobCounts(
                completed=int(bool(self.samples)), cancelled=int(self.cancelled and not self.samples),
                unavailable=int(not self.samples and not self.cancelled), requested=1,
            )
        tally = Counter(_get(row, "outcome") or (
            "completed" if _get(row, "available", False) else "unavailable"
        ) for row in self.samples)
        return JobCounts(
            **{key: tally[key] for key in ("completed", "failed", "cancelled", "unavailable")},
            requested=self.request.total,
        )


def _compute_comparison(engine, request, cancel, progress):
    previous = request.resume
    if request.native:
        samples = engine.compare(
            request.collections, request.keys, valid_time=request.valid_time,
            reference_index=request.reference, cancel=cancel, progress=progress,
            resume=previous.samples if previous is not None else (),
        )
    elif previous is not None and not (previous.cancelled and not previous.samples):
        samples = previous.samples
    elif cancel():
        return _ComparisonRun(request, (), True)
    else:
        samples = engine.compare(
            request.collections, request.keys, valid_time=request.valid_time,
            reference_index=request.reference,
        )
    samples = tuple(samples or ())
    if not request.native:
        progress(1, 1)
    return _ComparisonRun(request, samples)


@dataclass(frozen=True)
class _EnsembleRequest:
    collection: CollectionSnapshot
    source_id: int
    keys: tuple[str, ...]
    valid_time: object
    members: tuple[str, ...]
    acquisition: EnsembleAcquisition
    label: str
    affected_input: str
    native: bool
    resume: _EnsembleRun | None = None

    @property
    def total(self):
        return len(self.members) if self.native else 1


@dataclass(frozen=True)
class _EnsembleRun:
    request: _EnsembleRequest
    summary: object = None
    cancelled: bool = False

    @property
    def counts(self):
        if not self.request.native:
            available = bool(_get(self.summary, "available", False))
            return JobCounts(completed=int(available), cancelled=int(self.cancelled),
                             unavailable=int(not available and not self.cancelled), requested=1)
        tally = Counter(row.outcome for row in self.summary.member_samples)
        return JobCounts(
            **{key: tally[key] for key in ("completed", "failed", "cancelled", "unavailable")},
            requested=self.request.total,
        )


def _compute_ensemble(engine, request, cancel, progress):
    previous = request.resume
    if request.native:
        summary = engine.ensemble(
            request.collection, request.keys, valid_time=request.valid_time,
            cancel=cancel, progress=progress, resume=previous.summary if previous is not None else None,
        )
    elif previous is not None and not (previous.cancelled and previous.summary is None):
        summary = previous.summary
    elif cancel():
        return _EnsembleRun(request, cancelled=True)
    else:
        summary = engine.ensemble(request.collection, request.keys, valid_time=request.valid_time)
    if not request.native:
        progress(1, 1)
    return _EnsembleRun(request, summary)


class _TaskSignals(QObject):
    completed = Signal(int, str, object)
    failed = Signal(int, str, str)
    progress = Signal(int, str, int, int)


class _ComputeTask(QRunnable):
    """A single bounded calculation submitted to the workspace pool."""

    def __init__(self, generation, kind, function):
        super().__init__()
        self.generation = int(generation)
        self.kind = str(kind)
        self.function = function
        self.signals = _TaskSignals()
        self.cancel_event = Event()
        self.done_event = Event()
        # tryTake must never target an auto-deleted/reused runnable address.
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.function(
                self.cancel_event.is_set,
                lambda done, total: self.signals.progress.emit(self.generation, self.kind, done, total),
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.signals.failed.emit(self.generation, self.kind, str(exc))
        else:
            self.signals.completed.emit(self.generation, self.kind, result)
        finally:
            self.done_event.set()
