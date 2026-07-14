"""Regression coverage for durable desktop-GUI preferences."""

import os
from types import SimpleNamespace

# Select the headless Qt platform before importing ``sharpmod.gui``. This keeps
# renderer pixel tests deterministic when this module runs first in a batch.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication

import sharpmod.gui as gui
from sharpmod import gui_picker
from sharpmod.gui import (
    CONFIG_PREFERENCE_DEFAULTS,
    PickerWindow,
    _build_settings,
    _read_settings_preferences,
    _save_settings_preferences,
    _settings_file_path,
)


def test_settings_file_path_honors_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.ini"
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(target))

    assert _settings_file_path() == target


def test_build_settings_migrates_existing_native_values_once(tmp_path):
    legacy = QSettings(str(tmp_path / "legacy.ini"), QSettings.IniFormat)
    legacy.setValue("units/temp_units", "Celsius")
    legacy.setValue("parcel/default_skewt", "ML")
    legacy.sync()

    settings = _build_settings(tmp_path / "settings.ini", legacy)

    assert settings.value("units/temp_units", "", str) == "Celsius"
    assert settings.value("parcel/default_skewt", "", str) == "ML"
    assert settings.value(
        "meta/native_settings_migrated", False, bool) is True

    legacy.setValue("units/temp_units", "Fahrenheit")
    legacy.sync()
    settings = _build_settings(tmp_path / "settings.ini", legacy)

    assert settings.value("units/temp_units", "", str) == "Celsius"


def test_build_settings_seeds_complete_user_preference_schema(tmp_path):
    settings = _build_settings(tmp_path / "settings.ini", QSettings())

    expected_strings = {
        "units/temp_units": "Fahrenheit",
        "units/wind_units": "knots",
        "units/pw_units": "in",
        "preferences/color_style": "standard",
        "preferences/readout_tr": "tmpc",
        "preferences/readout_br": "dwpc",
        "parcel/default_skewt": "MU",
    }
    assert {
        key: settings.value(key, "", str)
        for key in expected_strings
    } == expected_strings
    assert settings.value("viewer/combine_soundings", False, bool) is True
    assert settings.value("hide_tips", True, bool) is False


def test_preferences_round_trip_through_settings(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    chosen = {
        "temp_units": "Celsius",
        "wind_units": "m/s",
        "pw_units": "cm",
        "color_style": "inverted",
        "readout_tr": "thetae",
        "readout_br": "omeg",
    }

    _save_settings_preferences(settings, chosen)

    assert _read_settings_preferences(settings) == chosen


def test_invalid_persisted_preferences_are_ignored(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("units/temp_units", "Kelvin")
    settings.setValue("preferences/color_style", "broken")
    settings.sync()

    assert _read_settings_preferences(settings) == {}


def test_lazy_config_creation_restores_celsius_and_other_preferences(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    chosen = {
        "temp_units": "Celsius",
        "wind_units": "m/s",
        "pw_units": "cm",
        "color_style": "protanopia",
        "readout_tr": "wetbulb",
        "readout_br": "wvmr",
    }
    _save_settings_preferences(settings, chosen)
    config = {
        ("preferences", key): value
        for key, value in CONFIG_PREFERENCE_DEFAULTS.items()
    }
    monkeypatch.setattr(
        gui_picker,
        "_render",
        lambda: SimpleNamespace(build_config=lambda _path: config),
    )
    owner = SimpleNamespace(
        config=None,
        _settings=settings,
        statusBar=lambda: SimpleNamespace(showMessage=lambda _text: None),
    )

    restored = PickerWindow._config(owner)

    assert {
        key: restored["preferences", key]
        for key in chosen
    } == chosen
    assert restored["preferences", "dewp_color"] == "#00ffff"
    app.processEvents()
