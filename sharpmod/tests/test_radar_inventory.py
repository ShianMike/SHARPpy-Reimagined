"""Every site uses published scan directories, never trial image downloads."""

from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest

from sharpmod.providers import radar_inventory as inventory, radar_level3 as radar, radar_site

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_directories():
    radar._DIRECTORIES.clear()
    radar._PUBLISHED.clear()
    yield
    radar._DIRECTORIES.clear()
    radar._PUBLISHED.clear()


def listing(when):
    return f'<a href="sn.0123">scan</a>{when:%d-%b-%Y %H:%M}'.encode()


def statuses(result):
    return {item["tilt"]: item["status"] for item in result["tilts"]}


@pytest.mark.parametrize("site", ["KTLX", "PGUA", "PAHG", "TJUA"])
@pytest.mark.parametrize("product,family", [("velocity", "p99v")])
def test_checks_each_site_product_cut_with_small_cached_listings(site, product, family):
    calls = []

    def opener(url, timeout, limit):
        calls.append(url)
        assert url.endswith("/") and f"DS.{family}" in url
        assert f"SI.{site.lower()}/" in url and limit == 256 * 1024 and timeout <= 8
        if f"DS.{family}z/" in url:
            raise radar_site.RadarSiteError("missing") from HTTPError(url, 404, "missing", {}, None)
        return listing(NOW - timedelta(minutes=2 if f"DS.{family}0/" in url else 80))

    first = inventory.availability(site, product, now=NOW, opener=opener)
    assert first["site_id"] == site and first["product"] == product
    assert statuses(first) == {cut: "available" if cut == "0" else "unavailable" for cut in inventory.TILTS}
    assert statuses(inventory.availability(site, product, now=NOW, opener=opener)) == statuses(first)
    assert len(calls) == 7


def test_history_rechecks_matching_times_without_refetching_the_same_directories():
    calls = []

    def opener(url, *_):
        calls.append(url)
        return listing(NOW - timedelta(minutes=3 if "DS.p99v0/" in url else 50))

    live = statuses(inventory.availability("KTLX", "velocity", now=NOW, opener=opener))
    past = statuses(inventory.availability("KTLX", "velocity", NOW - timedelta(minutes=50), now=NOW, opener=opener))
    assert set(live.values()) == {"available"}
    assert past["0"] == "unavailable" and past["1"] == "available" and len(calls) == 7


def test_real_clock_subseconds_are_normalized_to_the_public_timestamp_contract():
    result = inventory.availability("PGUA", "velocity", now=NOW.replace(microsecond=123456), opener=lambda *_: listing(NOW - timedelta(minutes=1)))
    assert result["checked_at"] == "2026-10-17T12:00:00Z"


def test_forbidden_is_missing_only_when_the_parent_confirms_absence():
    def opener(url, *_):
        if "/SI." not in url:
            return b'<a href="SI.ktlx/">site</a>' if "DS.p99v1/" in url else b'<a href="SI.pgua/">site</a>'
        raise radar_site.RadarSiteError("forbidden") from HTTPError(url, 403, "forbidden", {}, None)

    result = statuses(inventory.availability("KTLX", "velocity", now=NOW, opener=opener))
    assert result["1"] == "unknown"
    assert result["0"] == "unavailable"


@pytest.mark.parametrize("body", [None, b"<html>Upstream error</html>"])
def test_network_failure_and_malformed_listing_remain_unknown(body):
    def opener(*_):
        if body is None:
            raise radar_site.RadarSiteError("timeout")
        return body

    assert set(statuses(inventory.availability("KTLX", "velocity", now=NOW, opener=opener)).values()) == {"unknown"}
    assert not radar._DIRECTORIES


def test_directory_cache_expires_and_is_shared_with_frame_fetch(monkeypatch):
    moment = [100.0]
    monkeypatch.setattr(radar.time, "monotonic", lambda: moment[0])
    calls = []

    def opener(url, *_):
        calls.append(url)
        return listing(NOW - timedelta(minutes=1))

    inventory.availability("KTLX", "velocity", now=NOW, opener=opener)
    url = f"{radar.BASE}/DS.p99v0/SI.ktlx/"
    assert radar.directory_scans(url, opener, 1) and len(calls) == 7
    moment[0] += 46
    radar.directory_scans(url, opener, 1)
    assert len(calls) == 8


@pytest.mark.parametrize("site,product", [("../bad", "velocity"), ("KTLX", "echo-tops")])
def test_invalid_inputs_never_reach_the_network(site, product):
    with pytest.raises(radar_site.RadarSiteError, match="unknown"):
        inventory.availability(site, product, now=NOW, opener=lambda *_: pytest.fail("network"))


def test_bridge_rejects_missing_sites_bad_products_and_paths(monkeypatch):
    from sharpmod.bridge import radar_inventory as bridge
    from sharpmod.bridge.errors import BridgeError

    monkeypatch.setattr(inventory, "availability", lambda site, product, when: {"site_id": site, "product": product, "when": when})
    assert bridge.tilts({"site_id": "PGUA", "product": "velocity"})["site_id"] == "PGUA"
    for params in ({"site_id": "../bad", "product": "velocity"}, {"site_id": "KTLX", "product": "echo-tops"}, {"site_id": "KTLX", "product": "velocity", "url": "file:///bad"}):
        with pytest.raises(BridgeError):
            bridge.tilts(params)
