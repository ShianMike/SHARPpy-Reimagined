"""Opaque label plates stay suppressed, and the ``SFC`` label stays drawn.

Both of these are one-line regressions waiting to happen: the plates come back
if a wrapper is dropped, and the surface label disappears again the moment
clipping is left enabled around it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sharpmod.render import _RectSuppressingPainter

EXAMPLE = "hrrr_point_36.68N_95.66W_f018.npz"


class _Recorder:
    """Counts painter calls while forwarding nothing."""

    def __init__(self):
        self.rects = []
        self.texts = []
        self.clipping = []

    def drawRect(self, *args):  # noqa: N802 - Qt API name
        self.rects.append(args)

    def drawText(self, *args):  # noqa: N802 - Qt API name
        self.texts.append(str(args[-1]))

    def setClipping(self, enabled):  # noqa: N802 - Qt API name
        self.clipping.append(bool(enabled))

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


# --- the suppressing painter ---------------------------------------------- #

def test_suppressing_painter_blocks_only_draw_rect():
    recorder = _Recorder()
    painter = _RectSuppressingPainter(recorder)

    painter.drawRect(1, 2, 3, 4)
    painter.drawText("rect", "flags", "kept")

    assert recorder.rects == []
    assert recorder.texts == ["kept"]


def test_suppressing_painter_forwards_unknown_calls():
    """It must stay a transparent proxy, or vendored drawing breaks."""
    recorder = _Recorder()
    painter = _RectSuppressingPainter(recorder)

    painter.setClipping(False)

    assert recorder.clipping == [False]


# --- mounted-window regressions ------------------------------------------- #

@pytest.fixture
def mounted(qt_app, tmp_path):
    from sharpmod import render as render_mod
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip(f"{EXAMPLE} unavailable")
    render_mod.install_font(qt_app)
    # The plate suppression lives in the patch registry, so a test that does
    # not install it would pass against the unpatched vendored code.
    installed = render_mod.install_render_patches()
    assert "hodo.storm-motion-label-transparency" in installed
    prof_col, _stn = render_mod.decode(str(example))
    win, controller = compose_window(
        render_mod.build_config(str(tmp_path)), prof_col, mount=True)
    win.resize(1630, 1100)
    qt_app.processEvents()
    try:
        yield win.spc_widget
    finally:
        win.close()
        controller.close()


def test_storm_motion_labels_paint_no_plate(mounted, qt_app):
    """``drawSMV`` positions RM/LM with rects it means to be invisible.

    Upstream tries to hide them with an alpha-zero pen but never clears the
    brush, so they were filled by whatever brush the previous call left active.
    """
    hodo = mounted.hodo
    recorder = _Recorder()

    type(hodo).drawSMV(hodo, recorder)

    assert recorder.rects == []
    assert any("RM" in text for text in recorder.texts)
    assert any("LM" in text for text in recorder.texts)


def test_storm_motion_draw_restores_the_background_alpha(mounted, qt_app):
    """Upstream mutates ``self.bg_color`` in place; it must not stay mutated."""
    hodo = mounted.hodo
    before = hodo.bg_color.alpha()

    type(hodo).drawSMV(hodo, _Recorder())

    assert hodo.bg_color.alpha() == before


def test_effective_layer_draws_the_surface_label(mounted, qt_app):
    """A surface-based inflow layer labels its bottom ``SFC``.

    The label sits below the layer's lower line, which for a surface-based layer
    is at or under the plot's bottom edge, so it is only drawn if clipping is
    lifted around it.
    """
    skewt = mounted.sound
    prof = skewt.prof
    if prof.pres[prof.sfc] != prof.ebottom:
        pytest.skip("example's inflow layer is not surface based")
    recorder = _Recorder()

    type(skewt).draw_effective_layer(skewt, recorder)

    assert "SFC" in recorder.texts
    # Clipping is turned off for the surface label and back on afterwards.
    assert False in recorder.clipping
    assert recorder.clipping[-1] is True


def test_effective_layer_still_draws_top_and_helicity(mounted, qt_app):
    skewt = mounted.sound
    recorder = _Recorder()

    type(skewt).draw_effective_layer(skewt, recorder)

    assert any(text.endswith("m") for text in recorder.texts)
    assert any("m2s2" in text for text in recorder.texts)


def test_effective_layer_paints_no_plate(mounted, qt_app):
    """The three label rects position text; they must not be filled."""
    skewt = mounted.sound
    recorder = _Recorder()

    type(skewt).draw_effective_layer(skewt, recorder)

    assert recorder.rects == []
