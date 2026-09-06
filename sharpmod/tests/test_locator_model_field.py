"""The gridded model field on the sounding window's locator inset.

A sounding pulled while a field is on the picker map should arrive with that same
field under its locator thumbnail. The inset is painted from inside a vendored
render pass that receives nothing but the hodograph widget, so the image has to
travel as profile-collection metadata, and it has to be the *same* forecast the
profile came from rather than merely a nearby one.
"""

from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod import map_overlays as mo
from sharpmod.viz import hodo_locator as hl

UTC = timezone.utc
VALID = datetime(2026, 9, 4, 21, tzinfo=UTC)
FIELD_BOUNDS = (-134.5, -60.5, 20.5, 53.0)
LAT, LON = 40.39, -80.16


def _png(width=8, height=4):
    """A minimal valid RGBA PNG of a known size."""

    def chunk(tag, payload):
        body = tag + payload
        return (len(payload).to_bytes(4, "big") + body
                + zlib.crc32(body).to_bytes(4, "big"))

    header = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
              + bytes([8, 6, 0, 0, 0]))
    raw = b"".join(b"\x00" + bytes([90, 140, 200, 255]) * width
                   for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _raster(*, key="hrrr_field", valid=VALID, bounds=FIELD_BOUNDS,
            width=8, height=4, opacity=0.75, short_name="scp"):
    return mo.OverlayRaster(
        key=key,
        title="HRRR Supercell Composite",
        image_bytes=_png(width, height),
        bounds=bounds,
        short_name=short_name,
        valid_time=valid,
        retrieved_at=datetime.now(UTC),
        update_interval_s=3600.0,
        opacity=opacity,
        attribution="NOAA/NCEP HRRR",
    )


def _layer():
    ring = [[-82.0, 39.0], [-78.0, 39.0], [-78.0, 42.0], [-82.0, 42.0],
            [-82.0, 39.0]]
    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [ring]})[0]
    shape = mo.OverlayShape(rings=rings, bounds=mo.bounds_of(rings),
                            stroke="#005500", fill="#66A366", label="SLGT",
                            rank=1)
    return mo.build_layer(
        "spc_outlook", "SPC convective outlook", [shape],
        valid_from=VALID - timedelta(hours=6),
        valid_to=VALID + timedelta(hours=6))


class _Collection:
    """Stand-in for a ProfCollection's metadata store."""

    def __init__(self):
        self._meta: dict = {}

    def getMeta(self, key):  # noqa: N802 - vendored spelling
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value


def _widget(collection, when=VALID, monkeypatch=None):
    widget = SimpleNamespace(prof_collections=[collection], pc_idx=0)
    if monkeypatch is not None:
        monkeypatch.setattr(hl, "valid_time_from_widget", lambda _w: when)
    return widget


# --------------------------------------------------------------------------- #
# The transport seam carries both kinds of overlay
# --------------------------------------------------------------------------- #
def test_an_image_travels_on_the_locator_seam():
    collection = _Collection()
    raster = _raster()

    mo.attach_locator_overlay(collection, raster, key=raster.key)

    assert mo.locator_rasters(collection) == (raster,)


def test_an_image_does_not_appear_in_the_vector_accessor():
    """Each accessor takes the type its paint path knows how to draw."""
    collection = _Collection()
    mo.attach_locator_overlay(collection, _raster(), key="hrrr_field")

    assert mo.locator_overlays(collection) == ()


def test_a_field_and_an_outlook_coexist():
    """Attaching one product must not discard the other."""
    collection = _Collection()
    raster = _raster()
    layer = _layer()

    mo.attach_locator_overlay(collection, raster, key=raster.key)
    mo.attach_locator_overlay(collection, layer, key=layer.key)

    assert mo.locator_rasters(collection) == (raster,)
    assert mo.locator_overlays(collection) == (layer,)


def test_refetching_a_field_replaces_it_rather_than_accumulating():
    collection = _Collection()
    first = _raster()
    second = _raster(valid=VALID + timedelta(hours=1))

    mo.attach_locator_overlay(collection, first, key="hrrr_field")
    mo.attach_locator_overlay(collection, second, key="hrrr_field")

    assert mo.locator_rasters(collection) == (second,)


def test_replacing_a_field_keeps_the_outlook():
    collection = _Collection()
    layer = _layer()
    mo.attach_locator_overlay(collection, layer, key=layer.key)
    mo.attach_locator_overlay(collection, _raster(), key="hrrr_field")

    mo.attach_locator_overlay(
        collection, _raster(valid=VALID + timedelta(hours=1)),
        key="hrrr_field")

    assert mo.locator_overlays(collection) == (layer,)
    assert len(mo.locator_rasters(collection)) == 1


def test_detaching_a_field_removes_only_it():
    collection = _Collection()
    layer = _layer()
    mo.attach_locator_overlay(collection, layer, key=layer.key)
    mo.attach_locator_overlay(collection, _raster(), key="hrrr_field")

    mo.attach_locator_overlay(collection, None, key="hrrr_field")

    assert mo.locator_rasters(collection) == ()
    assert mo.locator_overlays(collection) == (layer,)


def test_a_collection_with_no_metadata_yields_nothing():
    assert mo.locator_rasters(None) == ()
    assert mo.locator_rasters(_Collection()) == ()


# --------------------------------------------------------------------------- #
# Matching the field to the sounding's own hour
# --------------------------------------------------------------------------- #
def test_a_field_valid_at_the_sounding_hour_is_used(monkeypatch):
    collection = _Collection()
    raster = _raster()
    mo.attach_locator_overlay(collection, raster, key="hrrr_field")

    widget = _widget(collection, VALID, monkeypatch)

    assert hl.overlay_rasters_for_widget(widget) == (raster,)


@pytest.mark.parametrize("offset_hours", (3, 9, -6, 48))
def test_a_field_from_another_hour_is_withheld(monkeypatch, offset_hours):
    """Two different forecasts must not be presented as one."""
    collection = _Collection()
    mo.attach_locator_overlay(collection, _raster(), key="hrrr_field")

    widget = _widget(
        collection, VALID + timedelta(hours=offset_hours), monkeypatch)

    assert hl.overlay_rasters_for_widget(widget) == ()


def test_a_sounding_with_no_time_still_shows_the_field(monkeypatch):
    """Nothing to compare against is not a reason to withhold context."""
    collection = _Collection()
    raster = _raster()
    mo.attach_locator_overlay(collection, raster, key="hrrr_field")

    widget = _widget(collection, None, monkeypatch)

    assert hl.overlay_rasters_for_widget(widget) == (raster,)


def test_an_unreadable_widget_yields_nothing():
    assert hl.overlay_rasters_for_widget(SimpleNamespace()) == ()
    assert hl.overlay_rasters_for_widget(None) == ()


# --------------------------------------------------------------------------- #
# Cropping the field to the inset extent
# --------------------------------------------------------------------------- #
def test_the_crop_is_centred_on_the_sounding():
    """The inset must show the sounding's own neighbourhood, not an offset one."""
    raster = _raster(width=3072, height=1536)
    bounds = hl.zoom_bounds(LAT, LON)
    west, south, east, north = bounds

    crop = hl.raster_source_rect(raster, bounds, 3072, 1536)

    assert crop is not None
    x, y, w, h = crop
    lon0, lon1, lat0, lat1 = raster.bounds
    back_lon = lon0 + (x + w / 2.0) / 3072 * (lon1 - lon0)
    back_lat = lat1 - (y + h / 2.0) / 1536 * (lat1 - lat0)
    assert back_lon == pytest.approx((west + east) / 2.0, abs=0.02)
    assert back_lat == pytest.approx((south + north) / 2.0, abs=0.02)


def test_the_crop_stays_inside_the_image_for_an_interior_sounding():
    raster = _raster(width=3072, height=1536)

    x, y, w, h = hl.raster_source_rect(
        raster, hl.zoom_bounds(LAT, LON), 3072, 1536)

    assert x >= 0 and y >= 0
    assert x + w <= 3072 + 1
    assert y + h <= 1536 + 1


def test_the_crop_is_left_unclamped_at_the_domain_edge():
    """The raw crop may fall outside the image, and has to.

    A sounding near the model's own boundary asks for ground the field does not
    cover. Clamping here would throw away the offset needed to place what does
    survive, which is what :func:`raster_draw_rects` uses.
    """
    raster = _raster(width=3072, height=1536)

    x, _y, w, _h = hl.raster_source_rect(
        raster, hl.zoom_bounds(40.0, -133.8), 3072, 1536)

    assert x < 0.0
    assert x + w > 0.0


# --------------------------------------------------------------------------- #
# Placement at the edges of the model's domain
# --------------------------------------------------------------------------- #
EDGE_CASES = (
    ("interior", 40.39, -80.16),
    ("west", 40.0, -133.8),
    ("east", 40.0, -60.8),
    ("north", 52.8, -100.0),
    ("south", 20.8, -100.0),
    ("north-west corner", 52.8, -134.0),
    ("south-east corner", 20.8, -60.8),
)


def _placement_error(bounds, source, target, rect, width, height):
    """Worst corner disagreement, in degrees, between source and target."""
    lon0, lon1, lat0, lat1 = FIELD_BOUNDS
    west, south, east, north = bounds
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    worst = 0.0
    for px, py, ix, iy in ((sx, sy, tx, ty),
                           (sx + sw, sy + sh, tx + tw, ty + th)):
        source_lon = lon0 + px / width * (lon1 - lon0)
        source_lat = lat1 - py / height * (lat1 - lat0)
        target_lon = west + (ix - rect.left()) / rect.width() * (east - west)
        target_lat = north - (iy - rect.top()) / rect.height() * (north - south)
        worst = max(worst, abs(source_lon - target_lon),
                    abs(source_lat - target_lat))
    return worst


@pytest.mark.parametrize("label,lat,lon", EDGE_CASES,
                         ids=[case[0] for case in EDGE_CASES])
def test_an_edge_sounding_keeps_the_field_on_its_own_ground(label, lat, lon,
                                                            qt_app):
    """The field must describe the ground it is painted over.

    Shrinking the crop to the image while leaving the destination alone let Qt
    stretch the surviving pixels across the whole inset, sliding the field up to
    0.8 degrees -- some seventy kilometres -- out from under the marker. The
    values shown at the point were then not the values at the point.
    """
    from qtpy import QtCore

    raster = _raster(width=3072, height=1536)
    bounds = hl.zoom_bounds(lat, lon)
    rect = QtCore.QRectF(4.0, 6.0, 240.0, 150.0)

    rects = hl.raster_draw_rects(raster, bounds, 3072, 1536, rect)

    assert rects is not None, label
    source, target = rects
    assert _placement_error(
        bounds, source, target, rect, 3072, 1536) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("label,lat,lon", EDGE_CASES,
                         ids=[case[0] for case in EDGE_CASES])
def test_the_drawn_rectangles_stay_within_the_image_and_the_inset(
        label, lat, lon, qt_app):
    from qtpy import QtCore

    raster = _raster(width=3072, height=1536)
    rect = QtCore.QRectF(4.0, 6.0, 240.0, 150.0)

    source, target = hl.raster_draw_rects(
        raster, hl.zoom_bounds(lat, lon), 3072, 1536, rect)

    sx, sy, sw, sh = source
    assert sx >= -1e-6 and sy >= -1e-6, label
    assert sx + sw <= 3072 + 1e-6 and sy + sh <= 1536 + 1e-6, label
    tx, ty, tw, th = target
    assert tx >= rect.left() - 1e-6 and ty >= rect.top() - 1e-6, label
    assert tx + tw <= rect.right() + 1e-6, label
    assert ty + th <= rect.bottom() + 1e-6, label


def test_an_edge_sounding_leaves_the_uncovered_inset_unpainted(qt_app):
    """Off-domain ground stays empty rather than being filled with a guess."""
    from qtpy import QtCore

    raster = _raster(width=3072, height=1536)
    rect = QtCore.QRectF(0.0, 0.0, 240.0, 150.0)

    _source, target = hl.raster_draw_rects(
        raster, hl.zoom_bounds(52.8, -134.0), 3072, 1536, rect)

    _tx, _ty, tw, th = target
    covered = (tw * th) / (rect.width() * rect.height())
    assert 0.2 < covered < 0.6


def test_an_interior_sounding_fills_the_whole_inset(qt_app):
    from qtpy import QtCore

    raster = _raster(width=3072, height=1536)
    rect = QtCore.QRectF(0.0, 0.0, 240.0, 150.0)

    _source, target = hl.raster_draw_rects(
        raster, hl.zoom_bounds(LAT, LON), 3072, 1536, rect)

    assert target == pytest.approx((0.0, 0.0, 240.0, 150.0), abs=1e-6)


def test_a_sounding_outside_the_field_is_not_drawn(qt_app):
    from qtpy import QtCore

    raster = _raster(width=3072, height=1536)

    assert hl.raster_draw_rects(
        raster, hl.zoom_bounds(51.5, -0.12), 3072, 1536,
        QtCore.QRectF(0.0, 0.0, 240.0, 150.0)) is None


def test_a_sounding_outside_the_field_gets_no_crop():
    """A CONUS field has nothing to say about a sounding over London."""
    raster = _raster(width=3072, height=1536)

    assert hl.raster_source_rect(
        raster, hl.zoom_bounds(51.5, -0.12), 3072, 1536) is None


def test_a_degenerate_field_extent_is_refused():
    raster = _raster(bounds=(-100.0, -100.0 + 1e-12, 40.0, 40.0 + 1e-12))

    assert hl.raster_source_rect(
        raster, hl.zoom_bounds(LAT, LON), 8, 4) is None


def test_an_unreadable_field_extent_is_refused():
    assert hl.raster_source_rect(
        SimpleNamespace(bounds=None), hl.zoom_bounds(LAT, LON), 8, 4) is None


# --------------------------------------------------------------------------- #
# Painting
# --------------------------------------------------------------------------- #
def test_the_field_is_painted_into_the_inset(qt_app):
    from qtpy import QtCore, QtGui

    surface = QtGui.QImage(120, 80, QtGui.QImage.Format_ARGB32_Premultiplied)
    surface.fill(0xFF101418)
    painter = QtGui.QPainter(surface)
    try:
        hl._draw_overlay_rasters(
            painter, (_raster(),), QtCore.QRectF(0, 0, 120, 80),
            hl.zoom_bounds(LAT, LON), QtCore, QtGui)
    finally:
        painter.end()

    painted = sum(1 for y in range(surface.height())
                  for x in range(surface.width())
                  if surface.pixel(x, y) != 0xFF101418)
    assert painted > 120 * 80 * 0.5


def test_a_corrupt_payload_paints_nothing_and_does_not_raise(qt_app):
    from qtpy import QtCore, QtGui

    broken = mo.OverlayRaster(
        key="hrrr_field", title="broken",
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
        bounds=FIELD_BOUNDS, valid_time=VALID,
        retrieved_at=datetime.now(UTC), opacity=0.75)
    surface = QtGui.QImage(60, 40, QtGui.QImage.Format_ARGB32_Premultiplied)
    surface.fill(0xFF101418)
    painter = QtGui.QPainter(surface)
    try:
        hl._draw_overlay_rasters(
            painter, (broken,), QtCore.QRectF(0, 0, 60, 40),
            hl.zoom_bounds(LAT, LON), QtCore, QtGui)
    finally:
        painter.end()

    assert all(surface.pixel(x, y) == 0xFF101418
               for y in range(surface.height())
               for x in range(surface.width()))


# --------------------------------------------------------------------------- #
# The picker hands its live raster over
# --------------------------------------------------------------------------- #
def test_the_viewer_reads_the_field_off_the_controller():
    from sharpmod.gui_viewer import _controller_model_field

    raster = _raster()
    controller = SimpleNamespace(selected_model_field=lambda: raster)

    assert _controller_model_field(controller) is raster


def test_a_controller_without_the_accessor_is_tolerated():
    from sharpmod.gui_viewer import _controller_model_field

    assert _controller_model_field(SimpleNamespace()) is None
    assert _controller_model_field(None) is None


def test_a_raising_controller_is_tolerated():
    from sharpmod.gui_viewer import _controller_model_field

    def _boom():
        raise RuntimeError("no map")

    assert _controller_model_field(
        SimpleNamespace(selected_model_field=_boom)) is None


def test_attaching_reports_whether_it_found_anything():
    from sharpmod.gui_viewer import attach_locator_model_field

    collection = _Collection()
    raster = _raster()

    assert attach_locator_model_field(
        None, collection, SimpleNamespace(selected_model_field=lambda: raster))
    assert mo.locator_rasters(collection) == (raster,)

    assert not attach_locator_model_field(
        None, _Collection(), SimpleNamespace())


# --------------------------------------------------------------------------- #
# Naming the field
#
# The inset used to draw the field with nothing identifying it, so a reader who
# had chosen the significant tornado parameter saw shading around the point and
# no way to tell it from CAPE or helicity, which occupy the same colours in the
# same places.
# --------------------------------------------------------------------------- #
INSET = (105.0, 62.0)
BACKGROUND = 0xFF05090B


def _ink_columns(image, top, bottom):
    """Which x columns carry non-background ink between two rows."""
    columns = set()
    for y in range(max(0, top), min(image.height(), bottom)):
        for x in range(image.width()):
            if image.pixel(x, y) != BACKGROUND:
                columns.add(x)
    return columns


def _render_chip(text, qt_app, rect=None):
    from qtpy import QtCore, QtGui

    box = rect or QtCore.QRectF(0.0, 0.0, *INSET)
    surface = QtGui.QImage(int(box.width()), int(box.height()),
                           QtGui.QImage.Format_ARGB32_Premultiplied)
    surface.fill(BACKGROUND)
    painter = QtGui.QPainter(surface)
    try:
        hl._draw_field_chip(painter, text, box, "#05090b", "#ffffff",
                            QtCore, QtGui)
    finally:
        painter.end()
    return surface


@pytest.mark.parametrize("key,expected", [
    ("stp", "STP"),
    ("srh-0-3km", "0-3 km SRH"),
    ("hgt-wind-500", "500 mb Wind"),
    ("refc", "REFC"),
])
def test_the_chip_names_the_product_the_raster_carries(key, expected):
    assert hl.raster_chip_text(_raster(short_name=key)) == expected


def test_an_unrecognised_field_names_itself_rather_than_another_product():
    """``get_product`` resolves an unknown key to the default, which would lie.

    Labelling an unrecognised raster "REFC" asserts an identity it does not have,
    which is worse than an ugly label.
    """
    unknown = SimpleNamespace(short_name="not-a-product", title="Some Field")

    assert hl.raster_chip_text(unknown) == "NOT-A-PRODUCT"


def test_a_raster_with_no_identifier_falls_back_to_its_title():
    assert hl.raster_chip_text(
        SimpleNamespace(short_name="", title="Some Field")) == "Some Field"


def test_a_raster_with_nothing_to_say_yields_no_label():
    assert hl.raster_chip_text(SimpleNamespace()) == ""


def test_the_chip_is_drawn_in_the_bottom_right_corner(qt_app):
    surface = _render_chip("0-3 km SRH", qt_app)

    band = _ink_columns(surface, int(INSET[1]) - 18, int(INSET[1]))
    assert band, "no chip was drawn"
    assert min(band) >= INSET[0] / 2.0 - 10.0, "the chip is not on the right"
    assert max(band) <= INSET[0] - 1, "the chip runs past the frame"
    assert not _ink_columns(surface, 0, int(INSET[1]) - 20), \
        "the chip strayed out of the bottom strip"


def test_the_field_chip_clears_the_outlook_chip(qt_app):
    """Both can be showing: the field is the airmass, the outlook a judgement."""
    from qtpy import QtCore, QtGui

    box = QtCore.QRectF(0.0, 0.0, *INSET)
    surface = QtGui.QImage(int(INSET[0]), int(INSET[1]),
                           QtGui.QImage.Format_ARGB32_Premultiplied)
    surface.fill(BACKGROUND)
    painter = QtGui.QPainter(surface)
    try:
        hl._draw_overlay_badge(painter, ("ENH", "#ff8000", "#ffc800"), box,
                               QtCore, QtGui)
        outlook = _ink_columns(surface, int(INSET[1]) - 18, int(INSET[1]))
        hl._draw_field_chip(painter, "0-3 km SRH", box, "#05090b", "#ffffff",
                            QtCore, QtGui)
    finally:
        painter.end()

    both = _ink_columns(surface, int(INSET[1]) - 18, int(INSET[1]))
    added = both - outlook
    assert outlook and added
    assert min(added) > max(outlook), "the two chips overlap"


def test_a_long_name_elides_instead_of_overflowing(qt_app):
    surface = _render_chip("A Ridiculously Long Product Name", qt_app)

    band = _ink_columns(surface, int(INSET[1]) - 18, int(INSET[1]))
    assert band
    assert max(band) <= INSET[0] - 1, "a long name ran off the frame"
    assert min(band) >= INSET[0] / 2.0 - 10.0, \
        "a long name grew into the outlook chip's corner"


def test_no_label_draws_no_chip(qt_app):
    """An empty box would assert that a field is attached when none is."""
    surface = _render_chip("", qt_app)

    assert not _ink_columns(surface, 0, int(INSET[1]))


def test_every_catalogued_product_fits_the_inset(qt_app):
    """A chip that elides has stopped identifying the field."""
    from sharpmod import hrrr_products

    for product in hrrr_products.available_products():
        surface = _render_chip(product.short_label, qt_app)
        band = _ink_columns(surface, int(INSET[1]) - 18, int(INSET[1]))
        assert band, "%s drew no chip" % product.key
        assert max(band) <= INSET[0] - 1, product.key
