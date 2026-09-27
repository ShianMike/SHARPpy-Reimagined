"""Saved/recent point controls shared by forecast and reanalysis pickers."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sharpmod.ui.features.gui_common import SortableTableItem
from sharpmod.state.saved_locations import LocationFormatError, SavedLocation
from sharpmod.export_paths import ExportDirectoryError, export_file_path


class _LocationEditor(QDialog):
    def __init__(self, location=None, *, point=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saved Location")
        form = QFormLayout(self)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Home, OUN, Manila…")
        form.addRow("Name", self.name_edit)
        self.lat_edit = QDoubleSpinBox(self)
        self.lat_edit.setRange(-90.0, 90.0)
        self.lat_edit.setDecimals(5)
        self.lat_edit.setSingleStep(0.1)
        form.addRow("Latitude", self.lat_edit)
        self.lon_edit = QDoubleSpinBox(self)
        self.lon_edit.setRange(-180.0, 180.0)
        self.lon_edit.setDecimals(5)
        self.lon_edit.setSingleStep(0.1)
        form.addRow("Longitude", self.lon_edit)
        self.group_edit = QLineEdit(self)
        self.group_edit.setPlaceholderText("Optional group, such as Home or Field work")
        form.addRow("Group", self.group_edit)
        if location is not None:
            self.name_edit.setText(location.name)
            self.lat_edit.setValue(location.lat)
            self.lon_edit.setValue(location.lon)
            self.group_edit.setText(location.group)
        elif point is not None:
            self.lat_edit.setValue(float(point[0]))
            self.lon_edit.setValue(float(point[1]))
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def location(self):
        return SavedLocation.create(
            self.name_edit.text(), self.lat_edit.value(), self.lon_edit.value(),
            self.group_edit.text(),
        )

    def accept(self):
        try:
            self.location()
        except LocationFormatError as exc:
            QMessageBox.warning(self, "Saved Location", str(exc))
            return
        super().accept()


class SavedLocationsDialog(QDialog):
    """Manage a versioned saved-location store and apply points to a picker."""

    _FAVORITE_KEY = "locations/favorites"
    _FAVORITE_PREFIX = "[Favorite] "

    def __init__(self, store, *, current_point=None, use_callback=None,
                 parent=None, settings=None):
        super().__init__(parent)
        self._store = store
        self._settings = settings or getattr(store, "settings", None)
        saved = (
            self._settings.value(self._FAVORITE_KEY, [], list)
            if self._settings is not None else []
        )
        self._favorites = {str(value) for value in saved or []}
        self._current_point = current_point
        self._use_callback = use_callback
        self.setWindowTitle("Saved Locations")
        self.resize(620, 410)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Save frequently used latitude/longitude points. Locations can be "
            "organized into groups, exported as portable JSON, and imported "
            "on another computer."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search_edit = QLineEdit(self)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(
            "Search saved locations by name, group, or coordinate…"
        )
        self.search_edit.textChanged.connect(self.refresh)
        layout.addWidget(self.search_edit)
        filters = QHBoxLayout()
        self.only_favorites = QCheckBox("Favorites only", self)
        self.only_favorites.toggled.connect(self.refresh)
        filters.addWidget(self.only_favorites)
        filters.addWidget(QLabel("Group", self))
        self.group_filter = QComboBox(self)
        self.group_filter.setAccessibleName("Saved location group filter")
        self.group_filter.addItem("All groups", None)
        self.group_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.group_filter)
        filters.addStretch(1)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ("Name", "Latitude", "Longitude", "Group")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._use_selected())
        layout.addWidget(self.table, 1)

        actions = QGridLayout()
        self.add_button = QPushButton("Add…", self)
        self.add_button.clicked.connect(self._add)
        actions.addWidget(self.add_button, 0, 0)
        self.edit_button = QPushButton("Edit…", self)
        self.edit_button.clicked.connect(self._edit)
        actions.addWidget(self.edit_button, 0, 1)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.remove_button, 0, 2)
        self.favorite_button = QPushButton("Add to favorites", self)
        self.favorite_button.clicked.connect(self._toggle_favorite)
        actions.addWidget(self.favorite_button, 0, 3)
        self.use_button = QPushButton("Use selected", self)
        self.use_button.clicked.connect(self._use_selected)
        actions.addWidget(self.use_button, 0, 4)
        actions.setColumnStretch(2, 1)
        self.import_button = QPushButton("Import…", self)
        self.import_button.clicked.connect(self._import)
        actions.addWidget(self.import_button, 1, 3)
        self.export_button = QPushButton("Export…", self)
        self.export_button.clicked.connect(self._export)
        actions.addWidget(self.export_button, 1, 4)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def refresh(self, *_args):
        selected_name = getattr(self._selected(), "name", "").casefold()
        try:
            locations = self._store.load()
        except LocationFormatError as exc:
            QMessageBox.warning(self, "Saved Locations", str(exc))
            locations = []
        self._refresh_group_filter(locations)
        query = self.search_edit.text().strip().casefold()
        selected_group = self.group_filter.currentData()
        if selected_group is not None:
            locations = [
                item for item in locations
                if item.group == selected_group
            ]
        if self.only_favorites.isChecked():
            locations = [
                item for item in locations
                if item.name in self._favorites
            ]
        if query:
            locations = [
                item for item in locations
                if query in item.name.casefold()
                or query in item.group.casefold()
                or query in f"{item.lat:.5f}"
                or query in f"{item.lon:.5f}"
            ]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(locations))
        for row, location in enumerate(locations):
            label = (
                self._FAVORITE_PREFIX
                if location.name in self._favorites else ""
            ) + location.name
            values = (
                label, f"{location.lat:.5f}", f"{location.lon:.5f}",
                location.group or "Ungrouped",
            )
            keys = (
                label.casefold(), location.lat, location.lon,
                location.group.casefold(),
            )
            for column, (value, key) in enumerate(zip(values, keys)):
                item = SortableTableItem(value, sort_key=key)
                if column == 0:
                    item.setData(Qt.UserRole, location)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        selected_row = next(
            (
                row for row in range(self.table.rowCount())
                if getattr(self.table.item(row, 0).data(Qt.UserRole), "name", "")
                .casefold() == selected_name
            ),
            -1,
        )
        if selected_row < 0 and locations:
            selected_row = 0
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self._update_buttons()

        self.favorite_button.setText(
            "Remove from favorites"
            if self._selected() is not None
            and self._selected().name in self._favorites
            else "Add to favorites"
        )

    def _refresh_group_filter(self, locations):
        selected = self.group_filter.currentData()
        groups = sorted(
            {item.group for item in locations if item.group}, key=str.casefold
        )
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("All groups", None)
        self.group_filter.addItem("Ungrouped", "")
        for group in groups:
            self.group_filter.addItem(group, group)
        index = self.group_filter.findData(selected)
        self.group_filter.setCurrentIndex(index if index >= 0 else 0)
        self.group_filter.blockSignals(False)

    def _save_favorites(self):
        if self._settings is not None:
            self._settings.setValue(self._FAVORITE_KEY, sorted(self._favorites))

    def _toggle_favorite(self):
        location = self._selected()
        if location is None:
            return
        if location.name in self._favorites:
            self._favorites.remove(location.name)
        else:
            self._favorites.add(location.name)
        self._save_favorites()
        self.refresh()

    def _update_buttons(self):
        selected = self._selected() is not None
        self.favorite_button.setEnabled(selected)
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        self.use_button.setEnabled(selected and self._use_callback is not None)

    def _point(self):
        if callable(self._current_point):
            return self._current_point()
        return self._current_point

    def _add(self):
        editor = _LocationEditor(point=self._point(), parent=self)
        if editor.exec() == QDialog.Accepted:
            location = editor.location()
            self._store.upsert(
                location.name, location.lat, location.lon, group=location.group
            )
            self.refresh()

    def _edit(self):
        original = self._selected()
        if original is None:
            return
        editor = _LocationEditor(original, parent=self)
        if editor.exec() != QDialog.Accepted:
            return
        location = editor.location()
        was_favorite = original.name in self._favorites
        if original.name.casefold() != location.name.casefold():
            self._store.remove(original.name)
            self._favorites.discard(original.name)
        self._store.upsert(
            location.name, location.lat, location.lon, group=location.group
        )
        if was_favorite:
            self._favorites.add(location.name)
            self._save_favorites()
        self.refresh()

    def _remove(self):
        location = self._selected()
        if location is None:
            return
        answer = QMessageBox.question(
            self, "Remove saved location?",
            f"Remove “{location.name}” from saved locations?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._store.remove(location.name)
            self._favorites.discard(location.name)
            self._save_favorites()
            self.refresh()

    def _use_selected(self):
        location = self._selected()
        if location is None or self._use_callback is None:
            return
        self._use_callback(location)
        self.accept()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Saved Locations", "",
            "SHARPpy Locations (*.json);;JSON files (*.json)"
        )
        if not path:
            return
        try:
            self._store.import_file(path, merge=True)
        except LocationFormatError as exc:
            QMessageBox.warning(self, "Saved Locations", str(exc))
        self.refresh()

    def _export(self):
        try:
            suggested = export_file_path("sharpmod-locations.json")
        except ExportDirectoryError as exc:
            QMessageBox.critical(self, "Saved Locations", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Saved Locations", str(suggested),
            "SHARPpy Locations (*.json)"
        )
        if not path:
            return
        try:
            self._store.export_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "Saved Locations", str(exc))


__all__ = ["SavedLocationsDialog"]
