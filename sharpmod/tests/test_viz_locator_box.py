"""The locator inset must show the area an averaged sounding came from.

A box mean is not a point. Drawing it as one marker over a fixed two-degree view
misreports where the numbers came from, and silently crops any box larger than
that view. These tests protect two things: the rectangle is drawn, and the inset
widens until the whole rectangle fits.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from qtpy import QtCore
from qtpy.QtGui import QColor, QPixmap

from sharpmod.viz import hodo_locator

# Every helper here builds a QPixmap, which needs a live QApplication.
pytestmark = pytest.mark.usefixtures("qt_app")

LAT, LON = 41.0, -77.0


class FakeCollection:
    """Stand-in for the parts of ProfCollection the locator reads."""

    def __init__(self, **meta):
        self._meta = {"lat": LAT, "lon": LON}
        self._meta.update(meta)

    def getCurrentDate(self):  # noqa: N802 - upstream API
        return None

    def getMeta(self, key, index=False):  # noqa: N802
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value


def _widget(collection, size=(760, 520)):
    pixmap = QPixmap(*size)
    pixmap.fill(QColor("black"))
    widget = SimpleNamespace(
        plotBitMap=pixmap,
        prof=SimpleNamespace(latitude=LAT, longitude=LON, date=None),
        prof_collections=[collection],
        pc_idx=0,
        width=lambda: size[0],
        height=lambda: size[1],
    )
    return widget, pixmap


def _box(half_lat, half_lon):
    """A region-ordered ``(lat0, lon0, lat1, lon1)`` around the point."""
    return [LAT - half_lat, LON - half_lon, LAT + half_lat, LON + half_lon]


# -- reading the metadata -------------------------------------------------- #


def test_a_box_mean_sounding_reports_its_area():
    widget, _pixmap = _widget(
        FakeCollection(box_mean_bounds=[36.0, -96.5, 37.4, -94.8]))
    # Returned west, south, east, north -- the locator's own order.
    assert hodo_locator.box_from_widget(widget) == (-96.5, 36.0, -94.8, 37.4)


def test_an_ordinary_sounding_reports_no_area():
    widget, _pixmap = _widget(FakeCollection())
    assert hodo_locator.box_from_widget(widget) is None


@pytest.mark.parametrize(
    "value",
    [
        "36, -96, 37, -95",              # a string is not four numbers
        [36.0, -96.5],                   # too few
        [36.0, -96.5, 37.4, "east"],     # not numeric
        [float("nan"), -96.5, 37.4, -94.8],
        [37.4, -96.5, 36.0, -94.8],      # north below south
        [200.0, -96.5, 250.0, -94.8],    # off the planet
    ],
)
def test_unusable_bounds_are_ignored_rather_than_drawn(value):
    widget, _pixmap = _widget(FakeCollection(box_mean_bounds=value))
    assert hodo_locator.box_from_widget(widget) is None


def test_an_antimeridian_box_is_unwrapped_to_its_real_span():
    """A wrapped east edge would otherwise read as the 340-degree complement."""
    widget, _pixmap = _widget(
        FakeCollection(box_mean_bounds=[50.0, 170.0, 56.0, -170.0]))
    west, south, east, north = hodo_locator.box_from_widget(widget)
    assert (south, north) == (50.0, 56.0)
    assert east > west
    assert east - west == pytest.approx(20.0)


# -- zooming out to fit ---------------------------------------------------- #


def _contains(bounds, box) -> bool:
    west, south, east, north = bounds
    b_west, b_south, b_east, b_north = box
    return (west <= b_west and east >= b_east
            and south <= b_south and north >= b_north)


def _span(bounds):
    return bounds[3] - bounds[1], bounds[2] - bounds[0]


@pytest.mark.parametrize(
    "half_lat, half_lon",
    [(0.3, 0.4), (0.7, 0.85), (2.0, 2.5), (7.0, 9.0), (1.0, 6.0), (6.0, 1.0)],
)
def test_the_inset_widens_until_the_whole_area_fits(half_lat, half_lon):
    box = (LON - half_lon, LAT - half_lat, LON + half_lon, LAT + half_lat)
    bounds = hodo_locator.zoom_bounds(LAT, LON, box)
    assert _contains(bounds, box)


def test_widening_preserves_the_inset_aspect():
    """The inset must keep its shape at every zoom, or it stops reading as a map."""
    plain_h, plain_w = _span(hodo_locator.zoom_bounds(LAT, LON))
    for half in (2.0, 5.0, 9.0):
        box = (LON - half, LAT - half, LON + half, LAT + half)
        height, width = _span(hodo_locator.zoom_bounds(LAT, LON, box))
        assert width / height == pytest.approx(plain_w / plain_h)


def test_a_small_area_does_not_zoom_in_past_the_normal_view():
    """The default extent is a floor: a tiny box must not crop the context."""
    plain = hodo_locator.zoom_bounds(LAT, LON)
    tiny = (LON - 0.05, LAT - 0.04, LON + 0.05, LAT + 0.04)
    assert hodo_locator.zoom_bounds(LAT, LON, tiny) == pytest.approx(plain)


def test_a_large_area_really_does_zoom_out():
    plain_h, _plain_w = _span(hodo_locator.zoom_bounds(LAT, LON))
    box = (LON - 3.0, LAT - 2.5, LON + 3.0, LAT + 2.5)
    height, _width = _span(hodo_locator.zoom_bounds(LAT, LON, box))
    assert height > plain_h


def test_an_off_centre_point_still_contains_the_area():
    """The sounding sits at the box centre today; do not rely on it."""
    box = (LON - 1.0, LAT - 1.0, LON + 3.0, LAT + 2.0)
    assert _contains(hodo_locator.zoom_bounds(LAT, LON, box), box)


def test_the_zoom_out_has_a_ceiling():
    box = (LON - 100.0, LAT - 60.0, LON + 100.0, LAT + 60.0)
    bounds = hodo_locator.zoom_bounds(LAT, LON, box)
    half_lat = (bounds[3] - bounds[1]) / 2.0
    assert half_lat <= hodo_locator.LOCATOR_MAX_HALF_LAT_DEGREES


# -- what actually gets painted ------------------------------------------- #


def test_the_area_outline_changes_the_rendered_inset(qt_app):
    """Proof the rectangle is drawn, not merely computed."""
    bare_widget, bare_pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(bare_widget) is True
    bare = bare_pixmap.toImage()

    boxed_widget, boxed_pixmap = _widget(
        FakeCollection(box_mean_bounds=_box(0.5, 0.6)))
    assert hodo_locator.draw_hodo_locator(boxed_widget) is True

    assert boxed_pixmap.toImage() != bare, "no outline was painted"


def test_an_ordinary_sounding_renders_exactly_as_before(qt_app):
    """The area outline must not leak into point soundings."""
    first_widget, first_pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(first_widget) is True

    second_widget, second_pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(second_widget) is True

    assert first_pixmap.toImage() == second_pixmap.toImage()


def test_the_painted_outline_lands_inside_the_inset(qt_app):
    """A rectangle drawn outside the frame would be worse than none at all."""
    half_lat, half_lon = 2.0, 3.0
    widget, _pixmap = _widget(
        FakeCollection(box_mean_bounds=_box(half_lat, half_lon)))
    assert hodo_locator.draw_hodo_locator(widget) is True

    box = hodo_locator.box_from_widget(widget)
    bounds = hodo_locator.zoom_bounds(LAT, LON, box)
    rect = hodo_locator._inset_rect(widget, QtCore)
    interior = rect.adjusted(5.0, 5.0, -5.0, -5.0)

    west, south, east, north = box
    for lat, lon in ((north, west), (north, east), (south, west), (south, east)):
        x, y = hodo_locator._map_point(interior, bounds, lat, lon)
        assert interior.left() <= x <= interior.right()
        assert interior.top() <= y <= interior.bottom()


def test_unusable_bounds_do_not_break_the_locator(qt_app):
    """Bad metadata must degrade to an ordinary locator, never to an exception."""
    widget, pixmap = _widget(FakeCollection(box_mean_bounds="nonsense"))
    assert hodo_locator.draw_hodo_locator(widget) is True

    plain_widget, plain_pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(plain_widget) is True
    assert pixmap.toImage() == plain_pixmap.toImage()


# -- the marker gives way to the outline ----------------------------------- #


def _marker_calls(widget):
    """Count the crosshair primitives ``draw_hodo_locator`` asks for."""
    from qtpy.QtGui import QPainter

    seen = {"ellipse": 0, "line": 0}
    real_ellipse = QPainter.drawEllipse
    real_line = QPainter.drawLine

    def count_ellipse(self, *args, **kwargs):
        seen["ellipse"] += 1
        return real_ellipse(self, *args, **kwargs)

    def count_line(self, *args, **kwargs):
        seen["line"] += 1
        return real_line(self, *args, **kwargs)

    QPainter.drawEllipse = count_ellipse
    QPainter.drawLine = count_line
    try:
        assert hodo_locator.draw_hodo_locator(widget) is True
    finally:
        QPainter.drawEllipse = real_ellipse
        QPainter.drawLine = real_line
    return seen


def test_a_point_sounding_still_gets_its_crosshair(qt_app):
    widget, _pixmap = _widget(FakeCollection())
    assert _marker_calls(widget)["ellipse"] >= 1


def test_an_averaged_area_gets_the_outline_instead_of_a_crosshair(qt_app):
    """A mean has no single point, so a crosshair would name one falsely."""
    widget, _pixmap = _widget(
        FakeCollection(box_mean_bounds=_box(0.5, 0.6)))
    assert _marker_calls(widget)["ellipse"] == 0


def test_the_outline_is_still_drawn_when_the_marker_is_dropped(qt_app):
    """Losing the crosshair must not mean losing the rectangle too."""
    bare_widget, bare_pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(bare_widget) is True

    boxed_widget, boxed_pixmap = _widget(
        FakeCollection(box_mean_bounds=_box(0.5, 0.6)))
    assert hodo_locator.draw_hodo_locator(boxed_widget) is True

    assert boxed_pixmap.toImage() != bare_pixmap.toImage()
