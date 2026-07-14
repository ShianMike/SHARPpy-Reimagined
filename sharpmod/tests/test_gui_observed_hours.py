"""Regression coverage for observed-sounding hour selection."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

from sharpmod import gui
from sharpmod.gui_common import SYNOPTIC_HOURS


def _combo_values(combo):
    return tuple(int(combo.itemData(index)) for index in range(combo.count()))


def _combo_labels(combo):
    return tuple(combo.itemText(index) for index in range(combo.count()))


def test_observed_pickers_offer_every_three_hour_utc_cycle():
    QApplication.instance() or QApplication([])
    expected_hours = tuple(range(0, 24, 3))
    expected_labels = tuple(f"{hour:02d}Z" for hour in expected_hours)

    assert SYNOPTIC_HOURS == expected_hours

    picker = gui.PickerWindow()
    try:
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()

        assert _combo_values(picker._map_cycle) == expected_hours
        assert _combo_labels(picker._map_cycle) == expected_labels
        assert _combo_values(picker._cycle_combo) == expected_hours
        assert _combo_labels(picker._cycle_combo) == expected_labels
    finally:
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()
        picker.close()
