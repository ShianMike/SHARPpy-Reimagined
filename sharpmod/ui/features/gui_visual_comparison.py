"""Synchronized two/four-panel thermodynamic and vertical-difference views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

import numpy as np
from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sharpmod.viz.colors import semantic_palette
from sharpmod.analysis.comparison import exact_profile, vertical_profile_difference
from sharpmod.ui.features.gui_comparison_charts import (
    ARRANGEMENTS,
    ARRANGEMENT_SIDE_BY_SIDE,
    CHART_DIFFERENCE,
    CHART_MODES,
    CHART_SKEWT,
    ComparisonChartGrid,
    RENDERED_MODES,
    comparison_png,
)
from sharpmod.ui.features.gui_theme import current_theme, mono_font, ui_font
from sharpmod.ui.styles.theme import OBJ_GHOST, OBJ_HINT, OBJ_SECTION_LABEL, SPACE


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except (AttributeError, KeyError, TypeError, ValueError):
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value is None else value


def _current_time(collection):
    try:
        return collection.getCurrentDate()
    except (AttributeError, IndexError, TypeError, ValueError):
        dates = tuple(getattr(collection, "_dates", ()) or ())
        index = getattr(collection, "_prof_idx", 0)
        return dates[index] if 0 <= int(index) < len(dates) else None


def _label(collection, index):
    location = str(_meta(collection, "loc", f"Sounding {index + 1}"))
    model = str(_meta(collection, "model", "") or "").upper()
    run = _meta(collection, "run")
    parts = [location]
    if model:
        parts.append(model)
    if isinstance(run, datetime):
        parts.append(run.strftime("%d %b %H%MZ"))
    return " · ".join(parts)


#: Panel geometry, shared by the painter and the hover hit-test.
_PLOT_LEFT_INSET = 34.0
_PLOT_RIGHT_INSET = 12.0
_MIN_STRIP_WIDTH = 25.0

#: Never collapse a difference axis onto a hairline scale, however small the
#: largest difference is; a ±5 floor keeps a 0.2 °C change from looking dramatic.
_MIN_DIFFERENCE_SPAN = 5.0

#: The subtraction direction, stated wherever a difference is presented. The
#: minus sign is U+2212 so it reads as an operator rather than a hyphen.
DIFFERENCE_DIRECTION = "Δ = slot − reference"

_THERMODYNAMIC_FIELDS = ("temperature_c", "dewpoint_c")
_WIND_FIELDS = ("u_wind_kt", "v_wind_kt")
_FAMILY_NAMES = {
    "thermodynamic_c": "ΔT and ΔTd",
    "wind_kt": "Δu and Δv",
}

#: The unit contract for the canvas, which is also what an exported image has to
#: carry on its own. Temperature and wind differences are not comparable, so they
#: never share a numeric span: a 30 kt wind change previously flattened a 2 °C
#: temperature change into a few percent of the same axis.
CANVAS_DESCRIPTION = (
    "Each slot plots temperature and dewpoint against height, then two separate "
    "difference axes so measurements with incompatible units never share one "
    "scale. Thermodynamic differences ΔT and ΔTd use °C; "
    "wind differences Δu and Δv use kt on a separate axis. "
    "The vertical coordinate is height in km MSL. "
    "Gaps mean missing values and are never bridged."
)


def _array(profile, field):
    try:
        values = np.ma.asarray(getattr(profile, field), dtype=float).reshape(-1)
    except (AttributeError, TypeError, ValueError):
        return np.asarray([], dtype=float)
    result = np.asarray(values.filled(np.nan), dtype=float)
    result[~np.isfinite(result) | (result <= -9998.0)] = np.nan
    return result


@dataclass(frozen=True)
class _Panel:
    slot_number: int
    profile_id: object
    label: str
    profile: object
    difference: object
    reference: bool
    modified: bool
    unavailable_reason: str = ""
    #: The originating collection, carried only so the scientific chart views can
    #: read its provenance metadata (location, model, run, observed) for their
    #: titles. The drawn profile still comes from ``profile``, which is already
    #: pinned to the exact reference valid time, so a chart and a difference can
    #: never be showing two different soundings.
    collection: object = None




class VisualComparisonWidget(QWidget):
    """Controls and synchronized canvas embedded in the Compare workspace."""

    layoutChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisVisualComparison")
        self._collections = ()
        self._profile_ids = ()
        self._reference = 0
        self._valid_time = None
        self._slot_ids = [None, None, None, None]
        self._known_labels = {}
        self._slots_initialized = False
        self._rebuilding_slots = False
        #: The panels last rendered, retained so an export and a chart-type switch
        #: reuse the resolved profiles and differences instead of recomputing them.
        self._panels = ()
        self._reference_time = None
        self._reference_label = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["xs"])
        row = QGridLayout()
        row.setHorizontalSpacing(SPACE["sm"])
        row.setVerticalSpacing(SPACE["xs"])
        label = QLabel("Synchronized profiles")
        label.setObjectName(OBJ_SECTION_LABEL)
        row.addWidget(label, 0, 0, 1, 2)
        self.layout_choice = QComboBox()
        self.layout_choice.setObjectName("analysisVisualLayout")
        self.layout_choice.setAccessibleName("Visual comparison panel count")
        self.layout_choice.addItem("2 panels", 2)
        self.layout_choice.addItem("4 panels", 4)
        self.layout_choice.currentIndexChanged.connect(self._layout_changed)
        # Fixed versus automatic ranges is a deliberate, visible choice: an
        # automatically refitted axis makes two times look alike when they are
        # not, and a silently fixed one hides how far the data actually goes.
        self.lock_scales = QCheckBox("Fixed ranges", self)
        self.lock_scales.setObjectName("analysisVisualLockScales")
        self.lock_scales.setAccessibleName(
            "Fix the comparison axis ranges instead of fitting each slot"
        )
        self.lock_scales.setToolTip(
            "Fix the height, temperature, and difference ranges at their current "
            "values so slots and valid times stay comparable. Values beyond a "
            "fixed range are clamped and reported, never silently truncated."
        )
        self.lock_scales.toggled.connect(self._lock_toggled)
        self.refit_scales = QPushButton("Refit", self)
        self.refit_scales.setObjectName(OBJ_GHOST)
        self.refit_scales.setAccessibleName("Refit the fixed comparison ranges")
        self.refit_scales.setToolTip(
            "Re-fit the fixed ranges to the profiles loaded now, staying fixed"
        )
        self.refit_scales.setEnabled(False)
        self.refit_scales.clicked.connect(self._refit_clicked)
        # Which chart. The difference axes stay the default because they answer
        # "how far apart" directly; the Skew-T and hodograph answer "what does
        # this sounding look like", which the difference view cannot.
        self.chart_choice = QComboBox()
        self.chart_choice.setObjectName("analysisVisualChart")
        self.chart_choice.setAccessibleName("Comparison chart type")
        self.chart_choice.setToolTip(
            "Choose the chart drawn in every slot. Skew-T and Hodograph are drawn "
            "by the same renderer the main sounding window uses."
        )
        for key, label in CHART_MODES:
            self.chart_choice.addItem(label, key)
        self.chart_choice.currentIndexChanged.connect(self._chart_changed)
        # Overlaid versus side by side. Both keep one colour per profile.
        self.arrangement_choice = QComboBox()
        self.arrangement_choice.setObjectName("analysisVisualArrangement")
        self.arrangement_choice.setAccessibleName("Comparison chart arrangement")
        self.arrangement_choice.setToolTip(
            "Side by side draws one chart per slot with the reference behind it. "
            "Overlaid draws every assigned profile on one chart."
        )
        for key, label in ARRANGEMENTS:
            self.arrangement_choice.addItem(label, key)
        self.arrangement_choice.currentIndexChanged.connect(self._arrangement_changed)
        self.arrangement_choice.setVisible(False)
        # Linked is the safe default: two charts on different scales invite a
        # comparison the pixels do not support. Unlinking stays available and says
        # so in the status line, because it is occasionally the only way to read a
        # slot whose own data is far off the shared scale.
        self.link_axes = QCheckBox("Linked axes", self)
        self.link_axes.setObjectName("analysisVisualLinkAxes")
        self.link_axes.setAccessibleName(
            "Hold every comparison chart on one shared vertical scale"
        )
        self.link_axes.setToolTip(
            "Keep every slot on one shared scale and one shared height cursor. "
            "Clearing this lets each slot scale itself, and slot positions then "
            "stop being comparable."
        )
        self.link_axes.setChecked(True)
        self.link_axes.setVisible(False)
        self.link_axes.toggled.connect(self._link_toggled)
        row.addWidget(self.lock_scales, 1, 0)
        row.addWidget(self.refit_scales, 1, 1)
        row.addWidget(self.link_axes, 2, 0, 1, 2)
        row.addWidget(self.chart_choice, 3, 0)
        row.addWidget(self.layout_choice, 3, 1)
        row.addWidget(self.arrangement_choice, 4, 0, 1, 2)
        row.setColumnStretch(0, 1)
        row.setColumnStretch(1, 1)
        for choice in (self.chart_choice, self.arrangement_choice, self.layout_choice):
            choice.setMinimumWidth(0)
            choice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        outer.addLayout(row)

        slot_grid = QGridLayout()
        slot_grid.setContentsMargins(0, 0, 0, 0)
        slot_grid.setHorizontalSpacing(SPACE["sm"])
        slot_grid.setVerticalSpacing(SPACE["xs"])
        self.slot_choices = []
        self._slot_cells = []
        for index in range(4):
            cell = QWidget(self)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            slot_label = QLabel(f"Slot {index + 1}", cell)
            slot_label.setObjectName(OBJ_SECTION_LABEL)
            choice = QComboBox(cell)
            choice.setObjectName(f"analysisVisualSlot{index + 1}")
            choice.setAccessibleName(f"Comparison slot {index + 1} profile")
            choice.setMinimumWidth(0)
            choice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            choice.setToolTip(
                "Choose the profile that remains assigned to this panel. "
                "Choosing a profile used by another slot swaps the two slots."
            )
            choice.currentIndexChanged.connect(
                lambda _choice_index, slot=index: self._slot_changed(slot)
            )
            cell_layout.addWidget(slot_label)
            cell_layout.addWidget(choice)
            slot_grid.addWidget(cell, index // 2, index % 2)
            self._slot_cells.append(cell)
            self.slot_choices.append(choice)
        slot_grid.setColumnStretch(0, 1)
        slot_grid.setColumnStretch(1, 1)
        outer.addLayout(slot_grid)
        self.canvas = _ComparisonCanvas(self)
        self.canvas.cursorHeightChanged.connect(self._cursor_changed)
        outer.addWidget(self.canvas, 1)
        # The scientific charts live beside the simplified canvas rather than
        # replacing it: exactly one is visible, and both are fed the same panels,
        # so switching chart type never re-derives a profile or a difference.
        self.charts = ComparisonChartGrid(self)
        self.charts.setVisible(False)
        outer.addWidget(self.charts, 1)
        self.status = QLabel(
            "Solid traces: temperature/dewpoint. Difference strip: candidate minus reference."
        )
        self.status.setObjectName(OBJ_HINT)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        outer.addWidget(self.status)
        self._update_slot_visibility()
        self._sync_chart_controls()
        self._rebuild_slot_choices()

    def layout_count(self):
        return int(self.layout_choice.currentData() or 2)

    def set_layout_count(self, count):
        index = self.layout_choice.findData(4 if int(count) == 4 else 2)
        if index >= 0:
            self.layout_choice.setCurrentIndex(index)

    def slot_ids(self):
        """Return all four stable assignments, including currently hidden slots."""
        return tuple(self._slot_ids)

    def slot_state(self):
        """Return JSON-safe slot IDs plus cached labels for unavailable profiles."""
        return [
            {
                "profile_id": profile_id,
                "label": (
                    self._known_labels.get(profile_id, "")
                    if profile_id is not None
                    else ""
                ),
            }
            for profile_id in self._slot_ids
        ]

    def restore_slot_state(self, state):
        """Restore explicit assignments before collections are necessarily loaded."""
        if not isinstance(state, (list, tuple)):
            return
        restored = [None, None, None, None]
        for index, entry in enumerate(state[:4]):
            if isinstance(entry, Mapping):
                profile_id = entry.get("profile_id")
                label = str(entry.get("label") or "")
            else:
                profile_id = entry
                label = ""
            restored[index] = profile_id
            if profile_id is not None and label:
                self._known_labels[profile_id] = label
        self._slot_ids = restored
        self._slots_initialized = True
        self._rebuild_slot_choices()
        self._render_panels()

    def set_collections(
        self,
        collections,
        reference_index=0,
        valid_time=None,
        *,
        profile_ids=None,
    ):
        self._collections = tuple(collections)
        self._reference = max(
            0, min(int(reference_index), max(0, len(self._collections) - 1))
        )
        supplied_ids = tuple(profile_ids) if profile_ids is not None else ()
        if len(supplied_ids) != len(self._collections):
            self._profile_ids = tuple(
                f"collection-{id(collection)}" for collection in self._collections
            )
        else:
            self._profile_ids = supplied_ids
        self._valid_time = valid_time
        for index, (profile_id, collection) in enumerate(
            zip(self._profile_ids, self._collections)
        ):
            self._known_labels[profile_id] = _label(collection, index)

        if self._collections:
            if not self._slots_initialized:
                ordered = [self._profile_ids[self._reference]]
                ordered.extend(
                    profile_id
                    for index, profile_id in enumerate(self._profile_ids)
                    if index != self._reference
                )
                self._slot_ids = (ordered + [None] * 4)[:4]
                self._slots_initialized = True
            else:
                assigned = {
                    profile_id for profile_id in self._slot_ids if profile_id is not None
                }
                candidates = (
                    profile_id
                    for profile_id in self._profile_ids
                    if profile_id not in assigned
                )
                for slot, profile_id in enumerate(self._slot_ids):
                    if profile_id is None:
                        self._slot_ids[slot] = next(candidates, None)

        self._rebuild_slot_choices()
        self._render_panels()

    def _reference_id(self):
        if 0 <= self._reference < len(self._profile_ids):
            return self._profile_ids[self._reference]
        return None

    def _rebuild_slot_choices(self):
        reference_id = self._reference_id()
        current_ids = set(self._profile_ids)
        self._rebuilding_slots = True
        try:
            for slot, choice in enumerate(self.slot_choices):
                selected = self._slot_ids[slot]
                blocked = choice.blockSignals(True)
                choice.clear()
                if selected is None:
                    choice.addItem("No profile assigned", None)
                elif selected not in current_ids:
                    label = self._known_labels.get(selected, str(selected))
                    suffix = " · reference" if selected == reference_id else ""
                    choice.addItem(f"Unavailable · {label}{suffix}", selected)
                for profile_id in self._profile_ids:
                    label = self._known_labels.get(profile_id, str(profile_id))
                    suffix = " · reference" if profile_id == reference_id else ""
                    choice.addItem(f"{label}{suffix}", profile_id)
                selected_index = choice.findData(selected)
                if selected_index >= 0:
                    choice.setCurrentIndex(selected_index)
                choice.setEnabled(choice.count() > 1 or selected is not None)
                choice.blockSignals(blocked)
        finally:
            self._rebuilding_slots = False

    def _slot_changed(self, slot):
        if self._rebuilding_slots or not 0 <= int(slot) < len(self._slot_ids):
            return
        slot = int(slot)
        selected = self.slot_choices[slot].currentData()
        previous = self._slot_ids[slot]
        if selected == previous:
            return
        if selected is not None:
            try:
                other = self._slot_ids.index(selected)
            except ValueError:
                other = -1
            if other >= 0 and other != slot:
                self._slot_ids[other] = previous
        self._slot_ids[slot] = selected
        self._rebuild_slot_choices()
        self._render_panels()

    def _panel(self, slot, reference_profile, reference_label, reference_time):
        profile_id = self._slot_ids[slot]
        reference_id = self._reference_id()
        is_reference = profile_id is not None and profile_id == reference_id
        if profile_id is None:
            return _Panel(
                slot + 1,
                None,
                "No profile selected",
                None,
                None,
                False,
                False,
                "No profile is assigned to this slot.",
            )
        try:
            collection_index = self._profile_ids.index(profile_id)
        except ValueError:
            label = self._known_labels.get(profile_id, str(profile_id))
            return _Panel(
                slot + 1,
                profile_id,
                label,
                None,
                None,
                is_reference,
                False,
                "The selected profile is no longer loaded. "
                "This slot remains reserved for it.",
            )
        collection = self._collections[collection_index]
        label = self._known_labels.get(profile_id, _label(collection, collection_index))
        profile = exact_profile(collection, reference_time)
        modified = bool(
            _meta(collection, "profile_edited", False)
            or any(getattr(collection, "_mod_therm", ()) or ())
            or any(getattr(collection, "_mod_wind", ()) or ())
        )
        if profile is None:
            time_label = (
                reference_time.strftime("%Y-%m-%d %H:%M UTC")
                if isinstance(reference_time, datetime)
                else "the selected time"
            )
            return _Panel(
                slot + 1,
                profile_id,
                label,
                None,
                None,
                is_reference,
                modified,
                f"No profile is available at the exact reference valid time "
                f"({time_label}). This slot stays assigned to {label}.",
                collection=collection,
            )
        difference = (
            vertical_profile_difference(
                reference_profile,
                profile,
                reference_label=reference_label,
                candidate_label=label,
            )
            if reference_profile is not None
            else None
        )
        return _Panel(
            slot + 1,
            profile_id,
            label,
            profile,
            difference,
            is_reference,
            modified,
            collection=collection,
        )

    def _render_panels(self):
        if not self._collections:
            panels = [
                self._panel(slot, None, "", self._valid_time) for slot in range(4)
            ]
            self._publish(panels, None)
            self.status.setText(
                "Load at least two soundings for visual comparison. "
                f"{DIFFERENCE_DIRECTION}."
            )
            return
        reference_collection = self._collections[self._reference]
        reference_time = self._valid_time or _current_time(reference_collection)
        reference_profile = exact_profile(reference_collection, reference_time)
        reference_label = _label(reference_collection, self._reference)
        panels = [
            self._panel(slot, reference_profile, reference_label, reference_time)
            for slot in range(4)
        ]
        self._reference_label = reference_label
        self._publish(panels, reference_time)
        self._update_status()

    def _update_status(self):
        """Restate the reference, the units, and the current range mode."""
        reference_label = self._reference_label
        reference_time = self._reference_time
        visible_panels = self._panels[: self.layout_count()]
        missing = sum(panel.profile is None for panel in visible_panels)
        message = (
            f"Reference: {reference_label} · exact valid "
            f"{reference_time:%Y-%m-%d %H:%MZ}"
            if isinstance(reference_time, datetime)
            else f"Reference: {reference_label} · valid time unknown"
        )
        if missing:
            message += (
                f" · {missing} visible slot(s) unavailable; assignments are preserved"
            )
        # The subtraction direction and the two separate unit scales belong with
        # every difference reading, not only in the initial hint that later
        # updates used to overwrite.
        message += f" · {DIFFERENCE_DIRECTION} · ΔT/ΔTd in °C, Δu/Δv in kt on separate axes"
        message += f" · {self._range_summary()}"
        self.status.setText(message)

    def _republish(self):
        """Re-show the panels already resolved, deriving nothing again.

        Changing chart type, arrangement, or axis linking changes only which view
        is drawn. Rebuilding the panels here would re-run
        ``vertical_profile_difference`` for every slot on every switch, which is
        both wasteful and a way for two views to disagree.
        """
        if not self._panels or not self._collections:
            self._render_panels()
            return
        self._publish(self._panels, self._reference_time)
        self._update_status()

    def _publish(self, panels, reference_time):
        """Hand one set of resolved panels to whichever view is showing.

        Both views receive the same panels. Switching chart type therefore never
        re-resolves a profile or recomputes a difference, which is what keeps the
        Skew-T, the difference axes, and an export describing the same data.
        """
        self._panels = tuple(panels)
        self._reference_time = reference_time
        self.canvas.set_data(panels, self.layout_count())
        if self.chart_mode() in RENDERED_MODES:
            self.charts.set_panels(
                self._panels,
                self.layout_count(),
                valid_time=reference_time,
                reference_index=self._reference,
            )

    def panels(self):
        """Return the resolved panels, so an export never recomputes them."""
        return self._panels

    # -- chart type, arrangement, and axis linking -------------------------- #

    def chart_mode(self):
        return str(self.chart_choice.currentData() or CHART_DIFFERENCE)

    def set_chart_mode(self, mode):
        index = self.chart_choice.findData(mode)
        if index >= 0:
            self.chart_choice.setCurrentIndex(index)

    def arrangement(self):
        return str(self.arrangement_choice.currentData() or ARRANGEMENT_SIDE_BY_SIDE)

    def set_arrangement(self, arrangement):
        index = self.arrangement_choice.findData(arrangement)
        if index >= 0:
            self.arrangement_choice.setCurrentIndex(index)

    def linked_axes(self):
        return bool(self.link_axes.isChecked())

    def _chart_changed(self, *_args):
        mode = self.chart_mode()
        rendered = mode in RENDERED_MODES
        if rendered:
            self.charts.set_mode(mode)
            self.charts.set_arrangement(self.arrangement())
            self.charts.set_linked(self.linked_axes())
        self._sync_chart_controls()
        self._republish()

    def _arrangement_changed(self, *_args):
        self.charts.set_arrangement(self.arrangement())
        self._republish()

    def _link_toggled(self, checked):
        self.charts.set_linked(bool(checked))
        self._republish()

    def _sync_chart_controls(self):
        """Show only the controls that act on the visible chart.

        The fixed-range controls govern the difference axes, and the link control
        governs the scientific charts. Leaving both visible at once implied that
        each did something to the other view, which neither does.
        """
        rendered = self.chart_mode() in RENDERED_MODES
        self.canvas.setVisible(not rendered)
        self.charts.setVisible(rendered)
        self.lock_scales.setVisible(not rendered)
        self.refit_scales.setVisible(not rendered)
        self.arrangement_choice.setVisible(rendered)
        self.link_axes.setVisible(rendered)

    def chart_state(self):
        """Return JSON-safe chart type, arrangement, and link state."""
        return {
            "mode": self.chart_mode(),
            "arrangement": self.arrangement(),
            "linked": self.linked_axes(),
        }

    def restore_chart_state(self, state):
        """Restore a saved chart choice, ignoring anything unknown."""
        if not isinstance(state, Mapping):
            return
        mode = state.get("mode")
        arrangement = state.get("arrangement")
        blocked = self.chart_choice.blockSignals(True)
        if self.chart_choice.findData(mode) >= 0:
            self.set_chart_mode(mode)
        self.chart_choice.blockSignals(blocked)
        blocked = self.arrangement_choice.blockSignals(True)
        if self.arrangement_choice.findData(arrangement) >= 0:
            self.set_arrangement(arrangement)
        self.arrangement_choice.blockSignals(blocked)
        if "linked" in state:
            blocked = self.link_axes.blockSignals(True)
            self.link_axes.setChecked(bool(state.get("linked")))
            self.link_axes.blockSignals(blocked)
        mode = self.chart_mode()
        if mode in RENDERED_MODES:
            self.charts.set_mode(mode)
            self.charts.set_arrangement(self.arrangement())
        self.charts.set_linked(self.linked_axes())
        self._sync_chart_controls()
        self._republish()

    # -- export ------------------------------------------------------------- #

    def export_png(self, width=1200, height=800, *, mode=None, scale=1.0):
        """Return the current comparison as PNG bytes at a caller-chosen size.

        The size is an argument rather than the widget's own geometry, so an
        export is not limited to whatever the splitter happens to be showing and
        never has to resize the live view to produce a larger image. The panels
        handed to the renderer are the ones already on screen, so no diagnostic is
        recalculated for the export.
        """
        requested = mode or self.chart_mode()
        if requested not in RENDERED_MODES:
            requested = CHART_SKEWT
        return comparison_png(
            self._panels,
            layout_count=self.layout_count(),
            mode=requested,
            arrangement=self.arrangement(),
            width=width,
            height=height,
            valid_time=self._reference_time,
            reference_index=self._reference,
            scale=scale,
        )

    def _lock_toggled(self, checked):
        self.canvas.set_scale_lock(bool(checked))
        self.refit_scales.setEnabled(bool(checked))
        self._render_panels()

    def _refit_clicked(self):
        self.canvas.refit_scales()
        self._render_panels()

    def scale_state(self):
        """Return JSON-safe axis-lock state for the analysis session."""
        return self.canvas.scale_state()

    def restore_scale_state(self, state):
        """Restore a saved axis lock and keep the visible control in step."""
        self.canvas.restore_scale_state(state)
        locked = self.canvas.scale_lock()
        blocked = self.lock_scales.blockSignals(True)
        self.lock_scales.setChecked(locked)
        self.lock_scales.blockSignals(blocked)
        self.refit_scales.setEnabled(locked)
        self._render_panels()

    def _range_summary(self):
        """Describe the current range mode, naming any clamped family."""
        if self.chart_mode() in RENDERED_MODES:
            # The vendored renderers carry their own axes, so the difference-axis
            # lock does not apply; say what does govern comparability here.
            arrangement = dict(ARRANGEMENTS).get(self.arrangement(), "")
            return (
                f"{arrangement.lower()} scientific charts, "
                + (
                    "one shared vertical scale"
                    if self.linked_axes()
                    else "independent per-slot scales; positions are not comparable "
                    "between slots"
                )
            )
        if not self.canvas.scale_lock():
            return "automatic ranges"
        scales = self.canvas.locked_scales() or {}
        summary = (
            "fixed ranges "
            f"ΔT/ΔTd ±{scales.get('thermodynamic_c', 0):g} °C, "
            f"Δu/Δv ±{scales.get('wind_kt', 0):g} kt"
        )
        clipped = self.canvas.clipped_families()
        if clipped:
            names = ", ".join(
                _FAMILY_NAMES.get(family, family) for family in clipped
            )
            summary += f"; {names} clamped at the axis edge, use Refit to see them"
        return summary

    def _layout_changed(self, *_args):
        self._update_slot_visibility()
        self.layoutChanged.emit(self.layout_count())
        self._render_panels()

    def _update_slot_visibility(self):
        count = self.layout_count()
        for index, cell in enumerate(self._slot_cells):
            cell.setVisible(index < count)

    def _cursor_changed(self, height):
        # One cursor drives every view, so moving it on the difference axes moves
        # it on the Skew-T and drives the hodograph's own height readout too.
        self.charts.set_cursor_height(height)
        if height is None:
            return
        self.status.setText(
            f"Shared cursor: {float(height):,.0f} m MSL · {DIFFERENCE_DIRECTION} · "
            "gaps remain missing; plot interpolation is not used for scalar "
            "diagnostics."
        )


__all__ = ["VisualComparisonWidget"]


from sharpmod.ui.comparison_canvas import _ComparisonCanvas  # noqa: E402
