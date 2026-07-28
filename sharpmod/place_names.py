"""Small, cached reverse-town lookup for user-selected sounding points."""

from __future__ import annotations

from functools import lru_cache
import gzip
import io
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sharpmod._version import __version__


DEFAULT_GEOCODER_URL = "https://nominatim.openstreetmap.org/reverse"
GEOCODER_URL_ENV = "SHARPMOD_GEOCODER_URL"
PLACE_CACHE_ENV = "SHARPMOD_PLACE_CACHE"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
NOMINATIM_POLICY_URL = \
    "https://operations.osmfoundation.org/policies/nominatim/"
CONUS_PLACE_RESOURCE = "conus-places.tsv.gz"

# Version 3 invalidates cached nearest-point-only labels after the area-aware
# resource/selection upgrade.
_CACHE_VERSION = 3
_POSITIVE_CACHE_TTL_S = 30 * 24 * 60 * 60
_FALLBACK_CACHE_TTL_S = 7 * 24 * 60 * 60
_NEGATIVE_CACHE_TTL_S = 15 * 60
_MIN_REQUEST_INTERVAL_S = 1.05
_CONUS_BOUNDS = (24.2, -125.0, 49.6, -66.4)
_CONUS_MAX_DISTANCE_KM = 200.0
_AREA_PROXY_RADIUS_SCALE = 1.25
_KM_PER_MILE = 1.609344
_BOUNDARY_PREFIX = "@boundary\t"
_NETWORK_LOCK = threading.RLock()
_LAST_REQUEST_AT = 0.0
_COORDINATE_LABEL_RE = re.compile(
    r"(?ix)"
    r"(?:\b\d{1,2}(?:\.\d+)?\s*[NS]\s*[,/ ]+\s*"
    r"\d{1,3}(?:\.\d+)?\s*[EW]\b)"
    r"|(?:[-+]?\d{1,2}(?:\.\d+)?\s*,\s*"
    r"[-+]?\d{1,3}(?:\.\d+)?)"
)


def _cache_path() -> Path:
    override = str(os.environ.get(PLACE_CACHE_ENV, "")).strip()
    if override:
        return Path(override).expanduser()
    root = str(os.environ.get("LOCALAPPDATA", "")).strip()
    if root:
        return Path(root) / "SHARPpy Reimagined" / "place-names.json"
    return Path.home() / ".cache" / "sharpmod" / "place-names.json"


def _cache_key(lat: float, lon: float) -> str:
    normalized_lon = ((float(lon) + 180.0) % 360.0) - 180.0
    return f"{float(lat):.3f},{normalized_lon:.3f}"


def _cache_ttl(source: str) -> float:
    if source == "online":
        return float(_POSITIVE_CACHE_TTL_S)
    if source == "offline":
        return float(_FALLBACK_CACHE_TTL_S)
    return float(_NEGATIVE_CACHE_TTL_S)


def _read_places(
    path: Path,
    *,
    now: float | None = None,
) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if payload.get("version") != _CACHE_VERSION:
        return {}
    places = payload.get("places")
    if not isinstance(places, dict):
        return {}
    current_time = time.time() if now is None else float(now)
    valid = {}
    for key, value in places.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        label = value.get("label")
        source = value.get("source")
        cached_at = value.get("cached_at")
        if not isinstance(label, str) or source not in {
            "online", "offline", "negative",
        }:
            continue
        try:
            timestamp = float(cached_at)
        except (TypeError, ValueError, OverflowError):
            continue
        age = current_time - timestamp
        if not math.isfinite(timestamp) or age < -300.0 \
                or age > _cache_ttl(str(source)):
            continue
        valid[key] = {
            "label": label,
            "source": str(source),
            "cached_at": timestamp,
        }
    return valid


def _write_places(
    path: Path,
    places: dict[str, dict[str, object]],
) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(
                {
                    "version": _CACHE_VERSION,
                    "attribution": OSM_ATTRIBUTION,
                    "updated_at": time.time(),
                    "places": places,
                },
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _place_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    address = payload.get("address")
    if not isinstance(address, dict):
        return ""
    settlement = next((
        str(address[key]).strip()
        for key in (
            "city", "town", "village", "municipality", "hamlet",
        )
        if str(address.get(key, "")).strip()
    ), "")
    if not settlement:
        return ""
    region = str(
        address.get("state")
        or address.get("region")
        or address.get("country")
        or ""
    ).strip()
    if region and region.casefold() != settlement.casefold():
        return f"{settlement}, {region}"
    return settlement


def _payload_is_us(payload: Any) -> bool:
    """Return whether an online result is explicitly U.S. or unspecified."""

    if not isinstance(payload, dict):
        return False
    address = payload.get("address")
    if not isinstance(address, dict):
        return False
    country_code = str(address.get("country_code", "")).strip().casefold()
    if country_code:
        return country_code == "us"
    country = str(address.get("country", "")).strip().casefold()
    if not country:
        return True
    return country in {
        "united states",
        "united states of america",
        "u.s.",
        "u.s.a.",
        "us",
        "usa",
    }


def needs_town_name(label: Any, model: Any = "") -> bool:
    """Return whether ``label`` is only a generic model/coordinate fallback."""

    text = " ".join(str(label or "").split())
    if not text or _COORDINATE_LABEL_RE.search(text):
        return True

    model_text = " ".join(str(model or "").split())
    if not model_text:
        return False
    folded = text.casefold()
    model_folded = model_text.casefold()
    return folded in {
        model_folded,
        f"{model_folded}pt",
        f"{model_folded} point",
    }


def _place_record_from_fields(
    fields: list[str],
) -> tuple[str, float, float, str, float] | None:
    """Parse current six-column and legacy five-column place records."""

    if len(fields) < 5:
        return None
    label, lat_text, lon_text, kind, _geoid = fields[:5]
    area_text = fields[5] if len(fields) >= 6 else ""
    try:
        lat = float(lat_text)
        lon = float(lon_text)
        land_area_sqmi = float(area_text) if area_text else 0.0
    except (TypeError, ValueError):
        return None
    if not label or kind not in {"place", "subdivision"} \
            or not all(math.isfinite(value) for value in (
                lat, lon, land_area_sqmi,
            )) \
            or land_area_sqmi < 0.0:
        return None
    return label, lat, lon, kind, land_area_sqmi


@lru_cache(maxsize=1)
def _conus_place_cells() -> dict[
    tuple[int, int],
    tuple[tuple[str, float, float, str, float], ...],
]:
    """Load the bundled Census title-place index into one-degree cells."""

    cells: dict[
        tuple[int, int],
        list[tuple[str, float, float, str, float]],
    ] = {}
    try:
        resource = files("sharpmod.resources").joinpath(CONUS_PLACE_RESOURCE)
        with resource.open("rb") as raw:
            with gzip.GzipFile(fileobj=raw) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as text:
                    for line in text:
                        if not line or line.startswith("#"):
                            continue
                        fields = line.rstrip("\n").split("\t")
                        record = _place_record_from_fields(fields)
                        if record is None:
                            continue
                        label, lat, lon, kind, land_area_sqmi = record
                        cells.setdefault(
                            (math.floor(lat), math.floor(lon)), []
                        ).append((
                            label,
                            lat,
                            lon,
                            kind,
                            land_area_sqmi,
                        ))
    except (OSError, UnicodeError):
        return {}
    return {key: tuple(values) for key, values in cells.items()}


@lru_cache(maxsize=1)
def _conus_boundary_polygons() -> tuple[
    tuple[
        float,
        float,
        float,
        float,
        tuple[tuple[float, float], ...],
    ],
    ...,
]:
    """Load Census state outlines stored beside the place-point index."""

    polygons = []
    try:
        resource = files("sharpmod.resources").joinpath(CONUS_PLACE_RESOURCE)
        with resource.open("rb") as raw:
            with gzip.GzipFile(fileobj=raw) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as text:
                    for line in text:
                        if not line.startswith(_BOUNDARY_PREFIX):
                            continue
                        fields = line.rstrip("\n").split("\t", 2)
                        if len(fields) != 3:
                            continue
                        points = []
                        for encoded in fields[2].split():
                            try:
                                lon_text, lat_text = encoded.split(",", 1)
                                lon = float(lon_text)
                                lat = float(lat_text)
                            except (TypeError, ValueError):
                                points = []
                                break
                            if not math.isfinite(lon) or not math.isfinite(lat):
                                points = []
                                break
                            points.append((lon, lat))
                        if len(points) < 4:
                            continue
                        if points[0] != points[-1]:
                            points.append(points[0])
                        longitudes = [point[0] for point in points]
                        latitudes = [point[1] for point in points]
                        polygons.append((
                            min(latitudes),
                            min(longitudes),
                            max(latitudes),
                            max(longitudes),
                            tuple(points),
                        ))
    except (OSError, UnicodeError):
        return ()
    return tuple(polygons)


def _point_on_segment(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    tolerance: float = 1.0e-8,
) -> bool:
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    scale = max(1.0, abs(x2 - x1), abs(y2 - y1))
    if abs(cross) > tolerance * scale:
        return False
    return (
        min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    )


def _point_in_polygon(
    lat: float,
    lon: float,
    points: tuple[tuple[float, float], ...],
) -> bool:
    """Ray-cast a longitude/latitude point against one closed ring."""

    x = float(lon)
    y = float(lat)
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def is_conus_point(lat: float, lon: float) -> bool:
    """Return whether a coordinate is in the lower 48 states or D.C."""

    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return False
    south, west, north, east = _CONUS_BOUNDS
    if not south <= latitude <= north or not west <= longitude <= east:
        return False
    for min_lat, min_lon, max_lat, max_lon, points in \
            _conus_boundary_polygons():
        if min_lat <= latitude <= max_lat \
                and min_lon <= longitude <= max_lon \
                and _point_in_polygon(latitude, longitude, points):
            return True
    return False


def _distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Return great-circle distance between two coordinates."""

    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    hav = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1r) * math.cos(lat2r)
        * math.sin(dlon / 2.0) ** 2
    )
    return 6371.0088 * 2.0 * math.asin(
        math.sqrt(min(1.0, max(0.0, hav)))
    )


def _area_proxy_radius_km(land_area_sqmi: float) -> float:
    """Return a conservative circular-footprint radius for a Census entity."""

    try:
        area = float(land_area_sqmi)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(area) or area <= 0.0:
        return 0.0
    equivalent_radius_miles = math.sqrt(area / math.pi)
    return (
        equivalent_radius_miles
        * _KM_PER_MILE
        * _AREA_PROXY_RADIUS_SCALE
    )


def offline_conus_town_name(
    lat: float,
    lon: float,
    *,
    max_distance_km: float = _CONUS_MAX_DISTANCE_KM,
) -> str:
    """Return the nearest bundled Census place for a likely CONUS point."""

    try:
        latitude = float(lat)
        longitude = float(lon)
        distance_limit = max(1.0, float(max_distance_km))
    except (TypeError, ValueError, OverflowError):
        return ""
    if not all(math.isfinite(value) for value in (
        latitude, longitude, distance_limit
    )):
        return ""

    if not is_conus_point(latitude, longitude):
        return ""

    lat_delta = distance_limit / 110.5 + 0.1
    lon_scale = max(0.35, math.cos(math.radians(latitude)))
    lon_delta = distance_limit / (111.0 * lon_scale) + 0.1
    cells = _conus_place_cells()
    best: tuple[float, int, str] | None = None
    containing_best: tuple[int, float, float, str] | None = None
    for lat_cell in range(
        math.floor(latitude - lat_delta),
        math.floor(latitude + lat_delta) + 1,
    ):
        for lon_cell in range(
            math.floor(longitude - lon_delta),
            math.floor(longitude + lon_delta) + 1,
        ):
            for label, place_lat, place_lon, kind, land_area_sqmi in cells.get(
                (lat_cell, lon_cell), ()
            ):
                distance = _distance_km(
                    latitude, longitude, place_lat, place_lon
                )
                # Prefer an incorporated/CDP place only when representative
                # points are effectively tied. A substantially closer named
                # town/township remains the better rural title.
                kind_rank = 0 if kind == "place" else 1
                candidate = (distance, kind_rank, label)
                if best is None or candidate < best:
                    best = candidate
                proxy_radius = _area_proxy_radius_km(land_area_sqmi)
                if proxy_radius > 0.0 and distance <= proxy_radius:
                    containing_candidate = (
                        kind_rank,
                        distance,
                        land_area_sqmi,
                        label,
                    )
                    if containing_best is None \
                            or containing_candidate < containing_best:
                        containing_best = containing_candidate
    if containing_best is not None:
        return containing_best[3]
    if best is None or best[0] > distance_limit:
        return ""
    return best[2]


def reverse_town_name(
    lat: float,
    lon: float,
    *,
    timeout: float = 4.0,
    opener=None,
) -> str:
    """Resolve a user-selected coordinate to a cached town/region label.

    The public default is intentionally replaceable through
    ``SHARPMOD_GEOCODER_URL``. The deterministic bundled CONUS result is tried
    first so normal GUI opens never wait on the network. If that index has no
    result, requests are serialized, limited to at most one per second, and
    identify this application. Online, offline, and negative results use
    source-specific cache expiry times.
    """

    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return ""
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        return ""
    if not is_conus_point(latitude, longitude):
        return ""

    path = _cache_path()
    key = _cache_key(latitude, longitude)
    places = _read_places(path)
    if key in places:
        return str(places[key]["label"])

    # The bundled Census index is complete for normal CONUS points and avoids
    # blocking a GUI paint/open path on a reverse-geocoding request. The public
    # resolver is retained only for the uncommon case where the bounded
    # nearest-place search has no result.
    place = offline_conus_town_name(latitude, longitude)
    if place:
        with _NETWORK_LOCK:
            places = _read_places(path)
            if key in places:
                return str(places[key]["label"])
            places[key] = {
                "label": place,
                "source": "offline",
                "cached_at": time.time(),
            }
            _write_places(path, places)
        return place

    endpoint = str(
        os.environ.get(GEOCODER_URL_ENV, DEFAULT_GEOCODER_URL)
    ).strip()
    if not endpoint or endpoint.casefold() in {"0", "false", "none", "off"}:
        with _NETWORK_LOCK:
            places = _read_places(path)
            if key in places:
                return str(places[key]["label"])
            places[key] = {
                "label": "",
                "source": "negative",
                "cached_at": time.time(),
            }
            _write_places(path, places)
        return ""

    request_url = endpoint + ("&" if "?" in endpoint else "?") + urlencode({
        "format": "jsonv2",
        "lat": f"{latitude:.6f}",
        "lon": f"{longitude:.6f}",
        "zoom": "13",
        "addressdetails": "1",
        "layer": "address",
    })
    request = Request(
        request_url,
        headers={
            "User-Agent": (
                f"SHARPpy-Reimagined/{__version__} "
                "(https://github.com/ShianMike/SHARPpy-Reimagined)"
            ),
            "Accept": "application/json",
            "Accept-Language": "en",
        },
    )
    open_request = opener or urlopen

    global _LAST_REQUEST_AT
    with _NETWORK_LOCK:
        # Another worker may have populated the persistent cache while this
        # worker waited for the single-request lane.
        places = _read_places(path)
        if key in places:
            return str(places[key]["label"])
        delay = _MIN_REQUEST_INTERVAL_S - (time.monotonic() - _LAST_REQUEST_AT)
        if delay > 0.0:
            time.sleep(delay)
        try:
            response = open_request(request, timeout=max(0.1, float(timeout)))
            with response:
                payload = json.loads(response.read(262_145).decode("utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            _LAST_REQUEST_AT = time.monotonic()
            place = offline_conus_town_name(latitude, longitude)
            places[key] = {
                "label": place,
                "source": "offline" if place else "negative",
                "cached_at": time.time(),
            }
            _write_places(path, places)
            return place
        _LAST_REQUEST_AT = time.monotonic()
        place = _place_from_payload(payload) if _payload_is_us(payload) else ""
        source = "online"
        if not place:
            place = offline_conus_town_name(latitude, longitude)
            source = "offline" if place else "negative"
        places[key] = {
            "label": place,
            "source": source,
            "cached_at": time.time(),
        }
        _write_places(path, places)
        if place:
            return place
        return ""


__all__ = [
    "CONUS_PLACE_RESOURCE",
    "DEFAULT_GEOCODER_URL",
    "GEOCODER_URL_ENV",
    "NOMINATIM_POLICY_URL",
    "OSM_ATTRIBUTION",
    "OSM_COPYRIGHT_URL",
    "PLACE_CACHE_ENV",
    "is_conus_point",
    "needs_town_name",
    "offline_conus_town_name",
    "reverse_town_name",
]
