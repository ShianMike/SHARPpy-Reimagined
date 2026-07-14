"""Bounded ownership for decoded forecast-model hours used by the GUI."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import shutil
import tempfile
import threading
from typing import Callable, Iterator


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
    waiters: int = 0
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
        self._generation = 0
        self._directory_factory = directory_factory
        self._directory_protector = directory_protector
        self._delete_download_dirs = bool(delete_download_dirs)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @contextmanager
    def lease(
        self,
        key: ModelHourKey,
        loader: Callable[[str], tuple[object, object]],
    ) -> Iterator[tuple[ModelHourEntry, bool]]:
        """Yield ``(entry, cache_hit)`` and keep its resources alive."""
        if not isinstance(key, ModelHourKey):
            raise TypeError("key must be a ModelHourKey")

        entry = None
        victims = []
        leader = False
        flight = None
        with self._condition:
            cached = self._entries.get(key)
            if cached is not None and not cached.stale:
                cached.leases += 1
                self._entries.move_to_end(key)
                entry = cached
                cache_hit = True
            else:
                flight = self._inflight.get(key)
                if flight is not None:
                    flight.waiters += 1
                    while not flight.done:
                        self._condition.wait()
                    if flight.error is not None:
                        raise flight.error
                    entry = flight.entry
                    if entry is None:
                        raise RuntimeError(
                            "model-hour loader completed without an entry"
                        )
                    # The leader reserved this follower's lease before waking
                    # it, preventing clear/eviction from disposing the entry.
                    cache_hit = True
                else:
                    flight = _Flight(generation=self._generation)
                    self._inflight[key] = flight
                    cache_hit = False
                    leader = True
                    victims = self._make_room_locked()

        self._dispose_entries(victims)

        if leader:
            download_dir = None
            directory_guard = None
            try:
                download_dir = self._new_download_directory(key)
                if self._directory_protector is not None:
                    directory_guard = self._directory_protector(download_dir)
                    directory_guard.__enter__()
                dataset, source = loader(download_dir)
            except BaseException as exc:
                if directory_guard is not None:
                    directory_guard.__exit__(type(exc), exc, exc.__traceback__)
                if self._delete_download_dirs and download_dir is not None:
                    shutil.rmtree(download_dir, ignore_errors=True)
                with self._condition:
                    flight.error = exc
                    flight.done = True
                    self._inflight.pop(key, None)
                    self._condition.notify_all()
                raise

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
            entry = ModelHourEntry(
                key=key,
                dataset=dataset,
                source_grib=str(source_grib) if source_grib else None,
                download_dir=download_dir,
                source_fields=source_fields,
                source_transport=(
                    str(source_transport) if source_transport else None
                ),
                leases=1 + flight.waiters,
                directory_guard=directory_guard,
            )
            post_load_victims = []
            with self._condition:
                if flight.generation == self._generation:
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
                self._inflight.pop(key, None)
                self._condition.notify_all()
            self._dispose_entries(post_load_victims)

        _LOGGER.info(
            "model_hour_cache.%s model=%s run=%s fxx=%03d member=%s dir=%s",
            "hit" if cache_hit else "miss",
            key.model,
            key.run_time.isoformat(),
            key.fxx,
            key.member,
            entry.download_dir,
        )
        try:
            yield entry, cache_hit
        except BaseException:
            # A dataset that fails during extraction must not poison every
            # later point request for the same hour. Active parallel leases
            # remain valid until they finish; disposal is reference-counted.
            with self._condition:
                if self._entries.get(key) is entry:
                    self._entries.pop(key, None)
                    entry.stale = True
            raise
        finally:
            disposable = []
            with self._condition:
                entry.leases = max(0, entry.leases - 1)
                if entry.stale:
                    candidate = self._take_for_disposal_locked(entry)
                    if candidate is not None:
                        disposable.append(candidate)
            self._dispose_entries(disposable)

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
