"""Compact picker rows for map layers and their on-demand settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget,
)

from sharpmod.ui.styles.theme import (
    OBJ_ERROR_TEXT, OBJ_MAP_TOOL, OBJ_PLAIN, OBJ_STATUS, OBJ_WARNING_TEXT,
    SPACE,
)


@dataclass
class _LayerRow:
    key: str
    controller: object
    check: QCheckBox
    detail: QWidget
    header: QWidget
    status: QLabel
    retry: QToolButton
    edit: QToolButton
    original_tip: str


class CompactLayerControls:
    """Keep layer switches visible while showing at most one settings pane."""

    _RETRY_STATES = {"failed", "unavailable", "offline", "no-data", "canceled"}
    _WARNING_STATES = {"stale", "empty", "outside-domain", "occluded"}

    def __init__(self, controllers: tuple, retry: Callable[[str], None]):
        outlook, reports, radar, field, context = controllers
        self._retry = retry
        self.rows: dict[str, _LayerRow] = {}
        self.open_key: str | None = None
        bindings = (
            ("spc_outlook", outlook, "_check", "_detail"),
            ("storm_reports", reports, "_check", "_detail"),
            ("radar_site", radar, "_check", "_detail"),
            ("hrrr_field", field, "_check", "_detail"),
            ("goes_context", context, "_sat_check", "_sat_details"),
            ("surface_observations", context, "_surface_check", "_surface_details"),
        )
        for key, controller, check_name, detail_name in bindings:
            if controller is not None:
                self._add_row(key, controller, check_name, detail_name)
        self.open(None)

    def _add_row(self, key: str, controller, check_name: str,
                 detail_name: str) -> None:
        control = controller.controls_widget()
        layout = control.layout()
        check = getattr(controller, check_name, None)
        detail = getattr(controller, detail_name, None)
        if layout is None or check is None or detail is None:
            return
        position = layout.indexOf(check)
        if position < 0:
            return

        controller._picker_compact = True
        controller._picker_open_key = None
        layout.removeWidget(check)
        header = QWidget(control)
        header.setObjectName(OBJ_PLAIN)
        line = QHBoxLayout(header)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(SPACE["xxs"])
        original_tip = check.toolTip()
        check.setParent(header)
        check.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        line.addWidget(check, 1)

        status = QLabel(header)
        status.setObjectName(OBJ_STATUS)
        status.setTextFormat(Qt.PlainText)
        status.setContentsMargins(0, 0, SPACE["sm"], 0)
        status.hide()
        line.addWidget(status)

        retry = QToolButton(header)
        retry.setText("Retry")
        retry.setAccessibleName(f"Retry {check.text()}")
        retry.setToolTip(f"Retry {check.text()}")
        retry.hide()
        retry.clicked.connect(lambda _checked=False, _key=key: self._retry(_key))
        line.addWidget(retry)

        edit = QToolButton(header)
        edit.setObjectName(OBJ_MAP_TOOL)
        edit.setText("Edit")
        edit.setCheckable(True)
        edit.setAccessibleName(f"Edit {check.text()} settings")
        edit.setToolTip(f"Show {check.text()} settings")
        edit.toggled.connect(
            lambda checked, _key=key: self.open(_key if checked else None)
        )
        line.addWidget(edit)

        layout.insertWidget(position, header)
        detail.hide()
        self.rows[key] = _LayerRow(
            key, controller, check, detail, header, status, retry, edit,
            original_tip,
        )

    def open(self, key: str | None) -> None:
        """Reveal one layer's settings, closing the previous settings pane."""
        if key not in self.rows or not self.rows[key].edit.isEnabled():
            key = None
        self.open_key = key
        selected = self.rows[key].controller if key else None
        for row in self.rows.values():
            row.controller._picker_open_key = key if row.controller is selected else None
        for row in self.rows.values():
            shown = row.key == key
            row.edit.blockSignals(True)
            row.edit.setChecked(shown)
            row.edit.blockSignals(False)
            row.edit.setToolTip(
                f"{'Hide' if shown else 'Show'} {row.check.text()} settings"
            )
            row.edit.setAccessibleName(
                f"{'Hide' if shown else 'Show'} {row.check.text()} settings"
            )
            set_detail = getattr(row.controller, "_set_detail_visible", None)
            if callable(set_detail):
                set_detail(shown)
            else:
                row.detail.setVisible(shown)

    def refresh(self, entries: list) -> None:
        """Keep each row to one line; put full status in its tooltip."""
        main = [entry for entry in entries
                if entry.scope == "main map" and entry.key != "geography"]
        by_key = {entry.key: entry for entry in main}
        order = {entry.key: index + 1 for index, entry in enumerate(main)}
        for key, row in self.rows.items():
            entry = by_key.get(key)
            if key == "radar_site" and entry is None:
                entry = by_key.get("radar_mosaic")
            if entry is None:
                row.status.hide()
                row.retry.hide()
                continue
            state = str(entry.state)
            failed = state in self._RETRY_STATES
            warning = state in self._WARNING_STATES
            row.edit.setEnabled(row.check.isEnabled())
            if failed:
                short = "Issue"
            elif entry.visible and state == "loading":
                short = "Loading"
            elif entry.visible and warning:
                short = {
                    "empty": "No frame",
                    "stale": "Stale",
                    "outside-domain": "Out of area",
                    "occluded": "Covered",
                }.get(state, "Check")
            elif entry.visible and entry.time_text and len(entry.time_text) <= 12:
                short = str(entry.time_text)
            elif entry.visible and entry.opacity < 1:
                short = f"{round(entry.opacity * 100)}%"
            else:
                short = ""
            row.status.setText(short)
            row.status.setVisible(bool(short))
            row.retry.setVisible(failed)
            role = (OBJ_ERROR_TEXT if failed else
                    OBJ_WARNING_TEXT if warning else OBJ_STATUS)
            if row.status.objectName() != role:
                row.status.setObjectName(role)
                row.status.style().unpolish(row.status)
                row.status.style().polish(row.status)

            facts = [entry.name,
                     f"Draw order {order[entry.key]} of {len(main)} "
                     "(bottom layers are painted first)"]
            if entry.visible:
                facts.append(f"Opacity {round(entry.opacity * 100)}%")
            for value in (entry.time_text, entry.match_text, entry.status_text(),
                          entry.detail, entry.occlusion, entry.requires):
                if value and value not in facts:
                    facts.append(str(value))
            tip = "\n".join(facts)
            row.status.setToolTip(tip)
            row.status.setAccessibleDescription(tip)
            row.check.setToolTip(
                f"{row.original_tip}\n\n{tip}" if row.original_tip else tip
            )
        if self.open_key and not self.rows[self.open_key].edit.isEnabled():
            self.open(None)
