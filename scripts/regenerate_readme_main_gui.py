"""Regenerate the current main-application screenshots used by the README.

The captures come directly from the real Qt ``PickerWindow`` and interactive
``SPCWindow``. They use the bundled OAX sounding and isolated temporary GUI
settings, so no personal preferences or network services affect the images.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QSettings
from qtpy.QtWidgets import QApplication

from sharpmod import render as render_mod
from sharpmod.gui_picker import PickerWindow
from sharpmod.gui_theme import apply_theme
from sharpmod.gui_viewer import compose_interactive


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "soundings" / "14061619.OAX"
OUTPUT = ROOT / "docs" / "images" / "v1.3.0"
CAPTURE_WIDTH = 1600
CAPTURE_HEIGHT = 950
STYLES = ("standard", "inverted", "protanopia")


def _seed_settings(path: Path, style: str) -> None:
    """Create deterministic settings without importing a user's legacy store."""
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setValue("meta/native_settings_migrated", True)
    settings.setValue("preferences/color_style", style)
    settings.setValue("last_station", "OAX")
    settings.setValue("hide_tips", False)
    settings.sync()


def _settle(app: QApplication, passes: int = 12) -> None:
    for _ in range(passes):
        app.processEvents()


def _save_window(app: QApplication, window, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    window.resize(CAPTURE_WIDTH, CAPTURE_HEIGHT)
    window.showNormal()
    window.raise_()
    window.activateWindow()
    window.ensurePolished()
    _settle(app)
    window.resize(CAPTURE_WIDTH, CAPTURE_HEIGHT)
    window.repaint()
    _settle(app, 6)

    pixmap = window.grab()
    if pixmap.isNull():
        raise RuntimeError(f"Qt returned an empty capture for {destination}")

    with tempfile.NamedTemporaryFile(
        suffix=".png", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        if not pixmap.save(str(temporary_path), "PNG"):
            raise RuntimeError(f"Qt could not save {destination}")
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)

    print(
        f"wrote {destination.relative_to(ROOT)} "
        f"({pixmap.width()}x{pixmap.height()})"
    )


def _new_picker(app: QApplication, settings_root: Path, style: str) -> PickerWindow:
    settings_path = settings_root / style / "settings.ini"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_settings(settings_path, style)
    os.environ["SHARPMOD_SETTINGS_PATH"] = str(settings_path)
    apply_theme(app, color_style=style)
    picker = PickerWindow()
    picker._select_tab("Station Map")
    return picker


def _capture_picker(app: QApplication, settings_root: Path) -> None:
    picker = _new_picker(app, settings_root, "standard")
    try:
        _save_window(app, picker, OUTPUT / "main-app-picker.png")
    finally:
        picker.close()
        _settle(app, 3)


def _capture_viewer(app: QApplication, settings_root: Path, style: str) -> None:
    picker = _new_picker(app, settings_root, style)
    picker.hide()
    collection, station_id = render_mod.decode(str(SOURCE))
    viewer = compose_interactive(
        picker._config(),
        collection,
        picker,
        stn_id=station_id,
    )
    try:
        failures = getattr(viewer, "_sharpmod_install_failures", ())
        if failures:
            raise RuntimeError("; ".join(failures))
        mounted = getattr(viewer, "sharpmod_products", None)
        if mounted is not None:
            mounted.streamwiseness.setChart("srw")
        viewer.setWindowTitle("SHARPpy Reimagined — OAX Main Sounding")
        _save_window(app, viewer, OUTPUT / f"main-app-{style}.png")
    finally:
        viewer.close()
        picker.close()
        _settle(app, 4)


def regenerate() -> None:
    app = QApplication.instance() or QApplication([])
    render_mod.install_font(app)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sharpmod-readme-main-gui-") as directory:
        settings_root = Path(directory)
        _capture_picker(app, settings_root)
        for style in STYLES:
            _capture_viewer(app, settings_root, style)


def main() -> int:
    regenerate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
