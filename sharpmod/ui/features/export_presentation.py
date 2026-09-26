"""Shared presentation, naming, and discovery rules for image exports.

This module deliberately contains no Qt imports.  Static PNG, GIF, and future
map exporters can therefore agree on exact pixel dimensions, safe names, and
recent-artifact metadata without sharing GUI implementation details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable

from sharpmod.export_paths import export_directory


MIN_EXPORT_DIMENSION = 320
MAX_EXPORT_DIMENSION = 4096
MAX_EXPORT_PIXELS = 12_000_000
MAX_EXPORT_FILENAME = 180
RECENT_EXPORT_LIMIT = 12
RECENT_EXPORTS_KEY = "exports/recent"
RECENT_EXPORTS_FORMAT = "sharpmod-recent-exports"
RECENT_EXPORTS_VERSION = 1


@dataclass(frozen=True)
class DimensionPreset:
    key: str
    label: str
    width: int
    height: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


DIMENSION_PRESETS = (
    DimensionPreset("full-hd", "Full HD · 16:9 · 1920 × 1080", 1920, 1080),
    DimensionPreset("presentation", "Presentation · 4:3 · 1600 × 1200", 1600, 1200),
    DimensionPreset("square", "Square · 1:1 · 1200 × 1200", 1200, 1200),
    DimensionPreset("print", "Print landscape · 3:2 · 2400 × 1600", 2400, 1600),
)
PRESET_BY_KEY = {item.key: item for item in DIMENSION_PRESETS}


def _dimension(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"export {field} must be a pixel count")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"export {field} must be a pixel count") from exc
    if not MIN_EXPORT_DIMENSION <= result <= MAX_EXPORT_DIMENSION:
        raise ValueError(
            f"export {field} must be between {MIN_EXPORT_DIMENSION} and "
            f"{MAX_EXPORT_DIMENSION} pixels"
        )
    return result


@dataclass(frozen=True)
class ExportPresentation:
    """Choices that change exported pixels, never the destination directory."""

    width: int = 1920
    height: int = 1080
    preset: str = "full-hd"
    include_caption: bool = True
    theme: str = "current"
    lock_aspect: bool = True

    def __post_init__(self):
        object.__setattr__(self, "width", _dimension(self.width, field="width"))
        object.__setattr__(self, "height", _dimension(self.height, field="height"))
        if self.width * self.height > MAX_EXPORT_PIXELS:
            raise ValueError(
                f"export dimensions exceed the {MAX_EXPORT_PIXELS:,}-pixel safety limit"
            )
        preset = str(self.preset or "custom")
        if preset not in {*PRESET_BY_KEY, "custom"}:
            preset = "custom"
        object.__setattr__(self, "preset", preset)
        if not isinstance(self.include_caption, bool):
            raise ValueError("include_caption must be a boolean")
        if not isinstance(self.lock_aspect, bool):
            raise ValueError("lock_aspect must be a boolean")
        if self.theme not in {"current", "dark", "light"}:
            object.__setattr__(self, "theme", "current")

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def aspect_label(self) -> str:
        from math import gcd

        common = gcd(self.width, self.height)
        return f"{self.width // common}:{self.height // common}"


def presentation_settings_key(namespace: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "-", str(namespace).strip().lower()).strip("-")
    if not safe:
        raise ValueError("an export presentation namespace is required")
    return f"exports/presentation/{safe}"


def load_presentation(
    settings: Any | None,
    namespace: str,
    *,
    default: ExportPresentation | None = None,
) -> ExportPresentation:
    """Read a choices-only presentation; malformed settings use ``default``."""

    fallback = default or ExportPresentation()
    if settings is None:
        return fallback
    try:
        raw = settings.value(presentation_settings_key(namespace), "", str)
    except (AttributeError, RuntimeError, TypeError):
        return fallback
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return fallback
        return ExportPresentation(
            width=value.get("width", fallback.width),
            height=value.get("height", fallback.height),
            preset=value.get("preset", fallback.preset),
            include_caption=value.get("include_caption", fallback.include_caption),
            theme=value.get("theme", fallback.theme),
            lock_aspect=value.get("lock_aspect", fallback.lock_aspect),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def save_presentation(
    settings: Any | None,
    namespace: str,
    presentation: ExportPresentation,
) -> None:
    """Persist pixel choices only; no path is accepted or written here."""

    if settings is None:
        return
    payload = json.dumps(asdict(presentation), sort_keys=True, separators=(",", ":"))
    try:
        settings.setValue(presentation_settings_key(namespace), payload)
        settings.sync()
    except (AttributeError, OSError, RuntimeError, TypeError):
        return


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_token(value: Any) -> str | None:
    value = _utc(value)
    return value.strftime("%Y%m%dT%H%MZ") if value is not None else None


def _metadata(collection: Any, key: str, default: Any = None) -> Any:
    try:
        value = collection.getMeta(key)
    except Exception:
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value in (None, "") else value


@dataclass(frozen=True)
class ExportIdentity:
    location: str = "sounding"
    source: str = ""
    initialization: datetime | None = None
    initialization_end: datetime | None = None
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    detail: str = ""

    @property
    def caption(self) -> str:
        pieces = [str(self.location or "Sounding")]
        if self.source:
            pieces.append(str(self.source))
        if self.initialization is not None:
            start = _utc(self.initialization)
            end = _utc(self.initialization_end)
            pieces.append(
                f"init {start:%Y-%m-%d %H:%MZ}–{end:%Y-%m-%d %H:%MZ}"
                if end is not None and end != start
                else f"init {start:%Y-%m-%d %H:%MZ}"
            )
        if self.valid_start is not None:
            start = _utc(self.valid_start)
            end = _utc(self.valid_end)
            if end is not None and end != start:
                pieces.append(
                    f"valid {start:%Y-%m-%d %H:%MZ}–{end:%Y-%m-%d %H:%MZ}"
                )
            else:
                pieces.append(f"valid {start:%Y-%m-%d %H:%MZ}")
        if self.detail:
            pieces.append(str(self.detail))
        return " · ".join(pieces)


def export_identity(
    collection: Any,
    *,
    valid_times: Iterable[datetime | None] = (),
    run_times: Iterable[datetime | None] = (),
) -> ExportIdentity:
    """Resolve only recorded collection metadata into export identity."""

    values = tuple(value for value in (_utc(item) for item in valid_times) if value)
    if not values:
        try:
            current = collection.getCurrentDate()
        except Exception:
            dates = tuple(getattr(collection, "_dates", ()) or ())
            try:
                current = dates[int(getattr(collection, "_prof_idx", 0) or 0)]
            except (IndexError, TypeError, ValueError):
                current = None
        if _utc(current) is not None:
            values = (_utc(current),)
    model = str(_metadata(collection, "model", "") or "").strip()
    provider = str(
        _metadata(collection, "source", "")
        or _metadata(collection, "provider", "")
        or ""
    ).strip()
    source = "-".join(dict.fromkeys(item for item in (model, provider) if item))
    runs = tuple(value for value in (_utc(item) for item in run_times) if value)
    if not runs:
        run = _utc(_metadata(collection, "run"))
        runs = (run,) if run is not None else ()
    return ExportIdentity(
        location=str(_metadata(collection, "loc", "sounding")),
        source=source,
        initialization=min(runs) if runs else None,
        initialization_end=max(runs) if runs else None,
        valid_start=min(values) if values else None,
        valid_end=max(values) if values else None,
    )


_UNSAFE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]+")
_SEPARATORS = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED = re.compile(r"[-_.]{2,}")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_filename(value: Any, *, fallback: str = "export") -> str:
    """Return one portable filename component without path traversal."""

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    text = _UNSAFE.sub("-", ascii_text)
    text = _SEPARATORS.sub("-", text)
    text = _REPEATED.sub("-", text).strip(" .-_")
    if not text:
        text = fallback
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def _short_filename(name: str, *, limit: int = MAX_EXPORT_FILENAME) -> str:
    path = Path(name)
    extension = path.suffix.lower()
    stem = path.stem
    if len(name) <= limit:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    room = max(12, limit - len(extension) - len(digest) - 1)
    return f"{stem[:room].rstrip(' .-_')}-{digest}{extension}"


def export_filename(
    identity: ExportIdentity,
    *,
    kind: str,
    extension: str,
) -> str:
    """Build an understandable, portable and length-bounded export name."""

    pieces = [sanitize_filename(identity.location, fallback="sounding")[:24].rstrip(" .-_")]
    if identity.source:
        pieces.append(sanitize_filename(identity.source, fallback="source")[:24].rstrip(" .-_"))
    init = _time_token(identity.initialization)
    if init:
        init_end = _time_token(identity.initialization_end)
        pieces.append(
            f"init-{init}-to-{init_end}"
            if init_end and init_end != init else f"init-{init}"
        )
    start = _time_token(identity.valid_start)
    end = _time_token(identity.valid_end)
    if start:
        pieces.append(f"valid-{start}" if not end or end == start else f"valid-{start}-to-{end}")
    if identity.detail:
        pieces.append(
            f"detail-{sanitize_filename(identity.detail, fallback='detail')[:20].rstrip(' .-_')}"
        )
    pieces.append(sanitize_filename(kind, fallback="export")[:16].rstrip(" .-_"))
    suffix = "." + str(extension).lstrip(".").lower()
    flexible = {0, len(pieces) - 1}
    if identity.source:
        flexible.add(1)
    while len("-".join(pieces) + suffix) > MAX_EXPORT_FILENAME:
        candidates = [
            index for index, piece in enumerate(pieces)
            if index in flexible and len(piece) > 10
        ]
        if not candidates:
            break
        target = max(candidates, key=lambda index: len(pieces[index]))
        pieces[target] = pieces[target][:-1].rstrip(" .-_")
    return _short_filename("-".join(pieces) + suffix)


def available_export_path(
    filename: str | os.PathLike[str],
    *,
    settings: Any | None = None,
    directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Return a non-colliding suggestion without changing the default folder."""

    name = _short_filename(sanitize_filename(Path(filename).stem) + Path(filename).suffix)
    root = Path(directory) if directory is not None else export_directory(settings=settings)
    candidate = root / name
    index = 2
    while candidate.exists():
        extension = candidate.suffix
        stem_limit = MAX_EXPORT_FILENAME - len(extension) - len(str(index)) - 1
        stem = Path(name).stem[:stem_limit].rstrip(" .-_")
        candidate = root / f"{stem}-{index}{extension}"
        index += 1
    return candidate


@dataclass(frozen=True)
class RecentExport:
    path: str
    kind: str
    summary: str
    width: int | None
    height: int | None
    completed_at: str

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def exists(self) -> bool:
        try:
            return Path(self.path).is_file() and Path(self.path).stat().st_size > 0
        except OSError:
            return False


def _parse_recent_record(value: Any) -> RecentExport | None:
    if not isinstance(value, dict):
        return None
    try:
        path = os.path.abspath(os.path.expanduser(str(value["path"])))
        kind = str(value["kind"])
        summary = str(value.get("summary", ""))
        completed = datetime.fromisoformat(
            str(value["completed_at"]).replace("Z", "+00:00")
        )
        if completed.tzinfo is None:
            return None
        width = value.get("width")
        height = value.get("height")
        width = int(width) if width is not None else None
        height = int(height) if height is not None else None
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if not path or "\0" in path or not kind or len(summary) > 4096:
        return None
    return RecentExport(
        path,
        kind,
        summary,
        width,
        height,
        completed.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def recent_exports(settings: Any | None) -> tuple[RecentExport, ...]:
    if settings is None:
        return ()
    try:
        raw = settings.value(RECENT_EXPORTS_KEY, "", str)
    except (AttributeError, RuntimeError, TypeError):
        return ()
    if not raw:
        return ()
    try:
        document = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ()
    if (
        not isinstance(document, dict)
        or document.get("format") != RECENT_EXPORTS_FORMAT
        or document.get("version") != RECENT_EXPORTS_VERSION
        or not isinstance(document.get("exports"), list)
    ):
        return ()
    records = []
    seen = set()
    for raw_record in document["exports"][: RECENT_EXPORT_LIMIT * 4]:
        record = _parse_recent_record(raw_record)
        identity = os.path.normcase(record.path) if record is not None else None
        if record is None or identity in seen:
            continue
        seen.add(identity)
        records.append(record)
        if len(records) >= RECENT_EXPORT_LIMIT:
            break
    return tuple(records)


def remember_export(
    settings: Any | None,
    path: str | os.PathLike[str],
    *,
    kind: str,
    summary: str = "",
    dimensions: tuple[int, int] | None = None,
    now: datetime | None = None,
) -> RecentExport:
    """Remember only an existing, non-empty finished artifact."""

    target = Path(path).expanduser().resolve()
    try:
        if not target.is_file() or target.stat().st_size <= 0:
            raise ValueError(f"completed export does not exist or is empty: {target}")
    except OSError as exc:
        raise ValueError(f"completed export cannot be inspected: {target}") from exc
    completed = _utc(now or datetime.now(timezone.utc))
    width, height = dimensions if dimensions is not None else (None, None)
    record = RecentExport(
        str(target),
        str(kind),
        str(summary),
        int(width) if width is not None else None,
        int(height) if height is not None else None,
        completed.isoformat(timespec="seconds"),
    )
    if settings is None:
        return record
    previous = recent_exports(settings)
    normalized = os.path.normcase(record.path)
    records = (record, *(item for item in previous if os.path.normcase(item.path) != normalized))
    document = {
        "format": RECENT_EXPORTS_FORMAT,
        "version": RECENT_EXPORTS_VERSION,
        "exports": [asdict(item) for item in records[:RECENT_EXPORT_LIMIT]],
    }
    try:
        settings.setValue(
            RECENT_EXPORTS_KEY,
            json.dumps(document, ensure_ascii=False, sort_keys=True),
        )
        settings.sync()
    except (AttributeError, OSError, RuntimeError, TypeError):
        pass
    return record


__all__ = [
    "DIMENSION_PRESETS",
    "MAX_EXPORT_DIMENSION",
    "MAX_EXPORT_PIXELS",
    "MIN_EXPORT_DIMENSION",
    "DimensionPreset",
    "ExportIdentity",
    "ExportPresentation",
    "RecentExport",
    "available_export_path",
    "export_filename",
    "export_identity",
    "load_presentation",
    "presentation_settings_key",
    "recent_exports",
    "remember_export",
    "sanitize_filename",
    "save_presentation",
]
