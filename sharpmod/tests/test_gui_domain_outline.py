"""A model domain has to be drawn where the model actually ends.

The dashed outline on the forecast map is what tells a forecaster which ground a
sounding can be pulled from, so it is only useful if it follows the boundary
under whichever projection is showing.

A lon/lat box is a screen rectangle only on the flat view. Under the conic a
parallel projects to an arc, and the outline used to be drawn from the four
corners alone: the chord across those arcs put the southern edge of the HRRR
domain 118 px from the truth on a CONUS view, worst at the central meridian.
"""

from __future__ import annotations

import pytest

from sharpmod import gui_maps

#: The HRRR/RAP CONUS box, as ``model_extract`` publishes it.
HRRR_BOX = (-130.0, -60.0, 20.0, 55.0)
#: ``(lat0, lon0, lat1, lon1)``, the order the box selection uses.
WIDE_BOX = (24.0, -120.0, 48.0, -70.0)

VIEWS = {
    "conus": (-125.0, -66.0, 24.0, 50.0),
    "regional": (-104.0, -92.0, 30.0, 40.0),
    "zoomed": (-99.0, -95.0, 34.0, 37.0),
}


@pytest.fixture
def forecast_map(qt_app):
    widget = gui_maps.PointMapWidget()
    widget.resize(1024, 640)
    try:
        yield widget
    finally:
        widget.close()


def _look_at(widget, view, projection):
    widget.set_projection(projection)
    widget._lon0, widget._lon1, widget._lat0, widget._lat1 = view
    widget._invalidate()
    return widget._proj()


def _worst_gap(widget, poly, lon0, lon1, lat0, lat1, p, samples=240):
    """Furthest the true boundary strays from the drawn polyline, in pixels."""
    points = [poly.at(index) for index in range(poly.count())]
    points.append(points[0])        # drawPolygon closes the ring itself

    def nearest(target):
        best = None
        for index in range(len(points) - 1):
            a, b = points[index], points[index + 1]
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length2 = dx * dx + dy * dy
            if length2 <= 0.0:
                distance = ((target.x() - a.x()) ** 2
                            + (target.y() - a.y()) ** 2) ** 0.5
            else:
                along = max(0.0, min(1.0, (
                    (target.x() - a.x()) * dx + (target.y() - a.y()) * dy
                ) / length2))
                distance = ((target.x() - (a.x() + along * dx)) ** 2
                            + (target.y() - (a.y() + along * dy)) ** 2) ** 0.5
            best = distance if best is None else min(best, distance)
        return best

    worst = 0.0
    for index in range(samples + 1):
        fraction = index / samples
        for lon, lat in (
            (lon0 + (lon1 - lon0) * fraction, lat0),
            (lon0 + (lon1 - lon0) * fraction, lat1),
            (lon0, lat0 + (lat1 - lat0) * fraction),
            (lon1, lat0 + (lat1 - lat0) * fraction),
        ):
            worst = max(worst, nearest(widget._to_px(lon, lat, p)))
    return worst


# --------------------------------------------------------------------------- #
# The bug
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("view", sorted(VIEWS), ids=sorted(VIEWS))
def test_the_domain_outline_follows_the_curved_projection(forecast_map, view):
    p = _look_at(forecast_map, VIEWS[view], "curved")
    assert not p.affine, "sanity: this view has to resolve to a cone"

    poly = forecast_map._lonlat_box_polygon(p, *HRRR_BOX)

    assert _worst_gap(forecast_map, poly, *HRRR_BOX, p) < 1.0


def test_four_corners_alone_would_miss_the_boundary_badly(forecast_map):
    """Guards the fix by measuring what it replaced.

    Without this, someone could quietly restore the corner-only outline and every
    other test here would still pass.
    """
    from qtpy.QtGui import QPolygonF

    p = _look_at(forecast_map, VIEWS["conus"], "curved")
    lon0, lon1, lat0, lat1 = HRRR_BOX
    corners = QPolygonF([
        forecast_map._to_px(lon0, lat1, p),
        forecast_map._to_px(lon1, lat1, p),
        forecast_map._to_px(lon1, lat0, p),
        forecast_map._to_px(lon0, lat0, p),
    ])

    assert _worst_gap(forecast_map, corners, *HRRR_BOX, p) > 50.0


# --------------------------------------------------------------------------- #
# Staying out of the way on the flat view
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("view", sorted(VIEWS), ids=sorted(VIEWS))
def test_the_flat_view_is_still_a_plain_rectangle(forecast_map, view):
    """No extra samples where a box genuinely is a rectangle."""
    p = _look_at(forecast_map, VIEWS[view], "flat")

    poly = forecast_map._lonlat_box_polygon(p, *HRRR_BOX)

    assert poly.count() == 4
    assert _worst_gap(forecast_map, poly, *HRRR_BOX, p) == pytest.approx(0.0,
                                                                        abs=1e-6)


def test_the_flat_outline_names_the_four_corners(forecast_map):
    lon0, lon1, lat0, lat1 = HRRR_BOX
    p = _look_at(forecast_map, VIEWS["conus"], "flat")

    poly = forecast_map._lonlat_box_polygon(p, *HRRR_BOX)
    drawn = {(round(poly.at(i).x(), 3), round(poly.at(i).y(), 3))
             for i in range(poly.count())}

    for lon, lat in ((lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)):
        pt = forecast_map._to_px(lon, lat, p)
        assert (round(pt.x(), 3), round(pt.y(), 3)) in drawn


# --------------------------------------------------------------------------- #
# The box selection shares the walk
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("view", sorted(VIEWS), ids=sorted(VIEWS))
def test_a_wide_box_selection_also_follows_the_projection(forecast_map, view):
    """The box had a fixed step count, which thinned out across a wide span."""
    p = _look_at(forecast_map, VIEWS[view], "curved")
    lat0, lon0, lat1, lon1 = WIDE_BOX

    poly = forecast_map._box_polygon(p, WIDE_BOX)

    assert _worst_gap(forecast_map, poly, lon0, lon1, lat0, lat1, p) < 1.0


def test_the_box_and_the_domain_use_one_walk(forecast_map):
    """Two implementations of this would drift apart; there is only one."""
    p = _look_at(forecast_map, VIEWS["conus"], "curved")
    lat0, lon0, lat1, lon1 = WIDE_BOX

    viax = forecast_map._box_polygon(p, WIDE_BOX)
    direct = forecast_map._lonlat_box_polygon(p, lon0, lon1, lat0, lat1)

    assert viax.count() == direct.count()
    for index in range(direct.count()):
        assert viax.at(index).x() == pytest.approx(direct.at(index).x())
        assert viax.at(index).y() == pytest.approx(direct.at(index).y())


# --------------------------------------------------------------------------- #
# Degenerate and wrapped inputs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("box,label", [
    ((-97.0, -97.0, 30.0, 40.0), "zero width"),
    ((-110.0, -90.0, 35.0, 35.0), "zero height"),
    ((-97.0, -97.0, 35.0, 35.0), "a single point"),
], ids=["zero-width", "zero-height", "point"])
def test_a_degenerate_box_still_produces_a_ring(forecast_map, box, label):
    """A collapsed domain must not divide by zero or vanish."""
    p = _look_at(forecast_map, VIEWS["conus"], "curved")

    poly = forecast_map._lonlat_box_polygon(p, *box)

    assert poly.count() >= 3, label


def test_no_vertex_is_emitted_twice(forecast_map):
    """Each edge stops short of the next edge's opening point."""
    p = _look_at(forecast_map, VIEWS["conus"], "curved")

    poly = forecast_map._lonlat_box_polygon(p, *HRRR_BOX)
    seen = [(round(poly.at(i).x(), 6), round(poly.at(i).y(), 6))
            for i in range(poly.count())]

    assert len(seen) == len(set(seen))


def test_a_global_domain_is_not_outlined(forecast_map, qt_app):
    """A whole-world box is not a boundary worth drawing."""
    from qtpy.QtGui import QImage, QPainter

    forecast_map.set_domain((-180.0, 180.0, -90.0, 90.0), "global")
    p = _look_at(forecast_map, VIEWS["conus"], "curved")
    surface = QImage(forecast_map.size(), QImage.Format_ARGB32_Premultiplied)
    surface.fill(0)
    painter = QPainter(surface)
    try:
        forecast_map._draw_domain(painter, p)
    finally:
        painter.end()

    painted = any(surface.pixel(x, y) != 0
                  for y in range(0, surface.height(), 4)
                  for x in range(0, surface.width(), 4))
    assert not painted


def test_the_domain_is_drawn_on_the_curved_view(forecast_map, qt_app):
    """The end-to-end path, since the walk alone proves only the geometry."""
    from qtpy.QtGui import QImage, QPainter

    forecast_map.set_domain(HRRR_BOX, "HRRR domain: CONUS")
    p = _look_at(forecast_map, VIEWS["conus"], "curved")
    surface = QImage(forecast_map.size(), QImage.Format_ARGB32_Premultiplied)
    surface.fill(0)
    painter = QPainter(surface)
    try:
        forecast_map._draw_domain(painter, p)
    finally:
        painter.end()

    painted = sum(1 for y in range(0, surface.height(), 2)
                  for x in range(0, surface.width(), 2)
                  if surface.pixel(x, y) != 0)
    assert painted > 500


# --------------------------------------------------------------------------- #
# The outline has to be the model's grid, not a box around it
#
# A Lambert grid is a rectangle in its own projection plane, so its geographic
# boundary curves. Outlined as a lon/lat box, the dashed line and the field
# inside it are never the same shape and they trade places between projections:
# on the flat view the data is a fan and the box is a rectangle, and on the conic
# view the data straightens while the box bows. That reads as though the two
# projections had been swapped, when in fact only the outline was wrong.
# --------------------------------------------------------------------------- #
def test_hrrr_publishes_its_grid_perimeter_rather_than_a_box():
    from sharpmod.lambert_grid import HRRR_GRID
    from sharpmod.tools import model_extract

    cfg = model_extract.get_config("hrrr")

    assert cfg.domain_outline, "HRRR must carry its real perimeter"
    assert cfg.domain_outline == HRRR_GRID.outline()
    assert cfg.domain_bounds == pytest.approx(HRRR_GRID.bounds()), \
        "the envelope has to be measured off the same grid it outlines"
    assert cfg.domain_bounds != HRRR_BOX, \
        "the rounded CONUS box is what this replaced"


def _gap_to_ring(widget, ring, truth, p):
    """Furthest the true boundary strays from a drawn closed ring, in pixels.

    Distance to the nearest *segment*, not the nearest vertex: a ring that traces
    the boundary perfectly still puts its vertices tens of pixels apart on a
    continental view, so a vertex-only measure would report that spacing as error.
    """
    points = [widget._to_px(lon, lat, p) for lon, lat in ring]
    if points[0] != points[-1]:
        points.append(points[0])

    def nearest(target):
        best = None
        for index in range(len(points) - 1):
            a, b = points[index], points[index + 1]
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length2 = dx * dx + dy * dy
            if length2 <= 0.0:
                distance = ((target.x() - a.x()) ** 2
                            + (target.y() - a.y()) ** 2) ** 0.5
            else:
                along = max(0.0, min(1.0, (
                    (target.x() - a.x()) * dx + (target.y() - a.y()) * dy
                ) / length2))
                distance = ((target.x() - (a.x() + along * dx)) ** 2
                            + (target.y() - (a.y() + along * dy)) ** 2) ** 0.5
            best = distance if best is None else min(best, distance)
        return best

    return max(nearest(widget._to_px(lon, lat, p)) for lon, lat in truth)


def test_the_published_outline_follows_the_grid_in_both_projections(
        forecast_map):
    """The point of the change: one shape, whichever projection is showing.

    Measured against a densely resampled boundary, so it fails if the outline
    becomes a box again *or* if it is sampled too coarsely to trace the arcs.
    """
    from sharpmod.lambert_grid import HRRR_GRID
    from sharpmod.tools import model_extract

    outline = model_extract.get_config("hrrr").domain_outline
    truth = HRRR_GRID.outline(samples_per_edge=200)

    for projection in ("flat", "curved"):
        p = _look_at(forecast_map, VIEWS["conus"], projection)

        gap = _gap_to_ring(forecast_map, outline, truth, p)

        assert gap < 2.0, (
            f"{projection}: the drawn outline strays {gap:.1f} px from the "
            "grid boundary")


def test_a_lonlat_box_would_miss_the_grid_boundary_by_a_long_way(forecast_map):
    """Guards the fix by measuring what it replaced, in both projections.

    Without this the outline could quietly revert to ``CONUS_DOMAIN`` and only
    the test above would notice.
    """
    from sharpmod.lambert_grid import HRRR_GRID

    truth = HRRR_GRID.outline(samples_per_edge=200)
    lon0, lon1, lat0, lat1 = HRRR_BOX
    box = ((lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1),
           (lon0, lat0))

    for projection in ("flat", "curved"):
        p = _look_at(forecast_map, VIEWS["conus"], projection)

        gap = _gap_to_ring(forecast_map, box, truth, p)

        assert gap > 60.0, (
            f"{projection}: the old box was only {gap:.1f} px out, so this "
            "test is no longer measuring the bug it was written for")


def test_the_grid_outline_is_drawn_on_both_views(forecast_map, qt_app):
    """End to end, since the geometry alone does not prove it reaches the frame."""
    from qtpy.QtGui import QImage, QPainter

    from sharpmod.tools import model_extract

    cfg = model_extract.get_config("hrrr")
    forecast_map.set_domain(cfg.domain_bounds, "HRRR domain: CONUS",
                            outline=cfg.domain_outline)

    for projection in ("flat", "curved"):
        p = _look_at(forecast_map, VIEWS["conus"], projection)
        surface = QImage(forecast_map.size(), QImage.Format_ARGB32_Premultiplied)
        surface.fill(0)
        painter = QPainter(surface)
        try:
            forecast_map._draw_domain(painter, p)
        finally:
            painter.end()

        painted = sum(
            1 for y in range(0, surface.height(), 3)
            for x in range(0, surface.width(), 3)
            if surface.pixel(x, y) != 0)
        assert painted > 0, f"{projection}: nothing was drawn"
