"""Bounded source ownership for batch extraction and interactive model hours."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
import shutil
import tempfile
import threading
from typing import Callable, Iterator

from sharpmod.models.model_transport import DownloadCancelled


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelHourKey:
    """Canonical identity of one decoded model/run/forecast-hour/member."""

    model: str
    run_time: datetime
    fxx: int
    member: str | None = None
    spatial: str | None = None

    @classmethod
    def create(
        cls, model, run_time, fxx, member=None, spatial=None
    ) -> "ModelHourKey":
        run_dt = run_time
        if not isinstance(run_dt, datetime):
            raise TypeError("run_time must be a datetime")
        if run_dt.tzinfo is None:
            run_dt = run_dt.replace(tzinfo=timezone.utc)
        else:
            run_dt = run_dt.astimezone(timezone.utc)
        member_value = str(member).strip() if member is not None else ""
        spatial_value = str(spatial).strip() if spatial is not None else ""
        return cls(
            model=str(model).strip().lower(),
            run_time=run_dt,
            fxx=int(fxx),
            member=member_value or None,
            spatial=spatial_value or None,
        )


@dataclass
class ModelHourEntry:
    """One cache-owned decoded dataset and its isolated GRIB tree."""

    key: ModelHourKey
    dataset: object
    source_grib: str | None
    download_dir: str
    source_fields: tuple[str, ...] = ()
    source_transport: str | None = None
    leases: int = 0
    stale: bool = False
    disposed: bool = False
    directory_guard: object | None = None


@dataclass
class _Flight:
    """Shared result state for one currently loading model hour."""

    generation: int
    consumers: list[tuple] = field(default_factory=list)
    abandoned: bool = False
    done: bool = False
    entry: ModelHourEntry | None = None
    error: BaseException | None = None


class ModelHourCache:
    """Small thread-safe LRU cache with lease-aware resource cleanup.

    The default one-entry limit is deliberate: a pressure-level HRRR subset can
    occupy hundreds of megabytes on disk, and its lazy xarray/cfgrib dataset can
    retain sizable indexes.  A lease prevents application shutdown or LRU
    eviction from closing a dataset while a worker is extracting a point.
    """

    def __init__(
        self,
        max_entries: int = 1,
        *,
        directory_factory: Callable[[ModelHourKey], str] | None = None,
        directory_protector: Callable[[str], object] | None = None,
        metadata_writer: Callable[..., object] | None = None,
        delete_download_dirs: bool = True,
    ):
        max_entries = int(max_entries)
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[ModelHourKey, ModelHourEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._inflight: dict[ModelHourKey, _Flight] = {}
        self._downloads: dict[str, ModelHourEntry | None] = {}
        self._background_loads = 0
        self._generation = 0
        self._directory_factory = directory_factory
        self._directory_protector = directory_protector
        self._metadata_writer = metadata_writer
        self._delete_download_dirs = bool(delete_download_dirs)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @contextmanager
    def lease(
        self,
        key: ModelHourKey,
        loader: Callable[..., tuple[object, object]],
        *,
        cancelled=None,
        progress_callback=None,
        on_bytes=None,
    ) -> Iterator[tuple[ModelHourEntry, bool]]:
        """Lease a source; cancellable loaders receive shared transfer callbacks.

        Legacy loaders take only the directory and run in the caller. A loader
        with ``cancelled`` supplied runs independently so any consumer can stop
        waiting without cancelling the other consumers' transfer.
        """
        if not isinstance(key, ModelHourKey):
            raise TypeError("key must be a ModelHourKey")
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")

        entry = None
        victims = []
        leader = False
        flight = None
        consumer = (cancelled, progress_callback, on_bytes)
        with self._condition:
            while True:
                if cancelled is not None and cancelled():
                    raise DownloadCancelled("forecast-model download cancelled")
                cached = self._entries.get(key)
                if cached is not None and not cached.stale:
                    cached.leases += 1
                    self._entries.move_to_end(key)
                    entry = cached
                    cache_hit = True
                    break
                flight = self._inflight.get(key)
                if flight is not None and self._stopped(flight):
                    flight = None
                if flight is not None:
                    cache_hit = True
                else:
                    # shortcut: one background producer per cache; increase
                    # only after source-memory and connection profiling.
                    if cancelled is not None and self._background_loads:
                        self._condition.wait(timeout=0.1)
                        continue
                    flight = _Flight(generation=self._generation)
                    self._inflight[key] = flight
                    cache_hit = False
                    leader = True
                    if cancelled is not None:
                        self._background_loads += 1
                    victims = self._make_room_locked()
                flight.consumers.append(consumer)
                break

        self._dispose_entries(victims)
        try:
            if leader:
                if cancelled is None:
                    self._load(key, flight, loader)
                else:
                    def load(directory):
                        return loader(
                            directory,
                            cancelled=lambda: self._stopped(flight),
                            progress_callback=lambda *args: self._notify(flight, 1, *args),
                            on_bytes=lambda size: self._notify(flight, 2, size),
                        )
                    try:
                        threading.Thread(
                            target=self._load, args=(key, flight, load, True),
                            name="model-hour", daemon=True,
                        ).start()
                    except BaseException as exc:
                        with self._condition:
                            flight.error = exc
                            flight.done = True
                            if self._inflight.get(key) is flight:
                                self._inflight.pop(key)
                            self._background_loads -= 1
                            self._condition.notify_all()
            if flight is not None:
                with self._condition:
                    while not flight.done:
                        if cancelled is not None and cancelled():
                            raise DownloadCancelled("forecast-model download cancelled")
                        self._condition.wait(timeout=0.1 if cancelled is not None else None)
                    if flight.error is not None:
                        raise flight.error
                    entry = flight.entry
                    if entry is None:
                        raise RuntimeError("model-hour loader completed without an entry")
            if cancelled is not None and cancelled():
                raise DownloadCancelled("forecast-model download cancelled")
            _LOGGER.info(
                "model_hour_cache.%s model=%s run=%s fxx=%03d member=%s dir=%s",
                "hit" if cache_hit else "miss", key.model,
                key.run_time.isoformat(), key.fxx, key.member, entry.download_dir,
            )
            yield entry, cache_hit
        except BaseException:
            if entry is not None and not (cancelled is not None and cancelled()):
                with self._condition:
                    if self._entries.get(key) is entry:
                        self._entries.pop(key)
                        entry.stale = True
            raise
        finally:
            disposable = []
            with self._condition:
                if flight is not None:
                    flight.consumers.remove(consumer)
                    # A completed load reserved a lease even if Stop won the
                    # race before this waiter observed the published entry.
                    if entry is None and flight.done:
                        entry = flight.entry
                if entry is not None:
                    entry.leases = max(0, entry.leases - 1)
                    if entry.stale:
                        candidate = self._take_for_disposal_locked(entry)
                        if candidate is not None:
                            disposable.append(candidate)
            self._dispose_entries(disposable)

    def _stopped(self, flight):
        with self._condition:
            if not flight.abandoned:
                flight.abandoned = all(
                    check is not None and check()
                    for check, _progress, _bytes in flight.consumers
                )
            return flight.abandoned

    def _notify(self, flight, index, *args):
        with self._condition:
            callbacks = [consumer[index] for consumer in flight.consumers
                         if consumer[index] is not None
                         and (consumer[0] is None or not consumer[0]())]
        for callback in callbacks:
            callback(*args)

    def _load(self, key, flight, loader, background=False):
        download_dir = None
        directory_guard = None
        dataset = None
        try:
            if self._stopped(flight):
                raise DownloadCancelled("forecast-model download cancelled")
            download_dir = self._new_download_directory(key)
            with self._condition:
                self._downloads[download_dir] = None
            if self._directory_protector is not None:
                directory_guard = self._directory_protector(download_dir)
                directory_guard.__enter__()
            dataset, source = loader(download_dir)
            source_grib = None
            source_fields = ()
            source_transport = None
            if source is not None:
                source_grib = getattr(
                    source, "_sharpmod_source_url", getattr(source, "grib", None)
                )
                source_fields = tuple(
                    getattr(source, "_sharpmod_fields", ()) or ()
                )
                source_transport = getattr(
                    source, "_sharpmod_transport", None
                )
            if self._metadata_writer is not None:
                try:
                    self._metadata_writer(
                        download_dir,
                        source_url=(str(source_grib) if source_grib else None),
                        source_transport=(
                            str(source_transport) if source_transport else None
                        ),
                        source_fields=source_fields,
                    )
                except Exception:
                    _LOGGER.exception(
                        "model_hour_cache.metadata_write_failed model=%s dir=%s",
                        key.model, download_dir,
                    )
            entry = ModelHourEntry(
                key=key,
                dataset=dataset,
                source_grib=str(source_grib) if source_grib else None,
                download_dir=download_dir,
                source_fields=source_fields,
                source_transport=(
                    str(source_transport) if source_transport else None
                ),
                directory_guard=directory_guard,
            )
            post_load_victims = []
            with self._condition:
                entry.leases = len(flight.consumers)
                self._downloads[download_dir] = entry
                if flight.generation == self._generation and not self._stopped(flight):
                    post_load_victims = self._make_room_locked()
                    self._entries[key] = entry
                    self._entries.move_to_end(key)
                else:
                    # A clear occurred while the loader was running.  The
                    # worker may finish this lease, but the result must not be
                    # reinserted after application shutdown.
                    entry.stale = True
                flight.entry = entry
                flight.done = True
                if self._inflight.get(key) is flight:
                    self._inflight.pop(key)
                if entry.stale:
                    candidate = self._take_for_disposal_locked(entry)
                    if candidate is not None:
                        post_load_victims.append(candidate)
                self._condition.notify_all()
            self._dispose_entries(post_load_victims)
        except BaseException as exc:
            try:
                if download_dir is not None:
                    self._dispose_entries([ModelHourEntry(
                        key, dataset, None, download_dir,
                        directory_guard=directory_guard,
                    )])
            finally:
                with self._condition:
                    flight.error = exc
                    flight.done = True
                    if self._inflight.get(key) is flight:
                        self._inflight.pop(key)
                    self._condition.notify_all()
        finally:
            if background:
                with self._condition:
                    self._background_loads -= 1
                    self._condition.notify_all()

    def protected_directories(self) -> tuple[str, ...]:
        """Sources still loading, leased, or closing cannot be manually removed."""
        with self._condition:
            return tuple(path for path, entry in self._downloads.items()
                         if entry is None or entry.leases or entry.disposed)

    def clear(self) -> None:
        """Evict every entry, deferring active-entry disposal to lease exit."""
        disposable = []
        with self._condition:
            self._generation += 1
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.stale = True
                candidate = self._take_for_disposal_locked(entry)
                if candidate is not None:
                    disposable.append(candidate)
        self._dispose_entries(disposable)

    def _make_room_locked(self) -> list[ModelHourEntry]:
        disposable = []
        while len(self._entries) >= self._max_entries:
            _old_key, old_entry = self._entries.popitem(last=False)
            old_entry.stale = True
            candidate = self._take_for_disposal_locked(old_entry)
            if candidate is not None:
                disposable.append(candidate)
        return disposable

    @staticmethod
    def _take_for_disposal_locked(
        entry: ModelHourEntry,
    ) -> ModelHourEntry | None:
        if entry.leases or entry.disposed:
            return None
        entry.disposed = True
        return entry

    def _dispose_entries(self, entries) -> None:
        for entry in entries:
            try:
                close = getattr(entry.dataset, "close", None)
                if callable(close):
                    close()
            except Exception:
                _LOGGER.exception(
                    "model_hour_cache.close_failed model=%s dir=%s",
                    entry.key.model,
                    entry.download_dir,
                )
            finally:
                if entry.directory_guard is not None:
                    try:
                        entry.directory_guard.__exit__(None, None, None)
                    except Exception:
                        _LOGGER.exception(
                            "model_hour_cache.guard_close_failed model=%s dir=%s",
                            entry.key.model,
                            entry.download_dir,
                        )
                if self._delete_download_dirs:
                    shutil.rmtree(
                        os.fspath(entry.download_dir), ignore_errors=True
                    )
                with self._condition:
                    self._downloads.pop(entry.download_dir, None)

    def _new_download_directory(self, key: ModelHourKey) -> str:
        if self._directory_factory is None:
            return tempfile.mkdtemp(prefix=self._directory_prefix(key))
        path = os.fspath(self._directory_factory(key))
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _directory_prefix(key: ModelHourKey) -> str:
        model = "".join(
            character if character.isalnum() else "_" for character in key.model
        )
        spatial = ""
        if key.spatial:
            spatial_value = "".join(
                character if character.isalnum() else "_"
                for character in key.spatial
            )
            spatial = f"p{spatial_value}_"
        return (
            f"sharpmod_hour_{model}_{key.run_time:%Y%m%d%H}_"
            f"f{key.fxx:03d}_{spatial}"
        )
