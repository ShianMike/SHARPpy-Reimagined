"""Streamwiseness calculation, inset rendering, and layout regression tests."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtCore, QtGui, QtWidgets

from sharpmod.viz.streamwiseness import plotStreamwiseness, streamwiseness_profile


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _circular_profile(*, clockwise=False):
    height = np.arange(0.0, 6000.0 + 500.0, 500.0)
    phase = height / 6000.0 * (np.pi / 2.0)
    if clockwise:
        phase = -phase
    return SimpleNamespace(
        hght=height,
        u=20.0 * np.cos(phase),
        v=20.0 * np.sin(phase),
        sfc=0,
        srwind=(0.0, 0.0, 0.0, 0.0),
    )


def _representative_profile():
    height = np.arange(0.0, 6500.0, 500.0)
    return SimpleNamespace(
        hght=height + 250.0,
        u=np.linspace(5.0, 45.0, height.size),
        v=12.0 * np.sin(height / 1600.0),
        sfc=0,
        srwind=(10.0, -8.0, -10.0, 8.0),
    )


def _mixed_sign_profile():
    height = np.arange(0.0, 6000.0 + 250.0, 250.0)
    phase = np.where(
        height <= 3000.0,
        -height / 3000.0 * (np.pi / 2.0),
        -np.pi / 2.0 + (height - 3000.0) / 3000.0 * (np.pi / 2.0),
    )
    return SimpleNamespace(
        hght=height + 125.0,
        u=20.0 * np.cos(phase),
        v=20.0 * np.sin(phase),
        sfc=0,
        srwind=(0.0, 0.0, 0.0, 0.0),
    )


def test_streamwiseness_profile_is_near_100_for_streamwise_circle():
    result = streamwiseness_profile(_circular_profile(clockwise=True))

    assert result is not None
    assert np.nanmean(result.percent[1:-1]) > 98.0
    assert np.all(result.signed_percent[1:-1] > 0.0)


def test_streamwiseness_profile_preserves_anticyclonic_sign():
    result = streamwiseness_profile(_circular_profile())

    assert result is not None
    assert np.nanmean(result.percent[1:-1]) > 98.0
    assert np.all(result.signed_percent[1:-1] < 0.0)


def test_streamwiseness_profile_returns_none_without_storm_motion():
    prof = SimpleNamespace(
        hght=np.array([0.0, 1000.0]),
        u=np.array([1.0, 2.0]),
        v=np.array([2.0, 3.0]),
        sfc=0,
    )

    assert streamwiseness_profile(prof) is None


def test_streamwiseness_profile_is_bounded_and_uses_agl_height():
    result = streamwiseness_profile(_representative_profile())

    assert result is not None
    assert result.height_km[0] == pytest.approx(0.0)
    assert result.height_km[-1] == pytest.approx(6.0)
    assert np.all((result.percent >= 0.0) & (result.percent <= 100.0))


def test_plot_streamwiseness_accepts_sharppy_widget_contract(qt_app):
    widget = plotStreamwiseness()
    widget.resize(250, 360)
    widget.setProf(_mixed_sign_profile())
    widget.setPreferences(
        update_gui=True,
        bg_color="#000000",
        fg_color="#ffffff",
    )
    widget.setDeviant("left")
    qt_app.processEvents()

    assert widget.use_left is True
    assert widget.data is not None
    assert widget.grab().toImage().isNull() is False


def test_plot_streamwiseness_draws_reference_labels_and_both_sign_fills(qt_app):
    widget = plotStreamwiseness()
    widget.resize(250, 360)
    widget.setProf(_mixed_sign_profile())
    labels = []
    text_colors = []
    original = widget._draw_text

    def capture(qp, rect, text, *args, **kwargs):
        labels.append(text)
        color = args[0] if args else kwargs.get("color")
        text_colors.append(color)
        return original(qp, rect, text, *args, **kwargs)

    widget._draw_text = capture
    widget._redraw()
    qt_app.processEvents()

    assert "Streamwiseness" in labels
    assert "Streamwiseness (%)" in labels
    assert "Height AGL (km)" in labels
    assert "Cyclonic" in labels
    assert "Anticyclonic" in labels
    assert any(str(label).endswith("%") for label in labels)
    assert labels.count(0) == 1
    assert widget.text_color.name() == "#ffffff"
    assert all(
        color is None or QtGui.QColor(color).name() == "#ffffff"
        for color in text_colors
    )

    plot = widget._geometry()
    assert widget.width() - plot.right() == pytest.approx(25)
    assert widget._legend_rect.top() == pytest.approx(plot.top() + 2)
    assert widget._legend_rect.right() == pytest.approx(plot.right() - 2)
    assert len(widget._border_lines) == 1
    border = widget._border_lines[0]
    assert border.x1() == pytest.approx(0.5)
    assert border.x2() == pytest.approx(0.5)
    assert border.y1() == pytest.approx(0.5)
    assert border.y2() == pytest.approx(widget.height() - 0.5)

    image = widget.plotBitMap.toImage().convertToFormat(
        widget.plotBitMap.toImage().Format.Format_RGB32)
    pixels = [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
    ]
    assert any(c.green() > 120 and c.green() > c.red() * 1.4 for c in pixels)
    assert any(c.red() > 25 and c.red() > c.blue() * 1.3 for c in pixels)
    assert any(c.blue() > 25 and c.blue() > c.red() * 1.3 for c in pixels)


def test_streamwiseness_interior_vertical_grid_lines_stay_dashed(qt_app):
    widget = plotStreamwiseness()
    widget.resize(250, 360)
    plot = widget._geometry()

    class RecordingPainter:
        def __init__(self):
            self.pen = QtGui.QPen()
            self.vertical_lines = []

        def setPen(self, pen):
            self.pen = QtGui.QPen(pen)

        def setFont(self, _font):
            pass

        def drawLine(self, start, end):
            if start.x() == pytest.approx(end.x()):
                self.vertical_lines.append(QtGui.QPen(self.pen))

        def drawText(self, _rect, _align, _text):
            pass

    painter = RecordingPainter()
    widget._draw_grid(painter, plot, 8)

    # 0, 25, 50, and 75 are interior grid lines; 100 is the solid edge.
    styles = [pen.style() for pen in painter.vertical_lines]
    assert styles[1:4] == [QtCore.Qt.PenStyle.DashLine] * 3
    assert styles[4] == QtCore.Qt.PenStyle.SolidLine
    assert painter.vertical_lines[4].color().name() == "#ffffff"


def test_plot_streamwiseness_renders_missing_indicator(qt_app):
    widget = plotStreamwiseness()
    widget.resize(250, 360)
    labels = []
    widget._draw_text = lambda _qp, _rect, text, *args, **kwargs: labels.append(text)
    widget.setProf(SimpleNamespace())

    assert "--" in labels


def test_streamwiseness_is_mounted_immediately_left_of_narrowed_stp(
        qt_app, tmp_path):
    from sharpmod import render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    render_mod.install_font(qt_app)
    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path))
    win, controller = compose_window(config, prof_col, mount=True)
    win.resize(1630, 1100)
    qt_app.processEvents()

    try:
        sw = win.spc_widget
        result = win.sharpmod_products
        stream = result.streamwiseness
        stp = sw.right_inset_ob
        grid = sw.grid3
        stream_pos = grid.getItemPosition(grid.indexOf(stream))
        stp_pos = grid.getItemPosition(grid.indexOf(stp))

        assert stream is sw.streamwiseness
        assert stream.data is not None
        assert stream_pos[1] == 3
        assert stp_pos[1] == 4
        assert stream.width() < stp.width() < sw.index_board.width()
        assert sw.insets["SHARPMOD STREAMWISENESS"] is stream
        assert sw.text.objectName() != "sharpmod_bottom_band"
        assert "border-width: 2px" in sw.text.styleSheet()
        assert sw.index_board._outer_border_lines == ()
        stream_plot = stream._geometry()
        assert stream.width() - stream_plot.right() == pytest.approx(25)

        sw.toggleVector("left")
        assert stream.use_left is True
        sw.toggleVector("right")
        assert stream.use_left is False

        sw.makeInsetMenu(sw.left_inset, sw.right_inset)
        ship_action = next(
            action for action in sw.menu_ag.actions()
            if action.data() == "SHIP")
        ship_action.setChecked(True)
        sw.inset_to_swap = "RIGHT"
        sw.swapInset()
        qt_app.processEvents()
        swapped_pos = sw.grid3.getItemPosition(
            sw.grid3.indexOf(sw.right_inset_ob))
        assert swapped_pos[1] == 4
        assert sw.grid3.getItemPosition(sw.grid3.indexOf(stream))[1] == 3
    finally:
        win.close()
        controller.close()


def test_family_panel_growth_ignores_stale_pre_layout_text_height(
        qt_app, tmp_path):
    from sharpmod import render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("HRRR .npz example unavailable")

    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path))
    win, controller = compose_window(config, prof_col, mount=True)

    try:
        sw = win.spc_widget
        text = sw.text
        stale_height = text.height()
        render_mod.apply_layout_compensation(sw)
        settled_hint = max(
            1,
            text.minimumHeight(),
            text.minimumSizeHint().height(),
            text.sizeHint().height(),
        )
        assert stale_height > settled_hint

        render_mod._grow_for_family_panels(win)

        assert text.minimumHeight() == (
            settled_hint + render_mod.CHART_HEIGHT_GROW)
    finally:
        win.close()
        controller.close()
