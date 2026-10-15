"""Opened soundings: portable files on disk, decoded collections in memory.

Every opened sounding gets a random id. Its data file (the portable ``.npz``
that sharpmod writes for observed and model soundings, or the uploaded file
itself) lives in ``<root>/<id><suffix>``, and rendered images in
``<root>/renders/``. The root is a new directory made for this process with
``tempfile.mkdtemp`` (inside ``SHARPMOD_BRIDGE_CACHE`` when that is set), and
it is removed when the process exits. Nothing outside that directory is ever
deleted.
"""

from __future__ import annotations

import atexit
import logging
import os
import secrets
import shutil
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sharpmod.bridge.compat import ensure_profile_compat
from sharpmod.bridge.errors import BridgeError

log = logging.getLogger("sharpmod.bridge")

CACHE_ENV = "SHARPMOD_BRIDGE_CACHE"
MAX_ENTRIES = 24


class SoundingDecodeError(Exception):
    """No registered decoder could read a sounding file."""


@dataclass
class Entry:
    sounding_id: str
    #: The data file: an ``.npz`` for observed and model soundings, else the upload.
    path: Path
    summary: dict[str, Any]
    collection: Any = None
    analysis: dict[str, Any] | None = None
    trends: dict[str, Any] | None = None
    #: Guards the decoded collection and the computed results.
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Guards image renders, which never touch the collection, so a slow first
    #: render never holds up the parameters of the same sounding.
    render_lock: threading.Lock = field(default_factory=threading.Lock)


def default_root() -> Path:
    """A fresh directory owned by this process, removed again at exit."""
    parent = os.environ.get(CACHE_ENV, "").strip() or None
    if parent is not None:
        Path(parent).mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="sharpmod-bridge-", dir=parent))
    atexit.register(shutil.rmtree, root, True)
    return root


class SoundingCache:
    """A bounded, thread-safe LRU of opened soundings."""

    def __init__(self, root: Path, max_entries: int = MAX_ENTRIES) -> None:
        self.root = root
        self.renders = root / "renders"
        self.max_entries = max_entries
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._lock = threading.Lock()
        self.renders.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_id() -> str:
        return secrets.token_hex(8)

    def data_path(self, sounding_id: str, suffix: str) -> Path:
        return self.root / f"{sounding_id}{suffix}"

    def render_path(self, sounding_id: str, parcel: str, mode: str) -> Path:
        return self.renders / f"{sounding_id}-{parcel.lower()}-{mode}.png"

    def add(self, entry: Entry) -> None:
        with self._lock:
            self._entries[entry.sounding_id] = entry
            self._entries.move_to_end(entry.sounding_id)
            while len(self._entries) > self.max_entries:
                _, old = self._entries.popitem(last=False)
                self._remove_files(old.sounding_id)

    def get(self, sounding_id: Any) -> Entry:
        if not isinstance(sounding_id, str) or not sounding_id:
            raise BridgeError(
                "INVALID_ARGUMENT",
                "The request was not valid: sounding_id must be a string.",
                False,
            )
        with self._lock:
            entry = self._entries.get(sounding_id)
            if entry is not None:
                self._entries.move_to_end(sounding_id)
        if entry is None:
            raise BridgeError(
                "SOUNDING_NOT_FOUND",
                "This sounding is no longer open in the backend. Open it again.",
                False,
                {"sounding_id": sounding_id},
            )
        return entry

    def _remove_files(self, sounding_id: str) -> None:
        for path in [
            *self.root.glob(f"{sounding_id}*"),
            *self.renders.glob(f"{sounding_id}-*"),
        ]:
            try:
                path.unlink()
            except OSError:
                log.warning("could not remove cached file %s", path)


_CACHE: SoundingCache | None = None
_CACHE_LOCK = threading.Lock()


def cache() -> SoundingCache:
    """The process-wide cache, created on first use."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = SoundingCache(default_root())
        return _CACHE


def reset_cache(root: Path | None = None) -> SoundingCache | None:
    """Replace the process-wide cache (tests); ``None`` drops it."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None if root is None else SoundingCache(root)
        return _CACHE


def decode_collection(path: Path) -> tuple[Any, str]:
    """Decode ``path`` the way the renderer does, without Qt.

    ``.npz`` point soundings go through ``load_npz``; anything else is tried
    against every registered decoder in turn, as ``rendering.input`` does.
    """
    ensure_profile_compat()
    from sharpmod.io import decoder
    from sharpmod.sharptab.accelerated_profile import accelerate_profile_collection

    if path.suffix.lower() == ".npz":
        collection, station = decoder.load_npz(str(path))
    else:
        collection = None
        station = ""
        last: BaseException | None = None
        for _name, cls in decoder.getDecoders().items():
            try:
                instance = cls(str(path))
                collection = instance.getProfiles()
                station = instance.getStnId()
                decoder.attach_json_sidecar(collection, str(path))
                break
            except Exception as exc:  # noqa: BLE001 - try every decoder in turn
                last = exc
                collection = None
        if collection is None:
            raise SoundingDecodeError(
                str(last) if last is not None else "no decoder is registered"
            )
    accelerate_profile_collection(collection)
    return collection, str(station or "")
