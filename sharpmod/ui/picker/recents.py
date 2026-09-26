"""Search recent destinations and recover missing files through existing actions."""

import os
import weakref

from qtpy.QtCore import QObject, Qt
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QToolButton, QVBoxLayout,
)

from sharpmod.ui.features.gui_commands import search_text
from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.state.recent_destinations import RecentDestinationStore, RecentFormatError
from sharpmod.state.saved_locations import SavedLocation
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE


class RecentDestinationsDialog(QDialog):
    def __init__(self, owner, *, query="", kind="all"):
        super().__init__(owner)
        self._owner_ref = weakref.ref(owner)
        self._store = RecentDestinationStore(owner._settings)
        self._history_error = ""
        self.setObjectName("recentDestinationsDialog")
        self.setWindowTitle("Recent files, sessions and points")
        self.resize(min(820, max(400, owner.width() - 80)), min(650, max(430, owner.height() - 80)))
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])
        hint = QLabel("Search recent destinations. Missing files stay listed; choose a replacement to recover them.")
        hint.setObjectName(OBJ_HINT)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setAccessibleName("Search recent files, sessions or points")
        self.search.setPlaceholderText("Name, full path, location, model or time…")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        self.kind = QComboBox(self)
        self.kind.setAccessibleName("Recent destination type")
        for label, value in (("All destinations", "all"), ("Sounding files", "file"),
                             ("Analysis sessions", "session"), ("Points", "location")):
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        row.addWidget(self.kind)
        layout.addLayout(row)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Recent destinations with last-used UTC and availability")
        self.results.setWordWrap(True)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        self.status = make_status_label(parent=self)
        layout.addWidget(self.status)
        self.recover = QToolButton(self)
        self.recover.setText("Choose replacement…")
        self.recover.setAccessibleName("Find a replacement for the missing recent file")
        self.recover.clicked.connect(self.choose_replacement)
        layout.addWidget(self.recover)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close, parent=self)
        self.open_button = buttons.button(QDialogButtonBox.Ok)
        self.open_button.setText("Open selected")
        self.open_button.setAutoDefault(False)
        self.open_button.setDefault(False)
        buttons.accepted.connect(self.open_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter_entries)
        self.search.returnPressed.connect(self.open_selected)
        self.kind.currentIndexChanged.connect(self.filter_entries)
        self.results.currentItemChanged.connect(self._selection_status)
        self.results.itemActivated.connect(self.open_selected)
        self.reload_entries()
        self.search.setText(query)

    def reload_entries(self):
        owner = self._owner_ref()
        locations = owner._safe_locations(owner._recent_location_store) if owner is not None else ()
        try:
            self.entries = self._store.load(locations=locations)
            self._history_error = ""
        except RecentFormatError as exc:
            self.entries = self._store.legacy(locations=locations)
            self._history_error = str(exc)
        self.filter_entries()

    def filter_entries(self, *_args):
        item = self.results.currentItem()
        previous = item.data(Qt.UserRole).identity if item is not None else None
        tokens = search_text(self.search.text()).split()
        self.results.blockSignals(True)
        try:
            self.results.clear()
            selected = None
            for entry in self.entries:
                if self.kind.currentData() not in {"all", entry.kind}:
                    continue
                blob = search_text(f"{entry.label} {entry.target} {entry.details} {entry.last_used_label} {entry.kind}")
                if not all(token in blob for token in tokens):
                    continue
                prefix = {"file": "Sounding file", "session": "Analysis session", "location": "Point"}[entry.kind]
                missing = " — missing" if not entry.available() else ""
                text = f"{prefix}: {entry.label}{missing}\n{entry.target}\n"
                if entry.details:
                    text += f"{entry.details}\n"
                text += entry.last_used_label
                result = QListWidgetItem(text)
                result.setData(Qt.UserRole, entry)
                result.setToolTip(text)
                self.results.addItem(result)
                if entry.identity == previous:
                    selected = result
            if selected is not None:
                self.results.setCurrentItem(selected)
            elif self.results.count():
                self.results.setCurrentRow(0)
        finally:
            self.results.blockSignals(False)
        self._selection_status()

    def _selection_status(self, *_args):
        item = self.results.currentItem()
        entry = item.data(Qt.UserRole) if item is not None else None
        available = entry is not None and entry.available()
        self.open_button.setEnabled(available)
        self.open_button.setText("Use point" if entry is not None and entry.kind == "location" else "Open selected")
        self.recover.setVisible(entry is not None and entry.kind != "location" and not available)
        if self._history_error:
            set_status_label(self.status, self._history_error + " Older file and point shortcuts remain available.", level="warning")
        elif entry is None:
            set_status_label(self.status, "No matching destination. Try a shorter search or All destinations." if self.entries else
                             "No recent destinations yet. Use File → Open Sounding File to add one.")
        elif not available:
            set_status_label(self.status, "This file is missing or moved. Choose replacement to find it; nothing opens automatically.", level="warning")
        elif entry.kind == "location":
            set_status_label(self.status, f"Use requested point {entry.lat:.4f}°, {entry.lon:.4f}° through the current point source. No sounding is loaded.")
        else:
            set_status_label(self.status, f"Open this {'analysis session' if entry.kind == 'session' else 'sounding file'} through the existing file reader.")

    def _open(self, entry, *, path=None):
        owner = self._owner_ref()
        if owner is None:
            return
        if entry.kind == "location":
            owner._apply_recent_location(SavedLocation.create(entry.label, entry.lat, entry.lon))
            self.accept()
            return
        target = path or entry.target
        if not os.path.isfile(target):
            self.filter_entries()
            set_status_label(self.status, "This file is missing or moved. Choose replacement to find it.", level="warning")
            return
        opened = owner._open_analysis_session(target) if entry.kind == "session" else owner._open_file(target)
        if opened:
            self.accept()
        else:
            set_status_label(self.status, "This item could not be opened. Check the error details or choose a replacement.", level="warning")

    def open_selected(self, *_args):
        item = self.results.currentItem()
        if item is not None:
            self._open(item.data(Qt.UserRole))

    def choose_replacement(self):
        item = self.results.currentItem()
        if item is None:
            return
        entry = item.data(Qt.UserRole)
        if entry.kind == "location":
            return
        filter_text = ("SHARPpy Analysis Session (*.sharpmod-session)" if entry.kind == "session" else
                       "Soundings (*.npz *.spc *.SPC *.oax *.OAX *.buf *.pecan *.txt);;All files (*.*)")
        path, _filter = QFileDialog.getOpenFileName(self, "Find replacement", os.path.dirname(entry.target), filter_text)
        if path:
            self._open(entry, path=path)


class _RecentsController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self._owner_ref = weakref.ref(owner)
        self.dialog = None

    def open(self, *_args, query="", kind="all"):
        owner = self._owner_ref()
        if owner is None:
            return
        if self.dialog is not None:
            try:
                self.dialog.deleteLater()
            except RuntimeError:
                pass
        self.dialog = RecentDestinationsDialog(owner, query=query, kind=kind)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.search.setFocus()


def install_recent_destinations(owner, menu):
    existing = getattr(owner, "_recent_destinations_action", None)
    if existing is not None:
        return existing
    key = QKeySequence("Ctrl+Shift+R")
    if any(key in action.shortcuts() for action in owner.findChildren(QAction)):
        raise ValueError("Ctrl+Shift+R is already assigned in this window")
    controller = _RecentsController(owner)
    action = QAction("Recent files, sessions and points…", owner)
    action.setShortcut(key)
    action.setToolTip("Search recent destinations and recover missing files (Ctrl+Shift+R)")
    action.triggered.connect(controller.open)
    menu.addAction(action)
    owner._recent_destinations_controller = controller
    owner._recent_destinations_action = action
    return action
