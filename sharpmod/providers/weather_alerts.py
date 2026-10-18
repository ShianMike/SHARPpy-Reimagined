"""Current NWS warning polygons and zone-based watches, without raster resampling."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BASE = "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer"
MAX_BYTES = 12 * 1024 * 1024
MAX_POINTS = 250_000


class AlertError(ValueError):
    pass


class HistoryUnavailable(AlertError):
    pass


def _json(url):
    request = Request(
        url,
        headers={
            "User-Agent": "SHARPpy-Reimagined (https://github.com/ShianMike/SHARPpy-Reimagined)",
            "Accept": "application/geo+json, application/json",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise AlertError("NWS alerts exceeded the transfer limit")
        result = json.loads(body)
        if not isinstance(result, dict) or "error" in result:
            raise AlertError("NWS did not return a usable alert collection")
        return result
    except (OSError, ValueError) as exc:
        if isinstance(exc, HTTPError):
            exc.close()
        raise AlertError(f"NWS alerts could not be read: {exc}") from exc


@lru_cache(maxsize=4)
def _colours(layer, opener):
    renderer = (
        opener(f"{BASE}/{layer}?f=json").get("drawingInfo", {}).get("renderer", {})
    )
    result = {}
    for group in renderer.get("uniqueValueGroups", []):
        for item in group.get("classes", []):
            symbol = item["symbol"]
            rgb = symbol["outline"]["color"] if layer == 0 else symbol["color"]
            if len(rgb) < 3 or any(
                type(n) is not int or not 0 <= n <= 255 for n in rgb[:3]
            ):
                raise AlertError("NWS supplied an invalid alert colour")
            for values in item.get("values", []):
                result[tuple(values)] = "#" + "".join(f"{n:02x}" for n in rgb[:3])
    if not result:
        raise AlertError("NWS alert colours are unavailable")
    return result


def _time(value):
    if not isinstance(value, str):
        raise AlertError("NWS supplied an invalid alert time")
    result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise AlertError("NWS supplied an alert time without a time zone")
    return result.astimezone(timezone.utc)


def _text(value, maximum):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= maximum:
        raise AlertError("NWS supplied invalid alert metadata")
    return value.strip()


def _confirm_empty(call, reference):
    # NOAA's map tables can briefly empty while updating; CAP confirms a real all-clear.
    payload = call("https://api.weather.gov/alerts/active")
    features = payload.get("features")
    if (
        not isinstance(features, list)
        or len(features) > 4000
        or payload.get("pagination", {}).get("next")
    ):
        raise AlertError("NWS did not provide a complete alert status check")
    for feature in features:
        props = feature["properties"]
        event = _text(props["event"], 120)
        if (
            event.endswith((" Warning", " Watch"))
            and props.get("status") == "Actual"
            and props.get("messageType") != "Cancel"
            and _time(props["expires"]) > reference
        ):
            raise AlertError(
                "NWS map areas are temporarily incomplete; active warnings or watches still exist. Refresh to check again"
            )


def fetch(valid_time, *, now=None, opener=None):
    reference = now or datetime.now(timezone.utc)
    if (
        not reference - timedelta(minutes=1)
        < valid_time
        <= reference + timedelta(seconds=5)
    ):
        raise HistoryUnavailable(
            "Warning and watch history is available only for snapshots cached during this session"
        )
    call = opener or _json
    alerts = {}
    preferred = set()
    points = 0
    empty_zones = False
    # The storm-based layer has the precise polygon; prefer it to its zone-based duplicate.
    for layer in (0, 1):
        # Generalise coastal zones to about 100 m; keep precise storm polygons intact.
        query = urlencode(
            {
                "f": "geojson",
                "where": "sig IN ('W','A')",
                "outFields": "*",
                "outSR": 4326,
                "geometryPrecision": 6,
                "resultRecordCount": 4000,
                **({"maxAllowableOffset": 0.001} if layer == 1 else {}),
            }
        )
        colours = _colours(layer, call)
        payload = call(f"{BASE}/{layer}/query?{query}")
        features = payload.get("features")
        if (
            payload.get("exceededTransferLimit")
            or not isinstance(features, list)
            or len(features) > 4000
        ):
            raise AlertError("NWS returned an incomplete alert collection")
        if layer == 1:
            empty_zones = not features
        for feature in features:
            props = feature["properties"]
            if props.get("sig") not in {"W", "A"}:
                continue
            issued, expires = _time(props["issuance"]), _time(props["expiration"])
            if (props.get("ends") or "").strip():
                expires = min(expires, _time(props["ends"]))
            if not issued <= reference < expires:
                continue
            identity = _text(props["cap_id"], 500)
            if layer == 1 and identity in preferred:
                continue
            geometry = feature["geometry"]
            if geometry["type"] not in {"Polygon", "MultiPolygon"}:
                raise AlertError("NWS supplied an unsupported alert geometry")
            polygons = (
                geometry["coordinates"]
                if geometry["type"] == "MultiPolygon"
                else [geometry["coordinates"]]
            )
            if not polygons:
                raise AlertError("NWS supplied an empty alert geometry")
            for polygon in polygons:
                if not polygon:
                    raise AlertError("NWS supplied an empty alert polygon")
                for ring in polygon:
                    points += len(ring)
                    if len(ring) < 4 or points > MAX_POINTS:
                        raise AlertError(
                            "NWS alert geometry exceeds the supported limits"
                        )
                    for point in ring:
                        if (
                            len(point) != 2
                            or any(
                                type(n) not in {float, int} or not math.isfinite(n)
                                for n in point
                            )
                            or not -180 <= point[0] <= 180
                            or not -90 <= point[1] <= 90
                        ):
                            raise AlertError("NWS supplied an invalid alert coordinate")
                    if ring[0] != ring[-1]:
                        raise AlertError("NWS supplied an unclosed alert boundary")
            if layer == 0:
                preferred.add(identity)
            colour = colours.get(
                (props["phenom"], props["sig"]) if layer == 0 else (props["prod_type"],)
            )
            if colour is None:
                raise AlertError("NWS did not identify this alert's display colour")
            link = _text(props["url"], 1000)
            parsed = urlsplit(link)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "api.weather.gov"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
            ):
                raise AlertError("NWS supplied an unsupported alert link")
            item = alerts.setdefault(
                identity,
                {
                    "id": identity,
                    "event": _text(props["prod_type"], 120),
                    "kind": "warning" if props["sig"] == "W" else "watch",
                    "office": _text(props["wfo"], 20),
                    "issued": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "expires": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "colour": colour,
                    "source_url": link,
                    "polygons": [],
                },
            )
            item["polygons"].extend(polygons)
    if len(alerts) > 4000:
        raise AlertError("NWS returned too many active alerts")
    if empty_zones or not alerts:
        _confirm_empty(call, reference)
    return {
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetched_at": reference.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alerts": list(alerts.values()),
    }
