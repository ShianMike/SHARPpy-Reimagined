"""Focused tests for the GUI's persistent default Skew-T parcel preference."""

import os
from types import SimpleNamespace

# Keep this GUI-focused test isolated from native desktop rendering and from
# the platform assumptions of renderer tests collected in the same process.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QTabWidget,
    QVBoxLayout,
)

import sharpmod.gui as gui
from sharpmod import gui_picker
from sharpmod.gui import (
    PickerWindow,
    _add_default_parcel_tab,
    _apply_default_parcel_to_window,
    _build_preferences_dialog,
    _normalize_default_parcel,
)


def test_default_parcel_normalization_preserves_supported_keys():
    for key in ("SFC", "ML", "FCST", "MU", "EFF", "USER"):
        assert _normalize_default_parcel(key) == key


def test_default_parcel_normalization_falls_back_to_existing_mu_default():
    assert _normalize_default_parcel(None) == "MU"
    assert _normalize_default_parcel("") == "MU"
    assert _normalize_default_parcel("not-a-parcel") == "MU"


def test_preferences_dialog_gets_parcel_tab_with_saved_choice_selected(tmp_path):
    from sharpmod.render import build_config

    app = QApplication.instance() or QApplication([])
    dialog = _build_preferences_dialog(build_config(str(tmp_path)))
    tabs = dialog.findChild(QTabWidget)

    parcel_box = _add_default_parcel_tab(dialog, "EFF")

    assert tabs is not None
    assert parcel_box is not None
    assert tabs.tabText(tabs.indexOf(parcel_box.parentWidget())) == "Parcel"
    assert parcel_box.currentData() == "EFF"
    dialog.deleteLater()
    app.processEvents()


def test_apply_default_parcel_uses_normal_update_path():
    selected = object()

    class FakeSPCWidget:
        def __init__(self):
            self.convective = SimpleNamespace(
                parcels={"ML": selected},
                pcl_types=["SFC", "ML", "FCST", "MU"],
                skewt_pcl=0,
            )
            self.updated = None

        def updateParcel(self, parcel):  # noqa: N802 - SHARPpy API
            self.updated = parcel

    spc_widget = FakeSPCWidget()
    win = SimpleNamespace(spc_widget=spc_widget)

    assert _apply_default_parcel_to_window(win, "ML") is True
    assert spc_widget.updated is selected
    assert spc_widget.convective.skewt_pcl == 1


def test_apply_default_parcel_returns_false_when_parcel_is_unavailable():
    spc_widget = SimpleNamespace(
        convective=SimpleNamespace(parcels={}, pcl_types=["SFC", "ML"]),
        updateParcel=lambda _parcel: None,
    )

    assert _apply_default_parcel_to_window(
        SimpleNamespace(spc_widget=spc_widget), "EFF"
    ) is False


def test_picker_default_parcel_round_trips_through_qsettings(tmp_path):
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.IniFormat)
    owner = SimpleNamespace(_settings=settings)

    assert PickerWindow._default_parcel(owner) == "MU"
    PickerWindow._save_default_parcel(owner, "SFC")
    settings.sync()
    assert PickerWindow._default_parcel(owner) == "SFC"

    settings.setValue("parcel/default_skewt", "invalid")
    assert PickerWindow._default_parcel(owner) == "MU"


def test_accepting_preferences_persists_and_applies_selected_parcel(monkeypatch):
    app = QApplication.instance() or QApplication([])
    saved = []
    applied = []
    saved_configs = []

    class StubPreferencesDialog(QDialog):
        def __init__(self):
            super().__init__()
            layout = QVBoxLayout(self)
            layout.addWidget(QTabWidget(self))

        def exec(self):
            parcel_box = self.findChild(QComboBox)
            parcel_box.setCurrentIndex(parcel_box.findData("SFC"))
            return QDialog.Accepted

    monkeypatch.setattr(
        gui_picker,
        "_build_preferences_dialog",
        lambda _config, parent=None: StubPreferencesDialog(),
    )
    owner = SimpleNamespace(
        _config=lambda: {},
        _default_parcel=lambda: "MU",
        _unit_preferences_from_config=lambda _config: {},
        _save_unit_preferences=lambda _preferences: None,
        _save_config_preferences=saved_configs.append,
        _apply_unit_preferences_to_viewers=lambda _config: None,
        _save_default_parcel=saved.append,
        _apply_default_parcel_to_viewers=applied.append,
        config_changed=SimpleNamespace(emit=lambda _config: None),
    )

    PickerWindow.preferencesbox(owner)

    assert saved == ["SFC"]
    assert applied == ["SFC"]
    assert len(saved_configs) == 1
    app.processEvents()
