"""Comparison page construction, table rendering, and export."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QSizePolicy, QSplitter, QVBoxLayout,
)

from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.ui.features.gui_common import action_label
from sharpmod.ui.features.gui_jobs import JobStatus
from sharpmod.ui.features.gui_metric_columns import MetricColumnsDialog, normalize_keys, write_preference
from sharpmod.ui.features.gui_visual_comparison import VisualComparisonWidget
from sharpmod.analysis.profile_metrics import (
    DEFAULT_METRIC_KEYS, ProfileMetricsEngine, export_comparison_csv, freeze_collection,
)
from sharpmod.ui.styles.theme import SPACE
from sharpmod.ui.analysis_controls import bounded_field
from sharpmod.ui.analysis.charts import (
    _MISSING, _collection_label, _comparison_basis_text, _current_date,
    _format_metric, _format_time, _get, _metric_title, _metric_value,
)
from sharpmod.ui.analysis.jobs import _ComparisonRequest, _compute_comparison


class ComparisonWorkspaceMixin:
    """Own the comparison page and its interactions."""

    def _build_compare_tab(self):
        self.compare_tab, layout = self._new_page(self.tabs)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE["xs"])
        header.addWidget(self._section("Reference", self.compare_tab))
        self.compare_reference = QComboBox(self.compare_tab)
        self.compare_reference.setObjectName("analysisComparisonReference")
        self.compare_reference.setToolTip(
            "Every \u0394 column is measured against this sounding"
        )
        self.compare_reference.setAccessibleName("Comparison reference sounding")
        bounded_field(self.compare_reference)
        header.addWidget(self.compare_reference)
        layout.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(SPACE["sm"])
        self.compare_status = self._new_status(self.compare_tab)
        self.compare_status.setWordWrap(True)
        self.compare_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.compare_status)
        self.compare_job = JobStatus(self.compare_tab)
        self.compare_job.set_compact_terminal()
        self.compare_job.cancelRequested.connect(lambda: self._cancel_analysis("compare"))
        self.compare_job.retryRequested.connect(self._retry_comparison)
        layout.addWidget(self.compare_job)
        self.compare_add = self._new_action(
            "Add sounding…",
            "Return to the existing picker and keep the loaded soundings in this viewer",
            self.compare_tab,
        )
        self.compare_add.setHidden(True)
        layout.addWidget(self.compare_add)
        self.compare_refresh = self._new_action(
            action_label("refresh"),
            "Realign and recompute the comparison",
            self.compare_tab,
        )
        self.compare_export = self._new_action(
            action_label("export_csv"),
            "Save the aligned comparison as CSV",
            self.compare_tab,
        )
        self.compare_export.setEnabled(False)
        # Which diagnostics matter depends on what is being analysed, and the
        # table is wide enough that unwanted columns push the relevant ones off
        # screen. The choice and its order are the analyst's.
        self.compare_columns = self._new_action(
            "Columns…",
            "Choose which diagnostics appear as comparison columns, and their order",
            self.compare_tab,
        )
        actions.addWidget(self.compare_columns, 1)
        actions.addWidget(self.compare_refresh, 1)
        actions.addWidget(self.compare_export, 1)
        actions.addStretch(1)
        for action in (
            self.compare_add, self.compare_columns, self.compare_refresh, self.compare_export,
        ):
            action.setMinimumWidth(0)
            action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            action.setMaximumWidth(150)
        layout.addLayout(actions)

        self.compare_table = self._new_table(
            "analysisComparisonTable", "Aligned sounding comparison values"
        )
        self.visual_compare = VisualComparisonWidget(self.compare_tab)
        self.compare_split = QSplitter(Qt.Vertical, self.compare_tab)
        self.compare_split.setObjectName("analysisComparisonSplitter")
        self.compare_split.setChildrenCollapsible(False)
        self.compare_split.setHandleWidth(SPACE["sm"])
        self.compare_split.addWidget(self.visual_compare)
        self.compare_split.addWidget(self.compare_table)
        self.compare_split.setStretchFactor(0, 3)
        self.compare_split.setStretchFactor(1, 2)
        layout.addWidget(self.compare_split, 1)
        self._add_tab(self.compare_tab, "compare")

    def comparison_metric_keys(self):
        """Return the diagnostics the comparison table shows, in order."""
        return tuple(self._comparison_metric_keys)

    def set_comparison_metric_keys(self, keys, *, persist=True):
        """Adopt a validated column order and recompute the comparison."""
        resolved = normalize_keys(keys)
        changed = resolved != tuple(self._comparison_metric_keys)
        self._comparison_metric_keys = resolved
        if persist:
            write_preference(self._settings(), resolved)
        if changed:
            # The engine is asked only for the requested diagnostics, so a
            # newly chosen column has to be computed, not read from stale values.
            self._refresh_compare()
        return resolved

    def _choose_comparison_columns(self):
        dialog = MetricColumnsDialog(
            self.comparison_metric_keys(), parent=self._window() or self
        )
        try:
            if dialog.exec() == QDialog.Accepted:
                self.set_comparison_metric_keys(dialog.selected_keys())
        finally:
            dialog.deleteLater()

    def _comparison_reference_changed(self):
        window = self._window()
        state = getattr(window, "_sharpmod_collection_state", None)
        index = self.compare_reference.currentData()
        ids = getattr(self, "_comparison_ids", ())
        if state is not None and isinstance(index, int) and 0 <= index < len(ids) and state.reference_id != ids[index]:
            state.pin(ids[index])
        else:
            self._refresh_compare()

    def _refresh_compare(self, *_args):
        if self.tabs.currentIndex() != self.TAB_COMPARE:
            return
        collections = self._collections()
        if len(collections) < 2:
            self.cancel_pending()
            self._comparison_samples = ()
            self.compare_table.clear()
            self.compare_table.setRowCount(0)
            self.compare_table.setColumnCount(0)
            self.compare_export.setEnabled(False)
            self.visual_compare.set_collections(
                collections, 0, profile_ids=self._comparison_ids
            )
            count = len(collections)
            explanation = (
                "No soundings are loaded.\n"
                if count == 0
                else "Only one sounding is loaded.\n"
            )
            self._set_status(
                self.compare_status,
                f"{explanation}At least two models or runs must share one valid time.",
            )
            self._show_recovery(self.compare_add, True)
            return
        self._show_recovery(self.compare_add, False)
        reference = self.compare_reference.currentData()
        try:
            reference = int(reference)
            valid_time = _current_date(collections[reference])
        except (IndexError, TypeError, ValueError):
            reference = 0
            valid_time = _current_date(collections[0])
        # Only the chosen diagnostics are requested, so an added column is
        # actually computed and a removed one costs nothing.
        keys = self.comparison_metric_keys()
        self._set_status(
            self.compare_status,
            f"Aligning {len(collections)} soundings at {_format_time(valid_time)}\u2026",
        )
        frozen = tuple(freeze_collection(collection) for collection in collections)
        labels = tuple(_collection_label(item, index) for index, item in enumerate(frozen))
        self._start_comparison(_ComparisonRequest(
            frozen, tuple(id(item) for item in collections), tuple(self._comparison_ids),
            keys, valid_time, reference,
            "; ".join(labels) + f" · reference {labels[reference]} · valid {_format_time(valid_time)}"
            + " · " + ", ".join(keys) + " · captured " + _format_time(datetime.now(timezone.utc)),
            isinstance(self.engine, ProfileMetricsEngine),
        ))

    def _start_comparison(self, request):
        engine = self.engine
        self._request(
            "compare", lambda cancel, progress: _compute_comparison(engine, request, cancel, progress),
            request=request,
        )

    def _render_compare(self, samples, *, request=None):
        current_row, current_column = self.compare_table.currentRow(), self.compare_table.currentColumn()
        selected_index = (
            _get(self._comparison_samples[current_row], "collection_index")
            if 0 <= current_row < len(self._comparison_samples) else None
        )
        vertical = self.compare_table.verticalScrollBar().value()
        horizontal = self.compare_table.horizontalScrollBar().value()
        samples = tuple(samples or ())
        self._comparison_samples = samples
        keys = request.keys if request is not None else self.comparison_metric_keys()
        headers = ["Sounding", "Valid time", "Alignment"]
        for key in keys:
            label, units, _decimals = _metric_title(key)
            headers.extend((f"{label}{f' ({units})' if units else ''}", "\u0394"))
        self.compare_table.clear()
        self.compare_table.setColumnCount(len(headers))
        self.compare_table.setHorizontalHeaderLabels(headers)
        self.compare_table.setRowCount(len(samples))

        reference_index = request.reference if request is not None else self.compare_reference.currentData()
        try:
            reference_index = int(reference_index)
        except (TypeError, ValueError):
            reference_index = 0
        reference = next(
            (
                sample
                for sample in samples
                if int(_get(sample, "collection_index", -1)) == reference_index
            ),
            None,
        )
        reference_label = str(_get(reference, "label", "the reference")) if reference else ""
        self.visual_compare.set_collections(
            request.collections if request is not None else self._collections(),
            reference_index,
            _get(reference, "valid_time"),
            profile_ids=request.profile_ids if request is not None else self._comparison_ids,
        )
        for row, sample in enumerate(samples):
            available = bool(_get(sample, "available", False))
            aligned = bool(_get(sample, "aligned", False))
            reason = _get(sample, "reason")
            is_reference = sample is reference
            relation_label, basis_tooltip = _comparison_basis_text(sample)
            alignment = (
                "Exact" if available and aligned else str(reason or "Unavailable")
            )
            if available and aligned and relation_label != "same location":
                alignment += f" · {relation_label}"
            if is_reference:
                # Name the row the deltas are measured from; otherwise the one
                # column of zeroes is the only hint, and it looks like a result.
                alignment = (
                    "Reference"
                    if available and aligned
                    else f"Reference \u00b7 {alignment}"
                )
            label = str(_get(sample, "label", f"Sounding {row + 1}"))
            self.compare_table.setItem(
                row, 0, self._text_cell(label, emphasis=is_reference)
            )
            self.compare_table.setItem(
                row,
                1,
                self._text_cell(
                    _format_time(_get(sample, "valid_time")),
                    tooltip=_format_time(_get(sample, "requested_time")),
                ),
            )
            self.compare_table.setItem(
                row,
                2,
                self._text_cell(
                    alignment,
                    # Always tooltipped: this column is width-capped, so the
                    # reason a row is unusable is often elided in the cell.
                    tooltip=(
                        f"{alignment}\n{basis_tooltip}"
                        if available and aligned
                        else f"{alignment}\nNo value is shown because the sounding "
                        "could not be aligned to the requested valid time.\n"
                        f"{basis_tooltip}"
                    ),
                ),
            )
            column = 3
            incompatible = dict(
                _get(_get(sample, "basis"), "incompatible_metrics", {}) or {}
            )
            for key in keys:
                metric_label, units, _decimals = _metric_title(key)
                unit_suffix = f" {units}" if units else ""
                value = _metric_value(sample, key) if available and aligned else None
                reference_value = _metric_value(reference, key) if reference else None
                incompatible_reason = incompatible.get(key)
                delta = (
                    value - reference_value
                    if (
                        value is not None
                        and reference_value is not None
                        and not incompatible_reason
                    )
                    else None
                )
                self.compare_table.setItem(
                    row,
                    column,
                    self._figure_cell(
                        _format_metric(key, value),
                        tooltip=f"{label} \u00b7 {metric_label}{unit_suffix}",
                    ),
                )
                if is_reference:
                    # A sounding cannot differ from itself; a printed 0 implies
                    # a measured agreement that was never computed.
                    self.compare_table.setItem(
                        row,
                        column + 1,
                        self._figure_cell(
                            _MISSING, tooltip="This row is the reference"
                        ),
                    )
                else:
                    if incompatible_reason:
                        delta_tip = (
                            f"No {metric_label} delta: {incompatible_reason}. "
                            "Absolute values remain inspectable."
                        )
                    elif delta is None:
                        delta_tip = f"No {metric_label} delta available"
                    else:
                        direction = (
                            "same as" if delta == 0
                            else ("above" if delta > 0 else "below")
                        )
                        delta_tip = (
                            f"{metric_label} is {_format_metric(key, abs(delta))}"
                            f"{unit_suffix} {direction} {reference_label}"
                            if delta
                            else f"{metric_label} matches {reference_label}"
                        )
                    self.compare_table.setItem(
                        row,
                        column + 1,
                        self._delta_cell(key, delta, tooltip=delta_tip),
                    )
                column += 2
        unavailable = sum(
            not bool(_get(sample, "available", False))
            or not bool(_get(sample, "aligned", False))
            for sample in samples
        )
        target = _get(samples[0], "requested_time") if samples else None
        comparable = len(samples) - unavailable
        if not samples:
            status = (
                "The loaded soundings produced no comparison rows.\n"
                f"Add a sounding for {_format_time(target)}"
            )
        elif comparable == 0:
            status = (
                f"None of {len(samples)} loaded soundings align at\n"
                f"{_format_time(target)}. Each mismatch reason is listed below"
            )
        else:
            status = f"Compared {comparable} at {_format_time(target)}"
            if unavailable:
                status += f"; {unavailable} mismatch/unavailable row(s) are explicit"
        self._set_status(self.compare_status, status + ".")
        self.compare_export.setEnabled(bool(samples))
        self._show_recovery(self.compare_add, comparable < 2)
        # 3 prose columns plus two per metric overruns any dock, so the reason
        # text is capped rather than allowed to push the figures off screen.
        self._finish_table(
            self.compare_table, 3, stretch=None, capped=((1, 132), (2, 148))
        )
        for row, sample in enumerate(samples):
            if selected_index is not None and _get(sample, "collection_index") == selected_index:
                self.compare_table.setCurrentCell(row, min(current_column, self.compare_table.columnCount() - 1))
                break
        self.compare_table.verticalScrollBar().setValue(vertical)
        self.compare_table.horizontalScrollBar().setValue(horizontal)

    def _choose_comparison_export(self):
        if not self._comparison_samples:
            return
        try:
            suggested = export_file_path("sounding-comparison.csv")
        except ExportDirectoryError as exc:
            self._set_status(self.compare_status, str(exc), level="error")
            return
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Export sounding comparison",
            str(suggested),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            try:
                saved = self.export_comparison(path)
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self._set_status(
                    self.compare_status, f"CSV export failed: {exc}", level="error"
                )
            else:
                self._set_status(self.compare_status, f"Saved {saved}")

    def export_comparison(self, path):
        """Export the rendered aligned comparison without recomputing it."""
        return export_comparison_csv(
            Path(path), self._comparison_samples, keys=tuple(DEFAULT_METRIC_KEYS)
        )
