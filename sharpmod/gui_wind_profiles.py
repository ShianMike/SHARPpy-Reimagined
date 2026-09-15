"""Observed NEXRAD VAD/VWP wind-profile analysis UI."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import math
from pathlib import Path

import numpy as np
from qtpy.QtCore import QPointF, QRectF, QThread, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.gui_scenarios import highlighted_profile
from sharpmod.gui_theme import current_theme
from sharpmod.theme import (
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)
from sharpmod.wind_profiles import (
    ObservedWindProfile,
    WindProfileCancelled,
    WindProfileSeries,
    compare_model_winds,
    export_wind_profile_csv,
    fetch_recent_vwp,
    import_level3,
    layer_diagnostics,
    read_wind_profiles,
    write_wind_profiles,
)


def _number(value, decimals=1, signed=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    return f"{number:+.{decimals}f}" if signed else f"{number:.{decimals}f}"


def _time_text(value):
    if not isinstance(value, datetime):
        return "Unknown"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _model_label(collection, index):
    metadata = dict(getattr(collection, "_meta", {}) or {})
    model = metadata.get("model") or metadata.get("loc") or f"Sounding {index + 1}"
    valid = getattr(collection, "_dates", ())
    current = getattr(collection, "_prof_idx", 0)
    try:
        valid = valid[current]
    except (IndexError, TypeError):
        valid = None
    return f"{model} · {_time_text(valid)}"


class _FetchWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)
    cancelled = Signal(int)

    def __init__(self, token, radar, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.radar = str(radar)

    def run(self):
        try:
            profile = fetch_recent_vwp(
                self.radar, cancel=self.isInterruptionRequested
            )
        except WindProfileCancelled:
            self.cancelled.emit(self.token)
            return
        except Exception as exc:  # noqa: BLE001 - provider boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, profile)


class HodographComparison(QWidget):
    """Compact data-coordinate hodograph with observed and model overlays."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("observedWindHodograph")
        self.setMinimumHeight(260)
        self._observed = None
        self._model = None

    def set_profiles(self, observed, model):
        self._observed = observed
        self._model = model
        self.update()

    @staticmethod
    def _model_points(profile):
        if profile is None:
            return ()
        try:
            u = np.ma.asarray(profile.u, dtype=float).filled(np.nan)
            v = np.ma.asarray(profile.v, dtype=float).filled(np.nan)
            height = np.ma.asarray(profile.hght, dtype=float).filled(np.nan)
        except (AttributeError, TypeError, ValueError):
            return ()
        size = min(u.size, v.size, height.size)
        return tuple(
            (float(u[index]), float(v[index]), float(height[index]))
            for index in range(size)
            if math.isfinite(u[index]) and math.isfinite(v[index]) and math.isfinite(height[index])
        )

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(theme.surface_sunken))
        rect = QRectF(self.rect()).adjusted(44.0, 26.0, -18.0, -32.0)
        observed = (
            tuple((item.u_kt, item.v_kt, item.height_msl_m) for item in self._observed.levels)
            if self._observed is not None
            else ()
        )
        model = self._model_points(self._model)
        all_points = (*observed, *model)
        extent = max(
            20.0,
            math.ceil(
                max((max(abs(u), abs(v)) for u, v, _height in all_points), default=20.0)
                / 10.0
            )
            * 10.0,
        )

        def point(u, v):
            return QPointF(
                rect.center().x() + (u / extent) * rect.width() / 2.0,
                rect.center().y() - (v / extent) * rect.height() / 2.0,
            )

        grid = QColor(theme.border)
        axis = QColor(theme.text_tertiary)
        painter.setPen(QPen(grid, 1.0, Qt.DotLine))
        for value in range(-int(extent), int(extent) + 1, 10):
            left = point(-extent, value)
            right = point(extent, value)
            painter.drawLine(left, right)
            bottom = point(value, -extent)
            top = point(value, extent)
            painter.drawLine(bottom, top)
        painter.setPen(QPen(axis, 1.2))
        painter.drawLine(point(-extent, 0), point(extent, 0))
        painter.drawLine(point(0, -extent), point(0, extent))
        painter.drawRect(rect)
        painter.drawText(8, 18, f"Wind components (kt), ±{extent:g}")

        def draw_series(points, colour, width, label, y):
            if points:
                path = QPainterPath(point(points[0][0], points[0][1]))
                for u, v, _height in points[1:]:
                    path.lineTo(point(u, v))
                painter.setPen(QPen(QColor(colour), width))
                painter.drawPath(path)
                for u, v, _height in points:
                    painter.drawEllipse(point(u, v), 2.7, 2.7)
            painter.setPen(QColor(colour))
            painter.drawText(rect.left() + 4, rect.bottom() + y, label)

        draw_series(model, theme.info, 1.6, "Model", 16)
        draw_series(observed, theme.warning, 2.2, "Observed VWP", 30)


class WindProfileWorkspace(QWidget):
    """Import/retrieve wind-only observations and compare with model soundings."""

    def __init__(self, *, collections_provider: Callable[[], tuple], parent=None):
        super().__init__(parent)
        self.setObjectName("analysisWindProfileWorkspace")
        self._collections_provider = collections_provider
        self._series = WindProfileSeries()
        self._worker = None
        self._token = 0
        self._build_ui()

    @property
    def profiles(self):
        return self._series.profiles

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        intro = QLabel(
            "Wind-only NEXRAD VAD Wind Profile (product 48). No temperature, "
            "dewpoint, parcel diagnostics, or unsupported vertical extrapolation is added."
        )
        intro.setObjectName(OBJ_HINT)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        source = QHBoxLayout()
        source.addWidget(QLabel("Radar"))
        self.radar = QLineEdit("KLOT")
        self.radar.setObjectName("windProfileRadar")
        self.radar.setMaxLength(4)
        self.radar.setMaximumWidth(90)
        source.addWidget(self.radar)
        self.retrieve = QPushButton("Retrieve latest public VWP")
        self.retrieve.setObjectName("windProfileRetrieve")
        self.retrieve.setToolTip("NOAA/NWS TGFTP RPCCDS product 48 sn.last")
        source.addWidget(self.retrieve)
        self.cancel_retrieve = self._button(
            "Cancel", "Cancel the active public VWP retrieval"
        )
        self.cancel_retrieve.setObjectName("windProfileCancelRetrieve")
        source.addWidget(self.cancel_retrieve)
        self.import_button = self._button("Import…", "Import Level III product 48, JSON, or wind-profile CSV")
        self.save_button = self._button("Save series…", "Save wind-only JSON")
        self.export_button = self._button("Export current CSV…", "Export provenance, levels, and quality")
        for button in (self.import_button, self.save_button, self.export_button):
            source.addWidget(button)
        source.addStretch(1)
        outer.addLayout(source)

        step = QHBoxLayout()
        self.previous = self._button("◀", "Previous observation time")
        self.next = self._button("▶", "Next observation time")
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("windProfileTime")
        self.profile_combo.setAccessibleName("Observed VWP time")
        step.addWidget(self.previous)
        step.addWidget(self.profile_combo, 1)
        step.addWidget(self.next)
        step.addWidget(QLabel("Model sounding"))
        self.model = QComboBox()
        self.model.setObjectName("windProfileModel")
        step.addWidget(self.model, 1)
        outer.addLayout(step)

        self.info = QLabel("Import a representative profile or retrieve the latest available VWP.")
        self.info.setObjectName(OBJ_STATUS)
        self.info.setWordWrap(True)
        outer.addWidget(self.info)

        self.hodograph = HodographComparison()
        self.differences = self._table("windProfileDifferences")
        self.differences.setColumnCount(9)
        self.differences.setHorizontalHeaderLabels(
            ("Height MSL", "Height AGL", "Observed u", "Observed v", "Model u", "Model v", "Δu", "Δv", "Quality")
        )
        split = QSplitter(Qt.Vertical)
        split.setObjectName("windProfileSplitter")
        split.setChildrenCollapsible(False)
        split.addWidget(self.hodograph)
        split.addWidget(self.differences)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        self.splitter = split

        diagnostics = QGridLayout()
        self.bottom = QDoubleSpinBox()
        self.bottom.setRange(0.0, 19000.0)
        self.bottom.setValue(100.0)
        self.bottom.setSuffix(" m AGL")
        self.top = QDoubleSpinBox()
        self.top.setRange(1.0, 20000.0)
        self.top.setValue(3000.0)
        self.top.setSuffix(" m AGL")
        self.use_storm = QCheckBox("Use explicit storm motion")
        self.storm_u = QDoubleSpinBox()
        self.storm_v = QDoubleSpinBox()
        for spin, prefix in ((self.storm_u, "u "), (self.storm_v, "v ")):
            spin.setRange(-150.0, 150.0)
            spin.setPrefix(prefix)
            spin.setSuffix(" kt")
        self.storm_source = QLineEdit()
        self.storm_source.setPlaceholderText("required source, e.g. Bunkers RM from HRRR")
        self.compute_layer = QPushButton("Compute supported layer diagnostics")
        self.compute_layer.setObjectName("windProfileLayerDiagnostics")
        diagnostics.addWidget(QLabel("Layer"), 0, 0)
        diagnostics.addWidget(self.bottom, 0, 1)
        diagnostics.addWidget(self.top, 0, 2)
        diagnostics.addWidget(self.compute_layer, 0, 3)
        diagnostics.addWidget(self.use_storm, 1, 0)
        diagnostics.addWidget(self.storm_u, 1, 1)
        diagnostics.addWidget(self.storm_v, 1, 2)
        diagnostics.addWidget(self.storm_source, 1, 3)
        outer.addLayout(diagnostics)
        self.layer_result = QLabel(
            "Layer shear requires both bounds within accepted VWP coverage; "
            "storm-relative fields additionally require an explicit motion and source."
        )
        self.layer_result.setObjectName(OBJ_HINT)
        self.layer_result.setWordWrap(True)
        outer.addWidget(self.layer_result)

        self.retrieve.clicked.connect(self._retrieve)
        self.cancel_retrieve.clicked.connect(self._cancel_fetch)
        self.import_button.clicked.connect(self._import)
        self.save_button.clicked.connect(self._save)
        self.export_button.clicked.connect(self._export)
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.profile_combo.currentIndexChanged.connect(self._select_profile)
        self.model.currentIndexChanged.connect(self._render)
        self.compute_layer.clicked.connect(self._layer)
        self._update_enabled()

    @staticmethod
    def _button(text, tooltip):
        button = QPushButton(text)
        button.setObjectName(OBJ_GHOST)
        button.setToolTip(tooltip)
        return button

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

    def refresh(self):
        previous = self.model.currentData()
        self.model.blockSignals(True)
        self.model.clear()
        for index, collection in enumerate(tuple(self._collections_provider() or ())):
            profile = highlighted_profile(collection)
            if profile is not None and hasattr(profile, "u") and hasattr(profile, "v"):
                self.model.addItem(_model_label(collection, index), index)
        selected = self.model.findData(previous)
        self.model.setCurrentIndex(selected if selected >= 0 else (0 if self.model.count() else -1))
        self.model.blockSignals(False)
        self._render()

    def _append(self, profiles):
        current_time = self._series.current.observed_at if self._series.current else None
        merged = {item.observed_at: item for item in self._series.profiles}
        for item in profiles:
            merged[item.observed_at] = item
        self._series = WindProfileSeries(merged.values())
        if current_time is not None:
            for index, item in enumerate(self._series.profiles):
                if item.observed_at == current_time:
                    self._series.select(index)
                    break
        self._populate_times()

    def _populate_times(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for index, item in enumerate(self._series.profiles):
            self.profile_combo.addItem(
                f"{item.radar_id} · {_time_text(item.observed_at)}", index
            )
        if self.profile_combo.count():
            self.profile_combo.setCurrentIndex(self._series.index)
        self.profile_combo.blockSignals(False)
        self._render()
        self._update_enabled()

    def _select_profile(self, index):
        if index >= 0:
            self._series.select(index)
        self._render()

    def _step(self, amount):
        current = self._series.step(amount)
        if current is not None:
            self.profile_combo.setCurrentIndex(self._series.index)

    def _model_profile(self):
        collections = tuple(self._collections_provider() or ())
        try:
            return highlighted_profile(collections[int(self.model.currentData())])
        except (IndexError, TypeError, ValueError):
            return None

    def _render(self, *_args):
        observed = self._series.current
        model = self._model_profile()
        self.hodograph.set_profiles(observed, model)
        if observed is None:
            self.differences.setRowCount(0)
            self.info.setText("Import a representative profile or retrieve the latest available VWP.")
            return
        bottom, top = observed.coverage_msl_m
        agl = observed.coverage_agl_m
        age = observed.age_seconds()
        age_text = (
            f"age {age / 60.0:.0f} min"
            if age is not None and age >= 0
            else "observation time is later than the current clock"
        )
        agl_text = (
            f"; {agl[0]:.0f}–{agl[1]:.0f} m AGL" if agl is not None else "; AGL unavailable"
        )
        self.info.setText(
            f"{observed.radar_id} ({observed.latitude:.3f}, {observed.longitude:.3f}); "
            f"observed {_time_text(observed.observed_at)}; {age_text}; coverage "
            f"{bottom:.0f}–{top:.0f} m MSL{agl_text}; "
            f"{observed.missing_or_poor_levels}/{len(observed.levels)} poor/unknown; "
            f"{observed.attribution}."
        )
        rows = compare_model_winds(observed, model) if model is not None else ()
        self.differences.setRowCount(len(observed.levels))
        for row, level in enumerate(observed.levels):
            difference = rows[row] if row < len(rows) else None
            values = (
                _number(level.height_msl_m, 0),
                _number(level.height_agl_m, 0),
                _number(level.u_kt),
                _number(level.v_kt),
                _number(difference.model_u_kt if difference else None),
                _number(difference.model_v_kt if difference else None),
                _number(difference.u_error_kt if difference else None, signed=True),
                _number(difference.v_error_kt if difference else None, signed=True),
                level.quality,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    level.quality if level.rms_kt is None else f"RMS residual {level.rms_kt:g} kt; {level.quality}"
                )
                self.differences.setItem(row, column, item)

    def _layer(self):
        profile = self._series.current
        if profile is None:
            return
        storm = None
        if self.use_storm.isChecked():
            storm = (
                self.storm_u.value(),
                self.storm_v.value(),
                self.storm_source.text().strip(),
            )
        try:
            result = layer_diagnostics(
                profile, self.bottom.value(), self.top.value(), storm_motion=storm
            )
        except Exception as exc:  # noqa: BLE001 - user input boundary
            self._set_status(f"Layer diagnostics unavailable: {exc}", "error")
            return
        shear = _number(result.shear_magnitude_kt)
        srh = _number(result.storm_relative_helicity_m2_s2)
        source = result.storm_motion_source or "none supplied"
        self.layer_result.setText(
            f"{result.bottom_agl_m:g}–{result.top_agl_m:g} m AGL: shear {shear} kt; "
            f"SRH {srh} m² s⁻²; usable levels {result.usable_level_count}; "
            f"storm motion source: {source}. {result.reason or 'Coverage and quality support the calculation.'}"
        )
        self.layer_result.setObjectName(OBJ_WARNING_TEXT if result.reason else OBJ_STATUS)

    def _retrieve(self):
        if self._worker is not None:
            return
        radar = self.radar.text().strip().upper()
        self._token += 1
        worker = _FetchWorker(self._token, radar, self)
        self._worker = worker
        worker.completed.connect(self._retrieved)
        worker.failed.connect(self._failed)
        worker.cancelled.connect(self._fetch_cancelled)
        worker.finished.connect(self._finished)
        self.retrieve.setEnabled(False)
        self.cancel_retrieve.setEnabled(True)
        self._set_status(f"Retrieving the latest public product 48 for {radar}…")
        worker.start()

    def _cancel_fetch(self):
        worker = self._worker
        if worker is None:
            return
        worker.requestInterruption()
        self.cancel_retrieve.setEnabled(False)
        self._set_status("Cancelling VWP retrieval…")

    def _retrieved(self, token, profile):
        if token != self._token:
            return
        self._append((profile,))
        self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)
        self._set_status(
            f"Retrieved actual {profile.radar_id} VWP timestamp {_time_text(profile.observed_at)}."
        )

    def _failed(self, token, message):
        if token == self._token:
            self._set_status(f"VWP retrieval failed: {message}", "error")

    def _fetch_cancelled(self, token):
        if token == self._token:
            self._set_status("VWP retrieval cancelled.", "warn")

    def _finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self._update_enabled()

    def _suggested(self, filename):
        try:
            return export_file_path(filename)
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _import(self):
        suggested = self._suggested("observed-wind-profiles.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Import observed VAD/VWP wind profile",
            str(Path(suggested).parent),
            "Wind profiles (*.json *.csv *.nids *.nexrad *.raw);;All files (*)",
        )
        if not path:
            return
        try:
            profiles = (
                (import_level3(path),)
                if Path(path).suffix.casefold() not in {".json", ".csv"}
                else read_wind_profiles(path)
            )
        except Exception as exc:  # noqa: BLE001 - file boundary
            self._set_status(f"Wind-profile import failed: {exc}", "error")
            return
        self._append(profiles)
        self._set_status(f"Imported {len(profiles)} wind-only profile(s) from {path}.")

    def _save(self):
        if not self._series.profiles:
            return
        suggested = self._suggested("observed-wind-profiles.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save observed wind profiles", str(suggested), "JSON files (*.json);;All files (*)"
        )
        if path:
            try:
                saved = write_wind_profiles(path, self._series.profiles)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Wind-profile save failed: {exc}", "error")
            else:
                self._set_status(f"Saved {saved}")

    def _export(self):
        profile = self._series.current
        if profile is None:
            return
        suggested = self._suggested(f"{profile.radar_id.lower()}-vwp.csv")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export observed wind profile", str(suggested), "CSV files (*.csv);;All files (*)"
        )
        if path:
            try:
                saved = export_wind_profile_csv(path, profile)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Wind-profile export failed: {exc}", "error")
            else:
                self._set_status(f"Saved {saved}")

    def session_state(self):
        return {
            "profiles": self._series.as_dicts(),
            "current": self._series.index,
            "radar": self.radar.text(),
            "model": self.model.currentData(),
            "layer": {
                "bottom_agl_m": self.bottom.value(),
                "top_agl_m": self.top.value(),
                "use_storm": self.use_storm.isChecked(),
                "storm_u_kt": self.storm_u.value(),
                "storm_v_kt": self.storm_v.value(),
                "storm_source": self.storm_source.text(),
            },
            "split": self.splitter.sizes(),
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        raw = state.get("profiles")
        if isinstance(raw, list):
            try:
                profiles = tuple(ObservedWindProfile.from_dict(item) for item in raw)
            except Exception as exc:  # noqa: BLE001 - session boundary
                self._set_status(f"Observed wind profiles were not restored: {exc}", "error")
            else:
                self._series = WindProfileSeries(profiles)
                try:
                    self._series.select(int(state.get("current", 0)))
                except (TypeError, ValueError):
                    pass
                self._populate_times()
        self.radar.setText(str(state.get("radar") or self.radar.text()))
        layer = state.get("layer")
        if isinstance(layer, Mapping):
            for widget, key in (
                (self.bottom, "bottom_agl_m"),
                (self.top, "top_agl_m"),
                (self.storm_u, "storm_u_kt"),
                (self.storm_v, "storm_v_kt"),
            ):
                try:
                    widget.setValue(float(layer.get(key, widget.value())))
                except (TypeError, ValueError):
                    pass
            self.use_storm.setChecked(bool(layer.get("use_storm", False)))
            self.storm_source.setText(str(layer.get("storm_source") or ""))
        sizes = state.get("split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.splitter.setSizes([int(item) for item in sizes])
            except (TypeError, ValueError):
                pass
        self._update_enabled()

    def _update_enabled(self):
        has_profile = self._series.current is not None
        self.retrieve.setEnabled(self._worker is None)
        self.cancel_retrieve.setEnabled(self._worker is not None)
        self.previous.setEnabled(has_profile and self._series.index > 0)
        self.next.setEnabled(has_profile and self._series.index + 1 < len(self._series.profiles))
        self.save_button.setEnabled(has_profile)
        self.export_button.setEnabled(has_profile)
        self.compute_layer.setEnabled(has_profile)

    def _set_status(self, text, level="info"):
        role = {"info": OBJ_STATUS, "warn": OBJ_WARNING_TEXT, "error": OBJ_ERROR_TEXT}.get(level, OBJ_STATUS)
        self.info.setObjectName(role)
        self.info.setText(str(text))
        style = self.info.style()
        style.unpolish(self.info)
        style.polish(self.info)

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


__all__ = ["HodographComparison", "WindProfileWorkspace"]
