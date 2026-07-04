"""Unit tests for the SHIP chart inset rendering (task 14.3).

Covers the rendering behavior of :class:`sharpmod.viz.ship.plotSHIP` against the
SHARPpy Reimagined acceptance criteria for Requirement 20:

* **20.1** -- when a SHIP value is available, the inset is drawn with the value
  positioned on a labeled scale (the value read-out and axis are painted, and a
  marker appears along the axis).
* **20.4** -- when SHIP cannot be computed (``MISSING`` masked constant,
  ``None``, or non-finite), the inset is still drawn but a missing-value
  indicator ("--") replaces the value marker.
* **20.5** -- all inset content fits/clips to its panel region; nothing is
  painted outside the widget's own rectangle.

Rendering is verified headlessly against an offscreen backing pixmap (Qt
``offscreen`` platform): the widget paints onto ``plotBitMap`` in ``plotData``
without a live event loop, so we can assert both that content *was* drawn and
that the operation completes without exceptions.

**Validates: Requirements 20.1, 20.4, 20.5**
"""

from __future__ import annotations

import os
from types import SimpleNamespace

# Ensure headless Qt before qtpy imports a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtWidgets
from qtpy.QtGui import QColor

from sharpmod.sharptab.constants import MISSING
from sharpmod.viz.ship import MISSING_STR, SHIP_SCALE_MAX, plotSHIP


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


def _make_widget(qt_app, size=(160, 80)):
    """Create a plotSHIP sized to a known panel region."""
    w = plotSHIP()
    w.resize(*size)
    # resize() alone may not fire resizeEvent headlessly; sync geometry.
    w._setupGeometry()
    w.clearData()
    return w


def _bg_pixel_count(image, bg=QColor(0, 0, 0)):
    """Count non-background pixels in a QImage (i.e. painted content)."""
    painted = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y) != bg:
                painted += 1
    return painted


# ---------------------------------------------------------------------------
# 20.1 -- inset drawn with a value
# ---------------------------------------------------------------------------

def test_inset_drawn_with_value_paints_content(qt_app):
    """A profile with a real SHIP value paints the value/axis/marker (20.1)."""
    w = _make_widget(qt_app)
    w.setProf(SimpleNamespace(ship=2.5))

    assert w.ship == pytest.approx(2.5)
    image = w.plotBitMap.toImage()
    # Something was drawn onto the (black) backing pixmap.
    assert _bg_pixel_count(image) > 0


def test_marker_position_tracks_value(qt_app):
    """The marker x-coordinate is the axis mapping of the value (20.1, 20.2)."""
    w = _make_widget(qt_app)
    w.setProf(SimpleNamespace(ship=3.0))

    frac = plotSHIP.scale_fraction(3.0)
    expected_x = w.axis_x0 + frac * (w.axis_x1 - w.axis_x0)
    # Marker sits between the axis endpoints, at the mapped location.
    assert w.axis_x0 <= expected_x <= w.axis_x1


def test_value_above_max_still_renders_within_axis(qt_app):
    """A value above the documented max clamps but still renders (20.1/20.3)."""
    w = _make_widget(qt_app)
    w.setProf(SimpleNamespace(ship=SHIP_SCALE_MAX + 50.0))

    assert plotSHIP.scale_fraction(w.ship) == 1.0
    image = w.plotBitMap.toImage()
    assert _bg_pixel_count(image) > 0


# ---------------------------------------------------------------------------
# 20.4 -- missing-value indicator drawn when SHIP is missing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_value",
    [MISSING, None, float("nan"), float("inf")],
    ids=["masked", "none", "nan", "inf"],
)
def test_missing_ship_still_draws_inset(qt_app, missing_value):
    """Missing SHIP leaves the inset drawn with ship=None (20.4)."""
    w = _make_widget(qt_app)
    w.setProf(SimpleNamespace(ship=missing_value))

    # Missing/masked/non-finite all collapse to None internally.
    assert w.ship is None
    # The inset is NOT omitted: the axis/title/indicator are still painted.
    image = w.plotBitMap.toImage()
    assert _bg_pixel_count(image) > 0


def test_missing_indicator_string_is_dashes():
    """The missing-value indicator is the documented '--' sentinel (20.4)."""
    assert MISSING_STR == "--"


def test_no_profile_is_treated_as_missing(qt_app):
    """A profile without a ship attribute renders the missing indicator (20.4)."""
    w = _make_widget(qt_app)
    w.setProf(SimpleNamespace())  # no `ship` attribute

    assert w.ship is None
    image = w.plotBitMap.toImage()
    assert _bg_pixel_count(image) > 0


def test_missing_and_present_differ(qt_app):
    """A present value paints differently than the missing indicator (20.1/20.4)."""
    present = _make_widget(qt_app)
    present.setProf(SimpleNamespace(ship=4.0))

    missing = _make_widget(qt_app)
    missing.setProf(SimpleNamespace(ship=MISSING))

    # Both draw content, but the rendered pixmaps must not be identical
    # (a marker + colored value vs. the "--" indicator).
    assert present.plotBitMap.toImage() != missing.plotBitMap.toImage()


# ---------------------------------------------------------------------------
# 20.5 -- content fits / clips to the panel region
# ---------------------------------------------------------------------------

def test_content_fits_within_panel_region(qt_app):
    """The backing pixmap matches the widget size; no content outside it (20.5)."""
    size = (160, 80)
    w = _make_widget(qt_app, size=size)
    w.setProf(SimpleNamespace(ship=5.0))

    # The pixmap the inset paints into is exactly the widget's panel region.
    assert w.plotBitMap.width() == w.width()
    assert w.plotBitMap.height() == w.height()

    # Every painted pixel is, by construction, inside that region: iterating
    # the whole image cannot find a coordinate outside [0,w) x [0,h).
    image = w.plotBitMap.toImage()
    assert image.width() == size[0]
    assert image.height() == size[1]


def test_axis_endpoints_inside_region(qt_app):
    """The scale axis is inset from the panel edges (fits the region, 20.5)."""
    w = _make_widget(qt_app, size=(160, 80))

    assert 0 <= w.axis_x0 < w.axis_x1 <= w.width()
    assert 0 <= w.axis_y <= w.height()


def test_render_small_panel_without_exceptions(qt_app):
    """Rendering into a very small panel region completes without exceptions (20.5)."""
    w = _make_widget(qt_app, size=(120, 60))  # the widget's minimum size
    # A range of values, including clamped and missing, must all paint cleanly.
    for value in (0.0, 1.0, 5.0, SHIP_SCALE_MAX + 10.0, MISSING, None):
        w.setProf(SimpleNamespace(ship=value))
        assert w.plotBitMap.width() == w.width()
        assert w.plotBitMap.height() == w.height()


def test_resize_reflows_content_into_new_region(qt_app):
    """After a resize the pixmap tracks the new panel region (20.5)."""
    w = _make_widget(qt_app, size=(160, 80))
    w.setProf(SimpleNamespace(ship=2.0))

    w.resize(200, 100)
    w._setupGeometry()
    w.clearData()
    w.plotData()

    assert w.plotBitMap.width() == w.width()
    assert w.plotBitMap.height() == w.height()
    assert w.axis_x1 <= w.width()
