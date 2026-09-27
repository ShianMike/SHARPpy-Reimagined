"""Qt layer for box soundings: extraction, analysis, and the workspace window.

Three pieces, split along the thread boundary:

:class:`BoxExtractWorker`
    Runs the bounded :mod:`sharpmod.analysis.batch_extract` job for a sample plan. Every
    node shares one model hour, so this is a single download followed by a bulk
    decode, and completed nodes stream back as they land.

:class:`BoxAnalysisWorker`
    Turns extracted ``.npz`` files into scalar fields. Kept off the UI thread
    because the SPC composite tier costs roughly 0.4 s per node -- minutes for a
    large box -- and would otherwise freeze the window.

:class:`BoxAnalysisWindow`
    The workspace: a field map, area statistics, a ranked node list, and a
    vertical cross-section, with any cell openable as a full Skew-T.

The window never composes a sounding viewer itself. It emits
:attr:`BoxAnalysisWindow.soundingRequested` and lets the picker -- which already
owns viewer composition, themes, and window bookkeeping -- do it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping

from qtpy.QtCore import Qt, QThread, QTimer, QUrl, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.analysis import box_analysis as _analysis
from sharpmod.analysis.box_sounding import (
    MAX_BOX_HOURS,
    MAX_BOX_POINTS,
    MAX_POINT_PROVIDER_POINTS,
    MAX_SEQUENCE_NODES,
    BoxSamplePlan,
    box_requests,
    box_sequence_requests,
    describe_plan,
    normalize_hours,
    plan_box_samples,
    sequence_request_id,
)
from sharpmod.ui.features.gui_theme import mono_font, ui_font
from sharpmod.export_paths import (
    ExportDirectoryError,
    export_directory,
    export_file_path,
)


__all__ = [
    "BoxAnalysisWindow",
    "BoxAnalysisWorker",
    "BoxExtractResult",
    "BoxExtractWorker",
    "BoxMeanWorker",
    "BoxPlanDialog",
]

#: How many nodes the ranked list shows. Long enough to find the interesting
#: corner of a box, short enough to read at a glance.
RANKED_ROWS = 12

#: Columns the spread view summarizes. Temperature and dewpoint together are how
#: a sounding is read, and their envelopes overlaid show both the thermal spread
#: and the moisture spread that a single field cannot.
#:
#: Retained for :meth:`sharpmod.analysis.box_analysis.BoxAnalysis.envelope` callers; the
#: workspace no longer draws a spread panel.
ENVELOPE_FIELDS = ("tmpc", "dwpc")


@dataclass(frozen=True)
class BoxExtractResult:
    """What a finished box extraction produced, at one hour or several."""

    plan: BoxSamplePlan
    #: ``request_id`` -> written ``.npz`` path for the primary hour, completed
    #: nodes only. Kept as the single-hour view even for a sequence.
    outputs: Mapping[str, str] = field(default_factory=dict)
    run_time: object = None
    valid_time: object = None
    fxx: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    output_dir: str = ""
    hours: tuple[int, ...] = ()
    #: forecast hour -> that hour's ``request_id`` -> path.
    outputs_by_hour: Mapping[int, Mapping[str, str]] = field(
        default_factory=dict)

    def __post_init__(self):
        # A one-hour run is a one-hour sequence. Deriving the per-hour view here
        # means every consumer can read `outputs_by_hour` without caring which
        # kind of run produced it.
        if not self.outputs_by_hour and self.outputs:
            object.__setattr__(
                self, "outputs_by_hour",
                {int(self.fxx): dict(self.outputs)})
        if not self.hours:
            object.__setattr__(
                self, "hours",
                tuple(sorted(self.outputs_by_hour)) or (int(self.fxx),))

    @property
    def ok(self) -> bool:
        return self.completed > 0

    @property
    def sequence(self) -> bool:
        """Whether this run covers more than one forecast hour."""
        return len(self.hours) > 1

    def outputs_for(self, hour) -> Mapping[str, str]:
        """Return one hour's outputs, or an empty mapping."""
        try:
            return self.outputs_by_hour.get(int(hour), {})
        except (TypeError, ValueError):
            return {}










class BoxAnalysisWindow(QWidget):
    """Field map, statistics, ranked nodes, and a cross-section for one box."""

    soundingRequested = Signal(str, str)   # npz path, label
    compositesRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Box sounding analysis")
        self._analysis = None
        self._sequence = None
        self._extraction: BoxExtractResult | None = None
        self._ranked_keys: list[tuple[int, int]] = []

        from sharpmod.ui.features.gui_maps import BoxFieldMapWidget

        root = QVBoxLayout(self)

        # -- controls ------------------------------------------------------- #
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Field"))
        self._field_combo = QComboBox()
        self._field_combo.setMinimumWidth(220)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        controls.addWidget(self._field_combo)
        from sharpmod.ui.features.gui_selectors import selector_search_button

        controls.addWidget(selector_search_button(self._field_combo, name="box parameter"))

        self._values_check = QCheckBox("Values")
        self._values_check.setChecked(True)
        self._values_check.setToolTip(
            "Label each cell with its value when the cells are large enough.")
        self._values_check.toggled.connect(self._on_values_toggled)
        controls.addWidget(self._values_check)

        self._composites_button = QPushButton("Add SPC composites")
        self._composites_button.setToolTip(
            "Compute STP, SCP, SHIP and the other composite indices. These "
            "need the full SHARPpy parcel surface, about 0.4 s per sounding."
        )
        self._composites_button.clicked.connect(self.compositesRequested.emit)
        controls.addWidget(self._composites_button)

        controls.addSpacing(12)
        controls.addWidget(QLabel("Ingredients"))
        self._screen_combo = QComboBox()
        self._screen_combo.setMinimumWidth(180)
        self._screen_combo.setToolTip(
            "Hatch the grid points where every ingredient of a mode holds at "
            "once, and report how much of the box qualifies.\n"
            "These are screening heuristics for narrowing attention, not "
            "official products and not a forecast."
        )
        self._screen_combo.currentIndexChanged.connect(self._on_screen_changed)
        controls.addWidget(self._screen_combo)

        controls.addStretch(1)
        self._export_button = QToolButton()
        self._export_button.setText("Export")
        self._export_button.setPopupMode(QToolButton.InstantPopup)
        export_menu = QMenu(self._export_button)
        export_menu.addAction(
            "Field map as PNG\u2026", self.export_field_png)
        export_menu.addAction("Values as CSV\u2026", self.export_csv)
        export_menu.addAction(
            "Sampled cells as GeoJSON\u2026", self.export_geojson)
        export_menu.addSeparator()
        export_menu.addAction("Open Export Folder", self.open_export_folder)
        self._export_button.setMenu(export_menu)
        self._export_button.setToolTip(
            "Save the field map, the per-point values, or the sampled cells "
            "for GIS.")
        controls.addWidget(self._export_button)
        root.addLayout(controls)

        # -- map, side panel, cross-section --------------------------------- #
        self._map = BoxFieldMapWidget()
        self._map.cellSelected.connect(self._on_cell_selected)
        self._map.cellActivated.connect(self._on_cell_activated)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)

        stats_box = QGroupBox("Across the box")
        stats_grid = QGridLayout(stats_box)
        self._stat_labels = {}
        for row, (key, caption) in enumerate((
            ("minimum", "Min"),
            ("mean", "Mean"),
            ("median", "Median"),
            ("maximum", "Max"),
            ("spread", "Spread"),
            ("extreme", "Extreme at"),
            ("count", "Soundings"),
        )):
            caption_label = QLabel(caption)
            caption_label.setFont(ui_font("caption"))
            value_label = QLabel("\u2014")
            value_label.setFont(mono_font("body"))
            stats_grid.addWidget(caption_label, row, 0)
            stats_grid.addWidget(value_label, row, 1)
            self._stat_labels[key] = value_label
        side_layout.addWidget(stats_box)

        self._coverage_box = QGroupBox("Ingredient overlap")
        coverage_layout = QVBoxLayout(self._coverage_box)
        self._coverage_criteria = QLabel("")
        self._coverage_criteria.setFont(ui_font("caption"))
        self._coverage_criteria.setWordWrap(True)
        coverage_layout.addWidget(self._coverage_criteria)
        self._coverage_value = QLabel("\u2014")
        self._coverage_value.setFont(mono_font("body"))
        self._coverage_value.setWordWrap(True)
        coverage_layout.addWidget(self._coverage_value)
        self._coverage_box.hide()
        side_layout.addWidget(self._coverage_box)

        ranked_box = QGroupBox(f"Most significant {RANKED_ROWS}")
        ranked_layout = QVBoxLayout(ranked_box)
        self._ranked = QTableWidget(0, 3)
        self._ranked.setHorizontalHeaderLabels(["Value", "Lat", "Lon"])
        self._ranked.verticalHeader().setVisible(False)
        self._ranked.setSelectionBehavior(QTableWidget.SelectRows)
        self._ranked.setEditTriggers(QTableWidget.NoEditTriggers)
        self._ranked.setFont(mono_font("caption"))
        self._ranked.itemSelectionChanged.connect(self._on_ranked_selected)
        self._ranked.itemDoubleClicked.connect(self._on_ranked_activated)
        ranked_layout.addWidget(self._ranked)
        open_button = QPushButton("Open sounding")
        open_button.setToolTip(
            "Open the selected grid point as a full Skew-T workspace "
            "(or double-click a cell on the map).")
        open_button.clicked.connect(self._open_selected)
        ranked_layout.addWidget(open_button)
        side_layout.addWidget(ranked_box)
        side_layout.addStretch(1)

        # The field map is what this window is for, so it gets the whole height.
        # A cross-section and a spread band used to sit under it; both were
        # vertical plots drawn on a plain linear axis, which read as broken
        # next to the application's own Skew-T, and neither answered a question
        # the field map and the mean sounding do not answer better.
        upper = QSplitter(Qt.Horizontal)
        upper.addWidget(self._map)
        upper.addWidget(side)
        upper.setStretchFactor(0, 3)
        upper.setStretchFactor(1, 1)
        root.addWidget(upper, 1)

        # -- forecast-hour playback ----------------------------------------- #
        # Hidden for a single-hour box: a slider with one position is furniture,
        # not a control.
        self._hour_bar = QWidget()
        hour_row = QHBoxLayout(self._hour_bar)
        hour_row.setContentsMargins(0, 0, 0, 0)
        self._hour_label = QLabel("")
        self._hour_label.setFont(mono_font("body"))
        self._hour_label.setMinimumWidth(150)
        hour_row.addWidget(self._hour_label)
        self._hour_prev = QToolButton()
        self._hour_prev.setText("\u25c0")
        self._hour_prev.setToolTip("Previous forecast hour")
        self._hour_prev.clicked.connect(lambda: self._step_hour(-1))
        hour_row.addWidget(self._hour_prev)
        self._hour_play = QToolButton()
        self._hour_play.setText("\u25b6")
        self._hour_play.setCheckable(True)
        self._hour_play.setToolTip("Play through the forecast hours")
        self._hour_play.toggled.connect(self._on_play_toggled)
        hour_row.addWidget(self._hour_play)
        self._hour_next = QToolButton()
        self._hour_next.setText("\u25b6|")
        self._hour_next.setToolTip("Next forecast hour")
        self._hour_next.clicked.connect(lambda: self._step_hour(1))
        hour_row.addWidget(self._hour_next)
        self._hour_slider = QSlider(Qt.Horizontal)
        self._hour_slider.setMinimum(0)
        self._hour_slider.setMaximum(0)
        self._hour_slider.setPageStep(1)
        self._hour_slider.setTracking(True)
        self._hour_slider.valueChanged.connect(self._on_hour_changed)
        hour_row.addWidget(self._hour_slider, 1)
        self._peak_button = QPushButton("Jump to peak")
        self._peak_button.setToolTip(
            "Go to the hour whose extreme of the selected field is the most "
            "significant across the whole sequence.")
        self._peak_button.clicked.connect(self._jump_to_peak)
        hour_row.addWidget(self._peak_button)
        self._hour_bar.hide()
        root.addWidget(self._hour_bar)

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(700)
        self._play_timer.timeout.connect(self._advance_playback)

        # -- status --------------------------------------------------------- #
        status = QHBoxLayout()
        self._status = QLabel("")
        self._status.setFont(ui_font("caption"))
        status.addWidget(self._status, 1)
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(220)
        self._progress.setVisible(False)
        status.addWidget(self._progress)
        root.addLayout(status)

        self.resize(1180, 860)

    # -- forecast-hour sequence --------------------------------------------- #
    def set_sequence(self, sequence) -> None:
        """Show a multi-hour box and enable the hour controls.

        A one-hour sequence is displayed as an ordinary single analysis, so a
        caller never has to branch on the count.
        """
        self._sequence = sequence
        hours = tuple(getattr(sequence, "hours", ()) or ())
        if len(hours) <= 1:
            self._hour_bar.hide()
            self._hour_play.setChecked(False)
            analysis = sequence.at(hours[0]) if hours else None
            if analysis is not None:
                self.set_analysis(analysis)
            return
        self._hour_slider.blockSignals(True)
        self._hour_slider.setMaximum(len(hours) - 1)
        self._hour_slider.setValue(0)
        self._hour_slider.blockSignals(False)
        self._hour_bar.show()
        self._show_hour(0)

    def sequence(self):
        return self._sequence

    def hour(self) -> int | None:
        """Return the forecast hour currently displayed, if this is a sequence."""
        hours = tuple(getattr(self._sequence, "hours", ()) or ())
        if not hours:
            return None
        index = min(max(0, self._hour_slider.value()), len(hours) - 1)
        return hours[index]

    def _show_hour(self, index) -> None:
        hours = tuple(getattr(self._sequence, "hours", ()) or ())
        if not hours:
            return
        index = min(max(0, int(index)), len(hours) - 1)
        hour = hours[index]
        analysis = self._sequence.at(hour)
        self._hour_label.setText(
            f"F{hour:03d}   ({index + 1} of {len(hours)})")
        if analysis is not None:
            self.set_analysis(analysis)

    def _on_hour_changed(self, index) -> None:
        self._show_hour(index)

    def _step_hour(self, delta) -> None:
        self._hour_slider.setValue(self._hour_slider.value() + int(delta))

    def _on_play_toggled(self, playing) -> None:
        self._hour_play.setText("\u25a0" if playing else "\u25b6")
        if playing:
            self._play_timer.start()
        else:
            self._play_timer.stop()

    def _advance_playback(self) -> None:
        if self._hour_slider.maximum() <= 0:
            self._hour_play.setChecked(False)
            return
        # Loop rather than stop at the end: watching a field build and decay
        # repeatedly is how the evolution reads.
        value = self._hour_slider.value() + 1
        if value > self._hour_slider.maximum():
            value = 0
        self._hour_slider.setValue(value)

    def _jump_to_peak(self) -> None:
        if self._sequence is None:
            return
        key = self._current_field()
        if not key:
            return
        hours = tuple(getattr(self._sequence, "hours", ()) or ())
        screen_name = self._screen_combo.currentData()
        # With a screen active, "peak" means the largest qualifying area; that is
        # what the reader is looking at.
        peak = (
            self._sequence.peak_coverage_hour(screen_name)
            if screen_name else self._sequence.peak_hour(key)
        )
        if peak is None or peak not in hours:
            self.set_status("No hour has a value for that field.")
            return
        self._hour_slider.setValue(hours.index(peak))

    # -- population --------------------------------------------------------- #
    def set_status(self, text: str) -> None:
        self._status.setText(str(text))

    def set_progress(self, done: int, total: int) -> None:
        """Show a bounded progress bar, hiding it when there is nothing to do."""
        if total <= 0:
            self._progress.setVisible(False)
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, int(total))
        self._progress.setValue(int(done))

    def clear_progress(self) -> None:
        self._progress.setVisible(False)

    def set_extraction(self, extraction: BoxExtractResult) -> None:
        """Record the extraction and centre the map on its box."""
        self._extraction = extraction
        plan = extraction.plan
        region = plan.region
        self._map.set_box(
            (region.lat0, region.lon0, region.lat1, region.lon1))
        self._map.set_view(region)
        try:
            from sharpmod.tools import model_extract

            config = model_extract.get_config(plan.model_key)
            self._map.set_domain(
                config.domain_bounds,
                f"{config.label} {config.domain}",
                config.domain_outline,
            )
        except Exception:
            pass

    def set_analysis(self, analysis) -> None:
        """Show a completed analysis, preserving the chosen field if possible."""
        self._analysis = analysis
        previous = self._field_combo.currentData()
        self._repopulate_fields(preferred=previous)
        self._map.set_analysis(analysis, self._field_combo.currentData())
        self._repopulate_screens(preferred=self._screen_combo.currentData())
        self._refresh_stats()
        self._refresh_ranked()
        has_composites = _analysis.COMPOSITE_TIER in getattr(
            analysis, "tiers", ())
        self._composites_button.setEnabled(not has_composites)
        if has_composites:
            self._composites_button.setText("SPC composites included")

    def analysis(self):
        return self._analysis

    def _repopulate_fields(self, *, preferred=None) -> None:
        # For a sequence take the fields every hour can show. Rebuilding this
        # list per hour would let the selection jump as the slider moved.
        if self._sequence is not None:
            available = self._sequence.available_parameters()
        else:
            available = (
                self._analysis.available_parameters() if self._analysis else ()
            )
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        chosen_index = -1
        for group, items in _analysis.parameter_groups(
                [item.key for item in available]):
            if self._field_combo.count():
                self._field_combo.insertSeparator(
                    self._field_combo.count())
            for item in items:
                self._field_combo.addItem(
                    f"{group}: {item.display_label}", item.key
                )
                if item.key == preferred:
                    chosen_index = self._field_combo.count() - 1
        if chosen_index < 0:
            # Default to a field that is almost always the first thing looked
            # at, when it is present.
            for fallback in ("mucape", "mlcape", "shear_6km"):
                index = self._field_combo.findData(fallback)
                if index >= 0:
                    chosen_index = index
                    break
        if chosen_index < 0 and self._field_combo.count():
            chosen_index = 0
        if chosen_index >= 0:
            self._field_combo.setCurrentIndex(chosen_index)
        self._field_combo.blockSignals(False)

    def _repopulate_screens(self, *, preferred=None) -> None:
        """Offer only the screens this box has the fields to evaluate."""
        available = self._analysis.screens() if self._analysis else ()
        self._screen_combo.blockSignals(True)
        self._screen_combo.clear()
        self._screen_combo.addItem("None", None)
        for name in available:
            self._screen_combo.addItem(name.capitalize(), name)
        index = (
            self._screen_combo.findData(preferred) if preferred else -1
        )
        self._screen_combo.setCurrentIndex(max(0, index))
        self._screen_combo.blockSignals(False)
        self._apply_screen()

    def _on_screen_changed(self, _index) -> None:
        self._apply_screen()

    def _apply_screen(self) -> None:
        name = self._screen_combo.currentData()
        self._map.set_screen(name)
        coverage = self._map.coverage()
        if name is None or coverage is None:
            self._coverage_box.hide()
            self._coverage_criteria.setText("")
            self._coverage_value.setText("\u2014")
            return
        self._coverage_criteria.setText(
            _analysis.describe_criteria(coverage.criteria))
        self._coverage_value.setText(coverage.describe())
        self._coverage_box.show()

    def screen(self):
        """Return the selected ingredient-screen name, or ``None``."""
        return self._screen_combo.currentData()

    def set_screen(self, name) -> None:
        """Select an ingredient screen by name, if it is available."""
        index = self._screen_combo.findData(name)
        if index >= 0:
            self._screen_combo.setCurrentIndex(index)

    # -- reactions ---------------------------------------------------------- #
    def _current_field(self):
        return self._field_combo.currentData()

    def _on_field_changed(self, _index) -> None:
        key = self._current_field()
        if not key:
            return
        self._map.set_field(key)
        self._refresh_stats()
        self._refresh_ranked()

    def _on_values_toggled(self, checked) -> None:
        self._map.set_show_values(bool(checked))

    def _refresh_stats(self) -> None:
        key = self._current_field()
        stats = (
            self._analysis.statistics(key)
            if self._analysis is not None and key else None
        )
        if stats is None:
            for label in self._stat_labels.values():
                label.setText("\u2014")
            return
        item = stats.parameter
        self._stat_labels["minimum"].setText(item.format(stats.minimum))
        self._stat_labels["mean"].setText(item.format(stats.mean))
        self._stat_labels["median"].setText(item.format(stats.median))
        self._stat_labels["maximum"].setText(item.format(stats.maximum))
        self._stat_labels["spread"].setText(item.format(stats.spread))
        self._stat_labels["extreme"].setText(
            f"{stats.extreme.lat:.2f}, {stats.extreme.lon:.2f}")
        self._stat_labels["count"].setText(str(stats.count))

    def _refresh_ranked(self) -> None:
        key = self._current_field()
        self._ranked.setRowCount(0)
        self._ranked_keys = []
        if self._analysis is None or not key:
            return
        item = _analysis.parameter(key)
        rows = self._analysis.ranked(key, limit=RANKED_ROWS)
        self._ranked.setRowCount(len(rows))
        for row_index, point in enumerate(rows):
            for column, text in enumerate((
                item.format(point.value(key)),
                f"{point.lat:.2f}",
                f"{point.lon:.2f}",
            )):
                cell = QTableWidgetItem(text)
                if column:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._ranked.setItem(row_index, column, cell)
            self._ranked_keys.append((point.row, point.col))
        self._ranked.resizeColumnsToContents()

    def _on_cell_selected(self, row, col) -> None:
        self._select_ranked_row((int(row), int(col)))
        self._describe_cell(int(row), int(col))

    def _on_cell_activated(self, row, col) -> None:
        self._open_cell(int(row), int(col))

    def _select_ranked_row(self, cell) -> None:
        try:
            index = self._ranked_keys.index(cell)
        except ValueError:
            self._ranked.clearSelection()
            return
        self._ranked.blockSignals(True)
        self._ranked.selectRow(index)
        self._ranked.blockSignals(False)

    def _on_ranked_selected(self) -> None:
        rows = {index.row() for index in self._ranked.selectedIndexes()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if 0 <= row < len(self._ranked_keys):
            cell = self._ranked_keys[row]
            self._map.set_selected_cell(*cell)
            self._describe_cell(*cell)

    def _on_ranked_activated(self, _item) -> None:
        self._open_selected()

    def _describe_cell(self, row, col) -> None:
        if self._analysis is None:
            return
        point = self._analysis.point_at(row, col)
        if point is None:
            return
        key = self._current_field()
        pieces = [f"Grid point {point.request_id} at "
                  f"{point.lat:.3f}, {point.lon:.3f}"]
        if key:
            item = _analysis.parameter(key)
            pieces.append(f"{item.label} {item.format(point.value(key), with_units=True)}")
        if point.error:
            pieces.append(point.error)
        self.set_status("   ".join(pieces))

    def _selected_cell(self):
        cell = self._map.selected_cell()
        if cell is not None:
            return cell
        rows = {index.row() for index in self._ranked.selectedIndexes()}
        if len(rows) == 1:
            row = rows.pop()
            if 0 <= row < len(self._ranked_keys):
                return self._ranked_keys[row]
        return None

    def _open_selected(self) -> None:
        cell = self._selected_cell()
        if cell is None:
            self.set_status(
                "Select a grid point on the map or in the list first.")
            return
        self._open_cell(*cell)

    def _open_cell(self, row, col) -> None:
        if self._analysis is None:
            return
        point = self._analysis.point_at(row, col)
        if point is None:
            return
        if not point.npz_path or not os.path.isfile(point.npz_path):
            self.set_status(
                f"Grid point {point.request_id} has no extracted sounding "
                f"({point.error or 'not extracted'}).")
            return
        label = f"Box {point.request_id}  {point.lat:.2f}, {point.lon:.2f}"
        self.soundingRequested.emit(point.npz_path, label)

    def set_field(self, key) -> None:
        """Select a field by key, if it is available."""
        index = self._field_combo.findData(str(key))
        if index >= 0:
            self._field_combo.setCurrentIndex(index)

    # -- export ------------------------------------------------------------- #
    def _export_source(self):
        """Return what an export should cover: the whole sequence if there is one.

        A sequence exports every hour into one file rather than making the reader
        step the slider and save repeatedly.
        """
        if self._sequence is not None \
                and len(getattr(self._sequence, "hours", ()) or ()) > 1:
            return self._sequence
        return self._analysis

    def _export_stem(self) -> str:
        """Return a default file name that identifies the box it came from."""
        analysis = self._analysis
        if analysis is None:
            return "box"
        plan = analysis.plan
        region = plan.region
        model = plan.model_key.replace("-", "_")
        hours = tuple(getattr(self._sequence, "hours", ()) or ())
        span = (
            f"f{hours[0]:03d}-f{hours[-1]:03d}" if len(hours) > 1
            else f"f{int(analysis.fxx):03d}"
        )
        return (
            f"{model}_box_{region.center_lat:.2f}N_"
            f"{region.center_lon:.2f}E_{span}"
        )

    def _ask_path(self, caption, default_name, filter_text) -> str | None:
        try:
            suggested = export_file_path(default_name)
        except ExportDirectoryError as exc:
            self.set_status(str(exc))
            return None
        path, _selected = QFileDialog.getSaveFileName(
            self, caption, str(suggested), filter_text)
        return path or None

    def open_export_folder(self) -> None:
        """Open the same directory used to seed every box export dialog."""
        try:
            directory = export_directory()
        except ExportDirectoryError as exc:
            self.set_status(str(exc))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.set_status(f"Could not open the export folder: {directory}")

    def export_field_png(self) -> None:
        """Preview and save a frozen map figure of the selected box hour."""
        if self._analysis is None:
            self.set_status("There is no field to export yet.")
            return
        from datetime import datetime, timedelta

        from sharpmod.ui.features.export_presentation import ExportIdentity
        from sharpmod.ui.maps.export import capture_map, export_map_figure
        from sharpmod.ui.features.gui_settings import _build_settings

        analysis = self._analysis
        try:
            valid = getattr(analysis, "valid_time", None)
            run = getattr(analysis, "run_time", None)
            hour = int(getattr(analysis, "fxx", 0))
            if valid is None and isinstance(run, datetime):
                valid = run + timedelta(hours=hour)
            field = str(self._field_combo.currentText() or self._map.field())
            title = f"Box field · {field} · F{hour:03d}"
            frame = capture_map(self._map, title=title)
            # The box field is derived from extracted profiles, not an HRRR
            # raster: its analysis time and field scale live on the analysis.
            from dataclasses import replace

            plan = analysis.plan
            time_parts = ["Box analysis"]
            if isinstance(valid, datetime):
                time_parts.append(f"Valid {valid:%d %b %Y %H:%M}Z")
            if isinstance(run, datetime):
                time_parts.append(f"Run {run:%d %b %Y %H:%M}Z · F{hour:03d}")
            screen = self._screen_combo.currentText().strip() \
                if self._screen_combo.currentData() is not None else ""
            frame = replace(
                frame, requested_time=valid if isinstance(valid, datetime) else None,
                run=run if isinstance(run, datetime) else None,
                forecast_hour=hour, time_header=" · ".join(time_parts),
                source_label=f"{plan.model_key} extracted box · {field}",
                legend=(frame.legend + (f" · Hatched screen: {screen}"
                                       if screen else "")),
            )
            identity = ExportIdentity(
                location=f"box-{plan.region.center_lat:.2f}-{plan.region.center_lon:.2f}",
                source=plan.model_key, initialization=run,
                valid_start=valid, detail=field,
            )
            export_map_figure(
                self, (frame,), settings=_build_settings(),
                namespace="map-box-field", identity=identity,
                kind="box-map", title=title, status=self.set_status,
            )
        except Exception as exc:  # noqa: BLE001 - export UI boundary
            self.set_status(f"Could not capture the field map: {exc}")

    def export_csv(self) -> None:
        """Save every sampled point's values."""
        source = self._export_source()
        if source is None:
            self.set_status("There is nothing to export yet.")
            return
        path = self._ask_path(
            "Save values", f"{self._export_stem()}.csv",
            "Comma-separated values (*.csv)")
        if path is None:
            return
        from sharpmod.analysis.box_export import write_box_csv

        try:
            rows = write_box_csv(
                source, path, criteria=self._screen_combo.currentData())
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            self.set_status(f"Could not write {path}: {exc}")
            return
        self.set_status(f"Saved {rows} rows to {path}")

    def export_geojson(self) -> None:
        """Save the sampled cells for GIS."""
        source = self._export_source()
        if source is None:
            self.set_status("There is nothing to export yet.")
            return
        path = self._ask_path(
            "Save sampled cells", f"{self._export_stem()}.geojson",
            "GeoJSON (*.geojson *.json)")
        if path is None:
            return
        from sharpmod.analysis.box_export import write_box_geojson

        try:
            features = write_box_geojson(
                source, path, criteria=self._screen_combo.currentData())
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            self.set_status(f"Could not write {path}: {exc}")
            return
        self.set_status(f"Saved {features} features to {path}")


from sharpmod.ui.box_workers import BoxExtractWorker, BoxMeanWorker, BoxAnalysisWorker  # noqa: E402
from sharpmod.ui.box_plan import BoxPlanDialog  # noqa: E402
