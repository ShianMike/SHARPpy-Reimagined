"""Forms section of the application chrome stylesheet."""

from __future__ import annotations

from sharpmod.ui.styles.theme import (
    OBJ_CARD,
    OBJ_CARD_RULE,
    OBJ_CARD_TITLE,
    OBJ_CARD_TOGGLE,
    OBJ_DANGER,
    OBJ_GHOST,
    OBJ_MAP_TOOL,
    OBJ_PLAIN,
    OBJ_POINT_LOCK,
    OBJ_PRIMARY,
    PROP_COMPACT,
    WEIGHT
)


def qss_forms(t, s, h, r, font, ui, mono) -> str:
    return f"""/* --- Cards (replacing the notched QGroupBox) ----------------------- */

QFrame#{OBJ_CARD} {{
    background: {t.surface_raised};
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
}}

QLabel#{OBJ_CARD_TITLE} {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* A collapsible card's header (see ``CollapsibleRailSection``).
 *
 * This is a QToolButton because it has to be clickable and carry a chevron, but
 * it is a *heading* -- so it takes OBJ_CARD_TITLE's treatment rather than a
 * button's. It previously wore OBJ_GHOST, which is the borderless *action*
 * style: regular weight at body size, identical to a tertiary link. Five of
 * those stacked down the rail meant no card had a title that read as a title,
 * which is most of why the rail looked like a column of flat grey boxes.
 *
 * The border stays transparent rather than dropping to `border: 0`. Fusion
 * derives the arrow's rectangle from the button's frame, and a style sheet that
 * removes the frame outright takes the chevron with it -- so the disclosure
 * affordance would vanish. Only the bottom edge is painted, which turns the
 * header into a banded row and separates it from the controls below.
 *
 * Hover brightens the text instead of washing the row with `accent_subtle` as
 * OBJ_GHOST does: a full-width tint under a header rule reads as a selected
 * list item, not as a heading you can fold. */
QToolButton#{OBJ_CARD_TOGGLE} {{
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 1px solid {t.border};
    border-radius: 0;
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
    padding: 0 0 {s['xs']}px 0;
    text-align: left;
}}

QToolButton#{OBJ_CARD_TOGGLE}:hover {{
    color: {t.text_primary};
}}

QToolButton#{OBJ_CARD_TOGGLE}:focus {{
    color: {t.text_primary};
    border-bottom-color: {t.accent};
}}

/* A hairline separating two ideas inside one card -- currently the map overlays
 * from the sounding-locator overlays below them.
 *
 * Painted as a 1px block rather than declared as a border, because Qt draws a
 * QFrame's HLine shape from the palette through the style and ignores a style
 * sheet border on it. The owning code therefore sets ``NoFrame`` and lets this
 * rule do the drawing, which is also what puts the line on a token colour. */
QFrame#{OBJ_CARD_RULE} {{
    background: {t.border};
    border: 0;
    min-height: 1px;
    max-height: 1px;
}}

/* A bare container used only to group widgets. The base `QWidget` rule paints
 * the window surface, which is a different colour from a card's raised
 * surface, so an unnamed grouping widget inside a card drew a visible panel of
 * the wrong shade behind its children. The id selector applies to the
 * container alone and is not inherited by what it holds. */
QWidget#{OBJ_PLAIN} {{
    background: transparent;
}}

/* QGroupBox stays styled while panels are migrated to cards, so the app
 * is coherent at every commit rather than only at the end.
 *
 * The title is placed *inside* the border, as a card header. The default
 * `subcontrol-origin: margin` draws it in the margin band above the frame,
 * which reads as a detached floating label rather than a heading that belongs
 * to the panel. Top padding reserves the row the title occupies.
 *
 * That reserved row is `xxl` rather than `xxxl`: the title is one line of the
 * small font, so `xxxl` left a visible empty band under every heading. At eight
 * pixels per card that band was also the single largest avoidable cost in the
 * control rails, where the forecast panel stacks seven cards. */
QGroupBox {{
    background: {t.surface_raised};
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
    margin-top: 0;
    padding: {s['xxl']}px {s['md']}px {s['md']}px {s['md']}px;
    font-weight: {WEIGHT['semibold']};
}}

QGroupBox::title {{
    subcontrol-origin: border;
    subcontrol-position: top left;
    margin: {s['sm']}px 0 0 {s['md']}px;
    padding: 0;
    /* Explicitly transparent: without this the title sub-control picks up the
     * window `surface` from the QWidget rule and paints a mismatched strip
     * across the top of the card. */
    background: transparent;
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* --- Text and numeric inputs --------------------------------------- */

QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['sm']}px;
    min-height: {h['md']}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {t.accent};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {t.focus_ring};
}}

QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

/* Coordinates, forecast hours, and cycle numbers are figures: monospace
 * so digits share an advance width and columns align. */
QDoubleSpinBox, QSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    font-family: {mono};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['xs']}px 0 {s['sm']}px;
    min-height: {h['md']}px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QDoubleSpinBox:hover, QSpinBox:hover, QDateEdit:hover,
QTimeEdit:hover, QDateTimeEdit:hover {{
    border-color: {t.accent};
}}

QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus,
QTimeEdit:focus, QDateTimeEdit:focus {{
    border-color: {t.focus_ring};
}}

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    background: transparent;
    border: 0;
    width: {s['lg']}px;
}}

QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background: {t.accent_subtle};
    border-radius: {r['sm']}px;
}}

/* --- Combo boxes --------------------------------------------------- */

QComboBox {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['sm']}px;
    min-height: {h['md']}px;
}}

QComboBox:hover {{
    border-color: {t.accent};
}}

QComboBox:focus {{
    border-color: {t.focus_ring};
}}

QComboBox:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
}}

/* The drop-down sub-control is deliberately *not* restyled.
 *
 * Giving it any property makes Qt render that sub-control from the style sheet
 * instead of from the style, and a style sheet cannot draw the arrow without an
 * `image:` asset. The previous rule set only a border and a width, so the arrow
 * vanished and every combo box in the application looked exactly like a
 * read-only text field -- there was no way to tell "United States (CONUS)" or
 * "HRRR" was a menu. Leaving it alone lets Fusion paint a palette-aware arrow,
 * which matches the date edit's arrow beside it. */

/* The popup is a separate top-level window, so it needs its own
 * surface, border, and selection colours. */
QComboBox QAbstractItemView {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px;
    outline: 0;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

/* --- Buttons ------------------------------------------------------- */

QPushButton, QToolButton {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: 0 {s['md']}px;
    min-height: {h['md']}px;
    font-weight: {WEIGHT['medium']};
}}

QPushButton:hover, QToolButton:hover {{
    background: {t.surface_overlay};
    border-color: {t.accent};
}}

QPushButton:pressed, QToolButton:pressed {{
    background: {t.surface_sunken};
}}

QPushButton:focus, QToolButton:focus {{
    border-color: {t.focus_ring};
}}

QPushButton:disabled, QToolButton:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

/* The map tool row is a mode switcher rather than a row of unrelated actions.
 * Its active button needs the same persistent state cue as the toolbar modes. */
QToolButton#{OBJ_MAP_TOOL}:checked {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

QPushButton#{OBJ_POINT_LOCK}:checked {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

/* Exactly one accent button per panel. Previously every button shared
 * one colour, so nothing read as the primary action. */
QPushButton#{OBJ_PRIMARY} {{
    background: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-size: {font['subhead']}pt;
    font-weight: {WEIGHT['semibold']};
    min-height: {h['lg']}px;
}}

QPushButton#{OBJ_PRIMARY}:hover {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
}}

QPushButton#{OBJ_PRIMARY}:pressed {{
    background: {t.accent_pressed};
    border-color: {t.accent_pressed};
}}

QPushButton#{OBJ_PRIMARY}:disabled {{
    background: {t.surface};
    color: {t.text_disabled};
    border-color: {t.border};
}}

QPushButton#{OBJ_DANGER} {{
    background: {t.danger};
    color: {t.accent_text};
    border: 1px solid {t.danger};
    font-weight: {WEIGHT['semibold']};
}}

QPushButton#{OBJ_GHOST}, QToolButton#{OBJ_GHOST} {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.text_secondary};
    font-weight: {WEIGHT['regular']};
}}

QPushButton#{OBJ_GHOST}:hover, QToolButton#{OBJ_GHOST}:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* ID selectors outrank the generic button focus rule. Preserve a visible
 * keyboard boundary even on the borderless action variant. */
QPushButton#{OBJ_GHOST}:focus, QToolButton#{OBJ_GHOST}:focus {{
    border-color: {t.focus_ring};
}}

/* A square glyph tool button -- currently the rail's zoom steppers.
 *
 * The shared button rule pads every button by `md` on each side, which is right
 * for a worded action and wrong for a single "+" or minus sign: the two
 * steppers rendered as wide pills flanking "Reset", so a row that should read
 * as [-][+] Reset read as three mismatched slabs. Squaring them to the control
 * height puts the glyph in the middle of its own target.
 *
 * Pinned here rather than with ``setFixedWidth`` for the reason the OBJ_GHOST
 * variant below documents: QStyleSheetStyle recomputes a widget's size
 * constraints from the style sheet and overrides the programmatic size, so the
 * style sheet has to be the single source of truth. The ghost rule carries an id
 * selector and therefore still wins for dock close buttons. */
QToolButton[{PROP_COMPACT}="true"] {{
    min-width: {h['md']}px;
    max-width: {h['md']}px;
    padding: 0;
}}

"""
