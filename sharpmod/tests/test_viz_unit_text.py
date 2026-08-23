"""Tests for compact unit suffix rendering in sounding value rows."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtGui, QtWidgets

from sharpmod.viz.unit_text import (
    RENDER_FONT_DEFAULT_HINTING,
    RENDER_FONT_SCALED_HINTING,
    RENDER_FONT_STYLE_STRATEGY,
    apply_render_font_quality,
    scaled_export_active,
    set_scaled_export,
    split_value_unit,
    value_unit_width,
)


def test_split_value_unit_recognizes_sounding_suffixes():
    assert split_value_unit("14.86 g/kg") == ("14.86", " g/kg")
    assert split_value_unit("245/23 kt") == ("245/23", " kt")
    assert split_value_unit("90\u00b0F") == ("90", "\u00b0F")
    assert split_value_unit("Supercell Comp = ") is None


def test_compact_unit_width_is_smaller_than_full_value_width():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _ = app
    font = QtGui.QFont("Helvetica")
    font.setPixelSize(13)
    metrics = QtGui.QFontMetrics(font)

    assert value_unit_width(font, "14.86 g/kg") < metrics.horizontalAdvance("14.86 g/kg")


def test_font_quality_hinting_is_applied_only_for_scaled_export():
    """Hinting choice is scale-dependent because it was measured that way.

    MEASURED on real renders, as the share of inked pixels fully on (higher is
    crisper): vertical-only hinting scored 0.202 vs 0.226 at 1x (worse) but
    0.372 vs 0.357 at 2x and 0.568 vs 0.527 at 2.8x (better). Applying it
    globally would therefore have degraded the original-size export, so the flag
    exists to keep 1x on Qt's default.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _ = app

    previous = set_scaled_export(False)
    try:
        unscaled = apply_render_font_quality(QtGui.QFont("Helvetica"))
        assert unscaled.styleStrategy() == RENDER_FONT_STYLE_STRATEGY
        # 1x keeps Qt's default hinting, which measured crisper there.
        assert unscaled.hintingPreference() == RENDER_FONT_DEFAULT_HINTING
        assert RENDER_FONT_DEFAULT_HINTING != RENDER_FONT_SCALED_HINTING

        set_scaled_export(True)
        assert scaled_export_active() is True
        scaled = apply_render_font_quality(QtGui.QFont("Helvetica"))
        assert scaled.styleStrategy() == RENDER_FONT_STYLE_STRATEGY
        assert scaled.hintingPreference() == RENDER_FONT_SCALED_HINTING

        # An explicit override wins over the ambient flag in both directions,
        # and is idempotent: a font built while scaled export was active must be
        # returnable to unscaled behaviour rather than keeping scaled hinting.
        forced_off = apply_render_font_quality(
            QtGui.QFont("Helvetica"), scaled=False
        )
        assert forced_off.hintingPreference() == RENDER_FONT_DEFAULT_HINTING
        set_scaled_export(False)
        forced_on = apply_render_font_quality(
            QtGui.QFont("Helvetica"), scaled=True
        )
        assert forced_on.hintingPreference() == RENDER_FONT_SCALED_HINTING
        # Round-tripping returns to the default rather than sticking.
        assert (
            apply_render_font_quality(forced_on, scaled=False).hintingPreference()
            == RENDER_FONT_DEFAULT_HINTING
        )
    finally:
        set_scaled_export(previous)


def test_density_context_declares_and_restores_scaled_export():
    """The render path must set the flag before any widget font is built."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _ = app
    from sharpmod import render as render_mod

    assert scaled_export_active() is False
    with render_mod._target_density_pixmaps(2.8):
        assert scaled_export_active() is True
    assert scaled_export_active() is False

    # Original-size export leaves the default hinting in place.
    with render_mod._target_density_pixmaps(1.0):
        assert scaled_export_active() is False
    assert scaled_export_active() is False
