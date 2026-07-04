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
from functools import lru_cache

try:  # Python 3.9+: importlib.resources.files
    from importlib.resources import files as _res_files
except Exception:  # pragma: no cover - very old Pythons
    _res_files = None

__all__ = [
    "load_catalog",
    "all_stations",
    "get_station",
    "search_stations",
    "SEED_STATIONS",
]

_RESOURCE_PACKAGE = "sharpmod.resources"
_RESOURCE_NAME = "uwyo_stations.json"

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
