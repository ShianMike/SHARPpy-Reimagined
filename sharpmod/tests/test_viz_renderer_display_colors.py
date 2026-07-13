"""Unit tests for renderer display and color substitutions (task 17.6).

Covers three renderer-facing behaviors against the SHARPpy Reimagined acceptance criteria:

* **Derived-index table display (Req 22.1 / the derived-parameter families).**
  :func:`sharpmod.viz.thermo.derived_rows` and :class:`plotDerivedIndices`
  show each parameter's value and, for an unavailable value, the documented
  missing-value indicator ("--").
* **Verbatim Possible Hazard Type label (Req 9.5).** The hazard widget draws,
  as text, the EXACT label produced by ``hazard.classify`` -- character for
  character, with no transformation. :func:`hazard_label_text` matches
  ``classify`` output and the widget's drawn ``label`` matches it too.
* **Concrete color substitutions (Req 22.2, 22.3, 22.4).** The documented
  legacy->modern substitutions ``LBROWN -> #c9a24b``, ``alert_l1 -> #c8911f``,
  and ``alert_l2 -> #e0a800`` are applied, and the amber-tier substitutions are
  exactly the values pushed to every panel via ``scheme_preferences`` /
  ``reapply_color_scheme``.

Rendering is verified headlessly (Qt ``offscreen`` platform): widgets paint onto
their backing pixmap in ``plotData`` without a live event loop.

**Validates: Requirements 22.1, 22.2, 22.3, 9.5**
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Ensure headless Qt before qtpy imports a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtGui, QtWidgets

from sharpmod import colors
from sharpmod.sharptab.constants import MISSING
from sharpmod.sharptab.hazard import HAZARD_LABELS, classify
from sharpmod.viz.hazard import HAZARD_LABEL_COLORS, hazard_label_text, plotHazard
from sharpmod.viz.thermo import (
    DERIVED_INDEX_ROWS,
    MISSING_STR,
    derived_rows,
    plotDerivedIndices,
)
from sharpmod.viz.SPCWindow import reapply_color_scheme


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    """A single offscreen QApplication for the module's widget tests."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


#: A Profile exposing a value for every derived-index attribute.
_FULL_DERIVED = SimpleNamespace(
    dcp=1.5,
    lrghail=2.3,
    vgp=0.42,
    peskov=0.8,
    mcs_index=3.1,
    ehi_0_1km=1.2,
    ehi_0_3km=2.6,
    hgz_cape=350.0,
    cape_0_6km=1800.0,
)


def _bg_pixel_count(image, bg=(0, 0, 0)):
    """Count non-background pixels in a QImage (painted content)."""
    from qtpy.QtGui import QColor

    bg_color = QColor(*bg)
    painted = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y) != bg_color:
                painted += 1
    return painted


def _hazard_profile(label):
    """Build a namespace whose classify() result is exactly ``label``.

    Uses the SHARPpy convective attribute names the classifier reads directly
    (``mupcl``/``mucape``, ``right_esrh``, ``ebwspd``, ``stp_cin``,
    ``right_scp``, ``ship``) so no ``sharppy`` oracle is required.
    """
    presets = {
        # mucape < 25 -> no meaningful convection.
        "none": dict(mucape=10.0, right_esrh=0.0, ebwspd=0.0,
                     stp_cin=0.0, right_scp=0.0, ship=0.0),
        # stp>=1, scp>=1, esrh>=100, ebwd>=30.
        "tornado": dict(mucape=2500.0, right_esrh=250.0, ebwspd=55.0,
                        stp_cin=3.0, right_scp=6.0, ship=2.0),
        # scp>=1 (or ebwd>=40) but esrh below tornado threshold.
        "supercell": dict(mucape=2000.0, right_esrh=40.0, ebwspd=45.0,
                          stp_cin=0.0, right_scp=3.0, ship=0.0),
        # ship>=1, scp<1, ebwd<40.
        "hail": dict(mucape=2000.0, right_esrh=0.0, ebwspd=15.0,
                     stp_cin=0.0, right_scp=0.0, ship=2.5),
        # mucape>=1000, ebwd>=30, no rotation/hail.
        "wind": dict(mucape=1500.0, right_esrh=0.0, ebwspd=35.0,
                     stp_cin=0.0, right_scp=0.0, ship=0.0),
        # mucape>=500 (or ebwd>=20) low-end severe.
        "marginal": dict(mucape=700.0, right_esrh=0.0, ebwspd=10.0,
                         stp_cin=0.0, right_scp=0.0, ship=0.0),
    }
    return SimpleNamespace(**presets[label])


class _PrefRecorder:
    """A minimal panel stand-in that records ``setPreferences`` kwargs."""

    def __init__(self):
        self.captured = None
        self.update_gui = None
        self.calls = 0

    def setPreferences(self, update_gui=True, **prefs):
        self.calls += 1
        self.update_gui = update_gui
        self.captured = dict(prefs)


# ===========================================================================
# Derived-index table: values and missing-value indicators (Req 22.1)
# ===========================================================================

def test_derived_rows_show_all_present_values():
    """Every present derived value appears formatted in the index rows."""
    rows = dict(derived_rows(_FULL_DERIVED))
    assert rows["DCP"] == "1.5"
    assert rows["LRGHAIL"] == "2.3"
    assert rows["VGP"] == "0.42"
    assert rows["Peskov"] == "0.8"
    assert rows["MCS"] == "3.1"
    assert rows["EHI 0-1km"] == "1.2"
    assert rows["EHI 0-3km"] == "2.6"
    # J/kg CAPE readouts are rounded integers.
    assert rows["HGZ CAPE"] == "350"
    assert rows["6CAPE"] == "1800"
    # No value cell is a missing indicator when every value is present.
    assert MISSING_STR not in rows.values()


@pytest.mark.parametrize(
    "missing_value",
    [MISSING, None, float("nan"), float("inf")],
    ids=["masked", "none", "nan", "inf"],
)
def test_derived_rows_missing_value_shows_indicator(missing_value):
    """A missing/masked/non-finite value renders the '--' indicator (22.1)."""
    prof = SimpleNamespace(
        dcp=missing_value,
        lrghail=2.3, vgp=0.4, peskov=0.8, mcs_index=3.1,
        ehi_0_1km=1.2, ehi_0_3km=2.6, hgz_cape=350.0, cape_0_6km=1800.0,
    )
    rows = dict(derived_rows(prof))
    assert rows["DCP"] == MISSING_STR
    # Other rows still show their values (missing propagates per-row only).
    assert rows["LRGHAIL"] == "2.3"


def test_derived_rows_absent_attribute_is_missing():
    """A row whose attribute is absent collapses to the missing indicator."""
    prof = SimpleNamespace()  # no derived attributes at all
    rows = dict(derived_rows(prof))
    assert len(rows) == len(DERIVED_INDEX_ROWS)
    assert all(value == MISSING_STR for value in rows.values())


def test_missing_indicator_string_is_dashes():
    """The documented missing-value indicator is exactly '--'."""
    assert MISSING_STR == "--"
    assert colors.MISSING_STR == "--"


def test_derived_index_table_paints_values(qt_app):
    """The derived-index panel lays out the value rows and paints them (22.1)."""
    w = plotDerivedIndices()
    w.resize(160, 200)
    w.setProf(_FULL_DERIVED)

    # The panel exposes the same (label, value) rows it draws.
    assert w.rows == derived_rows(_FULL_DERIVED)
    image = w.plotBitMap.toImage()
    assert _bg_pixel_count(image) > 0


def test_derived_index_table_paints_missing_indicator(qt_app):
    """A profile with missing values still draws the table with '--' (22.1)."""
    prof = SimpleNamespace()  # everything missing
    w = plotDerivedIndices()
    w.resize(160, 200)
    w.setProf(prof)

    assert all(value == MISSING_STR for _label, value in w.rows)
    image = w.plotBitMap.toImage()
    # Title + rows of "--" are still painted.
    assert _bg_pixel_count(image) > 0


# ===========================================================================
# Verbatim Possible Hazard Type label (Req 9.5)
# ===========================================================================

@pytest.mark.parametrize(
    "label",
    ["none", "marginal", "tornado", "supercell", "wind", "hail"],
)
def test_hazard_label_text_matches_classify(label):
    """hazard_label_text returns classify()'s label verbatim (9.5)."""
    prof = _hazard_profile(label)
    expected = classify(prof)
    # Sanity: the constructed profile actually classifies as intended.
    assert expected == label
    # The renderer helper reproduces the classifier output character-for-char.
    assert hazard_label_text(prof) == expected
    assert hazard_label_text(prof) in HAZARD_LABELS


def test_insufficient_data_label_is_verbatim():
    """A profile missing a required input yields the exact 'insufficient data'."""
    prof = SimpleNamespace(
        mucape=2000.0, right_esrh=100.0, ebwspd=40.0,
        stp_cin=1.0, right_scp=1.0, ship=MISSING,  # one input missing
    )
    assert classify(prof) == "insufficient data"
    assert hazard_label_text(prof) == "insufficient data"


@pytest.mark.parametrize(
    "label",
    ["none", "marginal", "tornado", "supercell", "wind", "hail"],
)
def test_hazard_widget_draws_exact_classify_label(qt_app, label):
    """plotHazard.setProf stores/draws the EXACT classify() label (9.5)."""
    prof = _hazard_profile(label)
    w = plotHazard()
    w.resize(180, 60)
    w.setProf(prof)

    # The widget's drawn label is the verbatim classifier output.
    assert w.label == classify(prof)
    assert w.label == label
    # It is one of the defined labels and drew content onto the pixmap.
    assert w.label in HAZARD_LABELS
    image = w.plotBitMap.toImage()
    assert _bg_pixel_count(image) > 0


def test_hazard_widget_no_transformation_applied(qt_app):
    """The drawn text is not title-cased, mapped, or abbreviated (9.5)."""
    prof = _hazard_profile("supercell")
    w = plotHazard()
    w.resize(180, 60)
    w.setProf(prof)
    # Lowercase classifier label preserved verbatim (no "Supercell"/"SUP").
    assert w.label == "supercell"


# ===========================================================================
# Concrete color substitutions (Req 22.2, 22.3, 22.4)
# ===========================================================================

def test_sars_nontornadic_substitution_lbrown():
    """SARS non-tornadic match LBROWN(#996600) -> #c9a24b (22.2)."""
    assert colors.LBROWN_LEGACY == "#996600"
    assert colors.SARS_NONTOR_MATCH == "#c9a24b"
    assert colors.LEGACY_SUBSTITUTIONS[("sars_nontornadic", "#996600")] == "#c9a24b"


def test_alert_tier_substitutions_l1_l2():
    """Amber alert tiers l1 -> #c8911f and l2 -> #e0a800 (22.3)."""
    assert colors.ALERT_L1_COLOR == "#c8911f"
    assert colors.ALERT_L2_COLOR == "#e0a800"
    assert colors.LEGACY_SUBSTITUTIONS[
        ("alert_l1_color", colors.ALERT_L1_LEGACY)] == "#c8911f"
    assert colors.LEGACY_SUBSTITUTIONS[
        ("alert_l2_color", colors.ALERT_L2_LEGACY)] == "#e0a800"
    # The modernized alert palette carries the substitutions at tiers 1 and 2.
    assert colors.ALERT_TIERS[1] == "#c8911f"
    assert colors.ALERT_TIERS[2] == "#e0a800"


def test_scheme_preferences_carry_alert_substitutions():
    """scheme_preferences pushes the exact modern amber tiers (22.3, 22.4)."""
    prefs = colors.scheme_preferences()
    assert prefs["alert_l1_color"] == "#c8911f"
    assert prefs["alert_l2_color"] == "#e0a800"


def test_scheme_preferences_carry_unit_preferences():
    """Custom mounted panels receive the same unit keys as vendored panels."""
    config = {
        ("preferences", "temp_units"): "Celsius",
        ("preferences", "wind_units"): "m/s",
        ("preferences", "pw_units"): "cm",
    }

    prefs = colors.scheme_preferences(config)

    assert prefs["temp_units"] == "Celsius"
    assert prefs["wind_units"] == "m/s"
    assert prefs["pw_units"] == "cm"


def test_index_board_formats_display_units(qt_app):
    """IndexBoard converts the hardcoded legacy readouts from preferences."""
    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.setPreferences(
        update_gui=False,
        temp_units="Celsius",
        wind_units="m/s",
        pw_units="cm",
    )

    assert board._temp(68.0) == "20\u00b0C"
    assert board._pwat(1.0) == "2.5 cm"
    assert board._wind_unit() == "m/s"
    assert board._wind_scalar(20.0) == "10"
    assert board._dirspd((180.0, 20.0)) == "180/10"
    assert board._uv_dirspd((10.0, 0.0)) == "270/05"


def test_index_board_preserves_primary_widths_with_compact_composites(qt_app):
    """Convective/kinematic columns retain room while SHIP/composites tighten."""
    from qtpy.QtCore import QRect

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.resize(1000, 320)
    board.clearData()
    rects = {}
    board._col_conv = lambda _qp, rect, _rh: rects.update(conv=QRect(rect))
    board._col_kin = lambda _qp, rect, _rh: rects.update(kin=QRect(rect))
    board._col_comp = lambda _qp, rect, _rh: rects.update(comp=QRect(rect))

    board.plotData()

    assert rects["conv"].width() == 372
    assert rects["kin"].width() == 328
    assert rects["comp"].width() == 275
    assert rects["kin"].width() > rects["comp"].width()
    assert rects["comp"].x() + rects["comp"].width() == 999
    assert 1000 - (rects["comp"].x() + rects["comp"].width()) == 1


def test_index_board_uses_compact_mcs_index_label(qt_app):
    """The composite readout uses the compact MCS Index display label."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace()
    board.dp = SimpleNamespace(mcs_index=-4.0)
    records = []

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text.startswith("MCS"):
            records.append(text)

    board._text = capture_text
    board._ship_chart = lambda *args, **kwargs: None
    pixmap = QtGui.QPixmap(320, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_comp(painter, QRect(0, 0, 300, 400), 18)
    painter.end()

    assert "MCS Index = " in records
    assert "MCS Idx = " not in records


def test_three_column_stats_keep_numeric_values_at_normal_size(qt_app):
    """Smaller unit suffixes avoid shrinking MeanW and SigSvr values."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard
    from sharpmod.viz.unit_text import value_unit_width

    board = IndexBoard()
    board.sp = SimpleNamespace(mean_mixr=14.86, sig_severe=43265.0)
    board.dp = SimpleNamespace()
    records = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text in {"14.86 g/kg", "43265 m\u00b3/s\u00b3"}:
            records[text] = (QRect(rect), QtGui.QFont(qp.font()))

    board._text = capture_text
    pixmap = QtGui.QPixmap(360, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_conv(painter, QRect(0, 0, 392, 400), 18)
    painter.end()

    regular_px = board.rf.pixelSize()
    for text in ("14.86 g/kg", "43265 m\u00b3/s\u00b3"):
        rect, font = records[text]
        assert font.pixelSize() == regular_px
        assert value_unit_width(font, text) <= rect.width()


def test_three_column_stats_leave_one_line_between_lapse_rows(qt_app):
    """The three-column stats layout does not crowd the lapse-rate block."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace()
    board.dp = SimpleNamespace()
    lapse_ys = []

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text.endswith(" LR = "):
            lapse_ys.append(rect.y())

    board._text = capture_text
    pixmap = QtGui.QPixmap(360, 320)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    row_height = 13
    board._col_conv(painter, QRect(0, 0, 392, 300), row_height)
    painter.end()

    assert len(lapse_ys) == 5
    assert min(b - a for a, b in zip(lapse_ys, lapse_ys[1:])) >= row_height


def test_composite_reorders_ecape_lscp_and_wbz(qt_app):
    """ECAPE shares NCAPE's row, then LSCP precedes WBZ Height below it."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace()
    board.dp = SimpleNamespace(
        hgz_cape=638.0,
        ncape=0.13,
        wbz_height=4106.0,
        ecape=1417.0,
        lscp=3.1,
    )
    records = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text in {"LSCP = ", "WBZ Height = ", "ECAPE = ", "NCAPE = "}:
            records[text] = QRect(rect)

    board._text = capture_text
    board._ship_chart = lambda *args, **kwargs: None
    pixmap = QtGui.QPixmap(320, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_comp(painter, QRect(0, 0, 300, 400), 18)
    painter.end()

    assert records["ECAPE = "].y() == records["NCAPE = "].y()
    assert records["ECAPE = "].x() > records["NCAPE = "].x()
    assert records["LSCP = "].y() < records["WBZ Height = "].y()
    assert records["WBZ Height = "].y() - records["LSCP = "].y() == 18


def test_index_board_sfc500m_kinematics_row_is_neutral_white(qt_app):
    """SFC-500m kinematics row label and values render as normal white text."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        latitude=44.83,
        mupcl=SimpleNamespace(brnshear=82.0),
        right_srw_4_5km=(227.0, 25.0),
        srwind=(10.0, 0.0, -10.0, 0.0),
        upshear_downshear=(10.0, 10.0, -10.0, -10.0),
        wind1km=(291.0, 13.0),
        wind6km=(243.0, 42.0),
    )
    board.dp = SimpleNamespace(
        srh500=-2.0,
        shear_sfc_500m=1.0,
        mean_wind_sfc_500m=(10.0, 0.0),
        srw_sfc_500m=(0.0, 10.0),
    )
    records = []

    def capture_text(qp, rect, s, color=None, align=Qt.AlignLeft):
        records.append((s, (color or board.fg).name().lower()))

    board._text = capture_text
    board._draw_agl_barbs = lambda *args, **kwargs: None

    pixmap = QtGui.QPixmap(460, 320)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_kin(painter, QRect(0, 0, 440, 300), 18)
    painter.end()

    row_start = next(i for i, (text, _color) in enumerate(records)
                     if text == "SFC-500m")
    row = records[row_start:row_start + 5]
    assert [text for text, _color in row] == [
        "SFC-500m", "-2", "1", "270/10", "180/10"]
    assert {color for _text, color in row} == {board.fg.name().lower()}


def test_index_board_gives_wind_vector_columns_extra_width(qt_app):
    """MnWind and SRW retain room for three-digit direction/speed values."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        latitude=44.83,
        mean_1km=(359.0, 999.0),
        srw_1km=(358.0, 999.0),
    )
    board.dp = SimpleNamespace()
    records = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text in {"SRH", "Shear", "MnWind", "SRW", "359/999", "358/999"}:
            records[text] = QRect(rect)

    board._text = capture_text
    board._draw_agl_barbs = lambda *args, **kwargs: None
    pixmap = QtGui.QPixmap(300, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_kin(painter, QRect(0, 0, 280, 400), 18)
    painter.end()

    assert records["MnWind"].width() >= 60
    assert records["SRW"].width() >= 60
    assert records["MnWind"].width() == records["SRW"].width()
    assert records["MnWind"].width() > records["SRH"].width()
    assert records["SRW"].width() > records["Shear"].width()
    assert records["359/999"].width() == records["MnWind"].width()
    assert records["358/999"].width() == records["SRW"].width()


def test_index_board_moves_only_srh_track_toward_layer_labels(qt_app):
    """SRH shifts left while the other kinematics tracks stay anchored."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace()
    board.dp = SimpleNamespace(srh500=233.0)
    records = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text in {"SRH", "233", "Shear", "MnWind", "SRW"}:
            records[text] = QRect(rect)

    board._text = capture_text
    board._draw_agl_barbs = lambda *args, **kwargs: None
    pixmap = QtGui.QPixmap(460, 320)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_kin(painter, QRect(0, 0, 440, 300), 18)
    painter.end()

    label_w = int(440 * 0.28)
    srh_w = int(440 * 0.13)
    srh_shift = max(4, int(440 * 0.04))
    assert records["SRH"].x() == label_w - srh_shift
    assert records["233"].x() == records["SRH"].x()
    assert records["Shear"].x() == label_w + srh_w
    assert records["MnWind"].x() > records["Shear"].x()
    assert records["SRW"].x() > records["MnWind"].x()


def test_index_board_uses_five_lapse_rows_and_places_lrghail_below_moshe(qt_app):
    """Keep LRGHAIL below MOSHE and omit the 3-6 km lapse-rate row."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        pwat=1.0,
        k_idx=30.0,
        lapserate_3km=7.1,
        lapserate_3_6km=7.3,
        lapserate_850_500=6.5,
        lapserate_700_500=6.2,
        right_scp=0.7,
        stp_cin=0.7,
        stp_fixed=0.7,
        ship=0.7,
        mupcl=SimpleNamespace(bplus=1500.0, bminus=-25.0),
        sfcpcl=SimpleNamespace(bplus=1200.0, bminus=-50.0),
        mlpcl=SimpleNamespace(bplus=1000.0, bminus=-75.0),
        fcstpcl=SimpleNamespace(bplus=900.0, bminus=-40.0),
        mucape=1500.0,
    )
    board.dp = SimpleNamespace(
        lapserate_sfc_500m=10.0,
        lapserate_sfc_1km=8.0,
        dcp=0.8,
        lrghail=6.9,
        modified_sherbe=2.2,
    )
    records = []
    severe_colors = {}

    def capture_text(qp, rect, s, color=None, align=Qt.AlignLeft):
        records.append((s, rect.y(), rect.height()))
        if s in {"Supercell Comp = ", "STP(cin) = ", "STP(fix) = ",
                 "SHIP = ", "Derecho Comp = "}:
            severe_colors[s] = QtGui.QColor(color).name()

    board._text = capture_text

    pixmap = QtGui.QPixmap(500, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_conv(painter, QRect(0, 0, 480, 400), 18)
    board._col_comp(painter, QRect(0, 0, 400, 400), 18)
    painter.end()

    texts = [text for text, _y, _h in records]
    assert "SFC-500m LR = " in texts
    idx = texts.index("SFC-500m LR = ")
    assert texts[idx + 1] == "10.0 C/km"
    assert texts.count("Derecho Comp = ") == 1
    assert "3-6km LR = " not in texts
    assert texts.count("LRGHAIL = ") == 1
    assert set(severe_colors.values()) == {colors.ALERT_L1_COLOR}

    composite_y = {text: y for text, y, _h in records
                   if text in {"MOSHE = ", "LRGHAIL = "}}
    assert composite_y["LRGHAIL = "] - composite_y["MOSHE = "] == 18

    lapse_y = {text: y for text, y, _h in records if text.endswith(" LR = ")}

    severe_y = {
        text: y for text, y, _h in records
        if text in {"Supercell Comp = ", "STP(cin) = ", "STP(fix) = ",
                    "SHIP = ", "Derecho Comp = "}
    }
    lapse_step = lapse_y["SFC-1km LR = "] - lapse_y["SFC-500m LR = "]
    severe_step = severe_y["STP(cin) = "] - severe_y["Supercell Comp = "]
    assert severe_step == lapse_step
    assert lapse_step <= 21
    assert severe_y["Derecho Comp = "] - severe_y["SHIP = "] == severe_step
    for lapse_label, severe_label in zip(
            ("SFC-500m LR = ", "SFC-1km LR = ", "SFC-3km LR = ",
             "850-500 LR = ", "700-500 LR = "),
            ("Supercell Comp = ", "STP(cin) = ", "STP(fix) = ",
             "SHIP = ", "Derecho Comp = ")):
        assert lapse_y[lapse_label] == severe_y[severe_label]
    top_gap = severe_y["Supercell Comp = "] - lapse_y["SFC-500m LR = "]
    bottom_gap = lapse_y["700-500 LR = "] - severe_y["Derecho Comp = "]
    assert top_gap == 0
    assert bottom_gap == 0
    row_rects = {text: (y, h) for text, y, h in records}
    assert row_rects["700-500 LR = "][0] + row_rects["700-500 LR = "][1] == 400
    assert row_rects["Derecho Comp = "][0] + row_rects["Derecho Comp = "][1] == 400


def test_index_board_storm_vectors_share_the_bottom_baseline(qt_app):
    """The final storm-motion row uses the board's common bottom margin."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        latitude=44.83,
        mupcl=SimpleNamespace(brnshear=82.0),
        right_srw_4_5km=(227.0, 25.0),
        srwind=(10.0, 0.0, -10.0, 0.0),
        upshear_downshear=(10.0, 10.0, -10.0, -10.0),
        wind1km=(291.0, 13.0),
        wind6km=(243.0, 42.0),
    )
    board.dp = SimpleNamespace(
        srh500=-2.0,
        shear_sfc_500m=1.0,
        mean_wind_sfc_500m=(10.0, 0.0),
        srw_sfc_500m=(0.0, 10.0),
    )
    board._draw_agl_barbs = lambda *args, **kwargs: None
    records = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text == "Corfidi Ushr = ":
            records[text] = QRect(rect)

    board._text = capture_text
    pixmap = QtGui.QPixmap(460, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_kin(painter, QRect(0, 0, 440, 400), 18)
    painter.end()

    assert records["Corfidi Ushr = "].bottom() + 1 == 400


def test_index_board_storm_vectors_fit_compact_corfidi_readouts(qt_app):
    """Compact Corfidi labels retain room for full direction/speed values."""
    from qtpy.QtCore import QRect, Qt

    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        latitude=44.83,
        mupcl=SimpleNamespace(brnshear=82.0),
        right_srw_4_5km=(227.0, 25.0),
        srwind=(10.0, 0.0, -10.0, 0.0),
        upshear_downshear=(10.0, 10.0, -10.0, -10.0),
        wind1km=(291.0, 13.0),
        wind6km=(243.0, 42.0),
    )
    board.dp = SimpleNamespace(
        srh500=-2.0,
        shear_sfc_500m=1.0,
        mean_wind_sfc_500m=(10.0, 0.0),
        srw_sfc_500m=(0.0, 10.0),
    )
    records = {}
    active_label = {"text": None}
    barb_region = {}

    def capture_text(qp, rect, text, color=None, align=Qt.AlignLeft):
        if text in {"Bunkers Right = ", "Bunkers Left = ",
                    "Corfidi Dshr = ", "Corfidi Ushr = "}:
            active_label["text"] = text
            records[text] = QRect(rect)
        elif active_label["text"] is not None:
            records[active_label["text"] + "value"] = QRect(rect)
            active_label["text"] = None

    def capture_barbs(qp, x, y, w, h):
        barb_region["rect"] = QRect(x, y, w, h)

    board._text = capture_text
    board._draw_agl_barbs = capture_barbs
    pixmap = QtGui.QPixmap(300, 420)
    pixmap.fill(QtGui.QColor("#000000"))
    painter = QtGui.QPainter(pixmap)
    board._col_kin(painter, QRect(0, 0, 280, 400), 18)
    painter.end()

    assert set(records) == {
        "Bunkers Right = ", "Bunkers Right = value",
        "Bunkers Left = ", "Bunkers Left = value",
        "Corfidi Dshr = ", "Corfidi Dshr = value",
        "Corfidi Ushr = ", "Corfidi Ushr = value",
    }
    text_width = int(280 * 0.54)
    assert records["Corfidi Dshr = "].width() >= text_width
    assert records["Corfidi Ushr = "].width() >= text_width
    assert records["Corfidi Dshr = value"].width() > 0
    assert records["Corfidi Ushr = value"].width() > 0
    assert barb_region["rect"].left() == records["Corfidi Dshr = "].width()
    assert barb_region["rect"].width() >= 120


def test_index_board_agl_barbs_use_a_compact_scale(qt_app, monkeypatch):
    """The 1/6 km AGL barb cluster remains legible without dominating its cell."""
    from sharpmod.viz.index_board import IndexBoard

    board = IndexBoard()
    board.sp = SimpleNamespace(
        wind1km=(291.0, 13.0),
        wind6km=(243.0, 42.0),
        latitude=44.83,
    )
    scales = []
    original_path = board._barb_path

    def capture_path(wdir, wspd, shemis, scale):
        scales.append(scale)
        return original_path(wdir, wspd, shemis, scale)

    monkeypatch.setattr(board, "_barb_path", capture_path)
    pixmap = QtGui.QPixmap(240, 130)
    pixmap.fill(QtGui.QColor("#000000"))

    painter = QtGui.QPainter(pixmap)
    board._draw_agl_barbs(painter, 20, 10, 190, 100)
    painter.end()

    image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_RGB32)
    points = []
    for y in range(0, 80):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() > 20 or color.green() > 20 or color.blue() > 20:
                points.append((x, y))

    assert points
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    assert max(xs) - min(xs) + 1 >= 48
    assert max(ys) - min(ys) + 1 >= 44
    assert scales
    assert max(scales) <= 1.25


def test_reapply_pushes_substitutions_to_every_panel():
    """reapply_color_scheme applies the same substitutions to every panel (22.4)."""
    r1 = _PrefRecorder()
    r2 = _PrefRecorder()
    sw = SimpleNamespace(derived_indices=r1, hazard_type=r2)
    win = SimpleNamespace(spc_widget=sw)

    applied = reapply_color_scheme(win)

    assert "derived_indices" in applied
    assert "hazard_type" in applied
    expected = colors.scheme_preferences()
    # The concrete substitution values are exactly what each panel receives.
    for recorder in (r1, r2):
        assert recorder.calls == 1
        assert recorder.update_gui is True
        assert recorder.captured == expected
        assert recorder.captured["alert_l1_color"] == "#c8911f"
        assert recorder.captured["alert_l2_color"] == "#e0a800"


def test_reapply_is_consistent_across_panels():
    """All panels get identical preferences (consistent scheme, 22.4)."""
    recorders = [_PrefRecorder() for _ in range(3)]
    sw = SimpleNamespace(
        derived_indices=recorders[0],
        hazard_type=recorders[1],
        ship_inset=recorders[2],
    )
    win = SimpleNamespace(spc_widget=sw)

    reapply_color_scheme(win)

    captured = [r.captured for r in recorders]
    assert all(c == captured[0] for c in captured)
    assert captured[0]["alert_l1_color"] == "#c8911f"
    assert captured[0]["alert_l2_color"] == "#e0a800"


def test_hazard_label_colors_use_amber_substitutions():
    """The hazard widget's per-label colors reuse the modern amber tiers (22.3)."""
    # marginal -> alert_l2 (#e0a800); wind -> alert_l1 (#c8911f).
    assert HAZARD_LABEL_COLORS["marginal"] == colors.ALERT_L2_COLOR == "#e0a800"
    assert HAZARD_LABEL_COLORS["wind"] == colors.ALERT_L1_COLOR == "#c8911f"
