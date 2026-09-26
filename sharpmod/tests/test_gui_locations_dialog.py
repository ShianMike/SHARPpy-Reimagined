"""Saved-locations dialog numeric sorting."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sharpmod.gui_locations import SavedLocationsDialog, _LocationEditor
from sharpmod.saved_locations import SavedLocationStore
from qtpy.QtCore import QSettings, Qt


def _store(tmp_path):
    store = SavedLocationStore(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    store.upsert("North", 35.22, -97.44)
    store.upsert("South", 9.50, -97.44)
    return store


def test_latitude_sorts_numerically_not_lexicographically(qt_app, tmp_path):
    dialog = SavedLocationsDialog(_store(tmp_path))
    try:
        dialog.table.sortItems(1)
        qt_app.processEvents()
        lats = [dialog.table.item(row, 1).text() for row in range(2)]
        assert lats == ["9.50000", "35.22000"]
    finally:
        dialog.close()
        dialog.deleteLater()

def test_favorite_toggle_pins_star_and_filters(qt_app, tmp_path):
    from qtpy.QtCore import QSettings
    settings = QSettings(str(tmp_path / "fav.ini"), QSettings.IniFormat)
    store = SavedLocationStore(settings)
    store.upsert("North", 35.22, -97.44)
    store.upsert("South", 9.50, -100.10)
    dialog = SavedLocationsDialog(store, settings=settings)
    try:
        qt_app.processEvents()
        dialog.table.selectRow(0)
        dialog.favorite_button.click()
        qt_app.processEvents()
        names = [dialog.table.item(row, 0).text() for row in range(2)]
        assert any(name.startswith("[Favorite] ") for name in names)
        dialog.only_favorites.setChecked(True)
        qt_app.processEvents()
        assert dialog.table.rowCount() == 1
    finally:
        dialog.close()
        dialog.deleteLater()


def test_favorites_persist_through_store_settings_by_default(qt_app, tmp_path):
    settings = QSettings(str(tmp_path / "fav-default.ini"), QSettings.IniFormat)
    store = SavedLocationStore(settings)
    store.upsert("North", 35.22, -97.44)
    first = SavedLocationsDialog(store)
    second = None
    try:
        first.favorite_button.click()
        first.close()
        second = SavedLocationsDialog(store)
        second.only_favorites.setChecked(True)
        qt_app.processEvents()
        assert second.table.rowCount() == 1
        assert second.table.item(0, 0).text() == "[Favorite] North"
    finally:
        first.close()
        first.deleteLater()
        if second is not None:
            second.close()
            second.deleteLater()


def test_groups_filter_search_sort_and_preserve_selection(qt_app, tmp_path):
    settings = QSettings(str(tmp_path / "groups.ini"), QSettings.IniFormat)
    store = SavedLocationStore(settings)
    store.upsert("North", 35.22, -97.44, group="Field work")
    store.upsert("South", 9.50, -100.10, group="Home")
    store.upsert("West", 30.00, -120.00)
    dialog = SavedLocationsDialog(store, settings=settings)
    try:
        dialog.table.sortItems(3)
        qt_app.processEvents()
        groups = [dialog.table.item(row, 3).text() for row in range(3)]
        assert groups == ["Ungrouped", "Field work", "Home"]

        home_index = dialog.group_filter.findData("Home")
        assert home_index >= 0
        dialog.group_filter.setCurrentIndex(home_index)
        qt_app.processEvents()
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).data(Qt.UserRole).name == "South"

        dialog.group_filter.setCurrentIndex(0)
        dialog.search_edit.setText("field work")
        qt_app.processEvents()
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).data(Qt.UserRole).name == "North"

        dialog.search_edit.clear()
        qt_app.processEvents()
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.UserRole).name == "South":
                dialog.table.selectRow(row)
                break
        dialog.refresh()
        assert dialog._selected().name == "South"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_location_editor_round_trips_group(qt_app, tmp_path):
    location = _store(tmp_path).load()[0]
    editor = _LocationEditor(location)
    try:
        editor.group_edit.setText("  Forecast offices  ")
        assert editor.location().group == "Forecast offices"
    finally:
        editor.close()
        editor.deleteLater()


def test_portable_actions_reflow_below_location_actions(qt_app, tmp_path):
    dialog = SavedLocationsDialog(_store(tmp_path))
    try:
        dialog.show()
        qt_app.processEvents()
        assert dialog.import_button.y() > dialog.add_button.y()
        assert dialog.export_button.y() > dialog.favorite_button.y()
    finally:
        dialog.close()
        dialog.deleteLater()
