"""Saved/recent point controls shared by forecast and reanalysis pickers."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sharpmod.saved_locations import LocationFormatError, SavedLocation


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
        if location is not None:
            self.name_edit.setText(location.name)
            self.lat_edit.setValue(location.lat)
            self.lon_edit.setValue(location.lon)
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
            self.name_edit.text(), self.lat_edit.value(), self.lon_edit.value()
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

    def __init__(self, store, *, current_point=None, use_callback=None,
                 parent=None):
        super().__init__(parent)
        self._store = store
        self._current_point = current_point
        self._use_callback = use_callback
        self.setWindowTitle("Saved Locations")
        self.resize(620, 410)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Save frequently used latitude/longitude points. Locations can be "
            "exported as portable JSON and imported on another computer."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search_edit = QLineEdit(self)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(
            "Search saved locations by name or coordinate…"
        )
        self.search_edit.textChanged.connect(self.refresh)
        layout.addWidget(self.search_edit)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(("Name", "Latitude", "Longitude"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._use_selected())
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        add = QPushButton("Add…", self)
        add.clicked.connect(self._add)
        row.addWidget(add)
        self.edit_button = QPushButton("Edit…", self)
        self.edit_button.clicked.connect(self._edit)
        row.addWidget(self.edit_button)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.remove_button)
        self.use_button = QPushButton("Use selected", self)
        self.use_button.clicked.connect(self._use_selected)
        row.addWidget(self.use_button)
        row.addStretch(1)
        import_button = QPushButton("Import…", self)
        import_button.clicked.connect(self._import)
        row.addWidget(import_button)
        export_button = QPushButton("Export…", self)
        export_button.clicked.connect(self._export)
        row.addWidget(export_button)
        layout.addLayout(row)

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
        query = self.search_edit.text().strip().casefold()
        if query:
            locations = [
                item for item in locations
                if query in item.name.casefold()
                or query in f"{item.lat:.5f}"
                or query in f"{item.lon:.5f}"
            ]
        self.table.setRowCount(len(locations))
        selected_row = -1
        for row, location in enumerate(locations):
            values = (location.name, f"{location.lat:.5f}", f"{location.lon:.5f}")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, location)
                self.table.setItem(row, column, item)
            if location.name.casefold() == selected_name:
                selected_row = row
        self.table.resizeColumnsToContents()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif locations:
            self.table.selectRow(0)
        self._update_buttons()

    def _update_buttons(self):
        selected = self._selected() is not None
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
            self._store.upsert(location.name, location.lat, location.lon)
            self.refresh()

    def _edit(self):
        original = self._selected()
        if original is None:
            return
        editor = _LocationEditor(original, parent=self)
        if editor.exec() != QDialog.Accepted:
            return
        location = editor.location()
        if original.name.casefold() != location.name.casefold():
            self._store.remove(original.name)
        self._store.upsert(location.name, location.lat, location.lon)
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Saved Locations", "sharpmod-locations.json",
            "SHARPpy Locations (*.json)"
        )
        if not path:
            return
        try:
            self._store.export_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "Saved Locations", str(exc))


__all__ = ["SavedLocationsDialog"]
