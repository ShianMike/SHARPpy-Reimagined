"""The Skew-T labels the same thermodynamic levels whatever panel is showing.

The vendored widget makes two sets of annotations mutually exclusive through a
single ``if self.plotdgz ... else ...`` in ``plotData``:

* the ``if`` branch draws the dendritic growth zone on the temperature trace, its
  bounding ticks, and the **freezing level** and **wet-bulb zero** labels;
* the ``else`` branch draws the maximum lapse-rate layer and the parcel's 0, -20
  and -30 degree levels.

``plotdgz`` is on only while the winter panel happens to be the one on display,
so choosing a panel silently changed which levels the Skew-T would label. Pick
the winter panel and the freezing level appeared while the lapse-rate layer
vanished; pick anything else and the reverse. None of that follows from the
physics -- the freezing level and wet-bulb zero are read for hail size,
precipitation type and icing regardless of which numbers are in the corner.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtGui

from sharpmod import gui_viewer
from sharpmod import render as render_mod
from sharpmod.viz.SPCWindow import attach_thermal_levels

EXAMPLE = "hrrr_point_36.68N_95.66W_f018.npz"

#: Every panel the sounding window can show in its swappable slot.
PANELS = ("STP STATS", "FIRE", "WINTER", "SHIP", "COND STP", "VROT")


@pytest.fixture(scope="module")
def skewt_window(qt_app, tmp_path_factory):
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip("bundled example sounding is not present")

    render_mod.install_render_patches()
    prof_col, _stn_id = render_mod.decode(str(example))
    config = render_mod.build_config(str(tmp_path_factory.mktemp("thermal")))
    win, controller = compose_window(config, prof_col, mount=True)
    win.resize(1630, 1100)
    qt_app.processEvents()
    gui_viewer._install_view_controls(win)
    gui_viewer._install_panel_menu(win)
    try:
        yield win
    finally:
        win.close()
        del controller


class _Annotations:
    """Records which annotation helpers a repaint actually reaches."""

    def __init__(self, skewt):
        self._skewt = skewt
        self.ticks = []
        self.traces = []

    def __enter__(self):
        skewt = self._skewt
        self._sig = skewt.draw_sig_levels
        self._temps = skewt.draw_temp_levels
        self._lapse = skewt.draw_max_lapse_rate_layer
        self._trace = skewt.drawTrace
        record = self

        def sig(qp, plevel=1000, color=None, var_id=""):
            record.ticks.append(var_id or "band")
            return record._sig(qp, plevel=plevel, color=color, var_id=var_id)

        def temps(qp):
            record.ticks.append("temp_levels")
            return record._temps(qp)

        def lapse(qp, bound=4.5):
            record.ticks.append("max_lapse_rate")
            return record._lapse(qp, bound)

        def trace(data, colour, qp, p=None, **kwargs):
            try:
                same = (QtGui.QColor(colour).name()
                        == QtGui.QColor(skewt.dgz_color).name())
                if same and kwargs.get("label") is False:
                    record.traces.append("dgz_band")
            except Exception:
                pass
            return record._trace(data, colour, qp, p=p, **kwargs)

        skewt.draw_sig_levels = sig
        skewt.draw_temp_levels = temps
        skewt.draw_max_lapse_rate_layer = lapse
        skewt.drawTrace = trace
        return self

    def __exit__(self, *_exc):
        skewt = self._skewt
        skewt.draw_sig_levels = self._sig
        skewt.draw_temp_levels = self._temps
        skewt.draw_max_lapse_rate_layer = self._lapse
        skewt.drawTrace = self._trace
        return False


def _repaint(win, panel):
    """Show ``panel``, repaint the Skew-T, and report what it annotated."""
    gui_viewer.show_sounding_panel(win, panel)
    skewt = win.spc_widget.sound
    with _Annotations(skewt) as record:
        skewt.plotData()
    return record


def test_the_pass_is_mounted_on_the_skewt(skewt_window):
    assert getattr(skewt_window.spc_widget.sound,
                   "_sharpmod_thermal_levels_attached", False) is True


# --------------------------------------------------------------------------- #
# The levels no longer depend on the panel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("panel", PANELS)
def test_the_freezing_level_is_labelled_on_every_panel(skewt_window, panel):
    assert "FRZ=" in _repaint(skewt_window, panel).ticks


@pytest.mark.parametrize("panel", PANELS)
def test_the_wet_bulb_zero_is_labelled_on_every_panel(skewt_window, panel):
    assert "WBZ=" in _repaint(skewt_window, panel).ticks


@pytest.mark.parametrize("panel", PANELS)
def test_the_growth_zone_is_drawn_on_every_panel(skewt_window, panel):
    assert "dgz_band" in _repaint(skewt_window, panel).traces


# --------------------------------------------------------------------------- #
# ...and nothing the other branch used to draw was traded away for them
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("panel", PANELS)
def test_the_parcel_temperature_levels_survive(skewt_window, panel):
    """The 0/-20/-30 degree levels used to vanish on the winter panel."""
    assert "temp_levels" in _repaint(skewt_window, panel).ticks


@pytest.mark.parametrize("panel", PANELS)
def test_the_maximum_lapse_rate_layer_survives(skewt_window, panel):
    assert "max_lapse_rate" in _repaint(skewt_window, panel).ticks


# --------------------------------------------------------------------------- #
# No double-drawing where the vendored branch already did the work
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["FRZ=", "WBZ=", "temp_levels",
                                  "max_lapse_rate"])
def test_nothing_is_annotated_twice_on_the_winter_panel(skewt_window, kind):
    """The winter panel is where both routes are live at once."""
    record = _repaint(skewt_window, "WINTER")

    assert record.ticks.count(kind) == 1


def test_the_growth_zone_band_is_drawn_once_on_the_winter_panel(skewt_window):
    record = _repaint(skewt_window, "WINTER")

    assert record.traces.count("dgz_band") == 1


# --------------------------------------------------------------------------- #
# The seam itself
# --------------------------------------------------------------------------- #
def test_attaching_twice_is_a_no_op(skewt_window):
    """Both the mount and a caller could reach for this."""
    skewt = skewt_window.spc_widget.sound
    wrapped = skewt.plotData

    assert attach_thermal_levels(skewt) is True
    assert skewt.plotData is wrapped


def test_a_widget_without_a_repaint_hook_is_refused():
    from types import SimpleNamespace

    assert attach_thermal_levels(SimpleNamespace()) is False
    assert attach_thermal_levels(None) is False


def test_a_failing_annotation_cannot_break_the_repaint(skewt_window):
    """An annotation is never worth losing the sounding for.

    Scoped to this pass. The vendored ``if plotdgz`` branch calls the same helper
    unguarded, so a panel where *that* branch is live would raise from inside
    upstream code and there is nothing this seam can do about it. A panel where
    only this pass draws the levels is the case under test.
    """
    skewt = skewt_window.spc_widget.sound
    gui_viewer.show_sounding_panel(skewt_window, "STP STATS")
    assert getattr(skewt, "plotdgz", False) is not True, \
        "sanity: the vendored branch must be the inactive one here"
    original = skewt.draw_sig_levels

    def boom(*_args, **_kwargs):
        raise RuntimeError("no levels for you")

    skewt.draw_sig_levels = boom
    try:
        skewt.plotData()          # must not raise
    finally:
        skewt.draw_sig_levels = original
