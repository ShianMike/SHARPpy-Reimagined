"""Tests for the hodograph sounding-location locator inset."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import urllib.request
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtpy import QtCore
from qtpy.QtGui import QColor, QPixmap

from sharpmod import colors
from sharpmod.viz import hodo_locator


def test_zoom_bounds_center_the_sounding_and_stay_local():
    lat, lon = 39.0319, -88.6713

    west, south, east, north = hodo_locator.zoom_bounds(lat, lon)

    assert west < lon < east
    assert south < lat < north
    assert east - west < 4.0
    assert north - south < 2.0


def test_the_zoom_constant_sits_just_inside_the_locality_contract():
    """The inset's zoom is one constant, and it is nearly at its ceiling.

    ``LOCATOR_HALF_LAT_DEGREES`` was raised from 0.70 to 0.98 to give a sounding
    more recognisable surroundings. The test above contracts the inset to stay
    local at under two degrees of latitude, which leaves 0.02 degrees of room:
    raising the constant any further is a decision to widen that contract, not a
    tweak, so it is stated here rather than discovered as a failure elsewhere.
    """
    half = hodo_locator.LOCATOR_HALF_LAT_DEGREES
    assert half * 2.0 < 2.0, "the zoom has outgrown the locality contract"
    assert half > 0.70, "the zoom-out was reverted without updating this test"


@pytest.mark.parametrize("lat", [0.0, 25.0, 41.79, 58.0, 72.0, -45.0])
def test_the_extent_stays_local_in_kilometres_at_every_latitude(lat):
    """Degrees of longitude are a poor proxy for how much ground is shown.

    The cosine division is what keeps the inset near-square on the ground, so
    the longitude span in *degrees* grows toward the poles by design. What must
    stay bounded is the distance covered, including at the cosine clamp where the
    span in degrees is at its widest.
    """
    west, south, east, north = hodo_locator.zoom_bounds(lat, 0.0)

    tall_km = (north - south) * 111.32
    cos_lat = max(0.35, math.cos(math.radians(lat)))
    wide_km = (east - west) * 111.32 * cos_lat

    assert 200.0 < tall_km < 240.0
    # Landscape, matching the inset's own shape, and never a regional map.
    assert tall_km < wide_km < 340.0
    assert wide_km / tall_km == pytest.approx(
        hodo_locator.LOCATOR_LON_ASPECT, rel=1e-6)


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


def test_location_name_uses_active_collection_town_label():
    collection = SimpleNamespace(
        getMeta=lambda key: {
            "loc": "  Norman,   Oklahoma  ",
            "lat": 35.22,
            "lon": -97.44,
        }.get(key),
    )
    widget = SimpleNamespace(
        prof=SimpleNamespace(latitude=35.22, longitude=-97.44),
        prof_collections=[collection],
        pc_idx=0,
    )

    assert hodo_locator.location_name_from_widget(widget) == \
        "Norman, Oklahoma"


def test_coordinate_only_legacy_location_falls_back_to_model_name():
    collection = SimpleNamespace(
        getMeta=lambda key: {
            "loc": "HRRR 45.76N 91.60W",
            "model": "HRRR",
            "lat": 45.76,
            "lon": -91.60,
        }.get(key),
    )
    widget = SimpleNamespace(
        prof=SimpleNamespace(latitude=45.76, longitude=-91.60),
        prof_collections=[collection],
        pc_idx=0,
    )

    assert hodo_locator.location_name_from_widget(widget) == "HRRR"


def test_global_boundaries_cover_international_point():
    bounds = hodo_locator.zoom_bounds(58.0, 57.25)

    layers = hodo_locator.global_lines_for_bounds(bounds)

    assert layers["states"]


def test_locator_has_no_surrounding_town_label_layer():
    assert not hasattr(hodo_locator, "towns_for_bounds")
    assert not hasattr(hodo_locator, "_draw_town_labels")


def test_production_locator_never_fetches_counties_during_paint(monkeypatch):
    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("locator paint attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_network)

    assert hodo_locator.county_features_for_point(
        39.0319, -88.6713) == ()
    assert hodo_locator.county_lines_for_bounds(
        hodo_locator.zoom_bounds(36.68, -95.66))


def test_bundled_counties_supply_context_for_interior_conus_point():
    bounds = hodo_locator.zoom_bounds(36.68, -95.66)

    lines = hodo_locator.county_lines_for_bounds(bounds)

    assert len(lines) >= 10
    assert sum(len(line) for line in lines) >= 100
    assert any(
        bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]
        for line in lines
        for lon, lat in line
    )


def test_county_lookup_decodes_only_nearby_one_degree_tiles():
    hodo_locator._county_tile_lines_cached.cache_clear()
    bounds = hodo_locator.zoom_bounds(36.68, -95.66)

    assert hodo_locator.county_lines_for_bounds(bounds)

    cache = hodo_locator._county_tile_lines_cached.cache_info()
    expected_maximum = (
        (int(bounds[2] // 1) - int(bounds[0] // 1) + 1)
        * (int(bounds[3] // 1) - int(bounds[1] // 1) + 1)
    )
    assert cache.misses == expected_maximum
    assert cache.currsize == expected_maximum
    assert cache.currsize < 20


def test_warm_county_lookup_performs_no_more_archive_io(monkeypatch):
    hodo_locator._county_tile_lines_cached.cache_clear()
    bounds = hodo_locator.zoom_bounds(36.68, -95.66)
    expected = hodo_locator.county_lines_for_bounds(bounds)
    assert expected

    def unexpected_archive_io():
        raise AssertionError("warm locator lookup reopened county archive")

    monkeypatch.setattr(
        hodo_locator, "_county_archive_cached", unexpected_archive_io)

    assert hodo_locator.county_lines_for_bounds(bounds) == expected


def test_national_county_query_is_rejected_before_decoding_tiles():
    hodo_locator._county_tile_lines_cached.cache_clear()

    assert hodo_locator.county_lines_for_bounds(
        (-125.0, 24.0, -66.0, 50.0)) == ()
    assert hodo_locator._county_tile_lines_cached.cache_info().currsize == 0


def test_county_archive_records_provenance_hash_and_contains_no_labels():
    archive_resource = files("sharpmod.resources").joinpath(
        "conus-counties.zip")
    metadata_resource = files("sharpmod.resources").joinpath(
        "conus-counties.metadata.json")
    archive_payload = archive_resource.read_bytes()
    metadata = json.loads(metadata_resource.read_text(encoding="utf-8"))

    assert metadata["source"] == (
        "U.S. Census Bureau County Cartographic Boundary File")
    assert metadata["source_scale"] == "1:500,000"
    assert metadata["source_year"] == 2025
    assert metadata["county_count"] >= 3_000
    assert set(metadata["state_fips"]) == {
        "01", "04", "05", "06", "08", "09", "10", "11", "12", "13",
        "16", "17", "18", "19", "20", "21", "22", "23", "24", "25",
        "26", "27", "28", "29", "30", "31", "32", "33", "34", "35",
        "36", "37", "38", "39", "40", "41", "42", "44", "45", "46",
        "47", "48", "49", "50", "51", "53", "54", "55", "56",
    }
    assert not {"02", "15", "72"} & set(metadata["state_fips"])
    assert metadata["archive_sha256"] == hashlib.sha256(
        archive_payload).hexdigest()

    with zipfile.ZipFile(archive_resource) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        tile_name = next(
            name for name in archive.namelist() if name.startswith("tiles/"))
        tile_text = archive.read(tile_name).decode("ascii")
    assert manifest["source_sha256"] == metadata["source_sha256"]
    assert not any(character.isalpha() for character in tile_text)


def test_county_archive_is_declared_for_wheels_and_pyinstaller():
    root = Path(__file__).resolve().parents[2]
    import tomllib

    with (root / "pyproject.toml").open("rb") as source:
        manifest = tomllib.load(source)
    package_data = manifest["tool"]["setuptools"]["package-data"][
        "sharpmod.resources"]
    spec = (root / "packaging" / "sharpmod_gui.spec").read_text(
        encoding="utf-8")

    assert "*.zip" in package_data
    assert 'os.path.join(_RES, "*.zip")' in spec


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


def test_locator_draws_an_inverted_map_surface_and_accessible_marker(
        monkeypatch, qt_app):
    monkeypatch.setattr(
        hodo_locator, "county_features_for_point", lambda _lat, _lon: ())
    monkeypatch.setattr(
        hodo_locator,
        "global_lines_for_bounds",
        lambda _bounds: {name: () for name in ("coastline", "countries", "states")},
    )
    pixmap = QPixmap(640, 480)
    pixmap.fill(QColor("magenta"))
    widget = SimpleNamespace(
        plotBitMap=pixmap,
        prof=SimpleNamespace(latitude=39.0319, longitude=-88.6713),
        bg_color=QColor("#ffffff"),
        fg_color=QColor("#000000"),
        width=lambda: 640,
        height=lambda: 480,
    )

    assert hodo_locator.draw_hodo_locator(widget) is True

    image = pixmap.toImage()
    rect = hodo_locator._inset_rect(widget, QtCore)
    fill = image.pixelColor(int(rect.left()) + 10, int(rect.top()) + 10)
    assert fill.name().lower() == "#ffffff"

    marker = colors.semantic_palette(
        "#ffffff", "#000000")["marker_yellow"]
    marker_pixels = sum(
        image.pixelColor(x, y).name().lower() == marker
        for y in range(image.height())
        for x in range(image.width())
    )
    assert marker_pixels > 0
    assert colors.contrast_ratio(marker, "#ffffff") >= 4.5
