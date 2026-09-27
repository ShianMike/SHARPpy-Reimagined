"""Searchable named-region combo shared by every picker map (T17.2).

One helper rather than four hand-built combos so the station, forecast,
ERA5, and panel rails cannot drift apart on search, sizing, or the map
call they make. Typing filters the region list; choosing a name (by click,
Enter, or exact programmatic text) jumps that map to the region. Free text
that matches no region leaves the map alone instead of guessing.
"""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QComboBox, QCompleter

from sharpmod.ui.features.gui_maps import MAP_AREAS
from sharpmod.ui.styles.theme import CONTROL_H


def region_combo(map_widget, *, parent=None,
                 on_changed=None) -> QComboBox:
    """Return a searchable region combo bound to ``map_widget``.

    ``on_changed`` optionally replaces the default ``map_widget.set_area``
    call (the forecast tab needs to repopulate its model list as well).
    The combo stays non-editable-looking but searchable: ``setEditable(True)``
    with ``NoInsert`` so typed text completes without ever adding entries.
    """
    combo = QComboBox(parent)
    for name in MAP_AREAS:
        combo.addItem(name)
    combo.setMinimumHeight(CONTROL_H["md"])
    combo.setMinimumWidth(120)
    combo.setToolTip(
        "Jump the map to a named region. Type to search region names.")
    combo.setAccessibleName("Map region")
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    completer = combo.completer()
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setFilterMode(Qt.MatchContains)
    if on_changed is None:
        def on_changed(name: str) -> None:
            setter = getattr(map_widget, "set_area", None)
            if callable(setter):
                try:
                    setter(name, push_history=True)
                except TypeError:
                    setter(name)
    combo.currentTextChanged.connect(on_changed)
    combo.setCurrentText(getattr(map_widget, "_area_name", "") or "")
    return combo
