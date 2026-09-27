"""Shared overlay hatch brush for map paint paths."""

from __future__ import annotations

from qtpy import QtCore, QtGui
from qtpy.QtGui import QBrush, QColor

from sharpmod.maps.overlay_hatch import hatch_brush as _overlay_hatch_brush


def hatch_brush(colour: QColor, level: int) -> QBrush:
    """Return the same hatch pattern used by map areas and legend swatches."""
    return _overlay_hatch_brush(QtCore, QtGui, colour, level)
