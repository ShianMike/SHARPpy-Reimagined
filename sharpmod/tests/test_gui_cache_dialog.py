"""Cache-manager dialog search and numeric sorting."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from sharpmod.gui_cache import CacheManagerDialog
from sharpmod.model_disk_cache import CacheEntry


def _entry(model, run, fxx, size, accessed, member=None, spatial=None):
    return CacheEntry(
        path=Path(f"/cache/{model}{fxx}"), model=model, run=run, fxx=fxx,
        member=member, spatial=spatial, source_url=None, source_transport=None,
        source_fields=(), accessed=accessed, size=size, protected=False,
        pinned=False, valid_grib=True, valid_sounding=False, file_count=1,
        contract_version=4,
    )


class _StubCache:
    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return list(self._entries)


def _rows(dialog):
    return [
        dialog.table.item(row, 0).text()
        for row in range(dialog.table.rowCount())
    ]


def test_search_filters_to_matching_entries(qt_app):
    from qtpy.QtWidgets import QAbstractItemView

    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 512, 1000.0),
        _entry("gfs", "2026-09-18 12:00", 6, 256, 2000.0),
    ]))
    try:
        assert dialog.table.selectionMode() == QAbstractItemView.ExtendedSelection
        assert dialog.table.rowCount() == 2
        dialog.search_edit.setText("gfs")
        qt_app.processEvents()
        assert dialog.table.rowCount() == 1
        assert _rows(dialog) == ["GFS"]
        dialog.search_edit.clear()
        qt_app.processEvents()
        assert dialog.table.rowCount() == 2
    finally:
        dialog.close()
        dialog.deleteLater()


def test_delete_selected_removes_every_highlighted_row(qt_app, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args: asked.append(args[2]) or QMessageBox.Yes,
    )
    deleted = []

    class _DeletableCache(_StubCache):
        def delete(self, path):
            deleted.append(path)
            self._entries = [e for e in self._entries if e.path != path]
            return True

    dialog = CacheManagerDialog(_DeletableCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 1536, 1000.0),
        _entry("gfs", "2026-09-18 12:00", 6, 512, 2000.0),
    ]))
    try:
        from qtpy.QtCore import QItemSelectionModel
        model = dialog.table.selectionModel()
        model.select(dialog.table.model().index(0, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        model.select(dialog.table.model().index(1, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        qt_app.processEvents()
        dialog.delete_button.click()
        qt_app.processEvents()
        assert len(asked) == 1
        assert "2" in asked[0] and "2.0 KiB" in asked[0]
        assert sorted(deleted) == [Path("/cache/gfs6"), Path("/cache/hrrr3")]
        assert dialog.table.rowCount() == 0
    finally:
        dialog.close()
        dialog.deleteLater()


def test_size_sorts_descending_by_bytes(qt_app):
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 0, 1536, 1000.0),
        _entry("hrrr", "2026-09-18 12:00", 1, 512, 2000.0),
    ]))
    try:
        from qtpy.QtCore import Qt
        dialog.table.sortItems(6, Qt.DescendingOrder)
        qt_app.processEvents()
        sizes = [dialog.table.item(row, 6).text() for row in range(2)]
        assert sizes == ["1.5 KiB", "512 B"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_size_sorts_by_bytes_not_text(qt_app):
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 0, 1536, 1000.0),
        _entry("hrrr", "2026-09-18 12:00", 1, 512, 2000.0),
    ]))
    try:
        dialog.table.sortItems(6)
        qt_app.processEvents()
        sizes = [dialog.table.item(row, 6).text() for row in range(2)]
        assert sizes == ["512 B", "1.5 KiB"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_delete_preview_names_size(qt_app, monkeypatch):
    from qtpy.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args: asked.append(args[2]) or QMessageBox.No,
    )
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 1536, 1000.0),
    ]))
    try:
        dialog.delete_button.click()
        qt_app.processEvents()
        assert len(asked) == 1
        assert "1.5 KiB" in asked[0]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_clear_unpinned_preview_names_count_and_bytes(qt_app, monkeypatch):
    from qtpy.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args: asked.append(args[2]) or QMessageBox.No,
    )
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 1536, 1000.0),
        _entry("gfs", "2026-09-18 12:00", 6, 512, 2000.0),
    ]))
    try:
        clear = [b for b in dialog.findChildren(type(dialog.delete_button)) if b.text() == "Clear unpinned"]
        assert len(clear) == 1
        clear[0].click()
        qt_app.processEvents()
        assert len(asked) == 1
        assert "2" in asked[0] and "2.0 KiB" in asked[0]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_storage_total_labels_entry_count_and_bytes(qt_app):
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 1536, 1000.0),
        _entry("gfs", "2026-09-18 12:00", 6, 512, 2000.0),
    ]))
    try:
        qt_app.processEvents()
        assert "2" in dialog.storage_label.text()
        assert "2.0 KiB" in dialog.storage_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_selection_detail_names_reuse_state(qt_app):
    dialog = CacheManagerDialog(_StubCache([
        _entry("hrrr", "2026-09-18 12:00", 3, 1536, 1000.0),
    ]))
    try:
        qt_app.processEvents()
        assert "GRIB" in dialog.detail_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()
