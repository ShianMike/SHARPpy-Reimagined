"""Bounded live responses under the bridge's existing, exit-cleaned session root."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sharpmod.bridge.sounding_cache import cache

MAX_ENTRIES = 128
MAX_BYTES = 128 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
METHODS = {"radar.mosaic": "radar", "radar.site": "radar", "satellite.goes": "satellite",
           "surface.plots": "observations", "alerts.weather": "alerts"}
_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def _ttl(method: str, params: dict) -> float:
    if method in {"radar.mosaic", "radar.site", "satellite.goes", "alerts.weather"}:
        try:
            when = datetime.fromisoformat(params["valid_time"].replace("Z", "+00:00"))
            when = (
                when.replace(tzinfo=timezone.utc)
                if when.tzinfo is None
                else when.astimezone(timezone.utc)
            )
            now = datetime.now(timezone.utc)
            if (method == "alerts.weather" and when < now.replace(second=0, microsecond=0)) or when <= now - timedelta(minutes=15):
                return float("inf")
        except (KeyError, AttributeError, TypeError, ValueError):
            pass  # The handler validates missing or malformed input on a cache miss.
    return 60.0


def _read(path: Path, ttl: float) -> dict | None:
    try:
        if path.is_symlink() or time.time() - path.stat().st_mtime >= ttl:
            return None
        with gzip.open(path, "rb") as stream:
            payload = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) <= MAX_RESPONSE_BYTES:
            result = json.loads(payload)
            if isinstance(result, dict):
                return result
    except (OSError, ValueError, EOFError, zlib.error):
        pass
    return None


def _write(path: Path, result: dict) -> None:
    payload = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_RESPONSE_BYTES:
        return
    compressed = gzip.compress(payload, compresslevel=1, mtime=0)
    if len(compressed) > MAX_BYTES:
        return
    temporary = None
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            # shortcut: scan the small session directory; index eviction if its entry budget grows.
            entries = []
            for item in path.parent.parent.rglob("*.json.gz"):
                if item == path:
                    continue
                info = item.stat()
                entries.append((info.st_mtime, info.st_size, item))
            total = sum(size for _, size, _ in entries)
            count = len(entries)
            for _, size, item in sorted(entries):
                if count < MAX_ENTRIES and total + len(compressed) <= MAX_BYTES:
                    break
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    continue
                count -= 1
                total -= size
            if count >= MAX_ENTRIES or total + len(compressed) > MAX_BYTES:
                return  # Locked files must not let a new response exceed the cache budget.
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=".part", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(compressed)
            os.replace(temporary, path)
    except OSError:
        log.debug("could not retain live response %s", path, exc_info=True)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def call(method: str, params: dict, handler):
    """Share successful replies between desktop and browser, without caching errors."""
    if method not in METHODS:
        return handler(params)
    digest = hashlib.sha256(
        json.dumps(
            [method, params], sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    try:
        path = cache().root / "live" / METHODS[method] / f"{digest}.json.gz"
    except OSError:
        log.debug("live session cache is unavailable", exc_info=True)
        return handler(params)
    result = _read(path, _ttl(method, params))
    if result is not None:
        return result
    result = handler(params)
    _write(path, result)
    return result
