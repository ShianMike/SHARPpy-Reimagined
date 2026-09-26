"""Capture real Qt screens for the README's archived severe-weather cases.

Each frame comes from the application window. SPC polygons and HRRR fields are
fetched for the exact UTC time in CASES; a failed provider aborts the capture.
The cases use isolated settings so a developer's saved layout cannot alter them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

CAPTURE_WIDTH = 1920
CAPTURE_HEIGHT = 1080

# Qt's default offscreen screen is 800x800. The sounding viewer sizes itself
# from screen geometry when it opens, so configuring only the final PNG/window
# would leave a tiny initial viewer (the 796x487 capture that prompted this).
# Keep the config alive until process exit; use a relative path because Qt
# treats the colon in a Windows drive path as a platform-option separator.
_SCREEN_DIR = tempfile.TemporaryDirectory(prefix="sharpmod-readme-screen-")
_SCREEN_CONFIG = Path(_SCREEN_DIR.name) / "screen.json"
_SCREEN_CONFIG.write_text(json.dumps({
    "screens": [{"name": "readme-1080p", "x": 0, "y": 0,
                 "width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT,
                 "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1.0}],
}), encoding="utf-8")
os.environ["QT_QPA_PLATFORM"] = (
    "offscreen:configfile="
    + os.path.relpath(_SCREEN_CONFIG, Path.cwd()).replace("\\", "/")
)

from qtpy.QtCore import QDate, QSettings
from qtpy.QtWidgets import QApplication

from sharpmod import render as render_mod
from sharpmod.gui_picker import PickerWindow
from sharpmod.gui_theme import apply_theme
from sharpmod.gui_viewer import compose_interactive
from sharpmod.maps import map_overlays
from sharpmod.providers import hrrr_field, spc_outlook


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "images" / "v2.0.0"
SOUNDING = ROOT / "examples" / "soundings" / "hrrr_point_36.68N_95.66W_f018.npz"

# A separate historical event for each screen, including the exact model run.
CASES = {
    "forecast-model": (datetime(2026, 4, 27, 0, tzinfo=timezone.utc), 21),
    "field-panels": (datetime(2026, 5, 18, 12, tzinfo=timezone.utc), 9),
    "map-overlays": (datetime(2026, 4, 28, 21, tzinfo=timezone.utc), 0),
    "sounding": (datetime(2026, 6, 25, 6, tzinfo=timezone.utc), 18),
}


def _settle(app: QApplication, count: int = 10) -> None:
    for _ in range(count):
        app.processEvents()


def _save(app: QApplication, window, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    window.resize(CAPTURE_WIDTH, CAPTURE_HEIGHT)
    window.showNormal()
    window.ensurePolished()
    _settle(app, 16)
    window.resize(CAPTURE_WIDTH, CAPTURE_HEIGHT)
    _settle(app, 6)
    window.repaint()
    _settle(app, 6)
    pixmap = window.grab()
    if pixmap.isNull() or (pixmap.width(), pixmap.height()) != (
        CAPTURE_WIDTH, CAPTURE_HEIGHT
    ):
        raise RuntimeError(f"Empty Qt capture: {name}")
    target = OUTPUT / f"{name}.png"
    with tempfile.NamedTemporaryFile(suffix=".png", dir=OUTPUT, delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        if not pixmap.save(str(temporary), "PNG") or temporary.stat().st_size == 0:
            raise RuntimeError(f"Qt could not save {target}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"wrote {target.relative_to(ROOT)} ({pixmap.width()}x{pixmap.height()})", flush=True)


def _picker(app: QApplication, settings_root: Path, name: str) -> PickerWindow:
    settings_path = settings_root / name / "settings.ini"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    settings.setValue("meta/native_settings_migrated", True)
    settings.setValue("preferences/color_style", "standard")
    settings.setValue("hide_tips", True)
    settings.sync()
    os.environ["SHARPMOD_SETTINGS_PATH"] = str(settings_path)
    apply_theme(app, color_style="standard")
    return PickerWindow()


def _outlook(valid: datetime):
    layer = spc_outlook.fetch_layer(valid, product="cat")
    if layer is None or not layer.covers(valid):
        raise RuntimeError(f"No time-matched SPC outlook for {valid}")
    print(f"SPC {valid:%Y-%m-%d %HZ}: {layer.source_url}", flush=True)
    return layer


def _field(product: str, run: datetime, hour: int):
    field = hrrr_field.fetch_field(product, run=run, fxx=hour,
                                  size=(1536, 768), opacity=0.76)
    valid = run + timedelta(hours=hour)
    if field is None or field.valid_time != valid:
        raise RuntimeError(f"No matching HRRR {product} field for {valid}")
    print(f"HRRR {product}: {field.subtitle}", flush=True)
    return field


def _show_layers(picker, prefix: str, field, outlook) -> None:
    """Keep the rail's switches and state consistent with the pixels."""
    field_control = getattr(picker, f"_{prefix}_field")
    outlook_control = getattr(picker, f"_{prefix}_outlook")
    field_control.set_product(field.short_name)
    field_control.set_enabled(True)
    outlook_control.set_enabled(True)
    field_control._timer.stop()
    outlook_control._timer.stop()
    field_control._refresh_timer.stop()
    outlook_control._supersede_timer.stop()
    field_control._token += 1
    outlook_control._token += 1
    widget = picker._map if prefix == "map" else picker._model_map
    widget.set_overlay(hrrr_field.OVERLAY_KEY, field, visible=True)
    widget.set_overlay(spc_outlook.OVERLAY_KEY, outlook, visible=True)
    field_control._set_status(field_control._describe(field), state="available")
    outlook_control._signature = outlook_control._current_signature()
    outlook_control._set_status(outlook_control._describe(outlook))
    picker._refresh_layer_list(prefix)


def _choose_hour(picker, prefix: str, run: datetime, hour: int) -> None:
    date = getattr(picker, f"_{prefix}_date")
    cycle = getattr(picker, f"_{prefix}_cycle")
    date.setDate(QDate(run.year, run.month, run.day))
    cycle_index = cycle.findData(run.hour)
    if cycle_index < 0:
        raise RuntimeError(f"Cycle {run.hour}Z unavailable in {prefix}")
    cycle.setCurrentIndex(cycle_index)
    combo = getattr(picker, f"_{prefix}_fxx_combo")
    hour_index = combo.findData(hour)
    if hour_index < 0:
        raise RuntimeError(f"F{hour:03d} unavailable in {prefix}")
    combo.setCurrentIndex(hour_index)
    getattr(picker, f"_{prefix}_update_valid_label")()


def _capture_forecast(app, settings_root: Path) -> None:
    run, hour = CASES["forecast-model"]
    valid = run + timedelta(hours=hour)
    outlook = _outlook(valid)
    field = _field("stp", run, hour)
    picker = _picker(app, settings_root, "forecast-model")
    try:
        picker._select_tab("Forecast Model")
        combo = picker._model_combo
        index = combo.findData("hrrr")
        if index < 0:
            raise RuntimeError("HRRR is unavailable in Forecast Model")
        combo.setCurrentIndex(index)
        _choose_hour(picker, "model", run, hour)
        picker._model_mode_combo.setCurrentIndex(
            picker._model_mode_combo.findData("history"))
        picker._model_lat.setValue(38.5)
        picker._model_lon.setValue(-90.5)
        picker._model_map.set_extent(-97, -84, 33, 43, pad=0.08)
        _show_layers(picker, "model", field, outlook)
        _save(app, picker, "forecast-model")
    finally:
        picker.close()
        _settle(app, 3)


def _capture_panels(app, settings_root: Path) -> None:
    run, hour = CASES["field-panels"]
    valid = run + timedelta(hours=hour)
    outlook = _outlook(valid)
    products = ("refc", "mlcape", "shear-0-6km", "stp")
    fields = [_field(product, run, hour) for product in products]
    picker = _picker(app, settings_root, "field-panels")
    try:
        picker._select_tab("Field Panels")
        picker._panels_count_combo.setCurrentIndex(
            picker._panels_count_combo.findData(4))
        _choose_hour(picker, "panels", run, hour)
        picker._panels_mode_combo.setCurrentIndex(
            picker._panels_mode_combo.findData("history"))
        picker._panels_lat.setValue(39.5)
        picker._panels_lon.setValue(-97.0)
        view = picker._panels_view
        view.set_point(39.5, -97.0)
        for panel, product, field in zip(view._panels, products, fields):
            panel.field.set_enabled(False)
            panel.set_product(product)
            panel.map.set_extent(-101, -93, 36, 42, pad=0.08)
            panel.map.set_overlay(hrrr_field.OVERLAY_KEY, field, visible=True)
            panel.map.set_overlay(spc_outlook.OVERLAY_KEY, outlook, visible=True)
        picker._panels_outlook.set_enabled(True)
        picker._panels_outlook._supersede_timer.stop()
        picker._panels_outlook._token += 1
        picker._panels_outlook._signature = (
            picker._panels_outlook._current_signature()
        )
        picker._panels_outlook._set_status(
            picker._panels_outlook._describe(outlook)
        )
        picker._refresh_layer_list("panels")
        _save(app, picker, "field-panels")
    finally:
        picker.close()
        _settle(app, 3)


def _capture_overlays(app, settings_root: Path) -> None:
    run, hour = CASES["map-overlays"]
    valid = run + timedelta(hours=hour)
    outlook = _outlook(valid)
    field = _field("refc", run, hour)
    picker = _picker(app, settings_root, "map-overlays")
    try:
        picker._select_tab("Station Map")
        picker._map_date.setDate(QDate(valid.year, valid.month, valid.day))
        index = picker._map_cycle.findData(valid.hour)
        if index < 0:
            raise RuntimeError(f"{valid.hour:02d}Z station-map hour unavailable")
        picker._map_cycle.setCurrentIndex(index)
        picker._map_mode_combo.setCurrentIndex(
            picker._map_mode_combo.findData("history"))
        picker._map.set_extent(-99, -89, 34, 42, pad=0.08)
        picker._map.set_selected("72440")
        picker._map_on_select("72440", check_availability=False)
        _show_layers(picker, "map", field, outlook)
        _save(app, picker, "map-overlays")
    finally:
        picker.close()
        _settle(app, 3)


def _capture_sounding(app, settings_root: Path) -> None:
    run, hour = CASES["sounding"]
    valid = run + timedelta(hours=hour)
    outlook = _outlook(valid)
    field = _field("stp", run, hour)
    picker = _picker(app, settings_root, "sounding")
    picker.hide()
    collection, station_id = render_mod.decode(str(SOUNDING))
    for layer in (field, outlook):
        map_overlays.attach_locator_overlay(collection, layer)
    viewer = compose_interactive(picker._config(), collection, picker,
                                 stn_id=station_id)
    try:
        failures = getattr(viewer, "_sharpmod_install_failures", ())
        if failures:
            raise RuntimeError("; ".join(failures))
        mounted = getattr(viewer, "sharpmod_products", None)
        if mounted is not None:
            mounted.streamwiseness.setChart("srw")
        _save(app, viewer, "sounding")
    finally:
        viewer.close()
        picker.close()
        _settle(app, 4)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()
    geometry = screen.availableGeometry() if screen is not None else None
    if geometry is None or (geometry.width(), geometry.height()) != (
        CAPTURE_WIDTH, CAPTURE_HEIGHT
    ):
        raise RuntimeError("README capture requires a 1920x1080 Qt screen")
    render_mod.install_font(app)
    render_mod.install_render_patches()
    with tempfile.TemporaryDirectory(prefix="sharpmod-readme-cases-") as directory:
        settings_root = Path(directory)
        _capture_forecast(app, settings_root)
        _capture_panels(app, settings_root)
        _capture_overlays(app, settings_root)
        _capture_sounding(app, settings_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
