"""Base section of the application chrome stylesheet."""

from __future__ import annotations

from sharpmod.ui.styles.theme import (
    OBJ_ATTRIBUTION,
    OBJ_DOCK_TITLE,
    OBJ_EMPHASIS,
    OBJ_ERROR_TEXT,
    OBJ_GHOST,
    OBJ_GUIDE_BODY,
    OBJ_GUIDE_DIALOG,
    OBJ_HEADER_BAR,
    OBJ_HINT,
    OBJ_NAV_RAIL,
    OBJ_NAV_RAIL_HEADER,
    OBJ_NUMERIC,
    OBJ_PROGRESS_DETAIL,
    OBJ_REPORT,
    OBJ_SECTION_LABEL,
    OBJ_SIDEBAR,
    OBJ_STATUS,
    OBJ_WARNING_TEXT,
    PROP_COMPACT,
    PROP_HEADER_ACTION,
    WEIGHT
)


def qss_base(t, s, h, r, font, ui, mono) -> str:
    return f"""
/* ===================================================================
 * SHARPpy Reimagined -- chrome style sheet
 * Generated from sharpmod.ui.styles.theme tokens for theme "{t.name}".
 * Do not hand-edit: change the tokens instead.
 * =================================================================== */

/* --- Base ---------------------------------------------------------- */

QWidget {{
    background: {t.surface};
    color: {t.text_primary};
    font-family: {ui};
    font-size: {font['body']}pt;
}}

QMainWindow, QDialog {{
    background: {t.surface};
}}

/* Containers must not repaint the surface, or nested panels stack
 * progressively lighter/darker tints on top of each other. */
QScrollArea, QSplitter, QStackedWidget, QTabWidget {{
    background: transparent;
    border: 0;
}}

QLabel {{
    background: transparent;
    color: {t.text_primary};
}}

QToolTip {{
    background: {t.surface_overlay};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['sm']}px;
    padding: {s['xs']}px {s['sm']}px;
}}

/* --- Semantic text roles ------------------------------------------- */

QLabel#{OBJ_HINT} {{
    color: {t.text_tertiary};
    font-size: {font['small']}pt;
}}

QLabel#{OBJ_ATTRIBUTION} {{
    color: {t.text_tertiary};
    font-size: {font['caption']}pt;
}}

QLabel#{OBJ_EMPHASIS} {{
    color: {t.text_primary};
    font-weight: {WEIGHT['semibold']};
}}

/* Secondary prose: readiness and validation sentences. Deliberately not
 * monospace -- only columns of figures get the mono family. */
/* The validating counterpart of OBJ_STATUS: same size and role, but reporting a
 * problem. Exists so a label that alternates between the two states can swap
 * object name rather than carrying an inline colour, which outranks this sheet
 * and cannot follow a theme change -- the timeline's summary line was hardcoded
 * to the dark palette and rendered pale grey on white on paper-light. */
QLabel#{OBJ_ERROR_TEXT} {{
    color: {t.danger};
    font-size: {font['small']}pt;
}}

/* The middle rung between OBJ_STATUS and OBJ_ERROR_TEXT: the result stands, but
 * it rests on less than it should -- a partial ensemble, a substituted run. Red
 * overstates that and plain secondary text hides it entirely. */
QLabel#{OBJ_WARNING_TEXT} {{
    color: {t.warning};
    font-size: {font['small']}pt;
}}

QLabel#{OBJ_STATUS} {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
}}

/* Byte counts, transfer rates, and phase counters, which are figures that
 * change in place -- monospace stops the text jittering as digits change. */
QLabel#{OBJ_PROGRESS_DETAIL} {{
    color: {t.text_secondary};
    font-family: {mono};
    font-size: {font['small']}pt;
}}

QLabel#{OBJ_SECTION_LABEL} {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_NUMERIC}, QLabel[role="numeric"] {{
    font-family: {mono};
}}

/* A combo box whose entries are figures rather than names -- cycle hours and
 * forecast hours, both of which now carry a timestamp. Monospaced so the columns
 * line up down the list and it reads as a table instead of ragged prose; the
 * proportional face put every ``F012 . Sep 14 12Z`` at a different offset.
 *
 * The popup needs the rule too, and needs it scoped. A popup is a separate
 * top-level window, so the field's family does not reach it, and the shared
 * ``QComboBox QAbstractItemView`` rule is the wrong place to fix that -- it would
 * put model names and region names in mono as well. */
QComboBox#{OBJ_NUMERIC} {{
    font-family: {mono};
}}

QComboBox#{OBJ_NUMERIC} QAbstractItemView {{
    font-family: {mono};
}}

/* --- Shell: header bar and navigation rail ------------------------- */

QFrame#{OBJ_HEADER_BAR} {{
    background: {t.surface_raised};
    border-bottom: 1px solid {t.border};
}}

QFrame#{OBJ_NAV_RAIL} {{
    background: {t.surface_raised};
    border: 0;
    border-right: 1px solid {t.border};
}}

QLabel#{OBJ_NAV_RAIL_HEADER} {{
    color: {t.text_tertiary};
    font-size: {font['caption']}pt;
    font-weight: {WEIGHT['semibold']};
    padding: 0 {s['sm']}px {s['xxs']}px {s['sm']}px;
}}

/* The rail's list must not inherit the sunken, bordered treatment that data
 * lists get -- it is navigation chrome sitting on the rail surface. */
QListWidget#{OBJ_NAV_RAIL} {{
    background: transparent;
    border: 0;
    padding: 0;
    outline: 0;
}}

QListWidget#{OBJ_NAV_RAIL}::item {{
    color: {t.text_secondary};
    padding: {s['sm']}px {s['sm']}px {s['sm']}px {s['md']}px;
    margin: {s['xxs']}px 0;
    border-radius: {r['md']}px;
    /* Reserve the accent bar's width on every row so selecting one does not
     * shift its label sideways. */
    border-left: {s['xxs']}px solid transparent;
    min-height: {h['md']}px;
}}

QListWidget#{OBJ_NAV_RAIL}::item:hover {{
    background: {t.accent_subtle};
    color: {t.text_primary};
}}

/* Tinted background plus an accent bar, rather than a filled accent row:
 * a solid accent block for the current section overpowers the panel beside it. */
QListWidget#{OBJ_NAV_RAIL}::item:selected {{
    background: {t.accent_subtle};
    color: {t.text_primary};
    border-left: {s['xxs']}px solid {t.accent};
    font-weight: {WEIGHT['semibold']};
}}

/* Single-line rows: the padding above keeps a two-line row legible, which is
 * far too airy when the row is just a member name. */
QListWidget#{OBJ_NAV_RAIL}[{PROP_COMPACT}="true"]::item {{
    padding: {s['xs']}px {s['sm']}px {s['xs']}px {s['md']}px;
    min-height: {h['sm']}px;
}}

/* --- Viewer context sidebar ---------------------------------------- */

/* Mirrors the nav rail, but bordered on the left because it docks right. The
 * scientific canvas sits immediately beside it, so the divider is what keeps
 * the chrome from reading as part of the sounding. */
QFrame#{OBJ_SIDEBAR} {{
    background: {t.surface_raised};
    border: 0;
    border-left: 1px solid {t.border};
}}

/* The dock supplies its own title-bar widget (see ``_dock_title_bar``), so
 * QDockWidget::title and ::close-button are not styled here. Qt's built-in
 * title bar was abandoned rather than themed: Fusion derives the close button's
 * rect from title-bar metrics and ignores the QSS width/height, collapsing it
 * to about 16x9px -- too small to aim at, and the only affordance for
 * dismissing the panel. */
QDockWidget {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QLabel#{OBJ_DOCK_TITLE} {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

/* A column-aligned text report. The family has to come from here rather than a
 * QFont: render.install_font patches QFont process-wide when the first sounding
 * opens, so a face chosen in Python is replaced by the chart font. */
QPlainTextEdit#{OBJ_REPORT} {{
    font-family: {mono};
    font-size: {font['small']}pt;
}}

/* The interaction guide. Prose, so it gets the reading size and a little room
 * to breathe rather than the dense control metrics. */
QDialog#{OBJ_GUIDE_DIALOG} {{
    background: {t.surface};
}}

QTextBrowser#{OBJ_GUIDE_BODY} {{
    background: {t.surface_sunken};
    border: 1px solid {t.border};
    border-radius: {r['md']}px;
    padding: {s['md']}px;
    font-size: {font['body']}pt;
}}

/* A square glyph button, e.g. a header close.
 *
 * The size is pinned here rather than with setFixedSize because
 * QStyleSheetStyle recomputes a widget's size constraints from the style sheet
 * and overrides the programmatic fixed size -- setting it in both places gives
 * two sources of truth, and the style sheet wins. Left at the shared
 * min-height it renders 34px tall and inflates its header to 50px. */
QToolButton#{OBJ_GHOST}[{PROP_COMPACT}="true"] {{
    min-width: {h['xs']}px;
    max-width: {h['xs']}px;
    min-height: {h['xs']}px;
    max-height: {h['xs']}px;
    padding: 0;
}}

/* A worded action inside a compact panel header. It shares the header's short
 * height but sizes horizontally from its label instead of becoming the square
 * glyph target used by close buttons. */
QToolButton#{OBJ_GHOST}[{PROP_HEADER_ACTION}="true"] {{
    min-height: {h['xs']}px;
    max-height: {h['xs']}px;
    padding: 0 {s['sm']}px;
}}

"""
