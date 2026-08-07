"""Regression tests for SHARPpy Reimagined hodograph context-menu actions."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtCore import QPoint

from sharpmod import render as render_mod
from sharpmod.tests._examples import examples_dir
from sharpmod.viz.SPCWindow import compose_window


def _action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    raise AssertionError(f"missing menu action: {text}")


def test_hodograph_context_menu_interpolates_focused_profile(qt_app, tmp_path):
    """The hodograph right-click menu mirrors the Profiles interpolation action."""
    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    render_mod.install_font(qt_app)
    render_mod._install_hodo_interpolation_menu()

    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path))
    win, controller = compose_window(config, prof_col, mount=False)
    qt_app.processEvents()

    try:
        hodo = win.spc_widget.hodo
        interp = _action(hodo.popupmenu, "Interpolate Focused Profile")
        reset = _action(hodo.popupmenu, "Reset Interpolation")

        assert win.spc_widget.isInterpolated() is False
        assert interp.isVisible() is True
        assert reset.isVisible() is False

        interp.trigger()
        qt_app.processEvents()

        assert win.spc_widget.isInterpolated() is True

        hodo.showCursorMenu(QPoint(1, 1))
        qt_app.processEvents()
        hodo.popupmenu.hide()

        assert interp.isVisible() is False
        assert reset.isVisible() is True

        reset.trigger()
        qt_app.processEvents()

        assert win.spc_widget.isInterpolated() is False
    finally:
        win.close()
        controller.close()
