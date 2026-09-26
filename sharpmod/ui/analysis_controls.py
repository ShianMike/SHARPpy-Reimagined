"""Shared spacing and bounded form widths for Analysis workspace pages."""

from __future__ import annotations

from qtpy.QtWidgets import QSizePolicy, QWidget

from sharpmod.ui.styles.theme import SPACE


PAGE_INSET = SPACE["lg"]
FORM_WIDTH = 820
SELECTOR_WIDTH = 620
ACTION_WIDTH = 160


def inset_page(layout):
    """Keep page content clear of the scroll viewport and tab borders."""
    layout.setContentsMargins(PAGE_INSET, PAGE_INSET, PAGE_INSET, PAGE_INSET)
    layout.setSpacing(SPACE["md"])


def bounded_form(parent, form_layout, *, width=FORM_WIDTH):
    """Use a readable form column that still contracts with a narrow dock."""
    panel = QWidget(parent)
    panel.setMinimumWidth(0)
    panel.setMaximumWidth(width)
    panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    form_layout.setContentsMargins(0, 0, 0, 0)
    panel.setLayout(form_layout)
    return panel


def bounded_field(widget, *, width=SELECTOR_WIDTH):
    widget.setMinimumWidth(0)
    widget.setMaximumWidth(width)
    widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return widget


def bounded_action(widget, *, width=ACTION_WIDTH):
    widget.setMinimumWidth(0)
    widget.setMaximumWidth(width)
    widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return widget
