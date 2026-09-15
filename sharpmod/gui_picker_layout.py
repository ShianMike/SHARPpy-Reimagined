"""Shared control-rail primitives for picker source modules."""

from __future__ import annotations

from datetime import datetime, timezone

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFrame,
    QGridLayout,
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
    DEPENDENT_INDENT,
    FIELD_W,
    OBJ_CARD,
    OBJ_CARD_TOGGLE,
    OBJ_ATTRIBUTION,
    OBJ_PLAIN,
    PROP_COMPACT,
    RAIL_W,
    SCROLLBAR_W,
    SPACE,
)

#: Display format for every date edit in the picker.
#:
#: The weekday leads because a run is chosen by day as much as by date -- "the
#: 12Z Sunday run" is how the thing is spoken about -- and because it is the
#: cheapest guard against an off-by-one date, which an ISO string alone gives no
#: way to notice. Shared so the four panels that own a date edit cannot drift into
#: showing it differently.
DATE_DISPLAY_FORMAT = "ddd  yyyy-MM-dd"

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


class CollapsibleRailSection(QFrame):
    """A compact card whose chevron hides controls without losing state."""

    def __init__(self, title: str, layout_type, parent=None):
        super().__init__(parent)
        self._title = str(title)
        self.setObjectName(OBJ_CARD)

        # Horizontal padding is `lg`, not `md`. The rail's usable width is
        # RAIL_W["max"] = 400px and its widest card (the forecast "Point" group)
        # needs 372px at `md`, so the extra 4px per side costs 380px of 400 and
        # buys the cards enough inset that their contents stop reading as
        # touching the border. ``test_no_control_rail_clips_its_widest_card``
        # is what keeps that budget honest if a card ever grows.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["lg"], SPACE["md"])
        outer.setSpacing(SPACE["sm"])

        # A heading that happens to be clickable, so it takes the card-title
        # treatment rather than a button's -- see QToolButton#cardToggle in
        # sharpmod.theme. It previously wore OBJ_GHOST, the borderless *action*
        # style, and so read as a tertiary link rather than as a section title.
        self.toggle = QToolButton(self)
        self.toggle.setObjectName(OBJ_CARD_TOGGLE)
        self.toggle.setText(self._title)
        self.toggle.setAccessibleName(f"{self._title} controls")
        self.toggle.setToolTip(f"Collapse {self._title.lower()} controls")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setArrowType(Qt.DownArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setAutoRaise(True)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.setMinimumHeight(CONTROL_H["md"])
        self.toggle.toggled.connect(self.set_expanded)
        outer.addWidget(self.toggle)

        # OBJ_PLAIN, or this holder repaints the *window* surface on top of the
        # card it sits in. The base `QWidget` style-sheet rule sets
        # `background: surface`, and a type selector matches every QWidget, so an
        # unnamed grouping widget is not neutral -- it is opaque and the wrong
        # shade. Every card was therefore drawing its raised fill only as a band
        # in the padding, with a window-coloured slab covering the whole content
        # area, which is why the rail read as flat boxes rather than as cards.
        # The id selector applies to this holder alone, not to what it holds.
        self.content = QWidget(self)
        self.content.setObjectName(OBJ_PLAIN)
        self._content_layout = layout_type(self.content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.content)

    def title(self) -> str:
        """Return the visible section title (QGroupBox compatibility)."""
        return self._title

    def content_layout(self):
        return self._content_layout

    def is_expanded(self) -> bool:
        return self.content.isVisibleTo(self)

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        verb = "Collapse" if expanded else "Expand"
        self.toggle.setToolTip(f"{verb} {self._title.lower()} controls")

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand from outside, chevron and tooltip included.

        Driven through the toggle rather than through :meth:`set_expanded` so the
        button's checked state, the arrow, and the tooltip cannot disagree with
        what the card is actually doing -- setting the content's visibility alone
        left a collapsed card wearing a down chevron that offered to collapse it.
        """
        self.toggle.setChecked(not bool(collapsed))


#: The order every control rail presents its cards in, as match tokens ranked
#: first to last.
#:
#: One list rather than a hand-built order per tab, because there are three rails
#: carrying overlapping sets of these cards and nothing kept them in step: the
#: same five cards appeared in three different orders, so moving between tabs
#: meant re-finding controls that had not changed.
#:
#: The sequence follows what a reader decides in what order. What am I looking at,
#: then which run of it, then what is drawn over that, and only then the framing:
#: how much of the map is on screen and where in it the sounding is taken.
RAIL_CARD_ORDER: tuple[str, ...] = (
    "source",
    "model",
    "panels",
    "run",
    "overlay",
    "region",
    "point",
)

#: Cards that arrive collapsed.
#:
#: Both are set once and then left: a region is chosen when the reader starts and
#: revisited rarely, and a point is normally picked by clicking the map rather than
#: by typing coordinates. Expanded, the two of them held roughly 200px of rail open
#: above the cards that *are* adjusted run to run, which on a laptop window pushed
#: the run controls below the fold.
RAIL_COLLAPSED_BY_DEFAULT: frozenset[str] = frozenset({"region", "point"})


def _rail_card_rank(section) -> int:
    """Rank one card by title, sorting anything unrecognised to the end."""
    title = section.title().casefold()
    for rank, token in enumerate(RAIL_CARD_ORDER):
        if token in title:
            return rank
    return len(RAIL_CARD_ORDER)


def _rail_card_is_collapsed_by_default(section) -> bool:
    title = section.title().casefold()
    return any(token in title for token in RAIL_COLLAPSED_BY_DEFAULT)


def order_rail_cards(layout) -> tuple[CollapsibleRailSection, ...]:
    """Put a rail's cards in :data:`RAIL_CARD_ORDER` and collapse the settings ones.

    Applied to the assembled rail rather than asked of each call site, so a new
    card is ordered by what it is called instead of by where it happened to be
    added, and the three rails cannot drift apart again.

    Only the cards move. Anything else in the rail -- an action row, a progress
    bar, the trailing stretch -- keeps its position, and the cards are placed back
    into the same slots they already occupied, so a rail whose cards are not
    contiguous is reordered within its own card slots and nothing else shifts.
    """
    positions = []
    sections = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if isinstance(widget, CollapsibleRailSection):
            positions.append(index)
            sections.append(widget)

    for section in sections:
        if _rail_card_is_collapsed_by_default(section):
            section.set_collapsed(True)

    # Stable, so cards sharing a rank -- and every unrecognised card -- keep the
    # order the builder added them in.
    ranked = sorted(sections, key=_rail_card_rank)
    if ranked == sections:
        return tuple(ranked)
    for section in sections:
        layout.removeWidget(section)
    # Ascending target indices, inserted in ascending order: each insertion shifts
    # everything after it by exactly one, which is what makes the next target
    # index land where it was measured.
    for target, section in zip(positions, ranked):
        layout.insertWidget(target, section)
    return tuple(ranked)


def dependent_panel(parent=None) -> tuple[QWidget, QVBoxLayout]:
    """Return an indented container for controls owned by the switch above it.

    The overlay card stacks six switches, most of which reveal settings when they
    are turned on. Flush against the left edge those settings read as six more
    peers in the list rather than as belonging to the switch that produced them --
    turning radar on appeared to add three unrelated combo boxes to the card.
    Indenting them by the indicator column (:data:`DEPENDENT_INDENT`) lines them
    up under their switch's label and makes the ownership readable without
    spending a box or a rule on it.

    The container itself stays visible; each child keeps whatever visibility rule
    its controller already applies. That keeps this purely a layout concern, and
    an empty container costs no height because its margins are zero on the axis
    that would show.

    ``OBJ_PLAIN`` is required, not optional: an unnamed grouping widget repaints
    the window surface over the card behind it.
    """
    panel = QWidget(parent)
    panel.setObjectName(OBJ_PLAIN)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(DEPENDENT_INDENT, 0, 0, 0)
    layout.setSpacing(SPACE["xs"])
    return panel, layout


def rail_card(title: str) -> tuple[CollapsibleRailSection, QVBoxLayout]:
    """Return one collapsible, consistently spaced stacked picker card."""
    box = CollapsibleRailSection(title, QVBoxLayout)
    layout = box.content_layout()
    layout.setSpacing(SPACE["sm"])
    return box, layout


def rail_form(title: str) -> tuple[CollapsibleRailSection, QGridLayout]:
    """Return one collapsible aligned label/field/action picker card."""
    box = CollapsibleRailSection(title, QGridLayout)
    grid = box.content_layout()
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

    # Only "Reset" is a worded action wanting the shared horizontal padding. Left
    # alone all three took it, so a single "+" sat in a button as wide as
    # "Reset" and the row read as three mismatched slabs rather than as a
    # stepper pair beside an action. See QToolButton[compact] in sharpmod.theme.
    for button in (zoom_out, zoom_in):
        button.setProperty(PROP_COMPACT, True)

    # Built by hand rather than in one loop so the steppers can sit tight
    # against each other and read as a single control, with a full gap before
    # the action that is not part of that pair.
    row = QHBoxLayout()
    row.setSpacing(0)
    row.addWidget(zoom_out)
    row.addSpacing(SPACE["xs"])
    row.addWidget(zoom_in)
    row.addSpacing(SPACE["md"])
    row.addWidget(reset)
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
    "DATE_DISPLAY_FORMAT",
    "TOWN_LOOKUP_TOOLTIP",
    "UTC_CLOCK_SAMPLE",
    "dependent_panel",
    "rail_card",
    "rail_form",
    "rail_row",
    "rail_zoom_row",
    "scrolling_control_rail",
    "set_button_busy",
    "town_lookup_attribution_label",
]
