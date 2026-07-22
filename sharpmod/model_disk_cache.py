"""Persistent, bounded storage for forecast-model GRIB subsets."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
import zipfile


_METADATA = ".cache.json"
_LEASE_PREFIX = ".lease-"


@dataclass(frozen=True)
class CacheEntry:
    """User-facing metadata for one persistent model-hour directory."""

    path: Path
    model: str
    run: str
    fxx: int
    member: str | None
    spatial: str | None
    source_url: str | None
    source_transport: str | None
    source_fields: tuple[str, ...]
    accessed: float
    size: int
    protected: bool
    pinned: bool
    valid_grib: bool
    valid_sounding: bool
    file_count: int


def default_model_cache_root() -> Path:
    """Return the platform cache directory, honoring an explicit override."""
    explicit = os.environ.get("SHARPMOD_MODEL_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "sharpmod" / "model-cache"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base).expanduser() / "sharpmod" / "model-cache"
    return Path.home() / ".cache" / "sharpmod" / "model-cache"


def _safe(value) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    ) or "none"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        os.replace(temporary, path)
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


class ModelDiskCache:
    """Own persistent model-hour directories under age and size limits."""

    def __init__(
        self,
        root=None,
        *,
        max_bytes: int | None = None,
        max_age_hours: float | None = None,
    ):
        self.root = Path(root or default_model_cache_root()).expanduser()
        if max_bytes is None:
            max_bytes = int(
                float(os.environ.get("SHARPMOD_MODEL_CACHE_GB", "3"))
                * 1024 ** 3
            )
        if max_age_hours is None:
            max_age_hours = float(
                os.environ.get("SHARPMOD_MODEL_CACHE_HOURS", "48")
            )
        self.max_bytes = max(0, int(max_bytes))
        self.max_age_hours = max(0.0, float(max_age_hours))
        self._lock = threading.RLock()

    def directory_for(self, key) -> Path:
        """Return and touch the deterministic directory for one model hour."""
        run = key.run_time
        if run.tzinfo is not None:
            run = run.astimezone(timezone.utc)
        member = _safe(key.member or "deterministic")
        spatial = _safe(getattr(key, "spatial", None) or "full-grid")
        path = (
            self.root
            / _safe(key.model)
            / run.strftime("%Y%m%d%H")
            / f"f{int(key.fxx):03d}-{member}-{spatial}"
        )
        path.mkdir(parents=True, exist_ok=True)
        self.touch(path, key=key)
        return path

    def touch(self, directory, *, key=None, now: float | None = None) -> None:
        """Update access metadata atomically."""
        directory = Path(directory)
        payload = {
            "accessed": float(time.time() if now is None else now),
        }
        metadata = directory / _METADATA
        try:
            current = json.loads(metadata.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                payload = {**current, **payload}
        except (OSError, ValueError, TypeError):
            pass
        if key is not None:
            payload.update({
                "model": str(key.model),
                "run": key.run_time.isoformat(),
                "fxx": int(key.fxx),
                "member": key.member,
                "spatial": getattr(key, "spatial", None),
            })
        with self._lock:
            _write_json(metadata, payload)

    def annotate(self, directory, **values) -> None:
        """Merge non-secret source provenance into one managed entry."""
        allowed = {
            "source_url", "source_transport", "source_fields",
            "source_provider",
        }
        update = {key: value for key, value in values.items() if key in allowed}
        if "source_fields" in update:
            update["source_fields"] = [
                str(value) for value in (update["source_fields"] or ())
            ]
        with self._lock:
            target = self._managed_directory(directory)
            metadata = target / _METADATA
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(update)
            payload["accessed"] = float(time.time())
            _write_json(metadata, payload)

    @contextmanager
    def protect(self, directory):
        """Prevent pruning while a worker or decoded dataset uses a directory."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / (
            f"{_LEASE_PREFIX}{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
        )
        marker.touch()
        try:
            self.touch(directory)
            yield directory
        finally:
            try:
                marker.unlink()
            except OSError:
                pass
            if directory.exists():
                self.touch(directory)

    @staticmethod
    def _entry_size(directory: Path) -> int:
        total = 0
        for root, _dirs, files in os.walk(directory):
            for name in files:
                if name == _METADATA or name.startswith(_LEASE_PREFIX):
                    continue
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total

    @staticmethod
    def _entry_files(directory: Path) -> list[Path]:
        result = []
        for root, _dirs, files in os.walk(directory):
            for name in files:
                if name == _METADATA or name.startswith(_LEASE_PREFIX):
                    continue
                result.append(Path(root) / name)
        return result

    @staticmethod
    def _payload_files(directory: Path, files: list[Path]) -> list[Path]:
        """Exclude resumable fragments and indexes from reusable payloads."""
        result = []
        for path in files:
            try:
                relative = path.relative_to(directory)
            except ValueError:
                continue
            if any(part.endswith(".ranges") for part in relative.parts[:-1]):
                continue
            name = path.name.lower()
            if name.endswith((".part", ".tmp", ".idx")):
                continue
            result.append(path)
        return result

    @staticmethod
    def _valid_grib(path: Path) -> bool:
        try:
            if path.stat().st_size < 8:
                return False
            with path.open("rb") as handle:
                if handle.read(4) != b"GRIB":
                    return False
                handle.seek(-4, os.SEEK_END)
                return handle.read(4) == b"7777"
        except OSError:
            return False

    @staticmethod
    def _valid_sounding(path: Path) -> bool:
        if path.suffix.lower() != ".npz":
            return False
        sidecar = path.with_suffix(".json")
        try:
            if path.stat().st_size < 32 or not sidecar.is_file():
                return False
            required = {
                "pres.npy", "hght.npy", "tmpc.npy", "dwpc.npy",
                "wdir.npy", "wspd.npy", "omeg.npy", "valid.npy",
                "run.npy", "loc.npy",
            }
            with zipfile.ZipFile(path) as archive:
                if not required.issubset(archive.namelist()):
                    return False
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            return isinstance(payload, dict)
        except (OSError, ValueError, TypeError, zipfile.BadZipFile):
            return False

    @staticmethod
    def _is_protected(directory: Path) -> bool:
        try:
            return any(
                child.name.startswith(_LEASE_PREFIX)
                for child in directory.iterdir()
            )
        except OSError:
            return False

    def _entries(self):
        if not self.root.exists():
            return []
        result = []
        for metadata in self.root.rglob(_METADATA):
            directory = metadata.parent
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    payload = {}
                accessed = float(payload.get("accessed", metadata.stat().st_mtime))
            except (OSError, ValueError, TypeError):
                payload = {}
                accessed = 0.0
            files = self._entry_files(directory)
            payload_files = self._payload_files(directory, files)
            result.append({
                "path": directory,
                "accessed": accessed,
                "size": self._entry_size(directory),
                "protected": self._is_protected(directory),
                "pinned": bool(payload.get("pinned", False)),
                "valid_grib": any(
                    self._valid_grib(path) for path in payload_files
                ),
                "valid_sounding": any(
                    self._valid_sounding(path) for path in payload_files
                ),
                "file_count": len(payload_files),
                "payload": payload,
            })
        return result

    def entries(self) -> list[CacheEntry]:
        """Return newest-first cache metadata without exposing partial files."""
        with self._lock:
            raw = sorted(
                self._entries(), key=lambda item: item["accessed"], reverse=True
            )
        result = []
        for item in raw:
            payload = item["payload"]
            try:
                fxx = int(payload.get("fxx", 0))
            except (TypeError, ValueError, OverflowError):
                fxx = 0
            result.append(CacheEntry(
                path=item["path"],
                model=str(payload.get("model", "unknown")),
                run=str(payload.get("run", "")),
                fxx=fxx,
                member=(
                    str(payload["member"])
                    if payload.get("member") not in {None, ""} else None
                ),
                spatial=(
                    str(payload["spatial"])
                    if payload.get("spatial") not in {None, ""} else None
                ),
                source_url=(
                    str(payload["source_url"])
                    if payload.get("source_url") not in {None, ""} else None
                ),
                source_transport=(
                    str(payload["source_transport"])
                    if payload.get("source_transport") not in {None, ""}
                    else None
                ),
                source_fields=tuple(
                    str(value) for value in payload.get("source_fields", ())
                ),
                accessed=item["accessed"],
                size=item["size"],
                protected=item["protected"],
                pinned=item["pinned"],
                valid_grib=item["valid_grib"],
                valid_sounding=item["valid_sounding"],
                file_count=item["file_count"],
            ))
        return result

    def valid_grib_paths(self, directory) -> tuple[Path, ...]:
        """Return complete reusable GRIB payloads in one managed entry."""
        with self._lock:
            target = self._managed_directory(directory)
            files = self._payload_files(target, self._entry_files(target))
            return tuple(path for path in files if self._valid_grib(path))

    def _managed_directory(self, directory) -> Path:
        root = self.root.resolve()
        target = Path(directory).expanduser().resolve()
        if target == root or root not in target.parents:
            raise ValueError("cache entry is outside the managed cache root")
        if not (target / _METADATA).is_file():
            raise ValueError("directory is not a managed cache entry")
        return target

    def set_pinned(self, directory, pinned=True) -> CacheEntry:
        """Pin/unpin one entry so automatic pruning and clearing preserve it."""
        with self._lock:
            target = self._managed_directory(directory)
            metadata = target / _METADATA
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["pinned"] = bool(pinned)
            payload["accessed"] = float(time.time())
            _write_json(metadata, payload)
            return next(entry for entry in self.entries() if entry.path == target)

    def delete(self, directory) -> bool:
        """Explicitly remove one unleased managed entry, even when pinned."""
        with self._lock:
            target = self._managed_directory(directory)
            if self._is_protected(target):
                return False
            shutil.rmtree(target, ignore_errors=True)
            return not target.exists()

    def prune(self, *, now: float | None = None) -> list[Path]:
        """Remove expired and least-recently-used entries under configured limits."""
        now = float(time.time() if now is None else now)
        cutoff = now - self.max_age_hours * 3600.0
        removed: list[Path] = []
        with self._lock:
            entries = sorted(self._entries(), key=lambda item: item["accessed"])
            kept = []
            for entry in entries:
                if not entry["protected"] and not entry["pinned"] \
                        and entry["accessed"] < cutoff:
                    shutil.rmtree(entry["path"], ignore_errors=True)
                    removed.append(entry["path"])
                else:
                    kept.append(entry)
            total = sum(entry["size"] for entry in kept)
            for entry in kept:
                if total <= self.max_bytes:
                    break
                if entry["protected"] or entry["pinned"]:
                    continue
                shutil.rmtree(entry["path"], ignore_errors=True)
                removed.append(entry["path"])
                total -= entry["size"]
        return removed

    def clear(self, *, include_pinned: bool = False) -> list[Path]:
        """Remove unprotected entries, preserving pinned data by default."""
        removed = []
        with self._lock:
            for entry in self._entries():
                if entry["protected"] or (
                    entry["pinned"] and not include_pinned
                ):
                    continue
                shutil.rmtree(entry["path"], ignore_errors=True)
                removed.append(entry["path"])
        return removed


__all__ = ["CacheEntry", "ModelDiskCache", "default_model_cache_root"]
