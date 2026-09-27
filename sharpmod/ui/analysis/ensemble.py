"""Ensemble page construction, threshold details, and export."""

from __future__ import annotations

from datetime import datetime, timezone

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFileDialog, QHBoxLayout, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.ui.features.gui_common import action_label
from sharpmod.ui.features.gui_ensemble_thresholds import EnsembleThresholdExplorer
from sharpmod.ui.features.gui_jobs import JobStatus
from sharpmod.analysis.profile_metrics import (
    DEFAULT_METRIC_KEYS, ProfileMetricsEngine, export_ensemble_csv, freeze_collection,
)
from sharpmod.ui.styles.theme import SPACE
from sharpmod.ui.analysis.charts import (
    _EnvelopeChart, _collection_label, _current_date, _format_metric,
    _format_range, _format_time, _get, _metric_title, _number,
)
from sharpmod.ui.analysis.jobs import _EnsembleRequest, _compute_ensemble


class EnsembleWorkspaceMixin:
    """Own the ensemble page and its interactions."""

    def _build_ensemble_tab(self):
        self.ensemble_tab, layout = self._new_page(self.tabs)
        header = QHBoxLayout()
        header.setSpacing(SPACE["sm"])
        self.ensemble_status = self._new_status(self.ensemble_tab)
        self.ensemble_status.setWordWrap(True)
        self.ensemble_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.ensemble_status)
        self.ensemble_job = JobStatus(self.ensemble_tab)
        self.ensemble_job.set_compact_terminal()
        layout.addWidget(self.ensemble_job)
        self.ensemble_job.cancelRequested.connect(lambda: self._cancel_analysis("ensemble"))
        self.ensemble_job.retryRequested.connect(self._retry_ensemble_summary)
        self.ensemble_load = self._new_action(
            "Load ensemble…",
            "Open the existing Forecast Model picker to load an ensemble sounding",
            self.ensemble_tab,
        )
        self.ensemble_load.setHidden(True)
        layout.addWidget(self.ensemble_load)
        self.ensemble_refresh = self._new_action(
            action_label("refresh"),
            "Resummarize the ensemble members",
            self.ensemble_tab,
        )
        header.addWidget(self.ensemble_refresh, 1)
        self.ensemble_retry = self._new_action(
            action_label("retry_unavailable"),
            "Fetch only members that are not already loaded",
            self.ensemble_tab,
        )
        self.ensemble_retry.setEnabled(False)
        header.addWidget(self.ensemble_retry, 1)
        self.ensemble_export = self._new_action(
            action_label("export_csv"),
            "Save distributions, denominators, and member acquisition status",
            self.ensemble_tab,
        )
        self.ensemble_export.setEnabled(False)
        header.addWidget(self.ensemble_export, 1)
        for action in (
            self.ensemble_load,
            self.ensemble_refresh,
            self.ensemble_retry,
            self.ensemble_export,
        ):
            action.setMinimumWidth(0)
            action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            action.setMaximumWidth(150)
        header.addStretch(1)
        layout.addLayout(header)

        self.ensemble_table = self._new_table(
            "analysisEnsembleTable", "Ensemble member distribution values"
        )
        self.ensemble_chart = _EnvelopeChart(self.ensemble_tab)
        # A splitter rather than a hard 50/50 stretch: which half matters
        # depends on the question, and a fixed split left both halves cramped.
        self.ensemble_split = QSplitter(Qt.Vertical, self.ensemble_tab)
        self.ensemble_split.setObjectName("analysisEnsembleSplitter")
        self.ensemble_split.setChildrenCollapsible(False)
        self.ensemble_split.setHandleWidth(SPACE["sm"])
        for title, widget, tip in (
            (
                "Member spread",
                self.ensemble_table,
                "Percentiles across the members available at this valid time",
            ),
            (
                "Vertical envelope",
                self.ensemble_chart,
                "Temperature and dewpoint spread by pressure level",
            ),
        ):
            pane = QWidget(self.ensemble_split)
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.setSpacing(SPACE["xs"])
            heading = self._section(title, pane)
            heading.setToolTip(tip)
            pane_layout.addWidget(heading)
            pane_layout.addWidget(widget, 1)
            self.ensemble_split.addWidget(pane)
        self.ensemble_split.setStretchFactor(0, 3)
        self.ensemble_split.setStretchFactor(1, 4)
        self.ensemble_modes = QTabWidget(self.ensemble_tab)
        self.ensemble_modes.setObjectName("analysisEnsembleModes")
        spread_page = QWidget(self.ensemble_modes)
        spread_layout = QVBoxLayout(spread_page)
        spread_layout.setContentsMargins(0, 0, 0, 0)
        spread_layout.addWidget(self.ensemble_split)
        self.ensemble_modes.addTab(spread_page, "Spread")
        self.threshold_explorer = EnsembleThresholdExplorer(
            self.engine,
            self.ensemble_modes,
            # The explorer knows which members a result could not use; the
            # workspace owns the fetch coordinator. Handing the request across
            # rather than duplicating the coordinator lookup keeps one retry path.
            request_retry=self._retry_threshold_members,
        )
        self.threshold_explorer.memberActivated.connect(
            self._activate_threshold_member
        )
        self.ensemble_modes.addTab(self.threshold_explorer, "Thresholds")
        layout.addWidget(self.ensemble_modes, 1)
        self._add_tab(self.ensemble_tab, "ensemble")

    def _activate_threshold_member(self, member, valid_time):
        collections = self._collections()
        if not collections:
            return
        collection = collections[min(self._focused_index(), len(collections) - 1)]
        try:
            collection.setHighlightedMember(str(member))
            if valid_time is not None:
                collection.setCurrentDate(valid_time)
            win = self._window()
            widget = getattr(win, "spc_widget", None) if win is not None else None
            if widget is not None:
                widget.updateProfs()
        except Exception as exc:  # noqa: BLE001 - upstream collection boundary
            self._set_status(
                self.ensemble_status,
                f"Could not open ensemble member {member}: {exc}",
                level="error",
            )

    def _retry_threshold_members(self, collection, members):
        """Retry the members one threshold result could not use.

        Returns whether a fetch actually started, so the explorer can say what
        happened rather than claiming a retry that never began. The underlying
        coordinator receives the exact stable member IDs the explorer offers.
        """
        if collection is None or not members:
            return False
        win = self._window()
        try:
            controller = getattr(win, "_sharpmod_controller", None) or win.parent()
        except (AttributeError, RuntimeError):
            controller = None
        coordinator = getattr(controller, "_model_compare_coordinator", None)
        retry = getattr(coordinator, "retry_ensemble", None)
        if not callable(retry):
            return False
        return bool(retry(win, collection, tuple(members)))

    def _refresh_ensemble(self, *_args):
        if self.tabs.currentIndex() != self.TAB_ENSEMBLE:
            return
        collections = self._collections()
        if not collections:
            self.cancel_pending()
            self._ensemble_summary = None
            self._ensemble_acquisition = None
            self._ensemble_retained = ""
            self.ensemble_job.hide()
            self.ensemble_export.setEnabled(False)
            self.threshold_explorer.refresh(None)
            self._set_status(
                self.ensemble_status,
                "No soundings are loaded. Load an ensemble sounding to summarize "
                "member spread and thresholds.",
            )
            self._show_recovery(self.ensemble_load, True)
            self.ensemble_table.clear()
            self.ensemble_table.setRowCount(0)
            self.ensemble_chart.set_band(None)
            return
        self._show_recovery(self.ensemble_load, False)
        index = min(self._focused_index(), len(collections) - 1)
        collection = collections[index]
        self.threshold_explorer.refresh(collection)
        ledger = EnsembleAcquisition.from_collection(collection)
        self.ensemble_retry.setEnabled(
            bool(ledger.unavailable_members and ledger.specs_for_retry())
        )
        valid_time = _current_date(collection)
        keys = tuple(DEFAULT_METRIC_KEYS)
        self._set_status(
            self.ensemble_status,
            f"Summarizing members at {_format_time(valid_time)}\u2026",
        )
        self.ensemble_export.setEnabled(self._ensemble_summary is not None)
        frozen = freeze_collection(collection)
        saved_ledger = EnsembleAcquisition.from_collection(frozen)
        members = tuple(dict.fromkeys((*saved_ledger.requested_members, *frozen._profs)))
        label = _collection_label(frozen, index)
        request = _EnsembleRequest(
            frozen, id(collection), keys, valid_time, members, saved_ledger, label,
            f"{label} · valid {_format_time(valid_time)} · {len(members)} member slots"
            f" · {', '.join(members)} · {', '.join(keys)}"
            f" · captured {_format_time(datetime.now(timezone.utc))}",
            isinstance(self.engine, ProfileMetricsEngine)
            and type(self.engine).ensemble is ProfileMetricsEngine.ensemble,
        )
        self._start_ensemble(request)

    def _start_ensemble(self, request):
        engine = self.engine
        self._request("ensemble", lambda cancel, progress: _compute_ensemble(engine, request, cancel, progress),
                      request=request)

    def _retry_unavailable_ensemble(self):
        collections = self._collections()
        if not collections:
            return
        collection = collections[min(self._focused_index(), len(collections) - 1)]
        win = self._window()
        try:
            controller = getattr(win, "_sharpmod_controller", None) or win.parent()
        except (AttributeError, RuntimeError):
            controller = None
        coordinator = getattr(controller, "_model_compare_coordinator", None)
        retry = getattr(coordinator, "retry_ensemble", None)
        if not callable(retry):
            self._set_status(
                self.ensemble_status,
                "Retry is unavailable because this viewer has no forecast-model "
                "controller; the saved member ledger is still intact.",
                level="warn",
            )
            return
        try:
            started = bool(retry(win, collection))
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self._set_status(
                self.ensemble_status,
                f"Could not start member retry: {exc}",
                level="error",
            )
            return
        if started:
            self.ensemble_retry.setEnabled(False)
            self._set_status(
                self.ensemble_status,
                "Retrying unavailable members; already loaded members are not "
                "downloaded again…",
            )

    def _render_ensemble(self, summary, *, request=None):
        selected_row, selected_column = self.ensemble_table.currentRow(), self.ensemble_table.currentColumn()
        horizontal = self.ensemble_table.horizontalScrollBar().value()
        vertical = self.ensemble_table.verticalScrollBar().value()
        self._ensemble_summary = summary
        self._ensemble_acquisition = request.acquisition if request is not None else None
        self.ensemble_export.setEnabled(summary is not None)
        available = bool(_get(summary, "available", False)) if summary else False
        scalar = (_get(summary, "scalar", {}) or {}) if summary else {}
        keys = tuple(DEFAULT_METRIC_KEYS)
        self.ensemble_table.clear()
        self.ensemble_table.setColumnCount(6)
        self.ensemble_table.setHorizontalHeaderLabels(
            ("Parameter", "Count", "p10", "Median", "p90", "Range")
        )
        self.ensemble_table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            distribution = scalar.get(key)
            label, units, _decimals = _metric_title(key)
            if units:
                label = f"{label} ({units})"
            count = _get(distribution, "count", 0) if distribution else 0
            minimum = _number(_get(distribution, "minimum")) if distribution else None
            maximum = _number(_get(distribution, "maximum")) if distribution else None
            self.ensemble_table.setItem(row, 0, self._text_cell(label))
            self.ensemble_table.setItem(
                row,
                1,
                self._figure_cell(
                    str(count),
                    tooltip=(
                        f"{count} member(s) reported this parameter; the "
                        "diagnostic denominator can be smaller than both the "
                        "loaded and requested ensemble"
                    ),
                ),
            )
            figures = (
                (_format_metric(key, _get(distribution, "p10")), "10th percentile"),
                (_format_metric(key, _get(distribution, "median")), "Median member"),
                (_format_metric(key, _get(distribution, "p90")), "90th percentile"),
                (_format_range(key, minimum, maximum), "Lowest to highest member"),
            )
            for offset, (text, tip) in enumerate(figures):
                self.ensemble_table.setItem(
                    row, 2 + offset, self._figure_cell(text, tooltip=tip)
                )
        distribution_available = bool(
            _get(summary, "distribution_available", True)
        ) if summary else False
        band = _get(summary, "bands") if summary and distribution_available else None
        self.ensemble_chart.set_band(band)
        member_count = int(_get(summary, "member_count", 0) or 0) if summary else 0
        used = int(_get(summary, "available_member_count", 0) or 0) if summary else 0
        loaded_value = _get(summary, "loaded_member_count", None) if summary else None
        loaded = int(loaded_value) if loaded_value is not None else used
        missing = tuple(_get(summary, "missing_members", ()) or ()) if summary else ()
        if available:
            text = (
                f"{used} of {member_count} members available for the summary; "
                f"{loaded}/{member_count} loaded"
            )
            if missing:
                text += "; unavailable or unusable: " + ", ".join(
                    str(item) for item in missing
                )
            if not distribution_available:
                text += "; an ensemble distribution needs at least two usable members"
            text += f" at {_format_time(_get(summary, 'valid_time'))}."
        else:
            text = str(
                _get(summary, "reason", None)
                or "This sounding does not contain a usable ensemble."
            )
        # A partial ensemble still renders, but the percentiles rest on fewer
        # members than the run advertises -- flag it rather than reporting it as
        # an ordinary result.
        self._set_status(
            self.ensemble_status,
            text,
            level=(
                "warn"
                if available and (missing or not distribution_available)
                else "info"
            ),
        )
        self._show_recovery(
            self.ensemble_load,
            not available or not distribution_available,
        )
        self._finish_table(self.ensemble_table, 1, stretch=None)
        if 0 <= selected_row < self.ensemble_table.rowCount():
            self.ensemble_table.setCurrentCell(selected_row, max(0, min(selected_column, 5)))
        self.ensemble_table.horizontalScrollBar().setValue(horizontal)
        self.ensemble_table.verticalScrollBar().setValue(vertical)

    def _choose_ensemble_export(self):
        if self._ensemble_summary is None:
            return
        try:
            suggested = export_file_path("ensemble-summary.csv")
        except ExportDirectoryError as exc:
            self._set_status(self.ensemble_status, str(exc), level="error")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export ensemble summary",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            saved = export_ensemble_csv(path, self._ensemble_summary, self._ensemble_acquisition)
        except Exception as exc:  # noqa: BLE001 - GUI export boundary
            self._set_status(
                self.ensemble_status,
                f"Ensemble CSV export failed: {exc}",
                level="error",
            )
        else:
            self._set_status(self.ensemble_status, f"Saved {saved}")
