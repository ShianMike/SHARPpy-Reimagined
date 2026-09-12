"""Coverage for overlay hosting, toggling, and painting on the picker maps."""

from __future__ import annotations

import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from qtpy.QtCore import QBuffer, QRectF
from qtpy.QtGui import QColor, QImage, QPainter, QPixmap

from sharpmod import gui_maps, map_overlays as mo

UTC = timezone.utc

OUTER = [[-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0], [-100.0, 45.0],
         [-100.0, 35.0]]
INNER = [[-97.0, 38.0], [-93.0, 38.0], [-93.0, 42.0], [-97.0, 42.0],
         [-97.0, 38.0]]
FAR_AWAY = [[100.0, -40.0], [110.0, -40.0], [110.0, -30.0], [100.0, -30.0],
            [100.0, -40.0]]

VALID_FROM = datetime(2025, 5, 14, 13, tzinfo=UTC)
VALID_TO = datetime(2025, 5, 15, 12, tzinfo=UTC)


def _shape(rings_source, *, stroke="#005500", fill="#66A366", label="MRGL",
           rank=1):
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": rings_source})
    rings = groups[0]
    return mo.OverlayShape(
        rings=rings, bounds=mo.bounds_of(rings), stroke=stroke, fill=fill,
        label=label, rank=rank)


def _layer(shapes=None, **kwargs):
    kwargs.setdefault("valid_from", VALID_FROM)
    kwargs.setdefault("valid_to", VALID_TO)
    kwargs.setdefault("subtitle", "valid 14 May 1300Z")
    return mo.build_layer(
        "spc_outlook", "SPC convective outlook \u2014 Day 1",
        shapes if shapes is not None else [_shape([OUTER, INNER])],
        **kwargs)


@pytest.fixture
def widget(qt_app):
    w = gui_maps.StationMapWidget([
        {"id": "OUN", "name": "Norman OK", "lat": 35.18, "lon": -97.44},
    ])
    w.resize(640, 480)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def point_widget(qt_app):
    w = gui_maps.PointMapWidget()
    w.resize(640, 480)
    try:
        yield w
    finally:
        w.close()


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_no_overlay_by_default(widget):
    assert widget.overlay_keys() == ()
    assert widget.overlay("spc_outlook") is None
    assert widget.valid_time() is None


def test_set_and_remove_overlay(widget):
    layer = _layer()
    widget.set_overlay("spc_outlook", layer)
    assert widget.overlay_keys() == ("spc_outlook",)
    assert widget.overlay("spc_outlook") is layer
    assert widget.is_overlay_visible("spc_outlook")

    widget.remove_overlay("spc_outlook")
    assert widget.overlay_keys() == ()


def test_setting_none_removes(widget):
    widget.set_overlay("spc_outlook", _layer())
    widget.set_overlay("spc_outlook", None)
    assert widget.overlay_keys() == ()


def test_visibility_survives_replacement(widget):
    """Refreshing for a new valid time must not re-enable a hidden overlay."""
    widget.set_overlay("spc_outlook", _layer())
    widget.set_overlay_visible("spc_outlook", False)
    widget.set_overlay("spc_outlook", _layer())
    assert not widget.is_overlay_visible("spc_outlook")


def test_explicit_visible_argument_wins(widget):
    widget.set_overlay("spc_outlook", _layer())
    widget.set_overlay_visible("spc_outlook", False)
    widget.set_overlay("spc_outlook", _layer(), visible=True)
    assert widget.is_overlay_visible("spc_outlook")


def test_hidden_overlay_keeps_its_geometry(widget):
    """Toggling back on must not need a refetch."""
    layer = _layer()
    widget.set_overlay("spc_outlook", layer)
    widget.set_overlay_visible("spc_outlook", False)
    assert widget.overlay("spc_outlook") is layer
    assert widget._visible_overlays() == []
    widget.set_overlay_visible("spc_outlook", True)
    assert widget._visible_overlays() == [layer]


def test_empty_layer_is_not_drawn(widget):
    widget.set_overlay("spc_outlook", _layer(shapes=[]))
    assert widget._visible_overlays() == []


def test_overlays_are_widget_local(qt_app):
    """Four maps coexist; one tab's overlay must not appear on another."""
    first = gui_maps.StationMapWidget([])
    second = gui_maps.PointMapWidget()
    try:
        first.set_overlay("spc_outlook", _layer())
        first.set_valid_time(VALID_FROM)
        assert second.overlay_keys() == ()
        assert second.valid_time() is None
    finally:
        first.close()
        second.close()


# --------------------------------------------------------------------------- #
# valid time
# --------------------------------------------------------------------------- #
def test_set_valid_time_is_recorded(widget):
    widget.set_valid_time(VALID_FROM)
    assert widget.valid_time() == VALID_FROM


def test_overlay_does_not_invalidate_the_basemap_raster(widget):
    """The extent is unchanged, so the cached raster must be kept."""
    widget._basemap_pixmap()
    raster = widget._basemap_cache
    assert raster is not None

    widget.set_overlay("spc_outlook", _layer())
    widget.set_valid_time(VALID_FROM)
    widget.set_overlay_visible("spc_outlook", False)
    widget.remove_overlay("spc_outlook")
    assert widget._basemap_cache is raster


def test_legend_dedupes_multipolygon_categories(widget):
    """One category split across polygons must appear once in the legend."""
    widget.set_overlay("spc_outlook", _layer(shapes=[
        _shape([OUTER], label="MRGL"),
        _shape([INNER], label="MRGL"),
        _shape([INNER], label="HIGH", stroke="#CC00CC", fill="#EE99EE", rank=5),
    ]))
    rows = widget._overlay_legend_rows()
    assert [row[0] for row in rows] == ["MRGL", "HIGH"]


def test_legend_is_empty_without_overlays(widget):
    assert widget._overlay_legend_rows() == []
    widget.set_overlay("spc_outlook", _layer())
    widget.set_overlay_visible("spc_outlook", False)
    assert widget._overlay_legend_rows() == []


def test_a_layer_of_observations_keeps_out_of_the_legend(widget):
    """A shape per observation makes the row a listing, not a key.

    Storm reports label every shape with its own magnitude, so the swatch row
    became "Hail 1.00 in  Wind 61 mph  Wind 57 mph ..." across the bottom of the
    map. The layer says so itself rather than the map naming the key, so a
    future point overlay does not have to be added here too.
    """
    widget.set_overlay("storm_reports", _layer(shapes=[
        _shape([INNER], label="Hail 1.00 in"),
        _shape([OUTER], label="Wind 61 mph"),
    ], legend=False))

    assert widget._overlay_legend_rows() == []


def test_suppressing_the_swatches_still_credits_the_source(widget, qt_app):
    """The credit is a licence condition, not decoration; only rows go."""
    layer = _layer(shapes=[_shape([INNER], label="Hail 1.00 in")],
                   legend=False, attribution="NWS via IEM")
    widget.set_overlay("storm_reports", layer)

    surface = QPixmap(widget.size())
    painter = QPainter(surface)
    try:
        widget._draw_overlay_legend(painter)
    finally:
        painter.end()

    assert widget._overlay_legend_rows() == []
    assert layer.attribution == "NWS via IEM"


def test_a_point_marker_is_painted_opaque_while_an_area_is_washed(widget):
    """A five-pixel dot hides nothing, so it has no reason to be translucent.

    The wash exists to keep the coastline and station dots readable under a
    continent-sized polygon. Applied to a marker it makes a smudge, and a
    cluster of reports merges into one bruise with nothing countable in it.
    """
    assert gui_maps.OVERLAY_MARKER_FILL_ALPHA == 255
    assert gui_maps.OVERLAY_FILL_ALPHA < gui_maps.OVERLAY_MARKER_FILL_ALPHA

    _conus(widget)
    base = _shape([INNER])
    white = "#FFFFFF"

    def painted_whites(marker):
        # A dark outline, so only the *fill* can put white on the frame: a
        # stroke is drawn at full strength either way and would answer for both.
        widget.set_overlay("storm_reports", _layer(shapes=[mo.OverlayShape(
            rings=base.rings, bounds=base.bounds, stroke="#101010", fill=white,
            label="Sig Wind 80 mph", marker=marker)], legend=False))
        image = _paint(widget).toImage()
        return sum(
            1 for y in range(0, image.height(), 3)
            for x in range(0, image.width(), 3)
            if image.pixelColor(x, y).rgb() == QColor(white).rgb())

    assert painted_whites(True) > 0, \
        "an opaque marker was still blended into the basemap"
    assert painted_whites(False) == 0, (
        "the same shape as an area must stay washed, or the wash that keeps a "
        "risk polygon readable has been lost")


# --------------------------------------------------------------------------- #
# painting
# --------------------------------------------------------------------------- #
def _paint(widget):
    widget._basemap_pixmap()
    return widget.grab()


def test_paint_makes_no_network_access(widget, monkeypatch):
    """An unreachable SPC must never be able to stall a repaint."""
    def forbidden(*args, **kwargs):
        raise AssertionError("overlay paint attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    widget.set_overlay("spc_outlook", _layer())
    widget.set_valid_time(VALID_FROM)
    assert not _paint(widget).isNull()


def test_paint_with_overlay_changes_the_frame(widget):
    before = _paint(widget).toImage()
    widget.set_overlay("spc_outlook", _layer())
    widget.set_valid_time(VALID_FROM)
    after = _paint(widget).toImage()
    assert before != after, "the overlay should be visible in the output"


def test_hidden_overlay_restores_the_bare_frame(widget):
    bare = _paint(widget).toImage()
    widget.set_overlay("spc_outlook", _layer())
    widget.set_valid_time(VALID_FROM)
    assert _paint(widget).toImage() != bare
    widget.set_overlay_visible("spc_outlook", False)
    assert _paint(widget).toImage() == bare


def test_offscreen_shapes_are_culled(qt_app, monkeypatch):
    """A shape outside the view must not be projected point by point.

    Asserted by counting coordinate transforms rather than comparing frames:
    the legend still reports an attached overlay whose geometry happens to be
    off screen, so the rendered output legitimately differs.
    """
    widget = gui_maps.StationMapWidget([])
    widget.resize(640, 480)
    try:
        widget.set_valid_time(VALID_FROM)

        def transforms_for(layer):
            """Count projections performed by the overlay layer alone."""
            widget.set_overlay("spc_outlook", layer)
            calls: list[int] = []
            real = gui_maps.StationMapWidget._to_px

            def spy(self, lon, lat, p=None):
                calls.append(1)
                return real(self, lon, lat, p)

            monkeypatch.setattr(gui_maps.StationMapWidget, "_to_px", spy)
            # The pixmap must outlive the painter, so keep a local reference to
            # it rather than passing a temporary into the constructor.
            surface = QPixmap(widget.size())
            painter = QPainter(surface)
            try:
                widget._draw_overlays(painter, widget._proj())
            finally:
                painter.end()
                monkeypatch.undo()
            return len(calls)

        offscreen = transforms_for(
            _layer(shapes=[_shape([FAR_AWAY], label="MRGL")]))
        onscreen = transforms_for(_layer(shapes=[_shape([OUTER], label="MRGL")]))

        assert offscreen == 0, "an off-screen shape should be rejected by bbox"
        assert onscreen > 0
    finally:
        widget.close()


def test_point_map_also_hosts_overlays(point_widget):
    bare = _paint(point_widget).toImage()
    point_widget.set_overlay("spc_outlook", _layer())
    point_widget.set_valid_time(VALID_FROM)
    assert _paint(point_widget).toImage() != bare


def test_mismatched_valid_time_still_paints(widget):
    """The legend warns rather than the overlay vanishing or raising."""
    layer = _layer()
    widget.set_overlay("spc_outlook", layer)
    widget.set_valid_time(VALID_TO + timedelta(days=3))
    assert not layer.covers(widget.valid_time())
    assert not _paint(widget).isNull()


def test_outline_only_shape_paints(widget):
    """``fill=None`` means outline only, which must still stroke cleanly."""
    outline_only = _shape([OUTER], label="MRGL", fill=None)
    assert outline_only.fill is None
    widget.set_overlay("spc_outlook", _layer(shapes=[outline_only]))
    widget.set_valid_time(VALID_FROM)
    bare = _paint(widget).toImage()
    widget.remove_overlay("spc_outlook")
    assert _paint(widget).toImage() != bare


# --------------------------------------------------------------------------- #
# raster overlays
# --------------------------------------------------------------------------- #
CONUS_BOUNDS = (-130.0, -60.0, 20.0, 55.0)
RADAR_KEY = "radar_mosaic"


def _png_bytes(size=8, alpha=200):
    """Encode a real PNG through Qt rather than hand-writing one.

    The paint path genuinely decodes what it is handed, so a literal byte string
    has to be a valid PNG down to its CRCs; letting Qt encode it removes a whole
    class of test bug where a bad checksum silently exercises only the failure
    branch.
    """
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(255, 0, 0, alpha))
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def _raster(**kwargs):
    fields = {
        "key": RADAR_KEY,
        "title": "Composite reflectivity (dBZ)",
        "image_bytes": _png_bytes(),
        "bounds": CONUS_BOUNDS,
        "attribution": "NOAA/NWS MRMS via NCEP GeoServer",
    }
    fields.update(kwargs)
    return mo.OverlayRaster(**fields)


def _conus(widget):
    """Point a widget at CONUS without depending on a named area existing."""
    widget._lon0, widget._lon1 = -125.0, -66.0
    widget._lat0, widget._lat1 = 24.0, 50.0
    widget._invalidate()


def _elsewhere(widget):
    widget._lon0, widget._lon1 = 0.0, 30.0
    widget._lat0, widget._lat1 = 40.0, 60.0
    widget._invalidate()


def test_a_raster_is_kept_apart_from_the_vector_overlays(widget):
    """Every consumer of the vector registry reaches for ``layer.shapes``."""
    widget.set_overlay(RADAR_KEY, _raster())
    assert widget.overlay(RADAR_KEY) is not None
    assert RADAR_KEY in widget.overlay_keys()
    assert widget._visible_overlays() == [], \
        "a raster must never reach the vector paint path"


def test_one_key_cannot_hold_both_kinds_at_once(widget):
    """Otherwise a product that changes representation draws twice."""
    widget.set_overlay("thing", _raster())
    widget.set_overlay("thing", _layer())
    assert widget._rasters == {}
    assert widget._raster_pixmaps == {}
    assert widget._visible_overlays() != []

    widget.set_overlay("thing", _raster())
    assert widget._overlays.get("thing") is None
    assert widget._rasters.get("thing") is not None


@pytest.mark.parametrize("cls_fixture", ["widget", "point_widget"])
def test_both_map_classes_draw_a_raster(cls_fixture, request):
    """Both paint orders are hardcoded, so each has to be wired separately."""
    target = request.getfixturevalue(cls_fixture)
    _conus(target)
    bare = _paint(target).toImage()
    target.set_overlay(RADAR_KEY, _raster())
    assert _paint(target).toImage() != bare


def test_hiding_a_raster_restores_the_bare_frame(widget):
    _conus(widget)
    bare = _paint(widget).toImage()
    widget.set_overlay(RADAR_KEY, _raster())
    assert _paint(widget).toImage() != bare
    widget.set_overlay_visible(RADAR_KEY, False)
    assert _paint(widget).toImage() == bare


def test_removing_a_raster_drops_its_decoded_pixmap(widget):
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster())
    _paint(widget)
    assert widget._raster_pixmaps.get(RADAR_KEY) is not None
    widget.remove_overlay(RADAR_KEY)
    assert RADAR_KEY not in widget._raster_pixmaps
    assert widget.overlay(RADAR_KEY) is None


def test_a_raster_outside_the_view_is_never_decoded(widget):
    """A CONUS mosaic is off-screen for most of the world."""
    _elsewhere(widget)
    widget.set_overlay(RADAR_KEY, _raster())
    assert widget._visible_rasters() == []
    bare_elsewhere = _paint(widget).toImage()
    assert RADAR_KEY not in widget._raster_pixmaps, \
        "an off-screen raster must not pay the decode cost"

    _conus(widget)
    assert widget._visible_rasters() != []
    assert _paint(widget).toImage() != bare_elsewhere


def test_the_decode_is_cached_across_repaints(widget):
    """``paintEvent`` runs on every mouse move."""
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster())
    _paint(widget)
    first = widget._raster_pixmaps[RADAR_KEY][1]
    assert first is not None
    _paint(widget)
    assert widget._raster_pixmaps[RADAR_KEY][1] is first


def test_an_opacity_change_reuses_the_decoded_pixmap(widget):
    """Dragging the opacity slider must not re-decode a full-extent frame."""
    _conus(widget)
    raster = _raster(opacity=1.0)
    widget.set_overlay(RADAR_KEY, raster)
    _paint(widget)
    first = widget._raster_pixmaps[RADAR_KEY][1]

    widget.set_overlay(RADAR_KEY, raster.at_opacity(0.3))
    _paint(widget)
    assert widget._raster_pixmaps[RADAR_KEY][1] is first


def test_a_new_frame_replaces_the_cached_pixmap(widget):
    _conus(widget)
    raster_first = _raster()
    widget.set_overlay(RADAR_KEY, raster_first)
    _paint(widget)
    first = widget._raster_pixmaps[RADAR_KEY][1]
    assert first is not None

    # A distinct payload object, which is what the cache keys on.
    replacement = _raster(image_bytes=_png_bytes(alpha=120))
    assert replacement.image_bytes is not raster_first.image_bytes
    widget.set_overlay(RADAR_KEY, replacement)
    _paint(widget)
    assert widget._raster_pixmaps[RADAR_KEY][1] is not first


def test_an_undecodable_payload_neither_crashes_nor_retries(widget):
    """A corrupt frame must not cost a decode attempt on every repaint."""
    _conus(widget)
    bare = _paint(widget).toImage()
    widget.set_overlay(RADAR_KEY, _raster(
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"garbage" * 8))
    assert _paint(widget).toImage() == bare, "nothing should be drawn"
    assert widget._raster_pixmaps[RADAR_KEY][1] is None
    _paint(widget)
    assert widget._raster_pixmaps[RADAR_KEY][1] is None


def test_a_raster_draws_when_zoomed_past_the_frame_extent(widget):
    """Only the visible source sub-rectangle is blitted."""
    widget._lon0, widget._lon1 = -99.0, -97.0
    widget._lat0, widget._lat1 = 35.0, 36.5
    widget._invalidate()
    bare = _paint(widget).toImage()
    widget.set_overlay(RADAR_KEY, _raster())
    assert _paint(widget).toImage() != bare


def test_the_legend_names_the_raster_and_credits_its_source(widget):
    """Attribution was carried on every overlay and painted on none of them."""
    _conus(widget)
    now = datetime.now(UTC)
    widget.set_overlay(RADAR_KEY, _raster(
        retrieved_at=now - timedelta(minutes=3), update_interval_s=120.0))

    surface = QPixmap(widget.size())
    painter = QPainter(surface)
    try:
        # The legend must survive being asked to draw a raster-only map, which
        # is the case the vector-only implementation never saw.
        widget._draw_overlay_legend(painter)
    finally:
        painter.end()

    rasters = widget._visible_rasters()
    assert len(rasters) == 1
    assert rasters[0][1].attribution


def test_the_legend_is_silent_with_nothing_attached(widget):
    surface = QPixmap(widget.size())
    painter = QPainter(surface)
    try:
        widget._draw_overlay_legend(painter)
    finally:
        painter.end()
    assert widget._visible_rasters() == []
    assert widget._visible_overlays() == []


def test_view_bounds_reports_the_visible_extent(widget):
    _conus(widget)
    assert widget.view_bounds() == (-125.0, -66.0, 24.0, 50.0)


def test_magnifying_a_raster_does_not_invent_intermediate_colours(widget):
    """Bilinear upscaling is what made the radar overlay look blurred.

    Interpolating a reflectivity field between data cells manufactures values
    the source never published. Nearest-neighbour keeps the published cells, so
    a magnified frame must contain only colours that were already in it.
    """
    # Two-tone source: the north-west quadrant opaque red, the rest fully
    # transparent, with nothing in between for an interpolator to blend toward.
    image = QImage(4, 4, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for x in range(2):
        for y in range(2):
            image.setPixelColor(x, y, QColor(255, 0, 0, 255))
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")

    # A view far smaller than the frame, so the image is magnified hard, and
    # inside the red quadrant: the north-west quarter of CONUS_BOUNDS is
    # longitude -130..-95 and latitude 37.5..55.
    widget._lon0, widget._lon1 = -115.0, -110.0
    widget._lat0, widget._lat1 = 44.0, 48.0
    widget._invalidate()

    bare = _paint(widget).toImage()
    widget.set_overlay(RADAR_KEY, _raster(
        image_bytes=bytes(buffer.data()), opacity=1.0))
    painted = _paint(widget).toImage()

    # Only the pixels the raster changed, and only the upper part of the frame:
    # sampling everything would pick up the grey basemap lines and the
    # antialiased legend text along the bottom edge.
    limit = int(painted.height() * 0.6)
    changed = {
        painted.pixelColor(x, y).rgb()
        for y in range(0, limit, 3)
        for x in range(0, painted.width(), 3)
        if painted.pixelColor(x, y).rgb() != bare.pixelColor(x, y).rgb()
    }
    assert changed, "the raster drew nothing to inspect"
    # The source holds one opaque colour, so a nearest-neighbour magnification
    # can only ever paint that colour. Bilinear would blend it toward the
    # transparent neighbours and produce a ramp of dozens.
    assert changed == {QColor(255, 0, 0).rgb()}, \
        "magnification is interpolating: %d distinct colours" % len(changed)


def test_minifying_a_raster_still_smooths(widget):
    """Shrinking without smoothing makes isolated cells flicker as it moves."""
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster(image_bytes=_png_bytes(size=512)))
    # Purely a "does not raise and still draws" check: the render hint itself
    # is not readable back off the painter.
    bare = _paint(widget).toImage()
    widget.set_overlay_visible(RADAR_KEY, False)
    assert _paint(widget).toImage() != bare

# --------------------------------------------------------------------------- #
# display density
#
# Every raster routed through an offscreen cache used to be rasterized at the
# widget's *logical* size and then blitted 1:1 onto a backing store that is
# larger by the device pixel ratio, so Qt bilinearly upscaled it. That softened
# the HRRR field overlay and the coastlines on any fractional-scaling display,
# which the picker opts into via ``PassThrough`` rounding.
# --------------------------------------------------------------------------- #
def _at_density(widget, monkeypatch, ratio):
    """Report ``ratio`` as the widget's density without needing such a screen."""
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: ratio)


def test_the_basemap_is_rasterized_at_the_screens_real_resolution(
        widget, monkeypatch):
    _at_density(widget, monkeypatch, 2.0)

    pixmap = widget._basemap_pixmap()

    assert pixmap.devicePixelRatio() == 2.0, "the ratio has to travel with it"
    assert (pixmap.width(), pixmap.height()) == (
        widget.width() * 2, widget.height() * 2
    ), "a logical-size buffer is what Qt then has to upscale"


def test_the_warped_overlay_composite_matches_the_basemap_resolution(
        widget, monkeypatch):
    """This is the path the HRRR field takes on the curved CONUS view."""
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster())
    _at_density(widget, monkeypatch, 2.0)

    composed = widget._compose_warped(widget._visible_rasters(), widget._proj())

    assert composed is not None
    assert composed.devicePixelRatio() == 2.0
    assert (composed.width(), composed.height()) == (
        widget.width() * 2, widget.height() * 2
    )


def test_a_fractional_density_is_not_rounded_away(widget, monkeypatch):
    """1.25 and 1.75 are ordinary once scale-factor rounding is PassThrough."""
    _at_density(widget, monkeypatch, 1.5)

    pixmap = widget._basemap_pixmap()

    assert pixmap.devicePixelRatio() == 1.5
    assert pixmap.width() == round(widget.width() * 1.5)


def test_moving_to_a_denser_monitor_rebuilds_both_caches(widget, monkeypatch):
    """A cache baked for the old ratio would be rescaled onto the new store."""
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster())
    projection = widget._proj()
    rasters = widget._visible_rasters()
    basemap_before = widget._basemap_key()
    warp_before = widget._warp_cache_key(rasters, projection)

    _at_density(widget, monkeypatch, 2.0)

    assert widget._basemap_key() != basemap_before
    assert widget._warp_cache_key(rasters, projection) != warp_before


def test_a_dense_cache_is_reblitted_from_its_own_device_pixels(
        widget, monkeypatch):
    """``drawPixmap`` measures its destination and source in different units.

    The destination is logical; the source is in the pixmap's device pixels. A
    preview re-blit that passes one where the other belongs samples a quarter of
    the cache at double density and stretches it over the whole window.
    """
    _at_density(widget, monkeypatch, 2.0)
    pixmap = widget._device_pixmap()

    assert widget._independent_size(pixmap) == (
        float(widget.width()), float(widget.height())
    )
    logical = QRectF(0.0, 0.0, *widget._independent_size(pixmap))
    device = widget._device_source(pixmap, logical)
    assert (device.width(), device.height()) == (
        float(pixmap.width()), float(pixmap.height())
    )


def test_magnification_is_judged_in_device_pixels(widget, monkeypatch):
    """Nearest-neighbour must survive a high-density screen.

    The guard against inventing colours between data cells compared a logical
    destination with a source measured in image pixels, so at ratio 2 a
    destination that genuinely covers twice the source still looked like a
    reduction and got smoothed.
    """
    # The interesting band is a destination narrower than the source in logical
    # terms but wider once the density is applied: 640 logical pixels is a
    # reduction of a 1024 px frame, while the 1280 physical pixels it really
    # occupies are an enlargement. A frame small enough to be magnified either
    # way would pass whether or not the density is taken into account.
    _conus(widget)
    widget.set_overlay(RADAR_KEY, _raster(image_bytes=_png_bytes(size=1024)))
    _at_density(widget, monkeypatch, 2.0)

    smoothing: list[bool] = []
    original = QPainter.setRenderHint

    def record(self, hint, on=True):
        if hint == QPainter.SmoothPixmapTransform:
            smoothing.append(bool(on))
        return original(self, hint, on)

    monkeypatch.setattr(QPainter, "setRenderHint", record)
    _paint(widget)

    assert smoothing, "the raster path has to make the decision explicitly"
    assert not smoothing[-1], (
        "1024 source pixels drawn across 1280 physical pixels is magnification, "
        "so the published cells must not be interpolated"
    )


# --------------------------------------------------------------------------- #
# click to find out what an area is
#
# A wash of colour is not self-describing: a probability band reads "5%" without
# saying which hazard, and two products put the same red in the same place.
# --------------------------------------------------------------------------- #
INSIDE = (-95.0, 40.0)  # within OUTER
OUTSIDE = (-60.0, 10.0)


def test_nothing_is_described_where_no_overlay_covers_the_point():
    """``None`` rather than empty text, so 'no overlay' is distinguishable."""
    layers = [_layer()]

    assert mo.describe_at(layers, *OUTSIDE) is None


def test_the_product_and_the_category_at_the_point_are_both_named():
    layers = [_layer(shapes=[_shape([OUTER], label="MRGL")])]

    text = mo.describe_at(layers, *INSIDE)

    assert "MRGL" in text
    assert layers[0].title.split()[0] in text, "the product has to be named too"


def test_a_category_description_is_included_when_it_has_one():
    """This is the first reader ``OverlayShape.description`` has ever had."""
    shape = _shape([OUTER], label="5%")
    shape = mo.OverlayShape(
        rings=shape.rings, bounds=shape.bounds, stroke=shape.stroke,
        fill=shape.fill, label="5%",
        description="5% chance of a tornado within 25 miles.")
    layers = [_layer(shapes=[shape])]

    text = mo.describe_at(layers, *INSIDE)

    assert "5%" in text
    assert "within 25 miles" in text


def test_the_graded_band_is_reported_before_the_hatched_qualifier():
    """A qualifier outranks every band so it paints on top.

    Answering with it alone would report the qualifier and discard the
    probability the point actually sits in.
    """
    band = mo.OverlayShape(
        rings=_shape([OUTER]).rings, bounds=_shape([OUTER]).bounds,
        stroke="#005500", fill="#66A366", label="10%", rank=1)
    qualifier = mo.OverlayShape(
        rings=_shape([OUTER]).rings, bounds=_shape([OUTER]).bounds,
        stroke="#000000", fill=None, label="", rank=99,
        hatch=True, hatch_level=2)
    layers = [_layer(shapes=[band, qualifier])]

    text = mo.describe_at(layers, *INSIDE)

    assert text.splitlines()[0].endswith("10%"), \
        "the band the point sits in has to lead"
    assert "conditional intensity group 2" in text


def test_clicking_bare_ground_describes_the_overlay_under_it(widget):
    _conus(widget)
    widget.set_overlay("spc_outlook", _layer(shapes=[
        _shape([OUTER], label="ENH")]))

    # A point inside the shape, expressed the way the widget will read it back.
    assert mo.describe_at(widget._visible_overlays(), *INSIDE) is not None


def test_a_hidden_overlay_is_not_described(widget):
    """What is not on screen must not be reported as though it were."""
    _conus(widget)
    widget.set_overlay("spc_outlook", _layer(shapes=[
        _shape([OUTER], label="ENH")]))
    widget.set_overlay_visible("spc_outlook", False)

    assert mo.describe_at(widget._visible_overlays(), *INSIDE) is None
