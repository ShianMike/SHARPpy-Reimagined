"""Regression tests for skew-T lower-edge label bounds."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import numpy.ma as ma
import pytest
from qtpy import QtCore, QtGui

from sharpmod import render as render_mod


class _RecordingPainter:
    def __init__(self):
        self.rects = []
        self.texts = []

    def setClipping(self, *_args):
        pass

    def setPen(self, *_args):
        pass

    def setBrush(self, *_args):
        pass

    def setFont(self, *_args):
        pass

    def drawPath(self, *_args):
        pass

    def drawLine(self, *_args):
        pass

    def drawRect(self, rect):
        self.rects.append(rect)

    def drawText(self, *args):
        if args and hasattr(args[0], "x"):
            text = args[-1]
            self.texts.append((args[0], str(text)))


class _SpeedPainter:
    def __init__(self):
        self.current_font = None
        self.texts = []

    def setPen(self, *_args):
        pass

    def setFont(self, font):
        self.current_font = QtGui.QFont(font)

    def drawLine(self, *_args):
        pass

    def drawText(self, x, y, width, height, flags, text):
        self.texts.append(SimpleNamespace(
            x=x,
            y=y,
            width=width,
            height=height,
            flags=flags,
            text=str(text),
            font=QtGui.QFont(self.current_font),
        ))


class _PanelPainter:
    def __init__(self):
        self.current_font = None
        self.texts = []
        self.lines = []
        self.rects = []

    def setPen(self, *_args):
        pass

    def setBrush(self, *_args):
        pass

    def setFont(self, font):
        self.current_font = QtGui.QFont(font)

    def drawEllipse(self, *_args):
        pass

    def drawRect(self, *args):
        if len(args) == 1 and hasattr(args[0], "x"):
            self.rects.append(QtCore.QRectF(args[0]))

    def fillRect(self, *_args):
        pass

    def drawLine(self, *args):
        self.lines.append(args)

    def drawText(self, *args):
        if args and hasattr(args[0], "x"):
            self.texts.append(SimpleNamespace(
                rect=QtCore.QRectF(args[0]),
                flags=args[1],
                text=str(args[2]),
                font=QtGui.QFont(self.current_font),
            ))


class _TraceWidget:
    lpad = 30
    brx = 250
    tpad = 20
    bry = 220
    originx = 0.0
    originy = 0.0
    scale = 1.0
    sfc_units = "Fahrenheit"
    bg_color = QtGui.QColor("#000000")
    environment_trace_font = QtGui.QFont("Helvetica", 16)

    def __init__(self):
        self.pres = ma.masked_array([1000.0, 900.0], mask=[False, False])

    def tmpc_to_pix(self, data, _pres):
        values = np.asarray(data, dtype=float)
        return np.linspace(248.0, 180.0, values.size)

    def pres_to_pix(self, pres):
        values = np.asarray(pres, dtype=float)
        return np.where(values >= 1000.0, 220.0, 180.0)

    def drawSTDEV(self, *_args):
        pass


class _EffectiveLayerWidget:
    lpad = 30
    brx = 250
    tpad = 20
    bry = 220
    originy = 0.0
    scale = 1.0
    esrh_height = 18
    use_left = False
    bg_color = QtGui.QColor("#000000")
    eff_layer_color = QtGui.QColor("#00ffff")
    esrh_font = QtGui.QFont("Helvetica", 12)

    def __init__(self):
        self.prof = SimpleNamespace(
            etop=800.0,
            ebottom=1000.0,
            pres=[1000.0],
            sfc=0,
            left_esrh=[75],
            right_esrh=[125],
        )

    def tmpc_to_pix(self, temp, _pres):
        if temp == -33:
            return 20.0
        return 246.0

    def pres_to_pix(self, pres):
        return 220.0 if pres >= 1000.0 else 150.0


class _HodographWidget:
    tlx = 0
    brx = 300
    tly = 0
    bry = 220
    wid = 300
    hgt = 220


class _HodographRingWidget(_HodographWidget):
    brx = 210
    wid = 210
    centerx = 105.0
    centery = 110.0
    scale = 1.0
    fg_color = QtGui.QColor("#ffffff")
    bg_color = QtGui.QColor("#000000")
    isotach_color = QtGui.QColor("#777777")

    def __init__(self):
        self.label_font = QtGui.QFont("Helvetica", 8)
        self.label_font.setBold(True)


class _MeanWindWidget(_HodographWidget):
    fg_color = QtGui.QColor("#000000")
    bg_color = QtGui.QColor("#ffffff")
    mean_lcl_el = (10.0, 20.0)
    mean_lcl_el_vec = (248.0, 25.0)
    wind_units = "knots"

    def __init__(self, x=150.0, y=100.0):
        self._point = (x, y)
        self.label_font = QtGui.QFont("Helvetica", 8)
        self.label_font.setBold(True)
        self._sharpmod_hodo_annotation_rects = []

    def uv_to_pix(self, _u, _v):
        return self._point


class _SpeedWidget:
    tly = 0
    bry = 90
    bpad = 20
    isotach_color = QtGui.QColor("#8a4b21")
    fg_color = QtGui.QColor("#ffffff")

    def __init__(self):
        self.label_font = QtGui.QFont("Helvetica", 16)

    def speed_to_pix(self, speed):
        return float(speed) - 40.0


def _assert_inside_plot(widget, rect):
    assert rect.left() >= widget.lpad + 2
    assert rect.right() <= widget.brx - 2
    assert rect.top() >= widget.tpad + 2
    assert rect.bottom() <= widget.bry - 2


def test_speed_axis_120kt_label_font_fits_bottom_slot(qt_app):
    pytest.importorskip("sharppy.viz.speed")
    render_mod._install_speed_title_cap()

    from sharppy.viz.speed import backgroundSpeed

    widget = _SpeedWidget()
    painter = _SpeedPainter()

    backgroundSpeed.draw_speed(widget, 120, painter, delta=20, drawlabel=True)

    label = painter.texts[-1]
    assert label.text == "120"
    assert label.y == widget.bry + 1
    assert label.height <= widget.bpad - 4
    assert label.flags & QtCore.Qt.AlignVCenter
    assert label.flags & QtCore.Qt.AlignHCenter

    metrics = QtGui.QFontMetrics(label.font)
    assert metrics.height() <= label.height
    assert metrics.horizontalAdvance(label.text) <= label.width - 2


@pytest.mark.parametrize(
    ("module_name", "class_name", "resize"),
    [
        ("sharppy.viz.stpef", "plotSTPEF", (550, 330)),
        ("sharppy.viz.vrot", "plotVROT", (590, 360)),
    ],
)
def test_conditional_probability_panel_labels_stay_inside_frame(
        qt_app, module_name, class_name, resize):
    pytest.importorskip(module_name)
    render_mod._install_conditional_prob_panel_fit()

    import importlib

    cls = getattr(importlib.import_module(module_name), class_name)
    widget = cls()
    widget.resize(*resize)
    widget.initUI()
    painter = _PanelPainter()

    widget.draw_frame(painter)

    assert painter.texts
    for item in painter.texts:
        assert item.rect.left() >= -0.5, item.text
        assert item.rect.right() <= widget.brx + 0.5, item.text
        assert item.rect.top() >= -0.5, item.text
        assert item.rect.bottom() <= widget.bry + widget.bpad + 0.5, item.text
        if item.text in {"EF0-EF1", "EF2-EF3", "EF4-EF5"}:
            assert item.rect.width() > 10


def test_winter_panel_uses_real_column_widths_for_long_rows(qt_app):
    pytest.importorskip("sharppy.viz.winter")
    render_mod._install_winter_text_fit()

    from sharppy.viz.winter import plotWinter

    widget = plotWinter()
    widget.resize(570, 340)
    widget.initUI()
    widget.prof = SimpleNamespace(missing=-9999, pres=[1000.0], sfc=0)
    widget.dgz_depth = 2071
    widget.dgz_zbot = 22673
    widget.dgz_ztop = 24744
    widget.dgz_meanrh = 41.0
    widget.dgz_pw = 0.0
    widget.dgz_meanq = 0.3
    widget.dgz_meanomeg = 10 * widget.prof.missing
    widget.oprh = -99990.0
    widget.plevel = 0
    widget.tpos = 0
    widget.tneg = 0
    widget.wpos = 0
    widget.wneg = 0
    widget.precip_type = "No precipitation type expected."
    widget.ptype_tmpf_string = "Based on SFC Temperature of 32.00 F"

    painter = _PanelPainter()
    widget.draw_frame(painter)
    widget.drawOPRH(painter)
    widget.drawDGZLayer(painter)
    widget.drawInitial(painter)
    widget.drawWCLayer(painter)
    widget.drawPrecipType(painter)
    widget.drawPrecipTypeTemp(painter)

    expected_full_text = {
        "*** DENDRITIC GROWTH ZONE (-12 TO -17 C) ***",
        "OPRH (Omega*PW*RH): N/A",
        "Layer Depth: 2071 ft (22673-24744 ft msl)",
        "Mean Layer RH: 41.0 %",
        "Mean Layer MixRat: 0.3 g/kg",
        "Mean Layer PW: 0.0 in",
        "Mean Layer Omega: N/A",
        "Initial Phase:  No Precipitation layers found.",
        "TEMPERATURE PROFILE",
        "WETBULB PROFILE",
        "Warm/Cold layers not found.",
        "*** BEST GUESS PRECIP TYPE ***",
        "No precipitation type expected.",
        "Based on SFC Temperature of 32.00 F",
    }
    rendered_text = {item.text for item in painter.texts}
    assert expected_full_text <= rendered_text
    assert not any("…" in item.text or "..." in item.text
                   for item in painter.texts)

    for item in painter.texts:
        if not item.text:
            continue
        metrics = QtGui.QFontMetrics(item.font)
        assert metrics.horizontalAdvance(item.text) <= item.rect.width() + 2
        row_step = widget.label_height + 5 + widget.os_mod
        assert metrics.height() <= max(item.rect.height(), row_step) + 2

    split = widget.brx * 0.48
    right_column = [
        item for item in painter.texts
        if item.text.startswith("Mean Layer MixRat")
        or item.text.startswith("WETBULB PROFILE")
    ]
    assert right_column
    for item in right_column:
        assert item.rect.left() > split
        assert item.rect.right() <= widget.brx - 2
        assert item.rect.width() > widget.wid / 10

    warm_cold = [
        item for item in painter.texts
        if item.text.startswith("Warm/Cold layers")
    ]
    assert len(warm_cold) == 2
    for item in warm_cold:
        assert item.rect.right() <= widget.brx - 2
        assert item.rect.width() > widget.wid / 10

    pw_row = next(item for item in painter.texts
                  if item.text.startswith("Mean Layer PW"))
    horizontal_lines = [
        args for args in painter.lines
        if len(args) == 4 and args[1] == args[3]
    ]
    dgz_divider = min(
        float(args[1]) for args in horizontal_lines
        if float(args[1]) > pw_row.rect.bottom())
    assert dgz_divider - pw_row.rect.bottom() >= 8


#: Everything the fire panel reads off a profile, with values from a real
#: sounding so the rows are the lengths they are in practice.
_FIRE_PROFILE = dict(
    fosberg=6.0,
    haines_hght=0,
    haines_low=4,
    haines_mid=4,
    haines_high=2,
    sfc_rh=80.0,
    rh01km=78.0,
    pblrh=77.0,
    pbl_h=327.0,
    meanwind01km=(-6.4, 11.5),
    meanwindpbl=(-6.4, 11.5),
    pblmaxwind=(-5.9, 20.8, 950.0),
    pwat=2.23,
    bplus_fire=2989.0,
    wdir=[105.0],
    wspd=[5.0],
    missing=-9999,
    pres=[1000.0],
    sfc=0,
)

_FIRE_PANEL_SIZES = ((320, 340), (256, 300), (420, 400), (200, 260))


def _fire_widget(size):
    """A fire panel of a given size, fed a realistic profile."""
    from sharppy.viz.fire import plotFire

    profile = SimpleNamespace(**_FIRE_PROFILE)
    profile.get_sfc = lambda: 0
    widget = plotFire()
    widget.resize(*size)
    # ``resize`` delivers no resizeEvent offscreen, and that event is what
    # recomputes the height-scaled font this panel's fault comes from.
    widget.initUI()
    widget.setProf(profile)
    return widget


def _fire_rows(widget):
    painter = _PanelPainter()
    widget.draw_frame(painter)
    widget.drawPBLchar(painter)
    widget.drawFosberg(painter)
    widget.drawHainesIndex(painter)
    widget.drawVentilationRate(painter)
    return painter


@pytest.mark.parametrize("size", _FIRE_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _FIRE_PANEL_SIZES])
def test_fire_panel_rows_fit_the_columns_they_are_drawn_into(qt_app, size):
    """The fire panel had the fault the winter panel was already fixed for.

    Its font is scaled from the panel's height and its moisture and wind rows are
    written into two-fifths-width rects with ``TextDontClip``, so measured on a
    real profile up to twelve of twenty-one rows were drawn wider than the box
    holding them -- "0-1 km mean = 169/22" wanting 240 px of a 102 px column.
    Nobody had noticed because the panel is only reachable by right-clicking one
    specific box.
    """
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget(size)
    try:
        painter = _fire_rows(widget)

        assert painter.texts, "the fire panel drew nothing"
        for item in painter.texts:
            if not item.text.strip():
                continue
            metrics = QtGui.QFontMetrics(item.font)
            assert metrics.horizontalAdvance(item.text) <= item.rect.width() + 2, \
                item.text
    finally:
        widget.close()


@pytest.mark.parametrize("size", _FIRE_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _FIRE_PANEL_SIZES])
def test_fire_panel_columns_cannot_reach_each_other(qt_app, size):
    """Left-aligned moisture and right-aligned wind must not interleave.

    This is what the overflow actually looked like on screen: the wind column is
    right-aligned, so text too long for it grew *leftwards* across the moisture
    column rather than off the edge of the panel.
    """
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget(size)
    try:
        painter = _fire_rows(widget)
        moisture = [item for item in painter.texts
                    if item.text.startswith(
                        ("SFC RH", "0-1 km RH", "BL mean RH", "PW ="))]
        wind = [item for item in painter.texts
                if item.text.startswith(
                    ("SFC =", "0-1 km mean", "BL mean =", "BL max"))]
        assert moisture and wind

        for item in moisture + wind:
            metrics = QtGui.QFontMetrics(item.font)
            width = metrics.horizontalAdvance(item.text)
            if item.text.startswith(("SFC =", "0-1 km mean", "BL mean =",
                                     "BL max")):
                painted_left = item.rect.right() - width      # right-aligned
            else:
                painted_left = item.rect.left()               # left-aligned
            painted_right = painted_left + width
            assert painted_left >= -0.5, item.text
            assert painted_right <= widget.brx + 0.5, item.text

        assert (min(item.rect.left() for item in wind)
                > max(item.rect.right() for item in moisture)), \
            "the moisture and wind columns share pixels"
    finally:
        widget.close()


@pytest.mark.parametrize("size", _FIRE_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _FIRE_PANEL_SIZES])
def test_fire_panel_rows_stay_inside_the_panel_height(qt_app, size):
    """Rows must not run off the bottom, which the vendored layout did.

    Row heights came from font metrics with no reference to the space available,
    and the font is itself scaled from the panel's height -- so a taller panel
    grew its rows faster than it gained room for them. Before this patch the
    Haines row was drawn below the frame at 420x400 and the whole derived block
    went under at 200x260.
    """
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget(size)
    try:
        painter = _fire_rows(widget)
        assert painter.texts
        for item in painter.texts:
            if not item.text.strip():
                continue
            assert item.rect.top() >= -0.5, item.text
            assert item.rect.bottom() <= widget.bry + 0.5, item.text
    finally:
        widget.close()


@pytest.mark.parametrize("size", _FIRE_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _FIRE_PANEL_SIZES])
def test_fire_panel_rows_never_share_a_line(qt_app, size):
    """Fitting rows into the panel must not stack them on top of each other."""
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget(size)
    try:
        painter = _fire_rows(widget)
        full_width = [item for item in painter.texts
                      if item.text.strip()
                      and item.rect.width() >= widget.brx - 2]
        tops = sorted(item.rect.top() for item in full_width)
        for earlier, later in zip(tops, tops[1:]):
            assert later > earlier, "two full-width rows share a top edge"
    finally:
        widget.close()


def test_fire_panel_reports_the_ventilation_rate(qt_app):
    """Mixing height times transport wind, which upstream never showed.

    Both factors were already computed and printed separately; their product is
    what a smoke-management or prescribed-burn decision turns on.
    """
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget((320, 340))
    try:
        shown = [item.text for item in _fire_rows(widget).texts]
    finally:
        widget.close()

    row = next((text for text in shown if text.startswith("Vent Rate")), None)
    assert row is not None, "the fire panel does not report a ventilation rate"
    # 327 m mixed layer, 13 kt transport wind -> ~2187 m^2/s.
    assert "m2/s" in row
    value = int(row.split("=")[1].strip().split()[0])
    assert 2100 <= value <= 2300, row


def test_fire_panel_says_so_when_the_ventilation_rate_is_unavailable(qt_app):
    """A masked mixing height must not become a plausible-looking number."""
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget((320, 340))
    try:
        widget.pbl_h = ma.masked
        painter = _PanelPainter()
        widget.draw_frame(painter)
        widget.drawVentilationRate(painter)
        shown = [item.text for item in painter.texts]
    finally:
        widget.close()

    assert "Vent Rate = M" in shown


def test_fire_panel_keeps_every_row_it_used_to_show(qt_app):
    """Fitting the text must not have been achieved by dropping rows."""
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget((320, 340))
    try:
        shown = {item.text for item in _fire_rows(widget).texts}
    finally:
        widget.close()

    for expected in (
        "Fire Weather Parameters",
        "Moisture",
        "Low-Level Wind",
        "Derived Indices",
        "SFC RH = 80%",
        "0-1 km RH = 78%",
        "BL mean RH = 77%",
        "PW = 2.23 in",
        "Fosberg FWI = 6",
        "Haines Index (L) = 4",
    ):
        assert expected in shown, expected
    assert any(text.startswith("PBL Height =") for text in shown)
    assert any(text.startswith("0-1 km mean =") for text in shown)
    assert any(text.startswith("BL max =") for text in shown)


def test_fire_panel_haines_hit_target_stays_inside_the_panel(qt_app):
    """The Haines row is clickable to change elevation, so its box must be real.

    ``mousePressEvent`` tests the cursor against ``haines_y1``, ``haines_width``
    and ``label_height``; rewriting the layout has to keep those meaning what the
    click handler assumes.
    """
    pytest.importorskip("sharppy.viz.fire")
    render_mod._install_fire_text_fit()

    widget = _fire_widget((320, 340))
    try:
        _fire_rows(widget)
        assert widget.haines_width > 0
        assert 0 < widget.haines_y1 < widget.bry
        assert widget.haines_y1 + widget.label_height <= widget.bry
        assert widget.fosberg_y1 < widget.haines_y1
    finally:
        widget.close()


def test_hodograph_readout_rect_clamps_inside_frame(qt_app):
    widget = _HodographWidget()
    rect = QtCore.QRectF(292.0, 214.0, 64.0, 16.0)

    fitted = render_mod._fit_rect_to_hodo(widget, QtCore, rect)

    assert fitted.width() == rect.width()
    assert fitted.height() == rect.height()
    assert fitted.left() >= widget.tlx + 2
    assert fitted.right() <= widget.brx - 2
    assert fitted.top() >= widget.tly + 2
    assert fitted.bottom() <= widget.bry - 2


def test_hodograph_ring_labels_use_only_natural_positions_that_fit(qt_app):
    pytest.importorskip("sharppy.viz.hodo")
    render_mod._install_hodo_label_fit()

    from sharppy.viz.hodo import backgroundHodo

    widget = _HodographRingWidget()
    painter = _PanelPainter()

    backgroundHodo.draw_ring(widget, 80, painter)
    backgroundHodo.draw_ring(widget, 90, painter)
    backgroundHodo.draw_ring(widget, 100, painter)

    labels_80 = [item for item in painter.texts if item.text == "80"]
    assert len(labels_80) == 4
    assert any(label.rect.center().y() < widget.centery
               for label in labels_80)
    assert any(label.rect.center().y() > widget.centery
               for label in labels_80)
    assert any(label.rect.center().x() < widget.centerx
               for label in labels_80)
    assert any(label.rect.center().x() > widget.centerx
               for label in labels_80)

    labels_90 = [item for item in painter.texts if item.text == "90"]
    # The right-side label sits exactly at the two-pixel frame margin with
    # some Qt/font combinations and one pixel beyond it with others.  It is
    # therefore valid only when the measured glyph width actually fits; the
    # top, bottom, and left natural positions must always remain present.
    assert len(labels_90) in {3, 4}
    assert any(label.rect.center().y() < widget.centery
               for label in labels_90)
    assert any(label.rect.center().y() > widget.centery
               for label in labels_90)
    assert any(label.rect.center().x() < widget.centerx
               for label in labels_90)
    for label in labels_90:
        assert label.rect.left() >= widget.tlx + 2
        assert label.rect.right() <= widget.brx - 2
        assert label.rect.top() >= widget.tly + 2
        assert label.rect.bottom() <= widget.bry - 2

    labels = [item for item in painter.texts if item.text == "100"]
    assert len(labels) == 1
    for label in labels:
        assert label.rect.left() >= widget.tlx + 2
        assert label.rect.right() <= widget.brx - 2
        assert label.rect.top() >= widget.tly + 2
        assert label.rect.bottom() <= widget.bry - 2
        metrics = QtGui.QFontMetrics(label.font)
        assert metrics.horizontalAdvance(label.text) <= label.rect.width() - 2

    assert labels[0].rect.center().y() < widget.centery
    assert labels[0].rect.center().x() > widget.centerx


def test_mean_wind_label_has_real_gap_after_centered_square(qt_app):
    pytest.importorskip("sharppy.viz.hodo")
    render_mod._install_hodo_label_fit()

    from sharppy.viz.hodo import plotHodo

    widget = _MeanWindWidget()
    painter = _PanelPainter()
    plotHodo.drawLCLtoEL_MW(widget, painter)

    marker = painter.rects[-1]
    label = next(item for item in painter.texts if item.text == "248/25")

    assert marker.center() == QtCore.QPointF(150.0, 100.0)
    assert label.rect.left() - marker.right() >= 5
    assert not label.rect.intersects(marker)
    assert label.rect.right() <= widget.brx - 2
    metrics = QtGui.QFontMetrics(label.font)
    assert metrics.horizontalAdvance(label.text) <= label.rect.width() - 4


def test_hodo_annotation_flips_away_from_edge_and_occupied_side():
    widget = _HodographWidget()
    near_right = QtCore.QRectF(284, 100, 8, 8)
    flipped = render_mod._place_hodo_annotation_rect(
        widget, QtCore, near_right, 60, 16, gap=5)

    assert flipped.right() + 5 <= near_right.left()
    assert flipped.left() >= widget.tlx + 2
    assert flipped.right() <= widget.brx - 2

    centered = QtCore.QRectF(140, 100, 8, 8)
    occupied_right = QtCore.QRectF(153, 96, 70, 20)
    avoided = render_mod._place_hodo_annotation_rect(
        widget, QtCore, centered, 60, 16,
        occupied=(occupied_right,), gap=5)

    assert not avoided.intersects(occupied_right.adjusted(-5, -5, 5, 5))
    assert not avoided.intersects(centered.adjusted(-5, -5, 5, 5))


def test_surface_labels_pack_in_anchor_order_with_four_pixel_gap():
    widget = _TraceWidget()
    labels = [
        {"center_x": 100, "line_y": 218, "width": 30, "height": 16},
        {"center_x": 110, "line_y": 218, "width": 30, "height": 16},
        {"center_x": 120, "line_y": 218, "width": 30, "height": 16},
    ]

    rects = render_mod._layout_skewt_surface_labels(
        widget, QtCore, labels, gap=4)

    assert [rect.left() for rect in rects] == sorted(
        rect.left() for rect in rects)
    for rect in rects:
        _assert_inside_plot(widget, rect)
    for left, right in zip(rects, rects[1:]):
        assert right.left() - left.right() >= 4
        assert not left.adjusted(-2, -2, 2, 2).intersects(right)


def test_surface_labels_use_extra_rows_when_plot_is_too_narrow():
    widget = SimpleNamespace(
        lpad=0, brx=50, tpad=0, bry=120, wid=50, hgt=120)
    labels = [
        {"center_x": 10, "line_y": 95, "width": 40, "height": 16},
        {"center_x": 25, "line_y": 95, "width": 40, "height": 16},
        {"center_x": 40, "line_y": 95, "width": 40, "height": 16},
    ]

    rects = render_mod._layout_skewt_surface_labels(
        widget, QtCore, labels, gap=4)

    assert len({rect.top() for rect in rects}) > 1
    for rect in rects:
        assert rect.left() >= 2
        assert rect.right() <= 48
        assert rect.top() >= 2
        assert rect.bottom() <= 118
    for index, rect in enumerate(rects):
        for other in rects[index + 1:]:
            assert not rect.adjusted(-2, -2, 2, 2).intersects(other)


def test_surface_label_rows_account_for_next_rows_taller_font():
    widget = SimpleNamespace(
        lpad=0, brx=70, tpad=0, bry=140, wid=70, hgt=140)
    labels = [
        {"center_x": 20, "line_y": 120, "width": 60, "height": 12},
        {"center_x": 35, "line_y": 120, "width": 60, "height": 28},
        {"center_x": 50, "line_y": 120, "width": 60, "height": 18},
    ]

    rects = render_mod._layout_skewt_surface_labels(
        widget, QtCore, labels, gap=4)

    for index, rect in enumerate(rects):
        for other in rects[index + 1:]:
            assert not rect.adjusted(-2, -2, 2, 2).intersects(other)


def test_primary_surface_label_queue_replaces_duplicate_profile_pass():
    queue = []
    first = {"text": "81", "color": "ensemble"}
    final = {"text": "81", "color": "primary"}

    render_mod._upsert_skewt_surface_label(
        queue, ("source", 123), first)
    render_mod._upsert_skewt_surface_label(
        queue, ("source", 123), final)

    assert queue == [{
        "text": "81",
        "color": "primary",
        "dedupe_key": ("source", 123),
    }]


def test_secondary_sounding_surface_label_joins_collision_queue(qt_app):
    render_mod._install_skewt_sfc_label_mask()

    from sharppy.viz.skew import plotSkewT

    widget = _TraceWidget()
    widget.wetbulb = ma.masked_array(
        [20.0, 18.0], mask=[False, False])
    widget.tmpc = ma.masked_array(
        [22.0, 19.0], mask=[False, False])
    widget.dwpc = ma.masked_array(
        [18.0, 16.0], mask=[False, False])
    widget._sharpmod_collect_sfc_labels = True
    widget._sharpmod_sfc_label_queue = []
    secondary = ma.masked_array(
        [21.0, 17.0], mask=[False, False])
    painter = _RecordingPainter()

    plotSkewT.drawTrace(widget, secondary, "#888888", painter)

    assert painter.texts == []
    assert len(widget._sharpmod_sfc_label_queue) == 1
    assert widget._sharpmod_sfc_label_queue[0]["dedupe_key"] == (
        "source", id(secondary))


def test_surface_trace_label_clamps_right_and_flips_above_bottom(qt_app):
    render_mod._install_skewt_sfc_label_mask()

    from sharppy.viz.skew import plotSkewT

    widget = _TraceWidget()
    painter = _RecordingPainter()
    data = ma.masked_array([33.3, 30.0], mask=[False, False])

    plotSkewT.drawTrace(widget, data, "#ff0000", painter)

    text_rect, text = painter.texts[-1]
    assert text == "92"
    _assert_inside_plot(widget, text_rect)
    assert text_rect.bottom() < widget.bry


def test_effective_layer_sfc_label_clamps_left_and_flips_above_bottom(
        qt_app, monkeypatch):
    render_mod._install_skewt_effective_layer_label_fit()

    import sharppy.viz.skew as skew_mod

    monkeypatch.setattr(skew_mod.tab.utils, "QC", lambda _value: True)
    monkeypatch.setattr(skew_mod.tab.utils, "INT2STR",
                        lambda value: str(int(value)))
    monkeypatch.setattr(
        skew_mod.tab.interp,
        "hght",
        lambda prof, pres: 0.0 if pres == prof.pres[prof.sfc] else 1000.0,
    )

    widget = _EffectiveLayerWidget()
    painter = _RecordingPainter()

    skew_mod.plotSkewT.draw_effective_layer(widget, painter)

    sfc_rect = next(rect for rect, text in painter.texts if text == "SFC")
    _assert_inside_plot(widget, sfc_rect)
    assert sfc_rect.bottom() < widget.bry


# --------------------------------------------------------------------------- #
# Winter panel: the growth-zone block, and the layout that has to hold it
# --------------------------------------------------------------------------- #
_WINTER_PANEL_SIZES = ((320, 300), (420, 400), (256, 300), (200, 260))

#: Every row the vendored panel showed before the growth-zone block grew.
_WINTER_ORIGINAL_ROWS = (
    "DENDRITIC GROWTH ZONE",
    "OPRH",
    "Layer Depth:",
    "Mean Layer RH:",
    "Mean Layer PW:",
    "Mean Layer MixRat:",
    "Mean Layer Omega:",
    "Phase:",
    "BEST GUESS PRECIP TYPE",
    "TEMPERATURE PROFILE",
    "WETBULB PROFILE",
)


@pytest.fixture(scope="module")
def _winter_profile():
    from sharpmod.tests._examples import examples_dir

    example = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"
    if not example.exists():
        pytest.skip("bundled example sounding is not present")
    render_mod.install_render_patches()
    prof_col, _stn = render_mod.decode(str(example))
    return prof_col.getHighlightedProf()


def _winter_widget(size, profile):
    """A winter panel of a given size, fed a real profile."""
    from sharppy.viz.winter import plotWinter

    widget = plotWinter()
    widget.resize(*size)
    # As with the fire panel: offscreen ``resize`` delivers no resizeEvent, and
    # that event is what recomputes the height-scaled font.
    widget.initUI()
    widget.setProf(profile)
    return widget


def _winter_rows(widget):
    painter = _PanelPainter()
    widget.draw_frame(painter)
    widget.drawOPRH(painter)
    widget.drawDGZLayer(painter)
    widget.drawInitial(painter)
    widget.drawPrecipType(painter)
    widget.drawPrecipTypeTemp(painter)
    widget.drawWCLayer(painter)
    return painter


def _row_texts(painter):
    return [item.text for item in painter.texts if item.text.strip()]


@pytest.mark.parametrize("size", _WINTER_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _WINTER_PANEL_SIZES])
def test_winter_panel_rows_stay_inside_the_panel_height(
        qt_app, _winter_profile, size):
    """The winter panel drew its last rows below its own frame.

    Row height came from font metrics with no reference to the height available,
    and the slot positions were identical at every panel size. Measured on a
    real profile at 200x260, three rows -- the precipitation type among them --
    were drawn past the bottom edge.
    """
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget(size, _winter_profile)
    try:
        painter = _winter_rows(widget)

        assert painter.texts, "the winter panel drew nothing"
        below = [item.text for item in painter.texts
                 if item.text.strip() and item.rect.bottom() > widget.bry]
        assert below == [], below
    finally:
        widget.close()


@pytest.mark.parametrize("size", _WINTER_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _WINTER_PANEL_SIZES])
def test_winter_panel_rows_never_share_a_line(qt_app, _winter_profile, size):
    """Fitting rows into the panel must not stack them on top of each other."""
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget(size, _winter_profile)
    try:
        painter = _winter_rows(widget)
        rows = [item for item in painter.texts if item.text.strip()]
        for first in rows:
            for second in rows:
                if first is second:
                    continue
                # Rows sharing a line legitimately sit side by side in the two
                # columns; only an overlap in *both* axes is a collision.
                if first.rect.intersects(second.rect):
                    raise AssertionError(
                        f"{first.text!r} overlaps {second.text!r}")
    finally:
        widget.close()


@pytest.mark.parametrize("size", _WINTER_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _WINTER_PANEL_SIZES])
def test_winter_panel_rows_fit_the_columns_they_are_drawn_into(
        qt_app, _winter_profile, size):
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget(size, _winter_profile)
    try:
        for item in _winter_rows(widget).texts:
            if not item.text.strip():
                continue
            metrics = QtGui.QFontMetrics(item.font)
            assert metrics.horizontalAdvance(item.text) <= \
                item.rect.width() + 2, item.text
    finally:
        widget.close()


def test_winter_panel_row_height_responds_to_the_panel_height(
        qt_app, _winter_profile):
    """The fault was that it did not: every size laid out identically."""
    pytest.importorskip("sharppy.viz.winter")

    short = _winter_widget((260, 260), _winter_profile)
    tall = _winter_widget((260, 460), _winter_profile)
    try:
        short_rows = _winter_rows(short)
        tall_rows = _winter_rows(tall)
        short_bottom = max(item.rect.bottom() for item in short_rows.texts)
        tall_bottom = max(item.rect.bottom() for item in tall_rows.texts)

        assert short_bottom < tall_bottom
        assert short_bottom <= short.bry
        assert tall_bottom <= tall.bry
    finally:
        short.close()
        tall.close()


@pytest.mark.parametrize("size", _WINTER_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _WINTER_PANEL_SIZES])
def test_winter_panel_reports_the_growth_zone_in_pressure(
        qt_app, _winter_profile, size):
    """The zone was only ever given in feet MSL.

    The Skew-T's own axis is pressure and the band is drawn against it, so the
    bounds are now stated in both.
    """
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget(size, _winter_profile)
    try:
        texts = _row_texts(_winter_rows(widget))
        bounds = [text for text in texts if text.startswith("DGZ:")]

        assert len(bounds) == 1, texts
        assert "hPa" in bounds[0]
        # Still reported in feet as well, so nothing was traded away for it.
        assert any("ft msl" in text for text in texts)
    finally:
        widget.close()


@pytest.mark.parametrize("size", _WINTER_PANEL_SIZES,
                         ids=["%dx%d" % s for s in _WINTER_PANEL_SIZES])
def test_winter_panel_reports_the_snow_to_liquid_ratio(
        qt_app, _winter_profile, size):
    """Upstream computed no snow ratio at all, in any panel."""
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget(size, _winter_profile)
    try:
        texts = _row_texts(_winter_rows(widget))
        rows = [text for text in texts if "Kuchera SLR" in text]

        assert len(rows) == 1, texts
        assert rows[0].rstrip().endswith(":1") or rows[0].endswith("M")
    finally:
        widget.close()


def test_winter_panel_names_the_method_behind_the_ratio(
        qt_app, _winter_profile):
    """Snow-ratio methods disagree widely, so the row has to say which one."""
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget((320, 300), _winter_profile)
    try:
        assert any("Kuchera" in text
                   for text in _row_texts(_winter_rows(widget)))
    finally:
        widget.close()


def test_winter_panel_says_so_when_the_ratio_is_unavailable(
        qt_app, _winter_profile, monkeypatch):
    """A profile it cannot answer for must not produce a plausible number."""
    pytest.importorskip("sharppy.viz.winter")
    from sharpmod.sharptab import winter as winter_calc

    monkeypatch.setattr(
        winter_calc, "profile_snow_liquid_ratio", lambda *_a, **_k: None)

    widget = _winter_widget((320, 300), _winter_profile)
    try:
        rows = [text for text in _row_texts(_winter_rows(widget))
                if "Kuchera SLR" in text]

        assert rows == ["Kuchera SLR: M"]
    finally:
        widget.close()


def test_winter_panel_survives_a_failing_ratio_calculation(
        qt_app, _winter_profile, monkeypatch):
    """One panel row is not worth losing the panel over."""
    pytest.importorskip("sharppy.viz.winter")
    from sharpmod.sharptab import winter as winter_calc

    def _explode(*_args, **_kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(winter_calc, "profile_snow_liquid_ratio", _explode)

    widget = _winter_widget((320, 300), _winter_profile)
    try:
        texts = _row_texts(_winter_rows(widget))

        assert "Kuchera SLR: M" in texts
        assert any("Mean Layer RH:" in text for text in texts)
    finally:
        widget.close()


@pytest.mark.parametrize("expected", _WINTER_ORIGINAL_ROWS)
def test_winter_panel_keeps_every_row_it_used_to_show(
        qt_app, _winter_profile, expected):
    """Making room must not have been achieved by dropping something."""
    pytest.importorskip("sharppy.viz.winter")

    widget = _winter_widget((420, 400), _winter_profile)
    try:
        texts = _row_texts(_winter_rows(widget))

        assert any(expected in text for text in texts), texts
    finally:
        widget.close()
