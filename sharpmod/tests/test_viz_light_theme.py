"""End-to-end regressions for live sounding color-scheme changes.

These tests deliberately exercise the same ``config_changed`` signal used by
the interactive picker.  A palette test that calls ``SPCWidget.updateConfig``
directly would miss the original defect: persisted light colors worked for a
new sounding, while changing an already-open sounding left most vendored
widgets in the dark palette.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy import QtGui, QtWidgets
from sharppy.viz.preferences import PrefDialog
from sutils.config import Config

from sharpmod import colors, render as render_mod
from sharpmod.gui_settings import (
    _color_style_preferences,
    _write_config_preferences,
)
from sharpmod.tests._examples import examples_dir
from sharpmod.viz.SPCWindow import compose_window


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture(scope="module")
def mounted_sounding(qt_app, tmp_path_factory):
    """A real, fully mounted HRRR sounding and its live controller."""
    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    render_mod.install_font(qt_app)
    render_mod.install_render_patches()
    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(
        str(tmp_path_factory.mktemp("light-theme")))
    win, controller = compose_window(config, prof_col, mount=True)
    win.resize(1630, 1100)
    for _ in range(4):
        qt_app.processEvents()

    try:
        yield win, controller, config
    finally:
        win.close()
        controller.close()
        qt_app.processEvents()


def _apply_style(qt_app, controller, config, style):
    """Select ``style`` and use only the interactive controller signal."""
    _write_config_preferences(config, {"color_style": style})
    controller.config_changed.emit(config)
    for _ in range(4):
        qt_app.processEvents()


def _renderer_surfaces(sw):
    """Return every sounding surface that owns a renderer palette."""
    candidates = {
        "spc_widget": sw,
        "skew_t": sw.sound,
        "hodograph": sw.hodo,
        "storm_slinky": sw.storm_slinky,
        "temperature_advection": sw.inferred_temp_advection,
        "wind_speed": sw.speed_vs_height,
        "storm_relative_winds": sw.srwinds_vs_height,
        "theta_e": sw.thetae_vs_pressure,
        "watch_type": sw.watch_type,
        "convective_table": sw.convective,
        "kinematic_table": sw.kinematic,
        "index_board": sw.index_board,
        "streamwiseness": sw.streamwiseness,
    }
    candidates.update({
        f"inset:{name}": widget
        for name, widget in sw.insets.items()
    })

    # Active left/right insets and streamwiseness also appear in ``insets``.
    # De-duplicate by identity while keeping a useful failure label.
    surfaces = {}
    seen = set()
    for name, widget in candidates.items():
        if widget is None or id(widget) in seen:
            continue
        seen.add(id(widget))
        surfaces[name] = widget
    return surfaces


def _surface_colors(widget):
    for bg_attr, fg_attr in (("bg_color", "fg_color"), ("bg", "fg")):
        if hasattr(widget, bg_attr) and hasattr(widget, fg_attr):
            return (
                QtGui.QColor(getattr(widget, bg_attr)).name().lower(),
                QtGui.QColor(getattr(widget, fg_attr)).name().lower(),
            )
    raise AssertionError(
        f"{type(widget).__module__}.{type(widget).__name__} has no palette pair")


def _assert_surface_palette(sw, *, bg, fg):
    actual = {
        name: _surface_colors(widget)
        for name, widget in _renderer_surfaces(sw).items()
    }
    expected = (bg.lower(), fg.lower())
    mismatches = {
        name: colors for name, colors in actual.items() if colors != expected
    }
    assert not mismatches, (
        f"renderer surfaces did not receive {expected}: {mismatches}")


def _image_luminance_fractions(widget):
    """Return dark/bright pixel fractions without platform-fragile goldens."""
    image = render_mod.grab_widget_pixmap(widget).toImage().convertToFormat(
        QtGui.QImage.Format.Format_RGB32)
    assert not image.isNull()
    assert image.width() > 1000
    assert image.height() > 700

    raw = np.frombuffer(image.bits(), dtype=np.uint8,
                        count=image.sizeInBytes())
    rows = raw.reshape(image.height(), image.bytesPerLine())
    bgra = rows[:, :image.width() * 4].reshape(
        image.height(), image.width(), 4)
    blue = bgra[..., 0].astype(np.float32)
    green = bgra[..., 1].astype(np.float32)
    red = bgra[..., 2].astype(np.float32)
    luminance = 0.0722 * blue + 0.7152 * green + 0.2126 * red
    return float(np.mean(luminance < 32)), float(np.mean(luminance > 223))


class _RecordingPainter:
    """Minimal QPainter stand-in that records colors used by draw paths."""

    def __init__(self):
        self.pen_colors = []

    def setPen(self, pen):  # noqa: N802 - Qt painter contract
        self.pen_colors.append(QtGui.QColor(pen.color()).name().lower())

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _reachable_patch_colors(sw):
    painters = {
        "corfidi": _RecordingPainter(),
        "hodo_0500": _RecordingPainter(),
        "speed_0500": _RecordingPainter(),
        "mpl": _RecordingPainter(),
        "tornado": _RecordingPainter(),
        "stp_stats": _RecordingPainter(),
    }
    sw.hodo.drawCorfidi(painters["corfidi"])
    sw.hodo.draw_hodo(
        painters["hodo_0500"], sw.hodo.prof, sw.hodo.colors)
    sw.speed_vs_height.draw_profile(painters["speed_0500"])
    sw.sound.draw_parcel_levels(painters["mpl"])
    sw.insets["COND STP"].draw_frame(painters["tornado"])
    sw.insets["STP STATS"].draw_frame(painters["stp_stats"])
    return {name: painter.pen_colors for name, painter in painters.items()}


def test_live_controller_signal_applies_inverted_palette_to_every_surface(
        qt_app, mounted_sounding):
    win, controller, config = mounted_sounding
    _apply_style(qt_app, controller, config, "standard")
    _apply_style(qt_app, controller, config, "inverted")

    assert config["preferences", "bg_color"].lower() == "#ffffff"
    assert config["preferences", "fg_color"].lower() == "#000000"
    _assert_surface_palette(win.spc_widget, bg="#ffffff", fg="#000000")

    # Check representative scientific roles, not only background paint.
    sw = win.spc_widget
    assert sw.sound.temp_color.name().lower() == config[
        "preferences", "temp_color"].lower()
    assert sw.sound.dewp_color.name().lower() == config[
        "preferences", "dewp_color"].lower()
    assert [color.name().lower() for color in sw.hodo.colors] == [
        config["preferences", key].lower()
        for key in ("0_3_color", "3_6_color", "6_9_color", "9_12_color")
    ]
    assert sw.watch_type.svr_color.name().lower() == config[
        "preferences", "watch_svr_color"].lower()

    semantic = colors.semantic_palette("#ffffff", "#000000")
    for attr, role in {
        "hdr": "header",
        "rule": "rule",
        "cyan": "cyan",
        "magenta": "magenta",
        "red": "red",
        "yellow": "yellow",
    }.items():
        assert getattr(sw.index_board, attr).name().lower() == semantic[role].lower()
    for attr, role in {
        "text_color": "neutral",
        "profile_color": "profile",
        "cyclonic_color": "cyclonic",
        "anticyclonic_color": "anticyclonic",
        "border_color": "border",
        "grid_color": "grid",
        "marker_gray": "marker_gray",
        "marker_orange": "marker_orange",
        "marker_yellow": "marker_yellow",
    }.items():
        assert (
            getattr(sw.streamwiseness, attr).name().lower()
            == semantic[role].lower()
        )


def test_live_controller_signal_round_trips_standard_and_inverted(
        qt_app, mounted_sounding):
    win, controller, config = mounted_sounding

    _apply_style(qt_app, controller, config, "inverted")
    _assert_surface_palette(win.spc_widget, bg="#ffffff", fg="#000000")

    _apply_style(qt_app, controller, config, "standard")
    _assert_surface_palette(win.spc_widget, bg="#000000", fg="#ffffff")

    _apply_style(qt_app, controller, config, "inverted")
    _assert_surface_palette(win.spc_widget, bg="#ffffff", fg="#000000")


def test_full_mounted_canvas_switches_dominant_luminance_with_theme(
        qt_app, mounted_sounding):
    win, controller, config = mounted_sounding

    _apply_style(qt_app, controller, config, "standard")
    dark, bright = _image_luminance_fractions(win.spc_widget)
    assert dark > 0.70
    assert bright < 0.15

    _apply_style(qt_app, controller, config, "inverted")
    dark, bright = _image_luminance_fractions(win.spc_widget)
    assert bright > 0.70
    assert dark < 0.15


def test_reachable_render_patches_follow_live_theme_and_preserve_dark_colors(
        qt_app, mounted_sounding):
    win, controller, config = mounted_sounding

    _apply_style(qt_app, controller, config, "standard")
    dark_palette = colors.semantic_palette("#000000", "#ffffff")
    actual = _reachable_patch_colors(win.spc_widget)
    assert actual["corfidi"] == [dark_palette["corfidi"]] * 2
    assert actual["hodo_0500"][0] == dark_palette["hodo_0500"]
    assert actual["speed_0500"][0] == dark_palette["hodo_0500"]
    assert dark_palette["mpl"] in actual["mpl"]
    assert {
        dark_palette[role]
        for role in (
            "tornado_ef1", "tornado_ef2", "tornado_ef3", "tornado_ef4")
    }.issubset(actual["tornado"])
    assert dark_palette["conditional_grid"] in actual["tornado"]
    assert {
        QtGui.QColor(value).name().lower()
        for value in render_mod.STP_XLABEL_COLORS.values()
    }.issubset(actual["stp_stats"])

    _apply_style(qt_app, controller, config, "inverted")
    light_palette = colors.semantic_palette("#ffffff", "#000000")
    actual = _reachable_patch_colors(win.spc_widget)
    assert actual["corfidi"] == [light_palette["corfidi"]] * 2
    assert actual["hodo_0500"][0] == light_palette["hodo_0500"]
    assert actual["speed_0500"][0] == light_palette["hodo_0500"]
    assert light_palette["mpl"] in actual["mpl"]
    assert {
        light_palette[role]
        for role in (
            "tornado_ef1", "tornado_ef2", "tornado_ef3", "tornado_ef4")
    }.issubset(actual["tornado"])
    assert light_palette["conditional_grid"] in actual["tornado"]
    assert {
        colors.resolve_theme_color(value, "#ffffff", "#000000")
        for value in render_mod.STP_XLABEL_COLORS.values()
    }.issubset(actual["stp_stats"])


def test_headless_build_config_preserves_selected_inverted_alert_colors(
        tmp_path):
    config_path = tmp_path / "sharpmod_render.ini"
    seeded = Config(str(config_path))
    PrefDialog.initConfig(seeded)
    seeded["preferences", "color_style"] = "inverted"
    seeded.toFile()

    config = render_mod.build_config(str(tmp_path))

    expected = _color_style_preferences("inverted")
    assert config["preferences", "color_style"] == "inverted"
    for key in (
            "bg_color", "fg_color", "temp_color", "dewp_color",
            "alert_l1_color", "alert_l2_color"):
        assert config["preferences", key].lower() == expected[key].lower()


def test_contrast_helpers_have_stable_color_contracts():
    assert colors.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert colors.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert colors.contrast_ratio("#123456", "#123456") == pytest.approx(1.0)

    # Already-readable colors are left alone. Low-contrast scientific hues are
    # adapted, but retain a valid color and satisfy the requested minimum.
    assert colors.resolve_theme_color(
        "#000000", "#ffffff", "#000000") == "#000000"
    adapted = colors.resolve_theme_color(
        "#ffff00", "#ffffff", "#000000", minimum=4.5)
    assert adapted.lower() != "#ffff00"
    assert colors.contrast_ratio(adapted, "#ffffff") >= 4.5


def test_light_semantic_palette_is_complete_and_readable():
    expected_roles = {
        "neutral", "header", "rule", "cyan", "magenta", "red", "yellow",
        "green", "orange", "blue", "amber_l1", "amber_l2", "profile",
        "cyclonic", "anticyclonic", "border", "grid", "marker_gray",
        "marker_orange", "marker_yellow", "corfidi", "mpl", "hodo_0500",
        "tornado_ef1", "tornado_ef2", "tornado_ef3", "tornado_ef4",
        "conditional_grid",
    }
    palette = colors.semantic_palette("#ffffff", "#000000")

    assert set(palette) == expected_roles
    failures = {
        role: (color, colors.contrast_ratio(color, "#ffffff"))
        for role, color in palette.items()
        if colors.contrast_ratio(color, "#ffffff") < 4.5
    }
    assert not failures, f"low-contrast light-theme roles: {failures}"
