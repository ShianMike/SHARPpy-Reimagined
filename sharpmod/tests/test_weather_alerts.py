"""Warnings and watches retain official geometry, times and source colours."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from sharpmod.bridge import weather_alerts as bridge
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import weather_alerts as provider

NOW = datetime(2026, 10, 4, 21, tzinfo=UTC)
RING = [[-98, 34], [-96, 34], [-96, 36], [-98, 36], [-98, 34]]


def feature(identity="warning", sig="W", **changes):
    props = {
        "cap_id": identity,
        "sig": sig,
        "phenom": "TO",
        "prod_type": "Tornado Warning" if sig == "W" else "Tornado Watch",
        "issuance": "2026-10-04T15:45:00-05:00",
        "expiration": "2026-10-04T22:00:00Z",
        "ends": " ",
        "onset": "2026-10-05T00:00:00Z",
        "wfo": "KOUN",
        "url": "https://api.weather.gov/alerts/example",
    }
    props.update(changes)
    return {
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [deepcopy(RING)]},
    }


def feed(storms=None, zones=None):
    calls = []

    def open_json(url):
        calls.append(url)
        if url == "https://api.weather.gov/alerts/active":
            return {"features": []}
        layer = int(urlsplit(url).path.split("/MapServer/")[1].split("/")[0])
        if "/query?" in url:
            return {
                "features": deepcopy(
                    (storms if storms is not None else [feature()])
                    if layer == 0
                    else (
                        zones
                        if zones is not None
                        else [feature(), feature("watch", "A")]
                    )
                )
            }
        classes = [
            {
                "symbol": {"color": colour, "outline": {"color": colour}},
                "values": [["TO", sig] if layer == 0 else [event]],
            }
            for sig, event, colour in [
                ("W", "Tornado Warning", [255, 0, 0, 255]),
                ("A", "Tornado Watch", [255, 255, 0, 255]),
            ]
        ]
        return {
            "drawingInfo": {"renderer": {"uniqueValueGroups": [{"classes": classes}]}}
        }

    return open_json, calls


@pytest.fixture(autouse=True)
def clear_styles():
    provider._colours.cache_clear()
    yield
    provider._colours.cache_clear()


def test_failed_download_closes_its_http_response(monkeypatch):
    body = BytesIO(b"temporarily unavailable")
    error = HTTPError(provider.BASE, 503, "Unavailable", {}, body)

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider, "urlopen", unavailable)
    with pytest.raises(provider.AlertError, match="503"):
        provider._json(provider.BASE)
    assert body.closed


def test_current_alerts_prefer_precise_polygons_keep_watch_onset_and_convert_to_utc():
    zone = feature()
    zone["geometry"]["coordinates"][0][1] = [-95, 34]
    open_json, calls = feed(
        zones=[
            zone,
            feature("watch", "A"),
            feature("expired", expiration="2026-10-04T21:00:00Z"),
            feature("advisory", "Y"),
        ]
    )
    result = provider.fetch(NOW, now=NOW, opener=open_json)
    assert result["fetched_at"] == result["valid_time"] == "2026-10-04T21:00:00Z"
    warning, watch = result["alerts"]
    assert warning["polygons"] == [[RING]]
    assert warning["issued"] == "2026-10-04T20:45:00Z"
    assert (warning["kind"], warning["colour"], watch["kind"], watch["colour"]) == (
        "warning",
        "#ff0000",
        "watch",
        "#ffff00",
    )
    assert "maxAllowableOffset" not in calls[1]  # Precise storm polygons stay intact.
    assert "maxAllowableOffset" in calls[3]


def test_multi_polygons_and_multiple_zone_parts_are_retained():
    part = feature("watch", "A")
    part["geometry"] = {
        "type": "MultiPolygon",
        "coordinates": [[deepcopy(RING)], [deepcopy(RING)]],
    }
    open_json, _ = feed(storms=[], zones=[part, feature("watch", "A")])
    assert (
        len(provider.fetch(NOW, now=NOW, opener=open_json)["alerts"][0]["polygons"])
        == 3
    )


def test_no_current_alerts_are_substituted_for_uncached_history():
    open_json, calls = feed()
    with pytest.raises(provider.HistoryUnavailable, match="cached during this session"):
        provider.fetch(NOW - timedelta(minutes=1), now=NOW, opener=open_json)
    assert not calls


@pytest.mark.parametrize(
    "change",
    ["coordinate", "open-ring", "empty", "unsupported", "link", "expired", "future"],
)
def test_untrusted_or_inactive_alerts(change):
    item = feature()
    if change == "coordinate":
        item["geometry"]["coordinates"][0][1] = [181, 34]
    elif change == "open-ring":
        item["geometry"]["coordinates"][0][-1] = [-97, 34]
    elif change == "empty":
        item["geometry"]["coordinates"] = []
    elif change == "unsupported":
        item["geometry"]["type"] = "Point"
    elif change == "link":
        item["properties"]["url"] = "https://api.weather.gov.evil.example/alerts"
    elif change == "expired":
        item["properties"]["ends"] = "2026-10-04T20:59:00Z"
    else:
        item["properties"]["issuance"] = "2026-10-04T21:01:00Z"
    open_json, _ = feed(storms=[item], zones=[])
    if change in {"expired", "future"}:
        assert provider.fetch(NOW, now=NOW, opener=open_json)["alerts"] == []
    else:
        with pytest.raises(provider.AlertError):
            provider.fetch(NOW, now=NOW, opener=open_json)


def test_incomplete_nws_results_are_an_error_not_a_partial_all_clear():
    open_json, _ = feed()

    def incomplete(url):
        payload = open_json(url)
        return (
            {**payload, "exceededTransferLimit": True} if "/query?" in url else payload
        )

    with pytest.raises(provider.AlertError, match="incomplete"):
        provider.fetch(NOW, now=NOW, opener=incomplete)


@pytest.mark.parametrize("storms", [[], [feature()]])
def test_empty_map_tables_during_an_update_do_not_hide_active_cap_alerts(storms):
    open_json, _ = feed(storms=storms, zones=[])

    def updating(url):
        if url == "https://api.weather.gov/alerts/active":
            return {
                "features": [
                    {
                        "properties": {
                            "event": "Tornado Watch",
                            "status": "Actual",
                            "messageType": "Alert",
                            "expires": "2026-10-04T22:00:00Z",
                        }
                    }
                ]
            }
        return open_json(url)

    with pytest.raises(provider.AlertError, match="temporarily incomplete"):
        provider.fetch(NOW, now=NOW, opener=updating)


def test_all_clear_is_allowed_when_cap_has_only_advisories():
    open_json, _ = feed(storms=[], zones=[])

    def advisories(url):
        return (
            {"features": [{"properties": {"event": "Frost Advisory"}}]}
            if url == "https://api.weather.gov/alerts/active"
            else open_json(url)
        )

    assert provider.fetch(NOW, now=NOW, opener=advisories)["alerts"] == []


def test_bridge_rejects_bad_time_and_marks_uncached_history_nonretryable(monkeypatch):
    with pytest.raises(BridgeError):
        bridge.current({"valid_time": "invalid"})

    def missing(when):
        raise provider.HistoryUnavailable("snapshot unavailable")

    monkeypatch.setattr(provider, "fetch", missing)
    with pytest.raises(BridgeError) as caught:
        bridge.current({"valid_time": "2026-10-04T21:00:00Z"})
    assert not caught.value.retryable
