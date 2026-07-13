"""Tests for compact unit suffix rendering in sounding value rows."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtGui, QtWidgets

from sharpmod.viz.unit_text import split_value_unit, value_unit_width


def test_split_value_unit_recognizes_sounding_suffixes():
    assert split_value_unit("14.86 g/kg") == ("14.86", " g/kg")
    assert split_value_unit("245/23 kt") == ("245/23", " kt")
    assert split_value_unit("90\u00b0F") == ("90", "\u00b0F")
    assert split_value_unit("Supercell Comp = ") is None


def test_compact_unit_width_is_smaller_than_full_value_width():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _ = app
    font = QtGui.QFont("Helvetica")
    font.setPixelSize(13)
    metrics = QtGui.QFontMetrics(font)

    assert value_unit_width(font, "14.86 g/kg") < metrics.horizontalAdvance("14.86 g/kg")
