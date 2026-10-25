"""Historical radar must use an advertised scan, never a relabelled latest PNG."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from sharpmod.bridge import map_layers, params, public
from sharpmod.bridge.errors import BridgeError
from sharpmod.maps.map_overlays import OverlayRaster
from sharpmod.providers import radar_mosaic, radar_site, radar_time

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
REQUESTED = NOW - timedelta(minutes=20)
SELECTED = NOW - timedelta(minutes=21, seconds=11)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def capabilities(layer, values):
    return (
        '<WMS_Capabilities xmlns="http://www.opengis.net/wms"><Capability><Layer>'
        '<Dimension name="time" units="ISO8601" nearestValue="1">'
        f"{values}</Dimension><Layer><Name>{layer}</Name></Layer>"
        "</Layer></Capability></WMS_Capabilities>"
    ).encode()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(map_layers, "_legend", lambda _: None)
    for provider in (radar_time, radar_mosaic, radar_site):
        provider.clear_cache()
    yield
    for provider in (radar_time, radar_mosaic, radar_site):
        provider.clear_cache()


@pytest.mark.parametrize("single_site", [False, True])
def test_timed_provider_uses_exact_advertised_time_and_keeps_native_detail(single_site):
    calls = []
    layer = "ktlx_boha" if single_site else "conus_cref_qcd"
    scans = f"{radar_time.stamp(NOW - timedelta(minutes=70))},{radar_time.stamp(SELECTED)},{radar_time.stamp(NOW - timedelta(minutes=1))}"

    def opener(url, timeout, limit):
        query = {
            key.lower(): value[0]
            for key, value in parse_qs(urlparse(url).query).items()
        }
        calls.append(query)
        return (
            capabilities(layer, scans) if query["request"] == "GetCapabilities" else PNG
        )

    if single_site:
        fetch = lambda: radar_site.fetch_frame(
            "one-hour", site_id="KTLX", valid_time=REQUESTED, now=NOW, opener=opener
        )
    else:
        fetch = lambda: radar_mosaic.fetch_frame(
            valid_time=REQUESTED, now=NOW, opener=opener
        )
    raster = fetch()
    assert raster.valid_time == SELECTED
    assert raster.retrieved_at == NOW
    assert raster.image_bytes == PNG
    assert calls[-1]["time"] == radar_time.stamp(SELECTED)
    assert calls[-1]["crs"] == "CRS:84"
    assert (int(calls[-1]["width"]), int(calls[-1]["height"])) == (
        (4096, 4096) if single_site else (8192, 4096)
    )
    assert fetch().image_bytes == PNG
    assert len(calls) == 2, "an identical scan must reuse capabilities and image bytes"


@pytest.mark.parametrize("single_site", [False, True])
@pytest.mark.parametrize(
    "times",
    ["", "2026-10-17T10:00:00Z", "2026-10-17T10:00:00Z/2026-10-17T12:00:00Z/PT2M"],
)
def test_missing_history_never_falls_back_to_latest(single_site, times):
    calls = []
    layer = "ktlx_boha" if single_site else "conus_cref_qcd"

    def opener(url, timeout, limit):
        calls.append(url)
        assert "GetCapabilities" in url, "no GetMap is allowed without an actual scan"
        return capabilities(layer, times)

    with pytest.raises(
        (radar_site.RadarScanMissing, radar_mosaic.RadarScanMissing),
        match="no historical scans",
    ):
        if single_site:
            radar_site.fetch_frame(
                "one-hour",
                site_id="KTLX",
                valid_time=REQUESTED,
                now=NOW,
                opener=opener,
            )
        else:
            radar_mosaic.fetch_frame(valid_time=REQUESTED, now=NOW, opener=opener)
    assert len(calls) == 1


def test_scan_selection_rejects_gaps_future_and_out_of_range_requests():
    def opener(*_):
        return capabilities(
            "radar", "2026-10-17T11:05:00Z,2026-10-17T11:59:00Z,2026-10-17T12:01:00Z"
        )

    with pytest.raises(radar_time.RadarTimeError, match="within 10 minutes"):
        radar_time.select_time(
            "https://example.test/wms",
            "radar",
            REQUESTED,
            now=NOW,
            opener=opener,
            timeout=1,
        )
    for requested in (NOW - timedelta(hours=2), NOW + timedelta(minutes=2)):
        with pytest.raises(radar_time.RadarTimeError, match="past 60 minutes"):
            radar_time.select_time(
                "https://example.test/wms",
                "radar",
                requested,
                now=NOW,
                opener=opener,
                timeout=1,
            )
    selected = radar_time.select_time(
        "https://example.test/wms", "radar", NOW, now=NOW, opener=opener, timeout=1
    )
    assert selected == NOW - timedelta(minutes=1)


def test_capabilities_inheritance_namespaces_and_nearest_ties_are_honest():
    body = capabilities(
        "conus:radar", "2026-10-17T11:39:00Z,2026-10-17T11:41:00Z,not-a-date"
    )
    assert len(radar_time.parse_capabilities(body)["radar"]) == 2
    selected = radar_time.select_time(
        "https://example.test/wms",
        "radar",
        REQUESTED,
        now=NOW,
        opener=lambda *_: body,
        timeout=1,
    )
    assert selected == REQUESTED - timedelta(minutes=1)
    with pytest.raises(radar_time.RadarTimeError, match="safety"):
        radar_time.parse_capabilities(b'<!DOCTYPE wms [<!ENTITY x "x">]><wms/>')
    with pytest.raises(radar_time.RadarTimeError, match="invalid capabilities"):
        radar_time.parse_capabilities(b"not XML")


def test_sixty_minute_target_can_wait_in_the_preload_queue():
    selected = NOW - timedelta(minutes=59, seconds=11)
    body = capabilities("radar", radar_time.stamp(selected))
    actual = radar_time.select_time(
        "https://example.test/wms",
        "radar",
        NOW - timedelta(minutes=62),
        now=NOW,
        opener=lambda *_: body,
        timeout=1,
    )
    assert actual == selected
    assert NOW - actual < timedelta(minutes=60)


def test_current_view_can_show_a_late_scan_without_inventing_history():
    selected = NOW - timedelta(minutes=25)
    body = capabilities("radar", radar_time.stamp(selected))
    assert (
        radar_time.select_time(
            "https://example.test/wms",
            "radar",
            NOW - timedelta(seconds=45),
            now=NOW,
            opener=lambda *_: body,
            timeout=1,
        )
        == selected
    )
    with pytest.raises(radar_time.RadarTimeError, match="within 10 minutes"):
        radar_time.select_time(
            "https://example.test/wms",
            "radar",
            NOW - timedelta(minutes=10),
            now=NOW,
            opener=lambda *_: body,
            timeout=1,
        )


@pytest.mark.parametrize(
    "handler,provider,arguments",
    [
        (map_layers.radar_mosaic, radar_mosaic, {}),
        (map_layers.radar_site, radar_site, {"site_id": "KTLX"}),
    ],
)
def test_bridge_rejects_a_provider_frame_without_actual_time(
    monkeypatch, handler, provider, arguments
):
    raster = OverlayRaster(
        key="radar", title="Radar", image_bytes=PNG, bounds=(-100, -90, 30, 40)
    )
    monkeypatch.setattr(provider, "fetch_frame", lambda *_args, **_kwargs: raster)
    with pytest.raises(BridgeError, match="actual time"):
        handler({**arguments, "valid_time": params.iso(REQUESTED)})
    assert handler(arguments)["valid_time"] is None


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize(
    "handler,provider,arguments",
    [
        (map_layers.radar_mosaic, radar_mosaic, {}),
        (map_layers.radar_site, radar_site, {"site_id": "KTLX"}),
    ],
)
def test_bridge_distinguishes_a_confirmed_gap_from_a_loading_failure(
    monkeypatch, missing, handler, provider, arguments
):
    error_type = provider.RadarScanMissing if missing else (
        provider.RadarSiteError if provider is radar_site else provider.RadarError
    )

    def fetch(*_args, **_kwargs):
        raise error_type("provider result")

    monkeypatch.setattr(provider, "fetch_frame", fetch)
    with pytest.raises(BridgeError) as error:
        handler(arguments)
    assert error.value.details == ({"reason": "scanMissing"} if missing else None)
    assert error.value.code == "OVERLAY_FETCH_FAILED"
    assert error.value.retryable


@pytest.mark.parametrize("payload", [b"<html>Service unavailable</html>", b"<ServiceExceptionReport/>"])
def test_service_error_documents_are_not_confirmed_scan_gaps(payload):
    with pytest.raises(radar_time.RadarTimeError, match="invalid capabilities") as error:
        radar_time.parse_capabilities(payload)
    assert not isinstance(error.value, radar_time.RadarScanMissing)


@pytest.mark.parametrize(
    "command,handler,provider,arguments",
    [
        (
            "get_radar_frame",
            map_layers.radar_mosaic,
            radar_mosaic,
            {"product": "composite-reflectivity"},
        ),
        (
            "get_radar_site_frame",
            map_layers.radar_site,
            radar_site,
            {"product": "one-hour", "site": "KTLX"},
        ),
    ],
)
def test_bridge_maps_camel_case_time_and_returns_actual_scan(
    monkeypatch, command, handler, provider, arguments
):
    monkeypatch.setattr(params, "now", lambda: NOW)
    calls = []
    original = provider.fetch_frame
    layer = "conus_cref_qcd" if provider is radar_mosaic else "ktlx_boha"

    def opener(url, *_):
        return (
            capabilities(layer, radar_time.stamp(SELECTED))
            if "GetCapabilities" in url
            else PNG
        )

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs, now=NOW, opener=opener)

    monkeypatch.setattr(provider, "fetch_frame", fetch)
    _, bridge_params = public.bridge_request(
        command, {**arguments, "validTime": params.iso(REQUESTED)}
    )
    result = handler(bridge_params)
    assert calls[0]["valid_time"] == REQUESTED
    assert result["valid_time"] == params.iso(SELECTED)
    assert "time=" in result["source_url"].lower()
    with pytest.raises(BridgeError) as error:
        handler({**bridge_params, "valid_time": "garbage"})
    assert error.value.code == "INVALID_ARGUMENT"
