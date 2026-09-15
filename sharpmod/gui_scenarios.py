"""Saved hypothetical-profile scenarios and bounded sensitivity experiments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import math
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
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.box_analysis import PARAMETERS, parameter
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.scenarios import (
    Perturbation,
    ProfileSnapshot,
    ScenarioBook,
    ScenarioError,
    read_scenario_book,
    run_sensitivity_grid,
    scenario_collection,
    scenario_from_cell,
    write_scenario_book,
)
from sharpmod.theme import (
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    SPACE,
)


DEFAULT_SCENARIO_METRICS = (
    "mlcape",
    "mlcin",
    "ml_lcl",
    "shear_6km",
    "srh_1km",
    "stp_cin",
)


def highlighted_profile(collection):
    """Return the focused member/profile without choosing a nearby time."""
    if collection is None:
        return None
    profiles = getattr(collection, "_profs", None)
    if not isinstance(profiles, Mapping) or not profiles:
        return None
    member = getattr(collection, "_highlight", None)
    if member not in profiles:
        getter = getattr(collection, "getHighlightedMemberName", None)
        try:
            member = getter() if callable(getter) else None
        except Exception:
            member = None
    if member not in profiles:
        member = next(iter(profiles))
    try:
        index = int(getattr(collection, "_prof_idx", 0))
        return profiles[member][index]
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _json_source(collection) -> dict[str, object]:
    result = {}
    for key, value in dict(getattr(collection, "_meta", {}) or {}).items():
        if isinstance(value, datetime):
            value = value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
    return result


class _MetricWorker(QThread):
    completed = Signal(int, str, object)
    failed = Signal(int, str)

    def __init__(self, token, book, scenario_id, engine, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.book = book
        self.scenario_id = str(scenario_id)
        self.engine = engine

    def run(self):
        try:
            rows = self.book.metric_deltas(
                self.engine, self.scenario_id, DEFAULT_SCENARIO_METRICS
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, self.scenario_id, rows)


class _SensitivityWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int)

    def __init__(
        self,
        token,
        book,
        engine,
        temperatures,
        dewpoints,
        metric,
        depth,
        parent=None,
    ):
        super().__init__(parent)
        self.token = int(token)
        self.book = book
        self.engine = engine
        self.temperatures = tuple(temperatures)
        self.dewpoints = tuple(dewpoints)
        self.metric = str(metric)
        self.depth = float(depth)

    def run(self):
        try:
            result = run_sensitivity_grid(
                self.book,
                self.engine,
                self.temperatures,
                self.dewpoints,
                (self.metric,),
                moisture_depth_m_agl=self.depth,
                cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(self.token, str(exc))
            return
        self.completed.emit(self.token, result)


class ScenarioWorkspace(QWidget):
    """Qt presentation layer around :class:`~sharpmod.scenarios.ScenarioBook`."""

    def __init__(
        self,
        engine,
        *,
        collection_provider: Callable[[], object | None],
        open_collection: Callable[[object], None],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("analysisScenarioWorkspace")
        self.engine = engine
        self._collection_provider = collection_provider
        self._open_collection = open_collection
        self._book = None
        self._baseline_identity = None
        self._selected_id = None
        self._metric_worker = None
        self._sensitivity_worker = None
        self._metric_token = 0
        self._pending_metric = None
        self._sensitivity_token = 0
        self._grid = None
        self._build_ui()

    @property
    def book(self):
        return self._book

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        explainer = QLabel(
            "Named hypothetical modifications always start from one immutable "
            "baseline. Changes are not applied to the original sounding."
        )
        explainer.setObjectName(OBJ_HINT)
        explainer.setWordWrap(True)
        outer.addWidget(explainer)

        top = QHBoxLayout()
        self.scenario = QComboBox()
        self.scenario.setObjectName("scenarioSelector")
        self.scenario.setAccessibleName("Saved sensitivity scenario")
        top.addWidget(self.scenario, 1)
        self.baseline_button = self._button(
            "Use focused as baseline",
            "Start a new scenario book from the exact profile in the viewer",
        )
        self.new_button = self._button("New", "Create an empty named scenario")
        self.duplicate_button = self._button("Duplicate", "Copy the selected scenario")
        self.reset_button = self._button("Reset", "Remove all changes from this scenario")
        self.delete_button = self._button("Delete", "Delete the selected scenario")
        for button in (
            self.baseline_button,
            self.new_button,
            self.duplicate_button,
            self.reset_button,
            self.delete_button,
        ):
            top.addWidget(button)
        outer.addLayout(top)

        self.mode_tabs = QTabWidget()
        self.mode_tabs.setObjectName("scenarioModes")
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        editor_layout.setSpacing(SPACE["sm"])

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setObjectName("scenarioName")
        name_row.addWidget(self.name, 1)
        self.apply_button = QPushButton("Apply changes")
        self.apply_button.setObjectName("scenarioApply")
        name_row.addWidget(self.apply_button)
        editor_layout.addLayout(name_row)

        grid = QGridLayout()
        for column, text in enumerate(("Use", "Change", "Amount", "Layer", "Rule")):
            hint = QLabel(text)
            hint.setObjectName(OBJ_HINT)
            grid.addWidget(hint, 0, column)
        self.surface_temperature = self._change_row(
            grid, 1, "Surface temperature", -30.0, 30.0, "°C", "surface only"
        )
        self.surface_dewpoint = self._change_row(
            grid, 2, "Surface dewpoint", -30.0, 30.0, "°C", "surface only"
        )
        self.moisture = self._layer_row(
            grid,
            3,
            "Moisture layer",
            ("linear-taper", "uniform"),
            bottom_enabled=False,
            defaults=(0.0, 1000.0),
        )
        self.cap = self._layer_row(
            grid,
            4,
            "Cap temperature",
            ("triangle", "uniform"),
            bottom_enabled=True,
            defaults=(500.0, 2000.0),
        )
        self.storm = self._storm_row(grid, 5)
        grid.setColumnStretch(1, 1)
        editor_layout.addLayout(grid)

        self.perturbations = self._table("scenarioPerturbations")
        self.perturbations.setColumnCount(1)
        self.perturbations.setHorizontalHeaderLabels(("Applied perturbation",))
        editor_layout.addWidget(self.perturbations, 1)

        metric_actions = QHBoxLayout()
        self.open_button = QPushButton("Open hypothetical in viewer")
        self.open_button.setObjectName("scenarioOpenInViewer")
        self.open_button.setToolTip(
            "Add a separately labelled hypothetical sounding and open Compare"
        )
        metric_actions.addWidget(self.open_button)
        metric_actions.addStretch(1)
        editor_layout.addLayout(metric_actions)
        self.metrics = self._table("scenarioMetricDeltas")
        self.metrics.setColumnCount(4)
        self.metrics.setHorizontalHeaderLabels(
            ("Diagnostic", "Baseline", "Scenario", "Δ scenario − baseline")
        )
        editor_layout.addWidget(self.metrics, 2)
        self.mode_tabs.addTab(editor, "Scenario")

        sensitivity = QWidget()
        sensitivity_layout = QVBoxLayout(sensitivity)
        sensitivity_layout.setContentsMargins(
            SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"]
        )
        sensitivity_layout.setSpacing(SPACE["sm"])
        controls = QGridLayout()
        self.t_min = self._axis_spin(-2.0)
        self.t_max = self._axis_spin(2.0)
        self.t_step = self._axis_spin(1.0, minimum=0.1)
        self.td_min = self._axis_spin(-2.0)
        self.td_max = self._axis_spin(2.0)
        self.td_step = self._axis_spin(1.0, minimum=0.1)
        self.sensitivity_depth = QSpinBox()
        self.sensitivity_depth.setRange(100, 10000)
        self.sensitivity_depth.setSingleStep(100)
        self.sensitivity_depth.setValue(1000)
        self.sensitivity_depth.setSuffix(" m AGL")
        self.sensitivity_metric = QComboBox()
        for item in PARAMETERS:
            self.sensitivity_metric.addItem(item.label, item.key)
        metric_index = self.sensitivity_metric.findData("mlcape")
        self.sensitivity_metric.setCurrentIndex(max(0, metric_index))
        for row, (text, first, second, third) in enumerate(
            (
                ("Surface T Δ °C", self.t_min, self.t_max, self.t_step),
                ("Moisture-layer Td Δ °C", self.td_min, self.td_max, self.td_step),
            )
        ):
            controls.addWidget(QLabel(text), row, 0)
            controls.addWidget(first, row, 1)
            controls.addWidget(QLabel("to"), row, 2)
            controls.addWidget(second, row, 3)
            controls.addWidget(QLabel("step"), row, 4)
            controls.addWidget(third, row, 5)
        controls.addWidget(QLabel("Moisture depth"), 2, 0)
        controls.addWidget(self.sensitivity_depth, 2, 1, 1, 2)
        controls.addWidget(QLabel("Display diagnostic"), 2, 3)
        controls.addWidget(self.sensitivity_metric, 2, 4, 1, 2)
        sensitivity_layout.addLayout(controls)
        actions = QHBoxLayout()
        self.run_grid = QPushButton("Run bounded grid")
        self.run_grid.setObjectName("scenarioRunSensitivity")
        self.cancel_grid = self._button("Cancel", "Keep completed sensitivity cells")
        self.cancel_grid.setEnabled(False)
        actions.addWidget(self.run_grid)
        actions.addWidget(self.cancel_grid)
        actions.addStretch(1)
        sensitivity_layout.addLayout(actions)
        grid_help = QLabel(
            "Cells show diagnostic change from baseline. Click a completed cell to "
            "create and open its hypothetical scenario; blanks remain unavailable."
        )
        grid_help.setObjectName(OBJ_HINT)
        grid_help.setWordWrap(True)
        sensitivity_layout.addWidget(grid_help)
        self.grid_table = self._table("scenarioSensitivityGrid")
        self.grid_table.setSelectionMode(QAbstractItemView.SingleSelection)
        sensitivity_layout.addWidget(self.grid_table, 1)
        self.mode_tabs.addTab(sensitivity, "T / Td grid")
        outer.addWidget(self.mode_tabs, 1)

        actions = QHBoxLayout()
        self.status = QLabel("Focus a sounding to establish an immutable baseline.")
        self.status.setObjectName(OBJ_STATUS)
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        self.save_button = self._button("Save / export…", "Save portable scenario JSON")
        self.load_button = self._button("Open…", "Open portable scenario JSON")
        actions.addWidget(self.save_button)
        actions.addWidget(self.load_button)
        outer.addLayout(actions)

        self.scenario.currentIndexChanged.connect(self._selection_changed)
        self.baseline_button.clicked.connect(lambda: self._capture_baseline(force=True))
        self.new_button.clicked.connect(self._new)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.reset_button.clicked.connect(self._reset)
        self.delete_button.clicked.connect(self._delete)
        self.apply_button.clicked.connect(self._apply)
        self.open_button.clicked.connect(self._open_selected)
        self.run_grid.clicked.connect(self._run_sensitivity)
        self.cancel_grid.clicked.connect(self._cancel_sensitivity)
        self.grid_table.cellClicked.connect(self._grid_clicked)
        self.save_button.clicked.connect(self._save)
        self.load_button.clicked.connect(self._load)
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

    @staticmethod
    def _delta_spin(minimum=-30.0, maximum=30.0):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" °C")
        return spin

    @classmethod
    def _change_row(cls, grid, row, label, minimum, maximum, units, rule):
        enabled = QCheckBox()
        spin = cls._delta_spin(minimum, maximum)
        grid.addWidget(enabled, row, 0)
        grid.addWidget(QLabel(label), row, 1)
        grid.addWidget(spin, row, 2)
        grid.addWidget(QLabel("surface"), row, 3)
        grid.addWidget(QLabel(rule), row, 4)
        return enabled, spin

    @classmethod
    def _layer_row(cls, grid, row, label, rules, *, bottom_enabled, defaults):
        enabled = QCheckBox()
        amount = cls._delta_spin()
        bottom = QSpinBox()
        bottom.setRange(0, 19900)
        bottom.setSingleStep(100)
        bottom.setValue(int(defaults[0]))
        bottom.setSuffix(" m AGL")
        bottom.setEnabled(bottom_enabled)
        top = QSpinBox()
        top.setRange(100, 20000)
        top.setSingleStep(100)
        top.setValue(int(defaults[1]))
        top.setSuffix(" m AGL")
        layer = QWidget()
        layer_row = QHBoxLayout(layer)
        layer_row.setContentsMargins(0, 0, 0, 0)
        layer_row.setSpacing(SPACE["xs"])
        layer_row.addWidget(bottom)
        layer_row.addWidget(QLabel("–"))
        layer_row.addWidget(top)
        rule = QComboBox()
        for item in rules:
            rule.addItem(item, item)
        grid.addWidget(enabled, row, 0)
        grid.addWidget(QLabel(label), row, 1)
        grid.addWidget(amount, row, 2)
        grid.addWidget(layer, row, 3)
        grid.addWidget(rule, row, 4)
        return enabled, amount, bottom, top, rule

    @staticmethod
    def _storm_row(grid, row):
        enabled = QCheckBox()
        u = QDoubleSpinBox()
        v = QDoubleSpinBox()
        for spin in (u, v):
            spin.setRange(-100.0, 100.0)
            spin.setDecimals(1)
            spin.setSuffix(" kt")
        vector = QWidget()
        vector_row = QHBoxLayout(vector)
        vector_row.setContentsMargins(0, 0, 0, 0)
        vector_row.addWidget(QLabel("u"))
        vector_row.addWidget(u)
        vector_row.addWidget(QLabel("v"))
        vector_row.addWidget(v)
        grid.addWidget(enabled, row, 0)
        grid.addWidget(QLabel("Storm-motion vector"), row, 1)
        grid.addWidget(vector, row, 2, 1, 2)
        grid.addWidget(QLabel("vector delta"), row, 4)
        return enabled, u, v

    @staticmethod
    def _axis_spin(value, *, minimum=-30.0):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 30.0)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setValue(value)
        return spin

    def refresh(self, collection=None):
        collection = collection or self._collection_provider()
        profile = highlighted_profile(collection)
        if profile is None:
            if self._book is None:
                self._set_status("The focused sounding has no usable profile.", "warn")
            self._update_enabled()
            return
        identity = (
            id(collection),
            id(profile),
            getattr(profile, "date", None),
            getattr(collection, "_highlight", None),
        )
        if self._book is not None:
            if identity != self._baseline_identity:
                self._set_status(
                    "The focused profile differs from this scenario book's immutable "
                    "baseline. Choose Use focused as baseline to start over explicitly.",
                    "warn",
                )
            return
        self._capture_baseline(collection=collection, profile=profile, identity=identity)

    def _capture_baseline(
        self, *, force=False, collection=None, profile=None, identity=None
    ):
        collection = collection or self._collection_provider()
        profile = profile or highlighted_profile(collection)
        if profile is None:
            self._set_status("The focused sounding has no usable profile.", "warn")
            return
        identity = identity or (
            id(collection),
            id(profile),
            getattr(profile, "date", None),
            getattr(collection, "_highlight", None),
        )
        if not force and self._book is not None:
            return
        self._metric_token += 1
        self._pending_metric = None
        if self._sensitivity_worker is not None:
            self._sensitivity_worker.requestInterruption()
        try:
            self._book = ScenarioBook(
                ProfileSnapshot.from_profile(profile), source=_json_source(collection)
            )
        except Exception as exc:  # noqa: BLE001 - profile boundary
            self._set_status(f"Could not establish scenario baseline: {exc}", "error")
            self._book = None
            self._baseline_identity = None
        else:
            self._baseline_identity = identity
            self._selected_id = None
            self._grid = None
            self._refresh_scenarios()
            self.grid_table.clear()
            self.grid_table.setRowCount(0)
            self._set_status(
                "Baseline captured from the exact focused profile; create a named "
                "hypothetical scenario to begin."
            )
        self._update_enabled()

    def _refresh_scenarios(self, select_id=None):
        previous = select_id or self._selected_id
        blocked = self.scenario.blockSignals(True)
        self.scenario.clear()
        if self._book is not None:
            for item in self._book.scenarios:
                self.scenario.addItem(item.name, item.scenario_id)
        index = self.scenario.findData(previous)
        self.scenario.setCurrentIndex(index if index >= 0 else (0 if self.scenario.count() else -1))
        self.scenario.blockSignals(blocked)
        self._selection_changed()

    def _selection_changed(self, *_args):
        self._selected_id = self.scenario.currentData()
        if self._book is None or not self._selected_id:
            self.name.clear()
            self._set_controls(())
            self._render_perturbations(())
            self.metrics.setRowCount(0)
            self._update_enabled()
            return
        item = self._book.scenario(self._selected_id)
        self.name.setText(item.name)
        self._set_controls(item.perturbations)
        self._render_perturbations(item.perturbations)
        self._request_metrics(item.scenario_id)
        self._update_enabled()

    def _set_controls(self, changes):
        by_kind = {item.kind: item for item in changes}
        for kind, controls in (
            ("surface_temperature", self.surface_temperature),
            ("surface_dewpoint", self.surface_dewpoint),
        ):
            enabled, amount = controls
            item = by_kind.get(kind)
            enabled.setChecked(item is not None)
            amount.setValue(item.amount if item else 0.0)
        for kind, controls in (("moisture_layer", self.moisture), ("cap_temperature", self.cap)):
            enabled, amount, bottom, top, rule = controls
            item = by_kind.get(kind)
            enabled.setChecked(item is not None)
            amount.setValue(item.amount if item else 0.0)
            if item:
                bottom.setValue(int(item.bottom_m_agl))
                top.setValue(int(item.top_m_agl))
                index = rule.findData(item.interpolation)
                if index >= 0:
                    rule.setCurrentIndex(index)
        enabled, u, v = self.storm
        item = by_kind.get("storm_motion")
        enabled.setChecked(item is not None)
        u.setValue(item.amount if item else 0.0)
        v.setValue(item.secondary_amount if item else 0.0)

    def _changes(self):
        result = []
        if self.surface_temperature[0].isChecked():
            result.append(Perturbation("surface_temperature", self.surface_temperature[1].value(), "degC"))
        if self.surface_dewpoint[0].isChecked():
            result.append(Perturbation("surface_dewpoint", self.surface_dewpoint[1].value(), "degC"))
        for kind, controls in (("moisture_layer", self.moisture), ("cap_temperature", self.cap)):
            enabled, amount, bottom, top, rule = controls
            if enabled.isChecked():
                result.append(
                    Perturbation(
                        kind,
                        amount.value(),
                        "degC",
                        bottom_m_agl=bottom.value(),
                        top_m_agl=top.value(),
                        interpolation=str(rule.currentData()),
                    )
                )
        enabled, u, v = self.storm
        if enabled.isChecked():
            result.append(
                Perturbation(
                    "storm_motion",
                    u.value(),
                    "kt",
                    interpolation="vector-delta",
                    secondary_amount=v.value(),
                )
            )
        return tuple(result)

    def _new(self):
        if self._book is None:
            return
        number = len(self._book.scenarios) + 1
        try:
            item = self._book.create(f"Scenario {number}", ())
        except ScenarioError as exc:
            self._set_status(str(exc), "error")
            return
        self._refresh_scenarios(item.scenario_id)

    def _duplicate(self):
        if self._book is None or not self._selected_id:
            return
        try:
            item = self._book.duplicate(self._selected_id)
        except ScenarioError as exc:
            self._set_status(str(exc), "error")
            return
        self._refresh_scenarios(item.scenario_id)

    def _reset(self):
        if self._book is None or not self._selected_id:
            return
        self._book.reset(self._selected_id)
        self._selection_changed()
        self._set_status("Scenario reset to the immutable baseline.")

    def _delete(self):
        if self._book is None or not self._selected_id:
            return
        self._book.remove(self._selected_id)
        self._selected_id = None
        self._refresh_scenarios()
        self._set_status("Scenario removed; the baseline was not changed.")

    def _apply(self):
        if self._book is None or not self._selected_id:
            return
        try:
            self._book.rename(self._selected_id, self.name.text())
            changed = self._book.replace_perturbations(self._selected_id, self._changes())
        except Exception as exc:  # noqa: BLE001 - validation boundary
            self._set_status(f"Scenario was not changed: {exc}", "error")
            return
        self._refresh_scenarios(changed.scenario_id)
        self._set_status("Saved hypothetical changes; cached diagnostics were invalidated.")

    def _render_perturbations(self, changes):
        self.perturbations.setRowCount(max(1, len(changes)))
        if not changes:
            self.perturbations.setItem(0, 0, QTableWidgetItem("No changes — identical to baseline"))
            return
        for row, item in enumerate(changes):
            self.perturbations.setItem(row, 0, QTableWidgetItem(item.description))

    def _request_metrics(self, scenario_id):
        self._metric_token += 1
        self._pending_metric = (self._metric_token, str(scenario_id))
        self.metrics.setRowCount(0)
        if self._metric_worker is not None:
            return
        self._start_metric_worker()

    def _start_metric_worker(self):
        if self._pending_metric is None or self._book is None:
            return
        token, scenario_id = self._pending_metric
        worker = _MetricWorker(token, self._book, scenario_id, self.engine, self)
        self._metric_worker = worker
        worker.completed.connect(self._metrics_ready)
        worker.failed.connect(self._metric_failed)
        worker.finished.connect(self._metric_finished)
        worker.start()

    def _metrics_ready(self, token, scenario_id, rows):
        if token != self._metric_token or scenario_id != self._selected_id:
            return
        self.metrics.setRowCount(len(rows))
        for row, value in enumerate(rows):
            try:
                label = parameter(value.key).label
            except Exception:
                label = value.key
            figures = (
                label,
                self._format_number(value.baseline),
                self._format_number(value.scenario),
                self._format_number(value.delta, signed=True),
            )
            for column, text in enumerate(figures):
                cell = QTableWidgetItem(text)
                if column:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.metrics.setItem(row, column, cell)

    @staticmethod
    def _format_number(value, *, signed=False):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(value):
            return "—"
        return f"{value:+.1f}" if signed else f"{value:.1f}"

    def _metric_failed(self, token, message):
        if token == self._metric_token:
            self._set_status(f"Scenario calculation failed: {message}", "error")

    def _metric_finished(self):
        worker = self.sender()
        if worker is self._metric_worker:
            self._metric_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        if self._pending_metric is not None and self._pending_metric[0] > worker.token:
            self._start_metric_worker()

    def _open_selected(self):
        if self._book is None or not self._selected_id:
            return
        template = self._collection_provider()
        if template is None:
            self._set_status("A loaded sounding is required as the scenario template.", "error")
            return
        try:
            result = scenario_collection(self._book, self._selected_id, template)
            self._open_collection(result)
        except Exception as exc:  # noqa: BLE001 - viewer boundary
            self._set_status(f"Could not open hypothetical sounding: {exc}", "error")
            return
        self._set_status("Opened a separately labelled hypothetical sounding in Compare.")

    @staticmethod
    def _axis_values(minimum, maximum, step):
        minimum = float(minimum)
        maximum = float(maximum)
        step = float(step)
        if maximum < minimum:
            raise ScenarioError("axis maximum must be at least its minimum")
        count = int(math.floor((maximum - minimum) / step + 1e-9)) + 1
        return tuple(round(minimum + index * step, 6) for index in range(count))

    def _run_sensitivity(self):
        if self._book is None or self._sensitivity_worker is not None:
            return
        try:
            temperatures = self._axis_values(self.t_min.value(), self.t_max.value(), self.t_step.value())
            dewpoints = self._axis_values(self.td_min.value(), self.td_max.value(), self.td_step.value())
            if len(temperatures) * len(dewpoints) > 121:
                raise ScenarioError("sensitivity grid is limited to 121 cells")
        except ScenarioError as exc:
            self._set_status(str(exc), "error")
            return
        self._sensitivity_token += 1
        worker = _SensitivityWorker(
            self._sensitivity_token,
            self._book,
            self.engine,
            temperatures,
            dewpoints,
            self.sensitivity_metric.currentData(),
            self.sensitivity_depth.value(),
            self,
        )
        self._sensitivity_worker = worker
        worker.progress.connect(
            lambda completed, total: self._set_status(
                f"Sensitivity grid: {completed}/{total} cells complete…"
            )
        )
        worker.completed.connect(self._sensitivity_ready)
        worker.failed.connect(self._sensitivity_failed)
        worker.finished.connect(self._sensitivity_finished)
        self.run_grid.setEnabled(False)
        self.cancel_grid.setEnabled(True)
        self._set_status("Running bounded sensitivity grid…")
        worker.start()

    def _cancel_sensitivity(self):
        if self._sensitivity_worker is not None:
            self._sensitivity_worker.requestInterruption()
            self._set_status("Cancelling after the current cell; completed cells will remain usable…")

    def _sensitivity_ready(self, token, grid):
        if token != self._sensitivity_token:
            return
        self._grid = grid
        self._render_grid()
        qualifier = "cancelled; " if grid.cancelled else ""
        self._set_status(
            f"Sensitivity grid {qualifier}{grid.completed_cell_count}/"
            f"{grid.requested_cell_count} cells usable."
        )

    def _sensitivity_failed(self, token, message):
        if token == self._sensitivity_token:
            self._set_status(f"Sensitivity calculation failed: {message}", "error")

    def _sensitivity_finished(self):
        worker = self.sender()
        if worker is self._sensitivity_worker:
            self._sensitivity_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self.cancel_grid.setEnabled(False)
        self.run_grid.setEnabled(self._book is not None)

    def _render_grid(self):
        grid = self._grid
        metric = str(self.sensitivity_metric.currentData())
        temperatures = grid.temperature_deltas_c
        dewpoints = grid.dewpoint_deltas_c
        self.grid_table.clear()
        self.grid_table.setRowCount(len(dewpoints))
        self.grid_table.setColumnCount(len(temperatures))
        self.grid_table.setHorizontalHeaderLabels(tuple(f"T {item:+g}°" for item in temperatures))
        self.grid_table.setVerticalHeaderLabels(tuple(f"Td {item:+g}°" for item in dewpoints))
        for row, dewpoint in enumerate(dewpoints):
            for column, temperature in enumerate(temperatures):
                cell = grid.cell(temperature, dewpoint)
                if cell is None:
                    item = QTableWidgetItem("—")
                    item.setToolTip("Not completed or unavailable")
                else:
                    value = cell.deltas.get(metric)
                    item = QTableWidgetItem(self._format_number(value, signed=True))
                    item.setData(Qt.UserRole, (temperature, dewpoint))
                    item.setToolTip(
                        f"Surface T {temperature:+g} °C; moisture-layer Td "
                        f"{dewpoint:+g} °C through {grid.moisture_depth_m_agl:g} m AGL"
                    )
                item.setTextAlignment(Qt.AlignCenter)
                self.grid_table.setItem(row, column, item)

    def _grid_clicked(self, row, column):
        if self._book is None or self._grid is None:
            return
        item = self.grid_table.item(row, column)
        coordinates = item.data(Qt.UserRole) if item is not None else None
        if not coordinates:
            self._set_status("That sensitivity cell was not completed.", "warn")
            return
        cell = self._grid.cell(*coordinates)
        try:
            scenario = scenario_from_cell(
                self._book,
                cell,
                moisture_depth_m_agl=self._grid.moisture_depth_m_agl,
                name=f"Sensitivity {len(self._book.scenarios) + 1}: T {coordinates[0]:+g} / Td {coordinates[1]:+g}",
            )
        except Exception as exc:  # noqa: BLE001 - validation boundary
            self._set_status(f"Could not create scenario from cell: {exc}", "error")
            return
        self._refresh_scenarios(scenario.scenario_id)
        self.mode_tabs.setCurrentIndex(0)
        self._open_selected()

    def _suggested(self):
        try:
            return export_file_path("sensitivity-scenarios.json")
        except ExportDirectoryError as exc:
            self._set_status(str(exc), "error")
            return None

    def _save(self):
        if self._book is None:
            return
        suggested = self._suggested()
        if suggested is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save sensitivity scenarios", str(suggested), "JSON files (*.json);;All files (*)"
        )
        if path:
            try:
                saved = write_scenario_book(path, self._book)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Scenario export failed: {exc}", "error")
            else:
                self._set_status(f"Saved {saved}")

    def _load(self):
        suggested = self._suggested()
        if suggested is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open sensitivity scenarios", str(Path(suggested).parent), "JSON files (*.json);;All files (*)"
        )
        if path:
            try:
                self._book = read_scenario_book(path)
            except Exception as exc:  # noqa: BLE001 - file boundary
                self._set_status(f"Scenario open failed: {exc}", "error")
                return
            self._baseline_identity = ("opened", str(Path(path).resolve()))
            self._selected_id = None
            self._refresh_scenarios()
            self._set_status(f"Opened {path}; baseline and provenance came from the file.")
            self._update_enabled()

    def session_state(self):
        return {
            "book": self._book.as_dict() if self._book is not None else None,
            "selected": self._selected_id,
            "mode": self.mode_tabs.currentIndex(),
            "grid": {
                "temperature": [self.t_min.value(), self.t_max.value(), self.t_step.value()],
                "dewpoint": [self.td_min.value(), self.td_max.value(), self.td_step.value()],
                "depth_m_agl": self.sensitivity_depth.value(),
                "metric": self.sensitivity_metric.currentData(),
            },
        }

    def restore_session_state(self, state):
        if not isinstance(state, Mapping):
            return
        raw_book = state.get("book")
        if isinstance(raw_book, Mapping):
            try:
                self._book = ScenarioBook.from_dict(raw_book)
            except Exception as exc:  # noqa: BLE001 - session boundary
                self._set_status(f"Scenario session state was not restored: {exc}", "error")
            else:
                self._baseline_identity = ("session", id(self._book))
                self._selected_id = state.get("selected")
                self._refresh_scenarios(self._selected_id)
        try:
            self.mode_tabs.setCurrentIndex(int(state.get("mode", 0)))
        except (TypeError, ValueError):
            pass
        raw_grid = state.get("grid")
        if isinstance(raw_grid, Mapping):
            for key, controls in (("temperature", (self.t_min, self.t_max, self.t_step)), ("dewpoint", (self.td_min, self.td_max, self.td_step))):
                values = raw_grid.get(key)
                if isinstance(values, (list, tuple)) and len(values) == 3:
                    for control, value in zip(controls, values):
                        try:
                            control.setValue(float(value))
                        except (TypeError, ValueError):
                            pass
            try:
                self.sensitivity_depth.setValue(int(raw_grid.get("depth_m_agl", 1000)))
            except (TypeError, ValueError):
                pass
            index = self.sensitivity_metric.findData(raw_grid.get("metric"))
            if index >= 0:
                self.sensitivity_metric.setCurrentIndex(index)
        self._update_enabled()

    def _update_enabled(self):
        has_book = self._book is not None
        has_scenario = has_book and bool(self._selected_id)
        for widget in (
            self.baseline_button,
            self.new_button,
            self.run_grid,
            self.save_button,
        ):
            widget.setEnabled(True if widget is self.baseline_button else has_book)
        for widget in (
            self.duplicate_button,
            self.reset_button,
            self.delete_button,
            self.apply_button,
            self.open_button,
            self.name,
        ):
            widget.setEnabled(has_scenario)

    def _set_status(self, text, level="info"):
        role = {"info": OBJ_STATUS, "warn": OBJ_WARNING_TEXT, "error": OBJ_ERROR_TEXT}.get(level, OBJ_STATUS)
        self.status.setObjectName(role)
        self.status.setText(str(text))
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def shutdown(self):
        self._pending_metric = None
        self._metric_token += 1
        self._sensitivity_token += 1
        for worker in (self._metric_worker, self._sensitivity_worker):
            if worker is None:
                continue
            worker.requestInterruption()
            worker.wait(2000)
            if worker.isRunning():
                from sharpmod.gui_threading import retain_worker_until_finished

                retain_worker_until_finished(worker)
        self._metric_worker = None
        self._sensitivity_worker = None


__all__ = ["ScenarioWorkspace", "highlighted_profile"]
