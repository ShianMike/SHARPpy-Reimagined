"""Forecast-hour playback controls for a multi-time SHARPpy collection."""

from __future__ import annotations

import os

from qtpy.QtCore import Qt, QThread, QTimer, Signal
from qtpy.QtGui import QAction
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
)

from sharpmod.profile_timeline import forecast_hour_range


MAX_TIMELINE_HOURS = 72


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

    def _update_summary(self, *_args):
        try:
            hours = self.hours()
        except ValueError as exc:
            self.summary.setText(str(exc))
            self.summary.setStyleSheet("color: #ff9a9a;")
            return
        self.summary.setStyleSheet("color: #aeb8c8;")
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
    """Run a bounded batch and stream each completed hour back to Qt."""

    item_ready = Signal(str, int)
    item_failed = Signal(int, str)
    progress = Signal(int, str, int, int)
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, model, lat, lon, run_time, hours, output_dir, *,
                 loc=None, member=None, disk_cache=None, parent=None):
        super().__init__(parent)
        self.model = str(model)
        self.lat = float(lat)
        self.lon = float(lon)
        self.run_time = run_time
        self.hours = tuple(int(hour) for hour in hours)
        self.output_dir = os.fspath(output_dir)
        self.loc = str(loc) if loc else None
        self.member = str(member) if member else None
        self.disk_cache = disk_cache
        self._extractor = None
        self._completed = 0

    def requestInterruption(self):  # noqa: N802 - Qt API override
        super().requestInterruption()
        if self._extractor is not None:
            self._extractor.cancel()

    def run(self):
        from sharpmod.batch_extract import BatchExtractor, BatchRequest
        from sharpmod.model_hour_cache import ModelHourCache

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
        cache = None

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
            if self.disk_cache is not None:
                cache = ModelHourCache(
                    max_entries=min(2, max(1, len(self.hours))),
                    directory_factory=self.disk_cache.directory_for,
                    directory_protector=self.disk_cache.protect,
                    metadata_writer=self.disk_cache.annotate,
                    delete_download_dirs=False,
                )
            self._extractor = BatchExtractor(progress_callback=on_progress)
            result = self._extractor.run(
                requests,
                output_dir=self.output_dir,
                max_workers=min(2, max(1, len(self.hours))),
                resume=True,
                cancelled=self.isInterruptionRequested,
                model_hour_cache=cache,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(f"Forecast timeline failed: {exc}")
            return
        finally:
            self._extractor = None
            if cache is not None:
                cache.clear()
        self.result_ready.emit(result)


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

    def current_index():
        dates = _collection_dates(collection)
        try:
            return dates.index(collection.getCurrentDate())
        except (AttributeError, ValueError):
            return 0

    def set_index(index):
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
    "ForecastTimelineDialog", "ModelTimelineWorker",
    "MAX_TIMELINE_HOURS",
    "install_timeline_controls", "refresh_timeline_controls",
]
