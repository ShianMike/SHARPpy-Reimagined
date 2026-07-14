from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Durable GUI preferences, units, parcels, and settings dialogs."""

from sharpmod.gui_common import APP_NAME

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QDateEdit,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
)

UNIT_DEFAULTS = {
    "temp_units": "Fahrenheit",
    "wind_units": "knots",
    "pw_units": "in",
}

UNIT_OPTIONS = {
    "temp_units": ("Fahrenheit", "Celsius"),
    "wind_units": ("knots", "m/s"),
    "pw_units": ("in", "cm"),
}

_READOUT_VARIABLES = (
    "tmpc", "dwpc", "thetae", "wetbulb", "theta", "wvmr", "omeg",
)

CONFIG_PREFERENCE_DEFAULTS = {
    **UNIT_DEFAULTS,
    "color_style": "standard",
    "readout_tr": "tmpc",
    "readout_br": "dwpc",
}

CONFIG_PREFERENCE_OPTIONS = {
    **UNIT_OPTIONS,
    "color_style": ("standard", "inverted", "protanopia"),
    "readout_tr": _READOUT_VARIABLES,
    "readout_br": _READOUT_VARIABLES,
}

PERSISTED_SETTING_DEFAULTS = {
    "units/temp_units": UNIT_DEFAULTS["temp_units"],
    "units/wind_units": UNIT_DEFAULTS["wind_units"],
    "units/pw_units": UNIT_DEFAULTS["pw_units"],
    "preferences/color_style": CONFIG_PREFERENCE_DEFAULTS["color_style"],
    "preferences/readout_tr": CONFIG_PREFERENCE_DEFAULTS["readout_tr"],
    "preferences/readout_br": CONFIG_PREFERENCE_DEFAULTS["readout_br"],
    "parcel/default_skewt": "MU",
    "viewer/combine_soundings": True,
    "model/prefetch_next_hour": False,
    "hide_tips": False,
}


def _settings_file_path() -> Path:
    """Return the durable, user-scoped GUI settings INI path."""
    override = os.environ.get("SHARPMOD_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        root = Path(
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or Path.home()
        )
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / APP_NAME / "settings.ini"


def _build_settings(path=None, legacy_settings=None) -> QSettings:
    """Open the durable INI and migrate the former native store once."""
    path = Path(path).expanduser() if path is not None else _settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(path), QSettings.IniFormat)
    if not settings.value("meta/native_settings_migrated", False, bool):
        legacy = legacy_settings if legacy_settings is not None else QSettings(
            "SHARPpyReimagined", "GUI")
        for key in legacy.allKeys():
            if not settings.contains(key):
                settings.setValue(key, legacy.value(key))
        settings.setValue("meta/native_settings_migrated", True)
        settings.sync()
    seeded = False
    for key, value in PERSISTED_SETTING_DEFAULTS.items():
        if not settings.contains(key):
            settings.setValue(key, value)
            seeded = True
    if seeded:
        settings.sync()
    return settings
#: Ordered parcel options for the Show Parcels dialog: (label, key).
_PARCEL_OPTIONS = [
    ("Surface-Based Parcel", "SFC"),
    ("100 mb Mixed Layer Parcel", "ML"),
    ("Forecast Surface Parcel", "FCST"),
    ("Most Unstable Parcel", "MU"),
    ("Effective Inflow Layer Parcel", "EFF"),
    ("User Defined Parcel", "USER"),
]

#: Parcel key -> Profile attribute (matches sharpmod.viz.index_board.PCL_ATTR).
_PARCEL_ATTR = {"SFC": "sfcpcl", "ML": "mlpcl", "FCST": "fcstpcl",
                "MU": "mupcl", "EFF": "effpcl", "USER": "usrpcl"}

#: Preserve SHARPpy's existing initial Skew-T parcel when no preference has
#: been saved yet (``SPCWidget.parcel_type`` also defaults to ``"MU"``).
_DEFAULT_SKEWT_PARCEL = "MU"


def _normalize_default_parcel(value) -> str:
    """Return a supported parcel key, falling back to SHARPpy's MU default."""
    try:
        key = str(value or "").strip().upper()
    except Exception:
        key = ""
    valid = {parcel_key for _label, parcel_key in _PARCEL_OPTIONS}
    return key if key in valid else _DEFAULT_SKEWT_PARCEL


def _build_preferences_dialog(config, parent=None):
    """Create SHARPpy's preferences dialog with its Qt6 readout fix.

    SHARPpy 1.4.0a5 passes the one-element NumPy result of ``where`` directly
    to ``QComboBox.setCurrentIndex``. Current PySide6 correctly requires a
    scalar integer, which otherwise prevents Preferences from opening at all.
    Keep the upstream dialog and override only that small widget constructor.
    """
    from sharppy.viz.preferences import PrefDialog

    class _Qt6PreferencesDialog(PrefDialog):
        def _createReadoutWidget(self):
            box = QWidget(self)
            layout = QVBoxLayout(box)
            layout.addWidget(QLabel("Top Right Readout Variable:"))
            self.combo1 = QComboBox(box)
            layout.addWidget(self.combo1)
            layout.addWidget(QLabel("Bottom Right Readout Variable:"))
            self.combo2 = QComboBox(box)
            layout.addWidget(self.combo2)

            self.variables = {
                "Temperature (C)": "tmpc",
                "Dewpoint (C)": "dwpc",
                "Equiv. Potential Temp. (K)": "thetae",
                "Wetbulb Temperature (C)": "wetbulb",
                "Potential Temperature (K)": "theta",
                "Water Vapor Mixing Ratio (g/kg)": "wvmr",
                "Vertical Velocity (mb/hr)": "omeg",
            }
            self.combo1.addItems(list(self.variables))
            self.combo2.addItems(list(self.variables))

            top = self._config["preferences", "readout_tr"]
            bottom = self._config["preferences", "readout_br"]
            labels_by_value = {
                value: label for label, value in self.variables.items()
            }
            self.combo1.setCurrentText(
                labels_by_value.get(top, "Temperature (C)"))
            self.combo2.setCurrentText(
                labels_by_value.get(bottom, "Dewpoint (C)"))
            return box

    return _Qt6PreferencesDialog(config, parent=parent)


def _add_default_parcel_tab(dialog, current_key):
    """Add the app-specific default-parcel choice to SHARPpy Preferences."""
    tabs = dialog.findChild(QTabWidget)
    if tabs is None:
        return None

    panel = QWidget(dialog)
    layout = QVBoxLayout(panel)
    layout.addWidget(QLabel(
        "Parcel visualized when a sounding window first opens."))

    parcel_box = QComboBox(panel)
    for label, key in _PARCEL_OPTIONS:
        parcel_box.addItem(label, key)
    selected = parcel_box.findData(_normalize_default_parcel(current_key))
    parcel_box.setCurrentIndex(max(0, selected))
    layout.addWidget(parcel_box)

    note = QLabel(
        "You can still click any parcel row in a sounding window to change "
        "that window's Skew-T trace.")
    note.setWordWrap(True)
    layout.addWidget(note)
    layout.addStretch(1)
    tabs.addTab(panel, "Parcel")
    return parcel_box


def _apply_default_parcel_to_window(win, parcel_key) -> bool:
    """Apply a preferred parcel through SHARPpy's normal interactive path."""
    try:
        key = _normalize_default_parcel(parcel_key)
        sw = getattr(win, "spc_widget", None)
        conv = getattr(sw, "convective", None) if sw is not None else None
        if sw is None or conv is None or not hasattr(sw, "updateParcel"):
            return False

        parcels = getattr(conv, "parcels", {}) or {}
        parcel = parcels.get(key)
        if parcel is None:
            prof = getattr(sw, "default_prof", None)
            parcel = getattr(prof, _PARCEL_ATTR[key], None)
        if parcel is None:
            return False

        pcl_types = list(getattr(conv, "pcl_types", None) or [])
        if key in pcl_types:
            conv.skewt_pcl = pcl_types.index(key)
        sw.updateParcel(parcel)
        return True
    except Exception:
        # A missing parcel (most often EFF in a stable profile) must never stop
        # a sounding from opening; SHARPpy's already-rendered parcel remains.
        return False


class _ParcelDialog(QDialog):
    """A reliable, self-contained "Show Parcels" chooser.

    Replaces the vendored ``SelectParcels`` (whose checkbox state is unreliable
    in our composed window and whose dark-theme indicators are invisible). It
    pre-checks the currently displayed parcels, enforces exactly four, and on
    OK invokes ``on_apply(list_of_keys)`` -- which updates the Skew-T parcel
    trace, the storm slinky, and the IndexBoard.
    """

    _QSS = (
        "QDialog{background-color:#1b2436;color:#e8eef7;}"
        "QLabel{color:#c8d4e6;}"
        "QCheckBox{color:#e8eef7;padding:4px;spacing:9px;}"
        "QCheckBox::indicator{width:16px;height:16px;}"
        "QCheckBox::indicator:unchecked{border:1px solid #8a97ab;"
        "background:#0d1220;border-radius:3px;}"
        "QCheckBox::indicator:checked{border:1px solid #4da3ff;"
        "background:#4da3ff;border-radius:3px;}"
        "QPushButton{background:#2c3e55;color:#ffffff;padding:6px 18px;"
        "border:1px solid #46607f;border-radius:4px;}"
        "QPushButton:hover{background:#37507a;}"
    )

    def __init__(self, current_keys, on_apply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Show Parcels")
        self.setStyleSheet(self._QSS)
        self._on_apply = on_apply
        self._boxes = {}

        layout = QVBoxLayout(self)
        hint = QLabel("Choose exactly four parcels to display:")
        layout.addWidget(hint)
        for label, key in _PARCEL_OPTIONS:
            cb = QCheckBox(label)
            cb.setChecked(key in current_keys)
            self._boxes[key] = cb
            layout.addWidget(cb)

        self._count_lbl = QLabel("")
        layout.addWidget(self._count_lbl)
        for cb in self._boxes.values():
            cb.stateChanged.connect(self._update_count)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK")
        ok.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        layout.addLayout(row)
        self._update_count()

    def _checked_keys(self):
        return [k for label, k in _PARCEL_OPTIONS if self._boxes[k].isChecked()]

    def _update_count(self):
        n = len(self._checked_keys())
        self._count_lbl.setText(f"{n} of 4 selected")
        self._count_lbl.setStyleSheet(
            "color:#7fd08a;" if n == 4 else "color:#e0a030;")

    def _apply(self):
        keys = self._checked_keys()
        if len(keys) != 4:
            QMessageBox.information(
                self, "Show Parcels",
                "Select exactly four parcels to display "
                f"(currently {len(keys)}).")
            return
        try:
            self._on_apply(keys)
        except Exception:
            pass
        self.accept()


class _UnitPreferencesDialog(QDialog):
    """Small display-unit editor for an already-open sounding window."""

    _QSS = (
        "QDialog{background-color:#1b2436;color:#e8eef7;}"
        "QLabel{color:#c8d4e6;}"
        "QComboBox{background:#0d1220;color:#e8eef7;padding:5px;"
        "border:1px solid #596a84;border-radius:4px;}"
        "QPushButton{background:#2c3e55;color:#ffffff;padding:6px 18px;"
        "border:1px solid #46607f;border-radius:4px;}"
        "QPushButton:hover{background:#37507a;}"
    )

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Units")
        self.setStyleSheet(self._QSS)
        self._boxes = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Display units for the active sounding."))

        form = QFormLayout()
        for key, label in (
            ("temp_units", "Temperatures"),
            ("wind_units", "Wind speeds / kinematics"),
            ("pw_units", "PWAT"),
        ):
            box = QComboBox()
            box.addItems(list(UNIT_OPTIONS[key]))
            value = preferences.get(key, UNIT_DEFAULTS[key])
            if value in UNIT_OPTIONS[key]:
                box.setCurrentText(value)
            self._boxes[key] = box
            form.addRow(label, box)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def preferences(self):
        return {key: box.currentText() for key, box in self._boxes.items()}


def _normalize_unit_preferences(preferences):
    prefs = dict(UNIT_DEFAULTS)
    for key, value in (preferences or {}).items():
        if key in UNIT_OPTIONS and value in UNIT_OPTIONS[key]:
            prefs[key] = value
    return prefs


def _preference_settings_key(key):
    return ("units/" if key in UNIT_DEFAULTS else "preferences/") + key


def _read_settings_preferences(settings):
    """Read validated renderer preferences from the durable GUI settings."""
    preferences = {}
    if settings is None:
        return preferences
    for key, options in CONFIG_PREFERENCE_OPTIONS.items():
        value = settings.value(_preference_settings_key(key), "", str)
        if value in options:
            preferences[key] = value
    return preferences


def _save_settings_preferences(settings, preferences) -> None:
    """Persist validated renderer preferences and flush them immediately."""
    if settings is None:
        return
    for key, value in (preferences or {}).items():
        if value in CONFIG_PREFERENCE_OPTIONS.get(key, ()):
            settings.setValue(_preference_settings_key(key), value)
    settings.sync()


def _read_config_preference(config, key):
    for accessor in (
        lambda: config["preferences", key],
        lambda: config[("preferences", key)],
        lambda: config.get(("preferences", key)),
        lambda: config.get("preferences", key),
    ):
        try:
            value = accessor()
        except Exception:
            continue
        if value in CONFIG_PREFERENCE_OPTIONS.get(key, ()):
            return value
    return None


def _read_config_preferences(config):
    preferences = {}
    if config is None:
        return preferences
    for key in CONFIG_PREFERENCE_OPTIONS:
        value = _read_config_preference(config, key)
        if value is not None:
            preferences[key] = value
    return preferences


def _write_config_preferences(config, preferences) -> None:
    """Apply validated persisted preferences to SHARPpy's runtime config."""
    if config is None:
        return
    validated = {
        key: value
        for key, value in (preferences or {}).items()
        if value in CONFIG_PREFERENCE_OPTIONS.get(key, ())
    }
    for key, value in validated.items():
        try:
            config["preferences", key] = value
        except Exception:
            pass

    # ``PrefDialog.initConfig`` applies the default palette before the durable
    # settings are restored. Reapply the selected palette's actual colors, not
    # only its name, so an inverted/protanopia choice is correct on first open.
    style = validated.get("color_style")
    if style is not None:
        try:
            from sharppy.viz.preferences import PrefDialog

            for key, color in PrefDialog._styles[style].items():
                if key in {"alert_l1_color", "alert_l2_color"}:
                    continue
                config["preferences", key] = color
        except Exception:
            pass


def _read_config_unit(config, key):
    """Backward-compatible unit-only wrapper around the preference reader."""
    if key not in UNIT_OPTIONS:
        return None
    return _read_config_preference(config, key)


def _write_unit_preferences_to_config(config, preferences) -> None:
    if config is None:
        return
    _write_config_preferences(config, _normalize_unit_preferences(preferences))


def _apply_unit_preferences_to_window(win, config) -> None:
    """Refresh mounted SHARPpy Reimagined widgets after unit changes."""
    try:
        from sharpmod.viz.SPCWindow import reapply_color_scheme

        reapply_color_scheme(win, config)
        return
    except Exception:
        pass
    try:
        from sharpmod import colors

        prefs = colors.scheme_preferences(config)
        sw = getattr(win, "spc_widget", None) or win
        board = getattr(sw, "index_board", None)
        if board is not None and hasattr(board, "setPreferences"):
            board.setPreferences(update_gui=True, **prefs)
    except Exception:
        pass
