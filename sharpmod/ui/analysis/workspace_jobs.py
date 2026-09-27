"""WorkspaceJobs behavior for the sounding analysis workspace."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timezone
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.ui.analysis.charts import _collection_label
from sharpmod.ui.analysis.charts import _format_time
from sharpmod.ui.analysis.charts import _get
from sharpmod.ui.analysis.charts import _time_scope
from sharpmod.ui.analysis.jobs import _ComputeTask
from sharpmod.ui.analysis.workspace import _LOGGER


class WorkspaceJobsMixin:
    """Focused methods shared by AnalysisWorkspace."""

    def cancel_pending(self):
        """Forget queued work without waiting for an in-flight calculation."""
        for task in self._tasks.values():
            task.cancel_event.set()
        for kind in self._generation:
            self._generation[kind] += 1
            # Results already in flight are now stale and will never reach
            # _accept_result, so clear the busy latch here or Refresh stays dead.
            self._set_busy(kind, False)
        self._take_queued_tasks()
        self._trend_request = self._trend_run = None
        self.trend_job.invalidate()
        self._compare_request = self._compare_run = None
        self.compare_job.invalidate()
        self._ensemble_request = self._ensemble_run = None
        self.ensemble_job.invalidate()

    def _take_queued_tasks(self):
        """Remove this workspace's not-yet-running jobs from the shared pool."""
        for key, task in tuple(self._tasks.items()):
            try:
                removed = self._pool.tryTake(task)
            except RuntimeError:
                removed = False
            if removed:
                self._tasks.pop(key, None)
                task.cancel_event.set()
                task.done_event.set()
                if task.generation == self._generation[task.kind]:
                    self._generation[task.kind] += 1
                    self._set_busy(task.kind, False)
                    if task.kind == "trends":
                        self._trend_request = self._trend_run = None
                        self.trend_job.invalidate("Queued timeline was superseded by another analysis.")
                    elif task.kind == "compare":
                        self._compare_request = self._compare_run = None
                        self.compare_job.invalidate("Queued comparison was superseded by another analysis.")
                    elif task.kind == "ensemble":
                        self._ensemble_request = self._ensemble_run = None
                        self.ensemble_job.invalidate("Queued ensemble summary was superseded by another analysis.")

    def shutdown(self):
        """Make outstanding results inert; closing a window remains instant."""
        self.cancel_pending()
        self.threshold_explorer.shutdown()
        self.animation_workspace.shutdown()

    def _refresh_active_tab(self, index=None):
        index = self.tabs.currentIndex() if index is None else int(index)
        if index == self.TAB_TRENDS:
            self._refresh_trends()
        elif index == self.TAB_COMPARE:
            self._refresh_compare()
        elif index == self.TAB_ENSEMBLE:
            self._refresh_ensemble()
        elif index == self.TAB_ANIMATION:
            self.animation_workspace.refresh()

    def _request(self, kind, function, *, request=None):
        self._take_queued_tasks()
        for task in self._tasks.values():
            if task.kind == kind:
                task.cancel_event.set()
        self._generation[kind] += 1
        generation = self._generation[kind]
        self._set_busy(kind, True)
        # Keep at most one queued analysis behind the running one. Rapid slider
        # or selector changes discard work which has not begun yet; the token
        # below discards a result from any task already in flight.
        task = _ComputeTask(generation, kind, function)
        task.signals.completed.connect(self._accept_result)
        task.signals.failed.connect(self._accept_failure)
        task.signals.progress.connect(self._accept_progress)
        self._tasks[(kind, generation)] = task
        if request is not None and kind == "trends":
            self._trend_request, self._trend_run = request, None
            self.trend_job.begin(
                generation, "Timeline diagnostics", request.affected_input,
                "Calculating saved exact-time profiles", total=request.total,
                unit="valid-time profiles" if request.native else "timelines",
                retained=self._trend_retained, cancellable=True,
            )
        elif request is not None and kind == "compare":
            self._compare_request, self._compare_run = request, None
            self.compare_job.begin(
                generation, "Comparison diagnostics", request.affected_input,
                "Aligning saved exact-time soundings", total=request.total,
                unit="soundings" if request.native else "comparisons",
                retained=self._compare_retained, cancellable=True,
            )
        elif request is not None and kind == "ensemble":
            self._ensemble_request, self._ensemble_run = request, None
            self.ensemble_job.begin(
                generation, "Ensemble diagnostics", request.affected_input,
                "Calculating saved member diagnostics and vertical envelope", total=request.total,
                unit="member diagnostics" if request.native else "ensemble summaries",
                retained=self._ensemble_retained, cancellable=True,
            )
        if self._async_compute:
            self._pool.start(task)
        else:
            task.run()

    def _accept_progress(self, generation, kind, done, total):
        if kind == "trends" and generation == self._generation[kind]:
            self.trend_job.update(generation, done=done, total=total)
        elif kind == "compare" and generation == self._generation[kind]:
            self.compare_job.update(generation, done=done, total=total)
        elif kind == "ensemble" and generation == self._generation[kind]:
            self.ensemble_job.update(generation, done=done, total=total)

    def _cancel_analysis(self, kind):
        generation = self._generation[kind]
        task = self._tasks.get((kind, generation))
        if task is None:
            return
        if kind == "trends":
            self.trend_job.request_cancel(generation)
        elif kind == "compare":
            self.compare_job.request_cancel(generation)
        elif kind == "ensemble":
            self.ensemble_job.request_cancel(generation)
        task.cancel_event.set()

    def _retry_trends(self):
        request = self._trend_request
        if request is None or not self.trend_job.snapshot.retryable:
            return
        if ("trends", self._generation["trends"]) in self._tasks:
            return
        if request.source_ids != tuple(id(item) for item in self._collections()):
            self.trend_job.invalidate("Loaded soundings changed; refresh a new timeline instead.")
            return
        self._start_trends(replace(request, resume=self._trend_run))

    def _retry_comparison(self):
        request = self._compare_request
        if request is None or not self.compare_job.snapshot.retryable:
            return
        if ("compare", self._generation["compare"]) in self._tasks:
            return
        if request.source_ids != tuple(id(item) for item in self._collections()):
            self.compare_job.invalidate("Loaded soundings changed; refresh a new comparison instead.")
            return
        self._start_comparison(replace(request, resume=self._compare_run))

    def _retry_ensemble_summary(self):
        request = self._ensemble_request
        if request is None or not self.ensemble_job.snapshot.retryable:
            return
        if ("ensemble", self._generation["ensemble"]) in self._tasks:
            return
        if request.source_id != id(self._focused_collection()):
            self.ensemble_job.invalidate("Focused sounding changed; refresh a new summary instead.")
            return
        self._start_ensemble(replace(request, resume=self._ensemble_run))

    def _accept_result(self, generation, kind, result):
        self._tasks.pop((str(kind), int(generation)), None)
        if int(generation) != self._generation.get(str(kind)):
            return
        self._set_busy(kind, False)
        if kind == "trends":
            if not self.trend_job.accepts(generation) or result.request is not self._trend_request:
                return
            if result.request.source_ids != tuple(id(item) for item in self._collections()):
                self.trend_job.invalidate("Loaded soundings changed; old timeline results were not applied.")
                return
            # Discard consumed resume chains; retain only the newest outcome set.
            self._trend_request = replace(result.request, resume=None)
            self._trend_run = replace(result, request=self._trend_request)
            counts = result.counts
            cancelled = self.trend_job.snapshot.state == "cancelling"
            if not cancelled and (counts.completed or not self._trend_series):
                self._render_trends(result.results, keys=result.request.keys)
                if counts.completed:
                    times = _time_scope(
                        _get(sample, "valid_time")
                        for entry in result.results for sample in entry[3]
                        if _get(sample, "available", False)
                    )
                    self._trend_retained = (
                        "; ".join(result.request.labels) + " · valid " + times
                        + " · updated " + _format_time(datetime.now(timezone.utc))
                    )
                    self.trend_job.update(generation, retained=self._trend_retained)
            elif not cancelled:
                self._set_status(self.trend_status, "No new usable diagnostics; prior timeline retained.", level="warn")
            self.trend_job.finish(
                generation, counts=counts, outcome="cancelled" if cancelled else None,
                message=("Timeline cancelled; completed diagnostics saved for retry." if cancelled
                         else next((f"Diagnostic failed: {error}. Saved successes retained; retry unfinished work."
                                    for error in result.errors if error),
                                   "Saved timelines updated; unavailable profiles remain marked as gaps."
                                   if counts.unavailable else "Saved timelines updated.")),
                retryable=bool(cancelled or counts.failed or counts.cancelled or counts.unattempted),
            )
        elif kind == "compare":
            if not self.compare_job.accepts(generation) or result.request is not self._compare_request:
                return
            if result.request.source_ids != tuple(id(item) for item in self._collections()):
                self.compare_job.invalidate("Loaded soundings changed; old comparison was not applied.")
                return
            self._compare_request = replace(result.request, resume=None)
            self._compare_run = replace(result, request=self._compare_request)
            counts = result.counts
            cancelled = self.compare_job.snapshot.state == "cancelling"
            if not cancelled and (counts.completed or not self._comparison_samples):
                self._render_compare(result.samples, request=result.request)
                if counts.completed:
                    reference_label = _collection_label(
                        result.request.collections[result.request.reference], result.request.reference,
                    )
                    self._compare_retained = (
                        f"Reference {reference_label} · valid {_format_time(result.request.valid_time)}"
                        f" · updated {_format_time(datetime.now(timezone.utc))}"
                    )
                    self.compare_job.update(generation, retained=self._compare_retained)
            elif not cancelled:
                self._set_status(self.compare_status, "No new usable rows; prior comparison retained.", level="warn")
            error = next((str(_get(row, "reason")) for row in result.samples
                          if _get(row, "outcome") == "failed"), "")
            self.compare_job.finish(
                generation, counts=counts, outcome="cancelled" if cancelled else None,
                message=("Comparison cancelled; completed diagnostics saved for retry." if cancelled
                         else f"Diagnostic failed: {error}. Saved successes retained; retry unfinished work." if error
                         else "Saved comparison updated; unavailable rows remain explicit." if counts.unavailable
                         else "Saved comparison updated."),
                retryable=bool(cancelled or counts.failed or counts.cancelled or counts.unattempted),
            )
        elif kind == "ensemble":
            if not self.ensemble_job.accepts(generation) or result.request is not self._ensemble_request:
                return
            if result.request.source_id != id(self._focused_collection()):
                self.ensemble_job.invalidate("Focused sounding changed; old ensemble summary was not applied.")
                return
            self._ensemble_request = replace(result.request, resume=None)
            self._ensemble_run = replace(result, request=self._ensemble_request)
            counts = result.counts
            cancelled = self.ensemble_job.snapshot.state == "cancelling"
            if not cancelled and (counts.completed or not _get(self._ensemble_summary, "available", False)):
                self._render_ensemble(result.summary, request=result.request)
                if counts.completed:
                    self._ensemble_retained = (
                        f"{result.request.label} · valid {_format_time(_get(result.summary, 'valid_time'))}"
                        f" · updated {_format_time(datetime.now(timezone.utc))}"
                    )
                    self.ensemble_job.update(generation, retained=self._ensemble_retained)
            elif not cancelled:
                self._set_status(self.ensemble_status, "No new usable diagnostics; prior summary retained.", level="warn")
            error = next((str(row.reason) for row in _get(result.summary, "member_samples", ())
                          if row.outcome == "failed"), "")
            request_empty = not result.request.total
            self.ensemble_job.finish(
                generation, counts=counts,
                outcome="cancelled" if cancelled else "partial" if request_empty else None,
                message=("Ensemble summary cancelled; completed contributions saved for retry." if cancelled
                         else "No member diagnostics were requested." if request_empty
                         else f"Diagnostic failed: {error}. Saved successes retained; retry unfinished work." if error
                         else "Saved ensemble summary updated; unavailable members remain explicit." if counts.unavailable
                         else "Saved ensemble summary updated."),
                retryable=bool(cancelled or counts.failed or counts.cancelled or counts.unattempted),
            )

    def _accept_failure(self, generation, kind, message):
        self._tasks.pop((str(kind), int(generation)), None)
        if int(generation) != self._generation.get(str(kind)):
            return
        if kind == "trends" and not self.trend_job.accepts(generation):
            return
        if kind == "compare" and not self.compare_job.accepts(generation):
            return
        if kind == "ensemble" and not self.ensemble_job.accepts(generation):
            return
        self._set_busy(kind, False)
        if kind == "trends" and self.trend_job.accepts(generation):
            total = self._trend_request.total
            self.trend_job.finish(
                generation, counts=JobCounts(failed=min(1, total), requested=total),
                outcome="failed", message=f"Timeline failed: {message}", retryable=True,
            )
        elif kind == "compare" and self.compare_job.accepts(generation):
            total = self._compare_request.total
            self.compare_job.finish(
                generation, counts=JobCounts(failed=1, requested=total),
                outcome="failed", message=f"Comparison failed: {message}", retryable=True,
            )
        elif kind == "ensemble" and self.ensemble_job.accepts(generation):
            total = self._ensemble_request.total
            self.ensemble_job.finish(
                generation, counts=JobCounts(failed=min(1, total), requested=total),
                outcome="failed", message=f"Ensemble summary failed: {message}", retryable=True,
            )
        label = {
            "trends": self.trend_status,
            "compare": self.compare_status,
            "ensemble": self.ensemble_status,
        }.get(str(kind))
        if label is not None:
            self._set_status(
                label, f"Analysis unavailable: {message}", level="error"
            )
        _LOGGER.warning("analysis_workspace.%s_failed: %s", kind, message)
