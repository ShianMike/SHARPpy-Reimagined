"""Menus section of the application chrome stylesheet."""

from __future__ import annotations

from sharpmod.ui.styles.theme import (
    OBJ_TOP_BAR,
    OBJ_TOP_BAR_END,
    OBJ_TOP_BAR_LABEL,
    OBJ_TOP_BAR_MENU,
    OBJ_TOP_BAR_SOURCE,
    OBJ_UTC_CLOCK,
    WEIGHT
)


def qss_menus(t, s, h, r, font, ui, mono) -> str:
    return f"""/* --- Top bar and menus -------------------------------------------- */

/* The menu bar doubles as the application's top bar: the picker installs the
 * source dropdown as its top-left corner widget and the UTC clock as its
 * top-right one. So this rule sets the height and rhythm of the whole strip,
 * not just of the four menu titles.
 *
 * The vertical padding must stay at `xxs`. ``gui_viewer._enlarge_canvas``
 * derives the sounding canvas height from ``menuBar().sizeHint().height()``, and
 * that composed geometry is a contract: ``sharpmod-render``'s PNG output shares
 * this code path and is required to stay byte-identical
 * (``test_composed_canvas_matches_the_documented_geometry`` pins it at
 * 1630x1091). Raising this to `xs` made every menu bar 4px taller and moved the
 * rendered PNG. The picker's top bar gets its breathing room from the vertical
 * margins on its own corner widget instead, which only affects the window that
 * actually has one. Horizontal padding is free -- the canvas budget reads the
 * height alone. */
QMenuBar {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border-bottom: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    spacing: {s['xxs']}px;
}}

/* Secondary, not primary. The menus are the least-used group in the bar -- the
 * source dropdown beside them is the control that actually drives the window --
 * but they inherited `text_primary` from the rule above and so carried exactly
 * as much weight as it did. */
QMenuBar::item {{
    background: transparent;
    color: {t.text_secondary};
    padding: {s['xs']}px {s['md']}px;
    border-radius: {r['md']}px;
    font-weight: {WEIGHT['medium']};
}}

QMenuBar::item:selected {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

QMenuBar::item:pressed {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* The source cluster. Transparent, so it reads as part of the bar rather than
 * as a panel floating in it, with a hairline on its right edge separating it
 * from the menus. Without that divider the eyebrow, the dropdown, and "File"
 * ran together as one row of same-sized text.
 *
 * The shared height floor is what makes this divider and the one on
 * QFrame#topBarEnd the same length. Each frame sits above different content -- a
 * 25px dropdown at one end, a line of caption text at the other -- so without a
 * common floor the two rules came out 33px and 18px. `sm` clears the taller of
 * the two, so both frames resolve to exactly it. Their children are added centred
 * rather than filled, or the layout would grow them to meet it. */
QFrame#{OBJ_TOP_BAR} {{
    background: transparent;
    border: 0;
    border-right: 1px solid {t.border};
    min-height: {h['sm']}px;
}}

/* An eyebrow, not a heading: it names the control beside it. Previously this
 * wore OBJ_EMPHASIS -- semibold `text_primary` at body size -- which is the
 * treatment for a resolved *value*, so it competed with both the dropdown it
 * labels and the menu titles next to it. */
QLabel#{OBJ_TOP_BAR_LABEL} {{
    color: {t.text_tertiary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* Same material as the menu items beside it and as the sounding window's View
 * toolbar: transparent and borderless at rest, accent-tinted on hover, matching
 * radius, padding, height, and weight.
 *
 * It previously took the shared input treatment -- a `surface_sunken` fill inside
 * a `border_strong` outline, 32px tall -- so the one control in the top bar was
 * the only boxed thing in a strip of borderless text, and read as a form field
 * dropped into a menu rather than as part of the bar.
 *
 * `min-height: 0` is load-bearing, and is why the strip is short. Omitting it does
 * not mean "no floor": the cascade falls back to the shared QComboBox rule, which
 * declares the 32px control height, and padding then stacks on top of that -- so
 * the control rendered 42px tall and forced the bar to 50px, which is twice what
 * the four menu titles beside it need. The floor has to be actively cancelled.
 *
 * The vertical padding is `xxs` where QMenuBar::item uses `xs`, because the two
 * are measured differently and this rule matches the *result* rather than the
 * declaration: Fusion adds its own metrics to a menu-bar item on top of the style
 * sheet's padding, while for a combo box the style sheet is the whole story. At
 * `xs` this came out 29px against the menu titles' 25px; at `xxs` the two sit on
 * one line. Everything else here is QMenuBar::item's own value.
 *
 * The result is under the 28px pointer target CONTROL_H documents. Accepted
 * knowingly: this is a menu-bar entry, and holding that floor is exactly what
 * made it the one item in the strip that did not sit with the others.
 *
 * No `min-width`. The width has to be stable or the menus slide sideways when the
 * source changes, but ``SourceSelector`` already guarantees that with
 * ``setMinimumContentsLength(18)`` -- one more than the longest entry
 * ("Reanalysis (ERA5)"), so every entry clamps to the same floor. */
QComboBox#{OBJ_TOP_BAR_SOURCE} {{
    min-height: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['md']}px;
    padding: {s['xxs']}px {s['md']}px;
    color: {t.text_secondary};
    font-weight: {WEIGHT['medium']};
}}

/* Drop the arrow. This is the one place ::drop-down *should* be styled, and it is
 * the exact inverse of the warning on the shared QComboBox rule above: touching
 * this sub-control moves it from the style to the style sheet, and a style sheet
 * cannot draw an arrow without an image asset -- so the arrow disappears. There
 * that was the bug; here it is the entire point.
 *
 * A permanent arrow is what still marked this out as a form control among the
 * menu titles: File, Locations, View and Help all open menus and none of them
 * advertises it. The list appears on click, exactly as theirs do. Collapsing the
 * sub-control to zero width also returns its column to the text. */
QComboBox#{OBJ_TOP_BAR_SOURCE}::drop-down {{
    width: 0;
    border: 0;
    background: transparent;
}}

/* QMenuBar::item:selected, again matched declaration for declaration -- a menu
 * title lifts to `text_primary` on a tinted ground, and so does this. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:hover {{
    background: {t.accent_subtle};
    border-color: {t.accent_subtle};
    color: {t.text_primary};
}}

/* Latched while its popup is open, mirroring the View toolbar's :checked state
 * so "this menu is showing" reads identically in both windows. Without it the
 * control looks untouched while its list is down, because the transparent rest
 * state gives Fusion nothing to press. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:on {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
}}

/* Keyboard focus has to stay visible now that the resting border is invisible:
 * this is the window's primary navigation control, and it is first in the tab
 * order. */
QComboBox#{OBJ_TOP_BAR_SOURCE}:focus {{
    border-color: {t.focus_ring};
}}

/* ...and so is its popup.
 *
 * Matching the field to the menu items beside it only got halfway: clicking it
 * still opened a form-field list, on a tighter radius with compact rows, so the
 * control read as a menu right up until it was used. These are QMenu's own
 * values, duplicated for the same reason QMenuBar::item's are on the field -- a
 * sub-control and a widget cannot share one rule.
 *
 * Selected by an object name on the *view*, not as ``QComboBox#topBarSource
 * QAbstractItemView``. That descendant form is accepted and silently matches
 * nothing: a combo's popup lives in its own top-level window, so the view is not
 * a style-sheet descendant of the combo even though it is a child of it in Qt's
 * object tree. The measured row height stayed at the shared list value.
 *
 * The name is now set on *every* combo popup in the application, by
 * ``sharpmod.ui.features.gui_common.install_popup_placement``, so a dropdown anywhere opens
 * with the same metrics and the same relationship to its field as the menus in
 * the top bar. It was restricted to this one view while the height was unbounded,
 * because the forecast-hour list runs to 209 entries and menu-height rows made a
 * popup taller than the screen. :data:`POPUP_ROWS` caps it, so a long list now
 * scrolls the way a long menu does. */
QAbstractItemView#{OBJ_TOP_BAR_MENU} {{
    border-radius: {r['lg']}px;
    padding: {s['xs']}px;
}}

/* Row *height* is not set here. It comes from the view's item delegate, which does
 * not consult the style sheet -- both ``padding`` and ``min-height`` were measured
 * leaving these rows at the compact list height -- so ``_MenuRowDelegate`` in
 * sharpmod.ui.shell supplies it instead. These are the declarations that do paint. */
QAbstractItemView#{OBJ_TOP_BAR_MENU}::item {{
    padding: 0 {s['xl']}px 0 {s['md']}px;
    border-radius: {r['sm']}px;
}}

/* The trailing group, mirroring QFrame#topBar at the other end of the bar.
 *
 * The divider lives on this frame rather than on the clock label so that both
 * rules are the height of a frame with the same vertical margins, and therefore
 * the same length by construction. Carrying it on the label instead meant tuning
 * a `min-height` token until the two happened to agree -- which then silently
 * became the tallest thing in the bar and set its height. */
QFrame#{OBJ_TOP_BAR_END} {{
    background: transparent;
    border: 0;
    border-left: 1px solid {t.border};
    min-height: {h['sm']}px;
}}

/* A readout, not a value the user acts on, so it recedes to secondary.
 *
 * No padding and no height here. ``_install_utc_clock`` pins this label's width
 * from a measured widest-case sample, and because the text is right-aligned any
 * width the style sheet adds after that measurement is clipped off the *left*
 * edge -- which is what once rendered "UTC" as "JTC". Spacing and the divider
 * both belong to the frame above, which cannot affect the measurement. */
QLabel#{OBJ_UTC_CLOCK} {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {font['caption']}pt;
}}

QMenu {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['lg']}px;
    padding: {s['xs']}px;
}}

QMenu::item {{
    padding: {s['sm']}px {s['xl']}px {s['sm']}px {s['md']}px;
    border-radius: {r['sm']}px;
}}

QMenu::item:selected {{
    background: {t.accent};
    color: {t.accent_text};
}}

QMenu::item:disabled {{
    color: {t.text_disabled};
}}

QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: {s['xs']}px {s['sm']}px;
}}

"""
