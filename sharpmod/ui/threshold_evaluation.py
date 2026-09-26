"""ThresholdEvaluation methods for EnsembleThresholdExplorer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from sharpmod.analysis.ensemble_thresholds import MEMBER_FAILED
from sharpmod.analysis.ensemble_thresholds import MEMBER_NONQUALIFYING
from sharpmod.analysis.ensemble_thresholds import MEMBER_NOT_EVALUATED
from sharpmod.analysis.ensemble_thresholds import MEMBER_NOT_LOADED
from sharpmod.analysis.ensemble_thresholds import MEMBER_NO_PROFILE
from sharpmod.analysis.ensemble_thresholds import MEMBER_QUALIFYING
from sharpmod.analysis.ensemble_thresholds import MEMBER_UNUSABLE
from sharpmod.ui.features.gui_common import set_status_label
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.analysis.profile_metrics import freeze_collection
from sharpmod.ui.features.gui_ensemble_thresholds import (
    _ThresholdRequest,
    _ThresholdWorker
)


class ThresholdEvaluationMixin:
    """Focused workspace behavior."""

    def _evaluate(self, timeline):
        if self._collection is None or self._worker is not None:
            return
        try:
            definition = self.definition()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._token += 1
        frozen = freeze_collection(self._collection)
        valid_times = (
            tuple(frozen._dates)
            if timeline
            else (frozen.current_date,)
        )
        acquisition = EnsembleAcquisition.from_collection(frozen)
        requested = tuple(acquisition.requested_members) or tuple(frozen._profs)
        requested = tuple(dict.fromkeys((*requested, *tuple(frozen._profs))))
        total = len(valid_times) * len(requested)
        request = _ThresholdRequest(
            self._token,
            self._token,
            frozen,
            id(self._collection),
            definition,
            bool(timeline),
            valid_times,
            total,
            self._request_input_label(
                frozen, definition, valid_times, len(requested), bool(timeline)
            ),
        )
        self._last_request = request
        self._start_request(request)

    @staticmethod
    def _time_text(value):
        return (
            value.strftime("%Y-%m-%d %H:%MZ")
            if isinstance(value, datetime)
            else "time not reported"
        )

    @classmethod
    def _request_input_label(
        cls, collection, definition, valid_times, member_count, timeline
    ):
        location = str(collection._meta.get("loc", "") or "").strip()
        model = str(collection._meta.get("model", "") or "").strip()
        source = (
            " · ".join(part for part in (location, model) if part)
            or "Ensemble sounding"
        )
        if timeline:
            if valid_times:
                time_scope = (
                    f"{len(valid_times)} exact valid times, "
                    f"{cls._time_text(valid_times[0])} to "
                    f"{cls._time_text(valid_times[-1])}"
                )
            else:
                time_scope = "0 exact valid times"
        else:
            time_scope = (
                f"focused exact valid time {cls._time_text(valid_times[0])}"
            )
        return (
            f"{source} · {time_scope} · {member_count} requested members · "
            f"{definition.summary}"
        )

    def _start_request(self, request):
        self._active_request = request
        self._cancel_requested = False
        worker = _ThresholdWorker(request, self.engine, parent=self)
        self._worker = worker
        worker.progress.connect(self._progress)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.evaluate_current.setEnabled(False)
        self.evaluate_timeline.setEnabled(False)
        self.job.begin(
            request.token,
            "Ensemble threshold evaluation",
            request.affected_input,
            (
                "Evaluating the saved rule across exact valid times"
                if request.timeline
                else "Evaluating the saved rule at the focused exact valid time"
            ),
            total=request.total,
            unit="member checks",
            retained=self._retained_result_label(),
            cancellable=True,
        )
        set_status_label(
            self.status,
            "Evaluating member-by-member across exact valid times…"
            if request.timeline
            else "Evaluating members at the focused exact valid time…",
        )
        worker.start()

    def _progress(self, token, done, total):
        request = self._active_request
        if (
            request is None
            or int(token) != self._token
            or int(token) != request.token
            or request.source_marker != id(self._collection)
        ):
            return
        self.job.update(
            token,
            stage=(
                "Evaluating the saved rule across exact valid times"
                if request.timeline
                else "Evaluating the saved rule at the focused exact valid time"
            ),
            done=done,
            total=total,
        )

    def cancel_pending(self):
        request = self._active_request
        if self._worker is None or request is None:
            return
        self._cancel_requested = True
        self.job.request_cancel(request.token)
        self._worker.requestInterruption()
        set_status_label(
            self.status, "Cancelling after the current member/time…", level="warn"
        )

    def shutdown(self):
        worker = self._worker
        if worker is None:
            return
        self._token += 1
        self._cancel_requested = True
        if self.job.snapshot is not None:
            self.job.invalidate(
                "Threshold explorer closed; the request was superseded."
            )
        self._active_request = None
        worker.requestInterruption()
        worker.wait(2000)
        if worker.isRunning():
            from sharpmod.ui.features.gui_threading import retain_worker_until_finished

            retain_worker_until_finished(worker)
        self._worker = None

    def _completed(self, token, results):
        request = self._active_request
        if (
            request is None
            or int(token) != self._token
            or int(token) != request.token
            or request.source_marker != id(self._collection)
            or not self.job.accepts(token)
        ):
            return
        results = tuple(results)
        queued_complete = (
            self._cancel_requested
            and sum(len(result.members) for result in results) == request.total
            and not any(result.cancelled for result in results)
        )
        if queued_complete:
            self.job.finish(
                token,
                counts=JobCounts(cancelled=request.total, requested=request.total),
                outcome="cancelled",
                message=(
                    "Threshold evaluation was cancelled before its queued complete "
                    "result was displayed; the earlier result remains unchanged."
                ),
                retryable=True,
            )
            self.job.retry_button.setEnabled(False)
            set_status_label(
                self.status,
                "Threshold evaluation cancelled; the earlier displayed result remains unchanged.",
                level="warn",
            )
            return

        cancelled = self._cancel_requested or any(
            result.cancelled for result in results
        )
        counts = self._job_counts(results, request.total, cancelled=cancelled)
        if results:
            self._results = results
            self._result_collection = self._collection
            self._result_request_key = request.key
            self._render_results()
        elif not self._results:
            set_status_label(
                self.status,
                "Evaluation cancelled before any member reached a final outcome.",
                level="warn",
            )
        self.job.update(
            token,
            done=counts.completed + counts.failed + counts.unavailable,
            retained=self._retained_result_label(),
        )
        message = (
            "Threshold evaluation cancelled; completed results remain usable."
            if counts.cancelled
            else "Threshold evaluation completed with member-level failures."
            if counts.failed
            else "Threshold evaluation completed with unavailable members."
            if counts.unavailable
            else "Threshold evaluation completed."
        )
        self.job.finish(
            token,
            counts=counts,
            message=message,
            retryable=bool(counts.failed or counts.cancelled),
        )
        self.job.retry_button.setEnabled(False)

    def _failed(self, token, message):
        request = self._active_request
        if (
            request is None
            or int(token) != self._token
            or int(token) != request.token
            or request.source_marker != id(self._collection)
            or not self.job.accepts(token)
        ):
            return
        if self._cancel_requested:
            self.job.finish(
                token,
                counts=JobCounts(cancelled=request.total, requested=request.total),
                outcome="cancelled",
                message=(
                    "Threshold evaluation was cancelled; the earlier displayed "
                    "result remains unchanged."
                ),
                retryable=True,
            )
            set_status_label(
                self.status, "Threshold evaluation cancelled.", level="warn"
            )
        else:
            failed = min(1, request.total)
            self.job.finish(
                token,
                counts=JobCounts(failed=failed, requested=request.total),
                outcome="failed",
                message=f"Threshold evaluation failed: {message}",
                retryable=True,
            )
            self._set_error(f"Threshold evaluation failed: {message}")
        self.job.retry_button.setEnabled(False)

    @staticmethod
    def _job_counts(results, total, *, cancelled=False):
        completed = sum(
            result.state_counts[MEMBER_QUALIFYING]
            + result.state_counts[MEMBER_NONQUALIFYING]
            for result in results
        )
        failed = sum(result.state_counts[MEMBER_FAILED] for result in results)
        unavailable = sum(
            result.state_counts[MEMBER_UNUSABLE]
            + result.state_counts[MEMBER_NO_PROFILE]
            + result.state_counts[MEMBER_NOT_LOADED]
            for result in results
        )
        withheld = sum(
            result.state_counts[MEMBER_NOT_EVALUATED] for result in results
        )
        missing = max(
            0, int(total) - completed - failed - unavailable - withheld
        )
        cancelled_count = withheld + missing if cancelled or withheld else 0
        return JobCounts(
            completed=completed,
            failed=failed,
            cancelled=cancelled_count,
            unavailable=unavailable,
            requested=total,
        )

    def _retained_result_label(self):
        if not self._results:
            return ""
        result = self._selected_result() or self._results[0]
        valid = self._time_text(result.valid_time)
        scope = (
            f"{len(self._results)} exact valid times"
            if len(self._results) != 1
            else "1 exact valid time"
        )
        return (
            f"{result.definition.name} · selected {valid} · {scope} · "
            f"{result.requested_member_count} requested members"
        )

    def _retry_evaluation(self):
        request = self._last_request
        if (
            request is None
            or self._worker is not None
            or self._collection is None
            or request.source_marker != id(self._collection)
        ):
            return
        resume = (
            self._results
            if self._result_request_key == request.key
            else request.resume
        )
        self._token += 1
        retried = replace(request, token=self._token, resume=tuple(resume))
        self._last_request = retried
        self._start_request(retried)

    def _finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self.refresh(self._collection)
        snapshot = self.job.snapshot
        if (
            snapshot is not None
            and snapshot.retryable
            and self._last_request is not None
            and self._last_request.source_marker == id(self._collection)
        ):
            self.job.retry_button.setEnabled(True)
