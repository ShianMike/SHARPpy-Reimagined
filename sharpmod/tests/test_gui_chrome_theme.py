"""Shared Qt renderer theme and font regressions.

The scientific image renderer still uses Qt, its application font, and the
shared palette. Retired picker layout and dialog tests live in v2.0.0 history.
"""

from __future__ import annotations

import pytest

from qtpy.QtGui import QFont

from sharpmod import gui_theme
from sharpmod import theme as T

# ---------------------------------------------------------------------------
# Application-level application
# ---------------------------------------------------------------------------


def test_ensure_theme_applied_is_idempotent(qt_app):
    """``main`` applies the theme first; later calls must not re-apply."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    first = qt_app.styleSheet()
    gui_theme.ensure_theme_applied(qt_app)
    assert qt_app.styleSheet() == first, (
        "ensure_theme_applied overwrote an already-applied theme"
    )
    gui_theme.ensure_theme_applied(
        qt_app, color_style="standard", text_scale=100, density="comfortable")
    assert qt_app.styleSheet() == first
    try:
        gui_theme.ensure_theme_applied(qt_app, color_style="inverted")
        assert qt_app.styleSheet() != first, (
            "an explicit color_style must still take precedence"
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_reapplying_the_theme_restores_a_replaced_stylesheet(qt_app):
    """The no-op shortcut must compare sheets, not mere non-emptiness."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    assert expected, "no chrome style sheet to replace"
    qt_app.setStyleSheet("QWidget { color: red; }")
    try:
        restored = gui_theme.apply_theme(qt_app, color_style="standard")
    finally:
        if qt_app.styleSheet() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert restored == gui_theme.theme_for_color_style("standard")
    assert qt_app.styleSheet() == expected, (
        "reapplying the theme did not restore the replaced style sheet"
    )


def test_theme_override_with_same_name_rebuilds_the_stylesheet(qt_app):
    """``with_overrides()`` keeps the name but must not keep the old CSS."""
    base = gui_theme.theme_for_color_style("standard")
    tinted = base.with_overrides(accent="#FF00FF")
    assert tinted.name == base.name
    assert tinted != base
    gui_theme.apply_theme(qt_app, theme=base)
    gui_theme.apply_theme(qt_app, theme=tinted)
    try:
        assert gui_theme.current_theme() == tinted
        assert "#FF00FF" in qt_app.styleSheet(), (
            "the cached sheet for the theme name hid the overridden accent"
        )
        assert base.accent not in qt_app.styleSheet()
    finally:
        gui_theme.apply_theme(qt_app, theme=base)
    assert base.accent in qt_app.styleSheet()


def test_ensure_theme_applied_restores_a_replaced_stylesheet(qt_app):
    """The PickerWindow entry point must validate the sheet, not the flag."""
    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    assert expected, "no chrome style sheet to replace"
    qt_app.setStyleSheet("QWidget { color: red; }")
    try:
        gui_theme.ensure_theme_applied()
    finally:
        if qt_app.styleSheet() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert qt_app.styleSheet() == expected, (
        "ensure_theme_applied left the replacement sheet in place"
    )


def test_ensure_theme_applied_keeps_a_non_default_scale(qt_app):
    """A bare call must not downgrade the recorded scale or density."""
    gui_theme.apply_theme(
        qt_app, color_style="standard", text_scale=150, density="compact")
    try:
        sheet_150 = qt_app.styleSheet()
        assert gui_theme.current_text_scale() == 150
        assert gui_theme.current_density() == "compact"
        gui_theme.ensure_theme_applied()
        assert gui_theme.current_text_scale() == 150
        assert gui_theme.current_density() == "compact"
        assert qt_app.styleSheet() == sheet_150, (
            "ensure_theme_applied installed the 100% sheet over a 150% session"
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


def test_applying_the_theme_twice_keeps_the_stylesheet_without_repolish(qt_app):
    """The shortcut's only win is skipping the live-widget re-polish."""
    from unittest import mock

    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = qt_app.styleSheet()
    with mock.patch.object(qt_app, "setStyleSheet") as setter:
        gui_theme.apply_theme(qt_app, color_style="standard")
    setter.assert_not_called()
    assert qt_app.styleSheet() == expected


def test_reapplying_the_theme_restores_a_replaced_application_font(qt_app):
    """The shortcut must validate the font, not just the style sheet."""
    from qtpy.QtGui import QFont

    gui_theme.apply_theme(qt_app, color_style="standard")
    expected = gui_theme.ui_font("body")
    drifted = QFont(expected)
    drifted.setPointSizeF(42.0)
    qt_app.setFont(drifted)
    assert qt_app.font() != expected
    try:
        gui_theme.apply_theme(qt_app, color_style="standard")
    finally:
        if qt_app.font() != expected:
            gui_theme.apply_theme(qt_app, color_style="standard")
    assert qt_app.font() == expected, (
        "reapplying the theme left the replaced 42 pt application font in place"
    )


def test_chrome_stylesheet_is_memoized_per_theme_scale_density(qt_app):
    """Repeat scale changes must not regenerate the same sheet text."""
    first = gui_theme.chrome_qss(
        gui_theme.theme_for_color_style("standard"), text_scale=150)
    second = gui_theme.chrome_qss(
        gui_theme.theme_for_color_style("standard"), text_scale=150)
    assert first == second
    gui_theme.apply_theme(qt_app, color_style="standard", text_scale=150)
    assert qt_app.styleSheet() == first
    gui_theme.apply_theme(qt_app, color_style="standard")


# ---------------------------------------------------------------------------
# Live theme switching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "style,expected",
    [
        ("standard", T.GRAPHITE_DARK.name),
        ("inverted", T.PAPER_LIGHT.name),
        ("protanopia", T.PROTANOPIA_DARK.name),
    ],
)
def test_switching_palette_retheme_the_application(qt_app, style, expected):
    """Chrome follows the canvas palette, live, without a restart."""
    applied = gui_theme.apply_theme(qt_app, color_style=style)
    assert applied.name == expected
    window_bg = qt_app.palette().color(qt_app.palette().ColorRole.Window).name().lower()
    assert window_bg == applied.surface.lower()
    assert applied.surface in qt_app.styleSheet()


def test_light_palette_produces_light_chrome(qt_app):
    """The dark-picker / light-viewer split is what this replaces."""
    gui_theme.apply_theme(qt_app, color_style="inverted")
    assert gui_theme.current_theme().is_dark is False
    gui_theme.apply_theme(qt_app, color_style="standard")
    assert gui_theme.current_theme().is_dark is True


@pytest.mark.parametrize("scale", T.TEXT_SCALE_OPTIONS)
def test_application_text_scale_updates_programmatic_and_stylesheet_fonts(
    qt_app, scale
):
    """Painter fonts and ordinary widgets must consume the same live scale."""
    try:
        gui_theme.apply_theme(
            qt_app, color_style="standard", text_scale=scale
        )
        assert gui_theme.current_text_scale() == scale
        assert gui_theme.ui_font("body").pointSizeF() == pytest.approx(
            T.FONT_PT["body"] * scale / 100.0
        )
        assert (
            f"font-size: {T.FONT_PT['body'] * scale / 100.0}pt;"
            in qt_app.styleSheet()
        )
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")


# ---------------------------------------------------------------------------
# Font survival
# ---------------------------------------------------------------------------


def test_bundled_chrome_families_register(qt_app):
    """Space Grotesk and JetBrains Mono ship with the package."""
    families = gui_theme.install_chrome_fonts()
    assert "Space Grotesk" in families
    assert "JetBrains Mono" in families


def test_ui_font_survives_a_forced_qfont_family(qt_app, monkeypatch):
    """``render.install_font`` replaces ``QFont`` and rewrites the family.

    It runs when the first sounding opens, i.e. after the picker is on screen.
    ``ui_font``/``mono_font`` must therefore set the family *after*
    construction, since only the constructor is intercepted.
    """

    class _ForcedFont(QFont):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setFamily("Forced Substitute")

    monkeypatch.setattr("sharpmod.gui_theme.QFont", _ForcedFont)

    assert gui_theme.ui_font("body").family() == "Space Grotesk"
    assert gui_theme.mono_font("small").family() == "JetBrains Mono"


def test_stylesheet_font_outranks_the_application_font(qt_app):
    """The style sheet carries the chrome family, not just the app font.

    ``app.setFont`` is overwritten by ``render.install_font`` mid-session, so
    the style-sheet declaration is what keeps the picker's typography stable.
    """
    gui_theme.apply_theme(qt_app, color_style="standard")
    qt_app.setFont(QFont("Forced Substitute", 7))
    assert "Space Grotesk" in qt_app.styleSheet()
