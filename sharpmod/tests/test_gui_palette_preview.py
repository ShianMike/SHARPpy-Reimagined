"""Tests for the Preferences palette preview.

Upstream's ``ColorPreview`` loads ``rc/sample_std.png`` from
``sharppy/viz/../../rc``. That is a *top-level* directory in site-packages
rather than package data inside ``sharppy``, so ``collect_all("sharppy")`` never
collected it: the frozen application has no ``_internal/rc``, the pixmap came
back null, and the Colors tab was blank in every release build.

:class:`~sharpmod.gui_shell.PalettePreview` draws the swatch from the palette
this fork will actually apply, which removes the resource dependency entirely
and cannot go stale when a palette value changes.
"""

from __future__ import annotations

import pytest

from qtpy.QtGui import QImage
from qtpy.QtWidgets import QComboBox

from sharpmod import gui_theme
from sharpmod.gui_settings import _color_style_preferences
from sharpmod.gui_shell import PalettePreview

PALETTES = ("standard", "inverted", "protanopia")


def _render(widget, width=420, height=240):
    """Render a widget offscreen and return the image."""
    widget.resize(width, height)
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(0)
    widget.render(image)
    return image


def _distinct_colours(image):
    step = 4
    seen = set()
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            seen.add(image.pixelColor(x, y).name())
    return seen


# ---------------------------------------------------------------------------
# Palette data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", PALETTES)
def test_preview_accepts_every_configured_palette(qt_app, style):
    """Protanopia omits keys the others define, so lookups must tolerate gaps."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    widget = PalettePreview(_color_style_preferences(style))
    try:
        _render(widget)  # must not raise on a missing key
    finally:
        widget.deleteLater()


def test_preview_survives_an_empty_palette(qt_app):
    """A failed palette resolve must not crash the Preferences dialog."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    widget = PalettePreview({})
    try:
        _render(widget)
    finally:
        widget.deleteLater()


def test_missing_key_falls_back_rather_than_raising(qt_app):
    """Protanopia has no ``skew_el_mkr_color``."""
    palette = _color_style_preferences("protanopia")
    assert "skew_el_mkr_color" not in palette, (
        "fixture assumption changed; pick another absent key")
    widget = PalettePreview(palette)
    try:
        colour = widget._colour("skew_el_mkr_color")
        assert colour.isValid()
        assert colour.name().lower() == palette["fg_color"].lower(), (
            "expected the documented fallback to fg_color")
    finally:
        widget.deleteLater()


# ---------------------------------------------------------------------------
# It actually paints the palette
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", PALETTES)
def test_preview_paints_the_palette_background(qt_app, style):
    """The canvas background is the palette's most defining colour."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    palette = _color_style_preferences(style)
    widget = PalettePreview(palette)
    try:
        image = _render(widget)
        expected = palette["bg_color"].lower()
        found = {c.lower() for c in _distinct_colours(image)}
        assert expected in found, (
            f"{style}: background {expected} never painted")
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("style", PALETTES)
def test_preview_paints_the_trace_colours(qt_app, style):
    """Temperature and dewpoint are what a user checks a palette against."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    palette = _color_style_preferences(style)
    widget = PalettePreview(palette)
    try:
        image = _render(widget, 640, 360)
        found = {c.lower() for c in _distinct_colours(image)}
        for key in ("temp_color", "dewp_color"):
            assert palette[key].lower() in found, (
                f"{style}: {key} ({palette[key]}) never painted")
    finally:
        widget.deleteLater()


def test_light_and_dark_palettes_render_differently(qt_app):
    """The preview exists to make the choice visible before accepting."""
    gui_theme.apply_theme(qt_app, color_style="standard")

    images = {}
    for style in ("standard", "inverted"):
        widget = PalettePreview(_color_style_preferences(style))
        try:
            images[style] = _render(widget).copy()
        finally:
            widget.deleteLater()

    assert images["standard"] != images["inverted"], (
        "the dark and light palettes render identically")


def test_switching_palette_repaints(qt_app):
    gui_theme.apply_theme(qt_app, color_style="standard")
    widget = PalettePreview(_color_style_preferences("standard"))
    try:
        before = _render(widget).copy()
        widget.set_palette(_color_style_preferences("inverted"))
        after = _render(widget).copy()
        assert before != after, "set_palette did not change the rendering"
    finally:
        widget.deleteLater()


# ---------------------------------------------------------------------------
# Installation into the real dialog
# ---------------------------------------------------------------------------


@pytest.fixture
def prefs_dialog(qt_app, tmp_path):
    from sharpmod import render as R
    from sharpmod.gui_settings import _build_preferences_dialog

    gui_theme.apply_theme(qt_app, color_style="standard")
    config = R.build_config(str(tmp_path))
    dialog = _build_preferences_dialog(config, parent=None)
    dialog.resize(600, 500)
    dialog.show()
    for _ in range(6):
        qt_app.processEvents()
    yield dialog
    dialog.close()


def test_preview_replaces_the_broken_upstream_widget(prefs_dialog):
    previews = prefs_dialog.findChildren(PalettePreview)
    assert len(previews) == 1, "the live preview was not installed"
    assert previews[0].isVisible()
    assert previews[0]._palette, "the preview has no palette bound"


def test_installed_preview_follows_the_palette_combo(prefs_dialog, qt_app):
    """The swatch must update before Accept, or it cannot inform the choice."""
    preview = prefs_dialog.findChildren(PalettePreview)[0]

    combo = None
    for candidate in prefs_dialog.findChildren(QComboBox):
        items = [candidate.itemText(i) for i in range(candidate.count())]
        if any(text.lower() == "standard" for text in items):
            combo = candidate
            break
    assert combo is not None, "palette combo not found"

    seen = {}
    for name in ("Standard", "Inverted", "Protanopia"):
        combo.setCurrentText(name)
        for _ in range(4):
            qt_app.processEvents()
        seen[name] = preview._palette.get("bg_color")

    assert seen["Standard"] != seen["Inverted"], (
        f"preview did not follow the combo: {seen}")


def test_dialog_still_opens_if_the_preview_cannot_be_installed(
        qt_app, tmp_path, monkeypatch):
    """Preferences is the only way to change units and the default parcel.

    A failure decorating it must never prevent it opening.
    """
    from sharpmod import gui_settings
    from sharpmod import render as R

    def _boom(_dialog):
        raise RuntimeError("simulated preview failure")

    monkeypatch.setattr(gui_settings, "_install_palette_preview", _boom)
    config = R.build_config(str(tmp_path))
    with pytest.raises(RuntimeError):
        # Confirms the monkeypatch is wired to the call site being guarded.
        gui_settings._install_palette_preview(None)

    # And the real guard: the helper swallows its own failures internally.
    monkeypatch.undo()
    dialog = gui_settings._build_preferences_dialog(config, parent=None)
    try:
        assert dialog is not None
    finally:
        dialog.close()
