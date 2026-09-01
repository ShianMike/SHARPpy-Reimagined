"""Coverage for the Qt-free overlay geometry model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sharpmod import map_overlays as mo

UTC = timezone.utc

# A square with a square hole, in the ring order GeoJSON uses.
SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
HOLE = [[3.0, 3.0], [7.0, 3.0], [7.0, 7.0], [3.0, 7.0], [3.0, 3.0]]


def test_polygon_and_multipolygon_both_decode():
    """SPC mixes the two types inside one collection, so both must work."""
    polygon = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE]})
    assert len(polygon) == 1
    assert len(polygon[0]) == 1

    multi = mo.rings_from_geometry(
        {"type": "MultiPolygon", "coordinates": [[SQUARE], [HOLE]]})
    assert len(multi) == 2
    assert all(len(group) == 1 for group in multi)


def test_interior_rings_are_preserved():
    """Holes carry the whole point of using the ``nolyr`` products."""
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE, HOLE]})
    assert len(groups) == 1
    assert len(groups[0]) == 2, "the hole ring must survive decoding"


def test_closing_vertex_is_dropped():
    """A path closes itself; a repeated vertex only adds a stroke artefact."""
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE]})
    ring = groups[0][0]
    assert len(ring) == 4, "the repeated first/last vertex should be removed"
    assert ring[0] != ring[-1]


def test_consecutive_duplicate_points_collapse():
    raw = [[0.0, 0.0], [0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [5.0, 5.0]]
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [raw]})
    assert groups[0][0] == ((0.0, 0.0), (5.0, 0.0), (5.0, 5.0))


@pytest.mark.parametrize("geometry", [
    None,
    {},
    {"type": "Point", "coordinates": [0.0, 0.0]},
    {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
    {"type": "Polygon"},
    {"type": "Polygon", "coordinates": "nope"},
    # Fewer than three vertices cannot enclose area.
    {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0]]]},
])
def test_unusable_geometry_yields_nothing(geometry):
    assert mo.rings_from_geometry(geometry) == ()


def test_out_of_range_and_nonfinite_coordinates_are_rejected():
    raw = [
        [0.0, 0.0],
        [999.0, 0.0],          # lon out of range
        [0.0, 91.0],           # lat out of range
        [float("nan"), 1.0],   # non-finite
        [float("inf"), 1.0],
        ["x", "y"],            # non-numeric
        [5.0],                 # too short
        [5.0, 5.0],
        [10.0, 10.0],
    ]
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [raw]})
    assert groups[0][0] == ((0.0, 0.0), (5.0, 5.0), (10.0, 10.0))


def test_oversized_ring_is_refused():
    huge = [[0.0, 0.0]] * (mo.MAX_POINTS_PER_RING + 1)
    assert mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [huge]}) == ()


def test_bounds_order_matches_the_basemap_convention():
    """``_draw_layer`` unpacks (min_lon, max_lon, min_lat, max_lat)."""
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE, HOLE]})
    assert mo.bounds_of(groups[0]) == (0.0, 10.0, 0.0, 10.0)
    assert mo.bounds_of(()) is None


def _shape(rank: int, label: str = "X") -> mo.OverlayShape:
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE]})
    return mo.OverlayShape(
        rings=groups[0],
        bounds=mo.bounds_of(groups[0]),
        stroke="#ffffff",
        fill="#000000",
        label=label,
        rank=rank,
    )


def test_build_layer_orders_by_rank():
    layer = mo.build_layer(
        "k", "T", [_shape(5, "HIGH"), _shape(0, "TSTM"), _shape(2, "SLGT")])
    assert [s.label for s in layer.shapes] == ["TSTM", "SLGT", "HIGH"]


def test_layer_bounds_span_every_shape():
    far = mo.rings_from_geometry(
        {"type": "Polygon",
         "coordinates": [[[20.0, -5.0], [30.0, -5.0], [30.0, 5.0]]]})
    shapes = [
        _shape(0),
        mo.OverlayShape(rings=far[0], bounds=mo.bounds_of(far[0]),
                        stroke="#fff"),
    ]
    layer = mo.build_layer("k", "T", shapes)
    assert layer.bounds == (0.0, 30.0, -5.0, 10.0)


def test_empty_layer_is_falsey():
    assert not mo.build_layer("k", "T", [])
    assert mo.build_layer("k", "T", [_shape(0)])
    assert mo.build_layer("k", "T", []).bounds is None


def test_point_count_totals_every_ring():
    groups = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE, HOLE]})
    shape = mo.OverlayShape(rings=groups[0], bounds=mo.bounds_of(groups[0]),
                            stroke="#fff")
    assert shape.point_count == 8
    assert mo.build_layer("k", "T", [shape]).point_count == 8


class TestCovers:
    """The validity window is what makes an overlay time aware."""

    start = datetime(2025, 5, 14, 13, tzinfo=UTC)
    end = datetime(2025, 5, 15, 12, tzinfo=UTC)

    def _layer(self, **kwargs):
        return mo.build_layer("k", "T", [_shape(0)], **kwargs)

    def test_inside_window(self):
        layer = self._layer(valid_from=self.start, valid_to=self.end)
        assert layer.covers(self.start)
        assert layer.covers(self.start + timedelta(hours=5))

    def test_boundaries_are_half_open(self):
        """``valid_to`` is exclusive so consecutive outlooks never overlap."""
        layer = self._layer(valid_from=self.start, valid_to=self.end)
        assert layer.covers(self.start)
        assert not layer.covers(self.end)
        assert not layer.covers(self.start - timedelta(minutes=1))

    def test_unbounded_layer_covers_everything(self):
        assert self._layer().covers(self.start)

    def test_none_is_covered(self):
        assert self._layer(valid_from=self.start, valid_to=self.end).covers(None)

    def test_naive_datetime_is_refused(self):
        """Assuming a timezone here would be wrong by up to a full day."""
        layer = self._layer(valid_from=self.start, valid_to=self.end)
        assert not layer.covers(datetime(2025, 5, 14, 18))


def test_shapes_are_immutable():
    layer = mo.build_layer("k", "T", [_shape(0)])
    with pytest.raises((AttributeError, TypeError)):
        layer.shapes[0].rank = 9
    with pytest.raises(AttributeError):
        layer.shapes[0].rings.append(())


# --------------------------------------------------------------------------- #
# point containment
# --------------------------------------------------------------------------- #
def _holed_shape(rank: int = 1, label: str = "MRGL") -> mo.OverlayShape:
    """A 0-10 square with a 3-7 square hole punched out of it."""
    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [SQUARE, HOLE]})[0]
    return mo.OverlayShape(
        rings=rings, bounds=mo.bounds_of(rings), stroke="#005500",
        fill="#66A366", label=label, rank=rank)


@pytest.mark.parametrize("lon,lat,inside", [
    (1.0, 5.0, True),      # between the outer edge and the hole
    (5.0, 1.0, True),
    (9.0, 9.0, True),
    (5.0, 5.0, False),     # inside the hole: belongs to a higher category
    (4.0, 5.0, False),
    (-1.0, 5.0, False),    # outside entirely
    (11.0, 5.0, False),
    (5.0, 11.0, False),
])
def test_shape_contains_respects_holes(lon, lat, inside):
    """The hole is where a higher category sits, so it is not this shape."""
    assert mo.shape_contains(_holed_shape(), lon, lat) is inside


def test_shape_contains_rejects_points_outside_the_bounding_box_cheaply():
    shape = _holed_shape()
    assert not mo.shape_contains(shape, 1000.0, 1000.0)


def test_shape_at_returns_the_most_severe_covering_shape():
    """A forecaster wants the worst category that applies to the point."""
    inner_rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [HOLE]})[0]
    inner = mo.OverlayShape(
        rings=inner_rings, bounds=mo.bounds_of(inner_rings),
        stroke="#CC0000", fill="#E06666", label="MDT", rank=4)
    layer = mo.build_layer("k", "T", [_holed_shape(), inner])

    # Inside the hole, only the more severe shape covers the point.
    assert mo.shape_at([layer], 5.0, 5.0).label == "MDT"
    # Outside the hole, only the outer shape does.
    assert mo.shape_at([layer], 1.0, 5.0).label == "MRGL"
    assert mo.shape_at([layer], 50.0, 50.0) is None


def test_shape_at_ignores_an_empty_layer_list():
    assert mo.shape_at([], 0.0, 0.0) is None


# --------------------------------------------------------------------------- #
# carrying overlays to the locator inset
# --------------------------------------------------------------------------- #
class _FakeCollection:
    """Stand-in for a sharppy ProfCollection's metadata contract."""

    def __init__(self, **meta):
        self._meta = dict(meta)

    def getMeta(self, key, index=False):  # noqa: N802 - upstream Qt-era API
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value


def _named_layer(key: str) -> mo.OverlayLayer:
    return mo.build_layer(key, key.title(), [_shape(0)])


def test_attach_and_read_a_locator_overlay():
    collection = _FakeCollection()
    assert mo.locator_overlays(collection) == ()
    layer = _named_layer("spc_outlook")
    mo.attach_locator_overlay(collection, layer)
    assert mo.locator_overlays(collection) == (layer,)


def test_attaching_the_same_key_replaces_rather_than_accumulates():
    """Refetching for a new time must not stack copies of the same product."""
    collection = _FakeCollection()
    for _ in range(4):
        mo.attach_locator_overlay(collection, _named_layer("spc_outlook"))
    assert len(mo.locator_overlays(collection)) == 1


def test_a_second_product_coexists():
    """The seam is generic so a model product can sit alongside the outlook."""
    collection = _FakeCollection()
    mo.attach_locator_overlay(collection, _named_layer("spc_outlook"))
    mo.attach_locator_overlay(collection, _named_layer("model_product"))
    assert [layer.key for layer in mo.locator_overlays(collection)] == \
        ["spc_outlook", "model_product"]


def test_attaching_none_removes_only_that_product():
    collection = _FakeCollection()
    mo.attach_locator_overlay(collection, _named_layer("spc_outlook"))
    mo.attach_locator_overlay(collection, _named_layer("model_product"))
    mo.attach_locator_overlay(collection, None, key="spc_outlook")
    assert [layer.key for layer in mo.locator_overlays(collection)] == \
        ["model_product"]


def test_an_empty_layer_is_not_attached():
    collection = _FakeCollection()
    mo.attach_locator_overlay(
        collection, mo.build_layer("spc_outlook", "T", []))
    assert mo.locator_overlays(collection) == ()


def test_the_number_of_attached_overlays_is_bounded():
    collection = _FakeCollection()
    for index in range(mo.MAX_LOCATOR_OVERLAYS + 5):
        mo.attach_locator_overlay(collection, _named_layer(f"product{index}"))
    assert len(mo.locator_overlays(collection)) == mo.MAX_LOCATOR_OVERLAYS


@pytest.mark.parametrize("stored", [
    None, "not a list", 42, [None], ["nope"], [object()],
])
def test_unusable_stored_overlays_read_as_empty(stored):
    """This is read from a paint path, so it must never raise."""
    collection = _FakeCollection(**{mo.LOCATOR_OVERLAY_META_KEY: stored})
    assert mo.locator_overlays(collection) == ()


def test_reading_overlays_from_a_collection_without_metadata():
    assert mo.locator_overlays(None) == ()
    assert mo.locator_overlays(object()) == ()


def test_overlays_covering_filters_by_validity_window():
    """Two soundings at different times must not share one overlay."""
    inside = mo.build_layer(
        "a", "A", [_shape(0)],
        valid_from=datetime(2025, 5, 14, 13, tzinfo=UTC),
        valid_to=datetime(2025, 5, 15, 12, tzinfo=UTC))
    unbounded = mo.build_layer("b", "B", [_shape(0)])
    layers = (inside, unbounded)

    covering = mo.overlays_covering(
        layers, datetime(2025, 5, 14, 18, tzinfo=UTC))
    assert covering == (inside, unbounded)

    missing = mo.overlays_covering(
        layers, datetime(2025, 5, 20, 18, tzinfo=UTC))
    assert missing == (unbounded,)


# --------------------------------------------------------------------------- #
# bands versus hatched qualifiers
# --------------------------------------------------------------------------- #
def _band_and_qualifier():
    """A probability band with a hatched qualifier drawn over the same area."""
    band = _shape(150, "15%")
    qualifier = mo.OverlayShape(
        rings=band.rings, bounds=band.bounds, stroke="#000000", fill="#888888",
        label="SIGN", rank=10_000, hatch=True)
    return mo.build_layer("spc_outlook", "T", [band, qualifier],
                          short_name="TOR")


def test_an_unrestricted_lookup_answers_with_the_qualifier():
    """It outranks every band so that it paints on top."""
    layer = _band_and_qualifier()
    assert mo.shape_at([layer], 1.0, 5.0).label == "SIGN"


def test_the_band_can_be_asked_for_separately():
    """Reporting only "SIGN" would discard the probability at the point."""
    layer = _band_and_qualifier()
    assert mo.shape_at([layer], 1.0, 5.0, hatch=False).label == "15%"


def test_the_qualifier_can_be_asked_for_separately():
    layer = _band_and_qualifier()
    assert mo.shape_at([layer], 1.0, 5.0, hatch=True).label == "SIGN"


def test_a_band_without_a_qualifier_reports_none_for_hatch():
    layer = mo.build_layer("spc_outlook", "T", [_shape(150, "15%")])
    assert mo.shape_at([layer], 1.0, 5.0, hatch=False).label == "15%"
    assert mo.shape_at([layer], 1.0, 5.0, hatch=True) is None


def test_a_layer_carries_a_compact_product_name():
    assert _band_and_qualifier().short_name == "TOR"
    assert mo.build_layer("k", "T", [_shape(0)]).short_name == ""


# --------------------------------------------------------------------------- #
# Raster overlays
# --------------------------------------------------------------------------- #
#: Enough to satisfy the magic-byte gate. Nothing here decodes an image.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CONUS = (-130.0, -60.0, 20.0, 55.0)


def _raster(**kwargs):
    fields = {
        "key": "radar",
        "title": "Composite reflectivity",
        "image_bytes": PNG,
        "bounds": CONUS,
    }
    fields.update(kwargs)
    return mo.OverlayRaster(**fields)


@pytest.mark.parametrize("payload,expected", [
    (PNG, True),
    (b"GIF89a" + b"\x00" * 8, True),
    (b"\xff\xd8\xff" + b"\x00" * 8, True),
    (b'<?xml version="1.0"?><ServiceException/>', False),
    (b"", False),
    (b"not an image at all", False),
    (None, False),
])
def test_only_real_image_signatures_are_accepted(payload, expected):
    """A WMS reports failure as XML with HTTP 200, so the status cannot decide."""
    assert mo.looks_like_image(payload) is expected


def test_a_non_image_payload_is_refused_at_construction():
    with pytest.raises(ValueError):
        _raster(image_bytes=b'<ServiceException>nope</ServiceException>')


def test_an_oversized_payload_is_refused():
    with pytest.raises(ValueError):
        _raster(image_bytes=PNG + b"\x00" * mo.MAX_RASTER_BYTES)


@pytest.mark.parametrize("bounds", [
    (10.0, 10.0, 20.0, 55.0),      # zero-width longitude span
    (-60.0, -130.0, 20.0, 55.0),   # longitudes reversed
    (-130.0, -60.0, 55.0, 20.0),   # latitudes reversed
    (-200.0, -60.0, 20.0, 55.0),   # longitude out of range
    (-130.0, -60.0, 20.0, 95.0),   # latitude out of range
    (-130.0, -60.0, 20.0),         # not four values
    (-130.0, -60.0, 20.0, float("nan")),
])
def test_unusable_bounds_are_refused(bounds):
    """A raster with a bad box would draw somewhere confidently wrong."""
    with pytest.raises(ValueError):
        _raster(bounds=bounds)


def test_opacity_is_clamped_rather_than_refused():
    """It arrives from a slider; drifting out of range must not blank the map."""
    assert _raster(opacity=1.7).opacity == 1.0
    assert _raster(opacity=-3.0).opacity == 0.0
    assert _raster(opacity=0.4).opacity == pytest.approx(0.4)
    assert _raster(opacity=float("nan")).opacity == 1.0


def test_at_opacity_shares_the_payload_object():
    """That identity is what lets the paint path reuse its decoded pixmap."""
    original = _raster(opacity=1.0)
    faded = original.at_opacity(0.3)
    assert faded.opacity == pytest.approx(0.3)
    assert faded.image_bytes is original.image_bytes
    assert original.opacity == 1.0, "the original must be untouched"


@pytest.mark.parametrize("view,expected", [
    ((-100.0, -90.0, 35.0, 45.0), True),
    ((-180.0, 180.0, -90.0, 90.0), True),
    ((0.0, 20.0, 40.0, 60.0), False),
    ((-130.0, -60.0, 60.0, 70.0), False),
    (None, False),
    (("x", "y", 1, 2), False),
])
def test_intersects_lets_the_paint_path_skip_a_decode(view, expected):
    assert _raster().intersects(view) is expected


def test_age_prefers_the_observation_time_over_the_retrieval_time():
    now = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)
    both = _raster(
        valid_time=now - timedelta(minutes=10),
        retrieved_at=now - timedelta(minutes=1))
    assert both.age_seconds(now) == pytest.approx(600.0)

    retrieved_only = _raster(retrieved_at=now - timedelta(minutes=2))
    assert retrieved_only.age_seconds(now) == pytest.approx(120.0)


def test_an_unknown_or_naive_time_yields_no_age_rather_than_a_guess():
    """Matches OverlayLayer.covers: never invent a timezone."""
    now = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)
    assert _raster().age_seconds(now) is None
    assert _raster(retrieved_at=datetime(2026, 8, 30, 4, 0)).age_seconds(now) \
        is None


def test_staleness_allows_one_missed_cycle():
    """A warning that fires every few minutes teaches users to ignore it."""
    now = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)

    def at(minutes):
        return _raster(retrieved_at=now - timedelta(minutes=minutes),
                       update_interval_s=120.0)

    assert not at(2).is_stale(now), "one cycle late is normal operation"
    assert not at(4).is_stale(now)
    assert at(6).is_stale(now), "three cycles late is a real problem"


def test_staleness_is_never_claimed_without_the_facts_to_support_it():
    now = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)
    # No cadence declared, so there is nothing to be late against.
    assert not _raster(retrieved_at=now - timedelta(hours=6)).is_stale(now)
    # No timestamp, so the age is unknown.
    assert not _raster(update_interval_s=120.0).is_stale(now)


@pytest.mark.parametrize("seconds,expected", [
    (None, ""),
    (-5.0, "just now"),
    (0.0, "0 s old"),
    (45.0, "45 s old"),
    (200.0, "3 min old"),
    (3600.0, "60 min old"),
    (20000.0, "5.6 h old"),
])
def test_age_is_worded_coarsely(seconds, expected):
    """A seconds-precise counter under the cursor reads as a stopwatch."""
    assert mo.format_age(seconds) == expected


def test_a_raster_is_truthy_only_with_a_payload():
    raster = _raster()
    assert raster
    assert raster.byte_count == len(PNG)
