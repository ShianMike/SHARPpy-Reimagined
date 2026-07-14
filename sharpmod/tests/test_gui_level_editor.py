"""Numeric Skew-T level-editor regressions."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import numpy.ma as ma
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import Signal  # noqa: E402
from qtpy.QtGui import QAction  # noqa: E402
from qtpy.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from sharpmod import gui  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    """Keep one QApplication alive for the dialog tests."""
    return QApplication.instance() or QApplication([])


def _profile():
    return SimpleNamespace(
        pres=ma.array([1000.0, 900.0, 800.0]),
        hght=ma.array([100.0, 1000.0, 2000.0]),
        tmpc=ma.array([25.0, 18.0, 10.0]),
        dwpc=ma.array([20.0, 12.0, 5.0]),
        wdir=ma.array([180.0, 200.0, 220.0]),
        wspd=ma.array([10.0, 20.0, 30.0]),
    )


class _Skew(QWidget):
    modified = Signal(int, dict)
    reset = Signal(list)

    def __init__(self):
        super().__init__()
        self.popupmenu = QMenu(self)


def test_nearest_profile_level_ignores_masked_and_nonfinite_pressure():
    prof = _profile()
    prof.pres = ma.array(
        [1000.0, 900.0, np.nan, 800.0],
        mask=[False, True, False, False],
    )

    assert gui._nearest_profile_level(prof, 830.0) == 3


def test_dialog_pressure_and_height_bounds_preserve_level_order():
    dialog = gui._SoundingLevelEditorDialog(_profile(), 1)

    assert dialog._pres.minimum() > 800.0
    assert dialog._pres.maximum() < 1000.0
    assert dialog._hght.minimum() > 100.0
    assert dialog._hght.maximum() < 2000.0


def test_dialog_returns_no_changes_when_values_are_untouched():
    dialog = gui._SoundingLevelEditorDialog(_profile(), 1)

    assert dialog.changes() == {}


def test_dialog_returns_complete_level_when_one_value_changes():
    dialog = gui._SoundingLevelEditorDialog(_profile(), 1)
    dialog._tmpc.setValue(17.0)

    assert dialog.changes() == {
        "pres": 900.0,
        "hght": 1000.0,
        "tmpc": 17.0,
        "dwpc": 12.0,
        "wdir": 200.0,
        "wspd": 20.0,
    }


def test_dialog_rejects_dewpoint_above_temperature():
    dialog = gui._SoundingLevelEditorDialog(_profile(), 1)
    dialog._dwpc.setValue(19.0)

    with pytest.raises(ValueError, match="Dewpoint cannot exceed temperature"):
        dialog.changes()


def test_installer_adds_one_editor_and_expands_reset_fields():
    skew = _Skew()
    old_reset_calls = []
    reset = QAction("Reset Skew-T", skew)
    reset.triggered.connect(lambda: old_reset_calls.append(True))
    skew.popupmenu.addAction(reset)
    window = SimpleNamespace(
        spc_widget=SimpleNamespace(sound=skew),
    )
    emitted = []
    skew.reset.connect(emitted.append)

    gui._install_level_editor(window)
    gui._install_level_editor(window)
    reset.trigger()

    labels = [action.text() for action in skew.popupmenu.actions()]
    assert labels == ["Edit Nearest Level\u2026", "Reset Skew-T"]
    assert old_reset_calls == []
    assert emitted == [["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]]
