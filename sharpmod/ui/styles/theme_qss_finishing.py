"""Finishing section of the application chrome stylesheet."""

from __future__ import annotations

from sharpmod.ui.styles.theme import (
    OBJ_AVAIL_DOT,
    OBJ_AVAIL_STATION,
    OBJ_AVAIL_TEXT,
    OBJ_CANVAS_HOST,
    PROGRESS_H,
    SCROLLBAR_W,
    WEIGHT,
    _avail_status_rules
)


def qss_finishing(t, s, h, r, font, ui, mono) -> str:
    return f"""/* --- Progress ----------------------------------------------------- */

QProgressBar {{
    background: {t.surface_sunken};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    min-height: {PROGRESS_H}px;
    max-height: {PROGRESS_H}px;
    text-align: center;
    font-family: {mono};
    font-size: {font['caption']}pt;
}}

QProgressBar::chunk {{
    background: {t.accent};
    border-radius: {r['sm']}px;
}}

/* --- Toolbar ------------------------------------------------------ */

QToolBar {{
    background: {t.surface_raised};
    border: 0;
    border-bottom: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    spacing: {s['xxs']}px;
}}

/* Toolbar buttons are borderless until hovered, so a row of them reads as one
 * strip rather than a fence of boxed controls. */
QToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r['md']}px;
    color: {t.text_secondary};
    padding: {s['xs']}px {s['sm']}px;
    min-height: {h['sm']}px;
    font-weight: {WEIGHT['medium']};
}}

QToolBar QToolButton:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

QToolBar QToolButton:pressed {{
    background: {t.surface_sunken};
}}

/* A latched mode button. Setting a checkable QAction is not enough on its own:
 * with a style sheet in play, Qt stops drawing its native checked indicator, so
 * a checked button would look identical to an unchecked one. */
QToolBar QToolButton:checked {{
    background: {t.accent_subtle};
    border-color: {t.accent};
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

QToolBar QToolButton:disabled {{
    color: {t.text_disabled};
    background: transparent;
}}

QToolBar::separator {{
    background: {t.border};
    width: 1px;
    margin: {s['xs']}px {s['sm']}px;
}}

/* --- Status bar --------------------------------------------------- */

/* Matches the top bar's treatment, so the window is framed by two strips of the
 * same material rather than by one styled bar and one default one. The padding
 * is what stops the readiness line sitting flush against the window edge. */
QStatusBar {{
    background: {t.surface_raised};
    color: {t.text_secondary};
    border-top: 1px solid {t.border};
    padding: {s['xxs']}px {s['sm']}px;
    font-size: {font['small']}pt;
}}

QStatusBar::item {{
    border: 0;
}}

/* --- Scrollbars --------------------------------------------------- */

/* The width is SCROLLBAR_W itself, not a spacing token that happens to equal
 * it: a scroll area reserves this much in its layout, so the two drifting
 * apart clips content or leaves a dead strip.
 *
 * The padding insets the handle *inside* that reservation instead of
 * shrinking it, which is what gives the bar a gutter. Without it the handle
 * filled all 12 px and butted straight against the card border beside it,
 * reading as though the bar sat on top of the rail rather than beside it.
 * Padding on the bar is the only one of the three obvious ways to inset a
 * handle that Qt actually applies -- margin on the handle and a transparent
 * border on the handle both leave the rect at the full bar width. */
QScrollBar:vertical {{
    background: transparent;
    width: {SCROLLBAR_W}px;
    padding: 0 {s['xxs']}px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: {SCROLLBAR_W}px;
    padding: {s['xxs']}px 0;
    margin: 0;
}}

/* Split by orientation. A shared rule also puts a min-width on the vertical
 * handle and a min-height on the horizontal one -- each bar's short axis,
 * where a 24 px floor can only fight the width declared above it. Only the
 * long axis wants a minimum, so the handle stays grabbable on a long rail. */
QScrollBar::handle:vertical {{
    background: {t.scrollbar};
    border-radius: {r['sm']}px;
    min-height: {s['xxl']}px;
}}

QScrollBar::handle:horizontal {{
    background: {t.scrollbar};
    border-radius: {r['sm']}px;
    min-width: {s['xxl']}px;
}}

QScrollBar::handle:hover {{
    background: {t.border_strong};
}}

/* Qt draws stepper arrows and page-gap tracks by default; both look
 * dated and neither is needed with a visible handle. */
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
    border: 0;
    background: transparent;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- Sliders (forecast timeline) ---------------------------------- */

QSlider::groove:horizontal {{
    background: {t.surface_sunken};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    height: {s['xs']}px;
}}

QSlider::sub-page:horizontal {{
    background: {t.accent};
    border-radius: {r['sm']}px;
}}

QSlider::handle:horizontal {{
    background: {t.accent};
    border: 2px solid {t.surface};
    width: {s['md']}px;
    height: {s['md']}px;
    margin: -{s['sm']}px 0;
    border-radius: {s['sm']}px;
}}

QSlider::handle:horizontal:hover {{
    background: {t.accent_hover};
}}

QSlider::handle:horizontal:disabled {{
    background: {t.border_strong};
}}

QSlider::sub-page:horizontal:disabled {{
    background: {t.border};
}}

/* The zoom slider sits inside a toolbar, so it needs tighter vertical metrics
 * than the timeline slider: the default handle margin makes the whole toolbar
 * taller than its buttons require. */
QSlider#zoomSlider {{
    margin: 0 {s['sm']}px;
}}

QSlider#zoomSlider::groove:horizontal {{
    height: {s['xxs']}px;
}}

QSlider#zoomSlider::handle:horizontal {{
    width: {s['md']}px;
    height: {s['md']}px;
    margin: -{s['xs']}px 0;
    border-radius: {s['sm']}px;
}}

/* --- Splitters ---------------------------------------------------- */

QSplitter::handle {{
    background: {t.border};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background: {t.accent};
}}

/* --- Dialog button box -------------------------------------------- */

QDialogButtonBox {{
    button-layout: 2;  /* Windows-style: affirmative action on the right */
}}

/* --- Availability chip -------------------------------------------- */

/* State-driven via a dynamic Qt property, so a theme switch restyles it
 * through the normal cascade. The previous implementation rewrote its own
 * style sheet on every update, which overrode any theme.
 *
 * Colour is never the only signal: the chip always carries a text label
 * alongside the dot. */
QLabel#{OBJ_AVAIL_STATION} {{
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_AVAIL_DOT} {{
    border-radius: 7px;
    border: 1px solid {t.surface_sunken};
    background: {t.text_tertiary};
}}

QLabel#{OBJ_AVAIL_TEXT} {{
    font-weight: {WEIGHT['semibold']};
    color: {t.text_tertiary};
}}

{_avail_status_rules(t)}

/* --- Scientific canvas host --------------------------------------- */

/* The canvas paints its own background from sharpmod.viz.colors. The host only
 * supplies a neutral inset so the canvas reads as content within the frame;
 * it must not impose a colour on the canvas itself.
 *
 * Worn by the two sounding hosts (_FixedSoundingScrollArea and
 * _ScaledSoundingView), which both derive from QFrame. They used to carry this
 * colour in an inline style sheet, which outranks this one and is not
 * recomputed, so switching colour style with a sounding open left the surround
 * on the previous theme.
 *
 * Borderless on purpose. These hosts fill the central widget, so a border would
 * be a line against the window edge -- and it would take 2px out of the
 * viewport in each direction, which is exactly what the sidebar width budget
 * and the fit scale are measured against. */
QFrame#{OBJ_CANVAS_HOST} {{
    background: {t.surface_sunken};
    border: 0;
}}
"""
