"""Shared control-rail primitives for picker source modules."""

from __future__ import annotations

from datetime import datetime, timezone

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.theme import (
    CONTROL_H,
    FIELD_W,
    OBJ_ATTRIBUTION,
    RAIL_W,
    SCROLLBAR_W,
    SPACE,
)

TOWN_LOOKUP_TOOLTIP = (
    "When the location label is blank, CONUS locations are resolved "
    "locally from the bundled U.S. Census state and place index. Only "
    "when the offline index has no result is the configured Nominatim "
    "service tried, and the result is cached. Enter a label to skip "
    "automatic lookup."
)

# A widest-case instant for measuring the UTC clock label. Every field in the
# clock's format is fixed width, so one sample sizes them all.
UTC_CLOCK_SAMPLE = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

_IDLE_TEXT_PROPERTY = "sharpmodIdleText"


def town_lookup_attribution_label(parent=None) -> QLabel:
    """Return the compact Census/OpenStreetMap town-name credit."""
    label = QLabel(
        "Town names: "
        '<a href="https://www.census.gov/geographies/reference-files/'
        'time-series/geo/gazetteer-files.html">Census</a> / '
        '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        parent,
    )
    label.setOpenExternalLinks(True)
    label.setObjectName(OBJ_ATTRIBUTION)
    label.setWordWrap(True)
    label.setToolTip(TOWN_LOOKUP_TOOLTIP)
    return label


def scrolling_control_rail(
    layout: QLayout, *, content_width: int = RAIL_W["max"]
) -> QScrollArea:
    """Return a fixed usable rail plus a non-overlapping vertical scrollbar."""
    content = QWidget()
    content.setLayout(layout)
    content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
    content.setMinimumWidth(0)

    total_width = int(content_width) + SCROLLBAR_W
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setMinimumWidth(total_width)
    scroll.setMaximumWidth(total_width)
    scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    scroll.setWidget(content)
    return scroll


def rail_card(title: str) -> tuple[QGroupBox, QVBoxLayout]:
    """Return one consistently spaced stacked picker card."""
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE["sm"])
    return box, layout


def rail_form(title: str) -> tuple[QGroupBox, QGridLayout]:
    """Return one aligned label/field/action picker card."""
    box = QGroupBox(title)
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setVerticalSpacing(SPACE["sm"])
    grid.setHorizontalSpacing(SPACE["sm"])
    grid.setColumnMinimumWidth(0, FIELD_W["label"])
    grid.setColumnStretch(1, 1)
    return box, grid


def rail_row(
    grid: QGridLayout,
    row: int,
    label: str,
    field,
    *,
    trailing=None,
    width: str = "wide",
):
    """Place one ``label: field [action]`` row in a picker form."""
    grid.addWidget(QLabel(label), row, 0)
    field.setMinimumWidth(FIELD_W[width])
    field.setMinimumHeight(CONTROL_H["md"])
    if trailing is None:
        grid.addWidget(field, row, 1, 1, 2)
    else:
        grid.addWidget(field, row, 1)
        trailing.setMinimumWidth(FIELD_W["action"])
        trailing.setMinimumHeight(CONTROL_H["md"])
        grid.addWidget(trailing, row, 2)
    return field


def rail_zoom_row(map_widget) -> QHBoxLayout:
    """Return the shared zoom-out, zoom-in, and reset row."""
    row = QHBoxLayout()
    row.setSpacing(SPACE["sm"])
    zoom_out = QToolButton()
    zoom_out.setText("\u2212")
    zoom_out.setToolTip("Zoom out")
    zoom_out.clicked.connect(lambda: map_widget.zoom(1.25))
    zoom_in = QToolButton()
    zoom_in.setText("+")
    zoom_in.setToolTip("Zoom in")
    zoom_in.clicked.connect(lambda: map_widget.zoom(0.8))
    reset = QToolButton()
    reset.setText("Reset")
    reset.setToolTip("Reset the view to the selected region")
    reset.clicked.connect(lambda: map_widget.reset_view())
    for button in (zoom_out, zoom_in, reset):
        button.setMinimumHeight(CONTROL_H["md"])
        row.addWidget(button)
    row.addStretch(1)
    return row


def set_button_busy(button, busy: bool, busy_text: str) -> None:
    """Swap a button's idle and busy labels without duplicating literals."""
    if button is None:
        return
    if busy:
        if not button.property(_IDLE_TEXT_PROPERTY):
            button.setProperty(_IDLE_TEXT_PROPERTY, button.text())
        button.setEnabled(False)
        button.setText(busy_text)
        return
    idle = button.property(_IDLE_TEXT_PROPERTY)
    if idle:
        button.setText(idle)


__all__ = [
    "TOWN_LOOKUP_TOOLTIP",
    "UTC_CLOCK_SAMPLE",
    "rail_card",
    "rail_form",
    "rail_row",
    "rail_zoom_row",
    "scrolling_control_rail",
    "set_button_busy",
    "town_lookup_attribution_label",
]
