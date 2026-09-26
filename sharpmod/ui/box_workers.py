"""Qt workers for box extraction, mean-sounding construction, and field analysis.

They keep network and compute work off the GUI thread and return progress/results to the
picker workflow; sampling and meteorological calculations live in ``sharpmod.analysis``."""

from __future__ import annotations

from qtpy.QtCore import QThread
from qtpy.QtCore import Signal
from sharpmod.analysis import box_analysis as _analysis
from sharpmod.analysis.box_sounding import box_requests
from sharpmod.analysis.box_sounding import box_sequence_requests
from sharpmod.analysis.box_sounding import normalize_hours
from sharpmod.analysis.box_sounding import sequence_request_id
import os
from sharpmod.ui.features.gui_box import (
    BoxExtractResult
)


class BoxExtractWorker(QThread):
    """Extract plan nodes in a killable child process, streaming results back."""

    point_ready = Signal(str, int, int)     # npz path, row, col
    point_failed = Signal(int, int, str)    # row, col, message
    progress = Signal(str, int, int)        # stage, done, total
    result_ready = Signal(object)           # BoxExtractResult
    failed = Signal(str)

    def __init__(self, plan, run_time, fxx, output_dir, *, member=None,
                 loc=None, disk_cache=None, hours=None, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.run_time = run_time
        self.fxx = int(fxx)
        self.output_dir = os.fspath(output_dir)
        self.member = str(member) if member else None
        self.loc = str(loc) if loc else None
        self.disk_cache = disk_cache
        #: Forecast hours to cover. ``None`` means the single ``fxx`` above.
        self.hours = None if hours is None else tuple(hours)
        self._runner = None
        self._outputs: dict[str, str] = {}
        self._outputs_by_hour: dict[int, dict[str, str]] = {}
        self._completed = 0

    def requestInterruption(self):  # noqa: N802 - Qt API override
        super().requestInterruption()
        if self._runner is not None:
            self._runner.cancel()

    def run(self):
        from sharpmod.ui.features.gui_batch_process import (
            IsolatedBatchCancelled,
            IsolatedBatchRunner,
        )

        try:
            if self.hours is None:
                hours = (self.fxx,)
                requests = box_requests(
                    self.plan,
                    run_time=self.run_time,
                    fxx=self.fxx,
                    member=self.member,
                    loc=self.loc,
                )
            else:
                hours = normalize_hours(self.hours)
                requests = box_sequence_requests(
                    self.plan,
                    run_time=self.run_time,
                    hours=hours,
                    member=self.member,
                    loc=self.loc,
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Box sampling failed: {exc}")
            return

        total = len(requests)
        primary = int(self.fxx) if self.fxx in hours else hours[0]
        # Preserve every requested forecast hour even when all of its nodes
        # fail.  Downstream sequence analysis needs that empty slot to report
        # "no data" at the requested hour instead of silently shortening the
        # sequence (or mistaking it for a single-hour run).
        self._outputs_by_hour = {int(hour): {} for hour in hours}
        # Map each request back to the hour and lattice cell it came from, so a
        # completed point can be filed under the right hour whichever id scheme
        # produced it.
        cells: dict[str, tuple[int, int, int]] = {}
        for item in requests:
            for node in self.plan.requestable_points:
                if item.id in (
                    node.request_id, sequence_request_id(item.fxx, node)
                ):
                    cells[item.id] = (int(item.fxx), node.row, node.col)
                    break
        node_ids = {
            item.id: (
                item.output.replace("\\", "/").rsplit("/", 1)[-1][:-4]
            )
            for item in requests
        }
        paths = {
            item.id: os.path.join(self.output_dir, item.output)
            for item in requests
        }
        def on_progress(event):
            request_id = event.get("request_id")
            kind = str(event.get("event", "working"))
            stage = str(event.get("stage") or kind)
            if kind == "completed" and request_id in paths:
                self._completed += 1
                hour, row, col = cells.get(request_id, (primary, -1, -1))
                node_id = node_ids.get(request_id, request_id)
                self._outputs_by_hour.setdefault(hour, {})[node_id] = (
                    paths[request_id]
                )
                if hour == primary:
                    self._outputs[node_id] = paths[request_id]
                self.point_ready.emit(paths[request_id], int(row), int(col))
            elif kind in {"failed", "cancelled"} and request_id in cells:
                _hour, row, col = cells[request_id]
                error = event.get("error") or {}
                self.point_failed.emit(
                    int(row), int(col), str(error.get("message") or kind))
            self.progress.emit(stage, self._completed, total)

        try:
            self._runner = IsolatedBatchRunner()
            result = self._runner.run(
                requests,
                output_dir=self.output_dir,
                # One worker per concurrently held model hour. A single-hour box
                # gains nothing from more, and a sequence is deliberately kept
                # to two so it cannot hold several hundred-megabyte subsets at
                # once.
                max_workers=1 if len(hours) == 1 else 2,
                progress_callback=on_progress,
                disk_cache=self.disk_cache,
            )
        except IsolatedBatchCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Box extraction failed: {exc}")
            return
        finally:
            self._runner = None

        valid_time = None
        try:
            from datetime import timedelta

            valid_time = self.run_time + timedelta(hours=primary)
        except Exception:
            valid_time = None
        self.result_ready.emit(BoxExtractResult(
            plan=self.plan,
            outputs=dict(self._outputs),
            run_time=self.run_time,
            valid_time=valid_time,
            fxx=primary,
            completed=int(getattr(result, "completed", self._completed)),
            failed=int(getattr(result, "failed", 0)),
            cancelled=int(getattr(result, "cancelled", 0)),
            output_dir=self.output_dir,
            hours=tuple(hours),
            outputs_by_hour={
                hour: dict(values)
                for hour, values in self._outputs_by_hour.items()
            },
        ))


class BoxMeanWorker(QThread):
    """Average an extracted box into one sounding, off the UI thread.

    Reading every member and writing the composite is a few hundred milliseconds
    of file work, and the picker then has to decode and build a full parcel
    surface for the result on the UI thread anyway. Doing the averaging here
    keeps the one unavoidable stall as short as it can be.

    Emits the written ``.npz`` path alongside the
    :class:`~sharpmod.analysis.box_mean.BoxMeanProfile`, so the caller can report how the
    average was formed without reopening the file.
    """

    ready = Signal(str, object)   # npz path, BoxMeanProfile
    failed = Signal(str)

    def __init__(self, extraction: BoxExtractResult, *, loc=None, parent=None):
        super().__init__(parent)
        self.extraction = extraction
        self.loc = loc

    def run(self):
        from sharpmod.analysis import box_mean

        extraction = self.extraction
        plan = extraction.plan
        region = plan.region
        try:
            profile = box_mean.box_mean_profile(
                extraction.outputs,
                lat=region.center_lat,
                lon=region.center_lon,
            )
            if self.isInterruptionRequested():
                return
            path = os.path.join(
                extraction.output_dir or ".", "box-mean-sounding.npz")
            written = box_mean.write_box_mean_sounding(
                profile, path,
                model=plan.model_label,
                model_key=plan.model_key,
                run_time=extraction.run_time,
                valid_time=extraction.valid_time,
                fxx=extraction.fxx,
                loc=self.loc,
                spacing_km=plan.spacing_km,
                box=(
                    region.lat0, region.lon0,
                    region.lat1, region.lon1,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"The box could not be averaged: {exc}")
            return
        self.ready.emit(str(written), profile)


class BoxAnalysisWorker(QThread):
    """Compute scalar fields for an extracted box off the UI thread.

    Emits a :class:`~sharpmod.analysis.box_analysis.BoxAnalysis` for a single-hour box and
    a :class:`~sharpmod.analysis.box_analysis.BoxSequence` for a multi-hour one, so the
    window can tell which it received without being told separately.
    """

    progress = Signal(int, int)   # done, total
    ready = Signal(object)        # BoxAnalysis or BoxSequence
    failed = Signal(str)

    def __init__(self, extraction: BoxExtractResult, *, tiers=None,
                 parent=None):
        super().__init__(parent)
        self.extraction = extraction
        self.tiers = tuple(tiers or (_analysis.FAST_TIER,))

    def run(self):
        try:
            if self.extraction.sequence:
                result = self._run_sequence()
            else:
                result = _analysis.analyze_box(
                    self.extraction.plan,
                    self.extraction.outputs,
                    tiers=self.tiers,
                    run_time=self.extraction.run_time,
                    valid_time=self.extraction.valid_time,
                    fxx=self.extraction.fxx,
                    progress=lambda done, total, _point: self.progress.emit(
                        int(done), int(total)),
                    cancelled=self.isInterruptionRequested,
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Box analysis failed: {exc}")
            return
        self.ready.emit(result)

    def _run_sequence(self):
        # Report progress across the whole sequence rather than restarting the
        # bar at each hour, which would look like it had stalled and jumped.
        hours = self.extraction.hours
        per_hour = {
            hour: len(self.extraction.outputs_for(hour)) for hour in hours
        }
        overall = sum(per_hour.values()) or 1
        offsets = {}
        running = 0
        for hour in hours:
            offsets[hour] = running
            running += per_hour[hour]

        def on_progress(hour, done, _total):
            self.progress.emit(
                int(offsets.get(hour, 0) + done), int(overall))

        return _analysis.analyze_box_sequence(
            self.extraction.plan,
            {
                hour: self.extraction.outputs_for(hour)
                for hour in hours
            },
            tiers=self.tiers,
            run_time=self.extraction.run_time,
            progress=on_progress,
            cancelled=self.isInterruptionRequested,
        )
