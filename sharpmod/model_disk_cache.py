"""Persistent, bounded storage for forecast-model GRIB subsets."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid


_METADATA = ".cache.json"
_LEASE_PREFIX = ".lease-"


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
                accessed = float(payload.get("accessed", metadata.stat().st_mtime))
            except (OSError, ValueError, TypeError):
                accessed = 0.0
            result.append({
                "path": directory,
                "accessed": accessed,
                "size": self._entry_size(directory),
                "protected": self._is_protected(directory),
            })
        return result

    def prune(self, *, now: float | None = None) -> list[Path]:
        """Remove expired and least-recently-used entries under configured limits."""
        now = float(time.time() if now is None else now)
        cutoff = now - self.max_age_hours * 3600.0
        removed: list[Path] = []
        with self._lock:
            entries = sorted(self._entries(), key=lambda item: item["accessed"])
            kept = []
            for entry in entries:
                if not entry["protected"] and entry["accessed"] < cutoff:
                    shutil.rmtree(entry["path"], ignore_errors=True)
                    removed.append(entry["path"])
                else:
                    kept.append(entry)
            total = sum(entry["size"] for entry in kept)
            for entry in kept:
                if total <= self.max_bytes:
                    break
                if entry["protected"]:
                    continue
                shutil.rmtree(entry["path"], ignore_errors=True)
                removed.append(entry["path"])
                total -= entry["size"]
        return removed

    def clear(self) -> list[Path]:
        """Remove all unprotected cache-managed entries."""
        removed = []
        with self._lock:
            for entry in self._entries():
                if entry["protected"]:
                    continue
                shutil.rmtree(entry["path"], ignore_errors=True)
                removed.append(entry["path"])
        return removed
