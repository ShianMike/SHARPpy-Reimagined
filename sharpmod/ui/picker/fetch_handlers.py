"""Observed fetch, model availability, and box mean handlers."""

from __future__ import annotations

from datetime import datetime
from qtpy.QtWidgets import QMessageBox
from sharpmod.ui.features.gui_common import APP_NAME
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_workers import AVAIL_UNKNOWN
import os
from sharpmod import gui_picker as _picker_api


class PickerFetchMixin:
    """Coordinate picker workers and completed profiles."""

    def _start_fetch(self, sid: str, when: datetime) -> None:
        """Fetch an observation from the source selected in the rail."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, APP_NAME, "A fetch is already in progress.")
            return
        _LOGGER.info("observed_fetch.start station=%s valid=%s", sid, when)
        self._settings.setValue("last_station", sid)

        cached = self._observed_profile_cache.get(self._observed_cache_key(sid, when))
        if cached is not None:
            fetched, _message, _station_label = cached
            _LOGGER.info(
                "observed_fetch.preflight_cache_hit station=%s valid=%s",
                sid,
                when,
            )
            self._display_prefetched_observation(fetched, sid, when)
            return

        source = self._observed_source()
        self._set_busy(True)
        label = _picker_api._observed_source_label(source)
        self.statusBar().showMessage(
            f"Fetching {sid} at {when:%Y-%m-%d %H}Z from {label}\u2026"
        )

        self._observed_sequence = int(getattr(self, "_observed_sequence", 0)) + 1
        self._observed_token = (id(self), self._observed_sequence, sid, when, source)
        self._observed_request = (sid, when, source)
        self._worker = _picker_api._FetchWorker(
            sid, when, parent=self, station=self._station(sid), provider=source
        )
        self._worker._sharpmod_observed_token = self._observed_token
        self._worker.finished_ok.connect(self._on_fetch_ok)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.finished.connect(self._on_observed_finished)
        self._observed_job_scroll.show()
        self._observed_job.begin(
            self._observed_token, "Observed sounding",
            f"{label} · {sid} · observation {when:%Y-%m-%d %H}Z",
            "Requesting the saved observed archive", total=1,
            unit="observed soundings", cancellable=True,
        )
        self._observed_job_retry = None
        self._worker.start()

    def _run_model_availability(self) -> None:
        request = self._model_availability_request
        if request is None:
            return

        # Keep this advisory path strictly single-flight. A stale Herbie probe
        # may still be returning from one bounded network request; starting a
        # replacement alongside it only multiplies network/native work and
        # leaves several QThreads for shutdown to force-stop.
        running = []
        for worker in list(self._model_availability_workers):
            try:
                if worker.isRunning():
                    running.append(worker)
            except RuntimeError:
                continue
        if running:
            self._model_availability_waiting_for_worker = True
            for worker in running:
                try:
                    worker.requestInterruption()
                except RuntimeError:
                    pass
            return
        self._model_availability_waiting_for_worker = False

        # Resolve the native GRIB boundary on the GUI thread before the probe
        # worker imports Herbie/ecCodes.  On Windows, first loading an
        # incompatible native library from QThread can terminate the process
        # instead of raising an ordinary Python exception.
        from sharpmod.tools import model_extract

        model, run_time, fxx, member = request
        if model_extract.requires_grib_runtime(model):
            try:
                model_extract.require_runtime_dependencies()
            except model_extract.RetrievalError:
                _LOGGER.exception("model_availability.runtime_unavailable")
                self._model_availability.set_status(
                    AVAIL_UNKNOWN,
                    "Forecast model runtime unavailable; Fetch remains available",
                )
                return

        token = self._model_availability_token
        worker = _picker_api._ModelAvailabilityWorker(
            model, run_time, fxx, member, token, parent=self
        )
        self._model_availability_workers.append(worker)
        worker.checked.connect(self._on_model_availability_checked)
        worker.finished.connect(self._on_model_availability_finished)
        worker.start()

    def _on_box_mean_ready(self, npz_path, profile) -> None:
        if self.sender() is not self._box_mean_worker:
            return
        extraction = self._box_extraction
        plan = extraction.plan if extraction is not None else None
        label = plan.model_label if plan is not None else "Model"
        try:
            # The ordinary decode path, so the averaged sounding arrives with the
            # same overlay, town lookup, and parcel logic as a single point.
            prof_col, stn_id = _picker_api._render().decode(npz_path)
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            _LOGGER.exception("box.mean_display_failed path=%s", npz_path)
            QMessageBox.critical(
                self,
                APP_NAME,
                f"The box was averaged, but the result could not be displayed:\n{exc}",
            )
            return
        run_time = extraction.run_time if extraction is not None else None
        fxx = int(extraction.fxx) if extraction is not None else 0
        # Through mean_model_label so the window title, the Skew-T title, and the
        # on-plot callout all make the claim in the same words. Phrasing it here
        # too gave two producers of one fact, free to drift apart.
        from sharpmod.analysis.box_mean import mean_model_label

        title = f"{APP_NAME} \u2014 {mean_model_label(label, profile.members)}"
        if run_time is not None:
            title += f" {run_time:%Y-%m-%d %H}Z F{fxx:03d}"
        win = self._show_sounding(prof_col, stn_id, title=title)
        # The averaged file is this window's only copy, so it is removed when the
        # window closes rather than left in the temporary directory.
        _picker_api._retain_model_data_until_close(win, npz_path, os.path.dirname(npz_path))
        self._box_output_dir = None
        self.statusBar().showMessage(
            f"Opened the {label} box mean: {profile.describe()}"
        )
        _LOGGER.info(
            "box.mean_displayed members=%d levels=%d clamped=%d viewer=%s",
            profile.members,
            profile.levels,
            profile.clamped_dewpoints,
            id(win),
        )
        if profile.clamped_dewpoints or profile.requested > profile.members:
            # Both are honest caveats about the average rather than errors, so
            # they belong in the status line, not a modal.
            self._model_progress_detail.setText(profile.describe())
            self._model_progress_detail.show()
