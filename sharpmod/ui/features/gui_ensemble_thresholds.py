"""Interactive ensemble ingredient-agreement threshold explorer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from qtpy.QtCore import QEvent, QThread, Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sharpmod.analysis.box_analysis import PARAMETERS, parameter
from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from sharpmod.analysis.ensemble_thresholds import (
    DISCLAIMER,
    LOADED_UNUSABLE_STATES,
    MEMBER_FAILED,
    MEMBER_NONQUALIFYING,
    MEMBER_NOT_EVALUATED,
    MEMBER_NOT_LOADED,
    MEMBER_NO_PROFILE,
    MEMBER_QUALIFYING,
    MEMBER_STATES,
    MEMBER_UNUSABLE,
    OPERATORS,
    ThresholdCondition,
    ThresholdDefinition,
    evaluate_threshold,
    evaluate_threshold_timeline,
    export_threshold_csv,
    fraction_text,
    read_threshold_definition,
    write_threshold_results,
)
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.ui.features.gui_common import action_label, make_status_label, set_status_label
from sharpmod.ui.features.gui_jobs import JobCounts, JobStatus
from sharpmod.ui.features.metric_controls import apply_to_spin, control_for
from sharpmod.analysis.profile_metrics import CollectionSnapshot, freeze_collection
from sharpmod.ui.styles.theme import OBJ_GHOST, OBJ_HINT, SPACE


#: Every count in the coverage row, as ``(label, tuple of member states)``, in
#: funnel order. ``()`` means every state.
#:
#: A count a reader cannot act on is only trivia. One filter per count turns each
#: number into a way of seeing the members behind it, and because the filters are
#: defined by state rather than by re-deriving membership, the list under a count
#: and the count itself cannot disagree.
MEMBER_FILTERS = (
    ("All requested members", ()),
    ("Loaded", (MEMBER_QUALIFYING, MEMBER_NONQUALIFYING, *LOADED_UNUSABLE_STATES)),
    ("Usable", (MEMBER_QUALIFYING, MEMBER_NONQUALIFYING)),
    ("Qualifying", (MEMBER_QUALIFYING,)),
    ("Does not qualify", (MEMBER_NONQUALIFYING,)),
    ("Loaded but unusable", tuple(LOADED_UNUSABLE_STATES)),
    ("Not loaded", (MEMBER_NOT_LOADED,)),
)

#: Coverage-row column index to the filter it opens. Column 0 is the valid time
#: and the last two are ratios, which the columns they are built from already cover.
_COLUMN_FILTERS = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}

#: Sentence fragments for the excluded-member tally, so the status line reads as
#: prose rather than as a list of state identifiers.
_EXCLUSION_TALLY = {
    MEMBER_NO_PROFILE: "have no profile at this valid time",
    MEMBER_FAILED: "failed their diagnostic calculation",
    MEMBER_UNUSABLE: "are missing a required diagnostic",
}


@dataclass(frozen=True)
class _ThresholdRequest:
    token: int
    origin_token: int
    collection: CollectionSnapshot
    source_marker: int
    definition: ThresholdDefinition
    timeline: bool
    valid_times: tuple[object, ...]
    total: int
    affected_input: str
    resume: tuple[object, ...] = ()

    @property
    def key(self):
        return self.source_marker, self.origin_token


class _ThresholdWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, int)

    def __init__(self, request, engine, parent=None):
        super().__init__(parent)
        self.request = request
        self.token = int(request.token)
        self.engine = engine

    def run(self):
        request = self.request

        def report(done, total):
            self.progress.emit(request.token, int(done), int(total))

        try:
            if request.timeline:
                result = evaluate_threshold_timeline(
                    request.collection,
                    request.definition,
                    self.engine,
                    valid_times=request.valid_times,
                    cancel=self.isInterruptionRequested,
                    resume=request.resume,
                    progress=report,
                )
            else:
                # The cancel reaches the member loop, so cancelling a single
                # evaluation now keeps the members it already finished instead of
                # running to completion whatever the reader asked for.
                result = (
                    evaluate_threshold(
                        request.collection,
                        request.definition,
                        self.engine,
                        valid_time=request.valid_times[0],
                        cancel=self.isInterruptionRequested,
                        resume=request.resume[0] if request.resume else None,
                        progress=report,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(request.token, str(exc))
            return
        self.completed.emit(request.token, result)


from sharpmod.ui.threshold_evaluation import ThresholdEvaluationMixin
from sharpmod.ui.threshold_results import ThresholdResultsMixin


class EnsembleThresholdExplorer(ThresholdEvaluationMixin, ThresholdResultsMixin, QWidget):
    """Single and joint member-by-member diagnostic threshold analysis."""

    memberActivated = Signal(str, object)

    MAX_CONDITIONS = 3

    #: Coverage-row columns as ``(header, tooltip)``, widest denominator first.
    #: Every header names its own denominator, so no cell on this row is a
    #: percentage a reader has to take on trust.
    TIMELINE_COLUMNS = (
        ("Valid time", "Exact valid time evaluated"),
        ("Requested", "Members in the original ensemble request"),
        ("Loaded", "Requested members that produced a profile"),
        ("Usable", "Loaded members with a value for every required diagnostic"),
        ("Qualifying", "Usable members that satisfy every condition"),
        ("Nonqualifying", "Usable members that fail at least one condition"),
        (
            "Agreement (qualifying / usable)",
            "Qualifying members over usable members. "
            "Ensemble ingredient agreement, not a calibrated probability.",
        ),
        (
            "Coverage (usable / requested)",
            "How much of the requested ensemble this result could actually use",
        ),
    )

    def __init__(self, engine, parent=None, *, request_retry=None):
        super().__init__(parent)
        self.setObjectName("analysisEnsembleThresholdExplorer")
        self.engine = engine
        self._request_retry = request_retry
        self._collection = None
        self._results = ()
        self._result_collection = None
        self._result_request_key = None
        self._worker = None
        self._token = 0
        self._active_request = None
        self._last_request = None
        self._cancel_requested = False
        # The threshold a reader last used for each diagnostic. Switching a
        # condition's diagnostic used to leave the previous number in place, so
        # ``1000 J/kg`` silently became ``1000 kt``; and switching back lost
        # whatever had been typed for the original.
        self._remembered = {}
        #: Which diagnostic each condition row currently holds, so the outgoing
        #: value can be filed under the metric it belonged to.
        self._row_metrics = {}
        self._visible_members = ()
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

        conditions = QVBoxLayout()
        conditions.setSpacing(SPACE["xs"])
        self.condition_rows = []
        defaults = ("mlcape", "shear_6km", "srh_3km")
        for row in range(self.MAX_CONDITIONS):
            condition = QWidget(self)
            condition.setObjectName(f"thresholdConditionRow{row + 1}")
            condition_grid = QGridLayout(condition)
            condition_grid.setContentsMargins(0, 0, 0, 0)
            condition_grid.setHorizontalSpacing(SPACE["sm"])
            condition_grid.setVerticalSpacing(SPACE["xs"])
            heading = QLabel(f"Condition {row + 1}", condition)
            heading.setObjectName(OBJ_HINT)
            enabled = QCheckBox()
            enabled.setObjectName(f"thresholdConditionEnabled{row + 1}")
            enabled.setAccessibleName(f"Use threshold condition {row + 1}")
            enabled.setChecked(row == 0)
            if row == 0:
                enabled.setEnabled(False)
                enabled.setText("Required")
                enabled.setAccessibleName("Use threshold condition 1 (required)")
                enabled.setToolTip("At least one condition is required")
            else:
                enabled.setText("Use condition")
            metric = QComboBox()
            metric.setObjectName(f"thresholdMetric{row + 1}")
            metric.setAccessibleName(f"Condition {row + 1} diagnostic")
            metric.setToolTip("Diagnostic and reader-facing units for this condition")
            metric.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            metric.setMinimumContentsLength(8)
            for item in PARAMETERS:
                metric.addItem(item.display_label, item.key)
            index = metric.findData(defaults[row])
            metric.setCurrentIndex(max(0, index))
            operator = QComboBox()
            operator.setObjectName(f"thresholdOperator{row + 1}")
            operator.setAccessibleName(f"Condition {row + 1} comparison rule")
            operator.setToolTip(
                "Choose a lower, upper, exact, or bounded comparison"
            )
            # OPERATORS is intentionally a set in the pure validation layer;
            # keep the control's order stable and put the common comparisons
            # first for keyboard users and deterministic sessions/screenshots.
            for item in (">", ">=", "<", "<=", "==", "between"):
                operator.addItem(item, item)
            operator.setCurrentIndex(max(0, operator.findData(">=")))
            # Bounds, step, and precision come from the diagnostic, not from one
            # hardcoded range shared by every diagnostic in the registry.
            value = QDoubleSpinBox()
            value.setObjectName(f"thresholdValue{row + 1}")
            value.setAccessibleName(f"Condition {row + 1} threshold value")
            upper = QDoubleSpinBox()
            upper.setObjectName(f"thresholdUpper{row + 1}")
            upper.setAccessibleName(f"Condition {row + 1} upper threshold")
            upper.setEnabled(False)
            for control in (metric, operator, value, upper):
                control.setMinimumWidth(0)
                control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            operator.currentIndexChanged.connect(
                lambda _index, combo=operator, spin=upper: spin.setEnabled(
                    combo.currentData() == "between"
                )
            )
            metric.currentIndexChanged.connect(
                lambda _index, index=row: self._metric_changed(index)
            )
            value.valueChanged.connect(lambda _value, index=row: self._remember(index))
            self._row_metrics[row] = str(metric.currentData())
            self._apply_metric_control(row, metric, value, upper)
            from sharpmod.ui.features.gui_selectors import selector_search_button

            search = selector_search_button(metric)
            condition_grid.addWidget(heading, 0, 0)
            condition_grid.addWidget(enabled, 0, 1, 1, 2)
            for grid_row, (label_text, control) in enumerate(
                (
                    ("Diagnostic", metric),
                    ("Rule", operator),
                    ("Value", value),
                    ("Upper", upper),
                ),
                start=1,
            ):
                label = QLabel(label_text, condition)
                label.setObjectName(OBJ_HINT)
                condition_grid.addWidget(label, grid_row, 0)
                condition_grid.addWidget(control, grid_row, 1)
                if control is metric:
                    condition_grid.addWidget(search, grid_row, 2)
            condition_grid.setColumnStretch(1, 1)
            conditions.addWidget(condition)
            self.condition_rows.append((enabled, metric, operator, value, upper))
        outer.addLayout(conditions)

        # What the definition actually tests, in the reader's own units. The
        # definition was identified only by the name someone typed, so a restored
        # or opened "Ingredient agreement" said nothing about its own contents.
        self.definition_summary = QLabel("")
        self.definition_summary.setObjectName(OBJ_HINT)
        self.definition_summary.setWordWrap(True)
        self.definition_summary.setAccessibleName("Threshold rule in words")
        outer.addWidget(self.definition_summary)

        actions = QGridLayout()
        self.evaluate_current = QPushButton("Evaluate current")
        self.evaluate_current.setObjectName("thresholdEvaluateCurrent")
        self.evaluate_current.setShortcut("Ctrl+Return")
        self.evaluate_current.setToolTip(
            "Evaluate the focused valid time (Ctrl+Enter)"
        )
        self.evaluate_timeline = QPushButton("Evaluate timeline")
        self.evaluate_timeline.setObjectName("thresholdEvaluateTimeline")
        self.evaluate_timeline.setShortcut("Ctrl+Shift+Return")
        self.evaluate_timeline.setToolTip(
            "Evaluate every exact valid time (Ctrl+Shift+Enter)"
        )
        self.job = JobStatus(self)
        self.cancel = self.job.cancel_button
        self.cancel.setObjectName("thresholdCancelEvaluation")
        self.job.retry_button.setObjectName("thresholdRetryEvaluation")
        self.job.retry_button.setAccessibleName(
            "Retry missing or failed threshold evaluations"
        )
        # A count of members that never loaded is only useful if something can be
        # done about it. This reuses the existing targeted-retry path rather than
        # starting a second one.
        self.retry = QPushButton(action_label("retry_unavailable"))
        self.retry.setObjectName("thresholdRetryUnavailable")
        self.retry.setToolTip(
            "Fetch only the members this result could not use because they never "
            "loaded; members already loaded are not downloaded again"
        )
        self.retry.setEnabled(False)
        self.save = QPushButton(action_label("save"))
        self.save.setObjectName(OBJ_GHOST)
        self.open_button = QPushButton(action_label("open"))
        self.open_button.setObjectName(OBJ_GHOST)
        self.export = QPushButton(action_label("export_csv"))
        self.export.setObjectName(OBJ_GHOST)
        self.export.setEnabled(False)
        action_widgets = (
            self.evaluate_current,
            self.evaluate_timeline,
            self.retry,
            self.save,
            self.open_button,
            self.export,
        )
        for index, widget in enumerate(action_widgets):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            actions.addWidget(widget, index // 2, index % 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        outer.addLayout(actions)
        outer.addWidget(self.job)

        self.status = make_status_label(
            "Load an ensemble sounding, then define one or more conditions.",
            parent=self,
        )
        outer.addWidget(self.status)

        self.timeline = self._table("thresholdTimelineTable")
        self.timeline.setAccessibleName("Threshold coverage by valid time")
        self.timeline.setAccessibleDescription(
            "Read-only member counts in funnel order, then two ratios that print "
            "their own denominators. Select a row to inspect its members, or "
            "select a count to filter the member list to it."
        )
        # Funnel order, widest denominator first. The previous order started at
        # the narrowest count and worked outwards, which is the opposite of how the
        # numbers are read: each one is a subset of the one before it.
        self.timeline.setColumnCount(len(self.TIMELINE_COLUMNS))
        self.timeline.setHorizontalHeaderLabels(
            tuple(label for label, _tip in self.TIMELINE_COLUMNS)
        )
        for column, (_label, tip) in enumerate(self.TIMELINE_COLUMNS):
            header = self.timeline.horizontalHeaderItem(column)
            if header is not None:
                header.setToolTip(tip)
        # Programmatic row moves and keyboard navigation can change the current
        # row without changing Qt's selection (for example after a shortcut).
        # The status and member details follow the current row, so one signal
        # also avoids rendering twice for an ordinary click.
        self.timeline.currentCellChanged.connect(
            lambda row, _column, previous_row, _previous_column:
            self._timeline_selected() if row != previous_row else None
        )
        self.timeline.cellClicked.connect(self._timeline_cell_clicked)
        outer.addWidget(self.timeline, 2)

        member_row = QGridLayout()
        member_row.addWidget(QLabel("Show"), 0, 0)
        self.member_filter = QComboBox()
        self.member_filter.setObjectName("thresholdMemberFilter")
        self.member_filter.setAccessibleName("Member outcome filter")
        self.member_filter.setToolTip(
            "Narrow the member list to one outcome group; selecting a count above "
            "does the same thing"
        )
        for label, states in MEMBER_FILTERS:
            self.member_filter.addItem(label, states)
        self.member_filter.setMinimumWidth(0)
        self.member_filter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        member_row.addWidget(self.member_filter, 0, 1)
        self.member_count = QLabel("")
        self.member_count.setObjectName(OBJ_HINT)
        self.member_count.setAccessibleName("Members shown")
        self.member_count.setWordWrap(True)
        self.member_count.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        member_row.addWidget(self.member_count, 1, 0, 1, 2)
        # Double-click was the only way in, which is unreachable from the keyboard.
        self.open_member = QPushButton("Open member")
        self.open_member.setObjectName("thresholdOpenMember")
        self.open_member.setToolTip(
            "Show the selected member's own profile in the sounding window"
        )
        self.open_member.setEnabled(False)
        self.open_member.setMinimumWidth(0)
        self.open_member.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        member_row.addWidget(self.open_member, 2, 1)
        member_row.setColumnStretch(1, 1)
        outer.addLayout(member_row)

        self.members = self._table("thresholdMemberTable")
        self.members.setAccessibleName("Threshold outcome by ensemble member")
        self.members.setAccessibleDescription(
            "Read-only member outcomes, the reason any member was excluded, and "
            "numeric diagnostics. Select a member and choose Open member, or "
            "double-click it, to show its own profile."
        )
        self.members.setSelectionMode(QAbstractItemView.SingleSelection)
        self.members.cellDoubleClicked.connect(self._member_double_clicked)
        self.members.itemSelectionChanged.connect(self._member_selection_changed)
        outer.addWidget(self.members, 3)

        self.evaluate_current.clicked.connect(lambda: self._evaluate(False))
        self.evaluate_timeline.clicked.connect(lambda: self._evaluate(True))
        self.job.cancelRequested.connect(self.cancel_pending)
        self.job.retryRequested.connect(self._retry_evaluation)
        self.retry.clicked.connect(self._retry_unavailable)
        self.save.clicked.connect(self._save)
        self.open_button.clicked.connect(self._open)
        self.export.clicked.connect(self._export)
        self.name.textChanged.connect(lambda _text: self._refresh_definition_summary())
        for enabled, _metric, operator, _value, _upper in self.condition_rows:
            enabled.toggled.connect(lambda _on: self._refresh_definition_summary())
            operator.currentIndexChanged.connect(
                lambda _index: self._refresh_definition_summary()
            )
        self.member_filter.currentIndexChanged.connect(lambda _index: self._render_members())
        self.open_member.clicked.connect(self._open_selected_member)
        self._refresh_definition_summary()
        self._establish_focus_order()
        self._fit_result_tables()

    def _apply_metric_control(self, row, metric=None, lower=None, upper=None, *, value=None):
        """Configure one condition row's numeric controls for its diagnostic.

        Units, precision, step, and bounds all come from the registry-derived
        profile. Previously only the suffix and the assistive text followed the
        diagnostic, so a control labelled ``kt`` still accepted ninety thousand in
        steps of one to a tenth of a knot.
        """
        if metric is None:
            _enabled, metric, _operator, lower, upper = self.condition_rows[row]
        key = str(metric.currentData())
        control = control_for(key)
        wanted = self._remembered.get(key) if value is None else value
        blocked = lower.blockSignals(True)
        try:
            applied = apply_to_spin(lower, control, value=wanted)
        finally:
            lower.blockSignals(blocked)
        apply_to_spin(upper, control, value=control.upper_for(applied))
        self._remembered[key] = applied
        self._row_metrics[row] = key
        description = control.describe()
        lower.setAccessibleDescription(description)
        lower.setToolTip(description)
        upper.setAccessibleDescription(f"Upper bound. {description}")
        upper.setToolTip(f"Upper bound of a between rule. {description}")
        return control

    def _metric_changed(self, row):
        """File the outgoing threshold under its own diagnostic, then re-configure.

        Without this the number stayed put while the units label changed
        underneath it, which is the one way a threshold control can be actively
        misleading rather than merely inconvenient.
        """
        _enabled, metric, _operator, lower, upper = self.condition_rows[row]
        previous = self._row_metrics.get(row)
        if previous and previous != str(metric.currentData()):
            self._remembered[previous] = lower.value()
        self._apply_metric_control(row, metric, lower, upper)
        self._refresh_definition_summary()

    def _remember(self, row):
        """Record a typed threshold against the diagnostic it was typed for."""
        key = self._row_metrics.get(row)
        if key:
            self._remembered[key] = self.condition_rows[row][3].value()
        self._refresh_definition_summary()

    def _establish_focus_order(self):
        widgets = [self.name]
        for controls in self.condition_rows:
            widgets.extend(controls)
        widgets.extend(
            (
                self.evaluate_current,
                self.evaluate_timeline,
                self.cancel,
                self.retry,
                self.save,
                self.open_button,
                self.export,
                self.timeline,
                self.member_filter,
                self.open_member,
                self.members,
            )
        )
        for previous, following in zip(widgets, widgets[1:]):
            QWidget.setTabOrder(previous, following)

    @staticmethod
    def _table(name):
        table = QTableWidget()
        table.setObjectName(name)
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _fit_result_tables(self):
        """Keep actual rows visible when enlarged text consumes the upper form.

        The analysis page itself is scrollable, so growing to a truthful minimum
        is preferable to squeezing both tables down to their headers.  Reserve one
        timeline row and up to three member rows; the tables retain their native
        scroll bars for everything beyond that.
        """
        for table, wanted_rows in (
            (getattr(self, "timeline", None), 1),
            (getattr(self, "members", None), 3),
        ):
            if table is None:
                continue
            text_height = table.fontMetrics().height()
            row_height = max(
                table.verticalHeader().minimumSectionSize(),
                text_height + SPACE["xs"] * 2,
            )
            table.verticalHeader().setDefaultSectionSize(row_height)
            rows = max(1, min(int(wanted_rows), max(1, table.rowCount())))
            header_height = max(
                table.horizontalHeader().sizeHint().height(),
                text_height + SPACE["xs"] * 2,
            )
            scroll_height = table.horizontalScrollBar().sizeHint().height()
            table.setMinimumHeight(
                header_height
                + rows * row_height
                + scroll_height
                + table.frameWidth() * 2
                + SPACE["xs"]
            )

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            self._fit_result_tables()

    def refresh(self, collection):
        changed = collection is not self._collection
        if changed:
            self._token += 1
            if self._worker is not None:
                self._worker.requestInterruption()
                # Results emitted by the old collection are stale even if the
                # worker reaches its signal before interruption takes effect.
            if self.job.snapshot is not None:
                self.job.invalidate(
                    "The ensemble input changed; the earlier threshold request was superseded."
                )
            self._active_request = None
            self._last_request = None
            self._cancel_requested = False
            self._clear_results()
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
            set_status_label(
                self.status, "The focused sounding is not a multi-member ensemble."
            )
        elif changed or not self._results:
            set_status_label(
                self.status,
                f"Ready to evaluate: {ledger.loaded_count}/{ledger.requested_count} "
                "requested ensemble members are loaded. Define one or more "
                f"conditions. {DISCLAIMER}",
            )

    def _clear_results(self):
        """Remove results whose collection identity no longer matches the input."""
        self._results = ()
        self._result_collection = None
        self._result_request_key = None
        self.timeline.setRowCount(0)
        self.members.clear()
        self.members.setRowCount(0)
        self.members.setColumnCount(0)
        self.member_count.clear()
        self._visible_members = ()
        self.export.setEnabled(False)
        self.retry.setEnabled(False)
        self.open_member.setEnabled(False)
        self._fit_result_tables()

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
            # Signals stay blocked while the combos move. Letting them fire would
            # run the interactive metric-change handler, which files the *current*
            # spin value under the outgoing diagnostic -- and during a restore that
            # current value is a freshly built default, so it overwrote the
            # remembered threshold this restore had just loaded.
            blocked_metric = metric.blockSignals(True)
            blocked_operator = operator.blockSignals(True)
            try:
                metric_index = metric.findData(condition.metric)
                if metric_index >= 0:
                    metric.setCurrentIndex(metric_index)
                operator_index = operator.findData(condition.operator)
                if operator_index >= 0:
                    operator.setCurrentIndex(operator_index)
            finally:
                metric.blockSignals(blocked_metric)
                operator.blockSignals(blocked_operator)
            # Configure the controls for the incoming diagnostic before writing its
            # value, or the previous diagnostic's bounds and precision silently
            # clamp or round a perfectly valid stored threshold.
            self._apply_metric_control(
                row, metric, value, upper, value=condition.value
            )
            is_between = condition.operator == "between"
            upper.setEnabled(is_between)
            if condition.upper is not None:
                upper.setValue(control_for(condition.metric).clamp(condition.upper))
        self._refresh_definition_summary()

    def session_state(self):
        try:
            definition = self.definition().as_dict()
        except Exception:
            definition = None
        return {
            "definition": definition,
            # Thresholds for diagnostics that are not in the current definition.
            # Without these, a reader who compares a CAPE gate against a shear gate
            # retypes one of them every time they switch back.
            "metric_values": {
                str(key): float(value) for key, value in self._remembered.items()
            },
            "member_filter": int(self.member_filter.currentIndex()),
        }

    def restore_session_state(self, state):
        if not isinstance(state, dict):
            return
        remembered = state.get("metric_values")
        if isinstance(remembered, dict):
            for key, value in remembered.items():
                try:
                    self._remembered[str(key)] = control_for(str(key)).clamp(value)
                except (TypeError, ValueError):
                    continue
        index = state.get("member_filter")
        try:
            if 0 <= int(index) < self.member_filter.count():
                self.member_filter.setCurrentIndex(int(index))
        except (TypeError, ValueError):
            pass
        if not isinstance(state.get("definition"), dict):
            # Remembered thresholds still apply, so a session carrying only those
            # is restored rather than discarded wholesale.
            for row in range(len(self.condition_rows)):
                self._apply_metric_control(row)
            self._refresh_definition_summary()
            return
        try:
            self.set_definition(state["definition"])
        except Exception as exc:  # malformed future/hand-edited state
            self._set_error(f"Threshold definition was not restored: {exc}")


























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
                set_status_label(self.status, f"Saved {saved}")

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
                set_status_label(
                    self.status,
                    f"Opened {path}; evaluate to refresh member results.",
                )

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
                set_status_label(self.status, f"Saved {saved}")

    def _set_error(self, message):
        set_status_label(self.status, message, level="error")


__all__ = ["EnsembleThresholdExplorer"]
