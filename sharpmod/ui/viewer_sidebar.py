"""Viewer Sidebar for the sounding viewer."""

from __future__ import annotations

from qtpy.QtCore import QEvent
from qtpy.QtCore import Qt
from qtpy.QtGui import QAction
from qtpy.QtGui import QActionGroup
from qtpy.QtWidgets import QAbstractItemView
from qtpy.QtWidgets import QDialog
from qtpy.QtWidgets import QDialogButtonBox
from qtpy.QtWidgets import QDockWidget
from qtpy.QtWidgets import QFrame
from qtpy.QtWidgets import QLabel
from qtpy.QtWidgets import QLineEdit
from qtpy.QtWidgets import QListWidget
from qtpy.QtWidgets import QListWidgetItem
from qtpy.QtWidgets import QMenu
from qtpy.QtWidgets import QPlainTextEdit
from qtpy.QtWidgets import QPushButton
from qtpy.QtWidgets import QScrollArea
from qtpy.QtWidgets import QSizePolicy
from qtpy.QtWidgets import QToolBar
from qtpy.QtWidgets import QToolButton
from qtpy.QtWidgets import QVBoxLayout
from qtpy.QtWidgets import QWidget
from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_common import make_status_label
from sharpmod.ui.features.gui_common import set_status_label
from sharpmod.ui.styles.theme import CONTROL_H
from sharpmod.ui.styles.theme import OBJ_HINT
from sharpmod.ui.styles.theme import OBJ_NAV_RAIL
from sharpmod.ui.styles.theme import OBJ_PLAIN
from sharpmod.ui.styles.theme import OBJ_REPORT
from sharpmod.ui.styles.theme import OBJ_SECTION_LABEL
from sharpmod.ui.styles.theme import OBJ_SIDEBAR
from sharpmod.ui.styles.theme import PROP_COMPACT
from sharpmod.ui.styles.theme import SPACE
from sharpmod.ui.styles.theme import VIEWER_SIDEBAR_W
from sharpmod.ui.shell import dock_title_bar
import weakref
from sharpmod import gui_viewer as _api


class _SoundingSidebar(QFrame):
    """Right-hand context panel for the loaded soundings.

    Surfaces the two pieces of viewer state that previously had no on-screen
    representation at all:

    * **Which sounding is focused, and how to change it.** Upstream only
      exposes this as ``Profiles`` -> a submenu per sounding -> ``Focus``: a
      three-level dive that never shows which one is currently active. ``Space``
      cycles, but blindly.
    * **Which ensemble member is highlighted.** Upstream binds this to the
      ``Up``/``Down`` arrows with no visible control and no member names.

    Deliberately *not* included: forecast-time stepping. That already has a
    dedicated toolbar (:func:`sharpmod.ui.features.gui_timeline.install_timeline_controls`)
    with prev/next, a scrub slider, and looping playback, so repeating it here
    would be two controls for one piece of state.

    The panel reflects state rather than owning it -- every mutation goes
    through the vendored widget's own methods, and :meth:`refresh` re-reads
    from it. ``updateProfs`` is the single funnel every upstream state change
    passes through, so :func:`_install_sounding_sidebar` wraps that to keep the
    panel in sync no matter whether the change came from this panel, a menu, or
    a key press.
    """

    def __init__(self, win, parent=None):
        super().__init__(parent)
        # Weak, deliberately. Connecting a bound method to a child's signal
        # makes Qt hold a C++-side reference to this panel, which Python's
        # cyclic GC cannot see -- so a strong reference here would pin the whole
        # sounding window's wrapper for the process lifetime and every
        # open/close cycle would retain a viewer. See
        # test_gui_viewer_lifecycle.py.
        self._win_ref = weakref.ref(win)
        from sharpmod.ui.features.gui_collection_state import install_collection_state

        self._collection_state = install_collection_state(win)
        self._selected_hidden_id = None
        self.setObjectName(OBJ_SIDEBAR)
        # Fixed, not a minimum. The width is a measured budget -- wide enough to
        # be useful, narrow enough that the sounding still fits unclipped at
        # 100% (see VIEWER_SIDEBAR_W). A minimum alone let the panel's own
        # content size hint win, which silently pushed it back to ~264 px and
        # clipped the canvas at Actual Size.
        from sharpmod.ui.features.gui_theme import current_text_scale

        self.setFixedWidth(round(VIEWER_SIDEBAR_W * current_text_scale() / 100))
        host = QVBoxLayout(self)
        host.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(self._scroll)
        body.setObjectName(OBJ_PLAIN)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        outer.setSpacing(SPACE["md"])
        self._scroll.setWidget(body)
        host.addWidget(self._scroll)
        self._sort_key, self._group_key, self._reverse = "loaded", "none", False

        # --- Loaded soundings ---
        outer.addWidget(self._section_label("LOADED SOUNDINGS"))
        self._search = QLineEdit(body)
        self._search.setObjectName("loadedSoundingSearch")
        self._search.setPlaceholderText("Search profiles…")
        self._search.setAccessibleName("Search loaded soundings by identity, location, source or time")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.refresh)
        outer.addWidget(self._search)
        self._organize = QToolButton(body)
        self._organize.setText("Organize…")
        self._organize.setAccessibleName("Group and sort loaded soundings")
        self._organize.setPopupMode(QToolButton.InstantPopup)
        self._organize.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._organize.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._organize.setMenu(self._organization_menu())
        outer.addWidget(self._organize)
        self._list = QListWidget(self)
        self._list.setObjectName(OBJ_NAV_RAIL)
        # Long place names ("Miles Grove Township, Illinois") exceed the panel
        # width, which is set by the sounding's needs rather than the label's.
        # Elide rather than clip, and keep the full text in the tooltip.
        self._list.setTextElideMode(Qt.ElideNone)
        self._list.setWordWrap(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.setToolTip(
            "Click a sounding to bring it into focus (Space also cycles)"
        )
        self._list.currentItemChanged.connect(self._on_pick)
        outer.addWidget(self._list)

        self._empty = QLabel(
            "Open another sounding from the picker to compare it here."
        )
        self._empty.setObjectName(OBJ_HINT)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)
        self._status = make_status_label("", parent=body)
        outer.addWidget(self._status)

        # Default button treatment, not OBJ_GHOST: ghost is for borderless
        # tertiary actions, and a borderless label floating under the list does
        # not read as clickable.
        self._reference = make_status_label("Reference: not pinned", parent=body)
        outer.addWidget(self._reference)
        self._pin = QPushButton("Pin reference", body)
        self._pin.setToolTip("Use the selected profile as the stable comparison reference, even if hidden")
        self._pin.clicked.connect(self._on_pin)
        outer.addWidget(self._pin)
        self._visibility = QPushButton("Hide selected", body)
        self._visibility.setToolTip("Hide the selected overlay without removing its data or changing reference/order")
        self._visibility.clicked.connect(self._on_visibility)
        outer.addWidget(self._visibility)
        self._collection_feedback = make_status_label("", parent=body)
        self._collection_feedback.hide()
        outer.addWidget(self._collection_feedback)
        self._remove = QPushButton("Remove selected")
        self._remove.setToolTip("Close the focused sounding and keep the others open")
        self._remove.clicked.connect(self._on_remove)
        outer.addWidget(self._remove)

        # --- Ensemble members ---
        self._member_label = self._section_label("ENSEMBLE MEMBER")
        outer.addWidget(self._member_label)
        self._members = QListWidget(self)
        self._members.setObjectName(OBJ_NAV_RAIL)
        # Member names are one line, so they must not inherit the two-line row
        # height the sounding list needs -- at 54 px a list of member names
        # reads as unfinished rather than spacious.
        self._members.setProperty(PROP_COMPACT, True)
        self._members.setToolTip(
            "Highlight a member (the Up/Down arrows also step through these)"
        )
        self._members.currentItemChanged.connect(self._on_member)
        outer.addWidget(self._members)

        outer.addStretch(1)

        # --- Provenance ---
        self._inspect = QPushButton("Source && Quality\u2026")
        self._inspect.setToolTip(
            "Extractor provenance and structural checks for the focused sounding"
        )
        self._inspect.clicked.connect(self._on_inspect)
        outer.addWidget(self._inspect)

        # Guards re-entry while refresh() is writing into the lists: setting
        # the current row emits currentItemChanged, which would otherwise be
        # read as a user pick and re-focus the collection mid-refresh.
        self._syncing = False
        from qtpy.QtCore import QTimer as OwnedTimer

        self._chrome_timer = OwnedTimer(self)
        self._chrome_timer.setSingleShot(True)
        self._chrome_timer.timeout.connect(self.refresh)
        self._collection_state.changed.connect(self._on_collection_state_changed)
        self._collection_state.feedback.connect(self._set_collection_feedback)
        self.refresh()

    def _set_collection_feedback(self, message, level):
        set_status_label(self._collection_feedback, message, level=level)
        self._collection_feedback.setVisible(bool(message))

    def _on_collection_state_changed(self):
        self.refresh()
        workspace = getattr(self._window(), "_sharpmod_analysis_workspace", None)
        if workspace is not None:
            workspace.refresh()

    def _selected_id(self):
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_pin(self):
        profile_id = self._selected_id()
        if profile_id is not None:
            state = self._collection_state
            state.pin(None if state.reference_id == profile_id else profile_id)

    def _on_visibility(self):
        profile_id = self._selected_id()
        if profile_id is not None:
            state = self._collection_state
            showing = profile_id in state.hidden_ids
            self._selected_hidden_id = None if showing else profile_id
            if not state.set_visible(profile_id, showing):
                self._selected_hidden_id = None
            self.refresh()

    def changeEvent(self, event):  # noqa: N802 - Qt API
        super().changeEvent(event)
        timer = getattr(self, "_chrome_timer", None)
        if timer is not None and event.type() in (QEvent.FontChange, QEvent.StyleChange) and not timer.isActive():
            timer.start(0)

    def _organization_menu(self):
        from sharpmod.state.collection_identity import ORGANIZE_FIELDS

        menu = QMenu(self._organize)
        self._sort_actions, self._group_actions = {}, {}
        for title, choices, attribute, actions in (
            ("Sort by", ORGANIZE_FIELDS, "_sort_key", self._sort_actions),
            ("Group by", (("No groups", "none"), *ORGANIZE_FIELDS[1:]), "_group_key", self._group_actions),
        ):
            submenu = menu.addMenu(title)
            group = QActionGroup(submenu)
            group.setExclusive(True)
            for label, key in choices:
                action = QAction(label, submenu, checkable=True)
                action.setChecked(key == getattr(self, attribute))
                group.addAction(action)
                submenu.addAction(action)
                actions[key] = action
                action.triggered.connect(lambda _checked=False, key=key, attribute=attribute:
                                         self._set_organization(attribute, key))
        menu.addSeparator()
        self._reverse_action = menu.addAction("Reverse sort (newest first for times)")
        self._reverse_action.setCheckable(True)
        self._reverse_action.toggled.connect(self._set_reverse)
        return menu

    def _set_organization(self, attribute, key):
        setattr(self, attribute, key)
        self.refresh()

    def _set_reverse(self, reverse):
        self._reverse = bool(reverse)
        self.refresh()

    def session_state(self):
        return {"version": 1, "query": self._search.text(), "sort": self._sort_key,
                "group": self._group_key, "reverse": self._reverse}

    def restore_session_state(self, state):
        if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
            return
        sort, group = state.get("sort"), state.get("group")
        self._sort_key = sort if isinstance(sort, str) and sort in self._sort_actions else "loaded"
        self._group_key = group if isinstance(group, str) and group in self._group_actions else "none"
        self._reverse = state.get("reverse") is True
        self._sort_actions[self._sort_key].setChecked(True)
        self._group_actions[self._group_key].setChecked(True)
        blocked = self._reverse_action.blockSignals(True)
        self._reverse_action.setChecked(self._reverse)
        self._reverse_action.blockSignals(blocked)
        query = state.get("query", "")
        blocked = self._search.blockSignals(True)
        self._search.setText(query[:1024] if isinstance(query, str) else "")
        self._search.blockSignals(blocked)
        self._selected_hidden_id = None
        self.refresh()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(OBJ_SECTION_LABEL)
        return label

    # -- state ----------------------------------------------------------
    def _window(self):
        """The sounding window, or ``None`` once it has been destroyed."""
        return self._win_ref()

    def _widget(self):
        return getattr(self._window(), "spc_widget", None)

    def _focused_collection(self):
        sw = self._widget()
        try:
            return sw.prof_collections[int(sw.pc_idx)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def refresh(self) -> None:
        """Re-read the vendored widget and repaint the panel."""
        sw = self._widget()
        if sw is None or self._syncing:
            return
        self._syncing = True
        try:
            from sharpmod.ui.features.gui_theme import current_text_scale

            self.setFixedWidth(round(VIEWER_SIDEBAR_W * current_text_scale() / 100))
            self._collection_state.prune()
            self._refresh_soundings(sw)
            self._refresh_members()
        except Exception:
            _LOGGER.exception("sounding_sidebar.refresh_failed")
        finally:
            self._syncing = False

    def _refresh_soundings(self, sw) -> None:
        ids = list(getattr(sw, "prof_ids", []) or [])
        try:
            active = int(sw.pc_idx)
        except (AttributeError, TypeError, ValueError):
            active = 0

        from sharpmod.state.collection_identity import collection_identities, organize_identities

        active_id = ids[active] if 0 <= active < len(ids) else None
        rows = organize_identities(collection_identities(sw), query=self._search.text(),
                                   sort=self._sort_key, group=self._group_key, reverse=self._reverse)
        self._list.clear()
        selected = None
        state = self._collection_state
        selected_id = (self._selected_hidden_id if self._selected_hidden_id in state.hidden_ids else active_id)
        for group, identity in rows:
            if identity is None:
                item = QListWidgetItem(group)
                item.setFlags(Qt.NoItemFlags)
                item.setToolTip(f"Group: {group}")
            else:
                marks = [label for condition, label in (
                    (identity.profile_id == active_id, "Focused"),
                    (identity.profile_id == state.reference_id, "Pinned reference"),
                    (identity.profile_id in state.hidden_ids, "Hidden"),
                ) if condition]
                item = QListWidgetItem(identity.label + ("\n" + " · ".join(marks) if marks else ""))
                item.setData(Qt.UserRole, identity.profile_id)
                item.setToolTip(identity.details)
                if identity.profile_id == selected_id:
                    selected = item
            self._list.addItem(item)
        if selected is not None:
            self._list.setCurrentItem(selected)

        multiple = len(ids) > 1
        # One sounding is not an error state, so the hint only appears when the
        # comparison feature is actually unused -- and the list still shows the
        # single sounding's identity, which is useful on its own.
        matching = sum(identity is not None for _group, identity in rows)
        self._empty.setText("No profiles match. Clear the search; the focused profile is unchanged."
                            if not matching else "Open another sounding from the picker to compare it here.")
        self._empty.setVisible(not multiple or not matching)
        # Mirrors upstream, which hides "Remove" while a single sounding is
        # loaded: removing the last one would leave an empty window.
        self._remove.setEnabled(multiple and selected is not None)
        self._remove.setToolTip(
            "Close the focused sounding and keep the others open"
            if multiple and selected is not None
            else "Clear the search to show the focused profile"
            if multiple
            else "The only loaded sounding cannot be removed"
        )
        # Height-to-content, so a single sounding does not leave a tall empty
        # well above the member list.
        self._list.setFixedHeight(self._list_height(self._list, len(rows)))
        set_status_label(self._status, f"{matching} of {len(ids)} profiles · "
                         f"Sort: {self._sort_key.replace('_', ' ')} · Group: {self._group_key.replace('_', ' ')}")
        self._organize.setToolTip(self._status.text() + "\nOrganization changes only the list, not source order.")
        self._pin.setEnabled(selected is not None)
        self._pin.setText("Unpin reference" if selected_id == state.reference_id and selected is not None else "Pin reference")
        hidden = selected_id in state.hidden_ids
        self._visibility.setText("Show selected" if hidden else "Hide selected")
        self._visibility.setEnabled(selected is not None and (hidden or len(set(ids) - state.hidden_ids) > 1))
        reference = next((identity for identity in collection_identities(sw) if identity.profile_id == state.reference_id), None)
        reference_text = ("Pinned reference: " + reference.label +
                          ("\nHidden (display only)" if state.reference_id in state.hidden_ids else "")
                          if reference is not None else "Reference: not pinned")
        set_status_label(self._reference, reference_text)

    def _refresh_members(self) -> None:
        collection = self._focused_collection()
        members: list[str] = []
        current = None
        if collection is not None:
            try:
                if collection.isEnsemble():
                    profs = collection.getCurrentProfs() or {}
                    members = sorted(profs.keys())
                    highlighted = collection.getHighlightedProf()
                    for name, prof in profs.items():
                        if prof is highlighted:
                            current = name
                            break
            except Exception:
                members = []

        # A deterministic sounding has no members to choose between, so the
        # whole section is hidden rather than shown empty or disabled.
        show = len(members) > 1
        self._member_label.setVisible(show)
        self._members.setVisible(show)
        if not show:
            self._members.clear()
            return

        self._members.clear()
        for name in members:
            item = QListWidgetItem(name or "(unnamed)")
            item.setData(Qt.UserRole, name)
            self._members.addItem(item)
        if current in members:
            self._members.setCurrentRow(members.index(current))
        self._members.setFixedHeight(self._list_height(self._members, len(members)))

    @staticmethod
    def _list_height(widget: QListWidget, rows: int) -> int:
        """Exact height for ``rows`` items, capped so long lists still scroll."""
        rows = max(1, rows)
        row_h = widget.sizeHintForRow(0) if widget.count() else 0
        if row_h <= 0:
            row_h = CONTROL_H["md"]
        frame = 2 * widget.frameWidth()
        visible = min(rows, 6)
        from sharpmod.ui.features.gui_theme import current_text_scale

        return min(visible * row_h + frame + SPACE["xs"], round(280 * current_text_scale() / 100))

    @staticmethod
    def _describe(prof_id: str, collection) -> str:
        """Two-line row: location, then the model and run that produced it.

        Upstream's own label is a single ``"KOUN (27/1200Z GFS)"`` string. Split
        across two lines the location -- the thing being compared -- is scannable
        down the list instead of being read out of a parenthesised suffix.
        """
        loc = model = run = None
        if collection is not None:
            for key, target in (("loc", "loc"), ("model", "model")):
                try:
                    value = collection.getMeta(key)
                except Exception:
                    value = None
                if value:
                    if target == "loc":
                        loc = str(value)
                    else:
                        model = str(value)
            try:
                run_dt = collection.getMeta("run")
                run = run_dt.strftime("%d %b %H%MZ")
            except Exception:
                run = None
        if not loc:
            return prof_id
        detail = " \u00b7 ".join(part for part in (model, run) if part)
        return f"{loc}\n{detail}" if detail else loc

    # -- actions --------------------------------------------------------
    def _on_pick(self, current, _previous) -> None:
        if self._syncing or current is None:
            return
        sw = self._widget()
        prof_id = current.data(Qt.UserRole)
        if sw is None or not prof_id:
            return
        if prof_id in self._collection_state.hidden_ids:
            self._selected_hidden_id = prof_id
            self._set_collection_feedback("This profile is hidden. Use Show selected before focusing it.", "warning")
            self._pin.setEnabled(True)
            self._visibility.setEnabled(True)
            self._visibility.setText("Show selected")
            self._remove.setEnabled(len(self._collection_state.ids()) > 1)
            self.refresh()
            return
        self._selected_hidden_id = None
        try:
            sw.setProfileCollection(prof_id)
        except Exception:
            _LOGGER.exception("sounding_sidebar.focus_failed")

    def _on_remove(self) -> None:
        item = self._list.currentItem()
        prof_id = item.data(Qt.UserRole) if item is not None else None
        win = self._window()
        if not prof_id or win is None:
            return
        try:
            # Window-level, not widget-level: this also hides the matching
            # Profiles submenu entry, which the widget-level call leaves behind.
            win.rmProfileCollection(prof_id)
        except Exception:
            _LOGGER.exception("sounding_sidebar.remove_failed")
        self.refresh()

    def _on_member(self, current, _previous) -> None:
        if self._syncing or current is None:
            return
        collection = self._focused_collection()
        name = current.data(Qt.UserRole)
        sw = self._widget()
        if collection is None or sw is None or name is None:
            return
        try:
            collection.setHighlightedMember(name)
            sw.updateProfs()
        except Exception:
            _LOGGER.exception("sounding_sidebar.member_failed")

    def _on_inspect(self) -> None:
        action = getattr(self._window(), "_sharpmod_data_inspector_action", None)
        if action is not None:
            action.trigger()


def _dock_title_bar(dock: QDockWidget, title: str) -> QFrame:
    """Themed sidebar title bar, carrying this dock's own toggle shortcut.

    The shared implementation lives in :func:`sharpmod.ui.shell.dock_title_bar`
    because the analysis workspace dock needs the same treatment; only the
    advertised shortcut differs.
    """
    return dock_title_bar(dock, title, shortcut_hint="Ctrl+B")


def _reserved_toolbar_height(win) -> int:
    """Height the top-area toolbars take out of the central widget.

    Counterpart to :func:`_reserved_dock_width`, and needed for the same reason:
    :func:`_fit_window_to_screen` sizes the window from the *screen*, so it has
    to know the sounding does not get the full height. Only the top area counts
    -- a left or right toolbar costs width, not height, and none is used here.

    Uses ``not isHidden()`` rather than ``isVisible()``: the only caller runs
    while the window is still hidden, where ``isVisible()`` is False for every
    descendant. See :func:`_reserved_dock_width`.
    """
    total = 0
    try:
        for bar in win.findChildren(QToolBar):
            if bar.isHidden() or bar.isFloating():
                continue
            if win.toolBarArea(bar) != Qt.TopToolBarArea:
                continue
            total += max(bar.height(), bar.sizeHint().height())
    except Exception:
        return 0
    return total


def _reserved_dock_width(win) -> int:
    """Width the visible docks take out of the central widget's viewport.

    The fit math sizes the window from the *screen*, so it has to know that the
    sounding does not get the full width. After the window is realized
    :func:`_finalize_scaled_fit` measures the viewport directly; this is only
    for the pre-show pass.

    The predicate is ``not isHidden()``, not ``isVisible()``. Both callers run
    from :func:`_fit_window_to_screen`, which happens between the ``win.hide()``
    in :func:`compose_interactive` and the matching ``showNormal()`` -- and
    ``isVisible()`` is False for *every* descendant of an unshown window, so the
    visible-dock test rejected the dock unconditionally and this returned 0 on
    every production call. ``isHidden()`` reflects the widget's own explicit
    hide state rather than the ancestor chain, so it still answers False for a
    dock the user closed while remaining correct before the first show.
    ``sizeHint()`` is valid either way; only the guard was wrong.
    """
    total = 0
    try:
        for dock in win.findChildren(QDockWidget):
            if not dock.isHidden() and not dock.isFloating():
                total += max(dock.width(), dock.sizeHint().width())
    except Exception:
        return 0
    return total


def _install_sounding_sidebar(win) -> None:
    """Dock the sounding context panel on the right.

    A dock is used rather than restructuring the central widget because
    :func:`_fit_window_to_screen` and :func:`_finalize_scaled_fit` both key off
    ``win.centralWidget()``, and the tests construct the sounding hosts
    directly. Docking leaves that contract untouched, and the existing
    ``chrome_w = win.width() - viewport.width()`` measurement already accounts
    for the panel's width with no change to the fit math.

    The width is free: see :data:`sharpmod.ui.styles.theme.VIEWER_SIDEBAR_W`.
    """
    try:
        panel = _api._SoundingSidebar(win)
        # The title doubles as the View-menu entry via toggleViewAction(), so
        # naming the dock once keeps the menu item and the panel header
        # identical instead of drifting apart.
        dock = QDockWidget("Sounding Panel", win)
        dock.setObjectName("soundingSidebar")
        # Keep the measured sidebar/canvas budget without giving its *dock*
        # a fixed maximum. Qt applies that maximum to every tabified sibling;
        # it otherwise prevents the analysis workspace from ever being widened.
        holder = QWidget(dock)
        holder.setObjectName(OBJ_PLAIN)
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(panel)
        dock.setWidget(holder)
        # Not floatable: a floating panel would sit over the sounding, and the
        # whole point is to use space the sounding cannot.
        dock.setFeatures(QDockWidget.DockWidgetClosable)
        dock.setAllowedAreas(Qt.RightDockWidgetArea)
        dock.setTitleBarWidget(_api._dock_title_bar(dock, dock.windowTitle()))
        win.addDockWidget(Qt.RightDockWidgetArea, dock)

        toggle = dock.toggleViewAction()
        toggle.setShortcut("Ctrl+B")
        toggle.setToolTip("Show or hide the sounding list and ensemble members")
        menu = getattr(win, "_sharpmod_view_menu", None)
        if menu is not None:
            menu.addSeparator()
            menu.addAction(toggle)

        win._sharpmod_sidebar = panel
        win._sharpmod_sidebar_dock = dock

        # Every upstream state change -- menu Focus, Space, arrow keys, the
        # timeline toolbar, adding a sounding -- funnels through updateProfs,
        # so wrapping it is what keeps the panel truthful without polling.
        sw = getattr(win, "spc_widget", None)
        original = getattr(sw, "updateProfs", None)
        if original is not None and not getattr(sw, "_sharpmod_profs_wrapped", False):
            # Weak, for the same reason the panel holds the window weakly: the
            # widget is a child of the window, so a strong capture here would
            # close a reference cycle Qt keeps alive outside Python's GC.
            panel_ref = weakref.ref(panel)

            def updateProfs(*args, **kwargs):  # noqa: N802 - matches upstream
                live = panel_ref()
                if live is not None:
                    live._collection_state.ensure_visible_focus()
                result = original(*args, **kwargs)
                if live is not None:
                    try:
                        live.refresh()
                    except Exception:
                        _LOGGER.exception("sounding_sidebar.sync_failed")
                    workspace = getattr(
                        live._window(), "_sharpmod_analysis_workspace", None
                    )
                    if workspace is not None:
                        try:
                            workspace.refresh()
                        except Exception:
                            _LOGGER.exception("analysis_workspace.sync_failed")
                return result

            sw.updateProfs = updateProfs
            sw._sharpmod_profs_wrapped = True
    except Exception as exc:
        _LOGGER.exception("sounding_sidebar.install_failed")
        _api._record_install_failure(win, "Sounding sidebar", exc)


def _install_data_inspector(win, prof_col) -> None:
    """Add a copyable source-provenance and conservative QC report."""
    try:
        menu = win.menuBar().addMenu("Data")
        action = QAction("Source && Quality Inspector…", win)
        # See _install_export_menu: resolved into a local of the same name so
        # this handler does not close over the window it is parented to.
        win_ref = weakref.ref(win)

        def show_report(_checked=False):
            from sharpmod.ui.features.profile_inspector import format_report

            win = win_ref()
            if win is None:
                return

            focused = prof_col
            try:
                widget = win.spc_widget
                focused = widget.prof_collections[int(widget.pc_idx)]
            except (AttributeError, IndexError, TypeError, ValueError):
                pass

            dialog = QDialog(win)
            dialog.setWindowTitle("Sounding Source & Quality")
            # Wider than the old 760x540: the report contains full GRIB URLs and
            # Windows temp paths, which at that width wrapped mid-token.
            dialog.resize(1000, 680)
            layout = QVBoxLayout(dialog)
            intro = QLabel(
                "Extractor provenance and non-mutating structural checks for "
                "the focused sounding."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            report = QPlainTextEdit()
            report.setReadOnly(True)
            # The report is a column-aligned table, so it needs the monospace
            # family -- it was rendering in the proportional UI face, which left
            # every value column ragged. Set via object name so the family comes
            # from the style sheet: render.install_font patches QFont
            # process-wide, so a QFont built here does not survive.
            report.setObjectName(OBJ_REPORT)
            # No wrapping, for the same reason: wrapping a fixed-width table
            # destroys the alignment, and it broke long URLs across lines.
            # A horizontal scrollbar is the honest alternative.
            report.setLineWrapMode(QPlainTextEdit.NoWrap)
            report.setPlainText(format_report(focused))
            layout.addWidget(report, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            # One connection. Close carries RejectRole, so the extra
            # clicked->accept both raced this and could never mean anything
            # different -- nothing reads the result code.
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            try:
                dialog.exec()
            finally:
                # Parented to the window, so Qt would otherwise keep this
                # dialog -- and its full report text -- alive until the window
                # dies, one per invocation. Scheduled after exec rather than via
                # WA_DeleteOnClose, which deletes from inside the close that
                # ends the modal loop and segfaults on teardown. Guarded because
                # the parent could be destroyed while the modal is up, leaving
                # this wrapper stale. See gui_common._show_controls_dialog.
                try:
                    dialog.deleteLater()
                except RuntimeError:
                    pass

        action.triggered.connect(show_report)
        menu.addAction(action)
        win._sharpmod_data_inspector_action = action
    except Exception as exc:
        _LOGGER.exception("data_inspector.install_failed")
        _api._record_install_failure(win, "Data inspector", exc)
