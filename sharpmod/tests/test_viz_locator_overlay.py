"""Coverage for the map overlay drawn on the hodograph's locator inset.

The inset is painted from inside a vendored render pass that receives only the
widget, so the overlay has to travel with the sounding as collection metadata.
The invariant these tests protect is that the paint path stays entirely local: a
slow or unreachable SPC must never be able to stall a hodograph repaint.
"""

from __future__ import annotations

import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from qtpy import QtCore
from qtpy.QtGui import QColor, QPixmap

from sharpmod import map_overlays as mo
from sharpmod.viz import hodo_locator

UTC = timezone.utc
LAT, LON = 38.6, -88.9
VALID = datetime(2023, 3, 31, 21, tzinfo=UTC)

#: A box comfortably larger than the inset's ~1.4 degree view around the point.
AREA = [[-92.0, 36.0], [-86.0, 36.0], [-86.0, 41.0], [-92.0, 41.0],
        [-92.0, 36.0]]
#: Far from the point, to prove off-screen shapes are skipped.
ELSEWHERE = [[10.0, 10.0], [16.0, 10.0], [16.0, 16.0], [10.0, 16.0],
             [10.0, 10.0]]


def _shape(ring, *, label="MDT", stroke="#CC0000", fill="#E06666", rank=4,
           hatch=False):
    rings = mo.rings_from_geometry(
        {"type": "Polygon", "coordinates": [ring]})[0]
    return mo.OverlayShape(
        rings=rings, bounds=mo.bounds_of(rings), stroke=stroke, fill=fill,
        label=label, rank=rank, hatch=hatch)


def _layer(shapes=None, *, valid_from=None, valid_to=None):
    return mo.build_layer(
        "spc_outlook", "SPC convective outlook \u2014 Day 1",
        shapes if shapes is not None else [_shape(AREA)],
        valid_from=valid_from, valid_to=valid_to)


class FakeCollection:
    """Stand-in for the parts of ProfCollection the locator reads."""

    def __init__(self, valid=VALID, lat=LAT, lon=LON):
        self._meta = {"lat": lat, "lon": lon}
        self.valid = valid

    def getCurrentDate(self):  # noqa: N802 - upstream API
        return self.valid

    def getMeta(self, key, index=False):  # noqa: N802
        return self._meta[key]

    def setMeta(self, key, value):  # noqa: N802
        self._meta[key] = value


def _widget(collection, lat=LAT, lon=LON, size=(760, 520)):
    pixmap = QPixmap(*size)
    pixmap.fill(QColor("black"))
    widget = SimpleNamespace(
        plotBitMap=pixmap,
        prof=SimpleNamespace(latitude=lat, longitude=lon, date=None),
        prof_collections=[collection],
        pc_idx=0,
        width=lambda: size[0],
        height=lambda: size[1],
    )
    return widget, pixmap


def _inset_difference(first, second, widget) -> float:
    """Fraction of the inset's pixels that differ between two renders."""
    rect = hodo_locator._inset_rect(widget, QtCore)
    changed = 0
    total = 0
    for y in range(int(rect.top()), int(rect.bottom())):
        for x in range(int(rect.left()), int(rect.right())):
            total += 1
            if first.pixelColor(x, y) != second.pixelColor(x, y):
                changed += 1
    return changed / max(1, total)


@pytest.fixture
def baseline(qt_app):
    """The inset as drawn with no overlay attached."""
    widget, pixmap = _widget(FakeCollection())
    assert hodo_locator.draw_hodo_locator(widget) is True
    return pixmap.toImage(), widget


# --------------------------------------------------------------------------- #
# valid time
# --------------------------------------------------------------------------- #
def test_valid_time_comes_from_the_collection(qt_app):
    widget, _ = _widget(FakeCollection(valid=VALID))
    assert hodo_locator.valid_time_from_widget(widget) == VALID


def test_a_naive_valid_time_is_read_as_utc(qt_app):
    """Not every decoder attaches a timezone; the consumers reject naive input."""
    naive = datetime(2023, 3, 31, 21)
    widget, _ = _widget(FakeCollection(valid=naive))
    assert hodo_locator.valid_time_from_widget(widget) == VALID


def test_a_non_utc_valid_time_is_converted(qt_app):
    eastern = timezone(timedelta(hours=-5))
    widget, _ = _widget(FakeCollection(
        valid=datetime(2023, 3, 31, 16, tzinfo=eastern)))
    assert hodo_locator.valid_time_from_widget(widget) == VALID


def test_the_valid_time_falls_back_to_metadata(qt_app):
    collection = FakeCollection(valid=None)
    collection.setMeta("valid", VALID)
    widget, _ = _widget(collection)
    assert hodo_locator.valid_time_from_widget(widget) == VALID


def test_the_valid_time_falls_back_to_the_profile(qt_app):
    widget, _ = _widget(FakeCollection(valid=None))
    widget.prof.date = VALID
    assert hodo_locator.valid_time_from_widget(widget) == VALID


def test_no_valid_time_available(qt_app):
    widget, _ = _widget(FakeCollection(valid=None))
    assert hodo_locator.valid_time_from_widget(widget) is None


# --------------------------------------------------------------------------- #
# selecting layers
# --------------------------------------------------------------------------- #
def test_no_overlay_attached(qt_app):
    widget, _ = _widget(FakeCollection())
    assert hodo_locator.overlay_layers_for_widget(widget) == ()


def test_an_attached_layer_reaches_the_paint_path(qt_app):
    collection = FakeCollection()
    layer = _layer()
    mo.attach_locator_overlay(collection, layer)
    widget, _ = _widget(collection)
    assert hodo_locator.overlay_layers_for_widget(widget) == (layer,)


def test_a_layer_for_another_valid_time_is_filtered_out(qt_app):
    """The window can hold several soundings and switch focus between them."""
    collection = FakeCollection()
    mo.attach_locator_overlay(collection, _layer(
        valid_from=VALID - timedelta(hours=1),
        valid_to=VALID + timedelta(hours=1)))
    collection.valid = VALID + timedelta(days=4)
    widget, _ = _widget(collection)
    assert hodo_locator.overlay_layers_for_widget(widget) == ()


def test_a_widget_without_collections_is_survivable(qt_app):
    widget = SimpleNamespace(prof=SimpleNamespace(latitude=LAT, longitude=LON))
    assert hodo_locator.overlay_layers_for_widget(widget) == ()


# --------------------------------------------------------------------------- #
# drawing
# --------------------------------------------------------------------------- #
def test_the_risk_area_is_visible_in_the_inset(baseline):
    bare, _ = baseline
    collection = FakeCollection()
    mo.attach_locator_overlay(collection, _layer())
    widget, pixmap = _widget(collection)

    assert hodo_locator.draw_hodo_locator(widget) is True
    assert _inset_difference(bare, pixmap.toImage(), widget) > 0.05


def test_a_filtered_layer_leaves_the_inset_untouched(baseline):
    bare, _ = baseline
    collection = FakeCollection()
    mo.attach_locator_overlay(collection, _layer(
        valid_from=VALID - timedelta(hours=1),
        valid_to=VALID + timedelta(hours=1)))
    collection.valid = VALID + timedelta(days=4)
    widget, pixmap = _widget(collection)

    assert hodo_locator.draw_hodo_locator(widget) is True
    assert pixmap.toImage() == bare


def test_a_shape_outside_the_view_is_skipped(baseline):
    """An outlook spans the continent; the inset spans under two degrees."""
    bare, _ = baseline
    collection = FakeCollection()
    mo.attach_locator_overlay(
        collection, _layer([_shape(ELSEWHERE, label="")]))
    widget, pixmap = _widget(collection)

    assert hodo_locator.draw_hodo_locator(widget) is True
    assert pixmap.toImage() == bare


def test_a_hatched_shape_paints(baseline):
    bare, _ = baseline
    collection = FakeCollection()
    mo.attach_locator_overlay(collection, _layer([
        _shape(AREA, label="SIGN", stroke="#000000", fill="#888888",
               rank=10_000, hatch=True)]))
    widget, pixmap = _widget(collection)

    assert hodo_locator.draw_hodo_locator(widget) is True
    assert _inset_difference(bare, pixmap.toImage(), widget) > 0.01


def test_the_locator_still_draws_when_the_overlay_is_malformed(baseline):
    """An overlay is an embellishment and must never cost the locator."""
    bare, _ = baseline
    collection = FakeCollection()
    collection.setMeta(
        mo.LOCATOR_OVERLAY_META_KEY,
        (SimpleNamespace(shapes="not iterable properly", covers=lambda _w: True),))
    widget, pixmap = _widget(collection)

    assert hodo_locator.draw_hodo_locator(widget) is True
    assert pixmap.toImage() == bare


def test_the_paint_path_makes_no_network_calls(monkeypatch, qt_app):
    """The reason the layer is injected rather than fetched here."""
    def forbidden(*args, **kwargs):
        raise AssertionError("locator paint attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    collection = FakeCollection()
    mo.attach_locator_overlay(collection, _layer())
    widget, _ = _widget(collection)
    assert hodo_locator.draw_hodo_locator(widget) is True


# --------------------------------------------------------------------------- #
# the category badge
# --------------------------------------------------------------------------- #
def test_the_badge_names_the_category_at_the_point():
    """A flat wash cannot say which category; the inset is usually all one."""
    layer = _layer()
    assert hodo_locator.overlay_label_at_point((layer,), LAT, LON) == \
        ("MDT", "#CC0000", "#E06666")


def test_the_badge_reports_the_most_severe_category():
    inner = [[-90.0, 38.0], [-88.0, 38.0], [-88.0, 39.0], [-90.0, 39.0],
             [-90.0, 38.0]]
    layer = _layer([
        _shape(AREA, label="MRGL", stroke="#005500", fill="#66A366", rank=1),
        _shape(inner, label="HIGH", stroke="#CC00CC", fill="#EE99EE", rank=5),
    ])
    label = hodo_locator.overlay_label_at_point((layer,), LAT, LON)
    assert label[0] == "HIGH"


def test_no_badge_outside_every_area():
    layer = _layer()
    assert hodo_locator.overlay_label_at_point((layer,), 20.0, -160.0) is None


def test_no_badge_without_a_label():
    layer = _layer([_shape(AREA, label="")])
    assert hodo_locator.overlay_label_at_point((layer,), LAT, LON) is None


def test_the_badge_changes_the_inset(baseline):
    """Regression guard: the wash alone used to be the only signal."""
    bare, _ = baseline
    collection = FakeCollection()
    # A shape covering the point but only just inside the view, so the badge is
    # the dominant difference rather than a full-inset wash.
    mo.attach_locator_overlay(collection, _layer())
    widget, pixmap = _widget(collection)
    assert hodo_locator.draw_hodo_locator(widget) is True
    assert hodo_locator.overlay_label_at_point((_layer(),), LAT, LON) is not None
    assert pixmap.toImage() != bare


# --------------------------------------------------------------------------- #
# naming the hazard on the badge
# --------------------------------------------------------------------------- #
def _prob_layer(short="TOR", label="15%", *, with_qualifier=False):
    shapes = [_shape(AREA, label=label, stroke="#CC0000", fill="#E06666",
                     rank=150)]
    if with_qualifier:
        shapes.append(_shape(AREA, label="SIGN", stroke="#000000",
                             fill="#888888", rank=10_000, hatch=True))
    return mo.build_layer(
        "spc_outlook", "SPC convective outlook \u2014 Day 1 tornado probability",
        shapes, short_name=short)


def test_the_badge_names_the_hazard_for_a_probability():
    """A bare "15%" does not say whether it is tornado, wind, or hail."""
    label = hodo_locator.overlay_label_at_point((_prob_layer(),), LAT, LON)
    assert label[0] == "TOR 15%"


@pytest.mark.parametrize("short,expected", [
    ("TOR", "TOR 15%"), ("WIND", "WIND 15%"), ("HAIL", "HAIL 15%"),
])
def test_every_hazard_is_named(short, expected):
    label = hodo_locator.overlay_label_at_point(
        (_prob_layer(short=short),), LAT, LON)
    assert label[0] == expected


def test_a_categorical_badge_is_left_unprefixed():
    """"MDT" already says what it is."""
    layer = mo.build_layer(
        "spc_outlook", "T", [_shape(AREA, label="MDT")], short_name="")
    label = hodo_locator.overlay_label_at_point((layer,), LAT, LON)
    assert label[0] == "MDT"


def test_the_badge_reports_the_band_not_the_hatched_qualifier():
    """The qualifier outranks every band, so it would otherwise win the lookup.

    Reporting "SIGN" alone would drop the probability the point actually sits
    in, which is the number a forecaster is reading the inset for.
    """
    label = hodo_locator.overlay_label_at_point(
        (_prob_layer(with_qualifier=True),), LAT, LON)
    assert label[0] == "TOR 15% SIG"


def test_the_badge_omits_sig_where_it_does_not_apply():
    label = hodo_locator.overlay_label_at_point((_prob_layer(),), LAT, LON)
    assert "SIG" not in label[0]


def test_the_badge_uses_the_band_colour_not_the_hatch_colour(qt_app):
    """The band is the primary reading; the hatch is an annotation on it."""
    label = hodo_locator.overlay_label_at_point(
        (_prob_layer(with_qualifier=True),), LAT, LON)
    assert label[1] == "#CC0000"
    assert label[2] == "#E06666"
