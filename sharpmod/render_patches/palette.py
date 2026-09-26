"""Semantic colors for vendored render widgets."""

from __future__ import annotations

from qtpy import QtGui

from sharpmod.viz import colors


def _semantic_qcolor(widget, role, *, override=None):
    """Resolve one draw-time semantic color from a widget's live palette."""
    bg_color = QtGui.QColor(
        getattr(widget, "bg_color", colors.BG_COLOR)).name()
    fg_color = QtGui.QColor(
        getattr(widget, "fg_color", colors.FG_COLOR)).name()
    palette = colors.semantic_palette(bg_color, fg_color)
    resolved = palette[role]

    # Preserve the documented HODO_0_500_COLOR environment override. The
    # default still flows through its named semantic role; a custom value is
    # contrast-adjusted only on a light canvas, like every other role.
    if override is not None:
        dark_default = colors.semantic_palette(
            colors.BG_COLOR, colors.FG_COLOR)[role]
        if QtGui.QColor(override).name() != QtGui.QColor(dark_default).name():
            resolved = colors.resolve_theme_color(
                override, bg_color, fg_color)
    return QtGui.QColor(resolved)
