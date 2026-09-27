"""A readable view of the existing bounded sounding edit history.

The application already records every vendored profile mutation in one
:class:`~sharpmod.state.sessions.AnalysisHistory` per window, but the only way to read
it was the Undo/Redo action text, which names a single step. This panel lists the
retained sequence, says which profile each change affected, marks the point the
current profile state sits at, and lets the user move to any point by reusing the
same undo/redo path. It deliberately adds no second restore mechanism.
"""

from __future__ import annotations

import weakref

from qtpy.QtCore import Qt
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from sharpmod.state.collection_identity import collection_identities
from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE

#: The row above every recorded change, representing the profile as loaded.
ORIGINAL_ROW = "Original, before any recorded change"


def _profile_label(widget, collection_index: int) -> str:
    """Name the profile a change affected, using the shared identity model."""
    try:
        identities = collection_identities(widget)
    except Exception:
        identities = ()
    if 0 <= collection_index < len(identities):
        identity = identities[collection_index]
        parts = [part for part in (identity.location, identity.source) if part]
        return " · ".join(parts) or identity.profile_id
    # The profile was closed after the change was recorded. Say so rather than
    # naming whichever profile happens to occupy that slot now.
    return f"Profile {collection_index + 1}, no longer loaded"


class EditHistoryDialog(QDialog):
    """List retained changes and move the profile state to a chosen point."""

    def __init__(self, window, history):
        super().__init__(window)
        self._window_ref = weakref.ref(window)
        self._history_ref = weakref.ref(history)
        self.setWindowTitle("Sounding edit history")
        self.setObjectName("editHistoryDialog")
        self.resize(
            min(820, max(420, window.width() - 80)),
            min(620, max(420, window.height() - 80)),
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])

        self.hint = QLabel(self)
        self.hint.setObjectName(OBJ_HINT)
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText)
        layout.addWidget(self.hint)

        self.steps = QListWidget(self)
        self.steps.setAccessibleName("Recorded sounding changes, oldest first")
        self.steps.setWordWrap(True)
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        layout.addWidget(self.steps, 1)

        self.status = make_status_label("", parent=self)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.move_button = buttons.addButton(
            "Move to selected point", QDialogButtonBox.ActionRole
        )
        self.undo_button = buttons.addButton("Undo", QDialogButtonBox.ActionRole)
        self.redo_button = buttons.addButton("Redo", QDialogButtonBox.ActionRole)
        for button in buttons.buttons():
            button.setAutoDefault(False)
        layout.addWidget(buttons)

        buttons.rejected.connect(self.reject)
        self.move_button.clicked.connect(self.move_to_selected)
        self.undo_button.clicked.connect(self._undo)
        self.redo_button.clicked.connect(self._redo)
        self.steps.itemActivated.connect(self.move_to_selected)
        self.steps.currentItemChanged.connect(self._selection_changed)

        history.add_listener(self.refresh)
        # Inspect mode locks every other editing route, so it must lock these
        # too rather than leaving one panel that can still change the profile.
        for name in ("_sharpmod_inspect_action", "_sharpmod_edit_action"):
            action = getattr(window, name, None)
            if action is not None:
                action.toggled.connect(self._mode_changed)
        self.refresh()
        self.steps.setFocus()

    # -- presentation -------------------------------------------------------

    def refresh(self) -> None:
        history, window = self._history_ref(), self._window_ref()
        if history is None or window is None:
            return
        widget = getattr(window, "spc_widget", None)
        try:
            steps = history.steps()
            applied = history.applied_count()
        except Exception:
            steps, applied = (), 0

        remembered = self.steps.currentItem()
        remembered_value = (
            remembered.data(Qt.UserRole) if remembered is not None else None
        )

        self.steps.blockSignals(True)
        self.steps.clear()
        current_row = 0
        original = QListWidgetItem(
            f"{ORIGINAL_ROW}\n"
            + (
                "Current profile state"
                if applied == 0
                else "Move here to undo every recorded change"
            )
        )
        original.setData(Qt.UserRole, 0)
        self.steps.addItem(original)
        for step in steps:
            state = (
                "Current profile state"
                if step.number == applied
                else step.state_text
            )
            # State and time share one line, so a row stays three lines even at
            # the largest interface text size.
            lines = [
                f"{step.number}. {step.label}",
                _profile_label(widget, step.collection_index),
                f"{state} · Recorded: {step.recorded_text}",
            ]
            item = QListWidgetItem("\n".join(lines))
            item.setData(Qt.UserRole, step.number)
            item.setToolTip("\n".join(lines))
            self.steps.addItem(item)
            if step.number == applied:
                current_row = self.steps.count() - 1

        target_row = current_row
        if remembered_value is not None:
            for row in range(self.steps.count()):
                if self.steps.item(row).data(Qt.UserRole) == remembered_value:
                    target_row = row
                    break
        self.steps.setCurrentRow(target_row)
        self.steps.blockSignals(False)
        self._ensure_row_visible(target_row)

        limit = getattr(history, "limit", 0)
        self.hint.setText(
            f"This window keeps the last {limit} recorded changes. "
            f"{len(steps)} recorded, {applied} currently applied. "
            "Moving to a point uses the same Undo and Redo path, so it stays reversible."
        )
        self._refresh_buttons()
        self._selection_changed()

    def _ensure_row_visible(self, row: int) -> None:
        """Show the acted-on row whole, not clipped by the viewport edge.

        At large interface text sizes a wrapped four-line row is taller than the
        default scroll step, so Qt's own "make visible" can leave the row the
        buttons act on cut off at the top or bottom.
        """
        item = self.steps.item(row)
        if item is None:
            return
        bar = self.steps.verticalScrollBar()
        rect = self.steps.visualItemRect(item)
        viewport = self.steps.viewport().height()
        if rect.height() >= viewport:
            # Taller than the viewport: align the top so reading starts there.
            bar.setValue(min(bar.maximum(), max(bar.minimum(), bar.value() + rect.top())))
            return
        if rect.top() < 0:
            bar.setValue(max(bar.minimum(), bar.value() + rect.top()))
        elif rect.bottom() > viewport:
            bar.setValue(min(bar.maximum(), bar.value() + rect.bottom() - viewport))

    def _mode_changed(self, *_args) -> None:
        self._refresh_buttons()
        self._selection_changed()

    def edits_allowed(self) -> bool:
        """Whether the window's current interaction mode permits a change."""
        window = self._window_ref()
        mode = getattr(window, "_sharpmod_interaction_mode", None) if window else None
        allows = getattr(mode, "allows_edits", None)
        if not callable(allows):
            return True
        try:
            return bool(allows())
        except Exception:
            return False

    def _refresh_buttons(self) -> None:
        history = self._history_ref()
        editable = self.edits_allowed()
        can_undo = bool(editable and history is not None and history.can_undo())
        can_redo = bool(editable and history is not None and history.can_redo())
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        undo_label = getattr(history, "undo_label", None) if history else None
        redo_label = getattr(history, "redo_label", None) if history else None
        self.undo_button.setText(f"Undo {undo_label}" if undo_label else "Undo")
        self.redo_button.setText(f"Redo {redo_label}" if redo_label else "Redo")

    def _selection_changed(self, *_args) -> None:
        history, item = self._history_ref(), self.steps.currentItem()
        # Keyboard and mouse selection get the same whole-row guarantee as a
        # refresh, because the buttons below act on whatever is selected.
        self._ensure_row_visible(self.steps.currentRow())
        if history is None or item is None:
            self.move_button.setEnabled(False)
            set_status_label(
                self.status,
                "No changes have been recorded for this window yet.",
            )
            return
        target = int(item.data(Qt.UserRole))
        applied = history.applied_count()
        if not self.edits_allowed():
            self.move_button.setEnabled(False)
            set_status_label(
                self.status,
                "Locked in Inspect mode. Choose Edit to move the profile state.",
                level="warning",
            )
            return
        self.move_button.setEnabled(target != applied)
        if target == applied:
            set_status_label(
                self.status, "This is the current profile state."
            )
        elif target < applied:
            set_status_label(
                self.status,
                f"Moving here undoes {applied - target} change"
                f"{'s' if applied - target != 1 else ''}.",
            )
        else:
            set_status_label(
                self.status,
                f"Moving here redoes {target - applied} change"
                f"{'s' if target - applied != 1 else ''}.",
            )

    # -- actions ------------------------------------------------------------

    def move_to_selected(self, *_args) -> None:
        history, item = self._history_ref(), self.steps.currentItem()
        if history is None or item is None or not self.move_button.isEnabled():
            return
        history.move_to(int(item.data(Qt.UserRole)))

    def _undo(self, *_args) -> None:
        history = self._history_ref()
        if history is not None:
            history.undo()

    def _redo(self, *_args) -> None:
        history = self._history_ref()
        if history is not None:
            history.redo()


def install_edit_history(win) -> QAction | None:
    """Add one idempotent `Edit history…` action to the window's Edit menu."""
    existing = getattr(win, "_sharpmod_edit_history_action", None)
    if existing is not None:
        return existing
    history = getattr(win, "_sharpmod_history", None)
    if history is None or not callable(getattr(history, "steps", None)):
        return None

    from qtpy.QtWidgets import QMenu

    menu = None
    for candidate in win.menuBar().findChildren(QMenu):
        if candidate.title().replace("&", "").strip().casefold() == "edit":
            menu = candidate
            break
    if menu is None:
        menu = win.menuBar().addMenu("&Edit")

    action = QAction("Edit history…", win)
    action.setToolTip(
        "List the recorded sounding changes and move to any point using Undo/Redo"
    )
    action.setEnabled(False)
    shortcut = QKeySequence("Ctrl+Alt+H")
    try:
        from sharpmod.ui.features.gui_commands import collect_commands

        taken = any(
            shortcut == key
            for command in collect_commands(win)
            for key in command.action.shortcuts()
        )
    except Exception:
        taken = False
    if not taken:
        action.setShortcut(shortcut)
    menu.addAction(action)

    win_ref = weakref.ref(win)

    def _show() -> None:
        window = win_ref()
        if window is None:
            return
        live = getattr(window, "_sharpmod_history", None)
        if live is None:
            return
        dialog = getattr(window, "_sharpmod_edit_history_dialog", None)
        if dialog is None:
            dialog = EditHistoryDialog(window, live)
            window._sharpmod_edit_history_dialog = dialog
            dialog.finished.connect(
                lambda *_args: setattr(window, "_sharpmod_edit_history_dialog", None)
            )
        dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_action() -> None:
        window = win_ref()
        if window is None:
            return
        live = getattr(window, "_sharpmod_history", None)
        try:
            recorded = len(live.steps()) if live is not None else 0
        except Exception:
            recorded = 0
        try:
            action.setEnabled(recorded > 0)
        except RuntimeError:
            return
        action.setText(
            f"Edit history ({recorded})…" if recorded else "Edit history…"
        )

    action.triggered.connect(_show)
    history.add_listener(_refresh_action)
    win._sharpmod_edit_history_action = action
    win._sharpmod_show_edit_history = _show
    return action


__all__ = ["ORIGINAL_ROW", "EditHistoryDialog", "install_edit_history"]
