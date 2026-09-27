"""Choose and order the diagnostic columns of the comparison table.

The comparison table previously rendered one fixed set of metrics in one fixed
order. Which diagnostics matter depends entirely on what the analyst is looking
at, and the table is already wide enough that unwanted columns push the relevant
ones off screen. This module owns the selection and the order, keeps them valid,
and persists them so the choice survives both a session restore and a restart.

Precision, units, and labels are not decided here: they come from the existing
``box_analysis`` parameter registry, so a column reads the same wherever it
appears.
"""

from __future__ import annotations

import json
import logging

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from sharpmod.analysis.box_analysis import PARAMETERS, parameter  # noqa: F401 - registry order
from sharpmod.ui.features.gui_common import make_status_label, set_status_label
from sharpmod.analysis.profile_metrics import DEFAULT_METRIC_KEYS
from sharpmod.ui.styles.theme import OBJ_HINT, SPACE

_LOGGER = logging.getLogger(__name__)

#: Durable preference, stored beside the other additive settings documents.
SETTINGS_KEY = "analysis/comparison_metrics"
SETTINGS_VERSION = 1

#: One column pair per metric is already wide; beyond this the table stops being
#: readable in any dock and the choice stops being a choice.
MAX_COLUMNS = 12


def available_keys():
    """Return every metric the comparison engine can be asked for, in registry order."""
    return tuple(item.key for item in PARAMETERS)


def metric_label(key):
    """Return the reader-facing column label, with units, from the registry."""
    try:
        item = parameter(key)
    except Exception:
        return str(key).replace("_", " ").upper()
    return item.display_label if item.display_units else item.label


def normalize_keys(keys, *, default=DEFAULT_METRIC_KEYS):
    """Return a valid, de-duplicated, bounded column order.

    Unknown keys are dropped rather than requested from the engine, and an empty
    result falls back to the default set instead of leaving a table with no
    diagnostics at all.
    """
    known = set(available_keys())
    ordered = []
    for key in keys or ():
        name = str(key)
        if name in known and name not in ordered:
            ordered.append(name)
        if len(ordered) >= MAX_COLUMNS:
            break
    return tuple(ordered) if ordered else tuple(default)


def read_preference(settings, *, default=DEFAULT_METRIC_KEYS):
    """Read the durable column preference, tolerating anything unusable."""
    if settings is None:
        return tuple(default)
    try:
        raw = settings.value(SETTINGS_KEY, "", str)
    except TypeError:
        raw = settings.value(SETTINGS_KEY, "")
    if not raw:
        return tuple(default)
    try:
        document = json.loads(str(raw))
    except (TypeError, ValueError):
        _LOGGER.debug("metric_columns.preference_unreadable")
        return tuple(default)
    if not isinstance(document, dict):
        return tuple(default)
    try:
        version = int(document.get("version", 0))
    except (TypeError, ValueError):
        return tuple(default)
    if version > SETTINGS_VERSION:
        # A newer application wrote this; leave it alone and use the default.
        return tuple(default)
    keys = document.get("keys")
    if not isinstance(keys, (list, tuple)):
        return tuple(default)
    return normalize_keys(keys, default=default)


def write_preference(settings, keys) -> bool:
    """Persist the column preference; returns whether it was stored."""
    if settings is None:
        return False
    payload = json.dumps(
        {"version": SETTINGS_VERSION, "keys": list(normalize_keys(keys))},
        separators=(",", ":"),
    )
    try:
        settings.setValue(SETTINGS_KEY, payload)
    except (AttributeError, RuntimeError, TypeError):
        _LOGGER.debug("metric_columns.preference_not_written")
        return False
    return True


class MetricColumnsDialog(QDialog):
    """Pick the comparison columns and the order they appear in."""

    def __init__(
        self,
        current,
        parent=None,
        *,
        default=DEFAULT_METRIC_KEYS,
        title="Comparison columns",
        hint_text=None,
        list_name="Comparison table columns, in order",
        limit=MAX_COLUMNS,
    ):
        super().__init__(parent)
        self._default = tuple(default)
        # Parameterised so the trend workspace can reuse this picker for its
        # stacked tracks. A track is bounded far lower than a column, and the two
        # say different things, but the selection, ordering, validation, and reset
        # behaviour is identical and worth having in one place.
        self._limit = max(1, int(limit))
        self.setWindowTitle(title)
        self.setObjectName("metricColumnsDialog")
        self.resize(460, 520)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE["sm"])

        hint = QLabel(
            hint_text
            or (
                "Checked diagnostics become table columns, each with its own value "
                f"and Δ column, in the order listed. Up to {self._limit} may be "
                "chosen; units and precision come from the diagnostic itself."
            ),
            self,
        )
        hint.setObjectName(OBJ_HINT)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.PlainText)
        layout.addWidget(hint)

        self.columns = QListWidget(self)
        self.columns.setAccessibleName(list_name)
        self.columns.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.columns.setWordWrap(True)
        layout.addWidget(self.columns, 1)

        self.status = make_status_label("", parent=self)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        self.move_up = buttons.addButton("Move up", QDialogButtonBox.ActionRole)
        self.move_down = buttons.addButton("Move down", QDialogButtonBox.ActionRole)
        self.reset = buttons.addButton(
            "Reset to defaults", QDialogButtonBox.ResetRole
        )
        for button in buttons.buttons():
            button.setAutoDefault(False)
        layout.addWidget(buttons)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.move_up.clicked.connect(lambda: self._move(-1))
        self.move_down.clicked.connect(lambda: self._move(1))
        self.reset.clicked.connect(self._reset)
        self.columns.itemChanged.connect(self._refresh_status)
        self.columns.currentRowChanged.connect(self._refresh_status)

        self._populate(current)
        self.columns.setFocus()

    # -- contents -----------------------------------------------------------

    def _populate(self, keys):
        """List the chosen columns first, in order, then the rest unchecked."""
        chosen = normalize_keys(keys, default=self._default)
        blocked = self.columns.blockSignals(True)
        self.columns.clear()
        for key in chosen:
            self._add(key, checked=True)
        for key in available_keys():
            if key not in chosen:
                self._add(key, checked=False)
        self.columns.blockSignals(blocked)
        if self.columns.count():
            self.columns.setCurrentRow(0)
        self._refresh_status()

    def _add(self, key, *, checked):
        item = QListWidgetItem(metric_label(key))
        item.setData(Qt.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setToolTip(f"{metric_label(key)} · registry key {key}")
        self.columns.addItem(item)

    def selected_keys(self):
        """Return the checked keys in their listed order."""
        return tuple(
            self.columns.item(row).data(Qt.UserRole)
            for row in range(self.columns.count())
            if self.columns.item(row).checkState() == Qt.Checked
        )

    # -- actions ------------------------------------------------------------

    def _move(self, delta):
        row = self.columns.currentRow()
        target = row + int(delta)
        if row < 0 or not 0 <= target < self.columns.count():
            return
        item = self.columns.takeItem(row)
        self.columns.insertItem(target, item)
        self.columns.setCurrentRow(target)
        self._refresh_status()

    def _reset(self):
        self._populate(self._default)

    def _refresh_status(self, *_args):
        chosen = self.selected_keys()
        row = self.columns.currentRow()
        self.move_up.setEnabled(row > 0)
        self.move_down.setEnabled(0 <= row < self.columns.count() - 1)
        if not chosen:
            set_status_label(
                self.status,
                "Choose at least one diagnostic; the table needs a column to compare.",
                level="warning",
            )
        elif len(chosen) > self._limit:
            set_status_label(
                self.status,
                f"Choose at most {self._limit}; only the first {self._limit} "
                "would be shown.",
                level="warning",
            )
        else:
            set_status_label(
                self.status,
                f"{len(chosen)} column(s), {len(chosen) * 2} table columns "
                "including Δ.",
            )
        ok = self.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(bool(chosen) and len(chosen) <= self._limit)

    def accept(self):  # noqa: D102 - Qt override
        if not self.selected_keys():
            return
        super().accept()


__all__ = [
    "MAX_COLUMNS",
    "SETTINGS_KEY",
    "MetricColumnsDialog",
    "available_keys",
    "metric_label",
    "normalize_keys",
    "read_preference",
    "write_preference",
]
