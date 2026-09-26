"""Read-only profile identities and organization; never reorder source data."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import unicodedata


ORGANIZE_FIELDS = (("Loaded order", "loaded"), ("Location", "location"),
                   ("Source / model", "source"), ("Initialization", "initialization"),
                   ("Valid time", "valid_time"))


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return default
    return default if value is None else value


def _utc(value):
    if not isinstance(value, datetime):
        return None
    # SHARPpy's naive profile dates have always represented UTC.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold()
                   if not unicodedata.combining(c))


def format_time(value):
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "not reported"


@dataclass(frozen=True)
class CollectionIdentity:
    profile_id: str
    loaded_index: int
    location: str
    model: str
    provider: str
    initialization: datetime | None
    valid_time: datetime | None
    kind: str
    edited: bool = False
    parent: str = ""

    @property
    def source(self):
        return " · ".join(part for part in (self.model, self.provider) if part) or "Source not reported"

    @property
    def type_label(self):
        return f"Edited {self.kind.lower()}" if self.edited else self.kind

    @property
    def label(self):
        place = self.location or self.profile_id
        if not any((self.location, self.model, self.provider, self.initialization, self.valid_time, self.parent)) and self.kind == "Type not reported":
            return place
        lines = [place, " · ".join((self.type_label, self.source))]
        if self.initialization:
            lines.append(f"Init: {format_time(self.initialization)}")
        lines.append(f"Valid: {format_time(self.valid_time)}")
        if self.parent:
            lines.append(f"Parent / source: {self.parent}")
        return "\n".join(lines)

    @property
    def details(self):
        return f"{self.label}\nType: {self.type_label}\nSource: {self.source}\nProfile ID: {self.profile_id}"

    def matches(self, query):
        haystack = _normalized(self.details)
        return all(token in haystack for token in _normalized(query).split())

    def field(self, key):
        if key == "loaded":
            return self.loaded_index
        if key == "source":
            return " · ".join(part for part in (self.model, self.provider) if part)
        return getattr(self, key)


def describe_identity(profile_id, collection, loaded_index=0):
    location = _text(_meta(collection, "loc"))
    model = _text(_meta(collection, "model"))
    provider = next((_text(value) for key in ("source_provider_name", "provider_name", "source_provider", "provider")
                     if (value := _meta(collection, key)) is not None and _text(value)), "")
    initialization = _utc(_meta(collection, "run"))
    try:
        valid = _utc(collection.getCurrentDate())
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        valid = _utc(_meta(collection, "valid"))
    observed = _meta(collection, "observed")
    explicit = _text(_meta(collection, "profile_kind")).lower()
    if explicit in {"observed", "forecast", "reanalysis"}:
        kind = explicit.capitalize()
    elif model.upper() in {"ERA5", "ERA-5"} or _text(_meta(collection, "model_key")).lower() == "era5":
        kind = "Reanalysis"
    elif observed is True:
        kind = "Observed"
    elif observed is False:
        kind = "Forecast"
    else:
        kind = "Type not reported"
    if kind in {"Observed", "Reanalysis"}:
        initialization = None
    edited = _meta(collection, "profile_edited") is True
    for method in ("isModified", "isInterpolated"):
        try:
            edited = edited or bool(getattr(collection, method)())
        except (AttributeError, IndexError, TypeError, ValueError):
            pass
    # Inspect already-loaded data only: getHighlightedProf can lazily calculate
    # and replace a source profile, which search/organization must never do.
    try:
        profile = collection._profs[collection._highlight][collection._prof_idx]
        motion, baseline = profile.user_srwind, profile.bunkers
        if motion is not None and motion is not baseline:
            for actual, original in zip(motion, baseline):
                if getattr(actual, "mask", False) or getattr(original, "mask", False):
                    continue
                if math.isfinite(float(actual)) and math.isfinite(float(original)):
                    edited = edited or not math.isclose(float(actual), float(original), rel_tol=1e-10, abs_tol=1e-10)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        pass
    parent = _text(_meta(collection, "parent_profile_id"))
    if not parent and edited:
        parent = "Loaded source baseline (modified in this profile)"
    return CollectionIdentity(str(profile_id), loaded_index, location, model, provider,
                              initialization, valid, kind, edited, parent)


def collection_identities(widget):
    collections = tuple(getattr(widget, "prof_collections", ()) or ())
    return tuple(describe_identity(profile_id, collections[index] if index < len(collections) else None, index)
                 for index, profile_id in enumerate(getattr(widget, "prof_ids", ()) or ()))


def organize_identities(identities, *, query="", sort="loaded", reverse=False, group="none"):
    """Return (group label, identity) rows; None identities are headings only."""
    keys = {key for _label, key in ORGANIZE_FIELDS}
    sort = sort if sort in keys else "loaded"
    group = group if group in keys - {"loaded"} else "none"
    entries = [entry for entry in identities if entry.matches(query)]
    known = [entry for entry in entries if entry.field(sort) not in (None, "")]
    unknown = [entry for entry in entries if entry.field(sort) in (None, "")]
    known.sort(key=lambda entry: (_normalized(entry.field(sort)) if isinstance(entry.field(sort), str)
                                 else entry.field(sort)), reverse=reverse)
    entries = known + unknown
    if group == "none":
        return tuple(("", entry) for entry in entries)
    groups = {}
    for entry in entries:
        value = entry.field(group)
        label = (f"Not applicable ({entry.kind.lower()})" if group == "initialization" and entry.kind in {"Observed", "Reanalysis"}
                 else format_time(value) if group in {"initialization", "valid_time"} else value or "Not reported")
        groups.setdefault(label, []).append(entry)
    return tuple(row for label in sorted(groups, key=_normalized)
                 for row in [(label, None), *((label, entry) for entry in groups[label])])
