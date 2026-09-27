"""WorkspaceUi behavior for the sounding analysis workspace."""

from __future__ import annotations

from qtpy.QtCore import QEvent
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QComboBox
from qtpy.QtWidgets import QHBoxLayout
from qtpy.QtWidgets import QLabel
from qtpy.QtWidgets import QPushButton
from qtpy.QtWidgets import QTabWidget
from qtpy.QtWidgets import QVBoxLayout
from qtpy.QtWidgets import QWidget
from sharpmod.ui.features.gui_animation import AnimationWorkspace
from sharpmod.ui.features.gui_common import make_status_label
from sharpmod.ui.features.gui_common import scrollable_page
from sharpmod.ui.features.gui_theme import current_density
from sharpmod.ui.analysis_controls import inset_page
from sharpmod.ui.styles.theme import CONTROL_H
from sharpmod.ui.styles.theme import OBJ_GHOST
from sharpmod.ui.styles.theme import OBJ_HINT
from sharpmod.ui.styles.theme import OBJ_SECTION_LABEL
from sharpmod.ui.styles.theme import SPACE
from sharpmod.ui.analysis.workspace import _LOGGER, TAB_GROUPS, TAB_SPECS


class WorkspaceUiMixin:
    """Focused methods shared by AnalysisWorkspace."""

    @staticmethod
    def _section(text, parent=None):
        label = QLabel(text, parent)
        label.setObjectName(OBJ_SECTION_LABEL)
        return label

    @staticmethod
    def _new_status(parent):
        return make_status_label(parent=parent)

    @staticmethod
    def _new_action(text, tooltip, parent):
        """Tertiary action styling: these sit beside status prose, not below it."""
        button = QPushButton(text, parent)
        button.setObjectName(OBJ_GHOST)
        button.setToolTip(tooltip)
        return button

    @staticmethod
    def _new_page(parent):
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        inset_page(layout)
        return page, layout

    @staticmethod
    def _new_row(spacing="sm"):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE[spacing])
        return row

    def _add_tab(self, page, key):
        """Append one registered page, keeping tab order and the registry aligned."""
        spec = TAB_SPECS[self.tabs.count()]
        if spec.key != key:  # pragma: no cover - guarded by a focused test
            raise RuntimeError(
                f"analysis tab {self.tabs.count()} built as {key!r}, "
                f"registry expects {spec.key!r}"
            )
        index = self.tabs.addTab(
            scrollable_page(
                page, parent=self.tabs, accessible_name=f"{spec.label} content"
            ),
            spec.label,
        )
        self.tabs.setTabToolTip(index, f"{spec.group} · {spec.tooltip}")
        return index

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        outer.setSpacing(SPACE["sm"])
        self._build_navigator(outer)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("analysisWorkspaceTabs")
        # Never abbreviate a page name: the navigator takes over when the bar
        # cannot show every label in full. Qt's scroll buttons stay enabled on
        # purpose -- disabling them makes the bar's minimum width the sum of all
        # labels, which would pin the whole dock open at that width.
        self.tabs.tabBar().setElideMode(Qt.ElideNone)
        outer.addWidget(self.tabs)
        # Built in registry order, so a tab index, its TAB_* constant, and its
        # registry entry always describe the same page.
        self._build_trends_tab()
        self._build_compare_tab()
        self._build_ensemble_tab()
        self._build_animation_tab()
        self._populate_navigator()
        self._connect()
        self._establish_focus_order()

    def _build_navigator(self, outer):
        """A grouped section chooser for when the tab labels do not fit.

        The workspace lives in a side dock, so the tab bar frequently cannot
        show every page. Qt's default answer is scroll arrows, which hide most
        of the analysis behind an affordance that says nothing about what is
        there. This chooser lists every page under its group heading, so the
        whole workspace stays one click away and readable at any dock width.
        """
        self.navigator_row = QWidget(self)
        self.navigator_row.setObjectName("analysisNavigatorRow")
        row = QHBoxLayout(self.navigator_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["sm"])
        caption = QLabel("Section", self.navigator_row)
        caption.setObjectName(OBJ_HINT)
        row.addWidget(caption)
        self.navigator = QComboBox(self.navigator_row)
        self.navigator.setObjectName("analysisNavigator")
        self.navigator.setAccessibleName("Analysis section, grouped by purpose")
        self.navigator.setMinimumHeight(
            24 if current_density() == "compact" else CONTROL_H["sm"]
        )
        self.navigator.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        caption.setBuddy(self.navigator)
        row.addWidget(self.navigator, 1)
        outer.addWidget(self.navigator_row)
        self.navigator_row.setVisible(False)

    def _populate_navigator(self):
        """List every registered page under its group heading, once."""
        blocked = self.navigator.blockSignals(True)
        self.navigator.clear()
        model = self.navigator.model()
        for group in TAB_GROUPS:
            members = [
                (index, spec)
                for index, spec in enumerate(TAB_SPECS)
                if spec.group == group
            ]
            if not members:
                continue
            self.navigator.addItem(group)
            heading = model.item(self.navigator.count() - 1)
            if heading is not None:
                # A heading names its group; it is not somewhere to navigate to.
                heading.setFlags(Qt.NoItemFlags)
            for index, spec in members:
                # The plain label, so the closed chooser reads exactly like the
                # tab it selects. Grouping is carried by order plus the disabled
                # headings above, which is Qt's own idiom for a grouped list.
                self.navigator.addItem(spec.label, index)
                item = model.item(self.navigator.count() - 1)
                if item is not None:
                    item.setToolTip(f"{spec.group} · {spec.tooltip}")
        self.navigator.blockSignals(blocked)
        self._sync_navigator(self.tabs.currentIndex())

    def _sync_navigator(self, index):
        """Point the chooser at the active page without re-entering selection."""
        for position in range(self.navigator.count()):
            if self.navigator.itemData(position) == index:
                blocked = self.navigator.blockSignals(True)
                self.navigator.setCurrentIndex(position)
                self.navigator.blockSignals(blocked)
                spec = TAB_SPECS[index] if 0 <= index < len(TAB_SPECS) else None
                if spec is not None:
                    self.navigator.setToolTip(f"{spec.group} · {spec.tooltip}")
                return

    def _navigator_activated(self, position):
        index = self.navigator.itemData(position)
        if isinstance(index, int) and 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _tab_bar_fits(self, width=None):
        """Whether every label can be read in the width actually available."""
        bar = self.tabs.tabBar()
        if bar.count() == 0:
            return True
        available = self.width() if width is None else int(width)
        margins = self.layout().contentsMargins() if self.layout() else None
        if margins is not None:
            available -= margins.left() + margins.right()
        return bar.sizeHint().width() <= max(0, available)

    def _apply_navigation_layout(self, width=None):
        """Show the tab bar when it fits, otherwise the grouped chooser.

        Qt no-ops a ``setVisible`` that changes nothing, and the explicit hidden
        flag is the only reliable signal here: the workspace lives in a dock that
        starts hidden, so ``isVisible()`` is ``False`` for both states.
        """
        fits = self._tab_bar_fits(width)
        self.tabs.tabBar().setVisible(fits)
        self.navigator_row.setVisible(not fits)
        return fits

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_navigation_layout(event.size().width())

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        # The dock starts hidden, so the first real width arrives here.
        self._apply_navigation_layout()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        # A larger interface text size widens every label, so the decision has
        # to be remade rather than cached from construction.
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            self._apply_navigation_layout()

    def tab_key(self, index=None):
        """Return the stable key of a tab index, or the active one."""
        position = self.tabs.currentIndex() if index is None else int(index)
        if 0 <= position < len(TAB_SPECS):
            return TAB_SPECS[position].key
        return ""

    @staticmethod
    def tab_index_for(name):
        """Resolve a stable key or a reader-facing label to a tab index."""
        wanted = str(name).strip().casefold()
        # Older sessions stored the former Share key for the same GIF page.
        if wanted == "share":
            wanted = "animation"
        for index, spec in enumerate(TAB_SPECS):
            if wanted in {spec.key.casefold(), spec.label.casefold()}:
                return index
        return None

    def _build_animation_tab(self):
        self.animation_workspace = AnimationWorkspace(
            self, request_sounding=self._request_sounding_input, parent=self.tabs
        )
        self._add_tab(self.animation_workspace, "animation")

    def _settings(self):
        """Return the application settings this viewer uses, if reachable."""
        win = self._window()
        settings = getattr(win, "_settings", None) if win is not None else None
        if settings is not None:
            return settings
        try:
            from sharpmod.ui.features.gui_settings import _build_settings

            return _build_settings()
        except Exception:
            _LOGGER.debug("analysis_workspace.settings_unavailable")
            return None

    def _connect(self):
        self.tabs.currentChanged.connect(self._refresh_active_tab)
        self.tabs.currentChanged.connect(self._sync_navigator)
        self.navigator.activated.connect(self._navigator_activated)
        self.compare_columns.clicked.connect(self._choose_comparison_columns)
        self.trend_metric.currentIndexChanged.connect(self._refresh_trends)
        self.trend_load.clicked.connect(
            lambda: self._request_sounding_input("Forecast Model")
        )
        self.trend_refresh.clicked.connect(self._refresh_trends)
        self.trend_export.clicked.connect(self._choose_trend_export)
        self.trend_chart.pointActivated.connect(self._activate_trend_point)
        self.compare_reference.currentIndexChanged.connect(self._comparison_reference_changed)
        self.compare_add.clicked.connect(lambda: self._request_sounding_input())
        self.compare_refresh.clicked.connect(self._refresh_compare)
        self.compare_export.clicked.connect(self._choose_comparison_export)
        self.ensemble_refresh.clicked.connect(self._refresh_ensemble)
        self.ensemble_load.clicked.connect(
            lambda: self._request_sounding_input("Forecast Model")
        )
        self.ensemble_retry.clicked.connect(self._retry_unavailable_ensemble)
        self.ensemble_export.clicked.connect(self._choose_ensemble_export)

    def _establish_focus_order(self):
        """Give the first adopted workspaces a predictable keyboard route."""
        for widgets in (
            (
                self.trend_metric,
                self.trend_load,
                self.trend_lock,
                self.trend_refit,
                self.trend_tracks,
                self.trend_refresh,
                self.trend_job.cancel_button,
                self.trend_job.retry_button,
                self.trend_export,
                self.trend_chart,
            ),
            (
                self.compare_reference,
                self.compare_add,
                self.compare_refresh,
                self.compare_job.cancel_button,
                self.compare_job.retry_button,
                self.compare_export,
                self.visual_compare.chart_choice,
                self.visual_compare.arrangement_choice,
                self.visual_compare.link_axes,
                self.visual_compare.layout_choice,
                *self.visual_compare.slot_choices,
                self.compare_table,
            ),
            (
                self.ensemble_load,
                self.ensemble_refresh,
                self.ensemble_job.cancel_button,
                self.ensemble_job.retry_button,
                self.ensemble_retry,
                self.ensemble_export,
                self.ensemble_table,
                self.ensemble_chart,
            ),
        ):
            for previous, following in zip(widgets, widgets[1:]):
                QWidget.setTabOrder(previous, following)
