"""Historical replay clocks and safe, portable offline case packages.

Case packages are ZIP containers treated strictly as data.  Members are never
imported or executed, paths are not extracted, and every packaged asset is
verified against its SHA-256 digest before it is exposed to a caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType

FORMAT = "sharpmod-portable-case"
VERSION = 1
MAX_ASSETS = 256
MAX_ASSET_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
MAX_DOWNLOAD_WORKERS = 4
ALLOWED_KINDS = frozenset(
    {
        "sounding-session",
        "sounding",
        "radar-level3",
        "radar-image",
        "outlook",
        "storm-reports",
        "satellite",
        "surface-observations",
        "wind-profile",
        "notes",
    }
)
PROVIDER_CAPABILITIES = MappingProxyType(
    {
        "radar-level3": (
            "NOAA/NCEI NEXRAD Level III archive; archive product time is known, "
            "original forecaster-availability time may be unknown"
        ),
        "outlook": "NOAA/SPC archive; issue and valid times are retained",
        "storm-reports": (
            "NOAA/SPC or IEM historical report feeds; report/event time is retained"
        ),
        "satellite": ("NOAA GOES ABI archive or packaged frame; scan time is retained"),
        "surface-observations": (
            "NWS observations API or packaged JSON; observation timestamp is retained"
        ),
    }
)


class CaseError(ValueError):
    """A case manifest, asset, or package violates the data contract."""


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value) -> str:
    parsed = _time(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else ""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_id(value) -> str:
    text = str(value).strip()
    if not text or len(text) > 120:
        raise CaseError("case asset id must contain 1-120 characters")
    if not all(character.isalnum() or character in "-_." for character in text):
        raise CaseError(f"case asset id contains unsafe characters: {text!r}")
    return text


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else bool(cancel)


@dataclass(frozen=True)
class CaseAsset:
    asset_id: str
    kind: str
    event_time: datetime | None
    available_time: datetime | None
    source_url: str
    package_name: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    status: str = "planned"
    note: str = ""

    def __post_init__(self):
        asset_id = _safe_id(self.asset_id)
        kind = str(self.kind).strip().lower()
        if kind not in ALLOWED_KINDS:
            raise CaseError(f"unsupported case asset kind: {kind!r}")
        status = str(self.status).strip().lower()
        if status not in {"planned", "complete", "missing", "failed", "cancelled"}:
            raise CaseError(f"unsupported case asset status: {status!r}")
        package_name = self.package_name
        if package_name is not None:
            package_name = str(PurePosixPath(str(package_name).replace("\\", "/")))
            path = PurePosixPath(package_name)
            if path.is_absolute() or ".." in path.parts or not package_name:
                raise CaseError("case package member path is unsafe")
        digest = str(self.sha256).lower() if self.sha256 else None
        if digest is not None and (
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise CaseError("asset SHA-256 digest is invalid")
        size = None if self.size_bytes is None else int(self.size_bytes)
        if size is not None and not 0 <= size <= MAX_ASSET_BYTES:
            raise CaseError("asset size is outside the package safety limit")
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "event_time", _time(self.event_time))
        object.__setattr__(self, "available_time", _time(self.available_time))
        object.__setattr__(self, "source_url", str(self.source_url or ""))
        object.__setattr__(self, "package_name", package_name)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "size_bytes", size)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "note", str(self.note or ""))

    @property
    def availability_known(self) -> bool:
        return self.available_time is not None

    def as_dict(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "event_time": _iso(self.event_time),
            "available_time": _iso(self.available_time),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        try:
            return cls(**dict(payload))
        except (TypeError, ValueError) as exc:
            raise CaseError(f"case asset is malformed: {exc}") from exc


@dataclass(frozen=True)
class CaseManifest:
    name: str
    assets: tuple[CaseAsset, ...]
    notes: str = ""
    created_at: datetime | None = None
    source_session_version: int | None = None

    def __post_init__(self):
        name = str(self.name).strip()
        assets = tuple(self.assets)
        if not name:
            raise CaseError("case name is required")
        if len(assets) > MAX_ASSETS:
            raise CaseError("case contains too many assets")
        ids = [asset.asset_id for asset in assets]
        if len(set(ids)) != len(ids):
            raise CaseError("case asset ids must be unique")
        package_names = [asset.package_name for asset in assets if asset.package_name]
        if len(set(package_names)) != len(package_names):
            raise CaseError("case package member names must be unique")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "notes", str(self.notes or ""))
        object.__setattr__(
            self, "created_at", _time(self.created_at) or datetime.now(timezone.utc)
        )

    @property
    def replay_times(self) -> tuple[datetime, ...]:
        return tuple(
            sorted({asset.event_time for asset in self.assets if asset.event_time})
        )

    @property
    def missing_assets(self) -> tuple[CaseAsset, ...]:
        return tuple(asset for asset in self.assets if asset.status != "complete")

    @property
    def training_limitations(self) -> tuple[str, ...]:
        return tuple(
            f"{asset.asset_id}: availability time is unknown"
            for asset in self.assets
            if asset.available_time is None
        )

    def visible_assets(self, when, *, training_mode=False) -> tuple[CaseAsset, ...]:
        """Return assets revealed by event time or known availability time."""
        selected = _time(when)
        if selected is None:
            raise CaseError("replay selection requires an exact UTC time")
        visible = []
        for asset in self.assets:
            if asset.status != "complete":
                continue
            boundary = asset.available_time if training_mode else asset.event_time
            # Unknown availability cannot be treated as if it were known in a
            # faithful training reconstruction.
            if boundary is not None and boundary <= selected:
                visible.append(asset)
        return tuple(visible)

    def as_dict(self) -> dict[str, object]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "name": self.name,
            "created_at": _iso(self.created_at),
            "source_session_version": self.source_session_version,
            "notes": self.notes,
            "provider_capabilities": dict(PROVIDER_CAPABILITIES),
            "assets": [asset.as_dict() for asset in self.assets],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        if payload.get("format") != FORMAT or payload.get("version") != VERSION:
            raise CaseError("unsupported portable case manifest")
        try:
            return cls(
                name=str(payload["name"]),
                created_at=_time(payload.get("created_at")),
                source_session_version=(
                    int(payload["source_session_version"])
                    if payload.get("source_session_version") is not None
                    else None
                ),
                notes=str(payload.get("notes", "")),
                assets=tuple(
                    CaseAsset.from_dict(item) for item in payload.get("assets", ())
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CaseError(f"portable case manifest is malformed: {exc}") from exc


class ReplayClock:
    """Pause, step, and advance across exact archived product times."""

    def __init__(self, times=(), *, loop=False):
        resolved = tuple(sorted({_time(item) for item in times if _time(item)}))
        self._times = resolved
        self._index = 0
        self.loop = bool(loop)
        self.playing = False

    @property
    def times(self):
        return self._times

    @property
    def current(self):
        return self._times[self._index] if self._times else None

    @property
    def index(self):
        return self._index

    def pause(self):
        self.playing = False
        return self.current

    def play(self):
        self.playing = bool(self._times)
        return self.current

    def select(self, when):
        selected = _time(when)
        if selected not in self._times:
            raise CaseError("replay time is not one of the archived exact times")
        self._index = self._times.index(selected)
        return self.current

    def step(self, amount=1):
        if not self._times:
            return None
        target = self._index + int(amount)
        if self.loop:
            self._index = target % len(self._times)
        else:
            self._index = max(0, min(target, len(self._times) - 1))
            if target >= len(self._times):
                self.playing = False
        return self.current

    def tick(self):
        return self.step(1) if self.playing else self.current


@dataclass(frozen=True)
class DownloadedCase:
    manifest: CaseManifest
    files: Mapping[str, Path]
    cancelled: bool


def _download_one(asset, destination, opener, timeout, cancel=None):
    parsed = urllib.parse.urlparse(asset.source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CaseError("remote case assets require an HTTPS source URL")
    request = urllib.request.Request(
        asset.source_url,
        headers={"User-Agent": "SHARPpy-Reimagined/1.2 case downloader"},
    )
    response = (opener or urllib.request.urlopen)(request, timeout=timeout)
    digest = hashlib.sha256()
    size = 0
    with response, Path(destination).open("wb") as stream:
        while True:
            if _cancelled(cancel):
                raise CaseError("case asset download cancelled")
            chunk = response.read(min(256 * 1024, MAX_ASSET_BYTES + 1 - size))
            if not chunk:
                break
            chunk = bytes(chunk)
            size += len(chunk)
            if size > MAX_ASSET_BYTES:
                raise CaseError(
                    f"asset {asset.asset_id} exceeds the download safety limit"
                )
            stream.write(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def download_case_assets(
    manifest: CaseManifest,
    directory,
    *,
    cancel=None,
    opener=None,
    timeout=30.0,
    max_workers=MAX_DOWNLOAD_WORKERS,
    progress: Callable[[int, int, CaseAsset], None] | None = None,
) -> DownloadedCase:
    """Download planned assets with bounded concurrency and partial retention."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(max_workers), MAX_DOWNLOAD_WORKERS))
    assets = list(manifest.assets)
    results = {asset.asset_id: asset for asset in assets}
    files = {}
    remote = [asset for asset in assets if asset.status == "planned"]
    completed = 0
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for asset in remote:
            if _cancelled(cancel):
                results[asset.asset_id] = replace(asset, status="cancelled")
                continue
            suffix = Path(urllib.parse.urlparse(asset.source_url).path).suffix or ".bin"
            target = root / f"{asset.asset_id}{suffix}"
            partial = root / f".{asset.asset_id}{suffix}.part"
            futures[
                pool.submit(
                    _download_one,
                    asset,
                    partial,
                    opener,
                    float(timeout),
                    cancel,
                )
            ] = (asset, partial, target)
        for future in as_completed(futures):
            asset, partial, target = futures[future]
            if _cancelled(cancel):
                # Stop work which has not begun. A request already inside its
                # response read observes the same cooperative cancellation
                # callback between bounded chunks.
                for pending in futures:
                    pending.cancel()
                # A request already in flight may have completed. Preserve its
                # bytes rather than discarding work the user already paid for.
                try:
                    digest, size = future.result()
                except Exception:
                    partial.unlink(missing_ok=True)
                    results[asset.asset_id] = replace(asset, status="cancelled")
                else:
                    os.replace(partial, target)
                    files[asset.asset_id] = target
                    results[asset.asset_id] = replace(
                        asset,
                        status="complete",
                        package_name=f"assets/{target.name}",
                        sha256=digest,
                        size_bytes=size,
                    )
                continue
            try:
                digest, size = future.result()
            except Exception as exc:  # noqa: BLE001 - per-asset network boundary
                partial.unlink(missing_ok=True)
                results[asset.asset_id] = replace(asset, status="failed", note=str(exc))
            else:
                os.replace(partial, target)
                files[asset.asset_id] = target
                results[asset.asset_id] = replace(
                    asset,
                    status="complete",
                    package_name=f"assets/{target.name}",
                    sha256=digest,
                    size_bytes=size,
                )
            completed += 1
            if progress is not None:
                progress(completed, len(remote), results[asset.asset_id])
    ordered = tuple(results[asset.asset_id] for asset in assets)
    return DownloadedCase(
        replace(manifest, assets=ordered),
        MappingProxyType(dict(files)),
        _cancelled(cancel),
    )


def create_case_package(path, manifest: CaseManifest, files: Mapping[str, object]):
    """Create an atomic package, hashing every included byte from disk."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        updated = []
        total = 0
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for asset in manifest.assets:
                source = files.get(asset.asset_id)
                if source is None:
                    updated.append(
                        asset
                        if asset.status in {"missing", "failed", "cancelled"}
                        else replace(
                            asset,
                            status="missing",
                            note="asset was not packaged",
                        )
                    )
                    continue
                source_path = Path(source)
                try:
                    source_size = source_path.stat().st_size
                except OSError as exc:
                    raise CaseError(
                        f"case asset {asset.asset_id} could not be read: {exc}"
                    ) from exc
                if (
                    source_size > MAX_ASSET_BYTES
                    or total + source_size > MAX_PACKAGE_BYTES
                ):
                    raise CaseError("portable case exceeds the package safety limit")
                suffix = source_path.suffix or ".bin"
                member = f"assets/{asset.asset_id}{suffix}"
                digest = hashlib.sha256()
                written = 0
                with (
                    source_path.open("rb") as input_stream,
                    archive.open(member, "w") as output_stream,
                ):
                    while chunk := input_stream.read(256 * 1024):
                        written += len(chunk)
                        if (
                            written > MAX_ASSET_BYTES
                            or total + written > MAX_PACKAGE_BYTES
                        ):
                            raise CaseError(
                                "portable case exceeds the package safety limit"
                            )
                        output_stream.write(chunk)
                        digest.update(chunk)
                total += written
                updated.append(
                    replace(
                        asset,
                        package_name=member,
                        sha256=digest.hexdigest(),
                        size_bytes=written,
                        status="complete",
                    )
                )

            packaged_manifest = replace(manifest, assets=tuple(updated))
            archive.writestr(
                "manifest.json",
                json.dumps(
                    packaged_manifest.as_dict(),
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                ).encode("utf-8"),
            )
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


@dataclass(frozen=True)
class LoadedCasePackage:
    manifest: CaseManifest
    assets: Mapping[str, bytes]

    def asset_bytes(self, asset_id):
        return self.assets.get(str(asset_id))


def load_case_package(path) -> LoadedCasePackage:
    """Validate all members and expose bytes without extracting or executing."""
    source = Path(path)
    try:
        package_size = source.stat().st_size
    except OSError as exc:
        raise CaseError(f"portable case could not be read: {exc}") from exc
    if package_size > MAX_PACKAGE_BYTES:
        raise CaseError("portable case package exceeds the safety limit")
    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            if names.count("manifest.json") != 1 or len(names) > MAX_ASSETS + 1:
                raise CaseError("portable case member list is invalid")
            if len(set(names)) != len(names):
                raise CaseError("portable case contains duplicate member names")
            expanded_size = 0
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise CaseError("portable case contains an unsafe member path")
                info = archive.getinfo(name)
                if info.file_size > MAX_ASSET_BYTES:
                    raise CaseError("portable case member exceeds the safety limit")
                expanded_size += info.file_size
                if expanded_size > MAX_PACKAGE_BYTES:
                    raise CaseError(
                        "portable case expanded data exceeds the safety limit"
                    )
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise CaseError("portable case may not contain symbolic links")
            manifest = CaseManifest.from_dict(
                json.loads(archive.read("manifest.json").decode("utf-8"))
            )
            assets = {}
            known_members = {"manifest.json"}
            for asset in manifest.assets:
                if asset.status != "complete":
                    continue
                if not asset.package_name or not asset.sha256:
                    raise CaseError(
                        f"complete asset {asset.asset_id} lacks integrity metadata"
                    )
                known_members.add(asset.package_name)
                payload = archive.read(asset.package_name)
                if len(payload) != asset.size_bytes or _sha256(payload) != asset.sha256:
                    raise CaseError(f"asset integrity check failed: {asset.asset_id}")
                assets[asset.asset_id] = payload
            if set(names) != known_members:
                raise CaseError("portable case contains unlisted data")
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise CaseError(f"portable case could not be read: {exc}") from exc
    return LoadedCasePackage(manifest, MappingProxyType(assets))


__all__ = [
    "ALLOWED_KINDS",
    "CaseAsset",
    "CaseError",
    "CaseManifest",
    "DownloadedCase",
    "LoadedCasePackage",
    "PROVIDER_CAPABILITIES",
    "ReplayClock",
    "create_case_package",
    "download_case_assets",
    "load_case_package",
]
