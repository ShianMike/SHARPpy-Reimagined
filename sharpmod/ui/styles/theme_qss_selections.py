"""Selections section of the application chrome stylesheet."""

from __future__ import annotations

from sharpmod.ui.styles.theme import (
    OBJ_MAP_LAYER_TABS,
    WEIGHT
)


def qss_selections(t, s, h, r, font, ui, mono) -> str:
    return f"""/* --- Check boxes and radio buttons --------------------------------- */

/* Unchecked is secondary, checked is primary.
 *
 * The overlay card stacks ten of these, and with every label at `text_primary`
 * the card was a paragraph-shaped wall in which the two or three overlays
 * actually switched on were indistinguishable from the seven that were not.
 * Weight is deliberately *not* changed with state: the label would re-measure
 * on every toggle, and a row that shifts width as you click it reads as a
 * glitch. Colour alone is enough here because the indicator carries the state
 * too -- this is emphasis, not the only signal.
 *
 * The row padding is `xs` rather than `xxs`: at 2px the rows ran together into
 * continuous text, so the card read as prose rather than as a list of controls. */
QCheckBox, QRadioButton {{
    background: transparent;
    color: {t.text_secondary};
    spacing: {s['sm']}px;
    padding: {s['xs']}px 0;
}}

QCheckBox:checked, QRadioButton:checked {{
    color: {t.text_primary};
}}

QCheckBox:hover, QRadioButton:hover {{
    color: {t.text_primary};
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {t.text_disabled};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {s['lg']}px;
    height: {s['lg']}px;
    background: {t.surface_sunken};
    border: 1px solid {t.border_strong};
}}

QCheckBox::indicator {{
    border-radius: {r['sm']}px;
}}

QRadioButton::indicator {{
    border-radius: {s['sm']}px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {t.surface};
    border-color: {t.border};
}}

/* --- Tabs ---------------------------------------------------------- */

QTabWidget::pane {{
    background: transparent;
    border: 1px solid {t.border};
    border-radius: {r['lg']}px;
    top: -1px;
}}

QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}

/* Qt sizes its native scroll buttons from tab-bar style metrics (16 px in
 * Fusion). Ordinary action padding consumes that entire glyph area. Keep the
 * native geometry, arrow, accessible name, and scrolling behavior intact. */
QTabBar QToolButton {{
    padding: 0;
    min-height: 0;
}}

QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-top-left-radius: {r['md']}px;
    border-top-right-radius: {r['md']}px;
    padding: {s['sm']}px {s['lg']}px;
    margin-right: {s['xxs']}px;
    font-weight: {WEIGHT['medium']};
}}

QTabBar::tab:hover {{
    background: {t.surface_raised};
    color: {t.text_primary};
}}

/* A 2 px accent underline reads as current far more clearly than the
 * previous filled-tab treatment. */
QTabBar::tab:selected {{
    background: {t.surface_raised};
    color: {t.text_primary};
    border-color: {t.border};
    border-bottom: 2px solid {t.accent};
}}

QTabBar::tab:disabled {{
    color: {t.text_disabled};
}}

/* The map-layer tabs already sit inside a raised card. A second rounded pane
 * around them would look like two old cards nested together, which defeats the
 * consolidation. The underline still provides a clear, keyboard-accessible
 * view switch while the content shares the card's own surface. */
QTabWidget#{OBJ_MAP_LAYER_TABS}::pane {{
    background: transparent;
    border: 0;
    border-top: 1px solid {t.border};
    border-radius: 0;
    top: -1px;
}}

QTabWidget#{OBJ_MAP_LAYER_TABS} > QTabBar::tab {{
    padding: {s['sm']}px {s['md']}px;
}}

/* --- Analysis workspace ------------------------------------------- */

QLabel#pickerSectionSummary, QLabel#pickerSelectionSummary {{
    color: {t.text_secondary};
    font-size: {font['small']}pt;
}}

QSplitter#pickerSelectionSplitter::handle:horizontal {{
    background: {t.border_strong};
    width: {s['sm']}px;
}}

QSplitter#pickerSelectionSplitter::handle:horizontal:hover {{
    background: {t.accent};
}}

/* The workspace is tabbed *into* the sounding sidebar, so Qt's dock tab bar and
 * the workspace's own tab bar stack directly on top of each other. Left
 * identical they read as one confusing double row; a smaller, tighter inner bar
 * reads as a level below the panel switcher above it. */
QTabWidget#analysisWorkspaceTabs > QTabBar::tab {{
    padding: {s['xs']}px {s['md']}px;
    font-size: {font['small']}pt;
}}

/* Widened from the app's 1px hairline: the pane headings already separate the
 * table from the chart, so this handle only has to be grabbable. Transparent at
 * rest, tinted on hover, which is what advertises it as draggable. */
QSplitter#analysisEnsembleSplitter::handle:vertical {{
    height: {s['sm']}px;
    background: transparent;
}}

QSplitter#analysisEnsembleSplitter::handle:vertical:hover {{
    background: {t.accent_subtle};
}}

/* --- Lists and tables --------------------------------------------- */

QListWidget, QListView, QTreeView, QTableWidget, QTableView {{
    background: {t.surface_sunken};
    alternate-background-color: {t.surface};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
    border-radius: {r['md']}px;
    padding: {s['xs']}px;
    outline: 0;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QListWidget::item, QListView::item, QTreeView::item {{
    padding: {s['xs']}px {s['sm']}px;
    border-radius: {r['sm']}px;
}}

QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
    background: {t.accent_subtle};
}}

QListWidget::item:selected, QListView::item:selected,
QTreeView::item:selected {{
    background: {t.accent};
    color: {t.accent_text};
}}

QTableWidget::item, QTableView::item {{
    padding: {s['xs']}px {s['sm']}px;
}}

QHeaderView {{
    background: transparent;
}}

QHeaderView::section {{
    background: {t.surface_raised};
    color: {t.text_secondary};
    border: 0;
    border-bottom: 1px solid {t.border};
    padding: {s['sm']}px;
    font-size: {font['small']}pt;
    font-weight: {WEIGHT['semibold']};
}}

QTableCornerButton::section {{
    background: {t.surface_raised};
    border: 0;
}}

"""
