"""The Skew-T's left-hand annotations are placed against each other.

The max-lapse-rate value used to be anchored five degrees warm of the
temperature trace, which is where the parcel levels, the freezing level and
wet-bulb zero, the significant-level ticks and the wind barbs all already are.
It now lives in the cold-side gutter beside the omega meter and the effective
inflow layer -- a region with room, but not unlimited room, so the three have to
be measured against one another rather than assumed to fit.

Geometry needs a real widget size. The Skew-T inside the SPC grid cannot be
resized while it has a parent, and its ``initUI`` reads ``size()`` to build
every scale, so these tests detach it first.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtCore, QtGui  # noqa: E402

from sharpmod import render as render_mod  # noqa: E402


EXAMPLE = "hrrr_point_36.68N_95.66W_f018.npz"

#: Sizes spanning a maximised window down to a cramped one.
SIZES = [(900, 700), (1200, 850), (700, 560), (560, 460)]


class _Recorder:
    """Capture geometry, text, and pen colour from a draw routine."""

    def __init__(self):
        self.rects = []
        self.texts = []
        self.lines = []
        self.colors = []
        self.font = None

    def setClipping(self, *_args):  # noqa: N802 - Qt API name
        pass

    def setBrush(self, *_args):  # noqa: N802 - Qt API name
        pass

    def setPen(self, pen):  # noqa: N802 - Qt API name
        color = getattr(pen, "color", None)
        if callable(color):
            self.colors.append(color())

    def setFont(self, font):  # noqa: N802 - Qt API name
        self.font = QtGui.QFont(font)

    def drawRect(self, *args):  # noqa: N802 - Qt API name
        self.rects.append(args)

    def drawLine(self, *args):  # noqa: N802 - Qt API name
        if len(args) >= 4:
            self.lines.append(tuple(float(value) for value in args[:4]))

    def drawPath(self, *_args):  # noqa: N802 - Qt API name
        pass

    def drawText(self, *args):  # noqa: N802 - Qt API name
        if args and hasattr(args[0], "x"):
            self.texts.append((args[0], str(args[-1]), self.font))
        elif len(args) >= 6:
            # ``draw_height`` uses the numeric (x, y, w, h, flags, text) form
            # rather than a rect, so a rect-only recorder captures nothing.
            x, y, width, height = (float(value) for value in args[:4])
            self.texts.append((
                QtCore.QRectF(x, y, width, height), str(args[-1]), self.font))

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None

    def boxes(self):
        """Return each label's *true* extent, not its positioning rect.

        The rects are drawn with ``TextDontClip``, so the rect is a minimum and
        the painted text can be wider. Collision has to be judged on what is
        actually inked.
        """
        result = []
        for rect, text, font in self.texts:
            metrics = QtGui.QFontMetrics(font) if font is not None else None
            advance = (float(metrics.horizontalAdvance(text)) if metrics
                       else float(rect.width()))
            result.append((text, QtCore.QRectF(
                float(rect.x()), float(rect.y()),
                max(advance, float(rect.width())), float(rect.height()))))
        return result


@pytest.fixture(scope="module")
def skewt(qt_app):
    """A detached, resizable Skew-T with the render patches installed."""
    from sharpmod.tests._examples import examples_dir
    from sharpmod.viz.SPCWindow import compose_window

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip(f"{EXAMPLE} unavailable")

    installed = render_mod.install_render_patches()
    assert "skewt.lapse-rate-label-placement" in installed
    prof_col, _stn = render_mod.decode(str(example))
    win, controller = compose_window(
        render_mod.build_config(os.devnull and "."), prof_col, mount=True)
    qt_app.processEvents()
    widget = win.spc_widget.sound
    widget.setParent(None)
    qt_app.processEvents()
    try:
        yield widget
    finally:
        widget.deleteLater()
        win.close()
        controller.close()


def _sized(skewt, qt_app, width, height):
    skewt.resize(width, height)
    skewt.initUI()
    qt_app.processEvents()
    return skewt


def _lapse(skewt):
    recorder = _Recorder()
    type(skewt).draw_max_lapse_rate_layer(skewt, recorder)
    return recorder


def _effective(skewt):
    recorder = _Recorder()
    type(skewt).draw_effective_layer(skewt, recorder)
    return recorder


# --------------------------------------------------------------------------- #
# The geometry helpers, in isolation
# --------------------------------------------------------------------------- #
class _FakeWidget:
    lpad = 40
    esrh_font = None
    hght_font = None
    plot_omega = True

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def tmpc_to_pix(self, tmpc, _pres):
        return 100.0 + (float(tmpc) + 50.0) * 5.0


#: Right edge of the height-marker column for ``_FakeWidget``, whose fonts are
#: absent so the measured fallback applies.
_FAKE_HEIGHT_BAND_RIGHT = (
    _FakeWidget.lpad + render_mod.SKEWT_HEIGHT_LABEL_OFFSET
    + render_mod.SKEWT_HEIGHT_LABEL_FALLBACK_PX
)


def test_the_height_marker_column_spans_its_tick_and_its_label():
    band = render_mod._skewt_height_label_band(_FakeWidget())

    assert band == (_FakeWidget.lpad, _FAKE_HEIGHT_BAND_RIGHT)


def test_the_height_marker_column_is_present_without_an_omega_meter():
    """``plotData`` draws these seven markers on every sounding."""
    with_meter = render_mod._skewt_height_label_band(_FakeWidget())
    without = render_mod._skewt_height_label_band(
        _FakeWidget(plot_omega=False))

    assert with_meter == without


def test_the_longest_height_label_sets_the_column_width():
    assert "15 km" in render_mod.SKEWT_HEIGHT_LABELS
    assert len(render_mod.SKEWT_HEIGHT_LABELS) == 7


def test_no_omega_band_when_the_meter_is_not_drawn():
    """Observed soundings carry no vertical velocity, so no meter is drawn."""
    assert render_mod._skewt_omega_band(_FakeWidget(plot_omega=False)) is None


def test_the_omega_band_spans_the_meters_own_bounds():
    band = render_mod._skewt_omega_band(_FakeWidget())

    assert band == (105.0, 145.0)


def test_a_non_finite_position_yields_no_band():
    class _Broken(_FakeWidget):
        def tmpc_to_pix(self, _tmpc, _pres):
            return float("nan")

    assert render_mod._skewt_omega_band(_Broken()) is None


class _BarWidget(_FakeWidget):
    """A widget whose omega bars overshoot the meter's own scale.

    ``draw_omega_profile`` scales each bar by the value, so it does not clip at
    the meter's bounds; ascent is drawn to the right, as here.
    """

    def __init__(self, omeg, pres=None):
        import numpy as np
        from types import SimpleNamespace

        super().__init__()
        count = len(omeg)
        self.prof = SimpleNamespace(
            omeg=np.ma.masked_invalid(np.ma.asarray(omeg, dtype=float)),
            pres=np.ma.asarray(
                pres if pres is not None else [1000.0] * count, dtype=float),
        )

    def omeg_to_pix(self, omeg):
        return 125.0 - float(omeg) * 4.0


def test_the_band_includes_bars_that_overshoot_the_meter_scale():
    """The nominal bounds alone are not the meter's real footprint."""
    nominal = render_mod._skewt_omega_band(_FakeWidget())
    band = render_mod._skewt_omega_band(_BarWidget([-5.0]))

    assert band[1] > nominal[1]
    assert band[1] >= 125.0 + 5.0 * 10.0 * 4.0


def test_levels_the_meter_does_not_draw_are_ignored():
    """It skips anything above its own top, so those bars do not count."""
    band = render_mod._skewt_omega_band(
        _BarWidget([-5.0, -50.0], pres=[1000.0, 50.0]))

    assert band[1] == pytest.approx(125.0 + 5.0 * 10.0 * 4.0)


def test_a_profile_with_no_usable_omega_falls_back_to_the_bounds():
    assert render_mod._skewt_omega_band(
        _BarWidget([float("nan")])) == render_mod._skewt_omega_band(
            _FakeWidget())


def test_a_widget_without_a_profile_still_reports_its_bounds():
    assert render_mod._skewt_omega_bar_extent(_FakeWidget()) is None


def test_the_gutter_clears_the_height_markers_when_no_meter_is_drawn():
    """The regression: an observed sounding has no meter, only these markers.

    Clearing the meter alone left the gutter at ``lpad`` plus a few pixels, so
    the maximum lapse rate was drawn hard against the left border and straight
    through the ``0 km``/``1 km``/``5 km`` labels.
    """
    gutter = render_mod._skewt_left_gutter(_FakeWidget(plot_omega=False))

    assert gutter == _FAKE_HEIGHT_BAND_RIGHT + \
        render_mod.SKEWT_ANNOTATION_CLEARANCE
    assert gutter > _FakeWidget.lpad + render_mod.SKEWT_ANNOTATION_CLEARANCE


def test_the_gutter_clears_the_meter_when_one_is_drawn():
    """The meter is the wider obstacle, so it decides this case."""
    gutter = render_mod._skewt_left_gutter(_FakeWidget())

    assert gutter == 145.0 + render_mod.SKEWT_ANNOTATION_CLEARANCE
    assert gutter > _FAKE_HEIGHT_BAND_RIGHT


def test_the_gutter_never_starts_at_the_plot_border():
    for widget in (_FakeWidget(), _FakeWidget(plot_omega=False)):
        gutter = render_mod._skewt_left_gutter(widget)
        assert gutter > widget.lpad + render_mod.SKEWT_ANNOTATION_CLEARANCE


def test_a_rect_with_no_blockers_is_left_alone():
    rect = QtCore.QRectF(10, 20, 30, 40)

    result = render_mod._skewt_clear_of(rect, (), QtCore)

    assert result == rect


def test_a_blocked_rect_moves_right_past_the_blocker():
    rect = QtCore.QRectF(10, 20, 30, 10)
    blocker = QtCore.QRectF(20, 20, 30, 10)

    result = render_mod._skewt_clear_of(rect, (blocker,), QtCore)

    assert result.x() == blocker.right() + render_mod.SKEWT_ANNOTATION_CLEARANCE
    assert not result.intersects(blocker)


def test_only_the_column_moves_because_height_carries_the_meaning():
    rect = QtCore.QRectF(10, 20, 30, 10)
    blocker = QtCore.QRectF(20, 20, 30, 10)

    result = render_mod._skewt_clear_of(rect, (blocker,), QtCore)

    assert (result.y(), result.height()) == (rect.y(), rect.height())


def test_a_rect_clear_in_y_is_not_moved():
    rect = QtCore.QRectF(10, 20, 30, 10)
    blocker = QtCore.QRectF(20, 200, 30, 10)

    assert render_mod._skewt_clear_of(rect, (blocker,), QtCore) == rect


def test_the_shift_stops_rather_than_leaving_the_plot():
    rect = QtCore.QRectF(10, 20, 30, 10)
    blocker = QtCore.QRectF(20, 20, 30, 10)

    result = render_mod._skewt_clear_of(
        rect, (blocker,), QtCore, right_limit=45)

    assert result == rect


def test_a_chain_of_blockers_is_cleared():
    rect = QtCore.QRectF(10, 20, 20, 10)
    blockers = (QtCore.QRectF(15, 20, 20, 10),
                QtCore.QRectF(40, 20, 20, 10))

    result = render_mod._skewt_clear_of(rect, blockers, QtCore)

    assert all(not result.intersects(blocker) for blocker in blockers)


# --------------------------------------------------------------------------- #
# Placement on a real widget
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_starts_at_the_gutter_or_right_of_it(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    gutter = render_mod._skewt_left_gutter(skewt, QtGui)

    boxes = _lapse(skewt).boxes()

    assert boxes, "the max-lapse-rate value should be drawn"
    for text, box in boxes:
        assert box.x() >= gutter - 0.6, f"{text!r} at {box.x():.1f} < {gutter:.1f}"


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_never_overlaps_the_omega_meter(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    band = render_mod._skewt_omega_band(skewt, QtGui)
    if band is None:
        pytest.skip("no omega meter on this profile")

    for text, box in _lapse(skewt).boxes():
        assert box.x() >= band[1], \
            f"{text!r} starts at {box.x():.1f}, inside the meter band {band}"


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_bracket_never_overlaps_the_omega_meter(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    band = render_mod._skewt_omega_band(skewt, QtGui)
    if band is None:
        pytest.skip("no omega meter on this profile")

    lines = _lapse(skewt).lines
    assert lines, "the bracket should still be drawn"
    for x1, _y1, x2, _y2 in lines:
        assert min(x1, x2) >= band[1], \
            f"bracket at x={min(x1, x2):.1f} is inside the meter band {band}"


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_stays_clear_of_the_traces(
        skewt, qt_app, width, height):
    """It moved off the crowded warm side; it must not creep back onto it.

    The bound that matters is the temperature and dewpoint traces rather than
    the diagram's midpoint: the free room in the cold region ends wherever the
    first trace is, and on a dry profile the dewpoint is much further left than
    on a saturated one.
    """
    import sharppy.sharptab as tab

    _sized(skewt, qt_app, width, height)
    _rate, pbot, ptop = skewt.prof.max_lapse_rate_2_6[:3]
    limit = render_mod._skewt_trace_left_edge(skewt, tab, pbot, ptop)
    assert limit is not None, "the traces should be measurable"

    for text, box in _lapse(skewt).boxes():
        assert box.right() <= limit, \
            f"{text!r} reaches {box.right():.1f}, past the trace at {limit:.1f}"


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_takes_the_column_right_of_the_inflow_labels(
        skewt, qt_app, width, height):
    """Its own column, rather than sharing the gutter with the inflow labels.

    Those labels sit at whatever height the inflow layer happens to be, so in a
    shared column they can come arbitrarily close to this one -- near enough to
    read as a single smear without ever strictly overlapping.
    """
    _sized(skewt, qt_app, width, height)
    inflow = _inflow_boxes(skewt)
    assert inflow, "the inflow annotation should be drawn"
    inflow_right = max(rect.right() for _text, rect in inflow)

    for text, box in _lapse(skewt).boxes():
        assert box.x() >= inflow_right, \
            f"{text!r} starts at {box.x():.1f}, left of the inflow labels " \
            f"ending at {inflow_right:.1f}"


def test_the_value_falls_back_to_the_gutter_when_the_traces_leave_no_room(
        skewt, qt_app, monkeypatch):
    """A saturated, cold profile can close that column entirely."""
    _sized(skewt, qt_app, 900, 700)
    gutter = render_mod._skewt_left_gutter(skewt, QtGui)
    inflow = _inflow_boxes(skewt)
    inflow_right = max(rect.right() for _text, rect in inflow)

    # Pretend the first trace sits just right of the inflow annotation.
    monkeypatch.setattr(
        render_mod, "_skewt_trace_left_edge",
        lambda *_a, **_k: inflow_right + 10.0)

    boxes = _lapse(skewt).boxes()

    assert boxes, "the value should still be drawn"
    for text, box in boxes:
        assert box.x() < inflow_right, \
            f"{text!r} should have retreated to the gutter"
        assert box.x() >= gutter - 0.6, text
        for inflow_text, rect in inflow:
            assert not box.intersects(rect), \
                f"{text!r} overlaps {inflow_text!r} in the fallback"


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_clears_every_effective_layer_label(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    lapse_boxes = _lapse(skewt).boxes()
    eff_boxes = _effective(skewt).boxes()

    for lapse_text, lapse_box in lapse_boxes:
        for eff_text, eff_box in eff_boxes:
            assert not lapse_box.intersects(eff_box), \
                f"{lapse_text!r} overlaps the inflow label {eff_text!r}"


@pytest.mark.parametrize("width,height", SIZES)
def test_every_effective_layer_label_starts_at_the_gutter_or_right_of_it(
        skewt, qt_app, width, height):
    """The fixed -33 C column can fall inside the meter on a narrow plot."""
    _sized(skewt, qt_app, width, height)
    gutter = render_mod._skewt_left_gutter(skewt, QtGui)

    for text, box in _effective(skewt).boxes():
        assert box.x() >= gutter - 0.6, \
            f"{text!r} at {box.x():.1f} < {gutter:.1f}"


@pytest.mark.parametrize("width,height", SIZES)
def test_every_effective_layer_label_clears_the_omega_meter(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    band = render_mod._skewt_omega_band(skewt, QtGui)
    if band is None:
        pytest.skip("no omega meter on this profile")

    for text, box in _effective(skewt).boxes():
        assert box.x() >= band[1], \
            f"{text!r} starts at {box.x():.1f}, inside the meter band {band}"


# --------------------------------------------------------------------------- #
# The annotation still says and marks the same thing
# --------------------------------------------------------------------------- #
def test_the_value_and_its_unit_are_unchanged(skewt, qt_app):
    _sized(skewt, qt_app, 900, 700)

    texts = [text for text, _box in _lapse(skewt).boxes()]

    assert len(texts) == 1
    assert texts[0].endswith(" C/km")
    assert float(texts[0].split()[0]) == pytest.approx(
        float(skewt.prof.max_lapse_rate_2_6[0]), abs=0.05)


def test_the_bracket_still_marks_the_layers_two_pressures(skewt, qt_app):
    """Only the column moved. The two heights are the meaning."""
    _sized(skewt, qt_app, 900, 700)
    _rate, pbot, ptop = skewt.prof.max_lapse_rate_2_6[:3]
    expected = {
        round(skewt.originy + skewt.pres_to_pix(pbot) / skewt.scale, 1),
        round(skewt.originy + skewt.pres_to_pix(ptop) / skewt.scale, 1),
    }

    lines = _lapse(skewt).lines
    drawn = {round(value, 1) for _x1, y1, _x2, y2 in lines
             for value in (y1, y2)}

    assert expected <= drawn


def test_the_bracket_is_a_pair_of_ticks_joined_by_a_bar(skewt, qt_app):
    _sized(skewt, qt_app, 900, 700)

    lines = _lapse(skewt).lines

    horizontal = [line for line in lines if line[1] == line[3]]
    vertical = [line for line in lines if line[0] == line[2]]
    assert len(horizontal) == 2
    assert len(vertical) == 1


@pytest.mark.parametrize("rate,tier", [
    (8.5, 5), (7.5, 4), (6.5, 1), (5.0, 0),
])
def test_the_colour_still_follows_the_rate(skewt, qt_app, rate, tier):
    _sized(skewt, qt_app, 900, 700)
    original = skewt.prof.max_lapse_rate_2_6
    skewt.prof.max_lapse_rate_2_6 = (rate, original[1], original[2])
    try:
        colors = _lapse(skewt).colors
    finally:
        skewt.prof.max_lapse_rate_2_6 = original

    assert colors, "a pen colour should be set"
    assert colors[-1].name() == skewt.alert_colors[tier].name()


def test_a_layer_below_the_threshold_is_not_drawn(skewt, qt_app):
    _sized(skewt, qt_app, 900, 700)
    original = skewt.prof.max_lapse_rate_2_6
    skewt.prof.max_lapse_rate_2_6 = (2.0, original[1], original[2])
    try:
        recorder = _lapse(skewt)
    finally:
        skewt.prof.max_lapse_rate_2_6 = original

    assert recorder.texts == []
    assert recorder.lines == []


def test_no_background_plate_is_painted(skewt, qt_app):
    """Same reasoning as the inflow labels: it only holes the isopleths."""
    _sized(skewt, qt_app, 900, 700)

    assert _lapse(skewt).rects == []


def test_the_label_stays_inside_the_plot_box(skewt, qt_app):
    _sized(skewt, qt_app, 900, 700)

    for text, box in _lapse(skewt).boxes():
        assert box.x() >= skewt.lpad, text
        assert box.y() >= 0, text


# --------------------------------------------------------------------------- #
# Collision, and the no-meter case
# --------------------------------------------------------------------------- #
def test_the_value_clears_the_inflow_labels_even_on_the_same_line(
        skewt, qt_app):
    """Drag the inflow layer's top onto the lapse layer's top.

    Both annotations then belong to the same height, which is the case that used
    to print one over the other. Its own column makes this unremarkable, and
    that is the point -- but it is the case worth pinning.
    """
    _sized(skewt, qt_app, 900, 700)
    undisturbed = _lapse(skewt).boxes()[0][1]

    original = skewt.prof.etop
    skewt.prof.etop = float(skewt.prof.max_lapse_rate_2_6[2])
    try:
        eff_boxes = _effective(skewt).boxes()
        moved = _lapse(skewt).boxes()[0][1]
    finally:
        skewt.prof.etop = original

    assert moved.y() == pytest.approx(undisturbed.y()), \
        "the height it belongs to must not change"
    for text, box in eff_boxes:
        assert not moved.intersects(box), f"overlaps {text!r}"
        assert moved.x() >= box.right(), \
            f"should sit right of {text!r}"


def _height_marker_ink(skewt):
    """Boxes for the ink of each height marker label.

    Measured from the text advance rather than the rect: ``draw_height`` passes
    ``lpad + 15`` as the rect *width*, which makes the rect much wider than the
    label, and the text is left-aligned inside it.
    """
    recorder = _Recorder()
    for level in (0.0, 1000.0, 3000.0, 6000.0, 9000.0, 12000.0, 15000.0):
        type(skewt).draw_height(skewt, level, recorder)
    boxes = []
    for rect, text, font in recorder.texts:
        metrics = QtGui.QFontMetrics(font) if font is not None else None
        advance = (float(metrics.horizontalAdvance(text)) if metrics
                   else float(rect.width()))
        boxes.append((text, QtCore.QRectF(
            float(rect.x()), float(rect.y()), advance,
            float(rect.height()))))
    return boxes


@pytest.mark.parametrize("width,height", SIZES)
def test_the_lapse_rate_value_clears_the_height_markers(
        skewt, qt_app, width, height):
    """These are drawn on every sounding, meter or no meter."""
    _sized(skewt, qt_app, width, height)
    markers = _height_marker_ink(skewt)
    assert markers, "the height markers should be drawn"

    for text, box in _lapse(skewt).boxes():
        for marker_text, marker in markers:
            assert not box.intersects(marker), \
                f"{text!r} overlaps the {marker_text!r} marker"


@pytest.mark.parametrize("width,height", SIZES)
def test_an_observed_sounding_still_clears_the_height_markers(
        skewt, qt_app, width, height):
    """The reported case: no omega meter, so nothing else pushed it right.

    The value was drawn hard against the left border and through the height
    labels, because the gutter only ever cleared the meter.
    """
    _sized(skewt, qt_app, width, height)
    skewt.plot_omega = False
    try:
        markers = _height_marker_ink(skewt)
        boxes = _lapse(skewt).boxes()
        assert boxes, "the value should still be drawn"
        for text, box in boxes:
            assert box.x() > skewt.lpad + \
                render_mod.SKEWT_ANNOTATION_CLEARANCE, \
                f"{text!r} sits on the plot border at {box.x():.1f}"
            for marker_text, marker in markers:
                assert not box.intersects(marker), \
                    f"{text!r} overlaps the {marker_text!r} marker"
    finally:
        skewt.plot_omega = True


@pytest.mark.parametrize("width,height", SIZES)
def test_the_observed_bracket_also_clears_the_height_markers(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    skewt.plot_omega = False
    try:
        band = render_mod._skewt_height_label_band(skewt, QtGui)
        lines = _lapse(skewt).lines
        assert lines, "the bracket should still be drawn"
        for x1, _y1, x2, _y2 in lines:
            assert min(x1, x2) >= band[1], \
                f"bracket at x={min(x1, x2):.1f} is inside the marker column"
    finally:
        skewt.plot_omega = True


def test_an_observed_sounding_reclaims_the_meters_column(skewt, qt_app):
    """No omega means no meter, so the gutter starts at the plot edge."""
    _sized(skewt, qt_app, 900, 700)
    with_meter = render_mod._skewt_left_gutter(skewt, QtGui)

    skewt.plot_omega = False
    try:
        without_meter = render_mod._skewt_left_gutter(skewt, QtGui)
        assert render_mod._skewt_omega_band(skewt, QtGui) is None
        boxes = _lapse(skewt).boxes()
    finally:
        skewt.plot_omega = True

    # The gutter itself reclaims the meter's column, which is what this is
    # about. Where the value ends up is decided by the column right of the
    # inflow annotation, so it is asserted separately.
    assert without_meter < with_meter
    assert boxes, "the value should still be drawn"
    assert boxes[0][1].x() >= without_meter


# --------------------------------------------------------------------------- #
# The inflow layer's own three labels
# --------------------------------------------------------------------------- #
def _inflow_layout(skewt):
    import sharppy.sharptab as tab

    return render_mod._skewt_effective_layer_labels(
        skewt, QtCore, QtGui, tab)


def _inflow_boxes(skewt):
    layout = _inflow_layout(skewt)
    if layout is None:
        return []
    return [(layout[text_key], layout[rect_key])
            for rect_key, text_key in (("rect_bot", "text_bot"),
                                       ("rect_top", "text_top"),
                                       ("rect_esrh", "text_esrh"))]


class _InflowLayer:
    """Temporarily reshape the effective inflow layer on a shared profile."""

    def __init__(self, skewt, ebottom, etop):
        self._prof = skewt.prof
        self._wanted = (ebottom, etop)
        self._original = None

    def __enter__(self):
        self._original = (self._prof.ebottom, self._prof.etop)
        self._prof.ebottom, self._prof.etop = self._wanted
        return self._prof

    def __exit__(self, *_exc):
        self._prof.ebottom, self._prof.etop = self._original
        return False


def _layer_cases(skewt):
    surface = float(skewt.prof.pres[skewt.prof.sfc])
    return {
        # Zero depth and surface based: the reported case. The bottom label
        # flips above its own line and lands on the top label.
        "degenerate-surface-based": (surface, surface),
        "shallow-surface-based": (surface, surface - 5.0),
        "shallow-elevated": (surface - 60.0, surface - 65.0),
        "deep-surface-based": (surface, surface - 285.0),
    }


@pytest.mark.parametrize("case", ["degenerate-surface-based",
                                 "shallow-surface-based",
                                 "shallow-elevated",
                                 "deep-surface-based"])
def test_the_inflow_layers_own_labels_never_overlap(skewt, qt_app, case):
    """A surface-based *and* shallow layer drew ``SFC`` through its top label.

    The bottom label already moves above its own line when the layer is surface
    based, because otherwise it falls out of the plot. When the layer is also
    shallow that flip puts it on the same line as the top label, in the same
    column.
    """
    _sized(skewt, qt_app, 900, 700)
    ebottom, etop = _layer_cases(skewt)[case]

    with _InflowLayer(skewt, ebottom, etop):
        boxes = _inflow_boxes(skewt)
        assert len(boxes) == 3
        for index, (first_text, first) in enumerate(boxes):
            for second_text, second in boxes[index + 1:]:
                assert not first.intersects(second), \
                    f"{first_text!r} overlaps {second_text!r}"


def test_the_bottom_label_is_the_one_that_moves(skewt, qt_app):
    """The top bound and the helicity keep their column; the base yields."""
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        settled = _inflow_layout(skewt)
    with _InflowLayer(skewt, surface, surface):
        collided = _inflow_layout(skewt)

    assert collided["rect_top"].x() == pytest.approx(settled["rect_top"].x())
    assert collided["rect_esrh"].x() == pytest.approx(
        settled["rect_esrh"].x())
    assert collided["rect_bot"].x() > settled["rect_bot"].x()


def test_the_bottom_label_keeps_the_height_it_belongs_to(skewt, qt_app):
    """It slides along its line, so it still marks the layer's base."""
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        settled = _inflow_layout(skewt)["rect_bot"]
    with _InflowLayer(skewt, surface, surface):
        collided = _inflow_layout(skewt)["rect_bot"]

    assert collided.y() == pytest.approx(settled.y())


def test_a_settled_layer_keeps_its_labels_in_one_column(skewt, qt_app):
    """Nothing moves when nothing collides."""
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        layout = _inflow_layout(skewt)

    assert layout["rect_bot"].x() == pytest.approx(layout["rect_top"].x())


def test_the_inflow_label_rects_are_sized_to_their_text(skewt, qt_app):
    """The vendored 25/50/50 pixel floors made every collision pessimistic.

    The text is left-aligned with ``TextDontClip``, so the rect width never
    affected where a glyph landed; carrying the floors only spread the labels
    apart by up to 30 px more than the ink needed.
    """
    _sized(skewt, qt_app, 900, 700)
    metrics = QtGui.QFontMetrics(skewt.esrh_font)

    for text, rect in _inflow_boxes(skewt):
        expected = max(float(metrics.horizontalAdvance(text)) + 4.0, 12.0)
        assert rect.width() == pytest.approx(expected, abs=1.0), text


@pytest.mark.parametrize("case", ["degenerate-surface-based",
                                 "shallow-surface-based"])
def test_the_lapse_rate_still_clears_the_shifted_inflow_labels(
        skewt, qt_app, case):
    """The lapse rate reads these rects as blockers, so it must see the shift."""
    _sized(skewt, qt_app, 900, 700)
    ebottom, etop = _layer_cases(skewt)[case]

    with _InflowLayer(skewt, ebottom, etop):
        inflow = _inflow_boxes(skewt)
        for lapse_text, lapse_box in _lapse(skewt).boxes():
            for text, rect in inflow:
                assert not lapse_box.intersects(rect), \
                    f"{lapse_text!r} overlaps {text!r}"


def test_the_value_is_no_longer_anchored_to_the_temperature_trace(
        skewt, qt_app):
    """The regression this replaces.

    Upstream put the value five degrees warm of the trace at ``pbot``. That is
    where the crowding was, so the new column must be well left of it.
    """
    import sharppy.sharptab as tab

    _sized(skewt, qt_app, 900, 700)
    _rate, pbot, _ptop = skewt.prof.max_lapse_rate_2_6[:3]
    upstream_x = skewt.tmpc_to_pix(
        tab.interp.vtmp(skewt.prof, pbot) + 5, pbot)

    box = _lapse(skewt).boxes()[0][1]

    assert box.right() < upstream_x - 50, (
        f"value reaches {box.right():.1f}; upstream anchored it at "
        f"{float(upstream_x):.1f}")
