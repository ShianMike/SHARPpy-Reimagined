"""Discover and invoke existing native actions without duplicating commands."""

from dataclasses import dataclass
import re
import unicodedata
import weakref

from qtpy.QtCore import QObject, QTimer, Qt
from qtpy.QtGui import QAction, QKeySequence, QPalette
from qtpy.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout,
)

from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE


def _plain(text):
    return str(text).replace("&", "").strip()


def search_text(text):
    normalized = unicodedata.normalize("NFKD", str(text)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


_ACTION_ALIASES = {
    "preferences": "settings options appearance",
    "controls": "help guide shortcuts keyboard",
    "downloaded": "cache reuse library",
    "session": "workspace analysis restore checkpoint",
    "export": "save output publish",
    "image": "png picture chart",
    "text": "txt data numeric",
    "soundings": "profiles",
    "sounding": "profile",
    "locations": "places coordinates favorites",
}


@dataclass(frozen=True)
class Command:
    action: QAction
    context: str
    ancestors: tuple[QAction, ...] = ()

    def available(self):
        try:
            return self.action.isEnabled() and all(action.isEnabled() for action in self.ancestors)
        except RuntimeError:  # Dynamic menus can remove an action while the palette is open.
            return False

    def visible(self):
        try:
            return self.action.isVisible() and all(action.isVisible() for action in self.ancestors)
        except RuntimeError:
            return False

    def search_blob(self):
        text = search_text(f"{self.action.text()} {self.context} "
                           f"{self.action.shortcut().toString()} {self.action.toolTip()}")
        aliases = " ".join(value for key, value in _ACTION_ALIASES.items() if key in text)
        return f"{text} {aliases}"


def collect_commands(window, *, exclude=None):
    """Menu ancestry remains part of command availability, not just its own flag."""
    commands, seen = [], set()

    def walk(actions, path=(), ancestors=()):
        for action in actions:
            if action is exclude or action.isSeparator():
                continue
            menu = action.menu()
            if menu is not None:
                walk(menu.actions(), (*path, _plain(action.text())), (*ancestors, action))
            elif action.text() and id(action) not in seen:
                seen.add(id(action))
                commands.append(Command(action, " › ".join(path) or "Window", ancestors))

    walk(window.menuBar().actions())
    walk(window.actions())
    return tuple(commands)


class CommandPalette(QDialog):
    """Native keyboard search, live availability, and authoritative triggering."""

    def __init__(self, window, *, exclude=None):
        super().__init__(window)
        self._window_ref = weakref.ref(window)
        self._exclude = exclude
        self._commands = ()
        self._watched = []
        self.setObjectName("commandPalette")
        self.setWindowTitle("Find an Action")
        self.resize(min(760, max(360, window.width() - 80)),
                    min(600, max(300, window.height() - 100)))
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])
        hint = QLabel("Search an action or shortcut. Use ↓ to browse and Enter to run; Esc closes.")
        hint.setObjectName(OBJ_HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find action, e.g. settings, export PNG, session…")
        self.search.setAccessibleName("Find action")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Matching commands and current availability")
        self.results.setWordWrap(True)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        self.status = make_status_label(parent=self)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close, parent=self)
        self.run_button = buttons.button(QDialogButtonBox.Ok)
        self.run_button.setText("Run action")
        self.run_button.setAutoDefault(False)
        self.run_button.setDefault(False)
        buttons.accepted.connect(self.run_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter_commands)
        self.search.returnPressed.connect(self.run_selected)
        self.results.itemActivated.connect(self.run_selected)
        self.results.currentItemChanged.connect(self._selection_status)
        self.search.installEventFilter(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.filter_commands)
        self.reload_commands()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        from qtpy.QtCore import QEvent

        if watched is self.search and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
            self.results.setFocus()
            return True
        return super().eventFilter(watched, event)

    def reload_commands(self):
        for action in self._watched:
            try:
                action.changed.disconnect(self._queue_refresh)
                action.destroyed.disconnect(self._queue_refresh)
            except (RuntimeError, TypeError):
                pass
        window = self._window_ref()
        self._commands = collect_commands(window, exclude=self._exclude) if window is not None else ()
        self._watched = list({id(action): action for command in self._commands
                              for action in (command.action, *command.ancestors)}.values())
        for action in self._watched:
            action.changed.connect(self._queue_refresh)
            action.destroyed.connect(self._queue_refresh)
        self.filter_commands()

    def _queue_refresh(self, *_args):
        self._refresh_timer.start(0)

    def filter_commands(self, *_args):
        previous = self.results.currentItem()
        previous_action = previous.data(Qt.UserRole).action if previous is not None else None
        tokens = search_text(self.search.text()).split()
        matches = [command for command in self._commands if command.visible()
                   and all(token in command.search_blob() for token in tokens)]
        self.results.blockSignals(True)
        try:
            self.results.clear()
            selected = None
            for command in matches:
                action = command.action
                shortcut = action.shortcut().toString(QKeySequence.NativeText)
                state = " — On" if action.isCheckable() and action.isChecked() else (
                    " — Off" if action.isCheckable() else ""
                )
                suffix = "" if command.available() else " — unavailable"
                item = QListWidgetItem(
                    f"{_plain(action.text())}{' · ' + shortcut if shortcut else ''}\n"
                    f"{command.context}{state}{suffix}"
                )
                item.setData(Qt.UserRole, command)
                item.setToolTip(action.toolTip() or _plain(action.text()))
                if not command.available():
                    # Keep the row keyboard-reachable so its reason can be read;
                    # invocation and the Run button remain disabled.
                    item.setForeground(self.results.palette().color(QPalette.Disabled, QPalette.Text))
                self.results.addItem(item)
                if action is previous_action:
                    selected = item
            if selected is not None:
                self.results.setCurrentItem(selected)
            elif self.results.count():
                self.results.setCurrentRow(0)
        finally:
            self.results.blockSignals(False)
        self._selection_status()

    def _selection_status(self, *_args):
        item = self.results.currentItem()
        self.run_button.setEnabled(item is not None and item.data(Qt.UserRole).available()
                                   and item.data(Qt.UserRole).visible())
        if item is None:
            text = "No matching action. Try a shorter name or shortcut."
        else:
            command = item.data(Qt.UserRole)
            text = "This action is no longer present. Search again." if not command.visible() else (
                (command.action.toolTip() or _plain(command.action.text())) if command.available() else (
                "This action is unavailable in the current application state. "
                + (command.action.toolTip() or _plain(command.action.text()))
                )
            )
        set_status_label(self.status, text)

    def run_selected(self, *_args):
        item = self.results.currentItem()
        if item is None:
            return
        command = item.data(Qt.UserRole)
        # Recheck at the exact invocation boundary, including menu ancestry.
        if not command.visible() or not command.available():
            self.filter_commands()
            return
        action = command.action
        self.accept()
        action.trigger()


class _PaletteController(QObject):
    def __init__(self, window, action):
        super().__init__(window)
        self._window_ref = weakref.ref(window)
        self.action, self.dialog = action, None
        action.triggered.connect(self.open)

    def open(self, *_args):
        window = self._window_ref()
        if window is None:
            return
        if self.dialog is None:
            self.dialog = CommandPalette(window, exclude=self.action)
            self.dialog.destroyed.connect(self._dialog_destroyed)
        else:
            self.dialog.reload_commands()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.search.setFocus()
        self.dialog.search.selectAll()

    def _dialog_destroyed(self, *_args):
        self.dialog = None


def install_command_palette(window, *, menu=None):
    existing = getattr(window, "_sharpmod_command_palette", None)
    if existing is not None:
        return existing.action
    key = QKeySequence("Ctrl+K")
    if any(key in action.shortcuts() for action in window.findChildren(QAction)):
        raise ValueError("Ctrl+K is already assigned in this window")
    action = QAction("Find an &Action…", window)
    action.setObjectName("commandPaletteAction")
    action.setShortcut(key)
    action.setToolTip("Search existing actions and their current availability (Ctrl+K)")
    window.addAction(action)
    if menu is not None:
        menu.addAction(action)
    window._sharpmod_command_palette = _PaletteController(window, action)
    return action
