"""Local sounding file tab, decoding, and recent-file list."""

from __future__ import annotations

import os

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from sharpmod.ui.features.gui_common import APP_NAME, MAX_RECENTS, _LOGGER, _render, make_status_label
from sharpmod.ui.styles.theme import CONTROL_H, OBJ_PRIMARY, SPACE
from sharpmod.ui.picker.selection import FileSelectionFeedback


class FileSourceMixin:
    """Build and operate the local sounding file source."""

    def _build_file_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(SPACE["sm"])

        modes = QTabWidget()
        decoded = QWidget()
        decoded_layout = QVBoxLayout(decoded)
        decoded_layout.setSpacing(SPACE["sm"])

        intro = QLabel(
            "Open a local sounding file \u2014 or drag one onto this window.\n"
            "Supported: .npz point soundings, SPC tabular, BUFKIT, PECAN, "
            "and WRF-ARW text."
        )
        intro.setWordWrap(True)
        decoded_layout.addWidget(intro)

        row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setClearButtonEnabled(True)
        self._file_edit.setPlaceholderText("Path to a sounding file\u2026")
        self._file_edit.returnPressed.connect(self._open_from_edit)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse_file)
        row.addWidget(self._file_edit)
        row.addWidget(browse)
        decoded_layout.addLayout(row)

        open_btn = QPushButton("Open Sounding")
        open_btn.setObjectName(OBJ_PRIMARY)
        open_btn.setMinimumHeight(CONTROL_H["md"])
        open_btn.clicked.connect(self._open_from_edit)
        self._file_open_btn = open_btn
        self._file_selection_summary = QLabel()
        self._file_selection_summary.setTextFormat(Qt.PlainText)
        self._file_selection_summary.setObjectName("pickerSelectionSummary")
        self._file_selection_summary.setWordWrap(True)
        self._file_selection_summary.setAccessibleName("Local file selection summary")
        self._file_selection_guidance = make_status_label()
        decoded_layout.addWidget(self._file_selection_summary)
        decoded_layout.addWidget(self._file_selection_guidance)
        decoded_layout.addWidget(open_btn)
        self._file_selection_feedback = FileSelectionFeedback(
            self._file_edit, self._file_selection_summary, self._file_selection_guidance,
            open_btn, decoded,
        )

        recent_box = QGroupBox("Recent files")
        rv = QVBoxLayout(recent_box)
        self._recent_list = QListWidget()
        self._recent_list.setWordWrap(True)
        from qtpy.QtWidgets import QAbstractItemView

        self._recent_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._recent_list.setAccessibleName("Recent sounding files and last-used times")
        self._recent_list.itemActivated.connect(self._open_recent_file)
        rv.addWidget(self._recent_list)
        recent_search = QToolButton()
        recent_search.setText("Search all recents…")
        recent_search.setAccessibleName("Search recent files, sessions and points")
        recent_search.clicked.connect(self._show_recent_destinations)
        rv.addWidget(recent_search)
        decoded_layout.addWidget(recent_box, stretch=1)
        modes.addTab(decoded, "Decoded Sounding")
        modes.addTab(self._build_wrf_file_panel(), "Raw WRF wrfout")
        layout.addWidget(modes)
        self._file_modes = modes
        return w

    def _browse_file(self) -> None:
        start = self._settings.value("last_dir", "", str)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Sounding File",
            start,
            "Soundings (*.npz *.spc *.SPC *.oax *.OAX *.buf *.pecan *.txt);;"
            "All files (*.*)",
        )
        if path:
            self._file_edit.setText(path)
            self._open_file(path)

    def _browse_and_open(self) -> None:
        self._select_tab("Open File")
        self._browse_file()

    def _open_from_edit(self) -> None:
        path = self._file_edit.text().strip().strip('"')
        if not path:
            QMessageBox.warning(self, APP_NAME, "Choose a sounding file first.")
            return
        self._open_file(path)

    def _open_file(self, path: str) -> None:
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, APP_NAME, f"File not found:\n{path}")
            return
        self.statusBar().showMessage(f"Decoding {os.path.basename(path)}\u2026")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            QApplication.processEvents()
            R = _render()
            prof_col, stn_id = R.decode(path)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage("Decode failed")
            QMessageBox.critical(
                self, APP_NAME, f"Could not decode this file:\n{path}\n\n{exc}"
            )
            return
        display_error = None
        try:
            self._show_sounding(
                prof_col, stn_id, title=f"{APP_NAME} \u2014 {os.path.basename(path)}"
            )
        except Exception as exc:  # noqa: BLE001 - GUI/render boundary
            _LOGGER.exception("local_file.display_failed path=%s", path)
            self.statusBar().showMessage("Display failed")
            display_error = exc
        finally:
            QApplication.restoreOverrideCursor()
        if display_error is not None:
            # Restore the normal cursor before entering the blocking modal;
            # otherwise the failure dialog itself misleadingly shows busy.
            QMessageBox.critical(
                self,
                APP_NAME,
                f"Decoded, but could not display this file:\n{path}\n\n{display_error}",
            )
            return
        self._settings.setValue("last_dir", os.path.dirname(path))
        record = getattr(self, "_record_recent_destination", None)
        if callable(record):
            from sharpmod.state.recent_destinations import describe_collection

            record("file", path, details=describe_collection(prof_col, fallback=str(stn_id)))
        self._remember_recent_file(path)
        self.statusBar().showMessage(f"Opened {os.path.basename(path)}")
        return True

    def _open_recent_file(self, item) -> None:
        path = item.data(Qt.UserRole)
        if not path:
            return
        if os.path.isfile(path):
            self._open_file(path)
        else:
            self._show_recent_destinations(query=path, kind="file")

    def _remember_recent_file(self, path: str) -> None:
        recents = list(self._settings.value("recent_files", [], list) or [])
        path = os.path.abspath(path)
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        recents = recents[:MAX_RECENTS]
        self._settings.setValue("recent_files", recents)
        self._load_recent_files(recents)

    def _load_recent_files(self, recents=None) -> None:
        if not hasattr(self, "_recent_list"):
            return
        if recents is None:
            recents = list(self._settings.value("recent_files", [], list) or [])
        from sharpmod.state.recent_destinations import RecentDestinationStore, RecentFormatError, destination

        try:
            entries = RecentDestinationStore(self._settings).load()
        except RecentFormatError:
            entries = ()
        metadata = {entry.identity: entry for entry in entries}
        self._recent_list.clear()
        for p in recents:
            try:
                legacy = destination("file", p)
            except RecentFormatError:
                continue
            entry = metadata.get(legacy.identity, legacy)
            state = " — missing; choose replacement" if not entry.available() else ""
            text = f"{entry.label}{state}\n{entry.target}\n{entry.last_used_label}"
            item = QListWidgetItem(text)
            item.setToolTip(f"{text}\n{entry.details}")
            item.setData(Qt.UserRole, entry.target)
            self._recent_list.addItem(item)
        if self._recent_list.count() == 0:
            hint = QListWidgetItem("(no recent files yet)")
            hint.setFlags(Qt.NoItemFlags)
            self._recent_list.addItem(hint)
