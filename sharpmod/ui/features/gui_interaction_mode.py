"""Explicit, guarded Inspect/Edit modes for the sounding viewer.

The vendored Skew-T and hodograph expose editing directly through their mouse
handlers.  That makes a normal click-drag ambiguous: it can either be an
inspection gesture or a scientific mutation depending on an unobvious cursor
state.  This module puts one window-owned mode controller in front of those
paths while continuing to use the vendored readout, edit, and history methods.
"""

from __future__ import annotations

from functools import wraps
import weakref

from qtpy.QtCore import QEvent, QObject, QSize, Qt
from qtpy.QtGui import QAction, QActionGroup
from qtpy.QtWidgets import QLabel, QMenu, QSizePolicy

from sharpmod.ui.styles.theme import OBJ_HINT

MODE_INSPECT = "inspect"
MODE_EDIT = "edit"
SETTING_KEY = "viewer/interaction_mode"

_INSPECT_HINT = "Inspect: linked readouts on; profile edits locked."
_EDIT_HINT = "Edit: drag or right-click; Undo records changes."

_MUTATION_LABELS = {
    "modifyProf": "Edit sounding level",
    "modifyVector": "Edit storm motion",
    "interpProf": "Interpolate profile",
    "resetProfModifications": "Reset profile edits",
    "resetProfInterpolation": "Reset interpolation",
    "resetVector": "Reset storm motion",
}

_SOUND_EDIT_ACTIONS = {
    "Modify Surface",
    "Edit Nearest Level…",
    "Reset Skew-T",
}
_HODO_EDIT_ACTIONS = {"Reset Hodograph", "Reset Storm Motion"}
_POINTER_EVENTS = {
    QEvent.MouseButtonPress,
    QEvent.MouseButtonRelease,
    QEvent.MouseButtonDblClick,
    QEvent.MouseMove,
}


def _setting_value(settings, key: str, default: str) -> str:
    if settings is None:
        return default
    try:
        value = settings.value(key, default, str)
    except TypeError:
        value = settings.value(key, default)
    return str(value or default).strip().casefold()


def _menu_action(menu, text: str):
    if menu is None:
        return None
    wanted = text.replace("...", "…").strip().casefold()
    for action in menu.actions():
        actual = action.text().replace("...", "…").strip().casefold()
        if actual == wanted:
            return action
    return None


def _window_menu(win, title: str):
    wanted = title.replace("&", "").strip().casefold()
    for menu in win.menuBar().findChildren(QMenu):
        if menu.title().replace("&", "").strip().casefold() == wanted:
            return menu
    return None


def _set_checked(action, checked: bool) -> None:
    if action is None or not action.isCheckable():
        return
    blocked = action.blockSignals(True)
    action.setChecked(checked)
    action.blockSignals(blocked)


def _left_pointer_event(event) -> bool:
    """Return whether *event* is part of an ordinary left-button gesture."""
    kind = event.type()
    if kind == QEvent.MouseMove:
        try:
            return bool(event.buttons() & Qt.LeftButton)
        except AttributeError:
            return False
    try:
        return event.button() == Qt.LeftButton
    except AttributeError:
        return False


class _ModeHint(QLabel):
    """A full accessible hint that elides before hiding other toolbar tools."""

    MIN_WIDTH = 140
    MAX_WIDTH = 420

    def __init__(self):
        super().__init__()
        self._full_text = ""
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def set_mode_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(
            f"{self._full_text}\nCtrl+Alt+I selects Inspect; "
            "Ctrl+Alt+E selects Edit"
        )
        self.setAccessibleDescription(self._full_text)
        self._refresh_elision()

    def sizeHint(self):  # noqa: N802 - Qt override
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full_text)
        return QSize(max(self.MIN_WIDTH, min(self.MAX_WIDTH, width)), hint.height())

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_elision()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            self._refresh_elision()

    def _refresh_elision(self) -> None:
        available = max(0, self.width())
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(
                self._full_text, Qt.ElideRight, available
            ),
        )


class InteractionModeController(QObject):
    """Own one sounding window's visible mode and mutation boundary."""

    def __init__(self, win, *, settings=None):
        super().__init__(win)
        self._win_ref = weakref.ref(win)
        self._settings = settings
        self._mode = MODE_INSPECT
        self._sound = win.spc_widget.sound
        self._hodo = win.spc_widget.hodo
        self._history = getattr(win, "_sharpmod_history", None)
        self._managed_actions: dict[QAction, bool] = {}

        self.inspect_action = QAction("Inspect", win)
        self.inspect_action.setCheckable(True)
        self.inspect_action.setShortcut("Ctrl+Alt+I")
        self.inspect_action.setToolTip(
            "Inspect linked values without changing the active profile"
        )
        self.inspect_action.setStatusTip(_INSPECT_HINT)

        self.edit_action = QAction("Edit", win)
        self.edit_action.setCheckable(True)
        self.edit_action.setShortcut("Ctrl+Alt+E")
        self.edit_action.setToolTip(
            "Arm profile and storm-motion editing; every change enters history"
        )
        self.edit_action.setStatusTip(_EDIT_HINT)

        self.action_group = QActionGroup(self)
        self.action_group.setExclusive(True)
        self.action_group.addAction(self.inspect_action)
        self.action_group.addAction(self.edit_action)
        self.inspect_action.triggered.connect(self.activate_inspect)
        self.edit_action.triggered.connect(self.activate_edit)

        self.hint = _ModeHint()
        self.hint.setObjectName(OBJ_HINT)
        self.hint.setAccessibleName("Sounding interaction mode guidance")

        self._install_actions(win)
        self._collect_edit_actions(win)
        self._install_pointer_guards()
        win.spc_widget._sharpmod_interaction_controller = self

        if self._history is not None:
            add_listener = getattr(self._history, "add_listener", None)
            if callable(add_listener):
                add_listener(self._sync_action_state)

        restored = _setting_value(settings, SETTING_KEY, MODE_INSPECT)
        if restored not in {MODE_INSPECT, MODE_EDIT}:
            restored = MODE_INSPECT
        self.set_mode(restored, persist=True, announce=False)

        win._sharpmod_interaction_mode = self
        win._sharpmod_inspect_action = self.inspect_action
        win._sharpmod_edit_action = self.edit_action
        win._sharpmod_mode_hint = self.hint

    @property
    def mode(self) -> str:
        return self._mode

    def allows_edits(self) -> bool:
        return self._mode == MODE_EDIT

    def activate_inspect(self, _checked=False) -> None:
        self.set_mode(MODE_INSPECT)

    def activate_edit(self, _checked=False) -> None:
        self.set_mode(MODE_EDIT)

    def set_mode(
        self,
        mode: str,
        *,
        persist: bool = True,
        announce: bool = True,
    ) -> None:
        normalized = str(mode).strip().casefold()
        if normalized not in {MODE_INSPECT, MODE_EDIT}:
            normalized = MODE_INSPECT
        self._mode = normalized
        _set_checked(self.inspect_action, normalized == MODE_INSPECT)
        _set_checked(self.edit_action, normalized == MODE_EDIT)
        self._cancel_active_drags()
        self._configure_surfaces()
        self._sync_action_state()

        hint = _INSPECT_HINT if normalized == MODE_INSPECT else _EDIT_HINT
        self.hint.set_mode_text(hint)
        if persist and self._settings is not None:
            try:
                self._settings.setValue(SETTING_KEY, normalized)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if announce:
            win = self._win_ref()
            if win is not None:
                try:
                    win.statusBar().showMessage(hint, 5000)
                except (AttributeError, RuntimeError):
                    pass

    def report_blocked(self, label: str) -> None:
        win = self._win_ref()
        if win is None:
            return
        message = f"{label} is locked in Inspect mode. Choose Edit to change data."
        try:
            win.statusBar().showMessage(message, 5000)
        except (AttributeError, RuntimeError):
            pass

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if self._mode != MODE_INSPECT or event.type() not in _POINTER_EVENTS:
            return False
        if not _left_pointer_event(event):
            return False
        if watched is self._hodo:
            # The boundary cursor annotates an inspection vector but does not
            # mutate the loaded sounding. Keep that deliberate tool available.
            return getattr(self._hodo, "cursor_type", "none") == "none"
        if watched is self._sound:
            # The upstream readout path is non-mutating. If its state drifts,
            # fail closed instead of allowing the same event into drag editing.
            return not bool(getattr(self._sound, "readout", False))
        return False

    def _install_actions(self, win) -> None:
        menu = _window_menu(win, "Edit")
        if menu is None:
            menu = win.menuBar().addMenu("&Edit")
        before = menu.actions()[0] if menu.actions() else None
        if before is None:
            menu.addAction(self.inspect_action)
            menu.addAction(self.edit_action)
            menu.addSeparator()
        else:
            menu.insertAction(before, self.inspect_action)
            menu.insertAction(before, self.edit_action)
            menu.insertSeparator(before)

        toolbar = getattr(win, "_sharpmod_view_toolbar", None)
        if toolbar is None:
            return
        before = toolbar.actions()[0] if toolbar.actions() else None
        if before is None:
            toolbar.addAction(self.inspect_action)
            toolbar.addAction(self.edit_action)
            toolbar.addSeparator()
            toolbar.addWidget(self.hint)
        else:
            toolbar.insertAction(before, self.inspect_action)
            toolbar.insertAction(before, self.edit_action)
            toolbar.insertSeparator(before)
            toolbar.insertWidget(before, self.hint)

    def _collect_edit_actions(self, win) -> None:
        for text in _SOUND_EDIT_ACTIONS:
            action = _menu_action(getattr(self._sound, "popupmenu", None), text)
            if action is not None:
                self._managed_actions[action] = action.isEnabled()
        for text in _HODO_EDIT_ACTIONS:
            action = _menu_action(getattr(self._hodo, "popupmenu", None), text)
            if action is not None:
                self._managed_actions[action] = action.isEnabled()
        for name in ("interpolate", "resetinterp"):
            action = getattr(win, name, None)
            if action is not None:
                self._managed_actions[action] = action.isEnabled()

        self._sound_no_cursor = _menu_action(
            getattr(self._sound, "popupmenu", None), "No Cursor"
        )
        self._sound_readout_cursor = _menu_action(
            getattr(self._sound, "popupmenu", None), "Readout Cursor"
        )
        for action in (self._sound_no_cursor, self._sound_readout_cursor):
            if action is not None:
                action.setEnabled(False)
                action.setToolTip(
                    "Controlled by the Inspect/Edit buttons in the viewer toolbar"
                )

        self._hodo_no_cursor = _menu_action(
            getattr(self._hodo, "popupmenu", None), "No Cursor"
        )
        self._hodo_boundary_cursor = _menu_action(
            getattr(self._hodo, "popupmenu", None), "Bndy Cursor"
        )
        if self._hodo_no_cursor is not None:
            self._hodo_no_cursor.triggered.connect(self._refresh_hodo_cursor)
        if self._hodo_boundary_cursor is not None:
            self._hodo_boundary_cursor.triggered.connect(self._refresh_hodo_cursor)

    def _install_pointer_guards(self) -> None:
        self._sound.installEventFilter(self)
        self._hodo.installEventFilter(self)

    def _cancel_active_drags(self) -> None:
        for surface, names in (
            (self._sound, ("drag_tmpc", "drag_dwpc")),
            (self._hodo, ("drag_hodo", "drag_rm", "drag_lm")),
        ):
            for name in names:
                drag = getattr(surface, name, None)
                if drag is not None and hasattr(drag, "_drag_idx"):
                    drag._drag_idx = None

    def _configure_surfaces(self) -> None:
        inspect = self._mode == MODE_INSPECT
        try:
            if inspect:
                self._sound.setReadoutCursor()
            else:
                self._sound.setNoCursor()
        except (AttributeError, RuntimeError, TypeError):
            pass
        _set_checked(self._sound_no_cursor, not inspect)
        _set_checked(self._sound_readout_cursor, inspect)

        try:
            self._hodo.setNoCursor()
        except (AttributeError, RuntimeError, TypeError):
            pass
        _set_checked(self._hodo_no_cursor, True)
        self._set_surface_cursors()

    def _set_surface_cursors(self) -> None:
        inspect = self._mode == MODE_INSPECT
        try:
            self._sound.setCursor(Qt.CrossCursor if inspect else Qt.SizeHorCursor)
        except RuntimeError:
            pass
        self._refresh_hodo_cursor()

    def _refresh_hodo_cursor(self, _checked=False) -> None:
        if getattr(self._hodo, "cursor_type", "none") != "none":
            return
        try:
            self._hodo.setCursor(
                Qt.CrossCursor if self._mode == MODE_INSPECT else Qt.SizeAllCursor
            )
        except RuntimeError:
            pass

    def _sync_action_state(self) -> None:
        editable = self._mode == MODE_EDIT
        for action, originally_enabled in tuple(self._managed_actions.items()):
            try:
                action.setEnabled(editable and originally_enabled)
            except RuntimeError:
                pass

        win = self._win_ref()
        if win is None:
            return
        undo = getattr(win, "_sharpmod_undo_action", None)
        redo = getattr(win, "_sharpmod_redo_action", None)
        if undo is not None:
            can_undo = bool(
                editable
                and self._history is not None
                and getattr(self._history, "can_undo", lambda: False)()
            )
            undo.setEnabled(can_undo)
        if redo is not None:
            can_redo = bool(
                editable
                and self._history is not None
                and getattr(self._history, "can_redo", lambda: False)()
            )
            redo.setEnabled(can_redo)


def install_interaction_mode(win, *, settings=None):
    """Install one idempotent interaction controller on a sounding window."""
    existing = getattr(win, "_sharpmod_interaction_mode", None)
    if existing is not None:
        return existing
    sw = getattr(win, "spc_widget", None)
    if sw is None or getattr(sw, "sound", None) is None or getattr(sw, "hodo", None) is None:
        return None
    return InteractionModeController(win, settings=settings)


def install_interaction_mode_hooks(spc_widget_class) -> None:
    """Guard the vendored mutation methods before Qt binds their signals."""
    if getattr(spc_widget_class, "_sharpmod_interaction_hooks_installed", False):
        return
    for name, label in _MUTATION_LABELS.items():
        original = getattr(spc_widget_class, name, None)
        if not callable(original):
            continue

        @wraps(original)
        def guarded(self, *args, __original=original, __label=label, **kwargs):
            controller = getattr(self, "_sharpmod_interaction_controller", None)
            if controller is not None and not controller.allows_edits():
                controller.report_blocked(__label)
                return None
            return __original(self, *args, **kwargs)

        setattr(spc_widget_class, name, guarded)
    spc_widget_class._sharpmod_interaction_hooks_installed = True


__all__ = [
    "MODE_EDIT",
    "MODE_INSPECT",
    "SETTING_KEY",
    "InteractionModeController",
    "install_interaction_mode",
    "install_interaction_mode_hooks",
]
