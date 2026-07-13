"""Loader and search over the bundled University of Wyoming station catalogue.

The full catalogue of fixed UWyo radiosonde stations is bundled as the package
resource ``sharpmod/resources/uwyo_stations.json`` (produced offline by
:mod:`sharpmod.tools.build_uwyo_catalog`). This module loads it *package
relative* through :mod:`importlib.resources` (no absolute paths, Requirement
15.2) and exposes lookup / search / listing helpers so every UWyo station is
choosable without a network round-trip.

Each catalogue record is ``{"id", "name", "lat", "lon", "src"}`` where ``src``
is the UWyo data source (``FM35`` / ``BUFR`` / ...) used when fetching that
station from the modern ``/wsgi/`` server.

If the bundled resource is missing (e.g. a checkout that has not run the
builder), the loader falls back to a small embedded seed catalogue so the
decoder still functions.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
from datetime import datetime
from functools import lru_cache
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

try:  # Python 3.9+: importlib.resources.files
    from importlib.resources import files as _res_files
except Exception:  # pragma: no cover - very old Pythons
    _res_files = None

try:  # certifi is a declared runtime dependency; fall back gracefully.
    import certifi
    _CA_FILE = certifi.where()
except Exception:  # pragma: no cover - certifi always present in practice
    _CA_FILE = None

__all__ = [
    "load_catalog",
    "all_stations",
    "get_station",
    "search_stations",
    "fetch_stations_for_datetime",
    "StationListError",
    "SEED_STATIONS",
]

_RESOURCE_PACKAGE = "sharpmod.resources"
_RESOURCE_NAME = "uwyo_stations.json"

#: Modern UWyo ``/wsgi/`` endpoint that lists every station reporting at a given
#: observation time. Unlike the bundled (fixed-in-time) catalogue, this reflects
#: the network as it actually was for that ``datetime`` -- so stations that were
#: relocated (and had their WMO index change) resolve correctly for the period
#: they reported under that id.
_STATION_LIST_URL = "https://weather.uwyo.edu/wsgi/sounding_json"

#: Hard fetch timeout in seconds for the live station-list request.
_STATION_LIST_TIMEOUT = 30

#: Minimal seed catalogue used only if the bundled JSON resource is absent.
#: Records mirror the JSON schema: id -> (name, lat, lon, elev-unused, src).
SEED_STATIONS: dict[str, tuple[str, float, float, str]] = {
    "72558": ("OAX Omaha/Valley, NE", 41.32, -96.37, "FM35"),
    "72357": ("OUN Norman, OK", 35.18, -97.44, "FM35"),
    "72249": ("FWD Fort Worth, TX", 32.83, -97.30, "FM35"),
    "72469": ("DNR Denver, CO", 39.77, -104.87, "FM35"),
    "72451": ("DDC Dodge City, KS", 37.77, -99.97, "FM35"),
    "72562": ("LBF North Platte, NE", 41.13, -100.68, "FM35"),
    "72572": ("SLC Salt Lake City, UT", 40.77, -111.95, "FM35"),
    "74560": ("ILX Lincoln, IL", 40.15, -89.34, "FM35"),
    "72403": ("IAD Washington Dulles, VA", 38.98, -77.47, "FM35"),
    "72776": ("TFX Great Falls, MT", 47.46, -111.38, "FM35"),
}


def _seed_catalog() -> list[dict]:
    out = []
    for sid, (name, lat, lon, src) in SEED_STATIONS.items():
        out.append({"id": sid, "name": name, "lat": lat, "lon": lon,
                    "src": src})
    return out


@lru_cache(maxsize=1)
def load_catalog() -> list[dict]:
    """Return the full station catalogue as a list of records.

    Loads the bundled JSON resource; on any failure falls back to
    :data:`SEED_STATIONS`. Cached for the process lifetime.
    """
    if _res_files is not None:
        try:
            res = _res_files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_NAME)
            text = res.read_text(encoding="utf-8")
            data = json.loads(text)
            stations = data.get("stations", [])
            # Normalize records and ensure required keys are present.
            out = []
            for row in stations:
                sid = str(row.get("id", "")).strip()
                if not sid:
                    continue
                out.append({
                    "id": sid,
                    "name": str(row.get("name", "")).strip(),
                    "lat": float(row.get("lat", "nan")),
                    "lon": float(row.get("lon", "nan")),
                    "src": str(row.get("src", "UNKNOWN")).strip() or "UNKNOWN",
                })
            if out:
                return out
        except Exception:
            pass
    return _seed_catalog()


def all_stations() -> list[dict]:
    """Return a copy of every station record, sorted by id."""
    return sorted((dict(r) for r in load_catalog()), key=lambda r: r["id"])


def get_station(station_id) -> dict | None:
    """Return the record for an exact station id, or ``None`` if unknown."""
    sid = str(station_id).strip()
    for row in load_catalog():
        if row["id"] == sid:
            return dict(row)
    return None


def search_stations(query, limit: int | None = None) -> list[dict]:
    """Return catalogue records matching ``query`` (id or name substring).

    An exact id match is returned first and alone. Otherwise a case-insensitive
    substring match against station id and name is returned, sorted by id.
    """
    q = str(query).strip()
    if q == "":
        return []
    exact = get_station(q)
    if exact is not None:
        return [exact]
    ql = q.casefold()
    matches = [dict(r) for r in load_catalog()
               if ql in r["id"].casefold() or ql in r["name"].casefold()]
    matches.sort(key=lambda r: r["id"])
    if limit is not None:
        matches = matches[:limit]
    return matches


# --------------------------------------------------------------------------- #
# Live, datetime-aware station listing (Requirement: relocated-station support)
# --------------------------------------------------------------------------- #
class StationListError(Exception):
    """The live UWyo station list could not be fetched or parsed."""


_WS_RE = re.compile(r"\s+")


def _clean_name(raw) -> str:
    """Normalize a station name from the ``sounding_json`` payload.

    The live feed is occasionally polluted with pandas ``repr`` fragments (e.g.
    ``"Stationid\\n73111    Kapuskasing ... Dtype: Str"``). Collapse whitespace,
    and drop obviously corrupt dumps so the picker shows a clean (or blank)
    label rather than garbage.
    """
    name = _WS_RE.sub(" ", str(raw or "")).strip()
    if not name:
        return ""
    low = name.casefold()
    if "dtype:" in low or "stationid" in low:
        return ""
    return name


def _build_station_list_url(when_utc: datetime) -> str:
    """Build the ``sounding_json`` request URL for ``when_utc`` (UTC)."""
    params = {"datetime": when_utc.strftime("%Y-%m-%d %H:00:00")}
    return "%s?%s" % (_STATION_LIST_URL, urlencode(params))


def fetch_stations_for_datetime(when_utc: datetime,
                                timeout: float = _STATION_LIST_TIMEOUT
                                ) -> list[dict]:
    """Fetch the stations UWyo reported at ``when_utc`` (UTC) over HTTPS.

    Queries the modern ``/wsgi/sounding_json`` endpoint, which returns the set
    of upper-air stations available for the requested observation time. Each
    record is normalized to the same ``{"id", "name", "lat", "lon", "src"}``
    schema the bundled catalogue uses, so the result is a drop-in replacement
    for :func:`all_stations` in a station picker -- but reflecting the network
    as it was at ``when_utc`` (Requirement: relocated / re-indexed stations).

    Parameters
    ----------
    when_utc : datetime.datetime
        The observation date/hour, interpreted as UTC.
    timeout : float
        Hard request timeout in seconds.

    Returns
    -------
    list[dict]
        Normalized station records, sorted by id (duplicate ids collapsed,
        keeping the first occurrence).

    Raises
    ------
    StationListError
        If the service cannot be reached, times out, or returns something that
        is not a parseable station list.
    """
    if not isinstance(when_utc, datetime):
        raise StationListError(
            f"observation time must be a datetime, got {when_utc!r}")

    url = _build_station_list_url(when_utc)
    context = ssl.create_default_context(cafile=_CA_FILE)
    try:
        with urlopen(url, timeout=timeout, context=context) as resp:
            raw = resp.read()
    except (socket.timeout, TimeoutError) as exc:
        raise StationListError(
            f"UWyo station list timed out after {timeout}s") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise StationListError(
            f"UWyo station list could not be reached ({reason})") from exc
    except (OSError, ValueError) as exc:
        raise StationListError(
            f"UWyo station list could not be reached ({exc})") from exc

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as exc:
        raise StationListError(
            f"UWyo station list was not valid JSON: {exc}") from exc

    rows = payload.get("stations") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise StationListError(
            "UWyo station list response did not contain a 'stations' array")

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("stationid", row.get("id", ""))).strip()
        if not sid or sid in seen:
            continue
        try:
            lat = float(row.get("lat", "nan"))
            lon = float(row.get("lon", "nan"))
        except (TypeError, ValueError):
            lat = lon = float("nan")
        seen.add(sid)
        out.append({
            "id": sid,
            "name": _clean_name(row.get("name", "")),
            "lat": lat,
            "lon": lon,
            "src": str(row.get("src", "UNKNOWN")).strip() or "UNKNOWN",
        })

    if not out:
        raise StationListError(
            "UWyo reported no stations for the requested time")

    out.sort(key=lambda r: r["id"])
    return out
