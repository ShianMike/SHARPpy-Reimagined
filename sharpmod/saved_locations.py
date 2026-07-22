"""Versioned saved and recent point locations for GUI source pickers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile


LOCATION_FORMAT = "sharpmod-saved-locations"
LOCATION_VERSION = 1
SAVED_SETTINGS_KEY = "locations/saved"
RECENT_SETTINGS_KEY = "locations/recent"


class LocationFormatError(ValueError):
    """Raised when a saved-location document is invalid."""


@dataclass(frozen=True)
class SavedLocation:
    """One user-named latitude/longitude point."""

    name: str
    lat: float
    lon: float

    @classmethod
    def create(cls, name, lat, lon) -> "SavedLocation":
        label = str(name or "").strip()
        if not label:
            raise LocationFormatError("location name cannot be empty")
        try:
            latitude = float(lat)
            longitude = float(lon)
        except (TypeError, ValueError, OverflowError) as exc:
            raise LocationFormatError("location coordinates must be numbers") from exc
        if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise LocationFormatError("latitude must be between -90 and 90")
        if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise LocationFormatError("longitude must be between -180 and 180")
        # Avoid two textual identities for the same dateline point.
        if longitude == 180.0:
            longitude = -180.0
        return cls(label, latitude, longitude)


def _document(locations) -> dict:
    return {
        "format": LOCATION_FORMAT,
        "version": LOCATION_VERSION,
        "locations": [asdict(location) for location in locations],
    }


def _parse_document(value) -> list[SavedLocation]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise LocationFormatError("saved-location JSON is invalid") from exc
    if not isinstance(value, dict):
        raise LocationFormatError("saved-location document must be an object")
    if value.get("format") != LOCATION_FORMAT:
        raise LocationFormatError("not a SHARPpy Reimagined location document")
    if value.get("version") != LOCATION_VERSION:
        raise LocationFormatError(
            f"unsupported saved-location version: {value.get('version')}"
        )
    records = value.get("locations")
    if not isinstance(records, list):
        raise LocationFormatError("saved-location list is missing")
    result = []
    names = set()
    for record in records:
        if not isinstance(record, dict):
            raise LocationFormatError("saved-location entry must be an object")
        location = SavedLocation.create(
            record.get("name"), record.get("lat"), record.get("lon")
        )
        folded = location.name.casefold()
        if folded in names:
            raise LocationFormatError(
                f"duplicate saved-location name: {location.name}"
            )
        names.add(folded)
        result.append(location)
    return result


class SavedLocationStore:
    """Persist a bounded location list in a QSettings-compatible object."""

    def __init__(self, settings, *, key=SAVED_SETTINGS_KEY, max_entries=None):
        self.settings = settings
        self.key = str(key)
        self.max_entries = (
            None if max_entries is None else max(1, int(max_entries))
        )

    def load(self) -> list[SavedLocation]:
        raw = self.settings.value(self.key, "", str)
        if not str(raw or "").strip():
            return []
        return _parse_document(raw)

    def save(self, locations) -> list[SavedLocation]:
        normalized = [
            location if isinstance(location, SavedLocation)
            else SavedLocation.create(
                location["name"], location["lat"], location["lon"]
            )
            for location in locations
        ]
        # Last writer wins case-insensitively while preserving display casing.
        deduplicated = {}
        for location in normalized:
            deduplicated[location.name.casefold()] = location
        result = list(deduplicated.values())
        if self.max_entries is not None:
            result = result[:self.max_entries]
        self.settings.setValue(
            self.key,
            json.dumps(_document(result), ensure_ascii=False, sort_keys=True),
        )
        sync = getattr(self.settings, "sync", None)
        if callable(sync):
            sync()
        return result

    def upsert(self, name, lat, lon, *, newest_first=False) -> SavedLocation:
        location = SavedLocation.create(name, lat, lon)
        existing = [
            item for item in self.load()
            if item.name.casefold() != location.name.casefold()
        ]
        items = ([location] + existing) if newest_first else (existing + [location])
        self.save(items)
        return location

    def remove(self, name) -> bool:
        folded = str(name or "").strip().casefold()
        before = self.load()
        after = [item for item in before if item.name.casefold() != folded]
        if len(after) == len(before):
            return False
        self.save(after)
        return True

    def remember_recent(self, lat, lon, label=None) -> SavedLocation:
        latitude = float(lat)
        longitude = float(lon)
        name = str(label or f"{latitude:.4f}, {longitude:.4f}").strip()
        candidate = SavedLocation.create(name, latitude, longitude)
        existing = [
            item for item in self.load()
            if not (
                abs(item.lat - candidate.lat) < 1e-6
                and abs(item.lon - candidate.lon) < 1e-6
            )
        ]
        self.save([candidate, *existing])
        return candidate

    def export_file(self, path) -> str:
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = _document(self.load())
        fd, temporary = tempfile.mkstemp(
            prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, destination)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        return str(destination)

    def import_file(self, path, *, merge=True) -> list[SavedLocation]:
        source = Path(path).expanduser()
        try:
            incoming = _parse_document(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise LocationFormatError(
                f"saved-location file could not be read: {exc}"
            ) from exc
        if not merge:
            return self.save(incoming)
        merged = {item.name.casefold(): item for item in self.load()}
        for item in incoming:
            merged[item.name.casefold()] = item
        return self.save(merged.values())


__all__ = [
    "LOCATION_FORMAT",
    "LOCATION_VERSION",
    "LocationFormatError",
    "RECENT_SETTINGS_KEY",
    "SAVED_SETTINGS_KEY",
    "SavedLocation",
    "SavedLocationStore",
]
