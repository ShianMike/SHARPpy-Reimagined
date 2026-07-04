"""Unit tests for Custom_Panel rendering.

**Validates: Requirements 10.2, 10.4, 10.5, 10.6, 10.7**

* **10.2** -- a configured named Profile parameter renders its current value
  together with its label and unit.
* **10.4** -- with no configuration, the panel defaults to the SARS inset
  content as its default content set.
* **10.5 / 10.7** -- content is fit / clipped to the panel's allocated slot;
  overflowing content is elided/clipped to the slot rather than expanding it.
* **10.6** -- a resolved-but-missing value renders the missing-value indicator
  in place of the value.

Text draws are captured with a ``QPainter`` subclass that records every
``drawText`` call so the tests can assert on the exact label/value/unit text
that reaches the canvas, headlessly, on the Qt ``offscreen`` platform.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Ensure headless Qt before qtpy imports a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtGui, QtWidgets

from sharpmod.sharptab.constants import MISSING
from sharpmod.viz import custom_panel
from sharpmod.viz.custom_panel import CustomPanel, PanelItem, MISSING_STR


class _RecordingPainter(custom_panel.QtGui.QPainter):
    """A ``QPainter`` that records every ``drawText(rect, flags, text)`` call."""

    calls: list[tuple[int, int, str]] = []

    def drawText(self, *args, **kwargs):  # noqa: N802 (Qt naming)
        rect = None
        text = None
        for a in args:
            if isinstance(a, str):
                text = a
            elif hasattr(a, "x") and hasattr(a, "y") and not isinstance(a, (int, float)):
                rect = a
        if text is not None and rect is not None:
            _RecordingPainter.calls.append((rect.x(), rect.y(), text))
        return super().drawText(*args, **kwargs)


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    original = custom_panel.QtGui.QPainter
    custom_panel.QtGui.QPainter = _RecordingPainter
    try:
        yield app
    finally:
        custom_panel.QtGui.QPainter = original


_LABEL_X = 8


def _capture(panel: CustomPanel):
    """Return ``(left_col_texts, right_col_texts)`` in draw order."""
    _RecordingPainter.calls.clear()
    panel.plotData()
    left = [t for (x, _, t) in _RecordingPainter.calls if x == _LABEL_X]
    right = [t for (x, _, t) in _RecordingPainter.calls if x != _LABEL_X]
    return left, right


# ---------------------------------------------------------------------------
# 10.2 -- label + value + unit rendering
# ---------------------------------------------------------------------------

def test_renders_label_value_and_unit(qt_app):
    """A resolved parameter draws its label and its value with the unit (10.2)."""
    panel = CustomPanel()
    panel.resize(360, 260)
    item = PanelItem(param="lapserate_sfc_1km")
    panel.configure([item])
    panel.setProf(SimpleNamespace(lapserate_sfc_1km=7.5))

    label, unit = panel._resolve_label_unit(item)
    assert label == "SFC-1km Lapse Rate"
    assert unit == "degrees C/km"

    left, right = _capture(panel)

    # The raw value text is the number plus the unit (10.2). We compare against
    # the panel's own elided output so the assertion is robust to the headless
    # font environment (Qt may elide differently without bundled fonts).
    column_w = (panel.width() - panel.lpad - panel.rpad) // 2
    expected_label = panel._elide(label, column_w, panel.label_font)
    raw_value = f"{panel._fmt_value(7.5)} {unit}"
    assert raw_value == "7.5 degrees C/km"
    expected_value = panel._elide(raw_value, column_w, panel.value_font)

    assert expected_label in left
    assert expected_value in right

    panel.deleteLater()


def test_item_label_and_unit_overrides_are_used(qt_app):
    """Explicit label/unit overrides on the item are rendered verbatim (10.2)."""
    panel = CustomPanel()
    panel.resize(360, 260)
    item = PanelItem(param="dcp", label="MyDCP", unit="x")
    panel.configure([item])
    panel.setProf(SimpleNamespace(dcp=3.0))

    left, right = _capture(panel)
    assert "MyDCP" in left
    assert "3 x" in right

    panel.deleteLater()


# ---------------------------------------------------------------------------
# 10.4 -- SARS default content when unconfigured
# ---------------------------------------------------------------------------

def test_unconfigured_panel_draws_sars_default(qt_app):
    """A never-configured panel draws the SARS default content set (10.4)."""
    panel = CustomPanel()
    panel.resize(360, 260)

    left, _ = _capture(panel)
    assert "SARS" in left
    for line in panel._sars_lines:
        assert line in left

    panel.deleteLater()


def test_configure_none_restores_sars_default(qt_app):
    """configure(None) selects the SARS default content set (10.4)."""
    panel = CustomPanel()
    panel.resize(360, 260)
    panel.configure([PanelItem(param="dcp")])
    panel.configure(None)

    left, right = _capture(panel)
    assert "SARS" in left
    # No configured item rows are drawn in the value column.
    assert right == []

    panel.deleteLater()


def test_set_sars_content_updates_default_lines(qt_app):
    """setSarsContent supplies the default lines drawn when unconfigured (10.4)."""
    panel = CustomPanel()
    panel.resize(360, 260)
    panel.setSarsContent(["ALPHA", "BRAVO"])

    left, _ = _capture(panel)
    assert "ALPHA" in left
    assert "BRAVO" in left

    panel.deleteLater()


# ---------------------------------------------------------------------------
# 10.6 -- missing-value indicator for resolved-but-missing values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", [None, MISSING, float("nan")])
def test_missing_value_indicator_for_resolved_but_missing(qt_app, bad_value):
    """A resolved parameter with no computed value draws MISSING_STR (10.6)."""
    panel = CustomPanel()
    panel.resize(360, 260)
    item = PanelItem(param="dcp")
    panel.configure([item])
    panel.setProf(SimpleNamespace(dcp=bad_value))

    _, right = _capture(panel)
    assert right == [MISSING_STR]

    panel.deleteLater()


# ---------------------------------------------------------------------------
# 10.5 / 10.7 -- content fit / clipped to the slot
# ---------------------------------------------------------------------------

def test_backing_pixmap_matches_widget_slot(qt_app):
    """The backing canvas is sized to the widget slot so drawing is bounded (10.5)."""
    panel = CustomPanel()
    panel.resize(200, 150)
    # A redraw rebuilds the backing canvas to the current slot dimensions, so no
    # content can be painted outside the slot boundaries.
    panel.clearData()
    panel.plotData()
    assert panel.plotBitMap.width() == panel.width()
    assert panel.plotBitMap.height() == panel.height()

    panel.deleteLater()


def test_long_label_is_elided_within_its_column(qt_app):
    """A too-long label is elided to fit its column, not drawn past it (10.7)."""
    panel = CustomPanel()
    panel.resize(160, 120)  # deliberately narrow so a long label overflows
    long_label = "X" * 200
    item = PanelItem(param="dcp", label=long_label, unit="")
    panel.configure([item])
    panel.setProf(SimpleNamespace(dcp=1.0))

    left, _ = _capture(panel)
    drawn_label = next(t for t in left if t != panel.title)

    # The elided label is shorter than the original and fits the column width.
    column_w = (panel.width() - panel.lpad - panel.rpad) // 2
    metrics = QtGui.QFontMetrics(panel.label_font)
    assert drawn_label != long_label
    assert metrics.horizontalAdvance(drawn_label) <= column_w

    panel.deleteLater()


def test_overflow_note_and_rows_confined_to_twelve(qt_app):
    """Over-12 configs draw only 12 rows plus one overflow note (10.7)."""
    items = [PanelItem(param=f"p{i}") for i in range(20)]
    panel = CustomPanel()
    panel.resize(320, 320)
    panel.configure(items)
    panel.setProf(SimpleNamespace())

    left, right = _capture(panel)
    assert len(right) == CustomPanel.MAX_ITEMS
    assert "+8 more items not shown" in left

    panel.deleteLater()
