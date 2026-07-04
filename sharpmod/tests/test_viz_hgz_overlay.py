"""Unit tests for the Hail Growth Zone (HGZ) skew-T overlay pass.

Covers the overlay behavior defined in ``sharpmod.viz.skew`` against the
SHARPpy Reimagined acceptance criteria for Requirement 19:

* **19.9** -- when ``prof.hgz_cape`` is *available* (a numeric, non-``MISSING``
  value), the overlay shades/annotates the -10 degrees C to -30 degrees C band
  within the skew-T plot area.
* **19.10** -- the overlay is confined to (clipped/clamped to) the plot
  rectangle; no overlay content is drawn outside the plot area.
* **19.11** -- when ``prof.hgz_cape`` is *missing*, no overlay is drawn at all.

The overlay helper is deliberately decoupled from a live SkewT widget: it takes
the plot rectangle plus a ``pres_to_pix`` transform callable, so these tests
exercise it headlessly with a synthetic profile and a plain transform. Real
Qt painting is verified against an offscreen :class:`QImage` (Qt ``offscreen``
platform), so we can confirm both that something *was* drawn and that *nothing*
was drawn outside the plot rectangle.

**Validates: Requirements 19.9, 19.10, 19.11**
"""

from __future__ import annotations

import math
import os
from types import SimpleNamespace

# Ensure headless Qt before qtpy imports a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy import QtGui
from qtpy.QtCore import QRect
from qtpy.QtGui import QColor, QImage, QPainter

from sharpmod.sharptab.constants import MISSING
from sharpmod.viz import skew


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    """A single offscreen QGuiApplication for the module's painting tests."""
    app = QtGui.QGuiApplication.instance()
    if app is None:
        app = QtGui.QGuiApplication([])
    return app


# Plot rectangle used by the tests (widget pixels).
PLOT_RECT = QRect(50, 30, 300, 400)  # left=50, top=30, right=350, bottom=430

# Pressure axis bounds used by the linear-in-log10 transform.
_P_BOTTOM = 1050.0  # maps to plot bottom (high pressure, low altitude)
_P_TOP = 100.0      # maps to plot top (low pressure, high altitude)


def _make_prof(hgz_cape):
    """Build a minimal profile stub spanning the -10 / -30 degrees C isotherms.

    ``pres_at_isotherm`` only reads ``pres``, ``tmpc`` and ``hght``; the overlay
    additionally reads ``hgz_cape``. Temperatures cross -10 degrees C (at
    600 hPa) and -30 degrees C (at 400 hPa) so both isotherms resolve.
    """
    return SimpleNamespace(
        pres=np.array([1000.0, 850.0, 700.0, 600.0, 500.0, 400.0, 300.0]),
        tmpc=np.array([20.0, 10.0, 0.0, -10.0, -20.0, -30.0, -45.0]),
        hght=np.array([100.0, 1500.0, 3000.0, 4200.0, 5500.0, 7000.0, 9000.0]),
        hgz_cape=hgz_cape,
    )


def _pres_to_pix(p):
    """Linear-in-log10 pressure->pixel transform within :data:`PLOT_RECT`.

    High pressure maps to the plot bottom, low pressure to the plot top, so the
    -10 / -30 degrees C isotherms land inside the plot rectangle.
    """
    lp = math.log10(float(p))
    lo = math.log10(_P_TOP)
    hi = math.log10(_P_BOTTOM)
    frac = (hi - lp) / (hi - lo)  # 0 at bottom pressure, 1 at top pressure
    top = float(PLOT_RECT.top())
    height = float(PLOT_RECT.height())
    return top + frac * height


def _blank_image(color=QColor("white")):
    """A plot-sized-plus-margin ARGB image filled with a solid background."""
    img = QImage(400, 480, QImage.Format_ARGB32)
    img.fill(color)
    return img


# ---------------------------------------------------------------------------
# 19.9 -- overlay present when hgz_cape is available
# ---------------------------------------------------------------------------

def test_band_pixels_present_when_hgz_cape_available():
    """band_pixels resolves a real, in-plot band when hgz_cape is numeric."""
    prof = _make_prof(hgz_cape=250.0)
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)

    band = overlay.band_pixels(prof)

    assert band is not None
    top, bottom = band
    assert top < bottom  # non-degenerate band
    # The band must sit inside the plot rectangle's vertical extent (19.9/19.10).
    assert top >= PLOT_RECT.top()
    assert bottom <= PLOT_RECT.bottom()


def test_draw_returns_true_and_paints_inside_band(qt_app):
    """draw() reports it drew, and pixels inside the band change (19.9)."""
    prof = _make_prof(hgz_cape=250.0)
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)
    top, bottom = overlay.band_pixels(prof)

    img = _blank_image()
    qp = QPainter(img)
    try:
        drew = overlay.draw(qp, prof)
    finally:
        qp.end()

    assert drew is True
    # A pixel in the middle of the band, mid-width, should no longer be white.
    mid_x = PLOT_RECT.left() + PLOT_RECT.width() // 2
    mid_y = int((top + bottom) / 2)
    assert img.pixelColor(mid_x, mid_y) != QColor("white")


def test_draw_hgz_overlay_free_function_matches_class(qt_app):
    """The free-function wrapper draws equivalently to the class helper."""
    prof = _make_prof(hgz_cape=180.0)

    img = _blank_image()
    qp = QPainter(img)
    try:
        drew = skew.draw_hgz_overlay(qp, prof, PLOT_RECT, _pres_to_pix)
    finally:
        qp.end()

    assert drew is True


# ---------------------------------------------------------------------------
# 19.11 -- overlay absent when hgz_cape is missing
# ---------------------------------------------------------------------------

def test_band_pixels_none_when_hgz_cape_missing():
    """No band is produced when hgz_cape is the MISSING sentinel (19.11)."""
    prof = _make_prof(hgz_cape=MISSING)
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)

    assert overlay.band_pixels(prof) is None


def test_band_pixels_none_when_hgz_cape_nan():
    """A NaN hgz_cape is also treated as missing -> no band (19.11)."""
    prof = _make_prof(hgz_cape=float("nan"))
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)

    assert overlay.band_pixels(prof) is None


def test_draw_absent_leaves_image_untouched(qt_app):
    """draw() returns False and paints nothing when hgz_cape is missing (19.11)."""
    prof = _make_prof(hgz_cape=MISSING)
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)

    before = _blank_image()
    img = _blank_image()
    qp = QPainter(img)
    try:
        drew = overlay.draw(qp, prof)
    finally:
        qp.end()

    assert drew is False
    assert img == before  # not a single pixel changed


def test_draw_absent_when_isotherms_not_spanned():
    """If the profile never reaches -10/-30 degrees C, nothing is drawn (19.11)."""
    prof = _make_prof(hgz_cape=250.0)
    # Warm the whole column so -10 / -30 degrees C are never crossed.
    prof.tmpc = np.array([25.0, 20.0, 15.0, 12.0, 10.0, 8.0, 5.0])
    overlay = skew.HGZOverlay(PLOT_RECT, _pres_to_pix)

    assert overlay.band_pixels(prof) is None


# ---------------------------------------------------------------------------
# 19.10 -- overlay confined to / clipped to the plot area
# ---------------------------------------------------------------------------

def test_band_clamped_to_plot_rect_when_transform_overflows():
    """A transform that maps isotherms outside the plot clamps to the rect (19.10)."""
    prof = _make_prof(hgz_cape=250.0)

    def overflowing_transform(p):
        # -10 degrees C (~600 hPa) below the bottom, -30 degrees C (~400 hPa)
        # above the top -- an intentionally oversized band. The 500 hPa split
        # cleanly separates the two isotherm pressures.
        if float(p) >= 500.0:
            return float(PLOT_RECT.bottom()) + 100.0
        return float(PLOT_RECT.top()) - 100.0

    overlay = skew.HGZOverlay(PLOT_RECT, overflowing_transform)
    band = overlay.band_pixels(prof)

    assert band is not None
    top, bottom = band
    assert top >= PLOT_RECT.top()
    assert bottom <= PLOT_RECT.bottom()


def test_draw_paints_nothing_outside_plot_rect(qt_app):
    """No overlay pixels land outside the plot rectangle (clip, 19.10).

    Uses an oversized band (transform overflows the plot on both ends) so the
    only thing keeping paint inside the plot area is the clamp + clip.
    """
    prof = _make_prof(hgz_cape=250.0)

    def overflowing_transform(p):
        if float(p) >= 500.0:
            return float(PLOT_RECT.bottom()) + 100.0
        return float(PLOT_RECT.top()) - 100.0

    overlay = skew.HGZOverlay(PLOT_RECT, overflowing_transform)

    img = _blank_image()
    qp = QPainter(img)
    try:
        drew = overlay.draw(qp, prof)
    finally:
        qp.end()

    assert drew is True

    white = QColor("white")
    # Sample points strictly outside the plot rectangle on every side; each
    # must remain the untouched background color.
    outside_points = [
        (10, 10),                                   # top-left margin
        (PLOT_RECT.left() - 5, PLOT_RECT.top() + 50),   # left of plot
        (PLOT_RECT.right() + 20, PLOT_RECT.top() + 50),  # right of plot
        (PLOT_RECT.left() + 50, PLOT_RECT.top() - 5),    # above plot
        (PLOT_RECT.left() + 50, PLOT_RECT.bottom() + 5),  # below plot
    ]
    for x, y in outside_points:
        assert img.pixelColor(x, y) == white, f"pixel ({x},{y}) was painted outside plot"

    # Sanity: a pixel well inside the (now full-height) band was painted.
    inside_x = PLOT_RECT.left() + PLOT_RECT.width() // 2
    inside_y = PLOT_RECT.top() + PLOT_RECT.height() // 2
    assert img.pixelColor(inside_x, inside_y) != white
