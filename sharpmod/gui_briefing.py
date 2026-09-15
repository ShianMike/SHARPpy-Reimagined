"""Briefing and fixed-scale animation export UI for loaded soundings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math

from qtpy.QtCore import QByteArray, QBuffer, QIODevice, QThread, Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.briefing_exports import (
    AnimationFrame,
    BriefingDocument,
    BriefingImage,
    BriefingSounding,
    write_animation_gif,
    write_briefing_html,
    write_briefing_pdf,
)
from sharpmod.ensemble_members import EnsembleAcquisition
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.theme import (
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


def _collection_label(collection, index):
    model = _meta(collection, "model", "")
    loc = _meta(collection, "loc", "")
    valid = _current_time(collection)
    label = " · ".join(value for value in (str(model or ""), str(loc or "")) if value)
    return f"{label or f'Sounding {index + 1}'} · {_utc_text(valid)}"


def _point(collection, prefix):
    latitude = _meta(collection, f"{prefix}_lat")
    longitude = _meta(collection, f"{prefix}_lon")
    if latitude is None and prefix == "selected":
        latitude = _meta(collection, "lat")
        longitude = _meta(collection, "lon")
    try:
        return f"{float(latitude):.4f}, {float(longitude):.4f}"
    except (TypeError, ValueError):
        return "Unknown"


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


def table_rows(table):
    """Return exactly the values currently rendered by a QTableWidget."""
    if table is None:
        return ()
    headers = []
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        headers.append(item.text() if item is not None else f"Column {column + 1}")
    rows = []
    for row in range(table.rowCount()):
        values = {}
        for column, header in enumerate(headers):
            item = table.item(row, column)
            values[header] = item.text() if item is not None else ""
        rows.append(values)
    return tuple(rows)


class _GifWorker(QThread):
    completed = Signal(int, str)
    failed = Signal(int, str)
    progress = Signal(int, int)

    def __init__(self, token, destination, frames, mode, duration, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.destination = str(destination)
        self.frames = tuple(frames)
        self.mode = str(mode)
        self.duration = int(duration)

    def run(self):
        try:
            saved = write_animation_gif(
                self.destination,
                self.frames,
                mode=self.mode,
                frame_duration_ms=self.duration,
                cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
            )
        except Exception as exc:  # noqa: BLE001 - export boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, str(saved))


class BriefingWorkspace(QWidget):
    """Capture actual viewer state into self-contained briefing artifacts."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisBriefingWorkspace")
        self.host = host
        self._checked = {}
        self._frame_specs = []
        self._worker = None
        self._token = 0
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        intro = QLabel(
            "Briefings embed the actual selected sounding/map pixels and the values "
            "already displayed in Compare and Trends. GIFs use one fixed viewer size "
            "and preserve explicit missing frames."
        )
        intro.setObjectName(OBJ_HINT)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Briefing title"))
        self.title = QLineEdit("SHARPpy Reimagined briefing")
        self.title.setObjectName("briefingTitle")
        title_row.addWidget(self.title, 1)
        self.html_button = QPushButton("Export HTML…")
        self.pdf_button = QPushButton("Export PDF…")
        for button in (self.html_button, self.pdf_button):
            button.setObjectName(OBJ_GHOST)
            title_row.addWidget(button)
        outer.addLayout(title_row)

        self.soundings = self._table("briefingSoundings")
        self.soundings.setColumnCount(5)
        self.soundings.setHorizontalHeaderLabels(
            ("Include", "Sounding", "Run", "Valid", "Context")
        )

        animation = QWidget()
        animation_layout = QVBoxLayout(animation)
        animation_layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        animation_layout.setSpacing(SPACE["sm"])
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Animation"))
        self.mode = QComboBox()
        self.mode.addItem("Forecast timeline", "forecast-timeline")
        self.mode.addItem("Successive runs at focused valid time", "run-to-run")
        controls.addWidget(self.mode, 1)
        controls.addWidget(QLabel("Frame duration"))
        self.duration = QSpinBox()
        self.duration.setRange(100, 10000)
        self.duration.setValue(750)
        self.duration.setSingleStep(100)
        self.duration.setSuffix(" ms")
        controls.addWidget(self.duration)
        self.gif_button = QPushButton("Export GIF…")
        self.gif_button.setObjectName("briefingExportGif")
        controls.addWidget(self.gif_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName(OBJ_GHOST)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.cancel_button)
        animation_layout.addLayout(controls)
        self.frames = self._table("briefingAnimationFrames")
        self.frames.setColumnCount(6)
        self.frames.setHorizontalHeaderLabels(
            ("Include", "Label", "Run", "Valid", "Lead", "Availability")
        )
        animation_layout.addWidget(self.frames, 1)

        split = QSplitter(Qt.Vertical)
        split.setObjectName("briefingSplitter")
        split.setChildrenCollapsible(False)
        split.addWidget(self.soundings)
        split.addWidget(animation)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        self.splitter = split

        self.status = QLabel("Select the soundings and frames to share.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.html_button.clicked.connect(lambda: self._export_briefing("html"))
        self.pdf_button.clicked.connect(lambda: self._export_briefing("pdf"))
        self.mode.currentIndexChanged.connect(self._refresh_frames)
        self.gif_button.clicked.connect(self._export_gif)
        self.cancel_button.clicked.connect(self._cancel)

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
        previous = {
            self.soundings.item(row, 0).data(Qt.UserRole): self.soundings.item(row, 0).checkState() == Qt.Checked
            for row in range(self.soundings.rowCount())
            if self.soundings.item(row, 0) is not None
        }
        self.soundings.setRowCount(len(collections))
        for index, collection in enumerate(collections):
            checked = previous.get(index, True)
            self.soundings.setItem(index, 0, self._check_item(checked, index))
            ledger = EnsembleAcquisition.from_collection(collection)
            context = ""
            if ledger.requested_members:
                context = f"{len(ledger.loaded_members)}/{len(ledger.requested_members)} loaded"
            if _meta(collection, "scenario_hypothetical", False):
                context = (context + "; " if context else "") + "hypothetical"
            values = (
                _collection_label(collection, index),
                _utc_text(_meta(collection, "run")),
                _utc_text(_current_time(collection)),
                context or "ordinary sounding",
            )
            for column, text in enumerate(values, start=1):
                self.soundings.setItem(index, column, QTableWidgetItem(text))
        self._refresh_frames()
        enabled = bool(collections)
        self.html_button.setEnabled(enabled)
        self.pdf_button.setEnabled(enabled)

    def _selected_soundings(self):
        return tuple(
            int(item.data(Qt.UserRole))
            for row in range(self.soundings.rowCount())
            if (item := self.soundings.item(row, 0)) is not None
            and item.checkState() == Qt.Checked
        )

    def _environment_controller(self):
        win = self.host._window()
        try:
            controller = getattr(win, "_sharpmod_controller", None) or win.parent()
        except (AttributeError, RuntimeError):
            return None
        getter = getattr(controller, "active_environment_context", None)
        return getter() if callable(getter) else None

    def _capture_sounding(self, index):
        win = self.host._window()
        sw = getattr(win, "spc_widget", None)
        collections = self.host._collections()
        collection = collections[index]
        ids = tuple(getattr(sw, "prof_ids", ()) or ())
        if index >= len(ids):
            raise RuntimeError("viewer has no profile identifier for this sounding")
        sw.setProfileCollection(ids[index])
        sw.updateProfs()
        QApplication.processEvents()
        return BriefingImage(
            _collection_label(collection, index),
            pixmap_png(sw.grab()),
            "Exact selected member and valid time",
            str(_meta(collection, "source_provider", _meta(collection, "model", "Unknown"))),
        )

    def build_document(self):
        indexes = self._selected_soundings()
        if not indexes:
            raise ValueError("select at least one sounding for the briefing")
        win = self.host._window()
        sw = getattr(win, "spc_widget", None)
        collections = self.host._collections()
        old_index = int(getattr(sw, "pc_idx", 0) or 0)
        soundings = []
        limitations = []
        try:
            for index in indexes:
                collection = collections[index]
                try:
                    image = self._capture_sounding(index)
                except Exception as exc:  # noqa: BLE001 - capture boundary
                    image = None
                    limitations.append(
                        f"{_collection_label(collection, index)} image unavailable: {exc}"
                    )
                ledger = EnsembleAcquisition.from_collection(collection)
                coverage = None
                if ledger.requested_members:
                    coverage = (
                        f"{len(ledger.loaded_members)}/{len(ledger.requested_members)} loaded"
                    )
                soundings.append(
                    BriefingSounding(
                        _collection_label(collection, index),
                        str(_meta(collection, "source_provider", _meta(collection, "model", "Unknown"))),
                        _meta(collection, "run"),
                        _current_time(collection),
                        _point(collection, "requested"),
                        _point(collection, "selected"),
                        (
                            str(_meta(collection, "scenario_name"))
                            if _meta(collection, "scenario_hypothetical", False)
                            else None
                        ),
                        coverage,
                        image,
                    )
                )
        finally:
            ids = tuple(getattr(sw, "prof_ids", ()) or ())
            if 0 <= old_index < len(ids):
                sw.setProfileCollection(ids[old_index])
                sw.updateProfs()
                QApplication.processEvents()

        map_images = []
        context_rows = []
        environmental = self._environment_controller()
        if environmental is not None:
            try:
                context_rows = tuple(environmental.context_rows())
                map_widget = getattr(environmental, "_map", None)
                if map_widget is not None and context_rows:
                    attributions = []
                    satellite = environmental.satellite_overlay()
                    surface = environmental.surface_observations()
                    if satellite is not None:
                        attributions.append(satellite.raster.attribution)
                    if surface is not None:
                        attributions.append(surface.attribution)
                    map_images.append(
                        BriefingImage(
                            "Map context",
                            pixmap_png(map_widget.grab()),
                            "Current optional overlays and map navigation state",
                            "; ".join(value for value in attributions if value),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - optional map boundary
                limitations.append(f"Map context unavailable: {exc}")
        else:
            limitations.append("No optional GOES/surface map context was active.")

        comparison = table_rows(getattr(self.host, "compare_table", None))
        trend_rows = []
        for series in getattr(self.host, "_trend_series", ()) or ():
            for sample in series.samples:
                value = None
                values = getattr(sample, "values", {}) or {}
                metric = self.host.trend_metric.currentData()
                if isinstance(values, Mapping):
                    value = values.get(metric)
                trend_rows.append(
                    {
                        "Sounding": series.label,
                        "Valid": getattr(sample, "valid_time", None),
                        "Diagnostic": metric,
                        "Displayed value": value,
                        "Availability": (
                            "Available"
                            if getattr(sample, "available", False)
                            else getattr(sample, "reason", None) or "Unavailable"
                        ),
                    }
                )
        limitations.append(
            "Visual profile lines may be interpolated for drawing; scientific deltas "
            "remain those in the displayed comparison/verification tables."
        )
        return BriefingDocument(
            self.title.text().strip(),
            datetime.now(timezone.utc),
            tuple(soundings),
            notes=self.host.notes.toPlainText(),
            map_images=tuple(map_images),
            comparison_rows=comparison,
            trend_rows=tuple(trend_rows),
            context_rows=tuple(context_rows),
            limitations=tuple(limitations),
        )

    def _suggested(self, filename):
        try:
            return export_file_path(filename)
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _export_briefing(self, kind):
        extension = "html" if kind == "html" else "pdf"
        suggested = self._suggested(f"sounding-briefing.{extension}")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            f"Export {extension.upper()} briefing",
            str(suggested),
            f"{extension.upper()} files (*.{extension});;All files (*)",
        )
        if not path:
            return
        self._set_status("Capturing selected displayed soundings and context…")
        try:
            document = self.build_document()
            saved = (
                write_briefing_html(path, document)
                if kind == "html"
                else write_briefing_pdf(path, document)
            )
        except Exception as exc:  # noqa: BLE001 - user-facing export boundary
            self._set_status(f"Briefing export failed: {exc}", "error")
        else:
            self._set_status(f"Saved self-contained {extension.upper()} briefing: {saved}")

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
                for index, valid in enumerate(tuple(getattr(reference, "_dates", ()) or ())):
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
                            "label": f"F{lead:03d}" if lead is not None else _utc_text(valid),
                            "run": run,
                            "valid": valid,
                            "lead": lead,
                            "missing": None if available else "profile is absent at this exact time",
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
                            "label": str(_meta(collection, "model", f"Run {collection_index + 1}")),
                            "run": run,
                            "valid": wanted,
                            "lead": lead,
                            "missing": None if available else "no profile at the focused exact valid time",
                        }
                    )
                specs.sort(
                    key=lambda item: (
                        item["run"] is None,
                        item["run"] or datetime.max.replace(tzinfo=timezone.utc),
                    )
                )
        self._frame_specs = specs
        self.frames.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            self.frames.setItem(row, 0, self._check_item(True, row))
            values = (
                spec["label"],
                _utc_text(spec["run"]),
                _utc_text(spec["valid"]),
                f"F{int(spec['lead']):03d}" if spec["lead"] is not None else "Unknown",
                spec["missing"] or "Exact profile available",
            )
            for column, value in enumerate(values, start=1):
                self.frames.setItem(row, column, QTableWidgetItem(value))
        self.gif_button.setEnabled(bool(specs) and self._worker is None)

    def _selected_specs(self):
        return tuple(
            self._frame_specs[row]
            for row in range(self.frames.rowCount())
            if self.frames.item(row, 0) is not None
            and self.frames.item(row, 0).checkState() == Qt.Checked
        )

    def capture_animation_frames(self):
        specs = self._selected_specs()
        if not specs:
            raise ValueError("select at least one animation frame")
        win = self.host._window()
        sw = getattr(win, "spc_widget", None)
        collections = self.host._collections()
        ids = tuple(getattr(sw, "prof_ids", ()) or ())
        old_active = int(getattr(sw, "pc_idx", 0) or 0)
        old_indices = [int(getattr(item, "_prof_idx", 0) or 0) for item in collections]
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
                        png = pixmap_png(sw.grab())
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

    def _export_gif(self):
        if self._worker is not None:
            return
        suggested = self._suggested(
            "forecast-timeline.gif"
            if self.mode.currentData() == "forecast-timeline"
            else "run-to-run.gif"
        )
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export sounding animation", str(suggested), "GIF files (*.gif);;All files (*)"
        )
        if not path:
            return
        try:
            frames = self.capture_animation_frames()
        except Exception as exc:  # noqa: BLE001 - capture boundary
            self._set_status(f"Animation capture failed: {exc}", "error")
            return
        self._token += 1
        worker = _GifWorker(
            self._token,
            path,
            frames,
            self.mode.currentData(),
            self.duration.value(),
            self,
        )
        self._worker = worker
        worker.progress.connect(
            lambda completed, total: self._set_status(
                f"Encoding GIF frame {completed}/{total}…"
            )
        )
        worker.completed.connect(self._gif_ready)
        worker.failed.connect(self._gif_failed)
        worker.finished.connect(self._gif_finished)
        self.gif_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._set_status("Encoding fixed-scale timestamped GIF off the GUI thread…")
        worker.start()

    def _cancel(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            self._set_status("Cancelling GIF; the prior destination will remain unchanged…")

    def _gif_ready(self, token, path):
        if token == self._token:
            self._set_status(f"Saved timestamped animation: {path}")

    def _gif_failed(self, token, message):
        if token != self._token:
            return
        level = "warn" if "cancel" in message.casefold() else "error"
        self._set_status(f"Animation export did not complete: {message}", level)

    def _gif_finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self.cancel_button.setEnabled(False)
        self.gif_button.setEnabled(bool(self._frame_specs))

    def session_state(self):
        return {
            "title": self.title.text(),
            "mode": self.mode.currentData(),
            "duration_ms": self.duration.value(),
            "split": self.splitter.sizes(),
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        self.title.setText(str(state.get("title") or self.title.text()))
        mode = self.mode.findData(state.get("mode"))
        if mode >= 0:
            self.mode.setCurrentIndex(mode)
        try:
            self.duration.setValue(int(state.get("duration_ms", 750)))
        except (TypeError, ValueError):
            pass
        sizes = state.get("split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.splitter.setSizes([int(item) for item in sizes])
            except (TypeError, ValueError):
                pass

    def _set_status(self, text, level="info"):
        role = {"info": OBJ_STATUS, "warn": OBJ_WARNING_TEXT, "error": OBJ_ERROR_TEXT}.get(level, OBJ_STATUS)
        self.status.setObjectName(role)
        self.status.setText(str(text))
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def shutdown(self):
        self._token += 1
        worker = self._worker
        if worker is None:
            return
        worker.requestInterruption()
        worker.wait(2000)
        if worker.isRunning():
            from sharpmod.gui_threading import retain_worker_until_finished

            retain_worker_until_finished(worker)
        self._worker = None


__all__ = ["BriefingWorkspace", "pixmap_png", "table_rows"]
