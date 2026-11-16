"""Geometry helpers and retained Skew-T annotation invariants.

Fixed lapse-rate column coverage is owned by the Rust painter tests; effective
inflow layout and the existing value, color and pressure-depth checks stay here.
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
    """A standalone, resizable native Skew-T with the render patches installed."""
    from sharpmod.tests._examples import examples_dir

    example = examples_dir() / EXAMPLE
    if not example.exists():
        pytest.skip(f"{EXAMPLE} unavailable")

    installed = render_mod.install_render_patches()
    assert "skewt.lapse-rate-label-placement" in installed
    from sharppy.viz.skew import plotSkewT

    prof_col, _stn = render_mod.decode(str(example))
    config = render_mod.build_config(".")
    preferences = {key: value for (section, key), value in config
                   if section == "preferences"}
    widget = plotSkewT()
    widget.setPreferences(update_gui=False, **preferences)
    widget.addProfileCollection(prof_col)
    widget.setActiveCollection(0, update_gui=False)
    widget.setParcel(prof_col.getHighlightedProf().mupcl)
    qt_app.processEvents()
    try:
        yield widget
    finally:
        widget.deleteLater()


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


def test_the_gutter_ignores_the_meter():
    assert render_mod._skewt_left_gutter(_FakeWidget()) == (
        render_mod._skewt_left_gutter(_FakeWidget(plot_omega=False)))


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
def test_the_lapse_rate_annotation_stays_put_with_omega(
        skewt, qt_app, width, height, monkeypatch):
    _sized(skewt, qt_app, width, height)
    monkeypatch.setattr(skewt, "plot_omega", False)
    without = _lapse(skewt)
    # Simulate long bars crossing the cold side of the plot.
    monkeypatch.setattr(skewt, "omeg_to_pix", lambda _omega: skewt.brx - 2.0)
    monkeypatch.setattr(skewt, "plot_omega", True)
    with_meter = _lapse(skewt)

    assert without.boxes() and without.lines
    assert with_meter.boxes() == without.boxes()
    assert with_meter.lines == without.lines


@pytest.mark.parametrize("width,height", SIZES)
def test_effective_layer_values_clear_the_height_markers(
        skewt, qt_app, width, height):
    _sized(skewt, qt_app, width, height)
    gutter = render_mod._skewt_left_gutter(skewt, QtGui)

    for text, box in _effective(skewt).boxes():
        if text != "SFC":
            assert box.x() >= gutter - 0.6, text


# --------------------------------------------------------------------------- #
# The annotation still says and marks the same thing
# --------------------------------------------------------------------------- #
def test_the_value_and_its_unit_are_unchanged(skewt, qt_app):
    _sized(skewt, qt_app, 900, 700)

    texts = [text for text, _box in _lapse(skewt).boxes()]

    assert len(texts) == 1
    assert texts[0].endswith(" °C/km")
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


def test_the_surface_label_keeps_its_column_in_shallow_layers(skewt, qt_app):
    """SFC stays at the surface while the other values clear it."""
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        settled = _inflow_layout(skewt)
    with _InflowLayer(skewt, surface, surface):
        collided = _inflow_layout(skewt)

    assert collided["rect_bot"].x() == pytest.approx(settled["rect_bot"].x())
    assert not collided["rect_bot"].intersects(collided["rect_top"])
    assert not collided["rect_bot"].intersects(collided["rect_esrh"])


def test_the_bottom_label_keeps_the_height_it_belongs_to(skewt, qt_app):
    """It slides along its line, so it still marks the layer's base."""
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        settled = _inflow_layout(skewt)["rect_bot"]
    with _InflowLayer(skewt, surface, surface):
        collided = _inflow_layout(skewt)["rect_bot"]

    assert collided.y() == pytest.approx(settled.y())


def test_inflow_labels_keep_their_positions_when_omega_reaches_them(
        skewt, qt_app, monkeypatch):
    _sized(skewt, qt_app, 900, 700)
    surface = float(skewt.prof.pres[skewt.prof.sfc])

    with _InflowLayer(skewt, surface, surface - 285.0):
        monkeypatch.setattr(skewt, "plot_omega", False)
        before = _inflow_layout(skewt)
        monkeypatch.setattr(skewt, "omeg_to_pix", lambda _omega: skewt.brx - 2.0)
        monkeypatch.setattr(skewt, "plot_omega", True)
        after = _inflow_layout(skewt)

    assert before["text_bot"] == "SFC"
    for key in ("rect_bot", "rect_top", "rect_esrh"):
        assert after[key] == before[key]


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
