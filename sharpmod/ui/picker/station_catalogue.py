"""Station catalogue view sizing for the observed sounding picker."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QTreeWidget, QTreeWidgetItem


class StationCatalogueItem(QTreeWidgetItem):
    """Sort coordinate columns by value instead of their displayed text."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        if column in (2, 3):
            return float(self.data(column, Qt.UserRole)) < float(
                other.data(column, Qt.UserRole))
        # Calling Qt's base comparator from this Python override re-enters the
        # override on PySide6. Compare display text directly instead.
        return self.text(column).casefold() < other.text(column).casefold()


class StationCatalogueView(QTreeWidget):
    """Keep station names readable when the results pane gets narrow."""

    COORDINATE_COLUMNS_MIN_WIDTH = 620

    def __init__(self, parent=None):
        super().__init__(parent)
        self._compact_columns: bool | None = None

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        compact = self.viewport().width() < self.COORDINATE_COLUMNS_MIN_WIDTH
        if compact == self._compact_columns:
            return
        self._compact_columns = compact
        # Coordinates remain available in the selected-station card and tooltip.
        self.setColumnHidden(2, compact)
        self.setColumnHidden(3, compact)
