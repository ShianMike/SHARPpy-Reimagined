"""Inspect and clear regenerable caches; never accept filesystem paths from IPC."""

from __future__ import annotations

import fnmatch
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

_LOCK = threading.RLock()
KEYS = ("models", "maps", "outlooks")


def _regular(info: os.stat_result) -> bool:
    return not (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _files(root: Path | None, pattern: str | tuple[str, ...] = "*") -> list[tuple[Path, int, bool]]:
    if root is None:
        return []
    try:
        if not _regular(root.lstat()):
            return []
    except FileNotFoundError:
        return []
    boundary = root.resolve()
    patterns = (pattern,) if isinstance(pattern, str) else pattern
    # shortcut: allow an hour for atomic writes; longer writes need explicit leases.
    partial_cutoff = time.time() - 3600
    result = []
    pending = [root]
    while pending:
        parent = pending.pop()
        if not parent.resolve().is_relative_to(boundary):
            continue
        try:
            with os.scandir(parent) as children:
                for child in children:
                    try:
                        info = child.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # An in-flight download can replace a temporary file.
                    if not _regular(info):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(child.path))
                    elif stat.S_ISREG(info.st_mode) and any(fnmatch.fnmatch(child.name, item) for item in patterns):
                        ready = not child.name.endswith(".part") or info.st_mtime < partial_cutoff
                        result.append((Path(child.path), info.st_size, ready))
        except FileNotFoundError:
            continue
    return result


def _scan():
    from sharpmod.bridge.sounding_cache import cache
    from sharpmod.models.model_disk_cache import ModelDiskCache
    from sharpmod.providers import hrrr_field, spc_outlook

    disk = ModelDiskCache()
    entries = disk.entries()
    session = cache()
    completed = set(session.completed_downloads())
    downloads = [path for path in session.root.iterdir()
                 if path.name.endswith("-grib") or path.name.startswith("sharpmod-ensemble")]
    session_files = [(path, size, directory in completed)
                     for directory in downloads for path, size, _ in _files(directory)]
    files = {
        "models": [(path, size, True) for path, size, ready in session_files if ready],
        "maps": _files(hrrr_field.disk_cache_root(), ("*.png", "*.json.gz", "*.part")),
        "outlooks": _files(spc_outlook.disk_cache_root(), ("*.json", "*.part")),
    }
    usage = {"caches": [{
        "key": key,
        "bytes": sum(size for _, size, _ in files[key]),
        "files": len(files[key]),
        "clearable_bytes": sum(size for _, size, ready in files[key] if ready),
    } for key in KEYS]}
    models = usage["caches"][0]
    models["bytes"] = sum(entry.size for entry in entries) + sum(size for _, size, _ in session_files)
    models["files"] = sum(entry.file_count for entry in entries) + len(session_files)
    models["clearable_bytes"] += sum(entry.size for entry in entries
                                     if not entry.pinned and not entry.protected)
    candidates = {key: [(path, size) for path, size, ready in files[key] if ready] for key in KEYS}
    return usage, candidates, disk, entries


def usage(params: dict[str, Any]) -> dict[str, Any]:
    p.keys(params, ())
    try:
        with _LOCK:
            return _scan()[0]
    except OSError as exc:
        raise BridgeError("INTERNAL", "Cache usage could not be read. Try refreshing Storage.", True) from exc


def clear(params: dict[str, Any]) -> dict[str, Any]:
    p.keys(params, ("cache",))
    target = p.choice(params, "cache", (*KEYS, "all"), "")
    selected = KEYS if target == "all" else (target,)
    try:
        with _LOCK:
            _, candidates, disk, entries = _scan()
            freed = failed = 0
            if "models" in selected:
                for entry in entries:
                    if entry.protected or entry.pinned:
                        continue
                    if disk.delete(entry.path):
                        freed += entry.size
                    else:
                        failed += entry.file_count
            for key in selected:
                for path, size in candidates[key]:
                    try:
                        path.unlink()
                        freed += size
                    except FileNotFoundError:
                        pass
                    except OSError:
                        failed += 1
            if "maps" in selected:
                from sharpmod.providers import hrrr_field
                hrrr_field.clear_cache()
            if "outlooks" in selected:
                from sharpmod.providers import spc_outlook
                spc_outlook.clear_cache()
            return {"usage": _scan()[0], "freed_bytes": freed, "failed_files": failed}
    except OSError as exc:
        raise BridgeError("INTERNAL", "Some cache data could not be cleared. Refresh Storage and try again.", True) from exc


HANDLERS = {"storage.usage": usage, "storage.clear": clear}
