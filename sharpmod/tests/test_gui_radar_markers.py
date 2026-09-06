"""Radar antenna markers on the picker maps.

The single-site radar overlay draws every WSR-88D as a marker so the antenna can
be chosen by clicking the map instead of hunting through a list of 155. The
markers have to be pickable on both map families, and they share the map with the
sounding stations -- several of which sit on the same mast as a radar -- so which
of two overlapping targets a click resolves to is the thing worth pinning.
"""

from __future__ import annotations

import pytest
from qtpy.QtCore import QPointF
from qtpy.QtGui import QImage, QPainter

from sharpmod import gui_maps, radar_site

CONUS = (-125.0, -66.0, 24.0, 50.0)
BACKGROUND = 0xFF12161B

#: Norman, Oklahoma: the sounding site and KTLX share a location, which is the
#: case that decides whether the radar layer can steal a station click.
OUN = {"id": "OUN", "name": "Norman OK", "lat": 35.18, "lon": -97.44}


def _look_at(widget, view=CONUS):
    widget._lon0, widget._lon1, widget._lat0, widget._lat1 = view
    widget._invalidate()


def _station_map(qt_app, stations=()):
    widget = gui_maps.StationMapWidget(list(stations))
    widget.resize(900, 560)
    _look_at(widget)
    return widget


def _point_map(qt_app):
    widget = gui_maps.PointMapWidget()
    widget.resize(900, 560)
    _look_at(widget)
    return widget


def _marker_pixels(widget) -> int:
    image = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(BACKGROUND)
    painter = QPainter(image)
    try:
        widget._draw_radar_sites(painter, widget._proj())
    finally:
        painter.end()
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixel(x, y) != BACKGROUND)


def _site_point(widget, site_id):
    site = radar_site.site_by_id(site_id)
    assert site is not None, site_id
    return widget._to_px(site.lon, site.lat, widget._proj())


@pytest.fixture(params=("station", "point"))
def mapped(request, qt_app):
    """Both map families, because the overlay is offered on both tabs."""
    widget = (_station_map(qt_app) if request.param == "station"
              else _point_map(qt_app))
    try:
        yield widget
    finally:
        widget.close()


# --------------------------------------------------------------------------- #
# The layer
# --------------------------------------------------------------------------- #
def test_no_markers_are_drawn_until_they_are_pushed(mapped):
    assert not mapped.radar_sites_visible()
    assert _marker_pixels(mapped) == 0


def test_the_whole_catalogue_can_be_shown(mapped):
    mapped.set_radar_sites(radar_site.sites())

    assert mapped.radar_sites_visible()
    assert len(mapped._radar_sites) == len(radar_site.sites())
    assert _marker_pixels(mapped) > 500


def test_clearing_the_layer_removes_it(mapped):
    mapped.set_radar_sites(radar_site.sites())
    mapped.set_radar_sites(())

    assert not mapped.radar_sites_visible()
    assert _marker_pixels(mapped) == 0


def test_records_can_be_dicts_or_objects(mapped):
    mapped.set_radar_sites([{"id": "ktlx", "lat": 35.33, "lon": -97.28}])

    assert mapped._radar_sites == (("KTLX", -97.28, 35.33),)


def test_unusable_records_are_skipped_not_fatal(mapped):
    mapped.set_radar_sites([
        {"id": "KTLX", "lat": 35.33, "lon": -97.28},
        {"id": "BAD", "lat": "nonsense", "lon": 0.0},
        {"lat": 1.0, "lon": 2.0},
        {"id": "POLE", "lat": 130.0, "lon": 0.0},
        None,
    ])

    assert [entry[0] for entry in mapped._radar_sites] == ["KTLX"]


def test_the_selected_antenna_is_drawn_differently(mapped):
    mapped.set_radar_sites(radar_site.sites())
    plain = _marker_pixels(mapped)

    mapped.set_radar_site_selected("KTLX")

    assert _marker_pixels(mapped) != plain


def test_identifiers_are_labelled_once_the_view_closes_in(mapped):
    mapped.set_radar_sites(radar_site.sites())
    wide = _marker_pixels(mapped)
    _look_at(mapped, (-100.0, -94.0, 33.0, 38.0))
    close = _marker_pixels(mapped)

    # Far fewer antennas are in view, yet each carries a four-character label.
    assert abs(mapped._lon1 - mapped._lon0) <= mapped.RADAR_LABEL_LON_SPAN
    assert close > 0 and wide > 0


# --------------------------------------------------------------------------- #
# Picking
# --------------------------------------------------------------------------- #
def test_the_antenna_under_the_cursor_is_found(mapped):
    mapped.set_radar_sites(radar_site.sites())
    point = _site_point(mapped, "KTLX")

    found = mapped._radar_site_at(point.x(), point.y())

    assert found is not None and found[0] == "KTLX"


def test_empty_space_finds_nothing(mapped):
    mapped.set_radar_sites(radar_site.sites())

    assert mapped._radar_site_at(-500.0, -500.0) is None


def test_a_click_reports_the_identifier(mapped):
    mapped.set_radar_sites(radar_site.sites())
    seen: list[str] = []
    mapped.radarSiteSelected.connect(seen.append)

    assert mapped._pick_radar_site(_site_point(mapped, "KTLX")) is True

    assert seen == ["KTLX"]
    assert mapped._radar_site_id == "KTLX"


def test_a_click_on_empty_space_reports_nothing(mapped):
    mapped.set_radar_sites(radar_site.sites())
    seen: list[str] = []
    mapped.radarSiteSelected.connect(seen.append)

    assert mapped._pick_radar_site(QPointF(-500.0, -500.0)) is False

    assert seen == []


def test_a_click_finds_nothing_while_the_layer_is_off(mapped):
    seen: list[str] = []
    mapped.radarSiteSelected.connect(seen.append)

    assert mapped._pick_radar_site(QPointF(100.0, 100.0)) is False
    assert seen == []


# --------------------------------------------------------------------------- #
# Overlapping targets: a radar sharing a mast with a sounding site
# --------------------------------------------------------------------------- #
def test_the_nearer_target_wins_a_contested_click(qt_app):
    """A sounding site must stay selectable under an antenna marker.

    Norman and KTLX are about six kilometres apart, so at a continental extent
    they land within a few pixels of each other. Giving the radar an
    unconditional veto made stations like this unselectable for as long as the
    layer was showing.
    """
    widget = _station_map(qt_app, [OUN])
    try:
        widget.set_radar_sites(radar_site.sites())
        station_point = widget._to_px(OUN["lon"], OUN["lat"], widget._proj())
        radar_point = _site_point(widget, "KTLX")

        # Directly on the station: the station is nearer, so the radar yields.
        rival = widget._station_rival(station_point, OUN)
        assert widget._pick_radar_site(station_point, rival) is False

        # Directly on the antenna: the radar is nearer and takes it.
        assert widget._pick_radar_site(
            radar_point, widget._station_rival(radar_point, OUN)) is True
    finally:
        widget.close()


@pytest.mark.parametrize("gap_px", (0.0, 0.006, 0.05, 0.5, 1.9, 2.9))
def test_a_station_on_the_same_mast_keeps_the_click(qt_app, gap_px):
    """Co-located pairs must not be decided by rounding error.

    Forty-eight station/antenna pairs sit under two pixels apart at ordinary
    zooms, and the closest -- KILX with Lincoln 74560 -- is six thousandths of a
    pixel away. "Whichever is nearer" handed those clicks to whichever floating
    point comparison came out ahead, so clicking the single visible dot on the
    Station Map re-aimed the radar instead of picking the sounding site, and did
    it differently on each attempt. The station takes every near tie.
    """
    widget = _station_map(qt_app, [OUN])
    try:
        widget.set_radar_sites(radar_site.sites())
        point = _site_point(widget, "KTLX")

        taken = widget._pick_radar_site(point, rival_d2=gap_px * gap_px)

        assert taken is False
    finally:
        widget.close()


def test_an_antenna_clear_of_any_station_is_still_selectable(qt_app):
    """The margin must not make the layer unusable where nothing competes."""
    widget = _station_map(qt_app, [OUN])
    try:
        widget.set_radar_sites(radar_site.sites())
        point = _site_point(widget, "KTLX")
        clear = (gui_maps.RADAR_PICK_MARGIN_PX + 2.0) ** 2

        assert widget._pick_radar_site(point, rival_d2=clear) is True
    finally:
        widget.close()


def test_the_margin_is_measured_in_pixels_not_squared_pixels(qt_app):
    """A far-off click must not scale the margin with its own distance.

    Comparing squared distances directly would make the effective margin grow
    with how far from the target the click landed, so a rival a fixed number of
    pixels beyond the antenna would win near the marker and lose away from it.
    """
    widget = _station_map(qt_app, [OUN])
    try:
        widget.set_radar_sites(radar_site.sites())
        point = _site_point(widget, "KTLX")
        # The antenna is 6 px from the click; the rival sits 2 px further out,
        # which is inside the margin, so the station keeps it.
        near_miss = QPointF(point.x() + 6.0, point.y())
        rival = 8.0 ** 2

        assert widget._pick_radar_site(near_miss, rival_d2=rival) is False
    finally:
        widget.close()


def test_a_station_click_still_selects_the_station(qt_app):
    widget = _station_map(qt_app, [OUN])
    try:
        widget.set_radar_sites(radar_site.sites())
        stations: list[str] = []
        radars: list[str] = []
        widget.stationSelected.connect(stations.append)
        widget.radarSiteSelected.connect(radars.append)
        point = widget._to_px(OUN["lon"], OUN["lat"], widget._proj())

        widget._dragged = False
        widget.mouseReleaseEvent(_left_release(point))

        assert stations == ["OUN"]
        assert radars == []
    finally:
        widget.close()


class _FakeRelease:
    """Minimal stand-in for a left-button release at a position."""

    def __init__(self, pos):
        self._pos = pos

    def button(self):
        from qtpy.QtCore import Qt
        return Qt.LeftButton

    def position(self):
        return self._pos


def _left_release(pos):
    return _FakeRelease(pos)


def test_a_radar_click_does_not_move_the_sounding_point(qt_app):
    """On the forecast map a click resolves to one thing, not both."""
    widget = _point_map(qt_app)
    try:
        widget.set_radar_sites(radar_site.sites())
        points: list[tuple[float, float]] = []
        radars: list[str] = []
        widget.pointSelected.connect(
            lambda lat, lon: points.append((lat, lon)))
        widget.radarSiteSelected.connect(radars.append)

        widget._dragged = False
        widget.mouseReleaseEvent(_left_release(_site_point(widget, "KTLX")))

        assert radars == ["KTLX"]
        assert points == [], "the profile location must not have moved"
    finally:
        widget.close()


def test_a_click_away_from_any_antenna_still_picks_a_point(qt_app):
    widget = _point_map(qt_app)
    try:
        widget.set_radar_sites(radar_site.sites())
        points: list[tuple[float, float]] = []
        radars: list[str] = []
        widget.pointSelected.connect(
            lambda lat, lon: points.append((lat, lon)))
        widget.radarSiteSelected.connect(radars.append)

        widget._dragged = False
        widget.mouseReleaseEvent(_left_release(QPointF(3.0, 3.0)))

        assert radars == []
        assert len(points) == 1
    finally:
        widget.close()
