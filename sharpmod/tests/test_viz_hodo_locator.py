"""Tests for the hodograph sounding-location locator inset."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy.QtGui import QColor, QPixmap
from qtpy.QtWidgets import QApplication

from sharpmod.viz import hodo_locator


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_zoom_bounds_center_the_sounding_and_stay_local():
    lat, lon = 39.0319, -88.6713

    west, south, east, north = hodo_locator.zoom_bounds(lat, lon)

    assert west < lon < east
    assert south < lat < north
    assert east - west < 4.0
    assert north - south < 2.0


def test_point_from_widget_uses_collection_metadata_for_longitude():
    collection = SimpleNamespace(
        getMeta=lambda key: {"lat": 39.0319, "lon": -88.6713}.get(key),
    )
    widget = SimpleNamespace(
        prof=SimpleNamespace(latitude=39.0319, longitude=None),
        prof_collections=[collection],
        pc_idx=0,
    )

    assert hodo_locator.point_from_widget(widget) == pytest.approx((39.0319, -88.6713))


def test_global_boundaries_cover_international_point():
    bounds = hodo_locator.zoom_bounds(58.0, 57.25)

    layers = hodo_locator.global_lines_for_bounds(bounds)

    assert layers["states"]


def test_locator_draws_global_outline_when_counties_are_unavailable(
        monkeypatch, qt_app):
    monkeypatch.setattr(
        hodo_locator, "county_features_for_point", lambda _lat, _lon: ())
    monkeypatch.setattr(
        hodo_locator,
        "global_lines_for_bounds",
        lambda _bounds: {
            "coastline": (),
            "countries": (),
            "states": (((56.0, 57.65), (58.5, 57.65)),),
        },
    )
    pixmap = QPixmap(640, 480)
    pixmap.fill(QColor("black"))
    widget = SimpleNamespace(
        plotBitMap=pixmap,
        prof=SimpleNamespace(latitude=58.0, longitude=57.25),
        width=lambda: 640,
        height=lambda: 480,
    )

    assert hodo_locator.draw_hodo_locator(widget) is True

    image = pixmap.toImage()
    outline_pixels = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if (
                color.red() >= 30
                and color.green() - color.red() >= 8
                and color.blue() - color.red() >= 15
            ):
                outline_pixels += 1
    assert outline_pixels > 5


def test_locator_draws_county_outline_and_sounding_marker(monkeypatch, qt_app):
    def features(_lat, _lon):
        return [{
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-89.2, 38.7], [-88.1, 38.7], [-88.1, 39.4],
                    [-89.2, 39.4], [-89.2, 38.7],
                ]],
            },
        }]

    monkeypatch.setattr(hodo_locator, "county_features_for_point", features)
    pixmap = QPixmap(640, 480)
    pixmap.fill(QColor("black"))
    widget = SimpleNamespace(
        plotBitMap=pixmap,
        prof=SimpleNamespace(latitude=39.0319, longitude=-88.6713),
        width=lambda: 640,
        height=lambda: 480,
    )

    assert hodo_locator.draw_hodo_locator(widget) is True

    image = pixmap.toImage()
    marker_pixels = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() > 180 and color.green() > 140 and color.blue() < 90:
                marker_pixels += 1
    assert marker_pixels > 5
