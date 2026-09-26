"""Saved point-model requests around the existing extractor and cache worker."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path

from qtpy.QtCore import QObject, Qt

from sharpmod.analysis.batch_extract import _sha256
from sharpmod.ui.features.gui_common import APP_NAME, as_utc
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.ui.features.gui_model_compare import ComparisonRequestSpec
from sharpmod.ui.features.gui_workers import _ModelFetchWorker, _cleanup_model_data, _portable_pair_valid


@dataclass(frozen=True)
class PointModelRequest:
    spec: ComparisonRequestSpec
    lat: float
    lon: float
    loc: str | None
    output_dir: str
    cached_grib: str | None = None
    cache_entry: object | None = None
    target: tuple | None = None

    @property
    def output_path(self):
        return os.path.join(self.output_dir, "sounding.npz")

    @property
    def affected_input(self):
        spec = self.spec
        member = f" · member {spec.member}" if spec.member else ""
        return (
            f"{spec.label} initialization {as_utc(spec.run_time):%Y-%m-%d %H:%MZ} "
            f"F{spec.fxx:03d}{member} · {self.lat:.4f}, {self.lon:.4f} "
            f"· valid {as_utc(spec.valid_time):%Y-%m-%d %H:%MZ} · {self.loc or spec.label}"
        )


@dataclass
class _PointSession:
    request: PointModelRequest
    pair_hashes: tuple | None = None
    viewer: object | None = None
    published: bool = False
    closed: bool = False
    updated_at: datetime | None = None


def _discard(session):
    request = session.request
    _cleanup_model_data(request.output_path, request.output_dir)


def _pair_hashes(path):
    path = Path(path)
    return _sha256(path), _sha256(path.with_suffix(".json"))


class PointModelCoordinator(QObject):
    """Own one point request, its raw artifact, and accepted GUI publication."""

    def __init__(self, picker):
        super().__init__(picker)
        self.picker = picker
        self.worker = self.session = self.retained = None
        self.token = None
        self._counter = 0
        self._cancel_requested = self._terminal = self._finishing = False
        self._pending_finished = None
        self._shutdown_started = False

    def _job(self):
        return getattr(self.picker, "_model_job", None)

    def _busy(self):
        return self.worker is not None or self._finishing or any(
            getattr(self.picker, name, None) is not None
            for name in (
                "_model_worker", "_model_timeline_worker", "_model_compare_worker",
                "_box_extract_worker", "_box_analysis_worker", "_box_mean_worker",
            )
        )

    @staticmethod
    def _target_live(request):
        if request.target is None:
            return True
        win, saved = request.target
        try:
            actual = tuple(win.spc_widget.prof_collections)
            return all(any(value is item for item in actual) for value in saved)
        except (AttributeError, RuntimeError):
            return False

    def open(self, request):
        if self._busy() or self._shutdown_started or getattr(self.picker, "_shutdown_started", False):
            return False
        previous = self.session
        if previous is not None and not previous.published:
            _discard(previous)
        self.session = _PointSession(request)
        if request.target is not None:
            self._watch_viewer(self.session, request.target[0])
        self._start(self.session)
        return True

    def _saved_pair(self, session):
        try:
            return bool(
                session.pair_hashes and _portable_pair_valid(session.request.output_path)
                and _pair_hashes(session.request.output_path) == session.pair_hashes
            )
        except OSError:
            return False

    def _start(self, session):
        request, entry = session.request, session.request.cache_entry
        os.makedirs(request.output_dir, exist_ok=True)
        self._counter += 1
        self.token = ("model-point", id(self), self._counter)
        self._cancel_requested = self._terminal = False
        worker = _ModelFetchWorker(
            request.spec.model, request.lat, request.lon, request.spec.run_time,
            request.spec.fxx, request.output_path, loc=request.loc,
            resolve_place=not bool(request.loc), member=request.spec.member,
            download_dir=request.output_dir, model_hour_cache=self.picker._model_hour_cache,
            cached_grib=request.cached_grib,
            cached_source_fields=entry.source_fields if entry is not None else (),
            cached_cache=self.picker._model_disk_cache if entry is not None else None,
            cached_directory=entry.path if entry is not None else None,
            cached_contract_version=entry.contract_version if entry is not None else None,
            completed_pair=session.pair_hashes if self._saved_pair(session) else None,
            parent=self.picker,
        )
        self.worker = self.picker._model_worker = worker
        worker._sharpmod_point_token, worker._sharpmod_point_session = self.token, session
        worker.finished_ok.connect(self._ok)
        worker.failed.connect(self._failed)
        worker.cancelled.connect(self._cancelled)
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        worker.finished.connect(lambda session=session: _discard(session) if session.closed else None)
        self.picker._set_model_busy(True)
        job = self._job()
        if job is not None:
            scroll = getattr(self.picker, "_model_job_scroll", None)
            if scroll is not None:
                scroll.show()
            job.begin(
                self.token, "Point-model acquisition", request.affected_input,
                "Preparing the exact saved point request", total=1,
                unit="point soundings prepared", retained=self._identity(self.retained),
                cancellable=True,
            )
            self.picker._model_job_retry = self.retry
            self._hide_legacy_detail()
        worker.start()

    def retry(self):
        session, job = self.session, self._job()
        if (
            session is None or session.closed or session.published or self._busy()
            or job is None or job.snapshot is None or job.snapshot.token != self.token
            or not job.snapshot.retryable or getattr(self.picker, "_shutdown_started", False)
        ):
            return False
        if not self._target_live(session.request):
            job.invalidate("Saved viewer changed; this retry cannot append to it.")
            return False
        self._start(session)
        return True

    def cancel(self):
        if self.worker is None:
            return
        self._cancel_requested = True
        job = self._job()
        if job is not None:
            job.request_cancel(self.token)
        try:
            self.worker.requestInterruption()
        except RuntimeError:
            pass

    def shutdown(self):
        if self._shutdown_started:
            return
        self.cancel()
        session = self.session
        if session is not None and not session.published:
            session.closed = True
            if self.worker is None:
                _discard(session)
        job = self._job()
        if job is not None and job.snapshot and job.snapshot.token == self.token:
            job.invalidate("Picker closed; no later point result can be displayed.")
            self.picker._model_job_retry = None
        self._shutdown_started = True

    def _current(self, worker):
        if (
            worker is None or worker is not self.worker
            or self._shutdown_started
            or getattr(worker, "_sharpmod_point_token", None) != self.token
            or getattr(self.picker, "_shutdown_started", False)
            or self.session is None or self.session.closed
        ):
            return False
        job = self._job()
        if job is not None:
            snapshot = job.snapshot
            if snapshot is None or snapshot.token != self.token:
                return False
            if snapshot.state == "cancelling":
                return self._cancel_requested
            if not job.accepts(self.token):
                return False
        if not self._target_live(self.session.request):
            if job is not None:
                job.invalidate("Saved viewer changed; the prior result is unchanged.")
            self.cancel()
            return False
        return True

    def _hide_legacy_detail(self):
        # The compact footer owns the byte-accurate transfer bar. Full stage
        # details remain available in the disclosure above its job record.
        details = getattr(self.picker, "_model_job_details", None)
        if details is None or not details.isVisible():
            self.picker._model_progress_detail.hide()

    def _progress(self, stage, total, baseline, started):
        worker = self.sender()
        if not self._current(worker) or self._terminal or self._cancel_requested:
            return
        self.picker._on_model_fetch_progress(stage, total, baseline, started)
        job = self._job()
        if job is not None:
            detail = self.picker._model_progress_detail.text()
            if stage == "saved":
                detail = "Reusing the saved complete sounding"
            job.update(self.token, stage=detail.rstrip("…"), done=int(stage == "complete"))
            self._hide_legacy_detail()

    def update_download(self, detail):
        if self._current(self.worker) and not self._terminal and not self._cancel_requested:
            job = self._job()
            if job is not None:
                job.update(self.token, stage=detail)
                self._hide_legacy_detail()

    def _record_result(self, worker, path=None):
        result = getattr(worker, "result", None)
        path = path or (result[0] if result else None)
        if path is None or Path(path).resolve() != Path(self.session.request.output_path).resolve():
            return False
        try:
            if not _portable_pair_valid(path):
                return False
            self.session.pair_hashes = _pair_hashes(path)
        except OSError:
            return False
        return True

    def _ok(self, path, _label, _run_time, _fxx):
        worker = self.sender()
        if not self._current(worker) or self._terminal:
            return
        usable = self._record_result(worker, path)
        self._terminal = self._finishing = True
        try:
            if self._cancel_requested:
                self._finish_cancelled(usable)
                return
            if not usable:
                raise ValueError("The saved point sounding is incomplete or invalid")
            job = self._job()
            if job is not None:
                job.update(self.token, stage="Checking the saved valid time and preparing the workspace", done=1)
            from sharpmod import gui_picker

            collection, station = gui_picker._render().decode(path)
            dates = tuple(as_utc(value) for value in collection._dates)
            if dates != (as_utc(self.session.request.spec.valid_time),):
                raise ValueError("Fetched sounding valid time does not match the saved request")
            if not self._current(worker):
                return
            if self._cancel_requested:
                self._finish_cancelled(True)
                return
            request = self.session.request
            if request.target is None:
                win = gui_picker.compose_interactive(
                    self.picker._config(), collection, self.picker,
                    stn_id=station, activate=False,
                )
                if not self._current(worker) or self._cancel_requested:
                    win.close()
                    if self._current(worker):
                        self._finish_cancelled(True)
                    return
                win.setWindowTitle(f"{APP_NAME} — {request.affected_input}")
                self.picker._viewers.append(win)
                watch_profiles = getattr(
                    self.picker, "_watch_loaded_profile_viewer", None
                )
                if callable(watch_profiles):
                    watch_profiles(win)
                self._watch_viewer(self.session, win)
            else:
                win = request.target[0]
                gui_picker._fill_profile_metadata(collection, station)
                if not self._current(worker) or self._cancel_requested:
                    if self._current(worker):
                        self._finish_cancelled(True)
                    return
                win.addProfileCollection(collection, focus=False, check_integrity=False)
                add_locator = getattr(win, "_sharpmod_add_locator_collection", None)
                if callable(add_locator):
                    add_locator(collection)
                gui_picker._start_locator_overlay_fetch(
                    win, collection, product=gui_picker._overlay_product_for(self.picker),
                    controller=self.picker, spec=gui_picker._locator_spec_for(self.picker),
                )
            refresh_profiles = getattr(
                self.picker, "_refresh_loaded_profile_markers", None
            )
            if callable(refresh_profiles):
                refresh_profiles()
            self.session.published = True
            self.session.updated_at = datetime.now(timezone.utc)
            self.retained = self.session
            if job is not None:
                job.update(self.token, retained=self._identity(self.retained))
                job.finish(self.token, counts=JobCounts(completed=1, requested=1), message="Saved point sounding displayed.")
            self.picker.statusBar().showMessage(f"Opened {request.affected_input}")
        except Exception as exc:  # noqa: BLE001 - retain a valid downloaded pair for repair
            if self._current(worker):
                if self._cancel_requested:
                    self._finish_cancelled(usable)
                else:
                    self._finish_failure(f"Fetched, but could not display: {exc}")
        finally:
            self._finishing = False
            pending, self._pending_finished = self._pending_finished, None
            if pending is not None:
                self._release(pending)

    def _finish_failure(self, message):
        job = self._job()
        if job is not None:
            job.finish(self.token, counts=JobCounts(failed=1, requested=1), message=message, retryable=True)
        self.picker.statusBar().showMessage(message)

    def _finish_cancelled(self, usable):
        job = self._job()
        message = (
            "Acquisition cancelled; a completed sounding awaits explicit Retry."
            if usable else "Point acquisition cancelled; the prior viewer is unchanged."
        )
        if job is not None:
            job.finish(
                self.token, counts=JobCounts(completed=int(usable), cancelled=int(not usable), requested=1),
                outcome="cancelled", message=message, retryable=True,
            )
        self.picker.statusBar().showMessage(message)

    def _failed(self, message):
        if not self._current(self.sender()) or self._terminal:
            return
        self._terminal = True
        if self._cancel_requested:
            self._finish_cancelled(self._record_result(self.worker))
        else:
            self._finish_failure(message)

    def _cancelled(self):
        if not self._current(self.sender()) or self._terminal:
            return
        self._terminal = self._cancel_requested = True
        self._finish_cancelled(self._record_result(self.worker))

    def _finished(self):
        worker = self.sender()
        if self._finishing and worker is self.worker:
            self._pending_finished = worker
            return
        self._release(worker)

    def _release(self, worker):
        if worker is self.worker:
            if self._current(worker) and not self._terminal:
                usable = self._record_result(worker)
                if self._cancel_requested:
                    self._finish_cancelled(usable)
                else:
                    job = self._job()
                    if job is not None:
                        job.finish(
                            self.token, counts=JobCounts(completed=int(usable), unknown=int(not usable), requested=1),
                            outcome="partial", message="Worker stopped without a terminal report; the prior viewer is unchanged.",
                            retryable=True,
                        )
            self.worker = self.picker._model_worker = None
            if not self._shutdown_started and not getattr(self.picker, "_shutdown_started", False):
                self.picker._set_model_busy(False)
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def _watch_viewer(self, session, win):
        session.viewer = win
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.destroyed.connect(lambda *_args: self._viewer_closed(session))

    def _viewer_closed(self, session):
        session.closed = True
        if self.retained is session:
            self.retained = None
        if self.worker is not None and getattr(self.worker, "_sharpmod_point_session", None) is session:
            self.cancel()
        else:
            _discard(session)
        if session is self.session and not self._shutdown_started and not getattr(self.picker, "_shutdown_started", False):
            job = self._job()
            if job is not None and job.snapshot and job.snapshot.token == self.token:
                job.invalidate("Point viewer closed; saved retry is no longer available.")

    @staticmethod
    def _identity(session):
        if session is None or session.closed or not session.published:
            return ""
        return (
            session.request.affected_input
            + (f" · updated {session.updated_at:%H:%M:%SZ}" if session.updated_at else "")
        )
