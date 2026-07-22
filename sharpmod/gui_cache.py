"""GUI cache library for pinning and reusing downloaded model data."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def format_size(size) -> str:
    value = float(max(0, int(size)))
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or suffix == "TiB":
            return f"{value:.0f} {suffix}" if suffix == "B" else f"{value:.1f} {suffix}"
        value /= 1024.0


def parse_spatial_point(value):
    """Return ``(lat, lon)`` for point-key metadata, otherwise ``None``."""
    try:
        left, right = str(value).split(",", 1)
        lat, lon = float(left), float(right)
    except (AttributeError, TypeError, ValueError):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def entry_reusable(entry) -> bool:
    return bool(entry.valid_grib or entry.valid_sounding)


class CacheManagerDialog(QDialog):
    """Inspect, pin, delete, and request reuse of persistent cache entries."""

    def __init__(self, cache, *, use_callback=None, parent=None):
        super().__init__(parent)
        self._cache = cache
        self._use_callback = use_callback
        self.setWindowTitle("Downloaded Data Library")
        self.resize(930, 480)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Cached model hours can be reused without downloading them again. "
            "Pinned entries are preserved by automatic cleanup."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels((
            "Model", "Run (UTC)", "Hour", "Member", "Scope",
            "Last used", "Size", "State",
        ))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(
            lambda _item: self._use_selected()
        )
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.use_button = QPushButton("Use / Re-extract", self)
        self.use_button.clicked.connect(self._use_selected)
        actions.addWidget(self.use_button)
        self.pin_button = QPushButton("Pin", self)
        self.pin_button.clicked.connect(self._toggle_pin)
        actions.addWidget(self.pin_button)
        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        self.copy_button = QPushButton("Copy metadata", self)
        self.copy_button.clicked.connect(self._copy_metadata)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        refresh = QPushButton("Refresh", self)
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        clear = QPushButton("Clear unpinned", self)
        clear.clicked.connect(self._clear_unpinned)
        actions.addWidget(clear)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _selected_entry(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def refresh(self, *_args):
        selected_path = getattr(self._selected_entry(), "path", None)
        entries = self._cache.entries()
        self.table.setRowCount(len(entries))
        select_row = -1
        for row, entry in enumerate(entries):
            try:
                accessed = datetime.fromtimestamp(
                    entry.accessed, timezone.utc
                ).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except (OSError, OverflowError, ValueError):
                accessed = "Unknown"
            status = []
            if entry.pinned:
                status.append("Pinned")
            if entry.protected:
                status.append("In use")
            if entry.valid_grib:
                status.append("GRIB ready")
            elif entry.valid_sounding:
                status.append("Sounding ready")
            else:
                status.append("Incomplete")
            values = (
                entry.model.upper(), entry.run, f"F{entry.fxx:03d}",
                entry.member or "—", entry.spatial or "Full grid", accessed,
                format_size(entry.size), ", ".join(status),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, entry)
                if entry.source_url:
                    item.setToolTip(
                        "\n".join(filter(None, (
                            f"Source: {entry.source_url}",
                            f"Transport: {entry.source_transport or 'unknown'}",
                            (
                                "Fields: " + ", ".join(entry.source_fields)
                                if entry.source_fields else ""
                            ),
                        )))
                    )
                self.table.setItem(row, column, item)
            if entry.path == selected_path:
                select_row = row
        self.table.resizeColumnsToContents()
        if select_row >= 0:
            self.table.selectRow(select_row)
        elif entries:
            self.table.selectRow(0)
        self._update_buttons()

    def _update_buttons(self):
        entry = self._selected_entry()
        selected = entry is not None
        self.use_button.setEnabled(
            selected and entry_reusable(entry) and self._use_callback is not None
        )
        self.pin_button.setEnabled(selected and not entry.protected)
        self.delete_button.setEnabled(selected and not entry.protected)
        self.copy_button.setEnabled(selected)
        self.pin_button.setText("Unpin" if selected and entry.pinned else "Pin")

    def _copy_metadata(self):
        entry = self._selected_entry()
        if entry is None:
            return
        payload = {
            "model": entry.model,
            "run": entry.run,
            "forecast_hour": entry.fxx,
            "member": entry.member,
            "spatial": entry.spatial,
            "source_url": entry.source_url,
            "source_transport": entry.source_transport,
            "source_fields": list(entry.source_fields),
            "path": str(entry.path),
            "accessed_utc": datetime.fromtimestamp(
                entry.accessed, timezone.utc
            ).isoformat(),
            "bytes": entry.size,
            "files": entry.file_count,
            "pinned": entry.pinned,
            "in_use": entry.protected,
            "valid_grib": entry.valid_grib,
            "valid_sounding": entry.valid_sounding,
        }
        QApplication.clipboard().setText(json.dumps(payload, indent=2))

    def _toggle_pin(self):
        entry = self._selected_entry()
        if entry is None:
            return
        try:
            self._cache.set_pinned(entry.path, not entry.pinned)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Downloaded Data Library", str(exc))
        self.refresh()

    def _delete_selected(self):
        entry = self._selected_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete cached data?",
            f"Delete {entry.model.upper()} F{entry.fxx:03d} cached data?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if not self._cache.delete(entry.path):
            QMessageBox.information(
                self, "Downloaded Data Library",
                "This entry is currently in use and cannot be deleted."
            )
        self.refresh()

    def _clear_unpinned(self):
        answer = QMessageBox.question(
            self,
            "Clear unpinned cached data?",
            "Delete all unpinned cache entries that are not currently in use?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._cache.clear(include_pinned=False)
            self.refresh()

    def _use_selected(self):
        entry = self._selected_entry()
        if entry is None or not entry_reusable(entry) \
                or self._use_callback is None:
            return
        self._cache.touch(entry.path)
        self._use_callback(entry)
        self.accept()


__all__ = [
    "CacheManagerDialog", "entry_reusable", "format_size",
    "parse_spatial_point",
]
