"""Qt coordinator for multi-model and ensemble comparison acquisition.

It owns worker/dialog progress, member retry and merge, and combined-viewer lifecycle
while ``gui_model_compare`` retains the feature dialog and request types."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from qtpy.QtCore import QObject
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog
from qtpy.QtWidgets import QMessageBox
import tempfile
from sharpmod.ui.features.gui_model_compare import (
    ModelComparisonDialog,
    ModelComparisonWorker,
    _AcquisitionRequest,
    _AcquisitionSession,
    _as_utc,
    _collection_meta,
    _discard_acquisition,
    _ensemble_target_context,
    _has_member_slot,
    _merge_retried_member,
    _spec_from_metadata,
    _spec_scope
)
from sharpmod.ui.features import gui_model_compare as _api


class _ModelComparisonCoordinator(QObject):
    """Own dialog, worker, progress, and one combined viewer lifecycle."""

    def __init__(self, picker):
        super().__init__(picker)
        self.picker = picker
        self.worker = None
        self._session = None
        self._retained = None
        self._token = 0
        self._job_token = None
        self._cancel_requested = False
        self._terminal = False
        self._finishing = False
        self._pending_finished = None

    def open(self) -> None:
        picker = self.picker
        if getattr(picker, "_shutdown_started", False):
            return
        if self.worker is not None:
            QMessageBox.information(
                picker, "Model comparison", "A comparison is already running."
            )
            return
        other_workers = (
            getattr(picker, "_model_worker", None),
            getattr(picker, "_model_timeline_worker", None),
            getattr(picker, "_box_extract_worker", None),
            getattr(picker, "_box_analysis_worker", None),
            getattr(picker, "_box_mean_worker", None),
        )
        if any(worker is not None for worker in other_workers):
            QMessageBox.information(
                picker,
                "Model comparison",
                "Another model-data operation is already running.",
            )
            return
        config = picker._model_config()
        if config is None:
            QMessageBox.warning(
                picker, "Model comparison", "Choose a forecast model first."
            )
            return
        lat = float(picker._model_lat.value())
        lon = float(picker._model_lon.value())
        loc = picker._model_loc.text().strip() or None
        if not picker._model_point_ok():
            QMessageBox.warning(
                picker,
                "Model comparison",
                f"{config.label} does not cover {lat:.4f}, {lon:.4f}.",
            )
            return
        dialog = _api.ModelComparisonDialog(
            current_model=config.key,
            run_time=picker._model_run_time(),
            fxx=picker._model_selected_fxx(),
            lat=lat,
            lon=lon,
            parent=picker,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        if self._busy():
            return
        specs = dialog.specs()
        kind = dialog.workspace_kind()
        from sharpmod.tools import model_extract

        if any(model_extract.requires_grib_runtime(spec.model) for spec in specs):
            try:
                model_extract.require_runtime_dependencies()
            except model_extract.RetrievalError as exc:
                QMessageBox.critical(
                    picker,
                    "Model comparison",
                    f"Forecast model support is unavailable:\n{exc}",
                )
                return

        picker._ensure_model_cache()
        picker._cancel_model_prefetch(wait=True)
        picker._remember_point(lat, lon, loc)
        self._new_session(_AcquisitionRequest(
            tuple(specs), lat, lon, loc, kind,
            tempfile.mkdtemp(prefix="model_comparison_"),
        ))

    def retry_ensemble(self, win, collection, members=None) -> bool:
        """Retry an exact member subset, or every unavailable member by default."""
        from sharpmod.analysis.ensemble_members import EnsembleAcquisition

        if self._busy():
            QMessageBox.information(
                self.picker,
                "Ensemble retry",
                "A model-data operation is already running.",
            )
            return False
        ledger = EnsembleAcquisition.from_collection(collection)
        raw_specs = (
            ledger.specs_for_retry()
            if members is None
            else ledger.specs_for_members(members)
        )
        if not raw_specs:
            self.picker.statusBar().showMessage(
                "No selected ensemble members have portable retry details",
                7000,
            )
            return False
        specs = tuple(
            spec for spec in (_spec_from_metadata(item) for item in raw_specs)
            if not _has_member_slot(collection, spec.member, spec.valid_time)
        )
        if not specs or not self._target_is_live(win, (collection,)):
            return False
        lat = _collection_meta(collection, "requested_lat")
        lon = _collection_meta(collection, "requested_lon")
        if lat is None:
            lat = _collection_meta(collection, "lat")
        if lon is None:
            lon = _collection_meta(collection, "lon")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "the saved ensemble has no usable requested coordinates"
            ) from exc
        self.picker._ensure_model_cache()
        self.picker._cancel_model_prefetch(wait=True)
        saved = self._session
        if (
            saved is not None and not saved.closed
            and saved.request.kind == "ensemble-retry" and saved.viewer is win
            and saved.request.target[1] is collection and saved.request.specs == specs
            and (saved.request.lat, saved.request.lon) == (lat, lon)
            and self._session_viewer_is_live(saved)
        ):
            self._start_session(saved)
            return True
        self._new_session(_AcquisitionRequest(
            specs, lat, lon, _collection_meta(collection, "loc"), "ensemble-retry",
            tempfile.mkdtemp(prefix="ensemble_retry_"), (win, collection, ledger),
            target_context=_ensemble_target_context(collection),
        ))
        return True

    def _job(self):
        return getattr(self.picker, "_model_job", None)

    def _busy(self):
        return bool(getattr(self.picker, "_shutdown_started", False) or any(
            getattr(self.picker, name, None) is not None for name in (
                "_model_worker", "_model_timeline_worker", "_model_compare_worker",
                "_box_extract_worker", "_box_analysis_worker", "_box_mean_worker",
            )
        ) or self.worker is not None)

    @staticmethod
    def _target_is_live(win, collections):
        try:
            actual = tuple(win.spc_widget.prof_collections)
            return all(any(value is item for value in actual) for item in collections)
        except (AttributeError, RuntimeError):
            return False

    def _new_session(self, request):
        previous = self._session
        if previous is not None and (not previous.collections or previous.closed):
            _discard_acquisition(previous)
        self._session = _AcquisitionSession(request)
        if request.target is not None:
            self._watch_viewer(self._session, request.target[0])
            self._retained = self._session
        self._start_session(self._session)

    def _start_session(self, session):
        request = session.request
        self._token += 1
        self._job_token = ("model-acquisition", id(self), self._token)
        self._cancel_requested = self._terminal = False
        session.errors.clear()
        self._initial_completed = {
            str(item.id) for item in getattr(session.result, "items", ())
            if item.status == "completed"
        }
        worker = _api.ModelComparisonWorker(
            request.specs, request.lat, request.lon, request.output_dir,
            loc=request.loc, disk_cache=self.picker._model_disk_cache, parent=self.picker,
        )
        self.worker = self.picker._model_compare_worker = worker
        worker._sharpmod_acquisition_token = self._job_token
        worker._sharpmod_acquisition_session = session
        worker.progress.connect(self._progress)
        worker.result_ready.connect(self._result)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.finished.connect(lambda session=session: (
            _discard_acquisition(session) if session.closed else None
        ))
        self.picker._set_model_busy(True)
        self.picker._model_progress_timer.stop()
        self.picker._model_progress.setRange(0, len(request.specs))
        self.picker._model_progress.setValue(0)
        self.picker._model_progress_detail.setText("Fetching saved unresolved soundings…")
        job = self._job()
        if job is not None:
            scroll = getattr(self.picker, "_model_job_scroll", None)
            if scroll is not None:
                scroll.show()
            operation = {
                "compare": "Aligned comparison acquisition",
                "ensemble": "Ensemble acquisition",
                "ensemble-retry": "Ensemble member retrieval",
            }[request.kind]
            remaining = len(request.specs) - len(self._initial_completed)
            job.begin(
                self._job_token, operation, request.affected_input,
                "Fetching saved unresolved downloads; reusing verified artifacts",
                total=remaining, unit="soundings downloaded", cancellable=True,
                retained=self._retained_identity(self._retained),
            )
            self.picker._model_job_retry = self.retry
            self.picker._model_progress.hide()
            self.picker._model_progress_detail.hide()
        self.picker.statusBar().showMessage(f"Fetching {len(request.specs)} saved soundings…")
        worker.start()

    def retry(self):
        session, job = self._session, self._job()
        if (
            session is None or session.closed or self._busy() or job is None
            or job.snapshot is None or job.snapshot.token != self._job_token
            or not job.snapshot.retryable
        ):
            return False
        if session.viewer is not None and not self._session_viewer_is_live(session):
            job.invalidate("Saved acquisition target changed; this retry cannot modify it.")
            return False
        self._start_session(session)
        return True

    def cancel(self) -> None:
        if self.worker is None:
            return
        self._cancel_requested = True
        job = self._job()
        if job is not None:
            job.request_cancel(self._job_token)
        self.worker.requestInterruption()
        self.picker.statusBar().showMessage("Cancelling acquisition; completed artifacts are kept…")

    def shutdown(self):
        self.cancel()
        session = self._session
        if session is not None and not session.collections:
            session.closed = True
            if self.worker is None:
                _discard_acquisition(session)
        job = self._job()
        if job is not None and job.snapshot and job.snapshot.token == self._job_token:
            job.invalidate("Picker closed; no later acquisition result can be displayed.")
        self.picker._model_job_retry = None

    def _watch_viewer(self, session, win):
        session.viewer = win
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.destroyed.connect(lambda *_args: self._viewer_destroyed(session))

    def _viewer_destroyed(self, session):
        session.closed = True
        if self._retained is session:
            self._retained = None
        worker = self.worker
        active = (
            worker is not None
            and getattr(worker, "_sharpmod_acquisition_session", None) is session
        )
        if active:
            self._cancel_requested = True
            try:
                worker.requestInterruption()
            except RuntimeError:  # a shutdown-retained native thread already retired
                _discard_acquisition(session)
        else:
            _discard_acquisition(session)
        if (
            session is self._session and not getattr(self.picker, "_shutdown_started", False)
        ):
            job = self._job()
            if job is not None and job.snapshot and job.snapshot.token == self._job_token:
                job.invalidate("Acquisition viewer closed; saved retry is no longer available.")

    def _session_viewer_is_live(self, session):
        target = session.request.target
        values = (target[1],) if target is not None else tuple(session.collections.values())
        try:
            return self._target_is_live(session.viewer, values) and (
                target is None or _ensemble_target_context(target[1]) == session.request.target_context
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _current_session(self, worker):
        session = self._session
        if (
            worker is None or worker is not self.worker
            or worker is not getattr(self.picker, "_model_compare_worker", None)
            or session is None or session.closed
            or getattr(worker, "_sharpmod_acquisition_token", None) != self._job_token
            or getattr(self.picker, "_shutdown_started", False)
        ):
            return None
        if session.viewer is not None and not self._session_viewer_is_live(session):
            self._cancel_requested = True
            worker.requestInterruption()
            if not session.collections:
                session.closed = True
            job = self._job()
            if job is not None and job.snapshot and job.snapshot.token == self._job_token:
                job.invalidate("Saved acquisition target replaced; no later result can modify it.")
            return None
        job = self._job()
        if job is not None and job.snapshot and job.snapshot.token != self._job_token:
            return None
        return session

    @staticmethod
    def _retained_identity(session):
        if session is None or session.closed or session.viewer is None:
            return ""
        request = session.request
        if request.target is not None:
            collection = request.target[1]
            members = tuple(collection._profs)
            dates = tuple(_as_utc(date) for date in collection._dates)
            label = str(_collection_meta(collection, "model", request.specs[0].label))
            portable = tuple(_spec_from_metadata(spec) for spec in request.target[2].request_specs
                             if str(spec.get("member")) in members)
            scope = _spec_scope(portable) if portable else f"{label} · loaded members {', '.join(members)}"
        else:
            if not session.collections:
                return ""
            dates = tuple(_as_utc(date) for collection in session.collections.values()
                          for date in collection._dates)
            scope = _spec_scope(spec for spec in request.specs if spec.request_id in session.collections)
        valid = f"valid {min(dates):%Y-%m-%d %H:%MZ}"
        if min(dates) != max(dates):
            valid += f" to {max(dates):%Y-%m-%d %H:%MZ}"
        updated = f" · updated {session.updated_at:%H:%M:%SZ}" if session.updated_at else ""
        return f"{scope} · {valid}{updated}"

    def _progress(self, stage: str, completed: int, total: int) -> None:
        if self._terminal or self._cancel_requested or self._current_session(self.sender()) is None:
            return
        job = self._job()
        if job is not None:
            done = max(0, min(int(completed) - len(self._initial_completed), job.snapshot.total))
            stage_text = "Sounding downloaded; waiting for remaining work" if stage == "completed" else stage.replace("_", " ")
            job.update(self._job_token, stage=stage_text,
                       done=done, retained=self._retained_identity(self._retained))
        else:
            self.picker._model_progress.setRange(0, max(1, int(total)))
            self.picker._model_progress.setValue(max(0, int(completed)))

    def _result(self, result):
        self._finish_result(self.sender(), result)

    def _cancelled(self, result):
        if self._current_session(self.sender()) is None:
            return
        self._cancel_requested = True
        self._finish_result(self.sender(), result)

    def _failed(self, message):
        worker = self.sender()
        self._finish_result(worker, getattr(worker, "result", None), error=str(message))

    def _finish_result(self, worker, result, *, error=""):
        session = self._current_session(worker)
        if session is None or self._terminal:
            return
        self._terminal = self._finishing = True
        if result is not None:
            session.result = result
        message = error
        try:
            if not self._cancel_requested and result is not None:
                job = self._job()
                if job is not None:
                    job.update(self._job_token, stage="Checking saved valid times and preparing the workspace")
                try:
                    displayed = self._publish(session, result)
                    message = (f"{error} · {displayed}" if error and displayed else displayed or error)
                except Exception as exc:  # noqa: BLE001 - GUI publication boundary
                    message = f"Fetched, but could not be displayed: {exc}"
                    for item in result.items:
                        if item.status == "completed" and item.id not in session.collections:
                            session.errors[str(item.id)] = str(exc)
                if session.collections:
                    self._retained = session
            counts = self._counts(session, result)
            if result is None and self._cancel_requested and not getattr(worker, "batch_started", True):
                from sharpmod.ui.features.gui_jobs import JobCounts

                counts = JobCounts(cancelled=len(session.request.specs), requested=len(session.request.specs))
            pending = sum(
                item.status == "completed" and item.id not in session.collections
                and item.id not in session.errors
                for item in getattr(session.result, "items", ())
            )
            reasons = list(session.errors.values())
            reasons.extend(
                str(item.error.get("message") or item.error.get("type") or "Acquisition failed")
                for item in getattr(result, "items", ())
                if item.status == "failed" and isinstance(item.error, Mapping)
            )
            if reasons:
                message += (" · " if message else "") + reasons[0]
            if self._cancel_requested:
                message = "Acquisition cancelled; the prior viewer is unchanged."
            if pending:
                message += f" · {pending} completed artifact(s) await explicit Retry."
            retryable = any(spec.request_id not in session.collections
                            for spec in session.request.specs)
            job = self._job()
            if job is not None:
                job.update(self._job_token, retained=self._retained_identity(self._retained))
                job.finish(
                    self._job_token, counts=counts, message=message,
                    retryable=retryable,
                    outcome="cancelled" if self._cancel_requested else ("failed" if error else None),
                )
            self.picker.statusBar().showMessage(message or counts.summary, 7000)
        finally:
            self._finishing = False
            pending_worker, self._pending_finished = self._pending_finished, None
            if pending_worker is not None:
                self._finish_worker(pending_worker)

    @staticmethod
    def _counts(session, result):
        from sharpmod.ui.features.gui_jobs import JobCounts

        items = {str(item.id): item for item in getattr(result, "items", ())}
        previous = {str(item.id): item for item in getattr(session.result, "items", ())}
        counts = dict(completed=0, failed=0, cancelled=0, unknown=0)
        for spec in session.request.specs:
            item = items.get(spec.request_id)
            status = str(getattr(item, "status", "unknown"))
            if spec.request_id in session.collections:
                status = "completed"
            elif spec.request_id in session.errors:
                status = "failed"
            elif result is None and getattr(previous.get(spec.request_id), "status", "") == "completed":
                status = "completed"
            if status not in counts:
                status = "unknown"
            counts[status] += 1
        return JobCounts(**counts, requested=len(session.request.specs))

    @staticmethod
    def _ensemble_ledger(session, result, collection=None, *, loaded_ids=()):
        from sharpmod.analysis.ensemble_members import EnsembleAcquisition, MemberFailure

        request = session.request
        base = request.target[2] if request.target else EnsembleAcquisition.from_batch(request.specs, result)
        specs = {str(spec.member): spec for spec in request.specs}
        items = {str(item.id): item for item in result.items}
        old_failures = {failure.member: failure for failure in base.failures}
        loaded = tuple(
            member for member in base.requested_members
            if (member in collection._profs if collection is not None else
                specs[member].request_id in loaded_ids)
        )
        failures = []
        for member in base.requested_members:
            if member in loaded:
                continue
            spec = specs.get(member)
            item = items.get(spec.request_id) if spec else None
            if spec is None and member in old_failures:
                failures.append(old_failures[member])
                continue
            reason = session.errors.get(spec.request_id) if spec else None
            status = "failed" if reason else str(getattr(item, "status", "unknown"))
            if status not in {"failed", "cancelled"}:
                status = "unknown"
            if not reason and isinstance(getattr(item, "error", None), Mapping):
                reason = str(item.error.get("message") or item.error.get("type") or "") or None
            failures.append(MemberFailure(member, status, reason))
        return EnsembleAcquisition(base.requested_members, loaded, tuple(failures), base.request_specs)

    def _publish(self, session, result):
        from sharpmod.gui_picker import (
            compose_interactive, _fill_profile_metadata, _locator_spec_for,
            _overlay_product_for, _render, _start_locator_overlay_fetch,
        )
        request = session.request
        specs = {spec.request_id: spec for spec in request.specs}
        decoded = {}
        for item in result.items:
            if item.status != "completed" or item.id in session.collections:
                continue
            if self._cancel_requested or self._current_session(self.worker) is None:
                return ""
            try:
                collection, station_id = _render().decode(str(item.output_path))
                dates = tuple(collection._dates)
                expected = _as_utc(specs[item.id].valid_time)
                if len(dates) != 1 or _as_utc(dates[0]) != expected:
                    raise ValueError(f"actual valid time does not match saved valid time {expected:%Y-%m-%d %H:%MZ}")
                decoded[item.id] = (collection, station_id)
            except Exception as exc:  # noqa: BLE001 - individual sounding boundary
                session.errors[str(item.id)] = str(exc)
        if self._cancel_requested or self._current_session(self.worker) is None:
            return ""
        if session.viewer is None:
            minimum = 1 if request.kind == "ensemble" else 2
            if len(decoded) < minimum:
                return f"Fewer than {minimum} exact-time sounding(s) are usable; no new workspace opened."
            first_id = next(iter(decoded))
            first, station_id = decoded[first_id]
            if request.kind == "ensemble":
                from sharpmod.analysis.profile_timeline import combine_ensemble_collections

                first = combine_ensemble_collections(
                    [value[0] for value in decoded.values()],
                    member_names=[str(specs[key].member) for key in decoded],
                    acquisition=self._ensemble_ledger(session, result, loaded_ids=set(decoded)),
                )
            self.picker._prune_closed_viewers()
            win = compose_interactive(self.picker._config(), first, self.picker,
                                      stn_id=station_id, activate=False)
            if self._cancel_requested or self._current_session(self.worker) is None:
                win.close()
                return ""
            self._watch_viewer(session, win)
            self.picker._viewers.append(win)
            watch_profiles = getattr(
                self.picker, "_watch_loaded_profile_viewer", None
            )
            if callable(watch_profiles):
                watch_profiles(win)
            if request.kind == "ensemble":
                session.collections.update(dict.fromkeys(decoded, first))
                decoded.clear()
            else:
                session.collections[first_id] = first
                decoded.pop(first_id)
            workspace = getattr(win, "_sharpmod_analysis_workspace", None)
            show = getattr(workspace, "show_ensemble" if request.kind == "ensemble" else "show_compare", None)
            if callable(show):
                show()
        win = session.viewer
        target = request.target[1] if request.target else (
            next(iter(session.collections.values())) if request.kind == "ensemble" else None
        )
        changed = False
        for request_id, (collection, station_id) in decoded.items():
            if self._cancel_requested or self._current_session(self.worker) is None:
                break
            if target is not None:
                spec = specs[request_id]
                merged = _merge_retried_member(target, str(spec.member), collection)
                if merged:
                    provenance = list(_collection_meta(target, "ensemble_provenance", ()) or ())
                    source = dict(getattr(collection, "_meta", {}) or {})
                    source["member_label"] = str(spec.member)
                    provenance.append(source)
                    target._meta["ensemble_provenance"] = provenance
                session.collections[request_id] = target
                changed = changed or merged
            else:
                _fill_profile_metadata(collection, station_id)
                win.addProfileCollection(collection, focus=False, check_integrity=False)
                add_locator = getattr(win, "_sharpmod_add_locator_collection", None)
                if callable(add_locator):
                    add_locator(collection)
                session.collections[request_id] = collection
                changed = True
                _start_locator_overlay_fetch(
                    win, collection, product=_overlay_product_for(self.picker),
                    controller=self.picker, spec=_locator_spec_for(self.picker),
                )
        if target is not None and not self._cancel_requested:
            self._ensemble_ledger(session, result, target).attach(target)
        if changed:
            win.spc_widget.updateProfs()
        refresh_profiles = getattr(
            self.picker, "_refresh_loaded_profile_markers", None
        )
        if callable(refresh_profiles):
            refresh_profiles()
        if session.collections:
            session.updated_at = datetime.now(timezone.utc)
            self._retained = session
        return f"{len(session.collections)} of {len(request.specs)} saved sounding(s) displayed."

    def _finished(self):
        worker = self.sender()
        if self._finishing and worker is self.worker:
            self._pending_finished = worker
            return
        if worker is self.worker and not self._terminal:
            self._finish_result(worker, getattr(worker, "result", None),
                                error="" if self._cancel_requested else "Worker stopped without a reported outcome.")
        self._finish_worker(worker)

    def _finish_worker(self, worker):
        if worker is None:
            return
        session = getattr(worker, "_sharpmod_acquisition_session", None)
        if worker is self.worker:
            self.worker = None
            if getattr(self.picker, "_model_compare_worker", None) is worker:
                self.picker._model_compare_worker = None
            if not getattr(self.picker, "_shutdown_started", False):
                self.picker._set_model_busy(False)
        if session is not None and session.closed:
            _discard_acquisition(session)
        worker.deleteLater()
