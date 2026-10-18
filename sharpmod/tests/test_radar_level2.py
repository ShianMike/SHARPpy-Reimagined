"""Native reflectivity keeps gate geometry, honest scan times and one volume per download."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image

from sharpmod.providers import (
    radar_inventory,
    radar_level2 as radar,
    radar_level3,
    radar_site,
)

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
WHEN = NOW - timedelta(minutes=5)
PAYLOAD = b"AR2V0006" + bytes(56)
KEY = "2026/10/17/KTLX/KTLX20261017_115500_V06"


def listing(*scans):
    return (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated>"
        + "".join(
            f"<Contents><Key>{key}</Key><Size>{len(PAYLOAD)}</Size></Contents>"
            for key in scans
        )
        + "</ListBucketResult>"
    ).encode()


@pytest.fixture(autouse=True)
def session(tmp_path, monkeypatch):
    radar.clear_cache()
    radar_site.clear_cache()
    monkeypatch.setattr(radar, "disk_cache_root", lambda: tmp_path / "volumes")
    yield tmp_path / "volumes"
    radar.clear_cache()
    radar_site.clear_cache()


@pytest.fixture
def reader(monkeypatch):
    site = radar_site.site_by_id("KTLX")

    class Reader:
        stid = b"KTLX"
        dt = WHEN.replace(tzinfo=None)
        reads = 0

        def __init__(self, _):
            Reader.reads += 1
            self.vcp_info = NS(els=[NS(el_angle=0.5), NS(el_angle=1.3)])
            self.sweeps = []
            for angle in [0.5, 1.3]:
                rays = []
                for az in [0.25, 0.75]:
                    header = NS(
                        el_angle=angle + 0.3,
                        az_angle=az,
                        az_spacing=0.5,
                        date=(WHEN.date() - datetime(1970, 1, 1).date()).days + 1,
                        time_ms=(WHEN.hour * 3600 + WHEN.minute * 60) * 1000,
                    )
                    moment = NS(
                        num_gates=4,
                        first_gate=2.125,
                        gate_width=0.25,
                        scale=2.0,
                        offset=66.0,
                        data_size=8,
                    )
                    values = np.array([np.nan, self.RANGE_FOLD, -32.0, 35.0])
                    rays.append(
                        NS(
                            header=header,
                            vol_consts=NS(lat=site.lat, lon=site.lon),
                            moments={b"REF": (moment, values)},
                        )
                    )
                self.sweeps.append(rays)

    monkeypatch.setattr("metpy.io.Level2File", Reader)
    return Reader


def test_native_decode_uses_commanded_angles_and_preserves_missing_and_folded_gates(
    reader,
):
    cuts = radar._decode(PAYLOAD, radar_site.site_by_id("KTLX"), WHEN)
    assert set(cuts) == {("reflectivity", "0"), ("reflectivity", "1")}, (
        "individual ray wobble must not change the selected cut"
    )
    sweep = cuts["reflectivity", "0"]
    assert sweep.angle == 0.5 and sweep.when == WHEN
    assert sweep.gate_width == 250 and sweep.gate_start == 2000
    np.testing.assert_array_equal(sweep.raw, [[0, 1, 2, 136]] * 2)
    assert sweep.rgba[0].tolist() == [0, 0, 0, 0]
    assert sweep.rgba[1].tolist() == list(radar_level3.RANGE_FOLDED)
    assert sweep.rgba[136].tolist() == [255, 255, 0, 255]


def test_shared_renderer_honours_native_gate_edges_and_half_degree_radials(
    monkeypatch, reader
):
    sweep = radar._decode(PAYLOAD, radar_site.site_by_id("KTLX"), WHEN)[
        "reflectivity", "0"
    ]
    azimuth = np.repeat([0.25, 0.75, 1.25, 359.75], 4).reshape(4, 4)
    distance = np.tile([1999.0, 2375.0, 2625.0, 2875.0], (4, 1))
    monkeypatch.setattr(radar_level3, "_projection", lambda *_: (azimuth, distance))
    result = radar_level3.render_radials(
        sweep.raw,
        sweep.starts,
        sweep.ends,
        sweep.lat,
        sweep.lon,
        (-1, 1, -1, 1),
        4,
        sweep.rgba,
        gate_start=sweep.gate_start,
        gate_width=sweep.gate_width,
    )
    image = np.array(Image.open(io.BytesIO(result)).convert("RGBA"))
    assert not image[:, 0, 3].any(), "the unsampled centre must remain transparent"
    np.testing.assert_array_equal(image[:2, 1], [radar_level3.RANGE_FOLDED] * 2)
    np.testing.assert_array_equal(image[:2, 3], [[255, 255, 0, 255]] * 2)
    assert not image[2:, :, 3].any(), (
        "a missing radial must not be spread into adjacent sectors"
    )


def test_panels_tilts_inventory_and_disk_reuse_one_volume_download(reader, session):
    calls = []

    def opener(url, *_):
        calls.append(url)
        return listing(KEY) if "list-type" in url else PAYLOAD

    def fetch(tilt):
        return radar_site.fetch_frame(
            "reflectivity",
            site_id="KTLX",
            tilt=tilt,
            now=NOW,
            opener=opener,
            pixels=256,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        frames = list(pool.map(fetch, ["0", "1"]))
    assert [frame.elevation_angle for frame in frames] == [0.5, 1.3]
    assert all(
        frame.valid_time == WHEN and "Level II" in frame.attribution for frame in frames
    )
    assert all(frame.source_url == f"{radar.BASE}/{KEY}" for frame in frames)
    calibration = json.loads(Image.open(io.BytesIO(frames[0].image_bytes)).info["sharppy-radar"])
    assert calibration["values"][:3] == [None, None, -32.0]
    assert calibration["values"][136] == 35.0 and calibration["folded"] == 1
    assert sum(url.endswith("_V06") for url in calls) == reader.reads == 1
    tilts = radar_inventory.availability("KTLX", "reflectivity", now=NOW, opener=opener)
    assert [row["tilt"] for row in tilts["tilts"] if row["status"] == "available"] == [
        "0",
        "1",
    ]
    assert len(list(session.glob("*.ar2"))) == 1
    radar.clear_cache()
    radar_site.clear_cache()
    fetch("0")
    assert sum(url.endswith("_V06") for url in calls) == 1 and reader.reads == 2
    radar.clear_cache()
    radar_site.clear_cache()
    next(session.glob("*.ar2")).write_bytes(b"x" * len(PAYLOAD))
    fetch("0")
    assert sum(url.endswith("_V06") for url in calls) == 2, (
        "a corrupt retained volume must refetch"
    )


def test_listing_rejects_external_keys_bad_xml_and_incomplete_results():
    site = radar_site.site_by_id("KTLX")
    scans = radar._listing(
        site,
        NOW.date(),
        NOW - timedelta(minutes=70),
        lambda *_: listing(KEY, "https://evil.test/volume", "2026/10/17/KTLX/../saved"),
        1,
    )
    assert scans == ((WHEN, KEY, len(PAYLOAD)),)
    for body in [
        b"<html>Error</html>",
        b"broken",
        listing(KEY).replace(b"false", b"true"),
    ]:
        radar.clear_cache()
        with pytest.raises(radar_site.RadarSiteError, match="invalid volume listing"):
            radar._listing(site, NOW.date(), NOW, lambda *_: body, 1)


def test_history_selects_real_nearby_volumes_including_previous_utc_day():
    now = NOW.replace(hour=0, minute=20)
    past = "2026/10/16/KTLX/KTLX20261016_234500_V06"
    current = "2026/10/17/KTLX/KTLX20261017_001500_V06"
    calls = []

    def opener(url, *_):
        calls.append(url)
        return listing(past, current)

    selected = radar._select("KTLX", now - timedelta(minutes=35), now, opener)[2]
    assert selected[1] == past and len(calls) == 2
    assert radar._select("KTLX", None, now, opener)[2][1] == current
    assert radar._select("KTLX", now - timedelta(minutes=60), now, opener)[2] is None
    for when in [now - timedelta(hours=2), now + timedelta(minutes=2)]:
        with pytest.raises(radar_site.RadarSiteError, match="past 60 minutes"):
            radar._select("KTLX", when, now, lambda *_: pytest.fail("network"))


def test_wrong_site_and_time_are_rejected_without_retaining_the_volume(reader, session):
    reader.stid = b"PGUA"
    with pytest.raises(radar_site.RadarSiteError, match="site or time"):
        radar.fetch_frame(
            "KTLX",
            now=NOW,
            opener=lambda url, *_: listing(KEY) if "list-type" in url else PAYLOAD,
        )
    assert not list(session.glob("*.ar2"))
    reader.stid = b"KTLX"
    reader.dt = (WHEN - timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(ValueError, match="site or time"):
        radar._decode(PAYLOAD, radar_site.site_by_id("KTLX"), WHEN)


def test_a_future_sweep_cannot_be_displayed_as_a_current_frame(reader, monkeypatch):
    from dataclasses import replace

    sweep = radar._decode(PAYLOAD, radar_site.site_by_id("KTLX"), WHEN)[
        "reflectivity", "0"
    ]
    monkeypatch.setattr(
        radar,
        "_volume",
        lambda *_: {
            ("reflectivity", "0"): replace(sweep, when=NOW + timedelta(minutes=2))
        },
    )
    with pytest.raises(
        radar_site.RadarSiteError, match="outside the current radar window"
    ):
        radar.fetch_frame("KTLX", now=NOW, opener=lambda *_: listing(KEY))


def test_native_volume_cache_stays_within_its_byte_and_entry_budgets(
    session, monkeypatch
):
    monkeypatch.setattr(radar, "MAX_CACHE_ENTRIES", 2)
    monkeypatch.setattr(radar, "MAX_CACHE_BYTES", len(PAYLOAD) * 2)
    for i in range(4):
        radar._store(session / f"volume-{i}.ar2", PAYLOAD)
    assert len(list(session.glob("*.ar2"))) == 2
    assert (
        sum(path.stat().st_size for path in session.glob("*.ar2")) == len(PAYLOAD) * 2
    )
    assert not list(session.glob("*.part"))


def test_correlation_uses_its_own_calibration_geometry_and_cuts_in_one_shared_volume(
    reader, monkeypatch
):
    original = reader.__init__
    values = np.array([np.nan, (90 + 60.5) / 300, (240 + 60.5) / 300])

    def with_correlation(self, stream):
        original(self, stream)
        header = NS(
            num_gates=3,
            first_gate=1.125,
            gate_width=0.25,
            scale=300.0,
            offset=-60.5,
            data_size=8,
        )
        for ray in self.sweeps[0]:
            ray.moments[b"RHO"] = header, values

    monkeypatch.setattr(reader, "__init__", with_correlation)
    calls = []

    def opener(url, *_):
        calls.append(url)
        return listing(KEY) if "list-type" in url else PAYLOAD

    def fetch(product):
        return radar_site.fetch_frame(
            product, site_id="KTLX", pixels=256, now=NOW, opener=opener
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reflectivity, correlation = list(
            pool.map(fetch, ["reflectivity", "correlation-coefficient"])
        )
    assert "Base Reflectivity" in reflectivity.title
    assert "Correlation Coefficient" in correlation.title
    calibration = json.loads(Image.open(io.BytesIO(correlation.image_bytes)).info["sharppy-radar"])
    assert calibration["folded"] is None and calibration["values"][:2] == [None, None]
    assert reflectivity.source_url == correlation.source_url
    assert sum(url.endswith("_V06") for url in calls) == reader.reads == 1
    cuts = radar._volume(
        radar_site.site_by_id("KTLX"), (WHEN, KEY, len(PAYLOAD)), opener, 1
    )
    sweep = cuts["correlation-coefficient", "0"]
    assert sweep.gate_start == 1000 and sweep.gate_width == 250
    np.testing.assert_array_equal(sweep.raw, [[0, 90, 240]] * 2)
    assert sweep.rgba[0, 3] == sweep.rgba[1, 3] == 0
    expected = radar_level3.colours(values, "correlation-coefficient")
    np.testing.assert_array_equal(sweep.rgba[[90, 240]], expected[[1, 2]])
    inventory = radar_inventory.availability(
        "KTLX", "correlation-coefficient", now=NOW, opener=opener
    )
    assert inventory["product"] == "correlation-coefficient"
    assert [
        row["tilt"] for row in inventory["tilts"] if row["status"] == "available"
    ] == ["0"]
    with pytest.raises(
        radar_site.RadarSiteError,
        match="does not have this Correlation Coefficient tilt",
    ):
        radar.fetch_frame(
            "KTLX", product="correlation-coefficient", tilt="1", now=NOW, opener=opener
        )
    with pytest.raises(radar_site.RadarSiteError, match="unknown Level II product"):
        radar.fetch_frame("KTLX", product="invalid", now=NOW, opener=opener)
