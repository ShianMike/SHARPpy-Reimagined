"""Regression tests for the default hodograph center mode."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sharpmod import render as render_mod
from sharpmod.tests._examples import examples_dir
from sharpmod.viz.SPCWindow import compose_window


def _action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    raise AssertionError(f"missing menu action: {text}")


def _assert_vector_is_at_widget_center(hodo, vector):
    x, y = hodo.uv_to_pix(vector[0], vector[1])
    assert float(x) == pytest.approx(hodo.wid / 2.0, abs=0.75)
    assert float(y) == pytest.approx(hodo.hgt / 2.0, abs=0.75)


def test_hodograph_defaults_to_mean_wind_and_preserves_manual_normal_mode(
    qt_app, tmp_path
):
    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    render_mod.install_font(qt_app)
    render_mod.install_render_patches()

    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path))
    win, controller = compose_window(config, prof_col, mount=False)
    win.resize(1500, 950)
    win.show()
    qt_app.processEvents()

    try:
        hodo = win.spc_widget.hodo
        normal = _action(hodo.popupmenu, "Normal")
        mean_wind = _action(hodo.popupmenu, "Mean Wind")

        assert render_mod.HODO_ZOOM_KTS == pytest.approx(160.0)
        assert hodo.hodomag == pytest.approx(render_mod.HODO_ZOOM_KTS)
        assert hodo.center_loc == "meanwind"
        assert mean_wind.isChecked() is True
        assert normal.isChecked() is False
        _assert_vector_is_at_widget_center(hodo, hodo.mean_lcl_el)

        # A resize rebuilds the vendored background around the origin. The
        # selected default must be restored for GUI resizes and render growth.
        hodo.resize(hodo.width() + 37, hodo.height() + 19)
        qt_app.processEvents()
        assert hodo.center_loc == "meanwind"
        _assert_vector_is_at_widget_center(hodo, hodo.mean_lcl_el)

        # "Default" must not mean "forced": a user's later menu selection is
        # retained across profile refreshes rather than reset to Mean Wind.
        normal.trigger()
        qt_app.processEvents()
        hodo.setActiveCollection(hodo.pc_idx)
        qt_app.processEvents()

        assert hodo.center_loc == "centered"
        assert normal.isChecked() is True
        assert mean_wind.isChecked() is False
        _assert_vector_is_at_widget_center(hodo, (0.0, 0.0))
    finally:
        win.close()
        controller.close()
