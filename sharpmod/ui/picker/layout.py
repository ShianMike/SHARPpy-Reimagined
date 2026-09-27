"""Shared control-rail primitives for picker source modules."""

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

#: The most of the top bar the active source's selection summary may occupy,
#: measured in average characters of the clock's monospaced face. The summary only
#: ever takes space the menus, the source control, and the clock did not need, and
#: this caps it even when a wide window would allow more, so the bar's right end
#: stays a fixed shape rather than growing and shrinking with the place name.
TOP_SUMMARY_CHARS = 64

#: Below this many characters the summary is a fragment rather than information,
#: so it is dropped entirely instead of eliding down to nothing. The full text
#: remains on the tooltip and in the accessible description.
TOP_SUMMARY_MIN_CHARS = 16

#: Slack kept between the summary's budget and the width at which the responsive
#: header stops fitting its corner widgets on the menu row. The summary is the one
#: optional thing in that row, so it absorbs the uncertainty rather than letting a
#: few pixels of style-sheet padding restack the entire bar.
TOP_SUMMARY_HEADROOM = SPACE["lg"] * 2

#: How a selection summary joins its parts, and how it is taken apart again to be
#: shortened. Produced by ``SelectionFeedback`` in ``gui_picker_selection``.
SUMMARY_JOIN = " \u00b7 "


def fit_summary_groups(text, metrics, width):
    """Shorten a middle-dot separated summary by dropping whole leading groups.

    Character-level eliding severs a label from its value. The picker's summary
    ends "Initialization 2026-09-17 06:00 UTC / Lead +0 h / Valid 2026-09-17 06:00
    UTC", and a plain left elide produced a bare "2026-09-17 06:00 UTC" sitting
    immediately before a labelled valid time, which reads as a second valid time.
    Dropping entire groups keeps every value that is still shown attached to its
    own label, and the leading ellipsis says earlier groups were removed.

    The last group is character-elided only as a final resort, when even one group
    will not fit. The untruncated line always remains on the tooltip.
    """
    text = str(text or "").strip()
    if not text or width <= 0:
        return ""
    if metrics.horizontalAdvance(text) <= width:
        return text
    groups = [group for group in text.split(SUMMARY_JOIN) if group]
    for start in range(1, len(groups)):
        candidate = "\u2026 " + SUMMARY_JOIN.join(groups[start:])
        if metrics.horizontalAdvance(candidate) <= width:
            return candidate
    return metrics.elidedText(groups[-1] if groups else text, Qt.ElideLeft, width)

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
    """Return a content-sized rail with a reserved vertical-scrollbar gutter.

    The normal width stays unchanged. Larger chrome text or expanded controls
    can raise the minimum, so disabling horizontal scrolling never clips them.
    The native selection pane can resize it above that minimum or hide it.
    """
    content = QWidget()
    content.setLayout(layout)
    content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
    content.setMinimumWidth(0)

    scroll = _ControlRail(content_width)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    scroll.setWidget(content)
    content.installEventFilter(scroll)
    scroll._sync_content_width()
    return scroll


class _ControlRail(QScrollArea):
    """Track Qt's minimum content size after live theme/layout changes."""

    contentWidthChanged = Signal(int)

    def __init__(self, content_width):
        super().__init__()
        self._content_width = int(content_width)
        self._width_timer = QTimer(self)
        self._width_timer.setSingleShot(True)
        self._width_timer.timeout.connect(self._sync_content_width)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() in {
            QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange, QEvent.Show,
        }:
            # Let Qt invalidate child size hints before asking for the new
            # minimum. Coalesce events from the many controls in each rail.
            self._width_timer.start(0)
        return super().eventFilter(watched, event)

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._width_timer.start(0)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.StyleChange}:
            self._width_timer.start(0)

    def _sync_content_width(self):
        content = self.widget()
        needed = content.minimumSizeHint().width() if content is not None else 0
        width = max(self._content_width, needed) + SCROLLBAR_W
        if self.minimumWidth() != width:
            self.setMinimumWidth(width)
            self.contentWidthChanged.emit(width)

    def sizeHint(self):  # noqa: N802 - Qt override
        return QSize(self.minimumWidth(), super().sizeHint().height())


class SelectionPane(QWidget):
    """Remembered native rail/map arrangement, independent of source state.

    The action area deliberately sits outside the splitter/scroll area, so hiding
    configuration cannot hide active jobs.

    Two pieces of chrome that used to sit in this pane's own header now live in
    the window's top bar: the rail toggle is the View menu's "Hide controls" item,
    and the selection summary sits beside the UTC clock. Both objects are still
    created and owned here, because this pane is what knows the per-source
    collapse memory and what computes the summary text; they are simply not drawn
    here any more. :attr:`collapsedChanged` and :attr:`summaryChanged` are how the
    window mirrors them, so the pane never has to know what presents them.
    """

    #: Emitted with ``True`` when the configuration rail becomes hidden.
    collapsedChanged = Signal(bool)
    #: Emitted with the current selection summary whenever it is recomputed.
    summaryChanged = Signal(str)

    def __init__(self, rail, content, *, settings=None, key, title, parent=None):
        super().__init__(parent)
        self.setObjectName(OBJ_PLAIN)
        # Accepts focus programmatically but is not a Tab stop: when the rail is
        # hidden with focus inside it, focus has to land somewhere real rather
        # than on the now-hidden toggle or nowhere at all.
        self.setFocusPolicy(Qt.ClickFocus)
        self.rail = rail
        self._settings = settings
        self._settings_prefix = f"picker/rail/{key}"
        self._preferred_width = None
        self._restored = False
        if settings is not None:
            try:
                stored = int(settings.value(f"{self._settings_prefix}/width", 0))
            except (TypeError, ValueError, OverflowError):
                stored = 0
            if 0 < stored <= 10000:
                self._preferred_width = stored
            collapsed = str(settings.value(
                f"{self._settings_prefix}/collapsed", "false"
            )).casefold() in {"true", "1", "yes"}
        else:
            collapsed = False
        self._title = title
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        self.header = QHBoxLayout()
        # Not added to the header layout and never shown. It remains the single
        # holder of this pane's collapse state, its Hide/Show wording, and its
        # accessible name, so the View menu item is a mirror rather than a second
        # source of truth that could disagree with the persisted setting.
        self.toggle = QToolButton(self)
        self.toggle.setObjectName("pickerRailToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.hide()
        self.header.addStretch(1)
        outer.addLayout(self.header)
        # Likewise retained but not drawn here: the text is presented beside the
        # top bar's clock. Keeping the label means the summary, its accessible
        # description, and its tooltip are still computed and owned in one place.
        self.summary = QLabel(self)
        self.summary.setObjectName("pickerSelectionSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName(f"{title} selection summary")
        self.summary.hide()
        # Stays in the layout as a fallback. The top bar is the intended home, but
        # at large text scales the menu row genuinely cannot spare the width, and
        # hiding configuration must never hide the selection context. The window
        # turns this back on through :meth:`set_summary_inline` when it cannot
        # present the summary itself.
        self._summary_inline = False
        outer.addWidget(self.summary)
        from sharpmod.ui.features.gui_common import make_status_label

        self.feedback = make_status_label("", parent=self)
        self.feedback.setAccessibleName(f"{title} selection guidance")
        self.feedback.hide()
        self._feedback_state = None
        outer.addWidget(self.feedback)
        self.corrective = QPushButton(self)
        self.corrective.setObjectName("pickerSelectionCorrection")
        self.corrective.hide()
        self._corrective_action = None
        self.corrective.clicked.connect(self._correct_selection)
        self._guidance_layout = None
        # Index 0 now that the toggle no longer occupies it; the stretch follows.
        self.header.insertWidget(0, self.corrective)
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName("pickerSelectionSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(SPACE["sm"])
        self.splitter.addWidget(rail)
        self.splitter.addWidget(content)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        handle = self.splitter.handle(1)
        handle.setAccessibleName(f"Resize {title} selection rail")
        handle.setToolTip("Drag to resize selection controls; use Hide controls for more map space")
        outer.addWidget(self.splitter, 1)
        self.actions = QVBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(SPACE["xs"])
        outer.addLayout(self.actions)
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_arrangement)
        rail.contentWidthChanged.connect(self._queue_restore)
        self.splitter.splitterMoved.connect(self._remember_width)
        self.toggle.toggled.connect(self._set_configuration_visible)
        self._set_configuration_visible(not collapsed, persist=False)

    def embed_guidance(self, layout, *, index=None):
        """Place corrective guidance inside the rail card that owns it.

        Most sources need a page-level correction because their invalid field
        can live in any one of several cards.  A station-map correction has one
        unambiguous subject, though: the selected station.  Reparenting the
        existing widgets preserves their actions, accessibility, and feedback
        state while avoiding a loose warning strip above the map.

        ``index`` lets the host put the guidance directly after its selection
        label and before lower-priority availability detail.
        """
        if layout is self._guidance_layout:
            return
        if self._guidance_layout is not None:
            self._guidance_layout.removeWidget(self.feedback)
            self._guidance_layout.removeWidget(self.corrective)
        else:
            self.layout().removeWidget(self.feedback)
            self.header.removeWidget(self.corrective)
            # The header only existed to carry this action.  Leaving its lone
            # stretch in the page layout would retain an unexplained top gap.
            self.layout().removeItem(self.header)
        self.corrective.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if index is None:
            layout.addWidget(self.feedback)
            layout.addWidget(self.corrective)
        else:
            layout.insertWidget(int(index), self.feedback)
            layout.insertWidget(int(index) + 1, self.corrective)
        self._guidance_layout = layout

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._restore_timer.start(0)

    def _queue_restore(self, *_args):
        self._restore_timer.start(0)

    def _restore_arrangement(self):
        if self.rail.isHidden() or not self.isVisible():
            return
        available = self.splitter.width() - self.splitter.handleWidth()
        if available <= 0:
            return
        content = self.splitter.widget(1)
        content_minimum = max(content.minimumWidth(), content.minimumSizeHint().width(), 0)
        desired = max(
            self.rail.minimumWidth(),
            min(self._preferred_width or self.rail.minimumWidth(),
                max(available - content_minimum, 0)),
        )
        self.splitter.setSizes([desired, max(available - desired, 0)])
        self._restored = True

    def _remember_width(self, *_args):
        if self.rail.isHidden() or not self._restored:
            return
        width = self.splitter.sizes()[0]
        if width > 0:
            self._preferred_width = width
            if self._settings is not None:
                self._settings.setValue(f"{self._settings_prefix}/width", width)
                self._settings.sync()

    def _set_configuration_visible(self, expanded, *, persist=True):
        expanded = bool(expanded)
        if not expanded:
            self._remember_width()
            focus = self.focusWidget()
            if focus is not None and self.rail.isAncestorOf(focus):
                # The rail is about to disappear with focus inside it. The toggle
                # used to catch it, but it lives in the View menu now, so hand
                # focus to the content that just gained the space. Focusing the
                # hidden toggle would silently drop focus to nothing.
                target = self.splitter.widget(1)
                if target is None or target.focusPolicy() == Qt.NoFocus:
                    target = self
                target.setFocus(Qt.OtherFocusReason)
        self.rail.setVisible(expanded)
        verb = "Hide" if expanded else "Show"
        self.toggle.setText(f"{verb} controls")
        self.toggle.setArrowType(Qt.LeftArrow if expanded else Qt.RightArrow)
        self.toggle.setAccessibleName(f"{verb} {self._title} selection controls")
        self.toggle.setToolTip(
            f"{verb} configuration without changing the selection or active request"
        )
        if persist and self._settings is not None:
            self._settings.setValue(f"{self._settings_prefix}/collapsed", not expanded)
            self._settings.sync()
        if expanded:
            self._restore_timer.start(0)
        self.collapsedChanged.emit(not expanded)

    def is_collapsed(self):
        """Whether the configuration rail is currently hidden."""
        return not self.toggle.isChecked()

    def set_summary_inline(self, enabled):
        """Present the summary in this pane because the top bar cannot."""
        self._summary_inline = bool(enabled)
        self._update_summary_visibility()

    def summary_is_inline(self):
        """Whether this pane is currently presenting its own summary."""
        return self._summary_inline

    def _update_summary_visibility(self):
        self.summary.setVisible(self._summary_inline and bool(self.summary.text()))

    def summary_text(self):
        """Return the current selection summary, whoever is presenting it."""
        return self.summary.text()

    def set_collapsed(self, collapsed):
        self.toggle.setChecked(not bool(collapsed))

    def set_feedback(self, summary, message="", *, level="info",
                     corrective_label="", corrective_action=None):
        from sharpmod.ui.features.gui_common import set_status_label

        if self.summary.text() != summary:
            self.summary.setText(summary)
            self.summary.setAccessibleDescription(summary)
            self.summary.setToolTip(summary)
            # The top bar measures and lays out this line. Only notify it when
            # the context actually changes, even if several controls report
            # the same state during one selection update.
            self._update_summary_visibility()
            self.summaryChanged.emit(summary)
        feedback_state = (message, level)
        if self._feedback_state != feedback_state:
            set_status_label(self.feedback, message, level=level)
            self._feedback_state = feedback_state
        if self.feedback.isHidden() == bool(message):
            self.feedback.setVisible(bool(message))
        self._corrective_action = corrective_action
        if self.corrective.text() != corrective_label:
            self.corrective.setText(corrective_label)
            self.corrective.setAccessibleName(corrective_label)
        show_corrective = bool(corrective_label and corrective_action)
        if self.corrective.isHidden() == show_corrective:
            self.corrective.setVisible(show_corrective)

    def _correct_selection(self):
        if self._corrective_action is not None:
            self._corrective_action()

    def reveal_field(self, field):
        """Reveal the actual existing control; do not choose a value for it."""
        self.set_collapsed(False)
        ancestor = field.parentWidget()
        while ancestor is not None and ancestor is not self.rail:
            if isinstance(ancestor, CollapsibleRailSection):
                ancestor.set_collapsed(False)
            ancestor = ancestor.parentWidget()
        self.rail.widget().layout().activate()
        self.rail.ensureWidgetVisible(field)
        field.setFocus()


def selection_pane(rail, content, *, settings=None, key, title, parent=None):
    """Use the same native selection arrangement for each picker source."""
    return SelectionPane(rail, content, settings=settings, key=key, title=title,
                         parent=parent)


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
        # sharpmod.ui.styles.theme. It previously wore OBJ_GHOST, the borderless *action*
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
        self.summary = QLabel(self)
        self.summary.setObjectName("pickerSectionSummary")
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        self.summary.hide()
        outer.addWidget(self.summary)

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
        self.summary.setVisible(not expanded and bool(self.summary.text()))
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        verb = "Collapse" if expanded else "Expand"
        detail = f" — {self.summary.text()}" if self.summary.text() else ""
        self.toggle.setToolTip(f"{verb} {self._title.lower()} controls{detail}")

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand from outside, chevron and tooltip included.

        Driven through the toggle rather than through :meth:`set_expanded` so the
        button's checked state, the arrow, and the tooltip cannot disagree with
        what the card is actually doing -- setting the content's visibility alone
        left a collapsed card wearing a down chevron that offered to collapse it.
        """
        self.toggle.setChecked(not bool(collapsed))

    def set_summary(self, text):
        text = str(text)
        if self.summary.text() == text:
            return
        self.summary.setText(text)
        self.summary.setAccessibleName(f"{self._title} summary")
        self.toggle.setAccessibleDescription(str(text))
        self.toggle.setToolTip(
            f"{'Collapse' if self.toggle.isChecked() else 'Expand'} "
            f"{self._title.lower()} controls — {text}" if text else
            f"{'Collapse' if self.toggle.isChecked() else 'Expand'} "
            f"{self._title.lower()} controls")
        self.summary.setVisible(not self.toggle.isChecked() and bool(text))


#: The order every control rail presents its cards in, as match tokens ranked
#: first to last.
#:
#: One list rather than a hand-built order per tab, because there are three rails
#: carrying overlapping sets of these cards and nothing kept them in step: the
#: same five cards appeared in three different orders, so moving between tabs
#: meant re-finding controls that had not changed.
#:
#: The sequence follows the workflow: choose the source and run, set the map
#: layers, then set the map location and framing controls.
RAIL_CARD_ORDER: tuple[str, ...] = (
    "sounding setup",
    "source",
    "model",
    "field setup",
    "panels",
    "run",
    "selected station",
    "selected area",
    "map layer",
    "map location",
    "region",
    "point",
)

#: Cards that arrive collapsed.
#:
#: Standalone Region and Point cards remain folded by default. The combined Map
#: location card stays open because choosing the point is the main map workflow.
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


def map_tool_row(map_widget, *, allow_box: bool = True) -> QHBoxLayout:
    """Return the shared explicit tool switcher row (T18.1).

    Select places the sounding point, Inspect reads values without moving
    anything, and Draw box (point maps only) arms sticky box drawing -- one
    state with ``set_box_mode``, so the rail and the map cannot disagree.
    Buttons stay checked with the widget's active tool through a repaint hook
    shared with :func:`rail_zoom_row`; tool keys (V/I/B) live on the map with
    focus, never as global grabs, so typing in a text field is unaffected.
    """
    group = QButtonGroup(map_widget)
    group.setExclusive(True)

    def _tool_button(text, name, tip, key, tool):
        button = QToolButton()
        button.setObjectName(OBJ_MAP_TOOL)
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(f"{tip} ({key} with map focus)")
        button.setAccessibleName(name)
        button.clicked.connect(lambda _checked=False: map_widget.set_map_tool(tool))
        button.setMinimumHeight(CONTROL_H["md"])
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setFocusPolicy(Qt.StrongFocus)
        button.setProperty("sharpmodMapTool", tool)
        group.addButton(button)
        return button

    tools = [("Select", "Map select tool",
              "Place the sounding point; double-click fetches", "V", "select"),
             ("Inspect", "Map inspect tool",
              "Read values and pin cards without moving the point", "I", "inspect")]
    if allow_box:
        tools.append(("Box", "Map draw-box tool",
                      "Drag a rectangle; a click still places the point", "B", "box"))
    row = QHBoxLayout()
    row.setSpacing(SPACE["xs"])
    buttons = [_tool_button(*spec) for spec in tools]
    for button in buttons:
        row.addWidget(button, 1)

    def _refresh_tool(*_args):
        try:
            active = map_widget.map_tool()
        except (AttributeError, RuntimeError):
            return
        for button in buttons:
            button.setChecked(button.property("sharpmodMapTool") == active)

    _refresh_tool()
    _install_repaint_refresh(map_widget, _refresh_tool)
    row._sharpmod_refresh_tool = _refresh_tool  # type: ignore[attr-defined]
    return row


def _install_repaint_refresh(map_widget, refresh) -> None:
    """Refresh rail rows on every map repaint, best-effort (T17.2/T18.1).

    One repaint hook serves every row: each caller registers its own refresh
    and the shared wrapper runs them all, so adding the tool row beside the
    zoom row cannot orphan either one.
    """
    try:
        existing = getattr(map_widget, "_sharpmod_repaint_refreshes", None)
    except Exception:  # noqa: BLE001 - optional refresh
        return
    if existing is None:
        existing = []
        try:
            map_widget._sharpmod_repaint_refreshes = existing  # type: ignore[attr-defined]
        except (AttributeError, TypeError):  # noqa: BLE001 - optional refresh
            return
    existing.append(refresh)
    try:
        repaint = map_widget.repaint
    except AttributeError:  # pragma: no cover - every map is a QWidget
        return
    if not callable(repaint) or getattr(
            repaint, "_sharpmod_refresh_wrapped", False):
        return

    def _repaint_and_refresh(*args, **kwargs):
        try:
            return repaint(*args, **kwargs)
        finally:
            for callback in list(
                    getattr(map_widget, "_sharpmod_repaint_refreshes", ())):
                try:
                    callback()
                except RuntimeError:  # pragma: no cover - map destroyed
                    pass

    _repaint_and_refresh._sharpmod_refresh_wrapped = True  # type: ignore[attr-defined]
    try:
        map_widget.repaint = _repaint_and_refresh  # type: ignore[assignment]
    except (AttributeError, TypeError):  # noqa: BLE001 - optional refresh
        pass


def rail_zoom_row(map_widget) -> QHBoxLayout:
    """Return the shared map-navigation row (T17.2).

    Zoom steppers, fit-to-region, center-on-selection, and previous/next view
    round out the T17 navigation contract. History buttons enable only while
    their stack is non-empty, and the row refreshes them on every map repaint
    so the enabled state cannot go stale after keyboard or wheel navigation.
    Shortcuts are display strings plus map-focus key handling in the widget,
    never global grabs, so typing in a text field is unaffected.
    """
    from qtpy.QtGui import QAction

    def _action_button(text, name, tip, shortcut, triggered):
        button = QToolButton()
        button.setText(text)
        button.setToolTip(f"{tip} ({shortcut})" if shortcut else tip)
        button.setAccessibleName(name)
        button.clicked.connect(triggered)
        button.setMinimumHeight(CONTROL_H["md"])
        button.setFocusPolicy(Qt.StrongFocus)
        return button

    zoom_out = _action_button(
        "\u2212", "Zoom map out", "Zoom out from the map centre", "Ctrl+-",
        lambda: map_widget.zoom(1.25))
    zoom_in = _action_button(
        "+", "Zoom map in", "Zoom in at the map centre", "Ctrl++",
        lambda: map_widget.zoom(0.8))
    fit = _action_button(
        "Fit", "Fit map to region", "Return to the region's extent", "Ctrl+0",
        lambda: map_widget.fit_region()
        if hasattr(map_widget, "fit_region") else map_widget.reset_view())
    center = _action_button(
        "Center", "Center map on selection",
        "Center on the selected sounding point or station", "L",
        lambda: map_widget.center_on_selection()
        if hasattr(map_widget, "center_on_selection") else None)
    # ``<``/``>`` read as history chevrons beside the Fit/Center words; the
    # tooltip carries the Alt+Arrow shortcut actually handled with map focus.
    prev = _action_button(
        "\u2039", "Previous map view", "Restore the previous view",
        "Alt+Left",
        lambda: map_widget.go_back() if hasattr(map_widget, "go_back") else None)
    next_ = _action_button(
        "\u203a", "Next map view", "Redo the undone view", "Alt+Right",
        lambda: map_widget.go_forward()
        if hasattr(map_widget, "go_forward") else None)

    # Only "Reset" is a worded action wanting the shared horizontal padding. Left
    # alone all three took it, so a single "+" sat in a button as wide as
    # "Reset" and the row read as three mismatched slabs rather than as a
    # stepper pair beside an action. See QToolButton[compact] in sharpmod.ui.styles.theme.
    for button in (zoom_out, zoom_in, prev, next_):
        button.setProperty(PROP_COMPACT, True)

    for button, sequence in (
        (zoom_in, "Ctrl++"), (zoom_out, "Ctrl+-"), (fit, "Ctrl+0"),
    ):
        action = QAction(button)
        action.setShortcut(sequence)
        action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        action.triggered.connect(button.click)
        button.addAction(action)

    def _refresh_history(*_args):
        can_back = bool(map_widget.can_go_back()) \
            if hasattr(map_widget, "can_go_back") else False
        can_forward = bool(map_widget.can_go_forward()) \
            if hasattr(map_widget, "can_go_forward") else False
        prev.setEnabled(can_back)
        next_.setEnabled(can_forward)

    _refresh_history()
    try:
        map_widget.viewSettled.connect(_refresh_history)
    except Exception:  # noqa: BLE001 - history buttons simply stay enabled
        pass
    # Wheel, keyboard, and programmatic navigation do not emit viewSettled
    # until the preview settles, so also refresh on repaint: cheap (two bool
    # reads) and the only way the buttons track keyboard zoom live. Shared
    # with the tool row so both rows survive on one repaint hook.
    _install_repaint_refresh(map_widget, _refresh_history)

    # Built by hand rather than in one loop so the steppers can sit tight
    # against each other and read as a single control, with a full gap before
    # the action that is not part of that pair.
    row = QHBoxLayout()
    row.setSpacing(0)
    row.addWidget(zoom_out)
    row.addSpacing(SPACE["xs"])
    row.addWidget(zoom_in)
    row.addSpacing(SPACE["md"])
    row.addWidget(fit)
    row.addSpacing(SPACE["xs"])
    row.addWidget(center)
    row.addSpacing(SPACE["md"])
    row.addWidget(prev)
    row.addSpacing(SPACE["xs"])
    row.addWidget(next_)
    row.addStretch(1)
    row._sharpmod_refresh_navigation = _refresh_history  # type: ignore[attr-defined]
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


from sharpmod.ui.picker.layer_list import ActiveLayerList  # noqa: E402
