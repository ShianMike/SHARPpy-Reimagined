"""Bounded discovery metadata, separate from scientific session/location formats."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os

from sharpmod.state.saved_locations import SavedLocation


SETTINGS_KEY = "recent/destinations"
FORMAT = "sharpmod-recent-destinations"
VERSION = 1
LIMITS = {"file": 8, "session": 8, "location": 12}


class RecentFormatError(ValueError):
    """Unsupported/corrupt metadata must not overwrite an existing history."""


def _path(value):
    text = str(value or "").strip()
    if not text or "\0" in text:
        raise RecentFormatError("Recent file path is invalid.")
    return os.path.abspath(os.path.expanduser(text))


def _stamp(value):
    if value is None:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecentFormatError("Recent last-used time is invalid.") from exc
    if when.tzinfo is None:
        raise RecentFormatError("Recent last-used time must include a timezone.")
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RecentDestination:
    kind: str
    target: str
    label: str
    details: str = ""
    last_used: str | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def identity(self):
        return self.kind, os.path.normcase(self.target) if self.kind != "location" else self.target

    @property
    def last_used_label(self):
        if self.last_used is None:
            return "Last used: not recorded (older history)"
        when = datetime.fromisoformat(self.last_used)
        return f"Last used: {when:%Y-%m-%d %H:%M:%S} UTC"

    def available(self):
        if self.kind == "location":
            return True
        try:
            return os.path.isfile(self.target)
        except (OSError, ValueError):
            return False


def destination(kind, target=None, *, label=None, details="", last_used=None, lat=None, lon=None):
    if not isinstance(kind, str) or kind not in LIMITS:
        raise RecentFormatError("Recent destination type is unsupported.")
    if kind == "location":
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise RecentFormatError("Recent point coordinates must be numbers, not flags.")
        try:
            location = SavedLocation.create(label or "Point", lat, lon)
        except ValueError as exc:
            raise RecentFormatError(str(exc)) from exc
        lat, lon = location.lat, location.lon
        target = f"{lat:.6f},{lon:.6f}"
        label = location.name
    else:
        target = _path(target)
        label = str(label or os.path.basename(target))
        lat, lon = None, None
    if not isinstance(details, str) or len(details) > 4096:
        raise RecentFormatError("Recent destination details are invalid.")
    if not isinstance(label, str) or not label.strip() or len(label) > 512:
        raise RecentFormatError("Recent destination name is invalid.")
    return RecentDestination(kind, target, label.strip(), details, _stamp(last_used), lat, lon)


def _bounded(entries):
    counts, seen, result = {kind: 0 for kind in LIMITS}, set(), []
    for entry in sorted(entries, key=lambda item: item.last_used or "", reverse=True):
        if entry.identity in seen or counts[entry.kind] >= LIMITS[entry.kind]:
            continue
        seen.add(entry.identity)
        counts[entry.kind] += 1
        result.append(entry)
    return tuple(result)


class RecentDestinationStore:
    def __init__(self, settings):
        self.settings = settings

    def legacy(self, *, locations=()):
        entries = []
        for path in self.settings.value("recent_files", [], list) or []:
            try:
                entries.append(destination("file", path))
            except (RecentFormatError, OSError):
                continue
        for location in locations:
            entries.append(destination("location", label=location.name, lat=location.lat, lon=location.lon))
        return _bounded(entries)

    def load(self, *, locations=()):
        raw = self.settings.value(SETTINGS_KEY, "", str)
        entries = []
        if raw:
            try:
                document = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RecentFormatError("Recent history could not be read.") from exc
            if not isinstance(document, dict) or document.get("format") != FORMAT:
                raise RecentFormatError("Recent history format is invalid.")
            if type(document.get("version")) is not int or document.get("version") != VERSION:
                raise RecentFormatError("Recent history version is unsupported; existing history was kept.")
            records = document.get("destinations")
            if not isinstance(records, list) or len(records) > 1000:
                raise RecentFormatError("Recent destination list is invalid.")
            for record in records:
                if not isinstance(record, dict):
                    raise RecentFormatError("Recent destination entry is invalid.")
                if not isinstance(record.get("label"), str) or not isinstance(record.get("target"), str):
                    raise RecentFormatError("Recent destination name or path is invalid.")
                entries.append(destination(record.get("kind"), record.get("target"),
                                           label=record.get("label"), details=record.get("details", ""),
                                           last_used=record.get("last_used"), lat=record.get("lat"), lon=record.get("lon")))
        return _bounded((*entries, *self.legacy(locations=locations)))

    def remember(self, kind, target=None, *, label=None, details="", lat=None, lon=None, now=None):
        when = now or datetime.now(timezone.utc)
        entry = destination(kind, target, label=label, details=details, lat=lat, lon=lon,
                            last_used=when.isoformat())
        previous = self.load()
        result = _bounded((entry, *(item for item in previous if item.identity != entry.identity)))
        self.settings.setValue(SETTINGS_KEY, json.dumps({"format": FORMAT, "version": VERSION,
                                                       "destinations": [asdict(item) for item in result]},
                                                      ensure_ascii=False, sort_keys=True))
        self.settings.sync()
        return entry


def describe_collection(collection, *, fallback="Sounding"):
    """Describe only recorded metadata, without decoding/recalculating anything."""
    parts = []
    for key, title in (("loc", "Location"), ("model", "Model"), ("run", "Initialization")):
        try:
            value = collection.getMeta(key)
        except (AttributeError, KeyError, TypeError, ValueError):
            value = None
        if isinstance(value, datetime):
            # SHARPpy's naive collection dates are UTC by its existing contract.
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            value = value.strftime("%Y-%m-%d %H:%M UTC")
        if value not in (None, ""):
            parts.append(f"{title}: {value}")
    try:
        valid = collection.getCurrentDate()
    except (AttributeError, IndexError, TypeError, ValueError):
        valid = None
    if isinstance(valid, datetime):
        valid = valid.replace(tzinfo=timezone.utc) if valid.tzinfo is None else valid.astimezone(timezone.utc)
        parts.append(f"Valid: {valid:%Y-%m-%d %H:%M UTC}")
    return " · ".join(parts) if parts else fallback
