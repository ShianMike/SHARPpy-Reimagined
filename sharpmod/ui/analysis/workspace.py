"""Interactive comparison, trend, and ensemble workspace for sounding viewers.

The expensive meteorological work lives in :mod:`sharpmod.analysis.profile_metrics`.
This module is deliberately a presentation layer: it submits one lazy job for
the visible tab, rejects stale results, and only paints pre-computed numbers.
That split keeps both the viewer and its focused GUI tests responsive.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import logging
import math
from pathlib import Path
from threading import Event
from typing import NamedTuple
import weakref

from qtpy.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QThreadPool,
    Qt,
    Signal,
)
from qtpy.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.analysis.box_analysis import PARAMETERS, parameter
from sharpmod.viz.colors import semantic_palette
from sharpmod.export_paths import ExportDirectoryError, export_file_path
from sharpmod.analysis.ensemble_members import EnsembleAcquisition
from sharpmod.ui.features.gui_common import (
    action_label, make_status_label, scrollable_page, set_status_label,
)
from sharpmod.ui.features.gui_metric_columns import (
    MetricColumnsDialog,
    normalize_keys,
    read_preference,
    write_preference,
)
from sharpmod.ui.features.gui_jobs import JobCounts, JobStatus
from sharpmod.ui.features import gui_trend_tracks as trend_tracks
from sharpmod.core import local_time
from sharpmod.state import run_updates
from sharpmod.ui.shell import dock_title_bar
from sharpmod.ui.features.gui_theme import current_density, current_theme, mono_font, ui_font
from sharpmod.ui.features.gui_animation import AnimationWorkspace
from sharpmod.ui.features.gui_ensemble_thresholds import EnsembleThresholdExplorer
from sharpmod.ui.features.gui_visual_comparison import VisualComparisonWidget
from sharpmod.ui.features.gui_workspace_layouts import install_workspace_layouts
from sharpmod.analysis.profile_metrics import (
    DEFAULT_METRIC_KEYS,
    CollectionSnapshot,
    ProfileMetricsEngine,
    export_comparison_csv,
    export_ensemble_csv,
    export_timeline_csv,
    export_timeline_series_csv,
    freeze_collection,
)
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_SECTION_LABEL,
    PROP_HEADER_ACTION,
    SPACE,
)


from sharpmod.ui.analysis.charts import (
    _MISSING,
    _THERMO_RANGE_C,
    _STANDARD_ISOBARS,
    _THERMO_STEP_C,
    _get,
    _number,
    _metric_value,
    _metric_title,
    _format_metric,
    _format_range,
    _delta_ink,
    _format_time,
    _time_scope,
    _comparison_basis_text,
    _meta,
    _collection_label,
    _current_date,
    _inset,
    _ChartInk,
    _trace_inks,
    _SERIES_ROLES,
    _series_inks,
    _series_colours,
    _short_labels,
    _TrendSeries,
    _TrendPoint,
    _chart_ink,
    _nice_ticks,
    _tick_decimals,
    _TrendChart,
    _EnvelopeChart,
)


from sharpmod.ui.analysis.jobs import (
    _analysis_pool,
    _TrendRequest,
    _TrendRun,
    _compute_trends,
    _ComparisonRequest,
    _ComparisonRun,
    _compute_comparison,
    _EnsembleRequest,
    _EnsembleRun,
    _compute_ensemble,
    _TaskSignals,
    _ComputeTask,
)


from sharpmod.ui.analysis.trends import TrendWorkspaceMixin
from sharpmod.ui.analysis.comparison import ComparisonWorkspaceMixin
from sharpmod.ui.analysis.ensemble import EnsembleWorkspaceMixin


_LOGGER = logging.getLogger(__name__)


class _TabSpec(NamedTuple):
    """One analysis page's stable key, reader-facing label, and group."""

    key: str
    label: str
    group: str
    tooltip: str


#: Group order for the section navigator at narrow dock widths.
TAB_GROUPS = ("Across soundings", "Export")

#: Declared in the same order the pages are built, so ``TAB_SPECS[i].key`` is the
#: page at tab index ``i`` and the existing ``TAB_*`` constants stay valid. The
#: keys are the stable identifiers: labels may be reworded without breaking
#: ``open()`` callers or a saved session.
TAB_SPECS = (
    _TabSpec(
        "trends",
        "Trends",
        "Across soundings",
        "Plot one diagnostic through every loaded sounding's forecast times",
    ),
    _TabSpec(
        "compare",
        "Compare",
        "Across soundings",
        "Compare models or runs at one valid time against a reference",
    ),
    _TabSpec(
        "ensemble",
        "Ensemble",
        "Across soundings",
        "Summarize ensemble spread and threshold exceedance for the loaded members",
    ),
    _TabSpec(
        "animation",
        "Animation",
        "Export",
        "Export exact forecast-time or run-to-run GIF animations",
    ),
)


from sharpmod.ui.analysis.workspace_ui import WorkspaceUiMixin
from sharpmod.ui.analysis.workspace_jobs import WorkspaceJobsMixin
from sharpmod.ui.analysis.workspace_session import WorkspaceSessionMixin


class AnalysisWorkspace(
    WorkspaceUiMixin,
    WorkspaceJobsMixin,
    WorkspaceSessionMixin,
    TrendWorkspaceMixin,
    ComparisonWorkspaceMixin,
    EnsembleWorkspaceMixin,
    QWidget,
):
    """Controller and widget for all cross-sounding analysis features."""

    # Index constants remain the load-bearing identifiers for the refresh
    # dispatch below. They are asserted against TAB_SPECS by a focused test, so
    # the registry and the constants cannot drift apart silently.
    TAB_TRENDS = 0
    TAB_COMPARE = 1
    TAB_ENSEMBLE = 2
    TAB_ANIMATION = 3

    def __init__(self, win, *, engine=None, async_compute=True, parent=None):
        super().__init__(parent or win)
        self.setObjectName("sharpmodAnalysisWorkspace")
        self._win_ref = weakref.ref(win)
        self.engine = engine or ProfileMetricsEngine()
        self._async_compute = bool(async_compute)
        self._generation = {"trends": 0, "compare": 0, "ensemble": 0}
        self._trend_samples = ()
        self._trend_series = ()
        self._comparison_samples = ()
        self._ensemble_summary = None
        self._trend_collection_index = 0
        self._pool = _analysis_pool()
        self._tasks = {}
        self._trend_request = None
        self._trend_run = None
        self._trend_retained = ""
        self._compare_request = None
        self._compare_run = None
        self._compare_retained = ""
        self._ensemble_request = self._ensemble_run = None
        self._ensemble_retained = ""
        self._ensemble_acquisition = None
        self._initial_width_applied = False
        # Seeded from the durable preference so the choice survives a restart,
        # then overridden by an analysis session when one is restored.
        self._comparison_metric_keys = read_preference(self._settings())
        self._trend_track_keys = trend_tracks.read_preference(self._settings())
        self._expanded_restore = None
        self.expand_action = None
        self.separate_window_action = None
        self.layout_button = None
        # One monospace font for every column of figures, so digits share an
        # advance width and a reader can compare rows down the column.
        self._figure_font = mono_font("small")
        self._build_ui()
        self._populate_controls()

    # -- construction ------------------------------------------------------- #








    # -- grouped, space-adaptive navigation --------------------------------- #






















    @staticmethod
    def _table_row_height():
        minimum = 24 if current_density() == "compact" else CONTROL_H["sm"]
        text_height = QFontMetricsF(mono_font("small")).height()
        return max(minimum, int(math.ceil(text_height + SPACE["xs"] * 2)))

    @staticmethod
    def _new_table(name, accessible_name):
        table = QTableWidget()
        table.setObjectName(name)
        table.setAccessibleName(accessible_name)
        table.setAccessibleDescription(
            "Read-only table. Use arrow keys to move between rows and columns."
        )
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        # Banded rows already separate records; grid lines on top of them add
        # ink without adding information.
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setCornerButtonEnabled(False)
        table.setTextElideMode(Qt.ElideRight)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(
            AnalysisWorkspace._table_row_height()
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        # The first column absorbs slack instead of the last. Stretching the last
        # one stranded the right-aligned figures far from their header.
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        return table

    def changeEvent(self, event):  # noqa: N802 - Qt override
        """Refresh explicit fonts when live text-size/density preferences change."""
        super().changeEvent(event)
        if event.type() not in {
            QEvent.ApplicationFontChange,
            QEvent.FontChange,
            QEvent.StyleChange,
        }:
            return
        self._figure_font = mono_font("small")
        for name in ("compare_table", "ensemble_table"):
            table = getattr(self, name, None)
            if table is None:
                continue
            table.verticalHeader().setDefaultSectionSize(self._table_row_height())
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None and item.textAlignment() & Qt.AlignRight:
                        item.setFont(self._figure_font)
                        meaning = item.data(Qt.UserRole)
                        if isinstance(meaning, (tuple, list)) and len(meaning) == 2 \
                                and meaning[0] == "comparisonDelta":
                            item.setForeground(QBrush(_delta_ink(meaning[1])))
        for name in ("trend_chart", "ensemble_chart"):
            chart = getattr(self, name, None)
            if chart is not None:
                chart.update()

    @staticmethod
    def _finish_table(table, figure_from, *, stretch=0, capped=()):
        """Align figure headers with their cells and place any leftover width.

        ``stretch`` is the column that absorbs slack, or ``None`` to leave it
        unclaimed. A wide table must pass ``None``: a stretched column is also
        the first one Qt shrinks when the sections overflow, which squeezed the
        sounding name -- the row's identity -- down to an ellipsis.

        Rows are deliberately left at the uniform default section height rather
        than resized to content: a per-row height fights the row token and, in
        the ensemble pane, inflated five rows past the height the splitter had
        given them, so the table opened already scrolling.
        """
        header = table.horizontalHeader()
        if stretch is not None and table.columnCount() > int(stretch):
            header.setSectionResizeMode(int(stretch), QHeaderView.Stretch)
        # Prose columns are capped so the figures stay on screen; the full text
        # remains reachable as a tooltip.
        for column, width in capped:
            if column < table.columnCount():
                header.setSectionResizeMode(column, QHeaderView.Interactive)
                header.resizeSection(column, int(width))
        for column in range(int(figure_from), table.columnCount()):
            item = table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    # -- cell factories ----------------------------------------------------- #

    @staticmethod
    def _text_cell(text, *, tooltip=None, emphasis=False):
        item = QTableWidgetItem(str(text))
        if tooltip:
            item.setToolTip(str(tooltip))
        if emphasis:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def _figure_cell(self, text, *, tooltip=None, colour=None):
        """A right-aligned monospace figure.

        Colour arrives as a resolved theme role rather than a literal: per-item
        foregrounds cannot be expressed in the style sheet without an item
        delegate, so this is the one place values are set in code.
        """
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFont(self._figure_font)
        if tooltip:
            item.setToolTip(str(tooltip))
        if colour is not None:
            item.setForeground(QBrush(colour))
        return item

    def _delta_cell(self, key, value, *, tooltip):
        item = self._figure_cell(
            _format_metric(key, value), tooltip=tooltip, colour=_delta_ink(value)
        )
        # Keep the numeric sign, not the resolved colour, for live retheming.
        item.setData(Qt.UserRole, ("comparisonDelta", value))
        return item

    # -- status ------------------------------------------------------------- #

    @staticmethod
    def _set_status(label, text, *, level="info"):
        set_status_label(label, text, level=level)

    def _set_busy(self, kind, busy):
        """Disable the matching Refresh while its calculation is outstanding."""
        button = {
            "trends": getattr(self, "trend_refresh", None),
            "compare": getattr(self, "compare_refresh", None),
            "ensemble": getattr(self, "ensemble_refresh", None),
        }.get(str(kind))
        if button is not None:
            button.setEnabled(not busy)

    def _window(self):
        return self._win_ref()

    def _collections(self):
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        return tuple(getattr(widget, "prof_collections", ()) or ())

    def _focused_collection(self):
        collections = self._collections()
        if not collections:
            return None
        return collections[min(self._focused_index(), len(collections) - 1)]

    def _request_sounding_input(self, source=None):
        """Focus the authoritative picker, optionally on one existing source tab."""
        win = self._window()
        try:
            controller = getattr(win, "_sharpmod_controller", None) or win.parent()
        except (AttributeError, RuntimeError):
            controller = None
        select = getattr(controller, "_select_tab", None)
        if source and callable(select):
            select(str(source))
        focus = getattr(controller, "focusPicker", None)
        if callable(focus):
            focus()

    @staticmethod
    def _show_recovery(button, needed):
        """Expose a recovery command only while its stated input is missing."""
        button.setHidden(not bool(needed))
        button.setEnabled(bool(needed))

    def _focused_index(self):
        win = self._window()
        widget = getattr(win, "spc_widget", None) if win is not None else None
        try:
            return max(0, int(widget.pc_idx))
        except (AttributeError, TypeError, ValueError):
            return 0

    def _populate_controls(self):
        collections = self._collections()
        previous_reference = self.compare_reference.currentData()
        previous_ids = getattr(self, "_comparison_ids", ())
        previous_id = (previous_ids[previous_reference] if isinstance(previous_reference, int)
                       and 0 <= previous_reference < len(previous_ids) else None)
        window = self._window()
        widget = getattr(window, "spc_widget", None)
        ids = tuple(getattr(widget, "prof_ids", ()) or ())
        if len(ids) != len(collections):
            ids = tuple(id(collection) for collection in collections)
        self._comparison_ids = ids
        state = getattr(window, "_sharpmod_collection_state", None)
        if state is not None and state.reference_id in ids:
            previous_id = state.reference_id
        focused = min(self._focused_index(), max(0, len(collections) - 1))
        combo = self.compare_reference
        blocked = combo.blockSignals(True)
        combo.clear()
        for index, collection in enumerate(collections):
            combo.addItem(_collection_label(collection, index), index)
        old_index = ids.index(previous_id) if previous_id in ids else -1
        combo.setCurrentIndex(old_index if old_index >= 0 else focused)
        combo.blockSignals(blocked)
        self._trend_collection_index = min(
            max(0, self._trend_collection_index), max(0, len(collections) - 1)
        )
    def refresh(self):
        """Refresh selectors and lazily recompute only the visible tab."""
        dock = getattr(self, "dock", None)
        if dock is not None and dock.isHidden():
            return
        self._apply_navigation_layout()
        self._populate_controls()
        self._refresh_active_tab(self.tabs.currentIndex())

    def _ensure_initial_width(self):
        """Give the first open useful reading space, then honor user resizing."""
        win, dock = self._window(), getattr(self, "dock", None)
        if win is None or dock is None or dock.isHidden() or self._initial_width_applied:
            return
        self._initial_width_applied = True
        status_width = QFontMetricsF(self.trend_status.font()).horizontalAdvance(
            "1 available time(s)."
        )
        row_width = status_width + self.trend_refresh.sizeHint().width() \
            + self.trend_export.sizeHint().width() + SPACE["lg"] * 3
        desired = min(max(480, round(row_width * 1.2)), max(320, round(win.width() * 0.6)))
        win.resizeDocks([dock], [desired], Qt.Horizontal)

    def presentation_mode(self):
        """Return the visible placement without encoding any scientific state."""
        if self._expanded_restore is not None:
            return "expanded"
        dock = getattr(self, "dock", None)
        return "separate" if dock is not None and dock.isFloating() else "docked"

    @staticmethod
    def _set_checked(action, checked):
        if action is None or action.isChecked() == bool(checked):
            return
        blocked = action.blockSignals(True)
        action.setChecked(bool(checked))
        action.blockSignals(blocked)

    def _sync_presentation_actions(self):
        mode = self.presentation_mode()
        self._set_checked(self.expand_action, mode == "expanded")
        self._set_checked(self.separate_window_action, mode == "separate")

    def set_expanded(self, expanded):
        """Give analysis the main window, or restore the exact prior chrome.

        The existing workspace widget stays in its dock throughout. Hiding the
        central canvas and sibling docks lets Qt allocate the whole main-window
        client area to analysis without copying profiles, comparison controls,
        or edit-history objects into a second widget tree.
        """
        expanded = bool(expanded)
        win, dock = self._window(), getattr(self, "dock", None)
        if win is None or dock is None:
            return
        if not expanded:
            self._leave_expanded(show_dock=True)
            return
        if self._expanded_restore is not None:
            self._sync_presentation_actions()
            return

        if dock.isFloating():
            dock.setFloating(False)

        central = win.centralWidget()
        siblings = []
        for other in win.findChildren(QDockWidget):
            if other is dock or other.parent() is not win:
                continue
            siblings.append((weakref.ref(other), bool(other.isHidden())))
        self._expanded_restore = {
            "central": weakref.ref(central) if central is not None else None,
            "central_hidden": bool(central is None or central.isHidden()),
            "siblings": tuple(siblings),
            "dock_width": max(1, int(dock.width())),
        }

        was_hidden = dock.isHidden()
        if central is not None:
            central.hide()
        for other_ref, _was_hidden in siblings:
            other = other_ref()
            if other is not None:
                other.hide()
        dock.show()
        dock.raise_()
        try:
            win.resizeDocks([dock], [max(1, int(win.width()))], Qt.Horizontal)
        except (AttributeError, RuntimeError):
            pass
        self._sync_presentation_actions()
        self._apply_navigation_layout()
        if was_hidden:
            self.refresh()

    def _leave_expanded(self, *, show_dock):
        restore = self._expanded_restore
        if restore is None:
            self._sync_presentation_actions()
            return
        self._expanded_restore = None
        central_ref = restore.get("central")
        central = central_ref() if callable(central_ref) else None
        if central is not None:
            central.setVisible(not bool(restore.get("central_hidden", False)))
        for other_ref, was_hidden in restore.get("siblings", ()):
            other = other_ref()
            if other is not None:
                other.setVisible(not bool(was_hidden))

        win, dock = self._window(), getattr(self, "dock", None)
        if dock is not None and show_dock:
            dock.show()
            dock.raise_()
        if win is not None and dock is not None:
            try:
                win.resizeDocks(
                    [dock],
                    [max(1, int(restore.get("dock_width", dock.width())))],
                    Qt.Horizontal,
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        self._sync_presentation_actions()
        self._apply_navigation_layout()

    def set_separate_window(self, separate):
        """Float or re-dock the same analysis widget for multi-monitor use."""
        separate = bool(separate)
        dock = getattr(self, "dock", None)
        if dock is None:
            return
        was_hidden = dock.isHidden()
        if separate and self._expanded_restore is not None:
            self._leave_expanded(show_dock=True)
        if dock.isFloating() != separate:
            dock.setFloating(separate)
        dock.show()
        dock.raise_()
        self._sync_presentation_actions()
        self._apply_navigation_layout()
        if was_hidden:
            self.refresh()

    def _dock_top_level_changed(self, floating):
        """Keep explicit actions truthful when native dock gestures are used."""
        if floating and self._expanded_restore is not None:
            self._leave_expanded(show_dock=True)
        self._sync_presentation_actions()
        self._apply_navigation_layout()

    def _dock_visibility_changed(self, visible):
        if visible:
            self._ensure_initial_width()
        else:
            if self._expanded_restore is not None:
                self._leave_expanded(show_dock=False)
            self.cancel_pending()

    def open(self, tab=None):
        """Show the dock and optionally select a tab by index, key, or label.

        Resolving a stable key as well as the displayed label means rewording a
        page name cannot silently leave a caller on whichever tab happened to be
        open.
        """
        if isinstance(tab, str):
            resolved = self.tab_index_for(tab)
            tab = resolved if resolved is not None else None
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)
        dock = getattr(self, "dock", None)
        if dock is not None:
            dock.show()
            dock.raise_()
            self._ensure_initial_width()
        self._apply_navigation_layout()
        self.refresh()

    def show_compare(self):
        """Open the comparison tab for picker-side acquisition workflows."""
        self.open(self.TAB_COMPARE)

    def show_ensemble(self):
        """Open the ensemble tab after a picker assembles fetched members."""
        self.open(self.TAB_ENSEMBLE)
















    # -- stacked trend tracks ------------------------------------------------ #


    # -- UTC, local time, and newer runs ------------------------------------- #





def install_analysis_workspace(
    win, *, engine=None, async_compute=True, settings=None
):
    """Install the dock once and return its :class:`AnalysisWorkspace`."""
    existing = getattr(win, "_sharpmod_analysis_workspace", None)
    if existing is not None:
        return existing

    workspace = AnalysisWorkspace(
        win, engine=engine, async_compute=async_compute, parent=win
    )
    dock = QDockWidget("Analysis Workspace", win)
    dock.setObjectName("analysisWorkspaceDock")
    dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
    dock.setFeatures(
        QDockWidget.DockWidgetClosable
        | QDockWidget.DockWidgetMovable
        | QDockWidget.DockWidgetFloatable
    )
    expand_action = QAction("Expand analysis in main window", dock)
    expand_action.setCheckable(True)
    expand_action.setToolTip(
        "Temporarily use the main window for analysis and hide the sounding canvas"
    )
    separate_action = QAction("Open analysis in separate window", dock)
    separate_action.setCheckable(True)
    separate_action.setToolTip(
        "Float analysis as a window that can be moved to another monitor"
    )
    workspace.expand_action = expand_action
    workspace.separate_window_action = separate_action
    # Qt's own title bar leaves a ~16x9px close target that ignores the style
    # sheet -- and it is the only affordance for dismissing this panel. The
    # sounding sidebar beside it already uses the themed replacement.
    title_bar = dock_title_bar(
        dock, dock.windowTitle(), shortcut_hint="Ctrl+Shift+A"
    )
    layout_button = QToolButton(title_bar)
    layout_button.setObjectName(OBJ_GHOST)
    layout_button.setProperty(PROP_HEADER_ACTION, True)
    layout_button.setText("Layout")
    layout_button.setAccessibleName("Analysis layout")
    layout_button.setToolTip("Choose where the analysis workspace is shown")
    layout_button.setPopupMode(QToolButton.InstantPopup)
    layout_menu = QMenu(layout_button)
    layout_menu.addAction(expand_action)
    layout_menu.addAction(separate_action)
    layout_button.setMenu(layout_menu)
    workspace.layout_button = layout_button
    workspace.layout_menu = layout_menu
    title_bar.layout().insertWidget(title_bar.layout().count() - 1, layout_button)
    dock.setTitleBarWidget(title_bar)
    dock.setWidget(workspace)
    win.addDockWidget(Qt.RightDockWidgetArea, dock)
    sidebar = getattr(win, "_sharpmod_sidebar_dock", None)
    if isinstance(sidebar, QDockWidget):
        win.tabifyDockWidget(sidebar, dock)
    dock.hide()
    workspace.dock = dock

    toggle = dock.toggleViewAction()
    toggle.setShortcut("Ctrl+Shift+A")
    # Name the groups rather than an out-of-date subset of the nine pages.
    toggle.setToolTip(
        "Open the analysis workspace: "
        + "; ".join(
            f"{group} ({', '.join(spec.label for spec in TAB_SPECS if spec.group == group)})"
            for group in TAB_GROUPS
        )
    )
    view_menu = getattr(win, "_sharpmod_view_menu", None)
    if view_menu is not None:
        view_menu.addSeparator()
        view_menu.addAction(toggle)
        view_menu.addAction(expand_action)
        view_menu.addAction(separate_action)
    workspace_ref = weakref.ref(workspace)

    def expand_requested(checked):
        live = workspace_ref()
        if live is not None:
            live.set_expanded(checked)

    def separate_requested(checked):
        live = workspace_ref()
        if live is not None:
            live.set_separate_window(checked)

    def top_level_changed(floating):
        live = workspace_ref()
        if live is not None:
            live._dock_top_level_changed(floating)

    expand_action.triggered.connect(expand_requested)
    separate_action.triggered.connect(separate_requested)
    dock.topLevelChanged.connect(top_level_changed)

    def toggled(checked):
        live = workspace_ref()
        if checked and live is not None:
            live.refresh()

    toggle.triggered.connect(toggled)

    def visibility_changed(visible):
        live = workspace_ref()
        if live is not None:
            live._dock_visibility_changed(visible)

    dock.visibilityChanged.connect(visibility_changed)
    install_workspace_layouts(
        workspace,
        settings=settings,
        layout_menu=layout_menu,
        view_menu=view_menu,
    )
    win.destroyed.connect(
        lambda *_args, ref=workspace_ref: (
            ref().shutdown() if ref() is not None else None
        )
    )

    win._sharpmod_analysis_workspace = workspace
    win._sharpmod_analysis_workspace_dock = dock
    win._sharpmod_analysis_workspace_action = toggle
    return workspace


def refresh_analysis_workspace(win):
    """Cheap integration hook for the viewer's existing ``updateProfs`` path."""
    workspace = getattr(win, "_sharpmod_analysis_workspace", None)
    if workspace is not None:
        workspace.refresh()


__all__ = [
    "AnalysisWorkspace",
    "install_analysis_workspace",
    "refresh_analysis_workspace",
]
