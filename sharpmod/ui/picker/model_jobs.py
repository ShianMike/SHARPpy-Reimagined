"""Forecast download, cache, progress, and prefetch workflows."""

from __future__ import annotations

import os
import tempfile
import time

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QMessageBox

from sharpmod.ui.features.gui_cache import CacheManagerDialog
from sharpmod.ui.features.gui_common import (
    APP_NAME, _LOGGER, _format_progress_bytes, _format_progress_duration,
)
from sharpmod.ui.features.gui_threading import (
    retain_worker_until_finished, shutdown_picker_workers,
)
from sharpmod.ui.features.gui_workers import (
    _ModelCachePruneWorker, _ModelPrefetchWorker, _cleanup_model_data,
)
from sharpmod.ui.picker.layout import set_button_busy as _set_button_busy
from sharpmod.ui.picker.selection import refresh_selection_feedback


class ModelJobsMixin:
    """Run forecast acquisition and synchronize its progress UI."""

    def _model_fetch(self, *_signal_args, cache_entry=None) -> None:
        if getattr(self, "_shutdown_started", False):
            return
        cfg = self._model_config()
        if cfg is None:
            QMessageBox.warning(self, APP_NAME, "Choose a forecast model first.")
            return
        if any(getattr(self, name).currentData() is None for name in (
            "_model_cycle", "_model_fxx_combo"
        ) if hasattr(self, name)):
            refresh_selection_feedback(self, "model")
            QMessageBox.warning(self, APP_NAME, "Choose an initialization and forecast lead first.")
            return
        if (
            self._model_worker is not None
            or getattr(self, "_model_timeline_worker", None) is not None
            or getattr(self, "_model_compare_worker", None) is not None
            or any(getattr(self, name, None) is not None for name in (
                "_box_extract_worker", "_box_analysis_worker", "_box_mean_worker",
            ))
        ):
            QMessageBox.information(
                self, APP_NAME, "A model fetch is already in progress."
            )
            return
        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        if not self._model_point_ok():
            QMessageBox.warning(
                self, APP_NAME, f"{cfg.label} does not cover {lat:.4f}, {lon:.4f}."
            )
            return

        from sharpmod.tools import model_extract

        # Keep native GRIB imports on the main Qt thread. The HRRR Zarr path
        # itself does not need ecCodes, but its automatic fallback does, and a
        # first native import from a Windows QThread can terminate the process.
        if model_extract.requires_grib_runtime(cfg):
            try:
                model_extract.require_runtime_dependencies()
            except model_extract.RetrievalError as exc:
                _LOGGER.exception("model_fetch.runtime_unavailable")
                self.statusBar().showMessage("Forecast model support unavailable")
                QMessageBox.critical(
                    self, APP_NAME, f"Forecast model support is unavailable:\n{exc}"
                )
                return

        self._ensure_model_cache()
        cached_grib = None
        if cache_entry is not None:
            try:
                candidates = self._model_disk_cache.valid_grib_paths(cache_entry.path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    f"The cached GRIB entry is no longer usable:\n{exc}",
                )
                return
            if not candidates:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "This cache entry no longer contains a complete GRIB payload.",
                )
                return
            cached_grib = max(
                candidates,
                key=lambda path: path.stat().st_size,
            )
            self._model_availability_timer.stop()
            self._model_availability_request = None

        self._cancel_model_prefetch(wait=True)
        fxx = self._model_selected_fxx()
        run_time = self._model_run_time()
        member = self._model_member_value()
        loc = self._model_loc.text().strip() or None
        self._remember_point(lat, lon, loc)

        download_dir = tempfile.mkdtemp(
            prefix=f"model_{cfg.key.replace('-', '_')}_{run_time:%Y%m%d%H}_f{fxx:03d}_"
        )
        npz_path = os.path.join(download_dir, "sounding.npz")

        _LOGGER.info(
            "model_fetch.start model=%s run=%s fxx=%03d lat=%.4f lon=%.4f "
            "download_dir=%s",
            cfg.key,
            run_time,
            fxx,
            lat,
            lon,
            download_dir,
        )

        from sharpmod.ui.features.gui_model_compare import ComparisonRequestSpec
        from sharpmod.ui.picker.model_point import PointModelCoordinator, PointModelRequest

        coordinator = getattr(self, "_model_point_coordinator", None)
        if coordinator is None:
            coordinator = PointModelCoordinator(self)
            self._model_point_coordinator = coordinator
        self._prune_closed_viewers()
        target = None
        if self._combine_soundings_enabled() and self._viewers:
            win = self._viewers[-1]
            target = win, tuple(win.spc_widget.prof_collections)
        request = PointModelRequest(
            ComparisonRequestSpec("point", cfg.key, cfg.label, run_time, fxx, member),
            lat, lon, loc, download_dir,
            cached_grib=os.fspath(cached_grib) if cached_grib is not None else None,
            cache_entry=cache_entry, target=target,
        )
        if not coordinator.open(request):
            _cleanup_model_data(npz_path, download_dir)

    def _model_fetch_timeline(self) -> None:
        """Open the forecast timeline workflow owned by gui_timeline."""
        from sharpmod.ui.features.gui_timeline import ForecastTimelineCoordinator

        coordinator = getattr(self, "_model_timeline_coordinator", None)
        if coordinator is None:
            coordinator = ForecastTimelineCoordinator(self)
            self._model_timeline_coordinator = coordinator
        coordinator.open()

    def _ensure_model_cache(self):
        """Create model caches on demand and prune them off the UI thread."""
        if self._model_disk_cache is not None:
            return self._model_disk_cache, self._model_hour_cache

        from sharpmod.models.model_disk_cache import ModelDiskCache
        from sharpmod.models.model_hour_cache import ModelHourCache

        disk_cache = ModelDiskCache()
        hour_cache = ModelHourCache(
            max_entries=1,
            directory_factory=disk_cache.directory_for,
            directory_protector=disk_cache.protect,
            metadata_writer=disk_cache.annotate,
            delete_download_dirs=False,
        )
        self._model_disk_cache = disk_cache
        self._model_hour_cache = hour_cache

        worker = _ModelCachePruneWorker(disk_cache, parent=self)
        self._model_cache_prune_worker = worker
        worker.pruned.connect(
            lambda count: _LOGGER.info(
                "model_disk_cache.background_prune removed=%d", count
            )
        )
        worker.failed.connect(
            lambda message: _LOGGER.warning(
                "model_disk_cache.background_prune_failed error=%s", message
            )
        )
        worker.finished.connect(self._on_model_cache_prune_finished)
        worker.start()
        return disk_cache, hour_cache

    def _on_model_cache_prune_finished(self) -> None:
        worker = self.sender()
        if self._model_cache_prune_worker is worker:
            self._model_cache_prune_worker = None
        worker.deleteLater()

    def _shutdown_model_cache(self) -> None:
        """Stop every owned worker before Qt destroys its native thread."""
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None:
            point.shutdown()
        timeline = getattr(self, "_model_timeline_coordinator", None)
        if timeline is not None:
            timeline.shutdown()
        comparison = getattr(self, "_model_compare_coordinator", None)
        if comparison is not None:
            comparison.shutdown()
        shutdown_picker_workers(
            self,
            retain=retain_worker_until_finished,
            logger=_LOGGER,
        )

    def _on_model_fetch_finished(self) -> None:
        """Release a completed worker before enabling the next request."""
        worker = self.sender()
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and worker is not None and point.worker is worker:
            return
        if self._model_worker is not worker:
            _LOGGER.warning(
                "model_fetch.finished_stale worker=%s current=%s",
                id(worker),
                id(self._model_worker),
            )
            worker.deleteLater()
            return
        _LOGGER.info("model_fetch.finished worker=%s", id(worker))
        self._model_worker = None
        try:
            self._set_model_busy(False)
        finally:
            worker.deleteLater()

    def _set_model_busy(self, busy: bool) -> None:
        _LOGGER.debug(
            "model_fetch.ui_busy busy=%s worker=%s",
            busy,
            id(self._model_worker) if self._model_worker else None,
        )
        if busy:
            point = getattr(self, "_model_point_coordinator", None)
            point_worker = None
            if point is not None:
                try:
                    point_worker = point.worker
                except AttributeError:
                    point_worker = None
            if (
                self._model_timeline_worker is None
                and hasattr(self, "_model_job")
                and point_worker is not self._model_worker
            ):
                self._model_job.invalidate("Another model acquisition superseded this job.")
                self._model_job.hide()
                self._model_job_scroll.hide()
                self._model_job_retry = None
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._model_fetch_btn.setEnabled(False)
            self._model_timeline_btn.setEnabled(False)
            compare_button = getattr(self, "_model_compare_btn", None)
            if compare_button is not None:
                compare_button.setEnabled(False)
            self._model_cancel_btn.setEnabled(True)
            _set_button_busy(self._model_cancel_btn, False, "")
            self._model_cancel_btn.show()
            _set_button_busy(self._model_fetch_btn, True, "Fetching\u2026")
            self._model_progress_stage = "locating"
            self._model_progress_total = 0
            self._model_progress_started = time.monotonic()
            self._model_progress_download_baseline = 0
            self._model_progress.setRange(0, 0)
            self._model_progress.setFormat("")
            self._model_progress.show()
            self._model_progress_detail.setText("Locating model run\u2026")
            self._model_progress_detail.show()
            self._model_progress_timer.start()
            refresh_selection_feedback(self, "model")
        else:
            QApplication.restoreOverrideCursor()
            self._model_progress_timer.stop()
            self._model_progress.hide()
            self._model_progress_detail.hide()
            self._model_progress_stage = ""
            self._model_progress_total = 0
            self._model_progress_download_baseline = 0
            self._model_cancel_btn.hide()
            _set_button_busy(self._model_fetch_btn, False, "")
            _set_button_busy(self._model_timeline_btn, False, "")
            compare_button = getattr(self, "_model_compare_btn", None)
            if compare_button is not None:
                compare_button.setEnabled(True)
            self._model_update_fetch_state()
        panels_cancel = getattr(self, "_panels_cancel_btn", None)
        if panels_cancel is not None:
            if busy:
                panels_cancel.show()
                panels_cancel.setEnabled(True)
                _set_button_busy(panels_cancel, False, "")
                self._panels_fetch_btn.setEnabled(False)
            else:
                panels_cancel.hide()
                self._panels_describe_point()
                self._model_update_fetch_state()
        refresh_selection_feedback(self, "panels")

    def _on_model_fetch_progress(
        self,
        stage: str,
        total_bytes: int,
        download_baseline: int | None = None,
        transfer_started: float | None = None,
    ) -> None:
        """Switch the visible model-fetch progress to a real worker stage."""
        stage = str(stage)
        total_bytes = max(0, int(total_bytes or 0))
        self._model_progress_stage = stage
        if total_bytes:
            self._model_progress_total = total_bytes
        self._model_progress.show()
        self._model_progress_detail.show()
        _LOGGER.info(
            "model_fetch.progress stage=%s total_bytes=%d",
            stage,
            self._model_progress_total,
        )

        if stage == "downloading":
            worker = self._model_worker
            self._model_progress_download_baseline = max(
                0,
                int(
                    download_baseline
                    if download_baseline is not None
                    else getattr(worker, "_progress_download_baseline", 0) or 0
                ),
            )
            self._model_progress_started = float(
                transfer_started
                if transfer_started is not None
                else getattr(worker, "_progress_transfer_started", 0.0)
                or time.monotonic()
            )
            if self._model_progress_total:
                self._model_progress.setRange(0, 100)
                self._model_progress.setValue(0)
                self._model_progress.setFormat("0%")
            else:
                self._model_progress.setRange(0, 0)
                self._model_progress.setFormat("")
            self._poll_model_fetch_progress()
            return

        messages = {
            "town": ("Resolving the selected town name…", "Locating town…"),
            "locating": ("Locating model run\u2026", "Locating\u2026"),
            "decoding": ("Decoding downloaded GRIB fields\u2026", "Decoding\u2026"),
            "cached": ("Using cached model hour\u2026", "Extracting\u2026"),
            "extracting": (
                "Extracting the nearest grid point\u2026",
                "Extracting\u2026",
            ),
            "writing": ("Writing the point sounding\u2026", "Writing\u2026"),
            "complete": ("Preparing the sounding display\u2026", "Preparing\u2026"),
            "rendering": ("Rendering the sounding window\u2026", "Rendering\u2026"),
        }
        detail, button = messages.get(
            stage, ("Processing forecast data\u2026", "Processing\u2026")
        )
        self._model_progress.setRange(0, 0)
        self._model_progress.setFormat("")
        self._model_progress_detail.setText(detail)
        self._model_fetch_btn.setText(button)
        self.statusBar().showMessage(detail)

    def _poll_model_fetch_progress(self) -> None:
        """Update download percentage from bytes in the isolated GRIB tree."""
        if self._model_progress_stage != "downloading":
            return
        worker = self._model_worker
        if worker is None:
            return
        downloaded = 0
        try:
            for root, _dirs, files in os.walk(worker._download_dir):
                for filename in files:
                    if filename.lower().endswith(
                        (".grib2", ".grib", ".grb2", ".grb", ".part")
                    ):
                        try:
                            downloaded += os.path.getsize(os.path.join(root, filename))
                        except OSError:
                            pass
        except OSError:
            pass

        downloaded = max(
            0,
            downloaded
            - int(getattr(self, "_model_progress_download_baseline", 0) or 0),
        )
        total = self._model_progress_total
        elapsed = max(0.001, time.monotonic() - self._model_progress_started)
        rate = downloaded / elapsed
        model_label = worker._model.upper()
        if total > 0:
            percent = min(100, max(0, int(downloaded * 100 / total)))
            self._model_progress.setRange(0, 100)
            self._model_progress.setValue(percent)
            self._model_progress.setFormat(f"{percent}%")
            detail = (
                f"{_format_progress_bytes(downloaded)} / "
                f"{_format_progress_bytes(total)}"
            )
            if rate > 0 and downloaded < total:
                remaining = (total - downloaded) / rate
                detail += (
                    f" \u2022 {_format_progress_bytes(rate)}/s"
                    f" \u2022 ~{_format_progress_duration(remaining)} left"
                )
            self._model_fetch_btn.setText(f"Downloading\u2026 {percent}%")
            operation = f"Downloading {model_label}"
            status = f"{operation}: {percent}% \u2014 {detail}"
        else:
            self._model_progress.setRange(0, 0)
            self._model_progress.setFormat("")
            detail = _format_progress_bytes(downloaded)
            if rate > 0:
                detail += f" \u2022 {_format_progress_bytes(rate)}/s"
            self._model_fetch_btn.setText("Downloading\u2026")
            operation = f"Downloading {model_label}"
            status = f"{operation}: {detail}"
        self._model_progress_detail.setText(detail)
        for setter in ("setToolTip", "setAccessibleDescription"):
            method = getattr(self._model_progress_detail, setter, None)
            if callable(method):
                method(detail)
        self.statusBar().showMessage(status)
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and point.worker is worker:
            point.update_download(status)

    def _on_model_fetch_failed(self, message: str) -> None:
        worker = self.sender()
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and worker is not None and point.worker is worker:
            return
        _LOGGER.error("model_fetch.failed message=%s", message)
        self.statusBar().showMessage("Forecast model fetch failed")
        QMessageBox.critical(self, APP_NAME, message)

    def _retry_model_job(self) -> None:
        retry = getattr(self, "_model_job_retry", None)
        if callable(retry):
            retry()

    def _cancel_model_fetch(self) -> None:
        panels_cancel = getattr(self, "_panels_cancel_btn", None)
        if panels_cancel is not None and not panels_cancel.isHidden():
            panels_cancel.setEnabled(False)
            _set_button_busy(panels_cancel, True, "Cancellation requested")
        comparison = getattr(self, "_model_compare_worker", None)
        if comparison is not None:
            coordinator = getattr(self, "_model_compare_coordinator", None)
            if coordinator is not None:
                coordinator.cancel()
            self._model_cancel_btn.setEnabled(False)
            _set_button_busy(self._model_cancel_btn, True, "Cancelling…")
            return
        timeline = self._model_timeline_worker
        if timeline is not None:
            coordinator = getattr(self, "_model_timeline_coordinator", None)
            if coordinator is not None:
                coordinator.cancel()
            else:
                timeline.requestInterruption()
            self._model_cancel_btn.setEnabled(False)
            _set_button_busy(self._model_cancel_btn, True, "Cancelling queue…")
            self.statusBar().showMessage(
                "Cancelling remaining timeline hours; completed hours are kept…"
            )
            return
        box = (
            self._box_extract_worker
            or self._box_analysis_worker
            or self._box_mean_worker
        )
        if box is not None:
            self._cancel_box_operation()
            self._model_cancel_btn.setEnabled(False)
            _set_button_busy(self._model_cancel_btn, True, "Cancelling…")
            self.statusBar().showMessage(
                "Cancelling the box sounding; completed points are kept…"
            )
            return
        worker = self._model_worker
        if worker is None:
            return
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and point.worker is worker:
            point.cancel()
        else:
            worker.requestInterruption()
        self._model_cancel_btn.setEnabled(False)
        _set_button_busy(self._model_cancel_btn, True, "Cancelling…")
        self.statusBar().showMessage("Cancelling forecast-model fetch…")

    def _on_model_fetch_cancelled(self) -> None:
        worker = self.sender()
        point = getattr(self, "_model_point_coordinator", None)
        if point is not None and worker is not None and point.worker is worker:
            return
        _LOGGER.info("model_fetch.cancelled")
        self.statusBar().showMessage("Forecast-model fetch cancelled", 5000)

    def _start_model_prefetch(
        self,
        model,
        lat,
        lon,
        run_time,
        current_fxx,
        member,
    ) -> None:
        if (
            not self._model_prefetch_enabled()
            or self._model_worker is not None
            or getattr(self, "_model_timeline_worker", None) is not None
        ):
            return
        if self._model_prefetch_worker is not None:
            return
        self._ensure_model_cache()
        from sharpmod.tools import model_extract

        cfg = model_extract.get_config(model)
        hours = model_extract.forecast_hours(cfg.key, cycle_hour=run_time.hour)
        next_fxx = next(
            (int(value) for value in hours if int(value) > int(current_fxx)),
            None,
        )
        if next_fxx is None:
            return
        worker = _ModelPrefetchWorker(
            cfg.key,
            lat,
            lon,
            run_time,
            next_fxx,
            member,
            self._model_hour_cache,
            parent=self,
        )
        self._model_prefetch_worker = worker
        worker.ready.connect(self._on_model_prefetch_ready)
        worker.failed.connect(self._on_model_prefetch_failed)
        worker.finished.connect(self._on_model_prefetch_finished)
        worker.start()
        _LOGGER.info(
            "model_prefetch.start model=%s run=%s fxx=%03d",
            cfg.key,
            run_time,
            next_fxx,
        )

    def _cancel_model_prefetch(self, *, wait: bool) -> bool:
        worker = self._model_prefetch_worker
        if worker is None:
            return True
        worker.requestInterruption()
        if not wait:
            return False
        finished = worker.wait(5000)
        if finished and self._model_prefetch_worker is worker:
            self._model_prefetch_worker = None
            worker.deleteLater()
        return bool(finished)

    def _on_model_prefetch_ready(self, label, run_time, fxx) -> None:
        self.statusBar().showMessage(
            f"Prefetched {label} {run_time:%Y-%m-%d %H}Z F{int(fxx):03d}",
            4000,
        )

    def _on_model_prefetch_failed(self, message) -> None:
        _LOGGER.info("model_prefetch.unavailable reason=%s", message)

    def _on_model_prefetch_finished(self) -> None:
        worker = self.sender()
        if self._model_prefetch_worker is worker:
            self._model_prefetch_worker = None
        worker.deleteLater()

    def _clear_model_cache(self) -> None:
        if (
            self._model_worker is not None
            or self._model_timeline_worker is not None
            or self._era5_worker is not None
        ):
            QMessageBox.information(
                self,
                APP_NAME,
                "Wait for the active model/ERA5 fetch to finish or cancel it first.",
            )
            return
        self._ensure_model_cache()
        self._cancel_model_prefetch(wait=True)
        self._model_hour_cache.clear()
        removed = self._model_disk_cache.clear()
        noun = "entry" if len(removed) == 1 else "entries"
        self.statusBar().showMessage(
            f"Cleared {len(removed)} downloaded model cache {noun}", 5000
        )

    def _show_cache_manager(self) -> None:
        self._ensure_model_cache()
        dialog = CacheManagerDialog(
            self._model_disk_cache,
            use_callback=self._reuse_cache_entry,
            parent=self,
        )
        dialog.exec()
