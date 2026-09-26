"""Interactive list of active map layers and their status."""

from __future__ import annotations

from datetime import datetime, timezone

from qtpy.QtCore import QEvent, QSize, QTimer, Qt, Signal
from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.styles.theme import (
    CONTROL_H,
    DEPENDENT_INDENT,
    FIELD_W,
    OBJ_CARD,
    OBJ_CARD_TOGGLE,
    OBJ_ATTRIBUTION,
    OBJ_EMPHASIS,
    OBJ_HINT,
    OBJ_MAP_TOOL,
    OBJ_PLAIN,
    OBJ_SECTION_LABEL,
    PROP_COMPACT,
    RAIL_W,
    SCROLLBAR_W,
    SPACE,
)


class ActiveLayerList(QWidget):
    """The T19 active-layer list: one row per layer, bottom-to-top (T19.1).

    Grouped under weather fields, observations/reports/outlooks, and geography
    headings; each row shows name, visibility, opacity, actual time, and
    availability/error state. Rows are checkable (visibility) except the
    always-drawn geography row, carry the map's paint order with a "top"
    marker on the uppermost visible fill so overlap is readable (T19.4), and
    expose a per-row Retry button wherever the row reports a failure (T19.5)
    plus Hide-all/Restore for the cluttered state (T19.2). Temporary
    hide-and-restore remembers only the layers the hide action itself switched
    off, so a restore never re-enables something the user had already turned
    off.

    Owns no fetch and no session: the host supplies entry snapshots and routes
    the signals. Refreshing replaces rows rather than editing them in place, so
    a removed layer cannot linger as a stale row.
    """

    #: ``key`` of the row whose visibility changed.
    visibilityToggled = Signal(str, bool)
    #: ``key`` of the row whose retry was requested.
    retryRequested = Signal(str)

    #: Row height the retry buttons match, so a row with a button is exactly
    #: as tall as a row without one and the list never re-flows on failure.
    _ROW_H = 26
    _RETRY_STATES = frozenset({
        "failed", "unavailable", "offline", "no-data", "canceled",
    })

    def __init__(self, parent=None, *, toolbar_only: bool = False):
        super().__init__(parent)
        self._toolbar_only = toolbar_only
        self.setObjectName(OBJ_PLAIN)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        self._entries: list = []
        self._hidden_by_hide_all: list[str] = []
        self._row_metrics_timer = QTimer(self)
        self._row_metrics_timer.setSingleShot(True)
        self._row_metrics_timer.timeout.connect(self._rebuild_for_theme)

        order_hint = QLabel("Paint order · bottom first, top last", self)
        order_hint.setObjectName(OBJ_SECTION_LABEL)
        order_hint.setToolTip(
            "Layers near the bottom are painted first; later rows can cover them")
        outer.addWidget(order_hint)

        self.list = QListWidget(self)
        self.list.setAccessibleName("Active map layers, bottom to top")
        self.list.setToolTip(
            "Every layer on this map, bottom to top. Each row uses two lines: "
            "identity first, then opacity, actual time, and state. Uncheck a "
            "row to hide it without losing its data; failures keep Retry.")
        self.list.setWordWrap(False)
        self.list.setUniformItemSizes(False)
        outer.addWidget(self.list)
        if toolbar_only:
            order_hint.hide()
            self.list.hide()

        preset_row = QHBoxLayout()
        preset_row.setSpacing(SPACE["xs"])
        preset_label = QLabel("Preset", self)
        preset_label.setObjectName(OBJ_SECTION_LABEL)
        preset_row.addWidget(preset_label)
        self.preset_combo = QComboBox(self)
        self.preset_combo.setToolTip(
            "Choose a compatible layer set with readable opacities")
        self.preset_combo.setAccessibleName("Layer preset")
        self.preset_combo.setMinimumWidth(0)
        self.preset_combo.setSizePolicy(QSizePolicy.Ignored,
                                        QSizePolicy.Fixed)
        preset_row.addWidget(self.preset_combo, 1)
        self.apply_preset_btn = QToolButton(self)
        self.apply_preset_btn.setText("Apply")
        self.apply_preset_btn.setToolTip("Apply the chosen layer preset")
        self.apply_preset_btn.setAccessibleName("Apply layer preset")
        outer.addLayout(preset_row)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(SPACE["xs"])
        self.hide_all = QToolButton(self)
        self.hide_all.setText("Hide all")
        self.hide_all.setToolTip("Temporarily hide every visible map layer")
        self.hide_all.setAccessibleName("Hide all map layers")
        self.hide_all.clicked.connect(self.hide_all_layers)
        self.show_all = QToolButton(self)
        self.show_all.setText("Restore")
        self.show_all.setToolTip("Restore the layers Hide all switched off")
        self.show_all.setAccessibleName("Restore hidden map layers")
        self.show_all.clicked.connect(self.restore_hidden_layers)
        self.show_all.setEnabled(False)
        for action in (self.apply_preset_btn, self.hide_all, self.show_all):
            action.setMinimumWidth(0)
            action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            actions_row.addWidget(action, 1)
        outer.addLayout(actions_row)
        self.summary = QLabel(self)
        self.summary.setObjectName("pickerSectionSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            timer = getattr(self, "_row_metrics_timer", None)
            if timer is not None:
                # Style-sheet propagation reaches parent and children in
                # several events. Coalesce them, then measure the final fonts.
                timer.start(0)

    def _rebuild_for_theme(self) -> None:
        if self._entries:
            self.set_entries(self._entries)

    def set_presets(self, presets: dict, current: str = "") -> None:
        """Fill the preset combo, selecting ``current`` when known."""
        from sharpmod.maps.map_layers import migrate_preset_choice

        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for key, entry in (presets or {}).items():
                self.preset_combo.addItem(str(entry.get("label", key)), key)
            wanted = migrate_preset_choice(current) if current else ""
            index = self.preset_combo.findData(wanted) if wanted else -1
            self.preset_combo.setCurrentIndex(max(0, index))
        finally:
            self.preset_combo.blockSignals(False)

    def selected_preset(self) -> str:
        """Return the chosen preset key, or ``""``."""
        try:
            return str(self.preset_combo.currentData() or "")
        except (AttributeError, RuntimeError):
            return ""

    def set_entries(self, entries) -> None:
        """Replace the rows with ``entries`` (already in paint order)."""
        from sharpmod.maps.map_layers import LAYER_GROUPS

        self._entries = list(entries or ())
        self.list.blockSignals(True)
        try:
            self.list.clear()
            last_group = None
            for entry in self._entries:
                group = str(getattr(entry, "group", "") or "")
                if group in LAYER_GROUPS and group != last_group:
                    heading_label = {
                        "weather fields": "Weather fields",
                        "observations/reports/outlooks": "Observations & outlooks",
                        "geography": "Map context",
                        "sounding locator": "Sounding locator",
                    }.get(group, group.title())
                    heading = QListWidgetItem(heading_label)
                    heading.setFlags(Qt.NoItemFlags)
                    heading.setToolTip(f"{heading_label}: paint-order section")
                    heading.setData(Qt.AccessibleTextRole, heading_label)
                    heading.setSizeHint(QSize(
                        0,
                        max(
                            self._ROW_H - 3,
                            self.list.fontMetrics().lineSpacing() + SPACE["sm"],
                        ),
                    ))
                    self.list.addItem(heading)
                    last_group = group
                primary, secondary, tip = self._row_texts(entry)
                display_text = (
                    f"{primary}\n{secondary}" if secondary else primary)
                item = QListWidgetItem(display_text)
                # Preserve the ordinary item text as a stable inspection and
                # accessibility contract, but let the installed row widget do
                # the painting.  Transparent delegate text prevents a ghosted
                # second copy beneath that widget.
                item.setForeground(QBrush(QColor(0, 0, 0, 0)))
                item.setData(Qt.UserRole, str(getattr(entry, "key", "")))
                item.setData(
                    Qt.AccessibleTextRole,
                    f"{primary}. {secondary}" if secondary else primary,
                )
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setToolTip(tip)
                # QListWidgetItem carries no accessible description; the
                # tooltip text is what assistive technology reads for a row.
                try:
                    item.setAccessibleDescription(tip)
                except AttributeError:
                    pass
                self.list.addItem(item)
                self._add_layer_row(
                    item,
                    entry,
                    primary,
                    secondary,
                    tip,
                    retry=(str(getattr(entry, "state", "")) in
                           self._RETRY_STATES),
                )
        finally:
            self.list.blockSignals(False)
        self._refresh_chrome()
        self._update_summary()

    def entries(self) -> list:
        """Return the current entry snapshots."""
        return list(self._entries)

    def hide_all_layers(self) -> None:
        """Hide every visible layer, remembering what was hidden (T19.2)."""
        self._hidden_by_hide_all = [
            str(entry.key) for entry in self._entries
            if entry.visible and entry.key != "geography"
            and not str(entry.key).startswith("locator:")
        ]
        for key in self._hidden_by_hide_all:
            self.visibilityToggled.emit(key, False)
        self._refresh_chrome()

    def restore_hidden_layers(self) -> None:
        """Restore exactly what Hide all switched off (T19.2)."""
        # Reports depend on the outlook switch, so restore that parent first
        # even though it sits later in paint order.
        for key in sorted(self._hidden_by_hide_all,
                          key=lambda item: item != "spc_outlook"):
            self.visibilityToggled.emit(key, True)
        self._hidden_by_hide_all = []
        self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        self.hide_all.setEnabled(any(
            entry.visible and entry.key != "geography"
            and not str(entry.key).startswith("locator:")
            for entry in self._entries
        ))
        self.show_all.setEnabled(bool(self._hidden_by_hide_all))
        if self._hidden_by_hide_all:
            self.show_all.setToolTip(
                f"Restore {len(self._hidden_by_hide_all)} hidden "
                "layer(s) — only what Hide all switched off")
        else:
            self.show_all.setToolTip(
                "Restore the layers Hide all switched off")

    def _add_layer_row(
        self,
        item,
        entry,
        primary: str,
        secondary: str,
        tip: str,
        *,
        retry: bool,
    ) -> None:
        """Install one readable, actionable two-line layer row.

        Qt's default list delegate flattens embedded newlines once a check
        indicator and eliding compete for the narrow rail.  A single row widget
        gives every state the same hierarchy: checkbox, identity, muted facts,
        and (only when needed) a scoped Retry action.  The item still carries
        accessible text and the stable layer key.
        """
        host = QWidget(self.list)
        host.setObjectName(OBJ_PLAIN)
        host.setToolTip(tip)
        host.setAccessibleName(
            f"{primary}. {secondary}" if secondary else primary)
        try:
            host.setAccessibleDescription(tip)
        except AttributeError:
            pass
        layout = QHBoxLayout(host)
        layout.setContentsMargins(SPACE["xs"], 2, 2, 2)
        layout.setSpacing(SPACE["sm"])
        key = str(getattr(entry, "key", ""))
        checkable = key != "geography" and not key.startswith("locator:")
        if checkable:
            toggle = QCheckBox(host)
            toggle.setChecked(bool(entry.visible))
            toggle.setText("")
            toggle.setToolTip(
                f"Show or hide {entry.name} without losing its data")
            toggle.setAccessibleName(f"Show {entry.name}")
            toggle.toggled.connect(
                lambda on, _key=key: self.visibilityToggled.emit(_key, bool(on)))
            layout.addWidget(toggle, 0, Qt.AlignLeft | Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        primary_label = QLabel(primary, host)
        primary_label.setObjectName(OBJ_EMPHASIS)
        primary_label.setTextFormat(Qt.PlainText)
        primary_label.setWordWrap(False)
        primary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        primary_label.setToolTip(tip)
        primary_label.setAccessibleName(
            f"{entry.name} {getattr(entry, 'state', '')}".strip())
        primary_label.ensurePolished()
        text.addWidget(primary_label)
        labels = [primary_label]
        if secondary:
            secondary_label = QLabel(secondary, host)
            secondary_label.setObjectName(OBJ_HINT)
            secondary_label.setTextFormat(Qt.PlainText)
            secondary_label.setWordWrap(False)
            secondary_label.setSizePolicy(
                QSizePolicy.Ignored, QSizePolicy.Fixed)
            secondary_label.setToolTip(tip)
            secondary_label.ensurePolished()
            text.addWidget(secondary_label)
            labels.append(secondary_label)
        layout.addLayout(text, 1)

        if retry:
            button = QToolButton(host)
            button.setText("Retry")
            button.setToolTip(
                f"Retry {entry.name} alone; other layers keep theirs")
            button.setAccessibleName(f"Retry {entry.name}")
            button.setMinimumHeight(CONTROL_H["sm"])
            button.ensurePolished()
            button.clicked.connect(
                lambda _checked=False, _key=key:
                self.retryRequested.emit(_key))
            layout.addWidget(button, 0, Qt.AlignRight | Qt.AlignVCenter)

        # Use the actual themed line heights rather than a nominal 100%-scale
        # constant.  This is what keeps the primary and secondary lines from
        # painting over one another at the supported 125–200% text settings.
        margins = layout.contentsMargins()
        text_height = sum(
            max(label.sizeHint().height(), label.fontMetrics().lineSpacing())
            for label in labels
        )
        chrome = [
            *host.findChildren(QCheckBox),
            *host.findChildren(QToolButton),
        ]
        chrome_height = max(
            (child.sizeHint().height() for child in chrome), default=0)
        row_height = max(
            self._ROW_H,
            text_height + margins.top() + margins.bottom(),
            chrome_height + margins.top() + margins.bottom(),
        )
        item.setSizeHint(QSize(0, row_height))
        self.list.setItemWidget(item, host)

    def _row_texts(self, entry) -> tuple[str, str, str]:
        opacity = (f"{int(round(float(entry.opacity) * 100))}%"
                   if entry.visible else "Hidden")
        time = str(getattr(entry, "time_text", "") or "")
        status = entry.status_text() if hasattr(entry, "status_text") else ""
        # The geography row is always drawn and never carries data of its
        # own: name it without the per-layer value/time/status columns so one
        # housekeeping row cannot set the width of every control rail.
        if str(getattr(entry, "key", "")) == "geography":
            raw_name = str(entry.name)
            primary = "Geography"
            context = ""
            if "(" in raw_name and raw_name.endswith(")"):
                context = raw_name.partition("(")[2][:-1].strip()
            secondary = " · ".join(
                value for value in (context, "Always shown") if value)
        elif str(getattr(entry, "key", "")).startswith("locator:"):
            primary = entry.name
            secondary = str(getattr(entry, "detail", "") or "Shown on locator")
        else:
            primary = entry.name
            facts = [opacity]
            if time:
                facts.append(time)
            if status:
                facts.append(status)
            secondary = " · ".join(facts)
        # T21.2: the per-layer time-match state rides on the same row as the
        # actual time it qualifies, so "nearest -12 min" reads against the
        # frame it names rather than in a separate column.
        match = str(getattr(entry, "match_text", "") or "")
        if match and entry.visible:
            secondary += f" · {match}"
        occlusion = str(getattr(entry, "occlusion", "") or "")
        if occlusion and entry.visible:
            secondary += f" — {occlusion}"
        detail = str(getattr(entry, "detail", "") or "")
        tip = f"{primary}\n{secondary}" if secondary else primary
        requires = str(getattr(entry, "requires", "") or "")
        if requires:
            tip += f"\nRequires: {requires}"
        if detail and detail not in secondary:
            tip += f"\n{detail}"
        scope = str(getattr(entry, "scope", "") or "")
        if scope and scope != "main map":
            tip += f"\n{scope}"
        return primary, secondary, tip

    def _update_summary(self) -> None:
        visible = sum(1 for entry in self._entries if entry.visible)
        failed = sum(1 for entry in self._entries
                     if str(getattr(entry, "state", "")) in
                     ("failed", "unavailable", "offline", "no-data",
                      "canceled"))
        if not self._entries:
            text = "No layers."
        elif failed and visible:
            text = (f"{visible} of {len(self._entries)} layers visible · "
                    f"{failed} need attention")
        elif failed:
            text = f"{failed} layer(s) need attention"
        elif visible == 0:
            text = "All layers hidden."
        elif visible == len(self._entries):
            text = f"All {visible} layers visible."
        else:
            text = f"{visible} of {len(self._entries)} layers visible."
        self.summary.setText(text)
        self.summary.setAccessibleName("Active layer summary")
        self.summary.setVisible(not self._toolbar_only)
