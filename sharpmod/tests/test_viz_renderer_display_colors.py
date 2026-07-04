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
from qtpy import QtWidgets

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
    hpi=4.7,
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
    assert rows["HPI"] == "4.7"
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
        lrghail=2.3, hpi=4.7, peskov=0.8, mcs_index=3.1,
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
