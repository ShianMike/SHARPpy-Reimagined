"""Versioned, source-neutral saved geographic boxes (T23.5).

Only the geographic bounds are persisted.  A saved area deliberately carries
no model, cycle, forecast hour, ensemble member, or extraction mode, so
restoring it cannot silently switch the data source the user is working with.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from sharpmod.analysis.box_sounding import BoxRegion, BoxRegionError


BOX_REGION_FORMAT = "sharpmod-saved-box-regions"
BOX_REGION_VERSION = 1
BOX_REGION_SETTINGS_KEY = "regions/saved_boxes"
MAX_SAVED_BOX_REGIONS = 100


class SavedBoxRegionFormatError(ValueError):
    """Raised when saved geographic-area state is malformed."""


@dataclass(frozen=True)
class SavedBoxRegion:
    """One named geographic rectangle, including an unambiguous lon span."""

    name: str
    lat0: float
    lat1: float
    lon0: float
    lon_span: float

    @classmethod
    def create(cls, name, lat0, lat1, lon0, lon_span) -> "SavedBoxRegion":
        label = str(name or "").strip()
        if not label:
            raise SavedBoxRegionFormatError("area name cannot be empty")
        try:
            region = BoxRegion(
                lat0=float(lat0),
                lat1=float(lat1),
                lon0=float(lon0),
                lon_span=float(lon_span),
            )
        except (BoxRegionError, TypeError, ValueError) as exc:
            raise SavedBoxRegionFormatError(str(exc)) from exc
        return cls(
            name=label,
            lat0=region.lat0,
            lat1=region.lat1,
            lon0=region.lon0,
            lon_span=region.lon_span,
        )

    @classmethod
    def from_region(cls, name, region: BoxRegion) -> "SavedBoxRegion":
        if not isinstance(region, BoxRegion):
            raise SavedBoxRegionFormatError("saved area must be a BoxRegion")
        return cls.create(
            name, region.lat0, region.lat1, region.lon0, region.lon_span
        )

    @property
    def region(self) -> BoxRegion:
        return BoxRegion(
            lat0=self.lat0,
            lat1=self.lat1,
            lon0=self.lon0,
            lon_span=self.lon_span,
        )

    @property
    def map_corners(self) -> tuple[float, float, float, float]:
        """Bounds for the map, with the east longitude deliberately unwrapped."""
        return (self.lat0, self.lon0, self.lat1, self.lon0 + self.lon_span)


def _document(regions) -> dict:
    return {
        "format": BOX_REGION_FORMAT,
        "version": BOX_REGION_VERSION,
        "regions": [asdict(region) for region in regions],
    }


def _parse_document(value) -> list[SavedBoxRegion]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SavedBoxRegionFormatError("saved-area JSON is invalid") from exc
    if not isinstance(value, dict):
        raise SavedBoxRegionFormatError("saved-area document must be an object")
    if value.get("format") != BOX_REGION_FORMAT:
        raise SavedBoxRegionFormatError("not a SHARPpy Reimagined saved-area document")
    if value.get("version") != BOX_REGION_VERSION:
        raise SavedBoxRegionFormatError(
            f"unsupported saved-area version: {value.get('version')}"
        )
    records = value.get("regions")
    if not isinstance(records, list):
        raise SavedBoxRegionFormatError("saved-area list is missing")
    result = []
    names = set()
    for record in records:
        if not isinstance(record, dict):
            raise SavedBoxRegionFormatError("saved-area entry must be an object")
        region = SavedBoxRegion.create(
            record.get("name"),
            record.get("lat0"),
            record.get("lat1"),
            record.get("lon0"),
            record.get("lon_span"),
        )
        folded = region.name.casefold()
        if folded in names:
            raise SavedBoxRegionFormatError(
                f"duplicate saved-area name: {region.name}"
            )
        names.add(folded)
        result.append(region)
    return result


class SavedBoxRegionStore:
    """Persist named bounds in any QSettings-compatible object."""

    def __init__(self, settings, *, key=BOX_REGION_SETTINGS_KEY,
                 max_entries=MAX_SAVED_BOX_REGIONS):
        self.settings = settings
        self.key = str(key)
        self.max_entries = max(1, int(max_entries))

    def load(self) -> list[SavedBoxRegion]:
        raw = self.settings.value(self.key, "", str)
        if not str(raw or "").strip():
            return []
        return _parse_document(raw)

    def save(self, regions) -> list[SavedBoxRegion]:
        normalized = []
        for item in regions:
            if isinstance(item, SavedBoxRegion):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(SavedBoxRegion.create(
                    item.get("name"), item.get("lat0"), item.get("lat1"),
                    item.get("lon0"), item.get("lon_span")))
            else:
                raise SavedBoxRegionFormatError("saved area must be a mapping")
        # Last writer wins without allowing names that differ only by case.
        deduplicated = {}
        for item in normalized:
            deduplicated[item.name.casefold()] = item
        result = list(deduplicated.values())[-self.max_entries:]
        self.settings.setValue(
            self.key,
            json.dumps(_document(result), ensure_ascii=False, sort_keys=True),
        )
        sync = getattr(self.settings, "sync", None)
        if callable(sync):
            sync()
        return result

    def upsert(self, name, region: BoxRegion) -> SavedBoxRegion:
        saved = SavedBoxRegion.from_region(name, region)
        remaining = [
            item for item in self.load()
            if item.name.casefold() != saved.name.casefold()
        ]
        self.save([*remaining, saved])
        return saved

    def remove(self, name) -> bool:
        folded = str(name or "").strip().casefold()
        before = self.load()
        after = [item for item in before if item.name.casefold() != folded]
        if len(after) == len(before):
            return False
        self.save(after)
        return True

    def duplicate(self, name, *, region: BoxRegion | None = None) -> SavedBoxRegion:
        loaded = self.load()
        folded = str(name or "").strip().casefold()
        source = next((item for item in loaded if item.name.casefold() == folded), None)
        if source is None and region is None:
            raise SavedBoxRegionFormatError("choose a saved area to duplicate")
        source_region = region if region is not None else source.region
        base = (source.name if source is not None else str(name or "").strip()) or "Area"
        used = {item.name.casefold() for item in loaded}
        candidate = f"{base} copy"
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base} copy {suffix}"
            suffix += 1
        duplicate = SavedBoxRegion.from_region(candidate, source_region)
        self.save([*loaded, duplicate])
        return duplicate


__all__ = [
    "BOX_REGION_FORMAT",
    "BOX_REGION_SETTINGS_KEY",
    "BOX_REGION_VERSION",
    "MAX_SAVED_BOX_REGIONS",
    "SavedBoxRegion",
    "SavedBoxRegionFormatError",
    "SavedBoxRegionStore",
]
