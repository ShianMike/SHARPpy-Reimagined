"""The displayed mph scale and numeric NEXRAD colours must agree."""

import io
import json
from urllib.error import HTTPError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from sharpmod.providers import radar_site, radar_level3 as velocity

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)


def test_unpublished_cut_closes_the_http_response(monkeypatch):
    body = io.BytesIO(b"Not found")
    error = HTTPError(velocity.BASE, 404, "Not found", {}, body)

    def missing(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", missing)
    with pytest.raises(radar_site.RadarSiteError, match="HTTP 404"):
        radar_site._default_opener(velocity.BASE, 1, 1024)
    assert body.closed


def test_reference_colours_cover_signed_200_mph_and_keep_missing_values_transparent():
    assert velocity.STOPS[0] == (-200, "#ffdadc")
    assert velocity.STOPS[-1] == (200, "#2d0000")
    palette = velocity.palette()
    assert palette["units"] == "mph" and palette["display"] == "fixed"
    assert len(palette["stops"]) == 81
    values = np.ma.array(
        [-201, -200, 0, 200, 201, np.nan, 5], mask=[0, 0, 0, 0, 0, 0, 1]
    )
    rgba = velocity.colours(values)
    assert rgba[0].tolist() == rgba[1].tolist() == [255, 218, 220, 255]
    assert rgba[3].tolist() == rgba[4].tolist() == [45, 0, 0, 255]
    assert rgba[5, 3] == rgba[6, 3] == 0
    assert velocity.MPH_PER_MPS * 44.704 == pytest.approx(100)


def test_echo_top_height_flags_and_missing_codes_are_not_velocities(monkeypatch):
    from metpy.io.nexrad import DigitalEETMapper

    file = SimpleNamespace(
        header=SimpleNamespace(code=135),
        lat=0,
        lon=0,
        max_range=345,
        map_data=DigitalEETMapper(SimpleNamespace(thresholds=[127, 1, 2, 128])),
        sym_block=[
            [
                {
                    "start_az": [0],
                    "end_az": [1],
                    "first": 0,
                    "data": [[0, 1, 2, 42, 170, 128, 129, 52]],
                }
            ]
        ],
    )
    azimuth = np.full((8, 8), 0.5)
    distance = np.tile(np.arange(8) * 1000 + 500, (8, 1))
    monkeypatch.setattr(velocity, "_projection", lambda *_: (azimuth, distance))
    image = np.array(
        Image.open(io.BytesIO(velocity.render(file, (-1, 1, -1, 1), 8))).convert("RGBA")
    )
    assert not image[:, [0, 1, 5, 6], 3].any()
    expected = velocity.colours(np.array([0, 40, 40, 50]), "echo-tops")
    np.testing.assert_array_equal(image[0, [2, 3, 4, 7]], expected)
    calibration = json.loads(Image.open(io.BytesIO(velocity.render(file, (-1, 1, -1, 1), 8))).info["sharppy-radar"])
    assert calibration["values"][42] == calibration["values"][170] == 40
    assert 170 in calibration["lowerBounds"] and 42 not in calibration["lowerBounds"]
    assert calibration["folded"] is None and calibration["values"][128] is None
    palette = velocity.palette("echo-tops")
    assert palette["units"] == "kft" and palette["stops"] == velocity.ECHO_TOPS_STOPS


def test_echo_top_fetch_uses_product_135_without_an_elevation(monkeypatch):
    file = SimpleNamespace(
        siteID="TLX",
        header=SimpleNamespace(code=135),
        metadata={"vol_time": NOW - timedelta(minutes=5)},
    )
    monkeypatch.setattr("metpy.io.Level3File", lambda _: file)
    monkeypatch.setattr(velocity, "render", lambda *_: PNG)
    velocity._DIRECTORIES.clear()
    radar_site.clear_cache()
    calls = []

    def opener(url, *_):
        calls.append(url)
        return (
            b'<a href="sn.0123">scan</a>17-Oct-2026 11:56'
            if url.endswith("/")
            else b"nids"
        )

    frame = radar_site.fetch_frame(
        "echo-tops", site_id="KTLX", now=NOW, opener=opener, pixels=256
    )
    assert frame.title == "KTLX Enhanced Echo Tops" and frame.elevation_angle is None
    assert frame.valid_time == NOW - timedelta(minutes=5)
    assert frame.source_url.endswith("DS.135et/SI.ktlx/sn.0123")
    assert len(calls) == 2
    with pytest.raises(
        radar_site.RadarSiteError, match="does not have a selectable tilt"
    ):
        radar_site.fetch_frame("echo-tops", site_id="KTLX", tilt="1", opener=opener)
    radar_site.clear_cache()
    file.header.code = 99
    with pytest.raises(radar_site.RadarSiteError, match="does not match"):
        velocity.fetch_frame("KTLX", product="echo-tops", now=NOW, opener=opener)


def test_noaa_directory_accepts_only_numbered_local_scans():
    body = b"""<tr><td><a href="sn.0123">sn.0123</a></td><td>17-Oct-2026 11:58</td></tr>
    <tr><td><a href="https://evil.test/sn.0124">x</a></td><td>17-Oct-2026 11:59</td></tr>
    <tr><td><a href="sn.last">latest</a></td><td>17-Oct-2026 11:59</td></tr>"""
    assert velocity.scans(body) == ((NOW - timedelta(minutes=2), "sn.0123"),)
    with pytest.raises(radar_site.RadarSiteError, match="byte budget"):
        velocity.scans(bytes(256 * 1024 + 1))


class Mapper:
    units = "m/s"

    def __call__(self, data):
        return np.ma.array(np.full(data.shape, 44.704), mask=data < 2)


def test_render_uses_mph_and_preserves_missing_sectors_and_range_folding():
    file = SimpleNamespace(
        lat=0,
        lon=0,
        max_range=5,
        map_data=Mapper(),
        sym_block=[
            [
                {
                    "start_az": [0, 90, 180],
                    "end_az": [90, 180, 270],
                    "data": [[0, 1, 2, 2]] * 3,
                    "first": 0,
                }
            ]
        ],
    )
    image = np.array(
        Image.open(
            io.BytesIO(velocity.render(file, (-0.05, 0.05, -0.05, 0.05), 128))
        ).convert("RGBA")
    )
    assert image[64, 64, 3] == 0, "a no-data gate must not become a grey disk"
    assert image[32, 32, 3] == 0, "a missing northwest sector must not be filled"
    assert np.any(np.all(image == velocity.RANGE_FOLDED, axis=-1))
    expected = velocity.colours(np.array([100]))[0]
    assert np.any(np.all(image == expected, axis=-1))
    calibration = json.loads(Image.open(io.BytesIO(velocity.render(file, (-0.05, 0.05, -0.05, 0.05), 128))).info["sharppy-radar"])
    assert calibration["values"][2] == pytest.approx(100) and calibration["folded"] == 1


def test_fetch_validates_actual_product_site_and_scan_time(monkeypatch):
    from metpy.io import Level3File

    assert Level3File is not None
    file = SimpleNamespace(
        siteID="TLX",
        header=SimpleNamespace(code=99),
        metadata={"vol_time": NOW - timedelta(minutes=6), "el_angle": 0.5},
    )
    monkeypatch.setattr("metpy.io.Level3File", lambda _: file)
    monkeypatch.setattr(velocity, "render", lambda *_: PNG)
    velocity._DIRECTORIES.clear()
    radar_site.clear_cache()
    calls = []

    def opener(url, timeout, limit):
        calls.append(url)
        return (
            b'<a href="sn.0123">scan</a>17-Oct-2026 11:56'
            if url.endswith("/")
            else b"nids"
        )

    frame = velocity.fetch_frame(
        "KTLX", valid_time=NOW - timedelta(minutes=5), now=NOW, opener=opener
    )
    assert frame.valid_time == NOW - timedelta(minutes=6)
    assert frame.source_url.endswith("SI.ktlx/sn.0123")
    assert frame.image_bytes == PNG
    assert len(calls) == 2
    assert (
        velocity.fetch_frame(
            "KTLX", valid_time=NOW - timedelta(minutes=4), now=NOW, opener=opener
        )
        is frame
    )
    assert len(calls) == 2, "revisiting the same native scan must reuse its image"
    radar_site.clear_cache()
    file.siteID = "BAD"
    with pytest.raises(radar_site.RadarSiteError, match="does not match"):
        velocity.fetch_frame(
            "KTLX", valid_time=NOW - timedelta(minutes=5), now=NOW, opener=opener
        )
    with pytest.raises(radar_site.RadarSiteError, match="past 60 minutes"):
        velocity.fetch_frame(
            "KTLX", valid_time=NOW - timedelta(hours=2), now=NOW, opener=opener
        )


@pytest.mark.parametrize(
    "product,code,directory", [("reflectivity", 94, "p94r1"), ("velocity", 99, "p99v1")]
)
def test_tilt_specific_data_uses_actual_elevation_scan_time_and_separate_cache(
    monkeypatch, product, code, directory
):
    file = SimpleNamespace(
        siteID="TLX",
        header=SimpleNamespace(code=code),
        metadata={
            "vol_time": NOW - timedelta(minutes=8),
            "delta_time": 120,
            "el_angle": 1.3,
        },
    )
    monkeypatch.setattr("metpy.io.Level3File", lambda _: file)
    monkeypatch.setattr(velocity, "render", lambda *_: PNG)
    radar_site.clear_cache()
    velocity._DIRECTORIES.clear()
    calls = []

    def opener(url, *_):
        calls.append(url)
        return (
            b'<a href="sn.0123">scan</a>17-Oct-2026 11:56'
            if url.endswith("/")
            else b"nids"
        )

    scan = velocity.fetch_frame(
        "KTLX", product=product, tilt="1", now=NOW, opener=opener
    )
    assert f"DS.{directory}/" in scan.source_url
    assert scan.elevation_angle == 1.3 and scan.valid_time == NOW - timedelta(minutes=6)
    file.metadata["el_angle"] = 0.5
    base = velocity.fetch_frame(
        "KTLX", product=product, tilt="0", now=NOW, opener=opener
    )
    assert base is not scan and base.elevation_angle == 0.5 and len(calls) == 4
    radar_site.clear_cache()
    with pytest.raises(radar_site.RadarSiteError, match="does not match"):
        velocity.fetch_frame("KTLX", product=product, tilt="1", now=NOW, opener=opener)
    with pytest.raises(
        radar_site.RadarSiteError, match="unknown radar product or tilt"
    ):
        velocity.fetch_frame(
            "KTLX", product=product, tilt="../outside", now=NOW, opener=opener
        )


def test_reflectivity_uses_dbz_without_velocity_conversion_or_loss_of_masks():
    mapper = Mapper()
    mapper.units = "dBZ"
    file = SimpleNamespace(
        lat=0,
        lon=0,
        max_range=5,
        map_data=mapper,
        sym_block=[
            [{"start_az": [0], "end_az": [359], "data": [[0, 2, 2, 2]], "first": 0}]
        ],
    )
    image = np.array(
        Image.open(
            io.BytesIO(velocity.render(file, (-0.05, 0.05, -0.05, 0.05), 128))
        ).convert("RGBA")
    )
    expected = velocity.colours(np.array([44.704]), "reflectivity")[0]
    assert np.any(np.all(image == expected, axis=-1))
    assert image[64, 64, 3] == 0
    assert velocity.palette("reflectivity")["units"] == "dBZ"


def test_br_hires_preserves_breakpoints_ticks_and_transparency():
    values = [0, 34.5, 35, 62.5, 95, np.nan]
    assert velocity.colours(values, "reflectivity").tolist() == [
        [50, 50, 50, 255],
        [6, 104, 8, 255],
        [255, 255, 0, 255],
        [255, 163, 255, 255],
        [0, 0, 0, 255],
        [50, 50, 50, 0],
    ]
    palette = velocity.palette("reflectivity")
    assert len(palette["stops"]) == 18 and not palette["stepped"]
    assert palette["ticks"] == list(range(0, 96, 5))
    assert velocity.palette("mosaic")["stops"][0][0] == -27.5


@pytest.mark.parametrize("units", ["m/s", "dBZ"])
def test_indexed_png_preserves_every_native_gate_colour_and_alpha(monkeypatch, units):
    class NativeMapper:
        def __call__(self, data):
            return np.ma.array(data.astype(float) * 0.5 - 32, mask=data < 2)

    mapper = NativeMapper()
    mapper.units = units
    raw = np.arange(256, dtype=np.uint8).reshape(4, 64)
    file = SimpleNamespace(
        lat=0,
        lon=0,
        max_range=4,
        map_data=mapper,
        sym_block=[
            [
                {
                    "start_az": [0, 90, 180, 270],
                    "end_az": [90, 180, 270, 0],
                    "data": raw,
                    "first": 0,
                }
            ]
        ],
    )
    azimuth = np.repeat([45, 135, 225, 315], 16)[:, None] * np.ones((1, 64))
    distance = np.tile((np.arange(64) + 0.5) * 4000 / 64, (64, 1))
    monkeypatch.setattr(velocity, "_projection", lambda *_: (azimuth, distance))
    image = Image.open(io.BytesIO(velocity.render(file, (-1, 1, -1, 1), 64)))
    assert image.mode == "P" and image.size == (64, 64)
    expected = velocity.colours(
        mapper(raw) * (velocity.MPH_PER_MPS if units == "m/s" else 1),
        "velocity" if units == "m/s" else "reflectivity",
    )
    expected[raw == 0] = 0
    expected[raw == 1] = velocity.RANGE_FOLDED
    np.testing.assert_array_equal(
        np.array(image.convert("RGBA")), np.repeat(expected, 16, axis=0)
    )


def test_projection_reuses_one_bounded_lookup_with_submetre_wgs84_precision():
    from pyproj import Geod

    velocity._projection.cache_clear()
    bounds = (-103.0, -93.0, 31.0, 41.0)
    azimuth, distance = velocity._projection(36.0, -98.0, bounds, 65)
    cached = velocity._projection(36.0, -98.0, bounds, 65)
    assert cached[0] is azimuth and cached[1] is distance
    assert not azimuth.flags.writeable and not distance.flags.writeable
    assert azimuth.nbytes + distance.nbytes == 65 * 65 * 8
    assert velocity._projection.cache_info().maxsize == 1
    lon, lat = np.meshgrid(
        -103 + (np.arange(65) + 0.5) * 10 / 65, 41 - (np.arange(65) + 0.5) * 10 / 65
    )
    expected_angle, _, expected_range = Geod(ellps="WGS84").inv(
        np.full(lon.shape, -98.0), np.full(lat.shape, 36.0), lon, lat
    )
    assert (
        np.max(np.abs((azimuth.astype(float) - expected_angle + 180) % 360 - 180))
        < 0.00002
    )
    np.testing.assert_allclose(distance, expected_range, rtol=0, atol=0.04)
    moved = velocity._projection(37.0, -98.0, bounds, 65)
    assert moved[0] is not azimuth
    assert velocity._projection.cache_info().currsize == 1
    velocity._projection.cache_clear()
