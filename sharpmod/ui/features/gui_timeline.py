"""Forecast-hour playback controls for a multi-time SHARPpy collection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import os
import shutil
import tempfile
import threading

from qtpy.QtCore import QObject, Qt, QThread, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
)

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER, _render, as_utc
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.analysis.profile_timeline import forecast_hour_range
from sharpmod.ui.styles.theme import OBJ_ERROR_TEXT, OBJ_STATUS
from sharpmod.analysis.timeline_frames import TimelineCoverage


MAX_TIMELINE_HOURS = 72


@dataclass(frozen=True)
class _TimelineRequest:
    model: str
    model_label: str
    lat: float
    lon: float
    run_time: datetime
    hours: tuple[int, ...]
    output_dir: str
    loc: str | None
    member: str | None
    affected_input: str


@dataclass
class _TimelineSession:
    request: _TimelineRequest
    coverage: TimelineCoverage
    viewer: object = None
    collection: object = None
    paths: list[str] = field(default_factory=list)
    failures: dict[int, str] = field(default_factory=dict)
    viewer_closed: bool = False
    updated_at: datetime | None = None

    @property
    def completed_hours(self):
        return self.coverage.loaded_hours


def compose_interactive(*args, **kwargs):
    """Load the viewer stack only after the first timeline item arrives."""
    from sharpmod.gui_viewer import compose_interactive as compose

    return compose(*args, **kwargs)


def _collection_dates(collection):
    return tuple(getattr(collection, "_dates", ()))


class ForecastTimelineDialog(QDialog):
    """Choose an inclusive forecast-hour range without inventing hours."""

    def __init__(self, available_hours, *, current=0, parent=None):
        super().__init__(parent)
        self._available = tuple(sorted({int(hour) for hour in available_hours}))
        if not self._available:
            raise ValueError("no forecast hours are available")
        self.setWindowTitle("Forecast Timeline")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Fetch a range into one sounding timeline. Completed hours remain "
            "usable if a later hour is unavailable or the queue is cancelled."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.start_combo = QComboBox(self)
        self.end_combo = QComboBox(self)
        for hour in self._available:
            text = f"F{hour:03d}"
            self.start_combo.addItem(text, hour)
            self.end_combo.addItem(text, hour)
        current_index = self.start_combo.findData(int(current))
        self.start_combo.setCurrentIndex(max(0, current_index))
        default_end = min(
            len(self._available) - 1,
            max(0, self.start_combo.currentIndex()) + 12,
        )
        self.end_combo.setCurrentIndex(default_end)
        form.addRow("Start", self.start_combo)
        form.addRow("End", self.end_combo)
        self.step_spin = QSpinBox(self)
        self.step_spin.setRange(1, 24)
        self.step_spin.setValue(1)
        self.step_spin.setSuffix(" hour(s)")
        form.addRow("Step", self.step_spin)
        layout.addLayout(form)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self._accept_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.start_combo.currentIndexChanged.connect(self._update_summary)
        self.end_combo.currentIndexChanged.connect(self._update_summary)
        self.step_spin.valueChanged.connect(self._update_summary)
        self._update_summary()

    def hours(self) -> tuple[int, ...]:
        hours = forecast_hour_range(
            self._available,
            self.start_combo.currentData(),
            self.end_combo.currentData(),
            self.step_spin.value(),
        )
        if len(hours) > MAX_TIMELINE_HOURS:
            raise ValueError(
                f"select no more than {MAX_TIMELINE_HOURS} timeline hours"
            )
        return hours

    def _set_summary_role(self, object_name):
        """Swap the summary's semantic role, re-polishing so QSS re-resolves.

        Qt does not re-evaluate style-sheet selectors when an object name
        changes, so without the unpolish/polish pair the label keeps whichever
        rule matched at construction.
        """
        if self.summary.objectName() == object_name:
            return
        self.summary.setObjectName(object_name)
        style = self.summary.style()
        style.unpolish(self.summary)
        style.polish(self.summary)

    def _update_summary(self, *_args):
        try:
            hours = self.hours()
        except ValueError as exc:
            self.summary.setText(str(exc))
            self._set_summary_role(OBJ_ERROR_TEXT)
            return
        self._set_summary_role(OBJ_STATUS)
        self.summary.setText(
            f"{len(hours)} hour{'s' if len(hours) != 1 else ''}: "
            + ", ".join(f"F{hour:03d}" for hour in hours)
        )

    def _accept_valid(self):
        try:
            self.hours()
        except ValueError:
            return
        self.accept()


class ModelTimelineWorker(QThread):
    """Run a killable child batch and stream each completed hour back to Qt."""

    item_ready = Signal(str, int)
    item_failed = Signal(int, str)
    progress = Signal(int, str, int, int)
    result_ready = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(self, model, lat, lon, run_time, hours, output_dir, *,
                 loc=None, resolve_place=False, member=None, disk_cache=None,
                 completed_hours=(), parent=None):
        super().__init__(parent)
        self.model = str(model)
        self.lat = float(lat)
        self.lon = float(lon)
        self.run_time = run_time
        self.hours = tuple(int(hour) for hour in hours)
        self.output_dir = os.fspath(output_dir)
        self.loc = str(loc) if loc else None
        self.resolve_place = bool(resolve_place) and not loc
        self.member = str(member) if member else None
        self.disk_cache = disk_cache
        self.completed_hours = tuple(int(hour) for hour in completed_hours)
        self._runner = None
        self._completed = len(self.completed_hours)
        self._cancel_requested = threading.Event()
        self.result = None

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested.set()
        super().requestInterruption()
        if self._runner is not None:
            self._runner.cancel()

    def run(self):
        from sharpmod.analysis.batch_extract import BatchRequest
        from sharpmod.ui.features.gui_batch_process import (
            IsolatedBatchCancelled,
            IsolatedBatchRunner,
        )

        if self._cancel_requested.is_set():
            self.cancelled.emit(None)
            return
        if self.resolve_place and not self.loc:
            self.progress.emit(-1, "town", 0, len(self.hours))
            from sharpmod.maps.place_names import reverse_town_name
            from sharpmod.tools import model_extract
            try:
                self.loc = reverse_town_name(self.lat, self.lon) \
                    or model_extract.get_config(self.model).label
            except Exception as exc:  # noqa: BLE001 - worker boundary
                self.failed.emit(f"Forecast timeline location lookup failed: {exc}")
                return
        if self._cancel_requested.is_set():
            self.cancelled.emit(None)
            return
        requests = [
            BatchRequest(
                id=f"f{hour:03d}", model=self.model, lat=self.lat,
                lon=self.lon, run_time=self.run_time, fxx=hour,
                output=f"f{hour:03d}.npz", loc=self.loc,
                member=self.member,
            )
            for hour in self.hours
        ]
        paths = {
            request.id: os.path.join(self.output_dir, request.output)
            for request in requests
        }
        hour_by_id = {request.id: request.fxx for request in requests}
        already_completed = {f"f{hour:03d}" for hour in self.completed_hours}

        def on_progress(event):
            request_id = event.get("request_id")
            if request_id in hour_by_id:
                hour = hour_by_id[request_id]
            else:
                try:
                    hour = int(event.get("fxx", -1))
                except (TypeError, ValueError):
                    hour = -1
            kind = str(event.get("event", "working"))
            stage = str(event.get("stage") or kind)
            if kind == "completed" and request_id in paths:
                if request_id not in already_completed:
                    already_completed.add(request_id)
                    self._completed += 1
                    self.item_ready.emit(paths[request_id], int(hour))
            elif kind == "failed" and hour >= 0:
                error = event.get("error") or {}
                message = str(error.get("message") or kind)
                self.item_failed.emit(int(hour), message)
            self.progress.emit(
                int(hour), stage, self._completed, len(self.hours)
            )

        try:
            self._runner = IsolatedBatchRunner()
            if self._cancel_requested.is_set():
                self._runner.cancel()
            result = self._runner.run(
                requests,
                output_dir=self.output_dir,
                max_workers=min(2, max(1, len(self.hours))),
                progress_callback=on_progress,
                disk_cache=self.disk_cache,
            )
        except IsolatedBatchCancelled as exc:
            self.result = exc.result
            self.cancelled.emit(exc.result)
            return
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.result = getattr(exc, "result", None)
            self.failed.emit(f"Forecast timeline failed: {exc}")
            return
        finally:
            self._runner = None
        self.result = result
        if self._cancel_requested.is_set():
            self.cancelled.emit(result)
        else:
            self.result_ready.emit(result)


class ForecastTimelineCoordinator(QObject):
    """Own forecast timeline selection, worker, streaming, and cleanup."""

    def __init__(self, picker):
        super().__init__(picker)
        self.picker = picker
        self._session = None
        self._token = 0
        self._cancel_requested = False
        self._terminal = False
        self._rendering = self._finishing = False
        self._pending_result = self._pending_finished = None

    def open(self) -> None:
        """Fetch a bounded forecast-hour range and stream it into one viewer."""
        cfg = self.picker._model_config()
        if cfg is None:
            QMessageBox.warning(
                self.picker, APP_NAME, "Choose a forecast model first."
            )
            return
        if (
            self.picker._model_worker is not None
            or self.picker._model_timeline_worker is not None
            or getattr(self.picker, "_model_compare_worker", None) is not None
        ):
            QMessageBox.information(
                self.picker, APP_NAME, "A model fetch is already in progress."
            )
            return
        self.picker._ensure_model_cache()
        lat = float(self.picker._model_lat.value())
        lon = float(self.picker._model_lon.value())
        if not self.picker._model_point_ok():
            QMessageBox.warning(
                self.picker,
                APP_NAME,
                f"{cfg.label} does not cover {lat:.4f}, {lon:.4f}.",
            )
            return

        from sharpmod.tools import model_extract

        if model_extract.requires_grib_runtime(cfg):
            try:
                model_extract.require_runtime_dependencies()
            except model_extract.RetrievalError as exc:
                QMessageBox.critical(
                    self.picker,
                    APP_NAME,
                    f"Forecast model support is unavailable:\n{exc}",
                )
                return
        run_time = self.picker._model_run_time()
        member = self.picker._model_member_value()
        loc = self.picker._model_loc.text().strip() or None
        available = model_extract.forecast_hours(cfg, cycle_hour=run_time.hour)
        try:
            dialog = ForecastTimelineDialog(
                available,
                current=self.picker._model_selected_fxx(),
                parent=self.picker,
            )
        except ValueError as exc:
            QMessageBox.warning(self.picker, APP_NAME, str(exc))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        hours = dialog.hours()
        if len(hours) < 2:
            QMessageBox.information(
                self.picker,
                APP_NAME,
                "Choose at least two forecast hours for a timeline.",
            )
            return

        self.picker._cancel_model_prefetch(wait=True)
        output_dir = tempfile.mkdtemp(
            prefix=(f"timeline_{cfg.key.replace('-', '_')}_{run_time:%Y%m%d%H}_")
        )
        location = loc or f"{lat:.4f}, {lon:.4f}"
        member_text = f" · member {member}" if member else ""
        request = _TimelineRequest(
            cfg.key,
            cfg.label,
            lat,
            lon,
            run_time,
            tuple(hours),
            output_dir,
            loc,
            member,
            f"{cfg.label} · model initialization {run_time:%Y-%m-%d %H:%MZ} · "
            f"F{hours[0]:03d}–F{hours[-1]:03d}{member_text} · {location}",
        )
        self._abandon_session()
        self._session = _TimelineSession(
            request,
            TimelineCoverage.requested_range(hours, run_time=run_time),
        )
        self.picker._remember_point(lat, lon, loc)
        self._start_session(self._session)

    def _job(self):
        return getattr(self.picker, "_model_job", None)

    def _start_session(self, session):
        request = session.request
        self._token += 1
        token = self._token
        completed = session.completed_hours
        self._cancel_requested = False
        self._terminal = False
        self._initial_completed = len(completed)
        for hour in session.coverage.missing_hours:
            session.coverage = session.coverage.with_state(hour, "requested")
            session.failures.pop(hour, None)
        worker = ModelTimelineWorker(
            request.model,
            request.lat,
            request.lon,
            request.run_time,
            request.hours,
            request.output_dir,
            loc=request.loc,
            resolve_place=not bool(request.loc),
            member=request.member,
            disk_cache=self.picker._model_disk_cache,
            completed_hours=completed,
            parent=self.picker,
        )
        worker._sharpmod_timeline_token = token
        worker._sharpmod_timeline_session = session
        self.picker._model_timeline_worker = worker
        worker.item_ready.connect(self._on_timeline_item_ready)
        worker.item_failed.connect(self._on_timeline_item_failed)
        worker.progress.connect(self._on_timeline_progress)
        worker.result_ready.connect(self._on_timeline_result)
        worker.cancelled.connect(self._on_timeline_cancelled)
        worker.failed.connect(self._on_timeline_failed)
        worker.finished.connect(self._on_timeline_finished)
        # Independent cleanup still runs if picker shutdown disconnects its
        # QObject slots while a retained, cancelled thread is finishing.
        worker.finished.connect(lambda session=session: (
            shutil.rmtree(session.request.output_dir, ignore_errors=True)
            if session.viewer_closed else None
        ))
        self.picker._set_model_busy(True)
        self.picker._model_progress_timer.stop()
        self.picker._model_fetch_btn.setText("Timeline queued…")
        self.picker._model_timeline_btn.setText("Timeline running…")
        remaining = max(0, len(request.hours) - len(completed))
        self.picker._model_progress.setRange(0, len(request.hours))
        self.picker._model_progress.setValue(len(completed))
        self.picker._model_progress.setFormat(
            f"{len(completed)} / {len(request.hours)} hours"
        )
        self.picker._model_progress_detail.setText(
            f"Queued {remaining} unresolved forecast hour(s); completed hours "
            "remain open as they arrive."
        )
        job = self._job()
        if job is not None:
            scroll = getattr(self.picker, "_model_job_scroll", None)
            if scroll is not None:
                scroll.show()
            retained = self._retained_identity(session)
            job.begin(
                token,
                "Forecast timeline",
                request.affected_input,
                "Fetching saved unresolved forecast hours",
                total=remaining,
                unit="forecast hours",
                retained=retained,
                cancellable=True,
            )
            job.update(token, done=0)
            self.picker._model_job_retry = self.retry
            self.picker._model_progress.hide()
            self.picker._model_progress_detail.hide()
        self.picker.statusBar().showMessage(
            f"Fetching {request.model_label} timeline "
            f"F{request.hours[0]:03d}–F{request.hours[-1]:03d}…"
        )
        worker.start()

    @staticmethod
    def _retained_identity(session):
        if session is None or not session.completed_hours:
            return ""
        request = session.request
        hours = ", ".join(f"F{hour:03d}" for hour in session.completed_hours)
        dates = tuple(frame.valid_time for frame in session.coverage if frame.available)
        valid = f"valid {min(dates):%Y-%m-%d %H:%MZ}"
        if min(dates) != max(dates):
            valid += f" to {max(dates):%Y-%m-%d %H:%MZ}"
        updated = (
            f" · updated {session.updated_at:%H:%M:%SZ}" if session.updated_at else ""
        )
        return (
            f"{request.model_label} initialization {request.run_time:%Y-%m-%d %H:%MZ} · "
            f"{valid} · loaded {hours}{updated}"
        )

    def cancel(self):
        worker = self.picker._model_timeline_worker
        if worker is None:
            return
        self._cancel_requested = True
        job = self._job()
        token = getattr(worker, "_sharpmod_timeline_token", None)
        if job is not None:
            job.request_cancel(token)
        worker.requestInterruption()
        self.picker.statusBar().showMessage(
            "Cancelling remaining timeline hours; completed hours are kept…"
        )

    def retry(self):
        session = self._session
        job = self._job()
        if (
            session is None
            or self.picker._model_timeline_worker is not None
            or self.picker._model_worker is not None
            or getattr(self.picker, "_model_compare_worker", None) is not None
            or job is None
            or not job.snapshot
            or not job.snapshot.retryable
            or session.viewer_closed
            or getattr(self.picker, "_shutdown_started", False)
        ):
            return False
        self._start_session(session)
        return True

    def shutdown(self):
        self.cancel()
        job = self._job()
        if job is not None:
            job.invalidate("Picker closed; no later timeline callback can replace displayed hours.")
        self.picker._model_job_retry = None
        session = self._session
        if session is not None and session.viewer is None:
            session.viewer_closed = True
            if self.picker._model_timeline_worker is None:
                shutil.rmtree(session.request.output_dir, ignore_errors=True)

    def _abandon_session(self):
        session = self._session
        if session is None:
            return
        if session.viewer is None or session.viewer_closed:
            shutil.rmtree(session.request.output_dir, ignore_errors=True)
        self._session = None

    def _current_session(self, worker):
        session = self._session
        if (
            session is None
            or worker is not self.picker._model_timeline_worker
            or getattr(worker, "_sharpmod_timeline_token", None) != self._token
            or session.viewer_closed
            or getattr(self.picker, "_shutdown_started", False)
        ):
            return None
        return session

    def _active_session(self, worker):
        if self._cancel_requested or self._terminal:
            return None
        return self._current_session(worker)

    def _on_timeline_item_ready(self, npz_path: str, fxx: int) -> None:
        worker = self.sender()
        session = self._active_session(worker)
        if session is None:
            return
        self._apply_timeline_item(worker, session, npz_path, fxx)

    def _apply_timeline_item(self, worker, session, npz_path, fxx):
        if (
            self._rendering or fxx not in session.request.hours
            or session.coverage.state_of(fxx) == "loaded"
        ):
            return
        self._rendering = True
        try:
            prof_col, stn_id = _render().decode(npz_path)
            valid = prof_col.getCurrentDate()
            expected = session.request.run_time + timedelta(hours=int(fxx))
            if as_utc(valid) != as_utc(expected):
                raise ValueError(
                    f"F{int(fxx):03d} expected valid {as_utc(expected):%Y-%m-%d %H:%MZ}; "
                    f"artifact has {as_utc(valid):%Y-%m-%d %H:%MZ}. No other time was substituted."
                )
            collection = session.collection
            if collection is None:
                source_meta = dict(getattr(prof_col, "_meta", {}))
                prof_col.setMeta("timeline", True)
                prof_col.setMeta("timeline_count", 1)
                prof_col.setMeta("timeline_hours", [int(fxx)])
                prof_col.setMeta("timeline_sources", [str(npz_path)])
                prof_col.setMeta("timeline_provenance", [source_meta])
                session.coverage.attach(prof_col)
                self.picker._prune_closed_viewers()
                win = compose_interactive(
                    self.picker._config(),
                    prof_col,
                    self.picker,
                    stn_id=stn_id,
                    activate=False,
                )
                if self._cancel_requested or self._current_session(worker) is not session:
                    win.close()
                    return
                win.setWindowTitle(f"{APP_NAME} — Forecast Timeline (1 hour loaded)")
                self.picker._viewers.append(win)
                watch_profiles = getattr(
                    self.picker, "_watch_loaded_profile_viewer", None
                )
                if callable(watch_profiles):
                    watch_profiles(win)
                session.viewer = win
                session.collection = prof_col
                output_dir = session.request.output_dir
                win.destroyed.connect(
                    lambda *_args, session=session, output_dir=output_dir: (
                        self._on_timeline_viewer_destroyed(session, output_dir)
                    )
                )
            else:
                from sharpmod.analysis.profile_timeline import append_collection

                append_collection(collection, prof_col)
                win = session.viewer
                win.spc_widget.updateProfs()
                refresh_timeline_controls(win)
                count = int(collection.getMeta("timeline_count"))
                win.setWindowTitle(
                    f"{APP_NAME} — Forecast Timeline ({count} hours loaded)"
                )
            refresh_profiles = getattr(
                self.picker, "_refresh_loaded_profile_markers", None
            )
            if callable(refresh_profiles):
                refresh_profiles()
            session.paths.append(str(npz_path))
            self._record_frame(worker, int(fxx), "loaded", valid_time=valid)
            session.failures.pop(int(fxx), None)
        except Exception as exc:  # noqa: BLE001 - decode/render boundary
            _LOGGER.exception(
                "forecast_timeline.display_failed path=%s fxx=%s", npz_path, fxx
            )
            session.failures[int(fxx)] = str(exc)
            self._record_frame(worker, int(fxx), "failed", reason=str(exc))
            self.picker.statusBar().showMessage(
                f"F{int(fxx):03d} downloaded but could not be displayed"
            )
        finally:
            self._rendering = False
            self._drain_pending()

    def _drain_pending(self):
        if self._rendering or self._finishing:
            return
        if self._pending_result is not None:
            worker, result, cancelled, error = self._pending_result
            self._pending_result = None
            self._finish_run(worker, result, cancelled=cancelled, error=error)
        if self._pending_finished is not None:
            worker = self._pending_finished
            self._pending_finished = None
            self._finish_worker(worker)

    def _on_timeline_item_failed(self, fxx: int, message: str) -> None:
        worker = self.sender()
        session = self._active_session(worker)
        if (
            session is None or fxx not in session.request.hours
            or session.coverage.state_of(fxx) == "loaded"
        ):
            return
        session.failures[int(fxx)] = str(message)
        self._record_frame(worker, int(fxx), "failed", reason=str(message))
        self.picker.statusBar().showMessage(
            f"Timeline F{int(fxx):03d} unavailable: {message}", 7000
        )

    def _record_frame(self, worker, fxx, state, *, valid_time=None, reason=None):
        """Move one frame's state and republish the ledger to the viewer.

        The ledger lives on the *collection* metadata, not on this worker, so it
        survives the worker being deleted and travels with the timeline into the
        analysis workspace and any export.
        """
        session = self._current_session(worker)
        if session is None or int(fxx) not in session.request.hours:
            return
        if session.coverage.state_of(fxx) == "loaded" and state != "loaded":
            return
        session.coverage = session.coverage.with_state(
            int(fxx), state, valid_time=valid_time, reason=reason
        )
        if state == "loaded":
            session.updated_at = datetime.now(timezone.utc)
        self._publish_coverage(session)

    def _publish_coverage(self, session):
        if session.collection is not None:
            session.coverage.attach(session.collection)
        if session.viewer is not None and not session.viewer_closed:
            refresh_timeline_controls(session.viewer)
        # T21.3: the picker map reuses this exact ledger.  No second clock or
        # inferred status is maintained in the rail; it sees the same loading,
        # failed, unavailable, and loaded transitions as the sounding viewer.
        publish_map = getattr(self.picker, "_set_map_frame_coverage", None)
        if callable(publish_map):
            publish_map(session.coverage)
        job = self._job()
        if job is not None:
            job.update(self._token, retained=self._retained_identity(session))

    def _on_timeline_progress(
        self, fxx: int, stage: str, completed: int, total: int
    ) -> None:
        worker = self.sender()
        session = self._active_session(worker)
        if session is None:
            return
        total = len(session.request.hours)
        completed = max(0, min(total, int(completed)))
        self.picker._model_progress.setRange(0, total)
        self.picker._model_progress.setValue(completed)
        self.picker._model_progress.setFormat(f"{completed} / {total} hours")
        prefix = f"F{int(fxx):03d}" if int(fxx) >= 0 else "Timeline"
        self.picker._model_progress_detail.setText(
            f"{prefix}: {str(stage).replace('_', ' ')} — "
            f"{completed} of {total} complete"
        )
        # An hour that has started is "loading", which is what lets the coverage
        # strip distinguish work in flight from work not yet begun.
        if session.coverage.state_of(fxx) == "requested":
            self._record_frame(worker, fxx, "loading")
        job = self._job()
        if job is not None:
            job.update(
                self._token,
                stage=f"{prefix}: {str(stage).replace('_', ' ')}",
                done=max(0, completed - self._initial_completed),
                retained=self._retained_identity(session),
            )

    def _on_timeline_result(self, result) -> None:
        self._finish_run(self.sender(), result, cancelled=self._cancel_requested)

    def _on_timeline_cancelled(self, result) -> None:
        self._finish_run(self.sender(), result, cancelled=True)

    def _on_timeline_failed(self, message: str) -> None:
        self._finish_run(
            self.sender(), getattr(self.sender(), "result", None),
            cancelled=self._cancel_requested, error=message,
        )

    def _finish_run(self, worker, result, *, cancelled=False, error=""):
        session = self._current_session(worker)
        if session is None or self._terminal:
            return
        if self._rendering:
            if self._pending_result is None:
                self._pending_result = (worker, result, cancelled, error)
            return
        self._terminal = self._finishing = True
        try:
            self._finish_result(
                worker, session, result,
                cancelled=cancelled or self._cancel_requested, error=error,
            )
        finally:
            self._finishing = False
            self._drain_pending()

    def _finish_result(self, worker, session, result, *, cancelled, error):
        if not session.request.loc and getattr(worker, "loc", None):
            session.request = replace(session.request, loc=worker.loc)
        if not cancelled:
            # The manifest, not stdout delivery, is authoritative. A completed
            # event can be absent if the child exits just after its atomic write.
            for item in tuple(getattr(result, "items", ()) or ()):
                if item.status == "completed" and not self._cancel_requested:
                    hour = self._item_hour(item)
                    if hour in session.request.hours and hour not in session.failures:
                        self._apply_timeline_item(worker, session, str(item.output_path), hour)
        cancelled = cancelled or self._cancel_requested
        if self._current_session(worker) is not session:
            return
        self._close_coverage(worker, result, cancelled=cancelled, reason=error)
        outcomes = {}
        for frame in session.coverage:
            outcomes[frame.forecast_hour] = {
                "loaded": "completed", "failed": "failed",
                "canceled": "cancelled", "unavailable": "unavailable",
            }.get(frame.state, "unknown")
        for item in tuple(getattr(result, "items", ()) or ()):
            hour = self._item_hour(item)
            if hour in outcomes and not session.failures.get(hour):
                outcomes[hour] = str(item.status)
        statuses = tuple(outcomes.values())
        counts = JobCounts(
            completed=statuses.count("completed"), failed=statuses.count("failed"),
            cancelled=statuses.count("cancelled"), unavailable=statuses.count("unavailable"),
            unknown=statuses.count("unknown"),
            requested=len(session.request.hours),
        )
        message = (
            "Cancelled; completed forecast-hour artifacts and displayed hours are kept."
            if cancelled else f"Timeline stopped: {error}" if error
            else session.coverage.summary()
        )
        if session.failures:
            hour, detail = next(iter(session.failures.items()))
            message += f" F{hour:03d}: {detail}"
        withheld = [
            hour for hour, outcome in outcomes.items()
            if outcome == "completed" and hour not in session.completed_hours
        ]
        if withheld:
            message += f" {len(withheld)} completed artifact(s) await explicit Retry to display."
        job = self._job()
        if job is not None:
            job.finish(
                self._token, counts=counts,
                outcome="cancelled" if cancelled else "failed" if error else None,
                message=message, retryable=bool(session.coverage.missing_hours),
            )
        self.picker.statusBar().showMessage(message, 12000)

    @staticmethod
    def _item_hour(item):
        try:
            return int(str(item.id).lstrip("fF"))
        except (AttributeError, TypeError, ValueError):
            return None

    def _close_coverage(self, worker, result=None, *, cancelled=False, reason="") -> None:
        """Give every unresolved hour a final state from the batch result.

        Final statuses remain authoritative, while a saved completed artifact
        is not called loaded until it was actually decoded. Only explicit
        cancellation labels unreported pending hours canceled; errors retain
        an unknown outcome instead of inventing a per-hour failure.
        """
        session = self._current_session(worker)
        if session is None:
            return
        coverage = session.coverage
        for item in tuple(getattr(result, "items", ()) or ()):
            status = str(getattr(item, "status", "") or "")
            hour = self._item_hour(item)
            if hour not in session.request.hours or coverage.state_of(hour) == "loaded":
                continue
            if status == "completed":
                if cancelled:
                    coverage = coverage.with_state(
                        hour, "canceled", reason="completed artifact saved; Retry to display"
                    )
                continue
            error = getattr(item, "error", None)
            item_reason = None
            if isinstance(error, Mapping):
                item_reason = error.get("message")
            coverage = coverage.with_state(
                hour, status, reason=str(item_reason) if item_reason else None
            )
        coverage = coverage.with_pending_resolved(
            "canceled" if cancelled else "unknown",
            reason=reason or "the forecast queue stopped before this hour reported an outcome",
        )
        session.coverage = coverage
        self._publish_coverage(session)

    def _on_timeline_viewer_destroyed(self, session, output_dir: str) -> None:
        session.viewer_closed = True
        worker = self.picker._model_timeline_worker
        if self._session is session and not getattr(self.picker, "_shutdown_started", False):
            if worker is not None:
                self.cancel()
            job = self._job()
            if job is not None:
                job.invalidate("Timeline viewer closed; no later hour can replace it.")
        if worker is None or getattr(worker, "_sharpmod_timeline_session", None) is not session:
            shutil.rmtree(output_dir, ignore_errors=True)

    def _on_timeline_finished(self) -> None:
        worker = self.sender()
        if self._rendering or self._finishing:
            self._pending_finished = worker
            return
        self._finish_worker(worker)

    def _finish_worker(self, worker):
        if self.picker._model_timeline_worker is worker:
            if not self._terminal:
                self._finish_run(
                    worker, getattr(worker, "result", None),
                    cancelled=self._cancel_requested,
                    error="Worker stopped without a final outcome.",
                )
            self.picker._model_timeline_worker = None
            self.picker._set_model_busy(False)
        session = getattr(worker, "_sharpmod_timeline_session", None)
        # Keep even a no-viewer partial manifest for saved-request retry.
        if session is not None and (session.viewer_closed or self._session is not session):
            if session.viewer is None or session.viewer_closed:
                shutil.rmtree(worker.output_dir, ignore_errors=True)
        worker.deleteLater()


def install_timeline_controls(win, collection) -> QToolBar | None:
    """Install playback, coverage, and exact-time selection for a timeline.

    Delegates to :class:`sharpmod.ui.features.gui_timeline_playback.TimelinePlaybackBar`. The
    ``win._sharpmod_timeline_*`` attributes are kept exactly as they were, because
    they are the API ``refresh_timeline_controls`` and the tests use.
    """
    if getattr(win, "_sharpmod_timeline_toolbar", None):
        return None
    # Installed when more than one hour *loaded* or more than one was *asked
    # for*. The old guard counted loaded hours only, so a twelve-hour request
    # that returned one hour got no toolbar at all -- exactly the case where the
    # reader most needs to be told that eleven hours are missing and why.
    coverage = TimelineCoverage.from_collection(collection)
    if len(_collection_dates(collection)) < 2 and len(coverage) < 2:
        return None

    from sharpmod.ui.features.gui_timeline_playback import TimelinePlaybackBar

    bar = TimelinePlaybackBar(win, collection)
    win.addToolBar(Qt.TopToolBarArea, bar)
    bar.set_index(bar.current_index())

    win._sharpmod_timeline_toolbar = bar
    win._sharpmod_timeline_timer = bar.timer
    win._sharpmod_timeline_slider = bar.slider
    win._sharpmod_timeline_play_action = bar.play_action
    win._sharpmod_timeline_collection = collection
    win._sharpmod_timeline_set_index = bar.set_index
    win._sharpmod_timeline_bar = bar
    return bar



def refresh_timeline_controls(win) -> QToolBar | None:
    """Install or refresh controls after a streamed hour is appended."""
    collection = getattr(win, "_sharpmod_timeline_collection", None)
    if collection is None:
        try:
            widget = win.spc_widget
            collection = widget.prof_collections[int(widget.pc_idx)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
    toolbar = getattr(win, "_sharpmod_timeline_toolbar", None)
    if toolbar is None:
        return install_timeline_controls(win, collection)
    bar = getattr(win, "_sharpmod_timeline_bar", None)
    if bar is not None:
        # Re-read the ledger too: a streamed hour that just landed has moved from
        # loading to loaded, and the coverage strip is what shows that.
        bar.set_coverage(TimelineCoverage.from_collection(collection))
        return toolbar
    slider = win._sharpmod_timeline_slider
    dates = _collection_dates(collection)
    slider.setMaximum(max(0, len(dates) - 1))
    current = collection.getCurrentDate()
    try:
        index = dates.index(current)
    except ValueError:
        index = 0
    setter = getattr(win, "_sharpmod_timeline_set_index", None)
    if callable(setter):
        setter(index)
    else:
        slider.setValue(index)
    return toolbar


__all__ = [
    "ForecastTimelineCoordinator", "ForecastTimelineDialog",
    "ModelTimelineWorker",
    "MAX_TIMELINE_HOURS",
    "install_timeline_controls", "refresh_timeline_controls",
]
