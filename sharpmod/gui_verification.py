"""Forecast-versus-observed sounding verification workspace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

from qtpy.QtCore import QThread, Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sharpmod.box_analysis import parameter
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.theme import (
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)
from sharpmod.verification import (
    DEFAULT_VERIFICATION_METRICS,
    VerificationRules,
    aggregate_verification,
    export_verification_csv,
    identity_for_collection,
    read_verification_json,
    verification_pair_from_dict,
    verify_pair,
    write_verification_json,
)


VERTICAL_LABELS = {
    "temperature_c": "Temperature (°C)",
    "dewpoint_c": "Dewpoint (°C)",
    "u_wind_kt": "u wind (kt)",
    "v_wind_kt": "v wind (kt)",
}


def _time_text(value):
    if not isinstance(value, datetime):
        return "Unknown"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _identity_label(identity):
    lead = f" F{identity.lead_hours:03d}" if identity.lead_hours is not None else ""
    station = f" · {identity.station_id}" if identity.station_id else ""
    return f"{identity.label}{lead} · {_time_text(identity.valid_time)}{station}"


def _number(value, decimals=1):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{decimals}f}" if math.isfinite(value) else "—"


class _VerificationWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, forecast, observation, engine, rules, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.forecast = forecast
        self.observation = observation
        self.engine = engine
        self.rules = rules

    def run(self):
        try:
            result = verify_pair(
                self.forecast,
                self.observation,
                self.engine,
                metric_keys=DEFAULT_VERIFICATION_METRICS,
                rules=self.rules,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, result)


class VerificationWorkspace(QWidget):
    """Pair loaded forecasts/observations and retain inspectable case results."""

    def __init__(
        self,
        engine,
        *,
        collections_provider: Callable[[], tuple],
        request_observation: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("analysisVerificationWorkspace")
        self.engine = engine
        self._collections_provider = collections_provider
        self._request_observation = request_observation
        self._pairs = []
        self._included = []
        self._worker = None
        self._token = 0
        self._build_ui()

    @property
    def pairs(self):
        return tuple(self._pairs)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        explanation = QLabel(
            "Forecasts pair only with collections explicitly classified as observed. "
            "Analyses and reanalyses stay visible as separate types and are excluded."
        )
        explanation.setObjectName(OBJ_HINT)
        explanation.setWordWrap(True)
        outer.addWidget(explanation)

        selectors = QGridLayout()
        selectors.addWidget(QLabel("Forecast"), 0, 0)
        self.forecast = QComboBox()
        self.forecast.setObjectName("verificationForecast")
        selectors.addWidget(self.forecast, 0, 1, 1, 4)
        selectors.addWidget(QLabel("Observation"), 1, 0)
        self.observation = QComboBox()
        self.observation.setObjectName("verificationObservation")
        selectors.addWidget(self.observation, 1, 1, 1, 3)
        self.load_observation = QPushButton("Load observed sounding…")
        self.load_observation.setObjectName(OBJ_GHOST)
        self.load_observation.setToolTip(
            "Return to the existing Station Map observed-sounding provider"
        )
        selectors.addWidget(self.load_observation, 1, 4)
        self.classifications = QLabel()
        self.classifications.setObjectName(OBJ_HINT)
        self.classifications.setWordWrap(True)
        selectors.addWidget(self.classifications, 2, 1, 1, 4)
        outer.addLayout(selectors)

        rules = QGridLayout()
        self.station = QLineEdit()
        self.station.setObjectName("verificationStationRule")
        self.station.setPlaceholderText("optional, e.g. KOUN")
        self.time_tolerance = QSpinBox()
        self.time_tolerance.setRange(0, 180)
        self.time_tolerance.setSuffix(" min")
        self.distance = QDoubleSpinBox()
        self.distance.setRange(0.0, 1000.0)
        self.distance.setValue(50.0)
        self.distance.setSuffix(" km")
        self.vertical_gap = QSpinBox()
        self.vertical_gap.setRange(1, 3000)
        self.vertical_gap.setValue(750)
        self.vertical_gap.setSuffix(" m")
        self.as_of = QLineEdit()
        self.as_of.setObjectName("verificationAsOf")
        self.as_of.setPlaceholderText("optional UTC ISO time, e.g. 2026-05-20T12:00Z")
        for column, (label, widget) in enumerate(
            (
                ("Station", self.station),
                ("Time tolerance", self.time_tolerance),
                ("Max distance", self.distance),
                ("Max vertical gap", self.vertical_gap),
            )
        ):
            rules.addWidget(QLabel(label), 0, column)
            rules.addWidget(widget, 1, column)
        rules.addWidget(QLabel("Forecast as-of cutoff"), 2, 0)
        rules.addWidget(self.as_of, 2, 1, 1, 3)
        outer.addLayout(rules)

        actions = QHBoxLayout()
        self.verify_button = QPushButton("Verify pair")
        self.verify_button.setObjectName("verificationRun")
        self.clear_button = self._button("Clear cases", "Remove retained verification results")
        actions.addWidget(self.verify_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.save_button = self._button("Save…", "Save verification cases as JSON")
        self.open_button = self._button("Open…", "Open saved verification cases")
        self.export_button = self._button("Export CSV…", "Export cases and vertical errors")
        for button in (self.save_button, self.open_button, self.export_button):
            actions.addWidget(button)
        outer.addLayout(actions)

        self.status = QLabel("Load one forecast and one observed sounding.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.cases = self._table("verificationCases")
        self.cases.setColumnCount(7)
        self.cases.setHorizontalHeaderLabels(
            ("Use", "Model", "Lead", "Forecast valid", "Observation", "Distance", "Outcome")
        )
        self.cases.setSelectionMode(QAbstractItemView.SingleSelection)

        self.details = QTabWidget()
        self.details.setObjectName("verificationDetails")
        self.vertical = self._table("verificationVerticalErrors")
        self.vertical.setColumnCount(7)
        self.vertical.setHorizontalHeaderLabels(
            ("Height MSL", "Height AGL", "Pressure", "ΔT", "ΔTd", "Δu", "Δv")
        )
        self.details.addTab(self.vertical, "Vertical errors")
        self.diagnostics = self._table("verificationDiagnosticErrors")
        self.diagnostics.setColumnCount(5)
        self.diagnostics.setHorizontalHeaderLabels(
            ("Diagnostic", "Forecast", "Observed", "Error", "Availability")
        )
        self.details.addTab(self.diagnostics, "Diagnostics")
        self.aggregates = self._table("verificationAggregates")
        self.aggregates.setColumnCount(8)
        self.aggregates.setHorizontalHeaderLabels(
            ("Model / lead", "Field", "N", "Bias", "MAE", "RMSE", "Matched", "Rejected")
        )
        self.details.addTab(self.aggregates, "Aggregate")
        split = QSplitter(Qt.Vertical)
        split.setObjectName("verificationSplitter")
        split.setChildrenCollapsible(False)
        split.addWidget(self.cases)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        self.splitter = split

        self.verify_button.clicked.connect(self._verify)
        self.load_observation.clicked.connect(self._request_observed)
        self.clear_button.clicked.connect(self._clear)
        self.save_button.clicked.connect(self._save)
        self.open_button.clicked.connect(self._open)
        self.export_button.clicked.connect(self._export)
        self.cases.itemSelectionChanged.connect(self._render_selected)
        self.cases.itemChanged.connect(self._case_changed)
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
        collections = tuple(self._collections_provider() or ())
        previous_forecast = self.forecast.currentData()
        previous_observation = self.observation.currentData()
        for combo in (self.forecast, self.observation):
            combo.blockSignals(True)
            combo.clear()
        counts = {"forecast": 0, "observation": 0, "analysis": 0, "reanalysis": 0}
        for index, collection in enumerate(collections):
            identity = identity_for_collection(collection)
            counts[identity.kind] = counts.get(identity.kind, 0) + 1
            if identity.kind == "forecast":
                self.forecast.addItem(_identity_label(identity), index)
            elif identity.kind == "observation":
                self.observation.addItem(_identity_label(identity), index)
        for combo, previous in (
            (self.forecast, previous_forecast),
            (self.observation, previous_observation),
        ):
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1))
            combo.blockSignals(False)
        self.classifications.setText(
            "Loaded classifications: "
            + ", ".join(f"{kind} {counts.get(kind, 0)}" for kind in ("forecast", "observation", "analysis", "reanalysis"))
            + ". Only forecast + observation can form a verification pair."
        )
        self._update_enabled()

    def _rules(self):
        raw = self.as_of.text().strip()
        as_of = None
        if raw:
            try:
                as_of = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("as-of cutoff must be an ISO UTC timestamp") from exc
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        return VerificationRules(
            station_id=self.station.text().strip() or None,
            valid_time_tolerance=timedelta(minutes=self.time_tolerance.value()),
            max_distance_km=self.distance.value(),
            max_vertical_gap_m=self.vertical_gap.value(),
            as_of=as_of,
        )

    def _verify(self):
        if self._worker is not None:
            return
        collections = tuple(self._collections_provider() or ())
        try:
            forecast = collections[int(self.forecast.currentData())]
            observation = collections[int(self.observation.currentData())]
            rules = self._rules()
        except Exception as exc:  # noqa: BLE001 - user input boundary
            self._set_status(f"Verification could not start: {exc}", "error")
            return
        self._token += 1
        worker = _VerificationWorker(
            self._token, forecast, observation, self.engine, rules, self
        )
        self._worker = worker
        worker.completed.connect(self._ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.verify_button.setEnabled(False)
        self._set_status("Matching exact loaded profiles and computing conservative errors…")
        worker.start()

    def _ready(self, token, pair):
        if token != self._token:
            return
        self._pairs.append(pair)
        # Rejected attempts are part of the sample basis and are checked by
        # default so aggregate tables disclose them instead of quietly dropping
        # them. Users may explicitly uncheck any case.
        self._included.append(True)
        self._render_cases(select=len(self._pairs) - 1)
        if pair.matched:
            self._set_status(
                f"Matched {_identity_label(pair.forecast)} to "
                f"{_identity_label(pair.observation)}; {len(pair.levels)} observed "
                "levels were evaluated without extrapolation."
            )
        else:
            self._set_status(
                "Pair retained as rejected: " + "; ".join(pair.rejection_reasons),
                "warn",
            )

    def _failed(self, token, message):
        if token == self._token:
            self._set_status(f"Verification failed: {message}", "error")

    def _finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self._update_enabled()

    def _render_cases(self, select=None):
        self.cases.blockSignals(True)
        self.cases.setRowCount(len(self._pairs))
        for row, pair in enumerate(self._pairs):
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            use.setCheckState(Qt.Checked if self._included[row] else Qt.Unchecked)
            use.setData(Qt.UserRole, row)
            self.cases.setItem(row, 0, use)
            values = (
                pair.forecast.label,
                f"F{pair.lead_hours:03d}" if pair.lead_hours is not None else "Unknown",
                _time_text(pair.forecast.valid_time),
                pair.observation.station_id or pair.observation.label,
                f"{pair.distance_km:.1f} km" if pair.distance_km is not None else "Unknown",
                "Matched" if pair.matched else "Rejected",
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 6 and not pair.matched:
                    item.setToolTip("; ".join(pair.rejection_reasons))
                self.cases.setItem(row, column, item)
        self.cases.blockSignals(False)
        if select is not None and 0 <= select < len(self._pairs):
            self.cases.selectRow(select)
        elif self._pairs and self.cases.currentRow() < 0:
            self.cases.selectRow(0)
        self._render_aggregates()
        self._update_enabled()

    def _case_changed(self, item):
        if item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self._included):
            self._included[row] = item.checkState() == Qt.Checked
            self._render_aggregates()

    def _render_selected(self):
        row = self.cases.currentRow()
        if not 0 <= row < len(self._pairs):
            self.vertical.setRowCount(0)
            self.diagnostics.setRowCount(0)
            return
        pair = self._pairs[row]
        self.vertical.setRowCount(len(pair.levels))
        for target_row, level in enumerate(pair.levels):
            values = (
                _number(level.height_msl_m, 0),
                _number(level.observed_height_agl_m, 0),
                _number(level.observed_pressure_hpa, 1),
                _number(level.temperature_c),
                _number(level.dewpoint_c),
                _number(level.u_wind_kt),
                _number(level.v_wind_kt),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.vertical.setItem(target_row, column, item)
        self.diagnostics.setRowCount(len(pair.diagnostics))
        for target_row, value in enumerate(pair.diagnostics):
            try:
                label = parameter(value.key).label
            except Exception:
                label = value.key
            values = (
                label,
                _number(value.forecast),
                _number(value.observed),
                _number(value.error),
                value.reason or "Available",
            )
            for column, text in enumerate(values):
                self.diagnostics.setItem(target_row, column, QTableWidgetItem(text))

    def _render_aggregates(self):
        selected = tuple(
            pair for pair, use in zip(self._pairs, self._included) if use
        )
        groups = aggregate_verification(selected)
        rows = []
        for group in groups:
            label = f"{group.model} / " + (
                f"F{group.lead_hours:03d}" if group.lead_hours is not None else "lead unknown"
            )
            fields = (*group.vertical.items(), *group.diagnostics.items())
            for field, metric in fields:
                rows.append((label, VERTICAL_LABELS.get(field, field), metric, group))
            if not fields:
                rows.append((label, "No valid samples", None, group))
        self.aggregates.setRowCount(len(rows))
        for row, (label, field, metric, group) in enumerate(rows):
            values = (
                label,
                field,
                str(metric.count) if metric is not None else "0",
                _number(metric.bias) if metric is not None else "—",
                _number(metric.mae) if metric is not None else "—",
                _number(metric.rmse) if metric is not None else "—",
                str(group.matched_pair_count),
                str(group.rejected_pair_count),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(group.matching_rule_summary)
                self.aggregates.setItem(row, column, item)

    def _request_observed(self):
        if callable(self._request_observation):
            self._request_observation()
            self._set_status(
                "The picker is ready on Station Map. Fetch the required observed "
                "sounding, keep multi-sounding enabled, then return and Refresh."
            )

    def _clear(self):
        self._pairs.clear()
        self._included.clear()
        self._render_cases()
        self._render_selected()
        self._set_status("Verification cases cleared.")

    def _suggested(self, filename):
        try:
            return export_file_path(filename)
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _save(self):
        if not self._pairs:
            return
        suggested = self._suggested("forecast-verification.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save forecast verification", str(suggested), "JSON files (*.json);;All files (*)"
        )
        if path:
            try:
                saved = write_verification_json(path, self._pairs)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Verification save failed: {exc}", "error")
            else:
                self._set_status(f"Saved {saved}")

    def _open(self):
        suggested = self._suggested("forecast-verification.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open forecast verification", str(Path(suggested).parent), "JSON files (*.json);;All files (*)"
        )
        if path:
            try:
                self._pairs = list(read_verification_json(path))
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Verification open failed: {exc}", "error")
                return
            self._included = [True for _pair in self._pairs]
            self._render_cases(select=0)
            self._set_status(f"Opened {len(self._pairs)} saved verification case(s).")

    def _export(self):
        if not self._pairs:
            return
        suggested = self._suggested("forecast-verification.csv")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export forecast verification", str(suggested), "CSV files (*.csv);;All files (*)"
        )
        if path:
            try:
                saved = export_verification_csv(path, self._pairs)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Verification export failed: {exc}", "error")
            else:
                self._set_status(f"Saved {saved}")

    def session_state(self):
        try:
            rules = self._rules().as_dict()
        except Exception:
            rules = None
        return {
            "pairs": [pair.as_dict() for pair in self._pairs],
            "included": list(self._included),
            "rules": rules,
            "split": self.splitter.sizes(),
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        pairs = state.get("pairs")
        if isinstance(pairs, list):
            restored = []
            try:
                restored = [verification_pair_from_dict(item) for item in pairs]
            except Exception as exc:  # noqa: BLE001 - session boundary
                self._set_status(f"Verification cases were not restored: {exc}", "error")
            else:
                self._pairs = restored
                raw_included = state.get("included")
                self._included = [
                    bool(raw_included[index])
                    if isinstance(raw_included, list) and index < len(raw_included)
                    else pair.matched
                    for index, pair in enumerate(restored)
                ]
                self._render_cases(select=0)
        rules = state.get("rules")
        if isinstance(rules, Mapping):
            station = rules.get("station_id")
            self.station.setText(str(station or ""))
            try:
                self.time_tolerance.setValue(
                    round(float(rules.get("valid_time_tolerance_seconds", 0.0)) / 60.0)
                )
                self.distance.setValue(float(rules.get("max_distance_km", 50.0)))
                self.vertical_gap.setValue(int(rules.get("max_vertical_gap_m", 750.0)))
            except (TypeError, ValueError):
                pass
            self.as_of.setText(str(rules.get("as_of") or ""))
        sizes = state.get("split")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                self.splitter.setSizes([int(value) for value in sizes])
            except (TypeError, ValueError):
                pass
        self._update_enabled()

    def _update_enabled(self):
        ready = self.forecast.count() > 0 and self.observation.count() > 0
        self.verify_button.setEnabled(ready and self._worker is None)
        has_pairs = bool(self._pairs)
        self.clear_button.setEnabled(has_pairs)
        self.save_button.setEnabled(has_pairs)
        self.export_button.setEnabled(has_pairs)

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


__all__ = ["VerificationWorkspace"]
