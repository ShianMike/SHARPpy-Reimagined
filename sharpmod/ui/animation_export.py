"""AnimationExport methods for AnimationWorkspace."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timezone
from pathlib import Path
from qtpy.QtWidgets import QFileDialog
from sharpmod.analysis.animation_exports import prepare_animation_frames
from sharpmod.ui.features.export_presentation import export_filename
from sharpmod.ui.features.export_presentation import export_identity
from sharpmod.ui.features.gui_export import resolved_export_theme
from sharpmod.ui.features.gui_jobs import JobCounts
import os
from sharpmod.ui.features.gui_animation import (
    _ExportRequest,
    _ExportWorker,
    _meta,
    _utc_text
)


class AnimationExportMixin:
    """Focused workspace behavior."""

    def _export_gif(self):
        if self._worker is not None or self._capturing:
            return
        selected = self._selected_specs()
        planned, error = self._planned_entries()
        if error or not planned:
            self._set_status(f"Cannot export this range: {error}", "error")
            return
        collections = self.host._collections()
        if not collections:
            self._set_status("Load a sounding before exporting an animation.", "error")
            return
        focused = collections[min(self.host._focused_index(), len(collections) - 1)]
        identity = export_identity(
            focused,
            valid_times=(spec["valid"] for spec in selected),
            run_times=(spec["run"] for spec in selected),
        )
        if self.mode.currentData() == "run-to-run":
            source_names = dict.fromkeys(
                str(_meta(collections[spec["collection"]], "model", ""))
                for spec in selected
            )
            identity = replace(
                identity, source="-".join(name for name in source_names if name)
            )
        suggested = self._suggested(
            export_filename(identity, kind=self.mode.currentData(), extension="gif")
        )
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Export sounding animation",
            str(suggested),
            "GIF files (*.gif);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".gif"):
            path += ".gif"
        mode, duration = self.mode.currentData(), self.duration.value()
        presentation = self.sizes.presentation()
        theme = resolved_export_theme(presentation.theme)
        context = self._frame_context(selected)
        policy = self.gap_policy.currentData()
        expected = self._expected_step()
        self._save_preferences()
        capture_token = self._token
        self._capturing = True
        self._update_export_controls()
        try:
            frames = self.capture_animation_frames(
                specs=selected, output_size=presentation.size, theme=theme
            )
        except Exception as exc:  # noqa: BLE001 - capture boundary
            self._set_status(f"Animation capture failed: {exc}", "error")
            return
        finally:
            self._capturing = False
            self._update_export_controls()
        if capture_token != self._token:
            return
        try:
            prepared = prepare_animation_frames(
                frames,
                mode=mode,
                gap_policy=policy,
                expected_interval_seconds=expected,
            )
        except Exception as exc:  # noqa: BLE001 - failed-frame policy boundary
            self._set_status(
                f"Animation gap policy prevented export: {exc}. "
                "Choose missing cards or adjust the range.", "error",
            )
            return
        self._token += 1
        self._start_export(
            _ExportRequest(
                self._token, path, frames, mode, duration, presentation.size,
                policy, expected, theme, context,
            ),
            output_count=len(prepared),
            summary=identity.caption,
        )

    def _start_export(self, request, *, output_count=None, summary=None):
        self._active_request = request
        self._last_request = request
        self._last_summary = summary or getattr(self, "_last_summary", "")
        self._cancel_requested = False
        worker = _ExportWorker(request, self)
        self._worker = worker
        worker.progress.connect(self._export_progress)
        worker.stage.connect(self._export_stage)
        worker.completed.connect(self._export_ready)
        worker.cancelled.connect(self._export_cancelled)
        worker.failed.connect(self._export_failed)
        worker.finished.connect(self._export_finished)
        scope = (
            "Forecast timeline"
            if request.mode == "forecast-timeline"
            else "Successive runs at focused exact valid time"
        )
        times = ", ".join(_utc_text(frame.valid_time) for frame in request.frames)
        affected = (
            f"{scope} · {len(request.frames)} selected frames · "
            f"{output_count or len(request.frames)} output frames · "
            f"{request.output_size[0]} × {request.output_size[1]} px · "
            f"{request.gap_policy} gaps · {request.duration} ms · "
            f"valid {times} · {request.destination}"
        )
        self.job.begin(
            request.token,
            "GIF export",
            affected,
            "Rendering the saved displayed data off the GUI thread",
            total=output_count or len(request.frames),
            unit="frames",
            retained=self._retained_artifact_label(request),
            cancellable=True,
        )
        self._update_export_controls()
        self._set_status(
            "Exporting the saved capture; the prior destination remains usable."
        )
        worker.start()

    def _cancel(self):
        if self._worker is None or not self.job.accepts(self._token):
            return
        self._cancel_requested = True
        self.job.request_cancel(self._token)
        self._worker.requestInterruption()
        self._set_status(
            "Cancel requested; the current encoder will finish without replacing "
            "the prior destination.",
            "warn",
        )

    def _accepts_export(self, token):
        request = self._active_request
        return bool(
            request is not None
            and token == self._token == request.token
            and self.job.accepts(token)
        )

    def _export_stage(self, token, stage):
        if self._accepts_export(token):
            self.job.update(token, stage=stage)

    def _export_progress(self, token, done, total):
        if self._accepts_export(token):
            self.job.update(
                token,
                stage="Encoding chosen-size GIF frames",
                done=done,
                total=total,
            )

    def _export_ready(self, token, candidate):
        if not self._accepts_export(token):
            return
        if self._cancel_requested:
            self._export_cancelled(token)
            return
        request = self._active_request
        try:
            with open(candidate, "rb") as stream:
                if stream.read(6) not in {b"GIF87a", b"GIF89a"}:
                    raise ValueError("the encoder produced no valid GIF header")
            os.replace(candidate, request.destination)
        except Exception as exc:  # noqa: BLE001 - atomic publication boundary
            self._export_failed(token, str(exc))
            return
        self._last_artifact = request.destination
        try:
            self.completion.completed(
                request.destination, kind="GIF", summary=self._last_summary,
                dimensions=request.output_size,
            )
        except (OSError, ValueError) as exc:
            self._set_status(
                f"GIF was saved at {request.destination}, but history could not "
                f"be updated: {exc}", "warn",
            )
        message = f"Saved complete GIF: {request.destination}"
        self.job.update(token, retained=self._retained_artifact_label(request))
        self.job.finish(
            token,
            counts=JobCounts(completed=1, requested=1),
            message=message,
        )
        self._set_status(message)

    def _export_cancelled(self, token):
        if not self._accepts_export(token):
            return
        message = (
            "Export cancelled; no artifact was replaced. Retry uses the saved capture."
        )
        self.job.finish(
            token,
            counts=JobCounts(cancelled=1, requested=1),
            message=message,
            retryable=True,
        )
        self._set_status(message, "warn")

    def _export_failed(self, token, message):
        if not self._accepts_export(token):
            return
        if self._cancel_requested:
            self._export_cancelled(token)
            return
        detail = f"Export failed: {message}. No artifact was replaced."
        self.job.finish(
            token,
            counts=JobCounts(failed=1, requested=1),
            message=detail,
            retryable=True,
        )
        self._set_status(detail, "error")

    def _export_finished(self):
        worker = self.sender()
        worker.discard_candidate()
        if worker is self._worker:
            self._worker = None
            self._active_request = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self._update_export_controls()

    def _retry_export(self):
        snapshot = self.job.snapshot
        if (
            self._worker is not None
            or self._capturing
            or self._last_request is None
            or snapshot is None
            or not snapshot.retryable
        ):
            return
        self._token += 1
        self._start_export(replace(self._last_request, token=self._token))

    def _retained_artifact_label(self, request):
        for path in (request.destination, self._last_artifact):
            if not path:
                continue
            try:
                updated = datetime.fromtimestamp(
                    Path(path).stat().st_mtime, timezone.utc
                )
            except OSError:
                continue
            label = Path(path).name if path == request.destination else path
            return f"{label} · updated {_utc_text(updated)}"
        return ""

    def _update_export_controls(self):
        idle = self._worker is None and not self._capturing
        planned, error = self._planned_entries()
        self.gif_button.setEnabled(idle and bool(planned) and not error)
        self.preview_button.setEnabled(idle and bool(planned))
        self.preview_index.blockSignals(True)
        self.preview_index.setRange(1 if planned else 0, len(planned))
        self.preview_index.blockSignals(False)
        missing = sum(frame.png_bytes is None for frame, _spec in planned)
        count = len(planned)
        self.frame_count.setText(
            f"{count} output frame{'s' if count != 1 else ''} in the selected "
            f"range · {missing} explicit gap card{'s' if missing != 1 else ''}"
            + (f" · {error}" if error else "")
        )
        snapshot = self.job.snapshot
        self.job.retry_button.setEnabled(idle and bool(snapshot and snapshot.retryable))
        if idle:
            self._schedule_preview()
