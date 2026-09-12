"""Forecast-hour playback controls for a multi-time SHARPpy collection."""

from __future__ import annotations

import os
import shutil
import tempfile
import weakref

from qtpy.QtCore import QObject, Qt, QThread, QTimer, Signal
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSlider,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
)

from sharpmod.gui_common import APP_NAME, _LOGGER, _render
from sharpmod.profile_timeline import forecast_hour_range
from sharpmod.theme import OBJ_ERROR_TEXT, OBJ_STATUS


MAX_TIMELINE_HOURS = 72


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
    failed = Signal(str)

    def __init__(self, model, lat, lon, run_time, hours, output_dir, *,
                 loc=None, resolve_place=False, member=None, disk_cache=None,
                 parent=None):
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
        self._runner = None
        self._completed = 0

    def requestInterruption(self):  # noqa: N802 - Qt API override
        super().requestInterruption()
        if self._runner is not None:
            self._runner.cancel()

    def run(self):
        from sharpmod.batch_extract import BatchRequest
        from sharpmod.gui_batch_process import (
            IsolatedBatchCancelled,
            IsolatedBatchRunner,
        )

        if self.resolve_place and not self.loc:
            self.progress.emit(-1, "town", 0, len(self.hours))
            from sharpmod.place_names import reverse_town_name
            from sharpmod.tools import model_extract
            self.loc = reverse_town_name(self.lat, self.lon) \
                or model_extract.get_config(self.model).label
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
                self._completed += 1
                self.item_ready.emit(paths[request_id], int(hour))
            elif kind in {"failed", "cancelled"} and hour >= 0:
                error = event.get("error") or {}
                message = str(error.get("message") or kind)
                self.item_failed.emit(int(hour), message)
            self.progress.emit(
                int(hour), stage, self._completed, len(self.hours)
            )

        try:
            self._runner = IsolatedBatchRunner()
            result = self._runner.run(
                requests,
                output_dir=self.output_dir,
                max_workers=min(2, max(1, len(self.hours))),
                progress_callback=on_progress,
                disk_cache=self.disk_cache,
            )
        except IsolatedBatchCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Forecast timeline failed: {exc}")
            return
        finally:
            self._runner = None
        self.result_ready.emit(result)


class ForecastTimelineCoordinator(QObject):
    """Own forecast timeline selection, worker, streaming, and cleanup."""

    def __init__(self, picker):
        super().__init__(picker)
        self.picker = picker

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
        available = model_extract.forecast_hours(
            cfg, cycle_hour=self.picker._model_run_time().hour
        )
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
        run_time = self.picker._model_run_time()
        member = self.picker._model_member_value()
        loc = self.picker._model_loc.text().strip() or None
        output_dir = tempfile.mkdtemp(
            prefix=(f"timeline_{cfg.key.replace('-', '_')}_{run_time:%Y%m%d%H}_")
        )
        worker = ModelTimelineWorker(
            cfg.key,
            lat,
            lon,
            run_time,
            hours,
            output_dir,
            loc=loc,
            resolve_place=not bool(loc),
            member=member,
            disk_cache=self.picker._model_disk_cache,
            parent=self.picker,
        )
        worker._sharpmod_viewer = None
        worker._sharpmod_collection = None
        worker._sharpmod_paths = []
        worker._sharpmod_failures = {}
        worker._sharpmod_viewer_closed = False
        self.picker._model_timeline_worker = worker
        self.picker._remember_point(lat, lon, loc)
        worker.item_ready.connect(self._on_timeline_item_ready)
        worker.item_failed.connect(self._on_timeline_item_failed)
        worker.progress.connect(self._on_timeline_progress)
        worker.result_ready.connect(self._on_timeline_result)
        worker.failed.connect(self._on_timeline_failed)
        worker.finished.connect(self._on_timeline_finished)
        self.picker._set_model_busy(True)
        self.picker._model_progress_timer.stop()
        self.picker._model_fetch_btn.setText("Timeline queued…")
        self.picker._model_timeline_btn.setText("Timeline running…")
        self.picker._model_progress.setRange(0, len(hours))
        self.picker._model_progress.setValue(0)
        self.picker._model_progress.setFormat(f"0 / {len(hours)} hours")
        self.picker._model_progress_detail.setText(
            f"Queued {len(hours)} forecast hours; completed hours will open "
            "as they arrive."
        )
        self.picker.statusBar().showMessage(
            f"Fetching {cfg.label} timeline F{hours[0]:03d}–F{hours[-1]:03d}…"
        )
        worker.start()

    def _on_timeline_item_ready(self, npz_path: str, fxx: int) -> None:
        worker = self.sender()
        if worker is not self.picker._model_timeline_worker or getattr(
            worker, "_sharpmod_viewer_closed", False
        ):
            return
        try:
            prof_col, stn_id = _render().decode(npz_path)
            collection = getattr(worker, "_sharpmod_collection", None)
            if collection is None:
                source_meta = dict(getattr(prof_col, "_meta", {}))
                prof_col.setMeta("timeline", True)
                prof_col.setMeta("timeline_count", 1)
                prof_col.setMeta("timeline_hours", [int(fxx)])
                prof_col.setMeta("timeline_sources", [str(npz_path)])
                prof_col.setMeta("timeline_provenance", [source_meta])
                self.picker._prune_closed_viewers()
                win = compose_interactive(
                    self.picker._config(),
                    prof_col,
                    self.picker,
                    stn_id=stn_id,
                )
                win.setWindowTitle(f"{APP_NAME} — Forecast Timeline (1 hour loaded)")
                self.picker._viewers.append(win)
                worker._sharpmod_viewer = win
                worker._sharpmod_collection = prof_col
                output_dir = worker.output_dir
                win.destroyed.connect(
                    lambda *_args, worker=worker, output_dir=output_dir: (
                        self._on_timeline_viewer_destroyed(worker, output_dir)
                    )
                )
            else:
                from sharpmod.profile_timeline import append_collection

                append_collection(collection, prof_col)
                win = worker._sharpmod_viewer
                win.spc_widget.updateProfs()
                refresh_timeline_controls(win)
                count = int(collection.getMeta("timeline_count"))
                win.setWindowTitle(
                    f"{APP_NAME} — Forecast Timeline ({count} hours loaded)"
                )
            worker._sharpmod_paths.append(str(npz_path))
        except Exception as exc:  # noqa: BLE001 - decode/render boundary
            _LOGGER.exception(
                "forecast_timeline.display_failed path=%s fxx=%s", npz_path, fxx
            )
            worker._sharpmod_failures[int(fxx)] = str(exc)
            self.picker.statusBar().showMessage(
                f"F{int(fxx):03d} downloaded but could not be displayed"
            )

    def _on_timeline_item_failed(self, fxx: int, message: str) -> None:
        worker = self.sender()
        if worker is self.picker._model_timeline_worker:
            worker._sharpmod_failures[int(fxx)] = str(message)
            self.picker.statusBar().showMessage(
                f"Timeline F{int(fxx):03d} unavailable: {message}", 7000
            )

    def _on_timeline_progress(
        self, fxx: int, stage: str, completed: int, total: int
    ) -> None:
        if self.sender() is not self.picker._model_timeline_worker:
            return
        completed = max(0, int(completed))
        total = max(1, int(total))
        self.picker._model_progress.setRange(0, total)
        self.picker._model_progress.setValue(completed)
        self.picker._model_progress.setFormat(f"{completed} / {total} hours")
        prefix = f"F{int(fxx):03d}" if int(fxx) >= 0 else "Timeline"
        self.picker._model_progress_detail.setText(
            f"{prefix}: {str(stage).replace('_', ' ')} — "
            f"{completed} of {total} complete"
        )

    def _on_timeline_result(self, result) -> None:
        worker = self.sender()
        if worker is not self.picker._model_timeline_worker:
            return
        missing = [item for item in result.items if item.status != "completed"]
        if missing:
            summary = ", ".join(
                f"{item.id.upper()} ({item.status})" for item in missing
            )
            self.picker.statusBar().showMessage(
                f"Timeline kept {result.completed} completed hour(s); "
                f"missing: {summary}",
                12000,
            )
            QMessageBox.information(
                self.picker,
                "Forecast Timeline — Partial Result",
                f"Kept {result.completed} completed forecast hour(s).\n\n"
                f"Unavailable or cancelled hours:\n{summary}",
            )
        else:
            self.picker.statusBar().showMessage(
                f"Forecast timeline complete: {result.completed} hours", 7000
            )

    def _on_timeline_failed(self, message: str) -> None:
        worker = self.sender()
        if worker is not self.picker._model_timeline_worker:
            return
        if getattr(worker, "_sharpmod_paths", []):
            QMessageBox.warning(
                self.picker,
                APP_NAME,
                f"The remaining timeline queue stopped, but completed hours "
                f"were kept:\n{message}",
            )
        else:
            QMessageBox.critical(self.picker, APP_NAME, str(message))

    def _on_timeline_viewer_destroyed(self, worker, output_dir: str) -> None:
        if self.picker._model_timeline_worker is worker:
            worker._sharpmod_viewer_closed = True
            worker.requestInterruption()
            return
        shutil.rmtree(output_dir, ignore_errors=True)

    def _on_timeline_finished(self) -> None:
        worker = self.sender()
        if self.picker._model_timeline_worker is worker:
            self.picker._model_timeline_worker = None
            self.picker._set_model_busy(False)
        viewer = getattr(worker, "_sharpmod_viewer", None)
        if viewer is None or getattr(worker, "_sharpmod_viewer_closed", False):
            shutil.rmtree(worker.output_dir, ignore_errors=True)
        worker.deleteLater()


def install_timeline_controls(win, collection) -> QToolBar | None:
    """Install a slider and loopable playback for a multi-time collection."""
    if len(_collection_dates(collection)) < 2 \
            or getattr(win, "_sharpmod_timeline_toolbar", None):
        return None

    toolbar = QToolBar("Forecast Timeline", win)
    toolbar.setObjectName("sharpmodForecastTimeline")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)

    previous = QAction("Previous", win)
    previous.setToolTip("Previous forecast hour")
    toolbar.addAction(previous)

    play = QAction("Play", win)
    play.setCheckable(True)
    play.setToolTip("Play or pause the forecast timeline")
    toolbar.addAction(play)

    following = QAction("Next", win)
    following.setToolTip("Next forecast hour")
    toolbar.addAction(following)

    toolbar.addSeparator()
    label = QLabel(win)
    label.setMinimumWidth(205)
    toolbar.addWidget(label)

    slider = QSlider(Qt.Horizontal, win)
    slider.setRange(0, len(_collection_dates(collection)) - 1)
    slider.setSingleStep(1)
    slider.setPageStep(1)
    slider.setTracking(True)
    slider.setMinimumWidth(220)
    slider.setToolTip("Drag to a forecast valid time")
    toolbar.addWidget(slider)

    loop = QAction("Loop", win)
    loop.setCheckable(True)
    loop.setChecked(True)
    loop.setToolTip("Loop to the first hour after the last")
    toolbar.addAction(loop)

    timer = QTimer(win)
    timer.setInterval(900)

    # See set_index below for why this is weak.
    win_ref = weakref.ref(win)

    def current_index():
        dates = _collection_dates(collection)
        try:
            return dates.index(collection.getCurrentDate())
        except (AttributeError, ValueError):
            return 0

    def set_index(index):
        # Resolved from the weakref into a local of the same name, so this
        # closure does not capture the outer ``win``. The slider, actions and
        # timer are all children of the window and Qt holds their connections
        # C++-side, so a strong capture makes win -> slider -> connection ->
        # set_index -> win an uncollectable cycle. That is not only a leak: the
        # window's wrapper then survives to interpreter exit, after Qt has torn
        # the C++ side down, and freeing it is an access violation. Measured on
        # the equivalent handler in gui_viewer._install_tip_bar: 6 crashes in 14
        # runs with a strong capture, 0 in 14 once held weakly. This installer
        # runs for every multi-time collection, i.e. every forecast sounding.
        win = win_ref()
        if win is None:
            return
        dates = _collection_dates(collection)
        if not dates:
            return
        slider.setMaximum(len(dates) - 1)
        index = max(0, min(len(dates) - 1, int(index)))
        collection.setCurrentDate(dates[index])
        # Keep any other non-observed overlays synchronized with this valid
        # time, matching the vendored left/right-arrow behavior.
        for other in getattr(win.spc_widget, "prof_collections", ()):
            if other is collection:
                continue
            try:
                if not other.getMeta("observed"):
                    other.setCurrentDate(dates[index])
            except (AttributeError, KeyError):
                pass
        win.spc_widget.updateProfs()
        slider.blockSignals(True)
        slider.setValue(index)
        slider.blockSignals(False)
        hours = None
        try:
            hours = collection.getMeta("timeline_hours")
        except (AttributeError, KeyError):
            pass
        fxx = ""
        if isinstance(hours, (list, tuple)) and index < len(hours):
            fxx = f"F{int(hours[index]):03d}  •  "
        label.setText(f"{fxx}{dates[index]:%Y-%m-%d %H:%MZ}")

    def move(delta):
        dates = _collection_dates(collection)
        index = current_index() + int(delta)
        if index >= len(dates):
            if loop.isChecked():
                index = 0
            else:
                play.setChecked(False)
                timer.stop()
                index = len(dates) - 1
        elif index < 0:
            index = len(dates) - 1 if loop.isChecked() else 0
        set_index(index)

    previous.triggered.connect(lambda _checked=False: move(-1))
    following.triggered.connect(lambda _checked=False: move(1))
    slider.valueChanged.connect(set_index)
    timer.timeout.connect(lambda: move(1))

    def toggle_play(enabled):
        play.setText("Pause" if enabled else "Play")
        if enabled:
            timer.start()
        else:
            timer.stop()

    play.toggled.connect(toggle_play)
    win.destroyed.connect(lambda *_args: timer.stop())
    win.addToolBar(Qt.TopToolBarArea, toolbar)
    set_index(current_index())

    win._sharpmod_timeline_toolbar = toolbar
    win._sharpmod_timeline_timer = timer
    win._sharpmod_timeline_slider = slider
    win._sharpmod_timeline_play_action = play
    win._sharpmod_timeline_collection = collection
    win._sharpmod_timeline_set_index = set_index
    return toolbar


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
