"""Exact-frame animation preview and export UI for loaded soundings."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

from qtpy.QtCore import QByteArray, QBuffer, QIODevice, QThread, Qt, QTimer, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.analysis.animation_exports import (
    AnimationFrame,
    ExportCancelled,
    animation_frame_preview_png,
    prepare_animation_frames,
    write_animation_gif,
)
from sharpmod.ui.analysis_controls import FORM_WIDTH, bounded_form, inset_page
from sharpmod.export_paths import ExportDirectoryError
from sharpmod.ui.features.export_presentation import (
    ExportPresentation,
    available_export_path,
    export_filename,
    export_identity,
    load_presentation,
    save_presentation,
)
from sharpmod.ui.features.gui_export import (
    ExportCompletionPanel,
    ExportSizeControls,
    resolved_export_theme,
)
from sharpmod.ui.features.gui_jobs import JobCounts, JobStatus
from sharpmod.ui.styles.theme import (
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value is None else value


def _current_time(collection):
    dates = tuple(getattr(collection, "_dates", ()) or ())
    try:
        return dates[int(getattr(collection, "_prof_idx", 0))]
    except (IndexError, TypeError, ValueError):
        return None


def _utc_text(value):
    if not isinstance(value, datetime):
        return "Unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def pixmap_png(pixmap) -> bytes:
    """Encode a GUI-thread pixmap without touching a filesystem."""
    if pixmap is None or pixmap.isNull():
        raise RuntimeError("widget produced an empty image")
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.WriteOnly) or not pixmap.save(buffer, "PNG"):
        raise RuntimeError("widget image could not be encoded as PNG")
    buffer.close()
    return bytes(data)


@dataclass(frozen=True)
class _ExportRequest:
    token: int
    destination: str
    frames: tuple[AnimationFrame, ...]
    mode: str
    duration: int
    output_size: tuple[int, int] | None = None
    gap_policy: str = "cards"
    expected_interval_seconds: float | None = None
    theme: str = "dark"
    context: str = ""


class _ExportWorker(QThread):
    completed = Signal(int, str)
    cancelled = Signal(int)
    failed = Signal(int, str)
    progress = Signal(int, int, int)
    stage = Signal(int, str)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request
        self.candidate = None

    def _new_candidate(self):
        destination = Path(self.request.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=f".ready{destination.suffix}",
            dir=destination.parent,
        )
        os.close(handle)
        self.candidate = Path(name)
        return self.candidate

    def discard_candidate(self):
        if self.candidate is not None:
            self.candidate.unlink(missing_ok=True)

    def run(self):
        request = self.request
        try:
            if self.isInterruptionRequested():
                raise ExportCancelled("export cancelled before rendering")
            candidate = self._new_candidate()
            self.stage.emit(request.token, "Encoding fixed-scale frame images")
            write_animation_gif(
                candidate,
                request.frames,
                mode=request.mode,
                frame_duration_ms=request.duration,
                output_size=request.output_size,
                gap_policy=request.gap_policy,
                expected_interval_seconds=request.expected_interval_seconds,
                theme=request.theme,
                context=request.context,
                cancelled=self.isInterruptionRequested,
                progress=lambda done, total: self.progress.emit(
                    request.token, done, total
                ),
            )
            if self.isInterruptionRequested():
                raise ExportCancelled("export cancelled before publication")
        except ExportCancelled:
            self.discard_candidate()
            self.cancelled.emit(request.token)
            return
        except Exception as exc:  # noqa: BLE001 - export boundary
            self.discard_candidate()
            self.failed.emit(request.token, str(exc))
            return
        self.completed.emit(request.token, str(candidate))


from sharpmod.ui.animation_export import AnimationExportMixin


class AnimationWorkspace(AnimationExportMixin, QWidget):
    """Preview and export exact viewer frames into chosen-size GIF animations."""

    def __init__(
        self,
        host,
        parent=None,
        *,
        request_sounding: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("analysisAnimationWorkspace")
        self.host = host
        self._request_sounding = request_sounding
        self._frame_specs = []
        self._worker = None
        self._capturing = False
        self._token = 0
        self._active_request = None
        self._last_request = None
        self._cancel_requested = False
        self._last_artifact = None
        self._presentation = load_presentation(
            self._settings(), "sounding-animation",
            default=ExportPresentation(1920, 1080, "full-hd", True, "current"),
        )
        self._build_ui()

    def _settings(self):
        candidate = getattr(self.host, "_settings", None)
        if callable(candidate):
            try:
                return candidate()
            except Exception:
                return None
        win = self.host._window()
        return getattr(win, "_settings", None) if win is not None else None

    def _build_ui(self):
        outer = QVBoxLayout(self)
        inset_page(outer)
        intro = QLabel(
            "Build a GIF from exact forecast times or successive runs. Every frame "
            "uses the chosen output pixels, and time gaps follow the policy below."
        )
        intro.setObjectName(OBJ_HINT)
        intro.setWordWrap(True)
        intro.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        outer.addWidget(intro)

        controls = QGridLayout()
        controls.addWidget(QLabel("Animation"), 0, 0)
        self.mode = QComboBox()
        self.mode.addItem("Forecast timeline", "forecast-timeline")
        self.mode.addItem("Successive runs at focused valid time", "run-to-run")
        self.mode.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mode.setMinimumContentsLength(1)
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self.mode, 0, 1, 1, 2)
        controls.addWidget(QLabel("Frame duration"), 1, 0)
        self.duration = QSpinBox()
        self.duration.setRange(100, 10000)
        self.duration.setValue(750)
        self.duration.setSingleStep(100)
        self.duration.setSuffix(" ms")
        self.duration.setMaximumWidth(160)
        controls.addWidget(self.duration, 1, 1)
        self.gap_policy = QComboBox(self)
        self.gap_policy.addItem("Show missing/time-gap cards (default)", "cards")
        self.gap_policy.addItem("Stop before the first gap", "stop")
        self.gap_policy.addItem("Fail if any gap exists", "error")
        self.gap_policy.setMinimumWidth(0)
        self.gap_policy.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        controls.addWidget(QLabel("Missing times"), 2, 0)
        self.gap_policy.setToolTip(
            "How to handle missing or nonconsecutive forecast times"
        )
        controls.addWidget(self.gap_policy, 2, 1, 1, 2)
        self.gif_button = QPushButton("Export GIF…")
        self.gif_button.setObjectName("animationExportGif")
        self.gif_button.setMinimumWidth(0)
        self.gif_button.setMaximumWidth(180)
        self.gif_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        controls.setColumnStretch(1, 3)
        outer.addWidget(bounded_form(self, controls))

        self.sizes = ExportSizeControls(self._presentation, caption=False, parent=self)
        self.sizes.setMinimumWidth(0)
        self.sizes.setMaximumWidth(FORM_WIDTH)
        self.sizes.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.size_details = QToolButton(self)
        self.size_details.setObjectName("animationSizeDetails")
        self.size_details.setText("Output size…")
        self.size_details.setToolTip("Configure output size, aspect, and frame theme")
        self.size_details.setAccessibleName("Output size and frame theme")
        self.size_details.setCheckable(True)
        self.size_details.setChecked(False)
        self.size_details.setMinimumWidth(0)
        self.size_details.setMaximumWidth(160)
        self.size_details.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.size_details.toggled.connect(self.sizes.setVisible)
        export_row = QHBoxLayout()
        export_row.addWidget(self.size_details)
        export_row.addWidget(self.gif_button)
        export_row.addStretch(1)
        outer.addLayout(export_row)
        outer.addWidget(self.sizes)
        self.sizes.hide()

        range_row = QGridLayout()
        self.range_start = QSpinBox(self)
        self.range_start.setAccessibleName("First frame in export range")
        self.range_end = QSpinBox(self)
        self.range_end.setAccessibleName("Last frame in export range")
        range_row.addWidget(QLabel("Frame range (inclusive)"), 0, 0, 1, 2)
        range_row.addWidget(self.range_start, 1, 0)
        range_row.addWidget(self.range_end, 1, 1)
        self.frame_count = QLabel("0 output frames", self)
        self.frame_count.setObjectName(OBJ_HINT)
        self.frame_count.setWordWrap(True)
        range_row.addWidget(self.frame_count, 2, 0, 1, 2)
        outer.addWidget(bounded_form(self, range_row))

        self.frames = self._table("animationFrames")
        self.frames.setColumnCount(6)
        self.frames.setHorizontalHeaderLabels(
            ("Include", "Label", "Run", "Valid", "Lead", "Availability")
        )
        outer.addWidget(self.frames, 1)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("Preview frame"))
        self.preview_index = QSpinBox(self)
        self.preview_index.setAccessibleName("Output frame to preview")
        self.preview_index.setMaximumWidth(110)
        preview_row.addWidget(self.preview_index)
        self.preview_button = QPushButton("Refresh preview", self)
        self.preview_button.setMaximumWidth(160)
        preview_row.addWidget(self.preview_button)
        preview_row.addStretch(1)
        outer.addLayout(preview_row)
        self.preview = QLabel("Select a frame to preview its output composition.", self)
        self.preview.setObjectName("animationFramePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(160)
        self.preview.setWordWrap(True)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        outer.addWidget(self.preview)
        self.preview_note = QLabel("", self)
        self.preview_note.setObjectName(OBJ_HINT)
        self.preview_note.setWordWrap(True)
        outer.addWidget(self.preview_note)

        self.job = JobStatus(self)
        self.job.cancelRequested.connect(self._cancel)
        self.job.retryRequested.connect(self._retry_export)
        outer.addWidget(self.job)
        self.cancel_button = self.job.cancel_button

        self.completion = ExportCompletionPanel(self._settings, self)
        outer.addWidget(self.completion)

        self.status = QLabel("Select the exact frames to animate.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        self.load_sounding = QPushButton("Load sounding…")
        self.load_sounding.setObjectName(OBJ_GHOST)
        self.load_sounding.setToolTip(
            "Return to the existing picker to add a sounding to this animation"
        )
        status_row.addWidget(self.load_sounding)
        outer.addLayout(status_row)

        self.mode.currentIndexChanged.connect(self._refresh_frames)
        self.gif_button.clicked.connect(self._export_gif)
        self.load_sounding.clicked.connect(self._load_required_sounding)
        self.frames.itemChanged.connect(self._selection_changed)
        self.range_start.valueChanged.connect(self._range_changed)
        self.range_end.valueChanged.connect(self._range_changed)
        self.preview_index.valueChanged.connect(self._schedule_preview)
        self.preview_button.clicked.connect(self._preview_frame)
        self.gap_policy.currentIndexChanged.connect(self._choices_changed)
        self.duration.valueChanged.connect(self._save_preferences)
        self.sizes.changed.connect(self._choices_changed)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._preview_frame)
        settings = self._settings()
        if settings is not None:
            try:
                duration = int(settings.value("exports/animation/duration_ms", 750))
                index = self.gap_policy.findData(
                    str(settings.value("exports/animation/gap_policy", "cards"))
                )
                self.duration.blockSignals(True)
                self.gap_policy.blockSignals(True)
                self.duration.setValue(duration)
                self.gap_policy.setCurrentIndex(max(0, index))
                self.duration.blockSignals(False)
                self.gap_policy.blockSignals(False)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                self.duration.blockSignals(False)
                self.gap_policy.blockSignals(False)
                pass

    @staticmethod
    def _table(name):
        table = QTableWidget()
        table.setObjectName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _check_item(checked=True, data=None):
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        if data is not None:
            item.setData(Qt.UserRole, data)
        return item

    def refresh(self):
        collections = self.host._collections()
        self._refresh_frames()
        enabled = bool(collections)
        self.load_sounding.setHidden(enabled)
        self.load_sounding.setEnabled(
            not enabled and callable(self._request_sounding)
        )
        if self._worker is not None or self._capturing:
            return
        if enabled:
            self._set_status(
                f"{len(collections)} loaded sounding(s) are available. Select the "
                "exact frames to animate."
            )
        else:
            self._set_status(
                "No soundings are loaded. Load at least one sounding before exporting "
                "an animation.",
                "warn",
            )

    def _load_required_sounding(self):
        if callable(self._request_sounding):
            self._request_sounding()
            self._set_status(
                "The existing sounding picker is ready. Load a sounding, then return "
                "to Animation to choose its frames."
            )

    def _suggested(self, filename):
        try:
            return available_export_path(filename, settings=self._settings())
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _save_preferences(self, *_args):
        settings = self._settings()
        if settings is None:
            return
        save_presentation(settings, "sounding-animation", self.sizes.presentation())
        try:
            settings.setValue("exports/animation/duration_ms", self.duration.value())
            settings.setValue("exports/animation/gap_policy", self.gap_policy.currentData())
            settings.sync()
        except (AttributeError, OSError, RuntimeError, TypeError):
            pass

    def _choices_changed(self, *_args):
        self._save_preferences()
        self._update_export_controls()

    def _selection_changed(self, *_args):
        self._update_export_controls()

    def _range_changed(self, *_args):
        if self.range_start.value() > self.range_end.value():
            changed = self.sender()
            other = self.range_end if changed is self.range_start else self.range_start
            blocked = other.blockSignals(True)
            other.setValue(changed.value())
            other.blockSignals(blocked)
        self._update_export_controls()

    def _expected_step(self):
        key = "valid" if self.mode.currentData() == "forecast-timeline" else "run"
        moments = [spec[key] for spec in self._frame_specs if isinstance(spec[key], datetime)]
        seconds = sorted(
            (later - earlier).total_seconds()
            for earlier, later in zip(moments, moments[1:])
            if (later - earlier).total_seconds() > 0
        )
        return seconds[0] if seconds else None

    def _frame_context(self, specs):
        collections = self.host._collections()
        if not specs or not collections:
            return ""
        locations = dict.fromkeys(
            str(_meta(collections[item["collection"]], "loc", "Sounding"))
            for item in specs
        )
        sources = dict.fromkeys(
            export_identity(collections[item["collection"]]).source
            for item in specs
        )
        return " · ".join(
            item for item in (
                "/".join(locations),
                "/".join(name for name in sources if name),
            ) if item
        )

    def _planned_entries(self):
        specs = self._selected_specs()
        if not specs:
            return (), "Select at least one frame in the chosen range."
        frames = tuple(
            AnimationFrame(
                str(spec["label"]),
                b"\x89PNG\r\n\x1a\n" if spec["missing"] is None else None,
                spec["valid"], spec["run"], spec["lead"], spec["missing"],
            )
            for spec in specs
        )
        try:
            prepared = prepare_animation_frames(
                frames,
                mode=self.mode.currentData(),
                gap_policy=self.gap_policy.currentData(),
                expected_interval_seconds=self._expected_step(),
            )
        except Exception as exc:  # noqa: BLE001 - planning UI boundary
            return (), str(exc)
        sources = {id(frame): spec for frame, spec in zip(frames, specs)}
        return tuple((frame, sources.get(id(frame))) for frame in prepared), ""

    def _schedule_preview(self, *_args):
        if not self._capturing and self._worker is None and self.isVisible():
            self._preview_timer.start(240)

    def _preview_frame(self, *_args):
        if self._capturing or self._worker is not None:
            return
        entries, error = self._planned_entries()
        if error:
            self.preview.clear()
            self.preview.setText("No output preview is available for the current selection.")
            self.preview_note.setText(error)
            return
        index = max(0, min(self.preview_index.value() - 1, len(entries) - 1))
        frame, spec = entries[index]
        presentation = self.sizes.presentation()
        theme = resolved_export_theme(presentation.theme)
        if spec is not None and spec["missing"] is None:
            self._capturing = True
            try:
                frame = self.capture_animation_frames(
                    specs=(spec,), output_size=presentation.size, theme=theme
                )[0]
            except Exception as exc:  # noqa: BLE001 - preview boundary
                self.preview.clear()
                self.preview.setText("Frame preview failed; the viewer was restored.")
                self.preview_note.setText(f"Could not capture {spec['label']}: {exc}")
                return
            finally:
                self._capturing = False
        try:
            data = animation_frame_preview_png(
                frame,
                mode=self.mode.currentData(),
                output_size=presentation.size,
                theme=theme,
                context=self._frame_context(self._selected_specs()),
            )
            pixmap = QPixmap()
            if not pixmap.loadFromData(data, "PNG"):
                raise ValueError("preview PNG could not be decoded")
        except Exception as exc:  # noqa: BLE001 - preview boundary
            self.preview.clear()
            self.preview.setText("Frame preview is unavailable.")
            self.preview_note.setText(str(exc))
            return
        self.preview.setPixmap(
            pixmap.scaled(
                max(320, self.preview.width()), 190,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
        )
        self.preview_note.setText(
            f"Frame {index + 1}/{len(entries)} · {_utc_text(frame.valid_time)} · "
            f"{presentation.width} × {presentation.height} px · {theme} frame · "
            f"{self.duration.value()} ms/frame"
            + (f" · gap: {frame.missing_reason}" if frame.missing_reason else "")
        )

    def _refresh_frames(self, *_args):
        collections = self.host._collections()
        specs = []
        mode = self.mode.currentData()
        if collections:
            focused = min(self.host._focused_index(), len(collections) - 1)
            reference = collections[focused]
            if mode == "forecast-timeline":
                run = _meta(reference, "run")
                profiles = getattr(reference, "_profs", {}) or {}
                member = getattr(reference, "_highlight", None)
                if member not in profiles:
                    member = next(iter(profiles), None)
                sequence = profiles.get(member, ()) if member is not None else ()
                for index, valid in enumerate(
                    tuple(getattr(reference, "_dates", ()) or ())
                ):
                    available = index < len(sequence) and sequence[index] is not None
                    lead = (
                        round((valid - run).total_seconds() / 3600.0)
                        if isinstance(valid, datetime) and isinstance(run, datetime)
                        else None
                    )
                    specs.append(
                        {
                            "collection": focused,
                            "profile_index": index,
                            "label": (
                                f"F{lead:03d}" if lead is not None else _utc_text(valid)
                            ),
                            "run": run,
                            "valid": valid,
                            "lead": lead,
                            "missing": (
                                None
                                if available
                                else "profile is absent at this exact time"
                            ),
                        }
                    )
            else:
                wanted = _current_time(reference)
                for collection_index, collection in enumerate(collections):
                    dates = tuple(getattr(collection, "_dates", ()) or ())
                    try:
                        profile_index = dates.index(wanted)
                    except ValueError:
                        profile_index = None
                    profiles = getattr(collection, "_profs", {}) or {}
                    member = getattr(collection, "_highlight", None)
                    if member not in profiles:
                        member = next(iter(profiles), None)
                    sequence = profiles.get(member, ()) if member is not None else ()
                    available = (
                        profile_index is not None
                        and profile_index < len(sequence)
                        and sequence[profile_index] is not None
                    )
                    run = _meta(collection, "run")
                    lead = (
                        round((wanted - run).total_seconds() / 3600.0)
                        if isinstance(wanted, datetime) and isinstance(run, datetime)
                        else _meta(collection, "fxx")
                    )
                    specs.append(
                        {
                            "collection": collection_index,
                            "profile_index": profile_index,
                            "label": str(
                                _meta(
                                    collection,
                                    "model",
                                    f"Run {collection_index + 1}",
                                )
                            ),
                            "run": run,
                            "valid": wanted,
                            "lead": lead,
                            "missing": (
                                None
                                if available
                                else "no profile at the focused exact valid time"
                            ),
                        }
                    )
                specs.sort(
                    key=lambda item: (
                        item["run"] is None,
                        item["run"] or datetime.max.replace(tzinfo=timezone.utc),
                    )
                )
        self._frame_specs = specs
        blocked = self.frames.blockSignals(True)
        self.frames.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            self.frames.setItem(row, 0, self._check_item(True, row))
            values = (
                spec["label"],
                _utc_text(spec["run"]),
                _utc_text(spec["valid"]),
                (
                    f"F{int(spec['lead']):03d}"
                    if spec["lead"] is not None
                    else "Unknown"
                ),
                spec["missing"] or "Exact profile available",
            )
            for column, value in enumerate(values, start=1):
                self.frames.setItem(row, column, QTableWidgetItem(value))
        self.frames.blockSignals(blocked)
        for control in (self.range_start, self.range_end):
            control.blockSignals(True)
            control.setRange(1 if specs else 0, len(specs))
        self.range_start.setValue(1 if specs else 0)
        self.range_end.setValue(len(specs))
        self.range_start.blockSignals(False)
        self.range_end.blockSignals(False)
        self._update_export_controls()

    def _selected_specs(self):
        return tuple(
            self._frame_specs[row]
            for row in range(self.frames.rowCount())
            if self.range_start.value() - 1 <= row < self.range_end.value()
            if self.frames.item(row, 0) is not None
            and self.frames.item(row, 0).checkState() == Qt.Checked
        )

    def capture_animation_frames(self, *, specs=None, output_size=None, theme="current"):
        specs = tuple(self._selected_specs() if specs is None else specs)
        if not specs:
            raise ValueError("select at least one animation frame")
        win = self.host._window()
        sw = getattr(win, "spc_widget", None)
        collections = self.host._collections()
        ids = tuple(getattr(sw, "prof_ids", ()) or ())
        old_active = int(getattr(sw, "pc_idx", 0) or 0)
        old_indices = [
            int(getattr(item, "_prof_idx", 0) or 0) for item in collections
        ]
        frames = []
        try:
            for spec in specs:
                missing = spec["missing"]
                png = None
                if missing is None:
                    collection_index = spec["collection"]
                    collection = collections[collection_index]
                    try:
                        collection.setCurrentDate(spec["valid"])
                        sw.setProfileCollection(ids[collection_index])
                        sw.updateProfs()
                        QApplication.processEvents()
                        if output_size is None:
                            pixmap = sw.grab()
                        else:
                            from sharpmod.rendering.cli import compose_widget_pixmap

                            caption_height = max(
                                38, min(64, int(round(output_size[1] * 0.065)))
                            )
                            pixmap = compose_widget_pixmap(
                                sw, output_size[0], output_size[1] - caption_height,
                                theme=theme,
                            )
                        png = pixmap_png(pixmap)
                    except Exception as exc:  # noqa: BLE001 - capture boundary
                        missing = f"viewer capture failed: {exc}"
                frames.append(
                    AnimationFrame(
                        str(spec["label"]),
                        png,
                        spec["valid"],
                        spec["run"],
                        spec["lead"],
                        missing,
                    )
                )
        finally:
            for collection, index in zip(collections, old_indices):
                dates = tuple(getattr(collection, "_dates", ()) or ())
                if 0 <= index < len(dates):
                    try:
                        collection.setCurrentDate(dates[index])
                    except Exception:
                        collection._prof_idx = index
            if 0 <= old_active < len(ids):
                sw.setProfileCollection(ids[old_active])
                sw.updateProfs()
                QApplication.processEvents()
        return tuple(frames)














    def session_state(self):
        return {
            "mode": self.mode.currentData(),
            "duration_ms": self.duration.value(),
            "gap_policy": self.gap_policy.currentData(),
            "presentation": {
                "width": self.sizes.presentation().width,
                "height": self.sizes.presentation().height,
                "preset": self.sizes.presentation().preset,
                "theme": self.sizes.presentation().theme,
                "lock_aspect": self.sizes.presentation().lock_aspect,
            },
            "range": [self.range_start.value(), self.range_end.value()],
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        mode = self.mode.findData(state.get("mode"))
        if mode >= 0:
            self.mode.setCurrentIndex(mode)
        try:
            self.duration.setValue(int(state.get("duration_ms", 750)))
        except (TypeError, ValueError):
            pass
        index = self.gap_policy.findData(state.get("gap_policy"))
        if index >= 0:
            self.gap_policy.setCurrentIndex(index)
        raw = state.get("presentation")
        if isinstance(raw, Mapping):
            try:
                self.sizes.set_presentation(
                    ExportPresentation(
                        raw.get("width", self._presentation.width),
                        raw.get("height", self._presentation.height),
                        raw.get("preset", "custom"),
                        True, raw.get("theme", "current"),
                        raw.get("lock_aspect", True),
                    )
                )
            except (TypeError, ValueError):
                pass
        range_values = state.get("range")
        if isinstance(range_values, (list, tuple)) and len(range_values) == 2:
            try:
                self.range_start.setValue(int(range_values[0]))
                self.range_end.setValue(int(range_values[1]))
            except (TypeError, ValueError):
                pass
        self._update_export_controls()

    def _set_status(self, text, level="info"):
        role = {
            "info": OBJ_STATUS,
            "warn": OBJ_WARNING_TEXT,
            "error": OBJ_ERROR_TEXT,
        }.get(level, OBJ_STATUS)
        self.status.setObjectName(role)
        self.status.setText(str(text))
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def shutdown(self):
        self._token += 1
        self._preview_timer.stop()
        self.job.invalidate(
            "This animation workspace closed; late export callbacks are ignored."
        )
        self._active_request = None
        self._last_request = None
        worker = self._worker
        if worker is None:
            return
        worker.requestInterruption()
        worker.wait(2000)
        if worker.isRunning():
            from sharpmod.ui.features.gui_threading import retain_worker_until_finished

            retain_worker_until_finished(worker)
        else:
            worker.discard_candidate()
        self._worker = None


__all__ = ["AnimationWorkspace", "pixmap_png"]
