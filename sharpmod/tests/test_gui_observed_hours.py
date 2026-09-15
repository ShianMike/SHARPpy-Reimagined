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


def _combo_hours_shown(combo):
    """Return each entry's leading ``NNZ``, ignoring any freshness marker.

    The hour and the marker are asserted separately: the hour is the contract
    this test exists for, and pinning the whole label made the two inseparable.
    """
    return tuple(text.split(" ", 1)[0] for text in _combo_labels(combo))


def _combo_markers(combo):
    """Return the entries carrying a freshness marker."""
    return tuple(text for text in _combo_labels(combo) if "\u00b7" in text)


def test_observed_pickers_offer_every_three_hour_utc_cycle():
    """Every three-hourly cycle is offered, newest first.

    The constant stays ascending because it describes the schedule; only the
    presentation is reversed, so the most recent launch is the first entry
    instead of sitting at the bottom of the list.

    The hour each entry shows is asserted from its leading token rather than from
    the whole label, because one entry also carries a "latest" marker naming the
    newest cycle whose run hour has passed. Pinning the label exactly conflated
    two separate promises -- that every cycle is offered as a zero-padded UTC
    hour, and whatever the freshness marker happens to say -- so a wording change
    to the marker read as a missing cycle.
    """
    QApplication.instance() or QApplication([])
    expected_hours = tuple(reversed(range(0, 24, 3)))
    expected_shown = tuple(f"{hour:02d}Z" for hour in expected_hours)

    assert SYNOPTIC_HOURS == tuple(range(0, 24, 3))
    assert sorted(expected_hours) == list(SYNOPTIC_HOURS)

    picker = gui.PickerWindow()
    try:
        picker._select_tab("Station List")
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()

        for combo in (picker._map_cycle, picker._cycle_combo):
            assert _combo_values(combo) == expected_hours
            assert _combo_hours_shown(combo) == expected_shown
            # At most one, or the marker stops meaning "this is the freshest".
            assert len(_combo_markers(combo)) <= 1, _combo_labels(combo)
    finally:
        picker._catalog_timer.stop()
        picker._avail_timer.stop()
        picker._model_availability_timer.stop()
        picker.close()
