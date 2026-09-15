"""Interactive ensemble ingredient-agreement threshold explorer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qtpy.QtCore import QThread, Qt, Signal
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.box_analysis import PARAMETERS, parameter
from sharpmod.ensemble_members import EnsembleAcquisition
from sharpmod.ensemble_thresholds import (
    DISCLAIMER,
    OPERATORS,
    ThresholdCondition,
    ThresholdDefinition,
    evaluate_threshold,
    evaluate_threshold_timeline,
    export_threshold_csv,
    read_threshold_definition,
    write_threshold_results,
)
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.theme import OBJ_ERROR_TEXT, OBJ_GHOST, OBJ_HINT, OBJ_STATUS, SPACE


class _ThresholdWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, collection, definition, engine, timeline, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.collection = collection
        self.definition = definition
        self.engine = engine
        self.timeline = bool(timeline)

    def run(self):
        try:
            if self.timeline:
                result = evaluate_threshold_timeline(
                    self.collection,
                    self.definition,
                    self.engine,
                    cancel=self.isInterruptionRequested,
                )
            else:
                result = (
                    evaluate_threshold(
                        self.collection, self.definition, self.engine
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, result)


class EnsembleThresholdExplorer(QWidget):
    """Single and joint member-by-member diagnostic threshold analysis."""

    memberActivated = Signal(str, object)

    MAX_CONDITIONS = 3

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisEnsembleThresholdExplorer")
        self.engine = engine
        self._collection = None
        self._results = ()
        self._worker = None
        self._token = 0
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        outer.setSpacing(SPACE["sm"])
        intro = QLabel(
            f"{DISCLAIMER} Missing members and unusable diagnostics remain separate "
            "from members that fail a condition."
        )
        intro.setObjectName(OBJ_HINT)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Definition"))
        self.name = QLineEdit("Ingredient agreement")
        self.name.setObjectName("thresholdDefinitionName")
        self.name.setAccessibleName("Threshold definition name")
        name_row.addWidget(self.name, 1)
        outer.addLayout(name_row)

        grid = QGridLayout()
        for column, text in enumerate(("Use", "Diagnostic", "Rule", "Value", "Upper")):
            label = QLabel(text)
            label.setObjectName(OBJ_HINT)
            grid.addWidget(label, 0, column)
        self.condition_rows = []
        defaults = ("mlcape", "shear_6km", "srh_3km")
        for row in range(self.MAX_CONDITIONS):
            enabled = QCheckBox()
            enabled.setObjectName(f"thresholdConditionEnabled{row + 1}")
            enabled.setChecked(row == 0)
            if row == 0:
                enabled.setEnabled(False)
                enabled.setToolTip("At least one condition is required")
            metric = QComboBox()
            metric.setObjectName(f"thresholdMetric{row + 1}")
            for item in PARAMETERS:
                suffix = f" ({item.units})" if item.units else ""
                metric.addItem(f"{item.label}{suffix}", item.key)
            index = metric.findData(defaults[row])
            metric.setCurrentIndex(max(0, index))
            operator = QComboBox()
            operator.setObjectName(f"thresholdOperator{row + 1}")
            # OPERATORS is intentionally a set in the pure validation layer;
            # keep the control's order stable and put the common comparisons
            # first for keyboard users and deterministic sessions/screenshots.
            for item in (">", ">=", "<", "<=", "==", "between"):
                operator.addItem(item, item)
            operator.setCurrentIndex(max(0, operator.findData(">=")))
            value = QDoubleSpinBox()
            value.setObjectName(f"thresholdValue{row + 1}")
            value.setRange(-100000.0, 100000.0)
            value.setDecimals(1)
            value.setValue((1000.0, 35.0, 150.0)[row])
            upper = QDoubleSpinBox()
            upper.setObjectName(f"thresholdUpper{row + 1}")
            upper.setRange(-100000.0, 100000.0)
            upper.setDecimals(1)
            upper.setValue(value.value() + 500.0)
            upper.setEnabled(False)
            operator.currentIndexChanged.connect(
                lambda _index, combo=operator, spin=upper: spin.setEnabled(
                    combo.currentData() == "between"
                )
            )
            for column, widget in enumerate((enabled, metric, operator, value, upper)):
                grid.addWidget(widget, row + 1, column)
            self.condition_rows.append((enabled, metric, operator, value, upper))
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        self.evaluate_current = QPushButton("Evaluate current")
        self.evaluate_current.setObjectName("thresholdEvaluateCurrent")
        self.evaluate_timeline = QPushButton("Evaluate timeline")
        self.evaluate_timeline.setObjectName("thresholdEvaluateTimeline")
        self.cancel = QPushButton("Cancel")
        self.cancel.setObjectName(OBJ_GHOST)
        self.cancel.setEnabled(False)
        self.save = QPushButton("Save…")
        self.save.setObjectName(OBJ_GHOST)
        self.open_button = QPushButton("Open…")
        self.open_button.setObjectName(OBJ_GHOST)
        self.export = QPushButton("Export CSV…")
        self.export.setObjectName(OBJ_GHOST)
        self.export.setEnabled(False)
        for widget in (
            self.evaluate_current,
            self.evaluate_timeline,
            self.cancel,
            self.save,
            self.open_button,
            self.export,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.status = QLabel("Load an ensemble sounding, then define one or more conditions.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.timeline = self._table("thresholdTimelineTable")
        self.timeline.setColumnCount(6)
        self.timeline.setHorizontalHeaderLabels(
            ("Valid time", "Qualifying", "Usable", "Loaded", "Requested", "Agreement")
        )
        self.timeline.itemSelectionChanged.connect(self._timeline_selected)
        outer.addWidget(self.timeline, 2)

        self.members = self._table("thresholdMemberTable")
        self.members.setSelectionMode(QAbstractItemView.SingleSelection)
        self.members.cellDoubleClicked.connect(self._member_double_clicked)
        outer.addWidget(self.members, 3)

        self.evaluate_current.clicked.connect(lambda: self._evaluate(False))
        self.evaluate_timeline.clicked.connect(lambda: self._evaluate(True))
        self.cancel.clicked.connect(self.cancel_pending)
        self.save.clicked.connect(self._save)
        self.open_button.clicked.connect(self._open)
        self.export.clicked.connect(self._export)

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

    def refresh(self, collection):
        self._collection = collection
        ledger = EnsembleAcquisition.from_collection(collection) if collection else None
        is_ensemble = bool(
            collection
            and (
                len(getattr(collection, "_profs", {}) or {}) > 1
                or (ledger and len(ledger.requested_members) > 1)
            )
        )
        self.evaluate_current.setEnabled(is_ensemble and self._worker is None)
        self.evaluate_timeline.setEnabled(is_ensemble and self._worker is None)
        if not is_ensemble:
            self.status.setText("The focused sounding is not a multi-member ensemble.")

    def definition(self):
        conditions = []
        for enabled, metric, operator, value, upper in self.condition_rows:
            if not enabled.isChecked():
                continue
            key = str(metric.currentData())
            item = parameter(key)
            op = str(operator.currentData())
            conditions.append(
                ThresholdCondition(
                    key,
                    op,
                    value.value(),
                    upper.value() if op == "between" else None,
                    item.units,
                )
            )
        return ThresholdDefinition(self.name.text(), tuple(conditions))

    def set_definition(self, definition):
        if not isinstance(definition, ThresholdDefinition):
            definition = ThresholdDefinition.from_dict(definition)
        self.name.setText(definition.name)
        for row, controls in enumerate(self.condition_rows):
            enabled, metric, operator, value, upper = controls
            condition = definition.conditions[row] if row < len(definition.conditions) else None
            blocked = enabled.blockSignals(True)
            enabled.setChecked(condition is not None)
            enabled.blockSignals(blocked)
            if condition is None:
                continue
            metric_index = metric.findData(condition.metric)
            if metric_index >= 0:
                metric.setCurrentIndex(metric_index)
            operator_index = operator.findData(condition.operator)
            if operator_index >= 0:
                operator.setCurrentIndex(operator_index)
            value.setValue(condition.value)
            upper.setValue(
                condition.upper if condition.upper is not None else condition.value
            )

    def session_state(self):
        try:
            definition = self.definition().as_dict()
        except Exception:
            definition = None
        return {"definition": definition}

    def restore_session_state(self, state):
        if not isinstance(state, dict) or not isinstance(state.get("definition"), dict):
            return
        try:
            self.set_definition(state["definition"])
        except Exception as exc:  # malformed future/hand-edited state
            self._set_error(f"Threshold definition was not restored: {exc}")

    def _evaluate(self, timeline):
        if self._collection is None or self._worker is not None:
            return
        try:
            definition = self.definition()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._token += 1
        worker = _ThresholdWorker(
            self._token,
            self._collection,
            definition,
            self.engine,
            timeline,
            parent=self,
        )
        self._worker = worker
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.evaluate_current.setEnabled(False)
        self.evaluate_timeline.setEnabled(False)
        self.cancel.setEnabled(True)
        self.status.setObjectName(OBJ_STATUS)
        self.status.setText(
            "Evaluating member-by-member across exact valid times…"
            if timeline
            else "Evaluating members at the focused exact valid time…"
        )
        worker.start()

    def cancel_pending(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            self.status.setText("Cancelling after the current member/time…")

    def shutdown(self):
        worker = self._worker
        if worker is None:
            return
        worker.requestInterruption()
        worker.wait(2000)
        if worker.isRunning():
            from sharpmod.gui_threading import retain_worker_until_finished

            retain_worker_until_finished(worker)
        self._worker = None

    def _completed(self, token, results):
        if int(token) != self._token:
            return
        self._results = tuple(results)
        self._render_results()

    def _failed(self, token, message):
        if int(token) == self._token:
            self._set_error(f"Threshold evaluation failed: {message}")

    def _finished(self):
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self.cancel.setEnabled(False)
        self.refresh(self._collection)

    def _render_results(self):
        self.timeline.setRowCount(len(self._results))
        for row, result in enumerate(self._results):
            agreement = result.agreement_fraction
            values = (
                result.valid_time.strftime("%Y-%m-%d %H:%MZ")
                if isinstance(result.valid_time, datetime)
                else "Unknown",
                str(result.qualifying_member_count),
                str(result.usable_member_count),
                str(result.loaded_member_count),
                str(result.requested_member_count),
                f"{agreement:.1%}" if agreement is not None else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                self.timeline.setItem(row, column, item)
        if self._results:
            self.timeline.selectRow(0)
            result = self._results[0]
            coverage = result.requested_coverage
            self.status.setObjectName(OBJ_STATUS)
            self.status.setText(
                f"{result.qualifying_member_count}/{result.usable_member_count} usable "
                f"members qualify; {result.loaded_member_count}/{result.requested_member_count} "
                f"loaded; usable coverage {coverage:.1%}. {DISCLAIMER}"
                if coverage is not None
                else f"No requested-member denominator is available. {DISCLAIMER}"
            )
        else:
            self.status.setText("Evaluation cancelled before any exact time completed.")
            self.members.setRowCount(0)
        self.export.setEnabled(bool(self._results))

    def _timeline_selected(self):
        row = self.timeline.currentRow()
        if not 0 <= row < len(self._results):
            self.members.setRowCount(0)
            return
        result = self._results[row]
        metrics = result.definition.metrics
        self.members.clear()
        self.members.setColumnCount(2 + len(metrics))
        self.members.setHorizontalHeaderLabels(("Member", "Outcome", *metrics))
        self.members.setRowCount(len(result.members))
        for item_row, member in enumerate(result.members):
            outcome = (
                "Qualifies"
                if member.satisfied is True
                else "Does not qualify"
                if member.satisfied is False
                else "Missing / unusable"
            )
            first = QTableWidgetItem(member.member)
            first.setData(Qt.UserRole, member.member)
            first.setToolTip(member.reason or outcome)
            self.members.setItem(item_row, 0, first)
            self.members.setItem(item_row, 1, QTableWidgetItem(outcome))
            for column, key in enumerate(metrics, start=2):
                value = member.values.get(key)
                self.members.setItem(
                    item_row,
                    column,
                    QTableWidgetItem("—" if value is None else f"{value:g}"),
                )

    def _member_double_clicked(self, row, _column):
        if not 0 <= row < self.members.rowCount():
            return
        item = self.members.item(row, 0)
        timeline_row = self.timeline.currentRow()
        if item is None or not 0 <= timeline_row < len(self._results):
            return
        member = str(item.data(Qt.UserRole) or "")
        if member:
            self.memberActivated.emit(member, self._results[timeline_row].valid_time)

    def _suggested(self, filename):
        try:
            return export_file_path(filename)
        except ExportDirectoryError as exc:
            self._set_error(str(exc))
            return None

    def _save(self):
        try:
            definition = self.definition()
        except Exception as exc:
            self._set_error(str(exc))
            return
        suggested = self._suggested("ensemble-threshold.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Save ensemble threshold",
            str(suggested),
            "JSON files (*.json);;All files (*)",
        )
        if path:
            try:
                saved = write_threshold_results(path, definition, self._results)
            except Exception as exc:
                self._set_error(f"Threshold save failed: {exc}")
            else:
                self.status.setText(f"Saved {saved}")

    def _open(self):
        suggested = self._suggested("ensemble-threshold.json")
        if suggested is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Open ensemble threshold",
            str(Path(suggested).parent),
            "JSON files (*.json);;All files (*)",
        )
        if path:
            try:
                self.set_definition(read_threshold_definition(path))
            except Exception as exc:
                self._set_error(f"Threshold open failed: {exc}")
            else:
                self.status.setText(f"Opened {path}; evaluate to refresh member results.")

    def _export(self):
        if not self._results:
            return
        suggested = self._suggested("ensemble-threshold-results.csv")
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Export ensemble threshold results",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            try:
                saved = export_threshold_csv(path, self._results)
            except Exception as exc:
                self._set_error(f"Threshold export failed: {exc}")
            else:
                self.status.setText(f"Saved {saved}")

    def _set_error(self, message):
        self.status.setObjectName(OBJ_ERROR_TEXT)
        self.status.setText(str(message))
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)


__all__ = ["EnsembleThresholdExplorer"]
