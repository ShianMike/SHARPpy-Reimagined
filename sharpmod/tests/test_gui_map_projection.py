"""Flat and curved map views, and the lake shorelines the basemap was missing.

Two things are protected here. The projection has to be invertible and has to
letterbox the whole requested extent into the widget, because every marker,
overlay, box, and click on every map tab is converted through it. And the lakes
layer has to survive the whole path from the bundled resource to the draw call:
``ne_50m_coastline`` contains no inland shoreline at all, so without a separate
layer the Great Lakes are simply absent.
"""

from __future__ import annotations

import math

import pytest
from qtpy.QtGui import QPainter, QPixmap

from sharpmod import gui_maps
from sharpmod.gui_maps import (
    BASEMAP_LAYER_NAMES,
    MAP_PROJECTIONS,
    PointMapWidget,
    _conic_constants,
    _load_basemap,
    _shared_basemap_layers,
)

pytestmark = pytest.mark.usefixtures("qt_app")

#: A CONUS-ish view, which is what a cone is for.
CONUS = (-125.0, -66.0, 24.0, 50.0)


@pytest.fixture
def view():
    widget = PointMapWidget()
    widget.resize(800, 500)
    widget.set_extent(*CONUS)
    return widget


def _extent_pixels(widget, projection):
    """Project the whole boundary of the view, not just its corners.

    The boundary comes from the widget's own stored extent, not from
    :data:`CONUS`: ``set_extent`` pads what it is given by 20% a side, so the
    requested box always sits comfortably inside the fitted one and would never
    reach an edge.
    """
    lon0, lon1 = widget._lon0, widget._lon1
    lat0, lat1 = widget._lat0, widget._lat1
    xs, ys = [], []
    for index in range(21):
        frac = index / 20
        for lon, lat in (
            (lon0 + (lon1 - lon0) * frac, lat0),
            (lon0 + (lon1 - lon0) * frac, lat1),
            (lon0, lat0 + (lat1 - lat0) * frac),
            (lon1, lat0 + (lat1 - lat0) * frac),
        ):
            x, y = projection.forward(lon, lat)
            xs.append(x)
            ys.append(y)
    return xs, ys


# -- the projections ------------------------------------------------------- #


def test_flat_is_the_default(view):
    """It is what every previous version drew, and it never degenerates."""
    assert view.projection() == "flat"
    assert view._proj().affine is True


def test_curved_resolves_to_a_conic_over_a_regional_view(view):
    view.set_projection("curved")
    assert view.projection() == "curved"
    assert view._proj().affine is False


@pytest.mark.parametrize("kind", MAP_PROJECTIONS)
def test_every_projection_round_trips(view, kind):
    """A click has to land on the point that was drawn there."""
    view.set_projection(kind)
    projection = view._proj()
    for lon in (-120.0, -100.0, -80.0, -70.0):
        for lat in (26.0, 33.0, 41.0, 48.0):
            x, y = projection.forward(lon, lat)
            back_lon, back_lat = projection.inverse(x, y)
            assert back_lon == pytest.approx(lon, abs=1e-6)
            assert back_lat == pytest.approx(lat, abs=1e-6)


@pytest.mark.parametrize("kind", MAP_PROJECTIONS)
def test_the_whole_extent_fits_inside_the_widget(view, kind):
    """Letterboxed, never cropped: a clipped edge hides real geography."""
    view.set_projection(kind)
    xs, ys = _extent_pixels(view, view._proj())
    assert min(xs) >= -0.5 and max(xs) <= 800.5
    assert min(ys) >= -0.5 and max(ys) <= 500.5


@pytest.mark.parametrize("kind", MAP_PROJECTIONS)
def test_the_extent_actually_uses_the_widget(view, kind):
    """A correct fit touches at least one pair of opposite edges."""
    view.set_projection(kind)
    xs, ys = _extent_pixels(view, view._proj())
    fills_width = min(xs) < 1.0 and max(xs) > 799.0
    fills_height = min(ys) < 1.0 and max(ys) > 499.0
    assert fills_width or fills_height


def test_the_flat_view_draws_straight_parallels(view):
    projection = view._proj()
    west = projection.forward(-125.0, 50.0)[1]
    middle = projection.forward(-95.5, 50.0)[1]
    east = projection.forward(-66.0, 50.0)[1]
    assert middle == pytest.approx((west + east) / 2.0, abs=0.01)


def test_the_curved_view_bows_its_parallels(view):
    """The whole point of the option: it has to visibly curve."""
    view.set_projection("curved")
    projection = view._proj()
    west = projection.forward(-125.0, 50.0)[1]
    middle = projection.forward(-95.5, 50.0)[1]
    east = projection.forward(-66.0, 50.0)[1]
    assert abs(((west + east) / 2.0) - middle) > 5.0


def test_the_curved_view_converges_its_meridians(view):
    """Two meridians must be closer together at the pole side."""
    view.set_projection("curved")
    projection = view._proj()
    south = abs(projection.forward(-120.0, 24.0)[0]
                - projection.forward(-70.0, 24.0)[0])
    north = abs(projection.forward(-120.0, 50.0)[0]
                - projection.forward(-70.0, 50.0)[0])
    assert north < south


@pytest.mark.parametrize(
    "lat0, lat1, why",
    [
        (-85.0, 85.0, "a whole-world view"),
        (-30.0, 30.0, "a view centred on the equator"),
        (-10.0, 10.0, "a narrow equatorial view"),
        (0.0, 88.0, "a pole-to-equator hemisphere"),
    ],
)
def test_a_cone_refuses_views_it_cannot_represent(lat0, lat1, why):
    assert _conic_constants(lat0, lat1, -95.5) is None, why


def test_an_impossible_curved_view_falls_back_to_flat():
    """Better a correct flat map than a distorted curved one."""
    widget = PointMapWidget()
    widget.resize(800, 500)
    widget.set_extent(-180.0, 180.0, -85.0, 85.0)
    widget.set_projection("curved")
    assert widget.projection() == "curved"
    assert widget._proj().affine is True


def test_an_unknown_projection_name_falls_back_to_flat(view):
    view.set_projection("globe-o-matic")
    assert view.projection() == "flat"


def test_switching_projection_drops_the_baked_basemap(view):
    """The cached raster has the old transform painted into it."""
    view._basemap_pixmap()
    assert view._basemap_cache is not None
    view.set_projection("curved")
    assert view._basemap_cache is None


def test_setting_the_same_projection_twice_is_a_no_op(view):
    view._basemap_pixmap()
    cached = view._basemap_cache
    view.set_projection("flat")
    assert view._basemap_cache is cached


def test_imagery_is_drawn_on_a_non_affine_view(view):
    """Imagery is warped into the conic rather than withheld from it.

    It used to be withheld, on the grounds that a conic has no rectangle to blit
    a plate-carree image into and a misplaced radar echo is worse than an absent
    one. Both halves of that are still true; what changed is that the transform
    is now applied piecewise instead of ignored, so the image lands in the right
    place and there is nothing left to withhold.
    """
    view.set_projection("curved")
    projection = view._proj()
    assert not projection.affine, "expected a conic for this extent"

    drawn = []
    original = view._draw_raster_warped
    view._draw_raster_warped = lambda qp, p: drawn.append(p)
    try:
        pixmap = QPixmap(view.width(), view.height())
        painter = QPainter(pixmap)
        view._draw_raster_overlays(painter, projection)
        painter.end()
    finally:
        view._draw_raster_warped = original
    assert drawn, "a non-affine view must route imagery through the warp"


def test_the_warp_places_geometry_where_the_projection_says(view):
    """Each warped cell must agree with the exact transform inside it.

    The warp treats a cell as affine, which is an approximation. This measures it
    against the real projection at the point the error is largest -- the cell
    centre, furthest from the four corners the transform is built from -- and
    holds it to well under a pixel, because a whole-pixel error would be visible
    as imagery sliding against the coastline it is drawn over.
    """
    from qtpy.QtCore import QPointF, QRectF
    from qtpy.QtGui import QPolygonF, QTransform

    view.set_projection("curved")
    projection = view._proj()
    assert not projection.affine

    lon0, lon1, lat0, lat1 = view.view_bounds()
    cell = gui_maps._WARP_TARGET_CELL_DEG
    worst = 0.0
    checked = 0
    lon = lon0
    while lon + cell <= lon1:
        lat = lat0
        while lat + cell <= lat1:
            corners = ((lon, lat + cell), (lon + cell, lat + cell),
                       (lon + cell, lat), (lon, lat))
            # A unit source square stands in for the cell's source rectangle;
            # the transform is scale-invariant, so the interior error is not.
            source = QRectF(0.0, 0.0, 1.0, 1.0)
            transform = QTransform()
            built = QTransform.quadToQuad(
                QPolygonF([QPointF(0.0, 0.0), QPointF(1.0, 0.0),
                           QPointF(1.0, 1.0), QPointF(0.0, 1.0)]),
                QPolygonF([QPointF(*projection.forward(*corner))
                           for corner in corners]),
                transform)
            assert built
            mapped = transform.map(QPointF(source.center()))
            exact_x, exact_y = projection.forward(lon + cell / 2.0,
                                                  lat + cell / 2.0)
            worst = max(worst, math.hypot(mapped.x() - exact_x,
                                          mapped.y() - exact_y))
            checked += 1
            lat += cell
        lon += cell

    assert checked > 4, "expected several cells across this extent"
    assert worst < 1.0, f"warp is off by {worst:.3f} px at a cell centre"


def _opaque_png(width: int = 512, height: int = 256) -> bytes:
    """A fully opaque red PNG, built through Qt so the test needs no Pillow."""
    from qtpy.QtCore import QBuffer, QByteArray
    from qtpy.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format_RGBA8888)
    image.fill(QColor(255, 0, 0, 255))
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(store)


@pytest.mark.parametrize("kind", MAP_PROJECTIONS)
@pytest.mark.parametrize("extent", [
    (-125.0, -66.0, 24.0, 50.0),   # CONUS, close to the window's aspect
    (-100.0, -90.0, 28.0, 48.0),   # taller than the window: wide letterbox
    (-120.0, -70.0, 34.0, 40.0),   # wider than the window: tall letterbox
])
def test_imagery_fills_the_window_it_covers(kind, extent):
    """Every visible pixel the image covers must be painted.

    Both transforms letterbox the requested extent into the window, and the conic
    additionally fits a bowed outline into a rectangle. The margin that leaves is
    real map — the basemap draws coastlines into it — so imagery that clamps
    itself to the requested extent stops short of the window and reads as being
    cut off along a curve, with the coastline still drawn beyond it.

    Regression: clamping to the extent left 11% of the covered window bare at
    CONUS on the flat view, 26% on the curved one, and 73% at an extent whose
    aspect did not match the window.
    """
    from sharpmod import hrrr_field

    bounds = hrrr_field.COVERAGE_BOUNDS
    widget = PointMapWidget()
    widget.resize(560, 360)
    widget.set_extent(*extent, pad=0.0)
    widget.set_overlay(hrrr_field.OVERLAY_KEY, hrrr_field.OverlayRaster(
        key=hrrr_field.OVERLAY_KEY, title="solid", image_bytes=_opaque_png(),
        bounds=bounds, short_name="mlcape", opacity=1.0), visible=True)
    widget.set_projection(kind)

    projection = widget._proj()
    target = QPixmap(widget.width(), widget.height())
    widget.render(target)
    image = target.toImage()

    covered = 0
    bare = 0
    # Skipping the top rows: the graticule's degree labels are drawn over the
    # imagery there, so those pixels are legitimately not the image's colour.
    for y in range(18, widget.height() - 2, 3):
        for x in range(2, widget.width() - 2, 3):
            lon, lat = projection.inverse(x, y)
            if not (bounds[0] + 0.3 < lon < bounds[1] - 0.3
                    and bounds[2] + 0.3 < lat < bounds[3] - 0.3):
                continue
            covered += 1
            if image.pixelColor(x, y).red() < 120:
                bare += 1

    assert covered > 500, "expected the image to cover much of this window"
    share = 100.0 * bare / covered
    assert share < 2.0, (
        f"{share:.1f}% of the covered window was left unpainted on the {kind} "
        f"view at {extent}")


@pytest.mark.parametrize("kind", MAP_PROJECTIONS)
def test_the_conic_survives_an_antimeridian_view(kind):
    """Longitudes are unwrapped, so a Pacific view must not fling geometry."""
    widget = PointMapWidget()
    widget.resize(800, 500)
    widget.set_extent(160.0, 200.0, 40.0, 60.0)
    widget.set_projection(kind)
    projection = widget._proj()
    for lon in (165.0, 180.0, 195.0):
        x, y = projection.forward(lon, 50.0)
        assert math.isfinite(x) and math.isfinite(y)
        assert -2000.0 < x < 2800.0


# -- the lakes ------------------------------------------------------------- #


def test_the_bundled_basemap_carries_lake_shorelines():
    """``ne_50m_coastline`` has no inland shoreline; lakes are their own layer."""
    layers = _load_basemap()
    assert "lakes" in layers
    assert len(layers["lakes"]) > 100


def test_lakes_are_declared_alongside_the_other_layers():
    """Loading a layer the preparer drops would be a silent no-op."""
    assert "lakes" in BASEMAP_LAYER_NAMES
    prepared = _shared_basemap_layers()
    for name in BASEMAP_LAYER_NAMES:
        assert name in prepared, name
    assert prepared["lakes"]


@pytest.mark.parametrize(
    "name, west, south, east, north",
    [
        ("Michigan", -88.1, 41.5, -84.6, 46.2),
        ("Superior", -92.2, 46.3, -84.2, 49.1),
        ("Huron", -84.9, 42.9, -79.6, 46.7),
        ("Erie", -83.6, 41.3, -78.8, 43.2),
        ("Ontario", -79.9, 43.1, -75.7, 44.6),
        ("Great Salt", -113.1, 40.6, -111.9, 41.8),
    ],
)
def test_each_named_lake_has_an_outline(name, west, south, east, north):
    prepared = _shared_basemap_layers()
    found = [
        bounds for bounds, _points in prepared["lakes"]
        if bounds[0] >= west and bounds[1] <= east
        and bounds[2] >= south and bounds[3] <= north
        and (bounds[1] - bounds[0]) > 0.5 and (bounds[3] - bounds[2]) > 0.5
    ]
    assert found, f"{name} has no shoreline in the basemap"


def test_the_locator_inset_gets_the_lakes_too():
    """The sounding's inset spans a couple of degrees; a lake fills much of it."""
    from sharpmod.viz.hodo_locator import (
        _GLOBAL_LAYER_NAMES, global_lines_for_bounds,
    )

    assert "lakes" in _GLOBAL_LAYER_NAMES
    layers = global_lines_for_bounds((-89.0, 41.0, -84.0, 46.5))
    assert layers["lakes"], "no lake shoreline over Lake Michigan"


def test_an_older_basemap_without_lakes_still_loads(monkeypatch):
    """A resource predating the lakes layer means no outlines, not no map."""
    from sharpmod import gui_maps

    prepared = gui_maps._prepare_basemap_layers(
        {"coastline": [[[0.0, 0.0], [1.0, 1.0]]], "countries": [], "states": []})
    assert prepared["lakes"] == ()
    assert prepared["coastline"]
