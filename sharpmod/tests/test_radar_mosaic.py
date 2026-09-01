"""Tests for the live radar mosaic client.

No test here touches the network: every fetch goes through an injected opener,
and the paths that must not spend a request are asserted to need no opener at
all.

The facts these tests defend are the ones that are invisible when wrong. A
transposed bounding box still returns an image, and a WMS service error still
arrives as HTTP 200, so both would render as a plausible-looking map rather than
as a failure.
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone

import pytest

from sharpmod import radar_mosaic as rm

UTC = timezone.utc
NOW = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)

#: Smallest thing that satisfies the magic-byte gate. These tests never decode.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

SERVICE_EXCEPTION = (
    b'<?xml version="1.0"?><ServiceExceptionReport>'
    b'<ServiceException code="LayerNotDefined">\n'
    b"  Could not find layer conus_bogus\n"
    b"</ServiceException></ServiceExceptionReport>"
)


@pytest.fixture(autouse=True)
def _clean_cache():
    rm.clear_cache()
    yield
    rm.clear_cache()


def _opener(payload=PNG, record=None):
    """Return an injectable transport with the project's opener signature."""
    def opener(url, timeout, limit):
        if record is not None:
            record.append({"url": url, "timeout": timeout, "limit": limit})
        if isinstance(payload, Exception):
            raise payload
        return payload
    return opener


def _refuse(*_args, **_kwargs):
    raise AssertionError("this path must not reach the network")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def test_composite_reflectivity_is_the_default_product():
    """The deep-storm question is what a sounding is drawn to investigate."""
    assert rm.DEFAULT_PRODUCT == "composite-reflectivity"
    assert rm.get_product().layer == "conus_cref_qcd"


@pytest.mark.parametrize("key,layer", [
    ("composite-reflectivity", "conus_cref_qcd"),
    ("base-reflectivity", "conus_bref_qcd"),
    ("echo-tops", "conus_neet_v18"),
])
def test_every_product_names_its_advertised_layer(key, layer):
    """These strings were read from the live capabilities document."""
    assert rm.get_product(key).layer == layer


def test_an_unknown_product_is_a_key_error():
    with pytest.raises(KeyError):
        rm.get_product("stormscope-9000")


def test_get_product_accepts_an_already_resolved_product():
    """Lets callers pass either form without branching."""
    spec = rm.get_product("echo-tops")
    assert rm.get_product(spec) is spec


def test_available_products_are_all_resolvable():
    for spec in rm.available_products():
        assert rm.get_product(spec.key) is spec


def test_the_legend_url_asks_the_service_for_its_own_colour_ramp():
    """Drawing our own key risks showing a scale the pixels do not follow."""
    url = rm.get_product().legend_url
    assert url.startswith(rm.WMS_URL + "?")
    assert "request=GetLegendGraphic" in url
    assert "layer=conus_cref_qcd" in url


# --------------------------------------------------------------------------- #
# Request contract
# --------------------------------------------------------------------------- #
def test_the_request_uses_a_longitude_first_crs():
    """EPSG:4326 is latitude-first in WMS 1.3.0, and would silently transpose.

    The service advertises the same layer as ``minx=-130`` under CRS:84 and
    ``minx=20`` under EPSG:4326. Asking for the wrong one still returns an
    image, which is exactly why this is pinned.
    """
    query = rm.build_query()
    assert query["CRS"] == "CRS:84"
    assert rm.WMS_CRS == "CRS:84"

    lon0, lon1, lat0, lat1 = rm.COVERAGE_BOUNDS
    west, south, east, north = (
        float(value) for value in query["BBOX"].split(","))
    assert (west, south, east, north) == (lon0, lat0, lon1, lat1)
    # Longitudes are the negative pair; latitudes are the positive one. If the
    # order ever flips, these stop being true before anything renders.
    assert west < 0.0 and east < 0.0
    assert south > 0.0 and north > 0.0


def test_the_request_is_one_getmap_for_a_transparent_png():
    query = rm.build_query()
    assert query["SERVICE"] == "WMS"
    assert query["VERSION"] == "1.3.0"
    assert query["REQUEST"] == "GetMap"
    assert query["FORMAT"] == "image/png"
    assert query["TRANSPARENT"] == "TRUE"
    assert query["STYLES"] == "radar_reflectivity"


def test_no_time_parameter_is_sent():
    """Omitting TIME selects the newest frame in one request.

    Naming an explicit time would mean fetching the capabilities document first
    to learn which times exist, doubling the per-refresh request count.
    """
    assert "TIME" not in rm.build_query()


def test_the_frame_extent_is_fixed_rather_than_following_the_viewport():
    """One request then serves every map and every pan and zoom."""
    assert rm.build_query()["BBOX"] == rm.build_query()["BBOX"]
    width, height = rm.DEFAULT_FRAME_SIZE
    lon0, lon1, lat0, lat1 = rm.COVERAGE_BOUNDS
    # The extent is 70 by 35 degrees, so the frame must be 2:1 or the pixels
    # would not be square and the image would render stretched.
    assert (lon1 - lon0) / (lat1 - lat0) == pytest.approx(width / height)


def test_the_default_frame_is_fine_enough_to_read_when_zoomed_in():
    """The frame's resolution is the ceiling on how sharp the overlay can be.

    The first version asked for 2048 pixels of width, which works out at 3.8 km
    per pixel against MRMS's own 1 km grid, and read as visibly soft on screen
    as soon as the map was zoomed past a continental view.
    """
    width, height = rm.DEFAULT_FRAME_SIZE
    _lon0, _lon1, lat0, lat1 = rm.COVERAGE_BOUNDS
    km_per_pixel = (lat1 - lat0) * 111.32 / height
    assert km_per_pixel <= 2.0, \
        "%.2f km/px is too coarse to zoom into" % km_per_pixel
    assert width <= rm.MAX_FRAME_PIXELS
    assert height <= rm.MAX_FRAME_PIXELS


@pytest.mark.parametrize("size,expected", [
    (None, rm.DEFAULT_FRAME_SIZE),
    ((1024, 512), (1024, 512)),
    ((99999, 99999), (rm.MAX_FRAME_PIXELS, rm.MAX_FRAME_PIXELS)),
    ((1, 1), (rm.MIN_FRAME_PIXELS, rm.MIN_FRAME_PIXELS)),
])
def test_a_requested_frame_size_is_clamped(size, expected):
    """The upper bound is what stops a request asking a public service for
    something enormous."""
    query = rm.build_query(size=size)
    assert (int(query["WIDTH"]), int(query["HEIGHT"])) == expected


def test_a_nonsense_frame_size_is_refused():
    with pytest.raises(ValueError):
        rm.build_query(size=("wide", "tall"))


def test_the_url_is_the_endpoint_plus_the_query():
    url = rm.frame_url()
    assert url.startswith(rm.WMS_URL + "?")
    assert "LAYERS=conus_cref_qcd" in url
    assert "apikey" not in url.lower()


# --------------------------------------------------------------------------- #
# Coverage gate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("view,expected", [
    ((-100.0, -90.0, 35.0, 45.0), True),    # Kansas
    ((-125.0, -65.0, 25.0, 50.0), True),    # whole CONUS
    ((-180.0, 180.0, -90.0, 90.0), True),   # world view still contains CONUS
    ((0.0, 20.0, 40.0, 60.0), False),       # Europe
    ((100.0, 140.0, 20.0, 45.0), False),    # east Asia
    ((-60.0, -40.0, -30.0, -10.0), False),  # South America
    (None, False),
    (("bad", "values", 1, 2), False),
])
def test_coverage_is_tested_before_a_request_is_spent(view, expected):
    """MRMS is CONUS only and this application picks points worldwide."""
    assert rm.covers(view) is expected


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def test_one_frame_is_one_request():
    seen = []
    raster = rm.fetch_frame(opener=_opener(record=seen), now=NOW)
    assert len(seen) == 1
    assert raster.image_bytes == PNG
    assert raster.key == rm.OVERLAY_KEY
    assert raster.bounds == rm.COVERAGE_BOUNDS
    assert raster.retrieved_at == NOW
    assert raster.attribution == rm.ATTRIBUTION
    assert raster.update_interval_s == 120.0


def test_the_body_size_is_bounded():
    seen = []
    rm.fetch_frame(opener=_opener(record=seen), now=NOW)
    assert 0 < seen[0]["limit"] <= rm.MAX_FRAME_BYTES


def test_a_service_exception_at_http_200_is_refused():
    """GeoServer reports failure as XML with a success status.

    Trusting the status code here would hand an XML document to the paint path
    as though it were an image.
    """
    with pytest.raises(rm.RadarError) as excinfo:
        rm.fetch_frame(opener=_opener(SERVICE_EXCEPTION), now=NOW)
    message = str(excinfo.value)
    assert "returned no image" in message
    # The service's own explanation is lifted out, not the raw XML around it.
    assert "Could not find layer conus_bogus" in message
    assert "<ServiceException" not in message


def test_a_body_that_is_not_an_image_is_refused_even_without_an_explanation():
    with pytest.raises(rm.RadarError):
        rm.fetch_frame(opener=_opener(b"totally not a picture"), now=NOW)


def test_an_http_error_is_reported_with_its_status():
    error = urllib.error.HTTPError(
        rm.WMS_URL, 503, "Service Unavailable", {}, None)
    with pytest.raises(rm.RadarError) as excinfo:
        rm.fetch_frame(opener=_opener(error), now=NOW)
    assert "503" in str(excinfo.value)


def test_a_transport_error_is_reported_rather_than_raised_raw():
    error = urllib.error.URLError("name resolution failed")
    with pytest.raises(rm.RadarError) as excinfo:
        rm.fetch_frame(opener=_opener(error), now=NOW)
    assert "name resolution failed" in str(excinfo.value)


def test_cancellation_answers_none_and_spends_nothing():
    """Turning the overlay off mid-flight is not an error to report."""
    assert rm.fetch_frame(
        opener=_refuse, now=NOW, should_cancel=lambda: True) is None


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_a_second_fetch_reuses_the_frame():
    """Four maps enabling the overlay together must cost one request."""
    seen = []
    first = rm.fetch_frame(opener=_opener(record=seen), now=NOW)
    second = rm.fetch_frame(opener=_refuse, now=NOW)
    assert len(seen) == 1
    assert second is first


def test_the_cache_key_excludes_the_viewport():
    """Frames are requested at a fixed extent, so panning must not miss."""
    seen = []
    rm.fetch_frame(opener=_opener(record=seen), now=NOW)
    assert rm.cached_frame() is not None
    assert len(seen) == 1


def test_a_differing_opacity_rewraps_rather_than_refetches():
    seen = []
    first = rm.fetch_frame(opener=_opener(record=seen), now=NOW, opacity=1.0)
    faded = rm.fetch_frame(opener=_refuse, now=NOW, opacity=0.4)
    assert len(seen) == 1
    assert faded.opacity == pytest.approx(0.4)
    # The payload object itself is shared, which is what lets the paint path
    # keep its decoded pixmap across an opacity change.
    assert faded.image_bytes is first.image_bytes


def test_different_products_do_not_share_a_cache_entry():
    seen = []
    opener = _opener(record=seen)
    rm.fetch_frame("composite-reflectivity", opener=opener, now=NOW)
    rm.fetch_frame("echo-tops", opener=opener, now=NOW)
    assert len(seen) == 2
    assert "conus_cref_qcd" in seen[0]["url"]
    assert "conus_neet_v18" in seen[1]["url"]


def test_a_failure_is_remembered_so_a_refresh_timer_is_not_a_retry_loop():
    """The refresh timer fires unattended; without this it would hammer a
    struggling service."""
    seen = []
    with pytest.raises(rm.RadarError):
        rm.fetch_frame(opener=_opener(SERVICE_EXCEPTION, record=seen), now=NOW)
    with pytest.raises(rm.RadarError) as excinfo:
        rm.fetch_frame(opener=_refuse, now=NOW)
    assert len(seen) == 1, "the second attempt must not reach the network"
    assert "not retrying yet" in str(excinfo.value)


def test_clearing_the_cache_allows_a_refetch():
    seen = []
    opener = _opener(record=seen)
    rm.fetch_frame(opener=opener, now=NOW)
    rm.clear_cache()
    rm.fetch_frame(opener=opener, now=NOW)
    assert len(seen) == 2


def test_cached_frame_reports_nothing_before_a_fetch():
    assert rm.cached_frame() is None


def test_the_cache_cannot_grow_without_bound():
    assert rm._CACHE_MAX_ENTRIES > 0
    for index in range(rm._CACHE_MAX_ENTRIES * 3):
        rm._cache_put(("synthetic", index), None, 60.0)
    assert len(rm._CACHE) <= rm._CACHE_MAX_ENTRIES


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_the_frame_records_where_it_came_from():
    raster = rm.fetch_frame(opener=_opener(), now=NOW)
    assert raster.source_url.startswith(rm.WMS_URL)
    assert "NOAA" in raster.attribution
    assert raster.subtitle
    assert "reflectivity" in raster.title.lower()


def test_no_credential_appears_anywhere_in_the_request():
    """This service needs no key, so nothing may look like one."""
    blob = rm.frame_url().lower()
    for leaked in ("apikey", "api_key", "token", "secret", "password"):
        assert leaked not in blob


def test_the_refresh_cadence_matches_the_published_update_interval():
    """Polling faster than the source publishes buys nothing."""
    for spec in rm.available_products():
        assert spec.update_interval_s >= 60.0
    assert rm.FRAME_CACHE_TTL_S < rm.get_product().update_interval_s, \
        "a frame must not be held across a full publication cycle"
