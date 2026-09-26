"""Explicit native reopening of bounded, actually retained profile data."""

import weakref

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QVBoxLayout,
)

from sharpmod.ui.features.gui_collection_state import CLOSED_LIMIT
from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE


class ClosedProfilesDialog(QDialog):
    def __init__(self, window, state):
        super().__init__(window)
        self._state_ref = weakref.ref(state)
        self.setWindowTitle("Reopen closed sounding")
        self.setObjectName("closedProfilesDialog")
        self.resize(min(850, max(420, window.width() - 80)), min(650, max(430, window.height() - 80)))
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])
        hint = QLabel(f"The last {CLOSED_LIMIT} closed profiles in this window are retained in memory. "
                      "Reopening these needs no retrieval. Older entries or a restarted window require opening "
                      "the source again; model or observation data may need retrieval.")
        hint.setObjectName(OBJ_HINT)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.PlainText)
        layout.addWidget(hint)
        self.search = QLineEdit(self)
        self.search.setAccessibleName("Search retained closed soundings by source identity or time")
        self.search.setPlaceholderText("Location, model, source or time…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Retained recently closed soundings")
        self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        layout.addWidget(self.results, 1)
        self.status = make_status_label("", parent=self)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.reopen_button = buttons.addButton("Reopen selected", QDialogButtonBox.ActionRole)
        for button in buttons.buttons():
            button.setAutoDefault(False)
        layout.addWidget(buttons)
        buttons.rejected.connect(self.reject)
        self.reopen_button.clicked.connect(self.open_selected)
        self.search.returnPressed.connect(self.open_selected)
        self.results.itemActivated.connect(self.open_selected)
        self.results.currentItemChanged.connect(self._selection_changed)
        self.search.textChanged.connect(self.refresh)
        state.changed.connect(self.refresh)
        state.feedback.connect(self._feedback)
        self.refresh()
        self.search.setFocus()

    def _feedback(self, message, level):
        set_status_label(self.status, message, level=level)

    def refresh(self):
        state = self._state_ref()
        self.results.blockSignals(True)
        self.results.clear()
        if state is not None:
            for record in state.closed:
                if not record.identity.matches(self.search.text()):
                    continue
                flags = [label for enabled, label in ((record.hidden, "Hidden when closed"), (record.reference, "Reference when closed")) if enabled]
                text = (record.identity.label + f"\nClosed: {record.closed_at:%Y-%m-%d %H:%M:%S} UTC\n"
                        "Retained data available · no retrieval" + ("\n" + " · ".join(flags) if flags else ""))
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, record.profile_id)
                item.setToolTip(record.identity.details)
                self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        self.results.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args):
        state, item = self._state_ref(), self.results.currentItem()
        if item is None or state is None:
            self.reopen_button.setEnabled(False)
            set_status_label(self.status, "No retained profiles match. Clear the search or reopen the original source.")
            return
        loaded = item.data(Qt.UserRole) in state.ids()
        self.reopen_button.setEnabled(not loaded)
        set_status_label(self.status, "This identity is already loaded; it will not be overwritten. Close it first to restore retained data."
                         if loaded else "Reopen restores retained source data and available position/reference/visibility context.",
                         level="warning" if loaded else "normal")

    def open_selected(self, *_args):
        state, item = self._state_ref(), self.results.currentItem()
        if state is None or item is None or not self.reopen_button.isEnabled():
            return
        if state.reopen(item.data(Qt.UserRole)):
            self.accept()
