"""Persistent, bounded storage for forecast-model GRIB subsets."""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from sharpmod.portable_sounding import portable_sounding_pair_valid

_METADATA = ".cache.json"
_LEASE_PREFIX = ".lease-"
_DEFAULT_LEASE_MAX_AGE_HOURS = 24.0
_SUPPLEMENTAL_DIRECTORIES = frozenset({"regional-guidance"})
# v3 separates reusable point-sounding payloads from supplemental HRRR frames.
# Invalidating v2 also prevents already-created v0.8 caches from presenting a
# regional-only GRIB as an offline point-sounding source.
MODEL_CACHE_CONTRACT_VERSION = 3


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
    contract_version: int


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
        with suppress(OSError):
            os.close(fd)
        with suppress(OSError):
            os.remove(temporary)
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
            run = run.astimezone(UTC)
        member = _safe(key.member or "deterministic")
        spatial = _safe(getattr(key, "spatial", None) or "full-grid")
        path = (
            self.root
            / f"v{MODEL_CACHE_CONTRACT_VERSION}"
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
            "contract_version": MODEL_CACHE_CONTRACT_VERSION,
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
            with suppress(OSError):
                marker.unlink()
            if directory.exists():
                self.touch(directory)

    @staticmethod
    def _entry_size(directory: Path) -> int:
        total = 0
        for root, _dirs, files in os.walk(directory):
            for name in files:
                if name == _METADATA or name.startswith(_LEASE_PREFIX):
                    continue
                with suppress(OSError):
                    total += (Path(root) / name).stat().st_size
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
            if any(
                part.casefold() in _SUPPLEMENTAL_DIRECTORIES
                for part in relative.parts[:-1]
            ):
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
        return portable_sounding_pair_valid(path)

    @staticmethod
    def _lease_max_age_seconds() -> float:
        try:
            hours = float(
                os.environ.get(
                    "SHARPMOD_MODEL_CACHE_LEASE_HOURS",
                    _DEFAULT_LEASE_MAX_AGE_HOURS,
                )
            )
        except (TypeError, ValueError, OverflowError):
            hours = _DEFAULT_LEASE_MAX_AGE_HOURS
        return max(1.0, hours * 3600.0)

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            return exc.errno == errno.EPERM or getattr(exc, "winerror", None) == 5
        except SystemError as exc:
            # CPython on Windows can surface ``os.kill(pid, 0)`` for an
            # invalid/stale PID as a SystemError whose direct cause is
            # WinError 87 instead of raising the OSError normally.
            cause = exc.__cause__
            if not isinstance(cause, OSError):
                raise
            return (
                cause.errno == errno.EPERM
                or getattr(cause, "winerror", None) == 5
            )
        return True

    @classmethod
    def _lease_is_active(cls, marker: Path, *, now: float | None = None) -> bool:
        try:
            age = float(time.time() if now is None else now) - marker.stat().st_mtime
        except OSError:
            return False
        if age > cls._lease_max_age_seconds():
            with suppress(OSError):
                marker.unlink()
            return False
        try:
            pid = int(marker.name[len(_LEASE_PREFIX):].split("-", 1)[0])
        except (TypeError, ValueError):
            return True
        if cls._process_is_running(pid):
            return True
        with suppress(OSError):
            marker.unlink()
        return False

    @classmethod
    def _is_protected(cls, directory: Path) -> bool:
        try:
            return any(
                child.name.startswith(_LEASE_PREFIX)
                and cls._lease_is_active(child)
                for child in directory.iterdir()
            )
        except OSError:
            return False

    @staticmethod
    def _remove_tree(directory: Path) -> bool:
        """Remove one entry and report the filesystem result truthfully."""
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            return True
        except OSError:
            return not directory.exists()
        return not directory.exists()

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
            try:
                current_contract = (
                    int(payload.get("contract_version", 0) or 0)
                    == MODEL_CACHE_CONTRACT_VERSION
                )
            except (TypeError, ValueError, OverflowError):
                current_contract = False
            result.append({
                "path": directory,
                "accessed": accessed,
                "size": self._entry_size(directory),
                "protected": self._is_protected(directory),
                "pinned": bool(payload.get("pinned", False)),
                "valid_grib": current_contract and any(
                    self._valid_grib(path) for path in payload_files
                ),
                "valid_sounding": current_contract and any(
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
            try:
                contract_version = int(payload.get("contract_version", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                contract_version = 0
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
                contract_version=contract_version,
            ))
        return result

    def valid_grib_paths(self, directory) -> tuple[Path, ...]:
        """Return complete reusable GRIB payloads in one managed entry."""
        with self._lock:
            target = self._managed_directory(directory)
            if not self._current_contract(target):
                return ()
            files = self._payload_files(target, self._entry_files(target))
            return tuple(path for path in files if self._valid_grib(path))

    def valid_sounding_paths(self, directory) -> tuple[Path, ...]:
        """Return safely decodable portable sounding pairs in one entry."""
        with self._lock:
            target = self._managed_directory(directory)
            if not self._current_contract(target):
                return ()
            files = self._payload_files(target, self._entry_files(target))
            return tuple(path for path in files if self._valid_sounding(path))

    @staticmethod
    def _current_contract(directory: Path) -> bool:
        try:
            payload = json.loads(
                (directory / _METADATA).read_text(encoding="utf-8")
            )
            return (
                isinstance(payload, dict)
                and int(payload.get("contract_version", 0))
                == MODEL_CACHE_CONTRACT_VERSION
            )
        except (OSError, TypeError, ValueError):
            return False

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
            return self._remove_tree(target)

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
                    if self._remove_tree(entry["path"]):
                        removed.append(entry["path"])
                    else:
                        kept.append(entry)
                else:
                    kept.append(entry)
            total = sum(entry["size"] for entry in kept)
            for entry in kept:
                if total <= self.max_bytes:
                    break
                if entry["protected"] or entry["pinned"]:
                    continue
                if self._remove_tree(entry["path"]):
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
                if self._remove_tree(entry["path"]):
                    removed.append(entry["path"])
        return removed


__all__ = [
    "CacheEntry",
    "MODEL_CACHE_CONTRACT_VERSION",
    "ModelDiskCache",
    "default_model_cache_root",
]
