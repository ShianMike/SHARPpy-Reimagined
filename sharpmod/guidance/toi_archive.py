"""Resumable, bounded archive retrieval for historical TOI case extraction.

Building a 2015-2025 TOI dataset means thousands of bounded HRRR subset
downloads.  That job has to survive crashes, network faults, rate limits, and a
full disk without corrupting its own output or silently degrading the science.
This module supplies that runner, and nothing else: feature extraction itself is
delegated verbatim to the operational producer via
:func:`sharpmod.guidance.toi_dataset.extract_toi_case`, so an archived case sees
exactly the live jet tracking, objective risk region, STP proxy, scorecard, and
three-hourly temporal sampling.

Design rules that matter for correctness rather than speed:

* **Deterministic cache keys.** A case's identity is a hash of every input that
  changes its features, including the method version.  Changing the sampling
  interval or the region radius produces a different key instead of quietly
  reusing an incompatible cached result.
* **Atomic writes.** Every artifact is written to a ``.partial`` file and then
  ``os.replace``-d, so a crash mid-write can never leave a half-parsed manifest.
* **Explicit outcomes.** Every case ends as ``success``, ``skipped``, or
  ``failed`` with a stated reason.  There is no silent drop, and a missing field
  is never substituted with a fallback field.
* **Bounded everything.** Transfer bytes, retained bytes, case count, wall time,
  concurrency, and free-disk headroom are all capped, and the runner stops with
  a named reason rather than exhausting the machine.
* **Resume by checkpoint.** Completed case keys are appended to a JSONL
  checkpoint; a rerun skips them, so an interrupted multi-hour job continues
  instead of restarting.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import shutil
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from sharpmod.model_transport import DownloadCancelled

from .hrrr import (
    DEFAULT_GRID_STRIDE,
    DEFAULT_REGION_RADIUS_KM,
    DEFAULT_RISK_STP_THRESHOLD,
    HRRR_TOI_METHOD_VERSION,
    TOI_SAMPLING_INTERVAL_HOURS,
    _great_circle_grid_km,
    fixed_layer_stp_proxy,
    toi_sampling_hours,
)
from .schemas import GuidanceState
from .toi_dataset import TOICase, TOIDatasetError, extract_toi_case
from .toi_evaluation import strict_json_dumps
from .toi_risk_objects import (
    TOI_RISK_OBJECT_METHOD_VERSION,
    TOIRiskObjectError,
    select_risk_object,
)
from .toi_strata import conus_region

TOI_ARCHIVE_RUNNER_VERSION = "sharpmod_toi_archive_runner_v1"

#: Official NOAA Open Data HRRR archive.  HRRR objects begin 2014-07-30 on this
#: bucket; the TOI programme targets complete years from 2015 onward.
HRRR_OPEN_DATA_BUCKET = "noaa-hrrr-bdp-pds"
HRRR_OPEN_DATA_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_OPEN_DATA_LICENSE = (
    "NOAA Open Data Dissemination (NODD); U.S. Government work, public domain"
)
HRRR_ARCHIVE_FIRST_YEAR = 2015
HRRR_ARCHIVE_LAST_YEAR = 2025

#: Official NCEI Storm Events bulk CSV directory used for verified outcomes.
NCEI_STORM_EVENTS_BASE_URL = (
    "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
)
NCEI_STORM_EVENTS_LICENSE = (
    "NOAA NCEI Storm Events Database; U.S. Government work, public domain"
)

#: Measured on 2026-08-05 from the official ``.idx`` files: the operational TOI
#: search matches exactly eight GRIB messages in every HRRR era, and the bounded
#: subset is roughly 8 MiB (v1-v3) to 11 MiB (v4) versus an 86-151 MiB full
#: ``wrfsfc`` file.  Kept as a documented fallback for offline estimates.
MEASURED_SUBSET_MIB_BY_ERA = {
    "HRRRv1": 7.98,
    "HRRRv2": 8.05,
    "HRRRv3": 8.07,
    "HRRRv4": 10.78,
}
MEASURED_MESSAGES_PER_FRAME = 8
#: Conservative planning value: the largest measured era.
PLANNING_SUBSET_MIB = 10.78

#: A fixed CONUS centre used only to resolve a case anchor from forecast fields.
#: It is a constant, so it cannot encode anything about what was later observed.
CONUS_ANCHOR_CENTRE = (39.0, -98.0)
CONUS_ANCHOR_RADIUS_KM = 2600.0
#: Additional issuance-time forecast hours sampled for anchor selection, so the
#: risk object reflects the window rather than one instant.  All are inside the
#: published 18-hour window and available at the same cycle.
ANCHOR_EXTRA_HOURS: tuple[int, ...] = (12, 18)

#: MEASURED 2026-08-05 against the official bucket: at 06Z, HRRRv1 and the
#: pre-v2 period publish forecasts only through F15; F18 first appears with
#: HRRRv2 (2016-08-23).  A 2015-2016 case therefore tops out at six sampled
#: frames and 15 h of coverage, which the runner reports as ``degraded`` rather
#: than silently accepting as complete.  This is a real, era-dependent sampling
#: non-stationarity that per-era stratified reporting exists to expose.
HRRR_F18_AVAILABLE_FROM = datetime(2016, 8, 23, tzinfo=UTC)
HRRR_LEGACY_MAXIMUM_FORECAST_HOUR = 15


def maximum_available_forecast_hour(run_time: datetime) -> int:
    """Return the largest 06Z forecast hour the archive publishes for a cycle."""

    moment = (
        run_time.replace(tzinfo=UTC) if run_time.tzinfo is None else run_time
    ).astimezone(UTC)
    if moment < HRRR_F18_AVAILABLE_FROM:
        return HRRR_LEGACY_MAXIMUM_FORECAST_HOUR
    return 18


class TOIArchiveError(RuntimeError):
    """Raised when an archive run cannot proceed safely."""


class FrameNotPublished(TOIArchiveError):
    """A requested forecast hour does not exist in this archive era.

    Distinct from a transport fault: retrying cannot help, so the fetcher raises
    this immediately instead of exhausting its attempt budget.
    """


class RunDirectoryLock:
    """Exclusive advisory lock over one run directory.

    Found by the pilot: two runners started against the same directory both
    treated the same cases as outstanding and duplicated the transfer.  An
    unattended multi-hour job must be single-writer, so the lock is taken with
    ``O_EXCL`` and records the owning pid for diagnosis.  A lock older than
    ``stale_after_seconds`` is reclaimed, so a hard crash does not wedge the run
    forever.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        stale_after_seconds: float = 6 * 3600.0,
    ):
        self.path = Path(path)
        self.stale_after_seconds = float(stale_after_seconds)
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                handle = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError as exc:
                existing = exc
                age = None
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:  # pragma: no cover - raced removal
                    continue
                if age is not None and age > self.stale_after_seconds:
                    self.path.unlink(missing_ok=True)
                    continue
                owner = ""
                with contextlib.suppress(OSError):
                    owner = self.path.read_text(encoding="utf-8").strip()
                raise TOIArchiveError(
                    f"run directory is locked by another process ({owner}); "
                    "a run directory must have a single writer so resumable "
                    "work is not duplicated. Remove "
                    f"{self.path} only if no runner is active."
                ) from existing
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(f"pid={os.getpid()} acquired_at={_now()}\n")
            self._held = True
            return
        raise TOIArchiveError(f"could not acquire run lock {self.path}")

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> RunDirectoryLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_text(path: Path, text: str) -> Path:
    """Write via a ``.partial`` sibling then rename, so readers never see half."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with open(partial, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    return path


#: Version of the scientific-content hash definition.  Bump this whenever the
#: set of hashed fields changes, so an old hash is never silently compared
#: against a new definition.
SCIENTIFIC_CONTENT_HASH_VERSION = "toi_scientific_content_sha256_v1"

#: Exactly the fields that define a case scientifically.  Volatile operational
#: metadata - timestamps, retry logs, elapsed time, transferred bytes - is
#: excluded, so an identical rerun on a different day with different retries
#: produces an identical hash.
SCIENTIFIC_HASH_FIELDS = (
    "hash_version",
    "cache_key",
    "cache_inputs",
    "case",
    "anchor",
    "guidance",
    "method_versions",
    "sources",
)


def scientific_content_hash(payload: Mapping[str, Any]) -> str:
    """Hash only the versioned scientific content of a case payload.

    The hash is computed over canonical strict JSON (sorted keys, no NaN) of a
    fixed field set.  It deliberately excludes ``extracted_at``, ``retry_log``,
    ``seconds``, and ``transfer_bytes``: those describe *how* the case was
    fetched, not *what* was computed, and including them made identical reruns
    produce different hashes.
    """

    missing = [name for name in SCIENTIFIC_HASH_FIELDS if name not in payload]
    if missing:
        raise TOIArchiveError(
            "scientific hash payload is missing field(s): " + ", ".join(missing)
        )
    canonical = {name: payload[name] for name in SCIENTIFIC_HASH_FIELDS}
    serialized = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    """Hash the final bytes of a written artifact."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


def _directory_bytes(path: str | os.PathLike[str]) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:  # pragma: no cover - transient during cleanup
                continue
    return total


def _numeric_leaves(value: Any, pointer: str = "$"):
    """Yield ``(json_pointer, float)`` for every numeric leaf in a payload."""

    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield pointer, float(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _numeric_leaves(item, f"{pointer}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _numeric_leaves(item, f"{pointer}[{index}]")


@dataclass(frozen=True)
class RunBudget:
    """Hard resource ceilings for one archive run."""

    #: Total bytes this run may transfer before stopping.
    maximum_transfer_bytes: int = 8 * 1024**3
    #: Cases this run may complete before stopping.
    maximum_cases: int = 24
    #: Wall-clock ceiling for the whole run.
    maximum_seconds: float = 3600.0
    #: Free disk that must remain available at all times.
    minimum_free_bytes: int = 12 * 1024**3
    #: Concurrent cases.  One is the archive-friendly default; frames inside a
    #: case always stay sequential because the operational producer is
    #: sequential and that behaviour must not change.
    maximum_concurrent_cases: int = 1
    #: Minimum spacing between archive requests, to respect the service.
    minimum_request_interval_seconds: float = 0.2
    #: Attempts per frame, including the first.
    maximum_attempts: int = 4
    #: Base for exponential backoff, with full jitter.
    backoff_base_seconds: float = 2.0
    backoff_maximum_seconds: float = 60.0
    #: Delete each raw GRIB subset after successful extraction.  Retained output
    #: is then only the compact per-case JSON, which is what makes a
    #: multi-thousand-case run fit on a laptop.
    discard_raw_after_extract: bool = True

    def __post_init__(self) -> None:
        for name in (
            "maximum_transfer_bytes",
            "maximum_cases",
            "minimum_free_bytes",
            "maximum_concurrent_cases",
            "maximum_attempts",
        ):
            value = int(getattr(self, name))
            if value < 1:
                raise TOIArchiveError(f"{name} must be at least one")
            object.__setattr__(self, name, value)
        if self.maximum_concurrent_cases > 4:
            raise TOIArchiveError(
                "maximum_concurrent_cases is capped at 4 so a bulk run cannot "
                "become an unbounded parallel download against a public archive"
            )
        for name in (
            "maximum_seconds",
            "minimum_request_interval_seconds",
            "backoff_base_seconds",
            "backoff_maximum_seconds",
        ):
            value = float(getattr(self, name))
            if value < 0 or not math.isfinite(value):
                raise TOIArchiveError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)

    def to_mapping(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class CaseEstimate:
    """Per-case and whole-catalog resource estimate."""

    frames_per_case: int
    subset_mib_per_frame: float
    messages_per_frame: int
    requests_per_frame: int
    seconds_per_frame: float
    retained_kib_per_case: float
    anchor_frames_per_case: int = 0

    @property
    def transfer_mib_per_case(self) -> float:
        frames = self.frames_per_case + self.anchor_frames_per_case
        return frames * self.subset_mib_per_frame

    @property
    def requests_per_case(self) -> int:
        frames = self.frames_per_case + self.anchor_frames_per_case
        return frames * self.requests_per_frame

    @property
    def seconds_per_case(self) -> float:
        frames = self.frames_per_case + self.anchor_frames_per_case
        return frames * self.seconds_per_frame

    def for_cases(self, cases: int) -> dict[str, Any]:
        count = max(0, int(cases))
        transfer_gib = self.transfer_mib_per_case * count / 1024.0
        return {
            "cases": count,
            "frames": (self.frames_per_case + self.anchor_frames_per_case) * count,
            "transfer_gib": round(transfer_gib, 2),
            "transfer_bytes": int(transfer_gib * 1024**3),
            "requests": self.requests_per_case * count,
            "wall_hours": round(self.seconds_per_case * count / 3600.0, 2),
            "retained_mib": round(self.retained_kib_per_case * count / 1024.0, 2),
            "peak_raw_mib_if_discarding": round(self.subset_mib_per_frame * 2, 2),
        }

    def to_mapping(self) -> dict[str, Any]:
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__}
        payload.update(
            {
                "transfer_mib_per_case": round(self.transfer_mib_per_case, 2),
                "requests_per_case": self.requests_per_case,
                "seconds_per_case": round(self.seconds_per_case, 2),
            }
        )
        return payload


def default_case_estimate(
    *,
    forecast_hour: int = 6,
    sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    subset_mib_per_frame: float = PLANNING_SUBSET_MIB,
    seconds_per_frame: float = 9.0,
    resolve_anchor: bool = True,
) -> CaseEstimate:
    """Return the documented planning estimate for one archived case."""

    frames = len(
        toi_sampling_hours(forecast_hour, interval_hours=sampling_interval_hours)
    )
    return CaseEstimate(
        frames_per_case=frames,
        subset_mib_per_frame=float(subset_mib_per_frame),
        messages_per_frame=MEASURED_MESSAGES_PER_FRAME,
        # One index request plus a handful of grouped byte-range requests.
        requests_per_frame=5,
        seconds_per_frame=float(seconds_per_frame),
        # One compact JSON feature record per case.
        retained_kib_per_case=4.0,
        anchor_frames_per_case=1 if resolve_anchor else 0,
    )


def audit_local_resources(
    *,
    working_directory: str | os.PathLike[str],
    cache_directories: Sequence[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    """Measure disk headroom and existing caches before planning a run."""

    working = Path(working_directory).expanduser().resolve()
    working.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(working)
    caches = []
    for candidate in cache_directories:
        path = Path(candidate).expanduser()
        if not path.exists():
            caches.append({"path": str(path), "present": False})
            continue
        size = _directory_bytes(path)
        caches.append(
            {
                "path": str(path),
                "present": True,
                "bytes": size,
                "mib": round(size / 1024**2, 1),
            }
        )
    return {
        "audited_at": _now(),
        "working_directory": str(working),
        "disk_total_gib": round(usage.total / 1024**3, 1),
        "disk_used_gib": round(usage.used / 1024**3, 1),
        "disk_free_gib": round(usage.free / 1024**3, 1),
        "disk_free_bytes": int(usage.free),
        "caches": caches,
    }


def archive_source_record() -> dict[str, Any]:
    """Return the documented provenance of every external source used."""

    return {
        "hrrr": {
            "name": "NOAA High-Resolution Rapid Refresh, NOAA Open Data",
            "bucket": HRRR_OPEN_DATA_BUCKET,
            "base_url": HRRR_OPEN_DATA_BASE_URL,
            "product": "conus wrfsfc (surface/diagnostic) GRIB2, byte-range subset",
            "first_year_used": HRRR_ARCHIVE_FIRST_YEAR,
            "last_year_used": HRRR_ARCHIVE_LAST_YEAR,
            "license": HRRR_OPEN_DATA_LICENSE,
            "messages_per_frame": MEASURED_MESSAGES_PER_FRAME,
            "measured_subset_mib_by_era": dict(MEASURED_SUBSET_MIB_BY_ERA),
        },
        "outcomes": {
            "name": "NOAA NCEI Storm Events Database (bulk CSV)",
            "base_url": NCEI_STORM_EVENTS_BASE_URL,
            "file_pattern": "StormEvents_details-ftp_v1.0_d{year}_c{created}.csv.gz",
            "license": NCEI_STORM_EVENTS_LICENSE,
            "note": (
                "Creation-date suffixes are versions; record the exact file "
                "name and hash used for a dataset."
            ),
        },
        "runner_version": TOI_ARCHIVE_RUNNER_VERSION,
        "feature_method_version": HRRR_TOI_METHOD_VERSION,
    }


def case_cache_key(
    case: TOICase,
    *,
    sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
    region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
    grid_stride: int = DEFAULT_GRID_STRIDE,
    method_version: str = HRRR_TOI_METHOD_VERSION,
    anchor_method_version: str = TOI_RISK_OBJECT_METHOD_VERSION,
) -> str:
    """Return a deterministic key covering every feature-changing input.

    The anchor selection method is one of those inputs: the case anchor is
    resolved from forecast fields at collection time, so changing how it is
    resolved changes every feature derived at that point.  It was originally
    omitted, which meant an anchor-method fix left the key identical and a resume
    silently skipped every already-collected case, yielding a stale dataset that
    merely looked recollected.
    """

    payload = {
        "event_id": case.event_id,
        "run_time": case.run_time.isoformat(),
        "forecast_hour": case.forecast_hour,
        "latitude": round(float(case.latitude), 4),
        "longitude": round(float(case.longitude), 4),
        "anchor_source": case.anchor_source,
        "sampling_interval_hours": int(sampling_interval_hours),
        "region_radius_km": round(float(region_radius_km), 3),
        "grid_stride": int(grid_stride),
        "method_version": str(method_version),
        "anchor_method_version": str(anchor_method_version),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class CaseOutcome:
    """The explicit result of attempting one archived case."""

    event_id: str
    run_time: str
    cache_key: str
    status: str
    reason: str = ""
    transfer_bytes: int = 0
    seconds: float = 0.0
    frames_requested: int = 0
    frames_decoded: int = 0
    attempts: int = 0
    scientific_content_sha256: str = ""
    artifact_sha256: str = ""
    sampling_status: str = ""
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"success", "skipped", "failed"}:
            raise TOIArchiveError(
                "case status must be 'success', 'skipped', or 'failed'"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_time": self.run_time,
            "cache_key": self.cache_key,
            "status": self.status,
            "reason": self.reason,
            "transfer_bytes": self.transfer_bytes,
            "seconds": round(self.seconds, 3),
            "frames_requested": self.frames_requested,
            "frames_decoded": self.frames_decoded,
            "attempts": self.attempts,
            "scientific_content_sha256": self.scientific_content_sha256,
            "artifact_sha256": self.artifact_sha256,
            "sampling_status": self.sampling_status,
        }


class ResilientFrameFetcher:
    """Wrap the operational frame fetcher with retries, limits, and accounting.

    The wrapped callable keeps the exact signature the operational producer
    expects, so temporal sampling and feature extraction remain untouched.  Each
    frame downloads into its own directory, which makes the transferred byte
    count exact and lets the raw subset be discarded immediately after decode.
    """

    def __init__(
        self,
        *,
        budget: RunBudget,
        root: Path,
        inner: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int = 20260805,
    ):
        self._budget = budget
        self._root = Path(root)
        self._inner = inner
        self._sleep = sleep
        self._monotonic = monotonic
        self._random = random.Random(seed)
        self._last_request_at: float | None = None
        self.transfer_bytes = 0
        self.attempts = 0
        self.frames_decoded = 0
        self.retry_log: list[str] = []

    def _resolve_inner(self) -> Callable[..., Any]:
        if self._inner is not None:
            return self._inner
        from .hrrr import fetch_hrrr_regional_frame

        return fetch_hrrr_regional_frame

    def _respect_rate_limit(self) -> None:
        interval = self._budget.minimum_request_interval_seconds
        if interval <= 0 or self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        if elapsed < interval:
            self._sleep(interval - elapsed)

    def _backoff(self, attempt: int) -> float:
        # Exponential with full jitter; deterministic under a seeded Random so
        # a replayed run is reproducible.
        ceiling = min(
            self._budget.backoff_maximum_seconds,
            self._budget.backoff_base_seconds * (2 ** (attempt - 1)),
        )
        return self._random.uniform(0.0, max(0.0, ceiling))

    def __call__(
        self,
        run_time,
        forecast_hour,
        point_latitude,
        point_longitude,
        pressure_level_hpa,
        *,
        download_dir=None,
        progress_callback=None,
        cancelled=None,
        region_radius_km=DEFAULT_REGION_RADIUS_KM,
        grid_stride=DEFAULT_GRID_STRIDE,
    ):
        # A frame the archive provably never published is not a transport
        # fault, so retrying it only burns wall time and requests.  MEASURED:
        # four attempts with backoff against a pre-HRRRv2 F18 cost tens of
        # seconds per case and can never succeed.
        available = maximum_available_forecast_hour(run_time)
        if int(forecast_hour) > available:
            raise FrameNotPublished(
                f"F{int(forecast_hour):03d} is not published for the "
                f"{run_time.isoformat()} cycle; this archive era serves at most "
                f"F{available:03d}"
            )
        inner = self._resolve_inner()
        frame_dir = self._root / f"f{int(forecast_hour):03d}-{os.getpid()}"
        last_error: Exception | None = None
        for attempt in range(1, self._budget.maximum_attempts + 1):
            if cancelled is not None and cancelled():
                raise DownloadCancelled("archive run cancelled")
            if frame_dir.exists():
                shutil.rmtree(frame_dir, ignore_errors=True)
            frame_dir.mkdir(parents=True, exist_ok=True)
            self._respect_rate_limit()
            self.attempts += 1
            self._last_request_at = self._monotonic()
            try:
                frame = inner(
                    run_time,
                    forecast_hour,
                    point_latitude,
                    point_longitude,
                    pressure_level_hpa,
                    download_dir=str(frame_dir),
                    progress_callback=progress_callback,
                    cancelled=cancelled,
                    region_radius_km=region_radius_km,
                    grid_stride=grid_stride,
                )
            except DownloadCancelled:
                shutil.rmtree(frame_dir, ignore_errors=True)
                raise
            except Exception as exc:  # retryable archive/transport fault
                last_error = exc
                self.transfer_bytes += _directory_bytes(frame_dir)
                shutil.rmtree(frame_dir, ignore_errors=True)
                self.retry_log.append(
                    f"F{int(forecast_hour):03d} attempt {attempt}: "
                    f"{type(exc).__name__}: {exc}"
                )
                if attempt >= self._budget.maximum_attempts:
                    break
                self._sleep(self._backoff(attempt))
                continue
            self.transfer_bytes += _directory_bytes(frame_dir)
            self.frames_decoded += 1
            if self._budget.discard_raw_after_extract:
                shutil.rmtree(frame_dir, ignore_errors=True)
            return frame
        shutil.rmtree(frame_dir, ignore_errors=True)
        raise TOIArchiveError(
            f"F{int(forecast_hour):03d} failed after "
            f"{self._budget.maximum_attempts} attempts: {last_error}"
        )


def resolve_forecast_anchor(
    run_time: datetime,
    forecast_hour: int,
    *,
    fetcher: Callable[..., Any],
    centre: tuple[float, float] = CONUS_ANCHOR_CENTRE,
    radius_km: float = CONUS_ANCHOR_RADIUS_KM,
    grid_stride: int = 12,
    pressure_level_hpa: int = 500,
    extra_hours: Sequence[int] = ANCHOR_EXTRA_HOURS,
    risk_threshold: float = DEFAULT_RISK_STP_THRESHOLD,
    **object_rules: Any,
) -> tuple[float, float, dict[str, Any]]:
    """Pick a case anchor from issuance-time forecast fields only.

    The search starts from a *fixed* CONUS centre, so the domain cannot encode
    anything about the outcome, and the anchor is the centroid of the
    highest-scoring valid *risk object* rather than one grid maximum.  Objects
    must clear documented minimum support, area, intensity, and land-fraction
    rules; if none does, the anchor is explicitly unavailable.
    """

    frames = []
    unpublished: list[int] = []
    for hour in sorted({int(forecast_hour), *(int(h) for h in extra_hours)}):
        try:
            frames.append(
                fetcher(
                    run_time,
                    hour,
                    float(centre[0]),
                    float(centre[1]),
                    int(pressure_level_hpa),
                    download_dir=None,
                    progress_callback=None,
                    cancelled=None,
                    region_radius_km=float(radius_km),
                    grid_stride=int(grid_stride),
                )
            )
        except FrameNotPublished:
            # MEASURED: 06Z cycles before HRRRv2 publish nothing past F015, so
            # the extra anchor hours are simply absent for 2015-2016 cases.  A
            # missing era frame must degrade the anchor search, not fail the
            # case, otherwise every pre-v2 case is unusable and the archive
            # silently loses two development years.
            unpublished.append(int(hour))
            continue
    if not frames:
        raise TOIArchiveError(
            "anchor resolution found no published frame for hours "
            + ",".join(str(hour) for hour in unpublished)
        )
    reference = frames[0]
    stp = np.stack([fixed_layer_stp_proxy(frame) for frame in frames])
    if not np.any(np.isfinite(stp)):
        raise TOIArchiveError(
            "anchor resolution found no finite proxy STP in the CONUS domain"
        )
    try:
        selected, provenance = select_risk_object(
            stp,
            reference.latitude,
            reference.longitude,
            threshold=risk_threshold,
            **object_rules,
        )
    except TOIRiskObjectError as exc:
        # An unavailable anchor is an explicit outcome, never a fallback pick.
        raise TOIArchiveError(f"anchor unavailable: {exc}") from exc

    latitude = selected.centroid_latitude
    longitude = selected.centroid_longitude
    distance = float(
        _great_circle_grid_km(
            np.asarray([[latitude]]),
            np.asarray([[longitude]]),
            float(centre[0]),
            float(centre[1]),
        )[0, 0]
    )
    provenance.update(
        {
            "anchor_source": "model_forecast_maximum_stp",
            "anchor_forecast_hours": ",".join(
                str(frame.forecast_hour) for frame in frames
            ),
            "anchor_peak_proxy_stp": round(float(np.nanmax(stp)), 3),
            "anchor_distance_from_fixed_centre_km": round(distance, 1),
            "anchor_search_centre": f"{centre[0]:g},{centre[1]:g}",
            "anchor_search_radius_km": f"{float(radius_km):g}",
            "anchor_resolved_region": conus_region(latitude, longitude),
            "anchor_unpublished_hours": (
                ",".join(str(hour) for hour in unpublished) or "none"
            ),
            "anchor_frames_complete": not unpublished,
        }
    )
    return latitude, longitude, provenance


@dataclass
class RunReport:
    """Accumulated result of one archive run."""

    started_at: str
    runner_version: str = TOI_ARCHIVE_RUNNER_VERSION
    outcomes: list[CaseOutcome] = field(default_factory=list)
    stop_reason: str = "completed"
    transfer_bytes: int = 0
    seconds: float = 0.0
    budget: Mapping[str, Any] = field(default_factory=dict)
    sources: Mapping[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> list[CaseOutcome]:
        return [item for item in self.outcomes if item.status == "success"]

    @property
    def failed(self) -> list[CaseOutcome]:
        return [item for item in self.outcomes if item.status == "failed"]

    @property
    def skipped(self) -> list[CaseOutcome]:
        return [item for item in self.outcomes if item.status == "skipped"]

    def to_mapping(self) -> dict[str, Any]:
        successes = self.succeeded
        return {
            "runner_version": self.runner_version,
            "started_at": self.started_at,
            "finished_at": _now(),
            "stop_reason": self.stop_reason,
            "cases_attempted": len(self.outcomes),
            "cases_succeeded": len(successes),
            "cases_failed": len(self.failed),
            "cases_skipped": len(self.skipped),
            "transfer_bytes": self.transfer_bytes,
            "transfer_mib": round(self.transfer_bytes / 1024**2, 2),
            "seconds": round(self.seconds, 2),
            "measured_mib_per_case": (
                round(self.transfer_bytes / 1024**2 / len(successes), 2)
                if successes
                else None
            ),
            "measured_seconds_per_case": (
                round(self.seconds / len(successes), 2) if successes else None
            ),
            "budget": dict(self.budget),
            "sources": dict(self.sources),
            "outcomes": [item.to_mapping() for item in self.outcomes],
            "experimental_not_official": True,
        }


class ArchiveRunner:
    """Resumable, bounded driver over a historical TOI case list."""

    def __init__(
        self,
        *,
        output_directory: str | os.PathLike[str],
        budget: RunBudget | None = None,
        fetcher: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        resolve_anchor: bool = False,
    ):
        self.root = Path(output_directory).expanduser().resolve()
        self.cases_dir = self.root / "cases"
        self.raw_dir = self.root / "raw"
        self.checkpoint_path = self.root / "checkpoint.jsonl"
        self.lock_path = self.root / "run.lock"
        self.budget = budget or RunBudget()
        self._fetcher = fetcher
        self._sleep = sleep
        self._monotonic = monotonic
        self._resolve_anchor = bool(resolve_anchor)
        for directory in (self.root, self.cases_dir, self.raw_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- checkpointing --------------------------------------------------- #

    def completed_keys(self) -> dict[str, str]:
        """Return terminal checkpoint results that a resume may safely skip.

        Successful cases already have a verified case artifact, while an
        intentional skip is terminal for the same catalogue inputs.  Failed or
        unknown records are deliberately excluded so a later invocation retries
        them instead of silently treating an incomplete run as finished.
        """

        done: dict[str, str] = {}
        for key, record in self.checkpoint_records().items():
            status = str(record.get("status", "unknown"))
            if status in {"success", "skipped"}:
                done[key] = status
        return done

    def _append_checkpoint(self, outcome: CaseOutcome) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(outcome.to_mapping(), sort_keys=True, allow_nan=False)
        with open(self.checkpoint_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- one case -------------------------------------------------------- #

    def _run_case(
        self,
        case: TOICase,
        *,
        sampling_interval_hours: int,
        region_radius_km: float,
        grid_stride: int,
        cancelled: Callable[[], bool] | None,
    ) -> CaseOutcome:
        key = case_cache_key(
            case,
            sampling_interval_hours=sampling_interval_hours,
            region_radius_km=region_radius_km,
            grid_stride=grid_stride,
        )
        started = self._monotonic()
        case_root = self.raw_dir / key
        fetcher = ResilientFrameFetcher(
            budget=self.budget, root=case_root, inner=self._fetcher,
            sleep=self._sleep, monotonic=self._monotonic,
        )
        frames_requested = len(
            toi_sampling_hours(
                case.forecast_hour, interval_hours=sampling_interval_hours
            )
        )
        anchor_provenance: dict[str, Any] = {}
        try:
            resolved = case
            if self._resolve_anchor:
                latitude, longitude, anchor_provenance = resolve_forecast_anchor(
                    case.run_time, case.forecast_hour, fetcher=fetcher
                )
                resolved = replace(
                    case,
                    latitude=latitude,
                    longitude=longitude,
                    anchor_source="model_forecast_maximum_stp",
                )
                frames_requested += 1
            guidance = extract_toi_case(
                resolved,
                fetcher=fetcher,
                download_dir=None,
                sampling_interval_hours=sampling_interval_hours,
                region_radius_km=region_radius_km,
                grid_stride=grid_stride,
                cancelled=cancelled,
            )
        except DownloadCancelled:
            shutil.rmtree(case_root, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(case_root, ignore_errors=True)
            return CaseOutcome(
                event_id=case.event_id,
                run_time=case.run_time.isoformat(),
                cache_key=key,
                status="failed",
                reason=f"{type(exc).__name__}: {exc}",
                transfer_bytes=fetcher.transfer_bytes,
                seconds=self._monotonic() - started,
                frames_requested=frames_requested,
                frames_decoded=fetcher.frames_decoded,
                attempts=fetcher.attempts,
            )
        finally:
            if self.budget.discard_raw_after_extract:
                shutil.rmtree(case_root, ignore_errors=True)

        product = guidance.toi
        if product.state is GuidanceState.UNAVAILABLE or product.features is None:
            return CaseOutcome(
                event_id=case.event_id,
                run_time=case.run_time.isoformat(),
                cache_key=key,
                status="skipped",
                reason=product.reason or "TOI features unavailable",
                transfer_bytes=fetcher.transfer_bytes,
                seconds=self._monotonic() - started,
                frames_requested=frames_requested,
                frames_decoded=fetcher.frames_decoded,
                attempts=fetcher.attempts,
                sampling_status=guidance.provenance.get(
                    "toi_sampling_status", "unknown"
                ),
            )

        payload = {
            # --- hashed scientific content -------------------------------- #
            "hash_version": SCIENTIFIC_CONTENT_HASH_VERSION,
            "cache_key": key,
            "cache_inputs": {
                "event_id": case.event_id,
                "run_time": case.run_time.isoformat(),
                "forecast_hour": case.forecast_hour,
                "latitude": round(float(case.latitude), 4),
                "longitude": round(float(case.longitude), 4),
                "anchor_source": case.anchor_source,
                "sampling_interval_hours": int(sampling_interval_hours),
                "region_radius_km": round(float(region_radius_km), 3),
                "grid_stride": int(grid_stride),
                "method_version": HRRR_TOI_METHOD_VERSION,
                # Must mirror case_cache_key exactly: verify re-derives the key
                # from this block, so any key input missing here is a mismatch.
                "anchor_method_version": TOI_RISK_OBJECT_METHOD_VERSION,
            },
            "case": {
                "event_id": case.event_id,
                "case_class": case.case_class,
                "run_time": case.run_time.isoformat(),
                "forecast_hour": case.forecast_hour,
                "requested_latitude": case.latitude,
                "requested_longitude": case.longitude,
                "resolved_latitude": resolved.latitude,
                "resolved_longitude": resolved.longitude,
                "anchor_source": resolved.anchor_source,
                "observed": dict(case.observed),
                "sample_weight": case.sample_weight,
            },
            "anchor": anchor_provenance,
            "guidance": guidance.to_mapping(),
            "method_versions": {
                "runner": TOI_ARCHIVE_RUNNER_VERSION,
                "feature_method": HRRR_TOI_METHOD_VERSION,
                "anchor_selection": TOI_RISK_OBJECT_METHOD_VERSION,
                "hash": SCIENTIFIC_CONTENT_HASH_VERSION,
            },
            "sources": {
                "hrrr_bucket": HRRR_OPEN_DATA_BUCKET,
                "hrrr_license": HRRR_OPEN_DATA_LICENSE,
            },
            # --- volatile operational metadata, never hashed --------------- #
            "extracted_at": _now(),
            "retry_log": list(fetcher.retry_log),
            "transfer_bytes": fetcher.transfer_bytes,
            "frames_decoded": fetcher.frames_decoded,
        }
        science_hash = scientific_content_hash(payload)
        payload["scientific_content_sha256"] = science_hash
        path = _atomic_write_text(
            self.cases_dir / f"{key}.json",
            strict_json_dumps(payload, indent=2),
        )
        # The file hash is recorded outside the file, over its final bytes, so
        # it is verifiable without being self-referential.
        artifact_hash = file_sha256(path)
        return CaseOutcome(
            event_id=case.event_id,
            run_time=case.run_time.isoformat(),
            cache_key=key,
            status="success",
            reason="",
            transfer_bytes=fetcher.transfer_bytes,
            seconds=self._monotonic() - started,
            frames_requested=frames_requested,
            frames_decoded=fetcher.frames_decoded,
            attempts=fetcher.attempts,
            scientific_content_sha256=science_hash,
            artifact_sha256=artifact_hash,
            sampling_status=guidance.provenance.get("toi_sampling_status", ""),
            payload=payload,
        )

    # -- the run --------------------------------------------------------- #

    def run(
        self,
        cases: Iterable[TOICase],
        *,
        sampling_interval_hours: int = TOI_SAMPLING_INTERVAL_HOURS,
        region_radius_km: float = DEFAULT_REGION_RADIUS_KM,
        grid_stride: int = DEFAULT_GRID_STRIDE,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[str], None] | None = None,
        resume: bool = True,
    ) -> RunReport:
        """Process cases within budget, skipping anything already checkpointed."""

        with RunDirectoryLock(self.lock_path):
            return self._run_locked(
                cases,
                sampling_interval_hours=sampling_interval_hours,
                region_radius_km=region_radius_km,
                grid_stride=grid_stride,
                cancelled=cancelled,
                progress=progress,
                resume=resume,
            )

    def _run_locked(
        self,
        cases: Iterable[TOICase],
        *,
        sampling_interval_hours: int,
        region_radius_km: float,
        grid_stride: int,
        cancelled: Callable[[], bool] | None,
        progress: Callable[[str], None] | None,
        resume: bool,
    ) -> RunReport:
        report = RunReport(
            started_at=_now(),
            budget=self.budget.to_mapping(),
            sources=archive_source_record(),
        )
        already = self.completed_keys() if resume else {}
        run_started = self._monotonic()
        completed = 0
        for case in cases:
            key = case_cache_key(
                case,
                sampling_interval_hours=sampling_interval_hours,
                region_radius_km=region_radius_km,
                grid_stride=grid_stride,
            )
            if key in already:
                if progress is not None:
                    progress(f"resume-skip {case.event_id} ({already[key]})")
                continue
            if cancelled is not None and cancelled():
                report.stop_reason = "cancelled"
                break
            if completed >= self.budget.maximum_cases:
                report.stop_reason = "case_budget_reached"
                break
            if report.transfer_bytes >= self.budget.maximum_transfer_bytes:
                report.stop_reason = "transfer_budget_reached"
                break
            elapsed = self._monotonic() - run_started
            if elapsed >= self.budget.maximum_seconds:
                report.stop_reason = "time_budget_reached"
                break
            free = shutil.disk_usage(self.root).free
            if free <= self.budget.minimum_free_bytes:
                report.stop_reason = (
                    f"disk_headroom_exhausted ({free / 1024**3:.1f} GiB free, "
                    f"floor {self.budget.minimum_free_bytes / 1024**3:.1f} GiB)"
                )
                break

            if progress is not None:
                progress(
                    f"case {case.event_id} {case.run_time.isoformat()} "
                    f"F{case.forecast_hour:03d} [{key}]"
                )
            try:
                outcome = self._run_case(
                    case,
                    sampling_interval_hours=sampling_interval_hours,
                    region_radius_km=region_radius_km,
                    grid_stride=grid_stride,
                    cancelled=cancelled,
                )
            except DownloadCancelled:
                report.stop_reason = "cancelled"
                break
            report.outcomes.append(outcome)
            report.transfer_bytes += outcome.transfer_bytes
            self._append_checkpoint(outcome)
            completed += 1
            if progress is not None:
                progress(
                    f"  -> {outcome.status} "
                    f"{outcome.transfer_bytes / 1024**2:.1f} MiB "
                    f"{outcome.seconds:.1f}s "
                    + (outcome.reason[:80] if outcome.reason else "")
                )
        report.seconds = self._monotonic() - run_started
        _atomic_write_text(
            self.root / "run-report.json",
            strict_json_dumps(report.to_mapping(), indent=2),
        )
        return report

    # -- dataset assembly ------------------------------------------------ #

    def checkpoint_records(self) -> dict[str, dict[str, Any]]:
        """Return the last checkpoint record per cache key."""

        if not self.checkpoint_path.exists():
            return {}
        records: dict[str, dict[str, Any]] = {}
        with open(self.checkpoint_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = record.get("cache_key")
                if key:
                    records[str(key)] = record
        return records

    def verify(
        self,
        *,
        expected_target: str | None = None,
        expected_feature_method: str | None = None,
    ) -> dict[str, Any]:
        """Recompute every hash and invariant; never trust an embedded value.

        Checks performed per case file: strict JSON parse, required schema
        fields, finite numeric values, recomputed scientific content hash,
        recomputed artifact hash against the checkpoint, cache key recomputed
        from the recorded cache inputs, filename/cache-key agreement, and
        method/target/source consistency.  Across the run: duplicate cache keys,
        duplicate event/cycle pairs, orphan files with no checkpoint, missing
        files for checkpointed successes, and checkpoint/file status
        disagreement.
        """

        failures: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        checkpoint = self.checkpoint_records()
        seen_keys: dict[str, str] = {}
        seen_events: dict[tuple[str, str, int], str] = {}

        def fail(kind: str, path: Any, detail: str, **extra: Any) -> None:
            failures.append(
                {"check": kind, "path": str(path), "detail": detail, **extra}
            )

        files = sorted(self.cases_dir.glob("*.json"))
        for path in files:
            raw = None
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                fail("unreadable", path, f"{type(exc).__name__}: {exc}")
                continue
            if any(token in raw for token in ("NaN", "Infinity")):
                fail("non_strict_json", path, "contains NaN or Infinity")
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                fail("corrupt_json", path, f"truncated or invalid JSON: {exc}")
                continue
            if not isinstance(payload, dict):
                fail("schema", path, "case payload is not an object")
                continue

            required = (
                "hash_version",
                "cache_key",
                "cache_inputs",
                "case",
                "guidance",
                "method_versions",
                "sources",
                "scientific_content_sha256",
            )
            missing = [name for name in required if name not in payload]
            if missing:
                fail("schema", path, "missing field(s): " + ", ".join(missing))
                continue

            key = str(payload["cache_key"])
            if path.stem != key:
                fail(
                    "filename_mismatch",
                    path,
                    f"file name {path.stem!r} does not match cache_key {key!r}",
                )
            if key in seen_keys:
                fail(
                    "duplicate_cache_key",
                    path,
                    f"cache_key {key} already seen in {seen_keys[key]}",
                )
            seen_keys[key] = str(path)

            # Recompute the scientific hash rather than trusting the stored one.
            try:
                recomputed = scientific_content_hash(payload)
            except (TOIArchiveError, ValueError) as exc:
                fail("hash_uncomputable", path, str(exc))
                continue
            stored = str(payload["scientific_content_sha256"])
            if recomputed != stored:
                fail(
                    "scientific_hash_mismatch",
                    path,
                    f"stored {stored[:16]} != recomputed {recomputed[:16]}; the "
                    "scientific content was modified after extraction",
                )

            # Recompute the cache key from the recorded cache inputs.
            inputs = payload["cache_inputs"]
            if isinstance(inputs, dict):
                serialized = json.dumps(
                    inputs, sort_keys=True, separators=(",", ":")
                )
                expected_key = hashlib.sha256(
                    serialized.encode("utf-8")
                ).hexdigest()[:20]
                if expected_key != key:
                    fail(
                        "cache_key_mismatch",
                        path,
                        f"cache_key {key} does not derive from cache_inputs "
                        f"(expected {expected_key})",
                    )
            else:
                fail("schema", path, "cache_inputs is not an object")

            case = payload.get("case", {})
            event_id = str(case.get("event_id", ""))
            run_time = str(case.get("run_time", ""))
            try:
                forecast_hour = int(case.get("forecast_hour"))
            except (TypeError, ValueError):
                forecast_hour = -1
                fail("schema", path, "case.forecast_hour is not an integer")
            identity = (event_id, run_time, forecast_hour)
            if identity in seen_events:
                fail(
                    "duplicate_case",
                    path,
                    f"event/cycle {identity} already seen in {seen_events[identity]}",
                )
            seen_events[identity] = str(path)

            # Every numeric leaf must be finite.
            for pointer, value in _numeric_leaves(payload):
                if not math.isfinite(value):
                    fail("non_finite", path, f"{pointer} is not finite")

            methods = payload.get("method_versions", {})
            if expected_feature_method and methods.get("feature_method") != (
                expected_feature_method
            ):
                fail(
                    "method_mismatch",
                    path,
                    f"feature_method {methods.get('feature_method')!r} != "
                    f"expected {expected_feature_method!r}",
                )
            if payload.get("hash_version") != SCIENTIFIC_CONTENT_HASH_VERSION:
                fail(
                    "hash_version_mismatch",
                    path,
                    f"hash_version {payload.get('hash_version')!r} != "
                    f"{SCIENTIFIC_CONTENT_HASH_VERSION!r}",
                )
            sources = payload.get("sources", {})
            if not sources.get("hrrr_bucket"):
                fail("missing_source", path, "sources.hrrr_bucket is absent")
            guidance = payload.get("guidance", {})
            toi = guidance.get("toi", {}) if isinstance(guidance, dict) else {}
            if expected_target and toi.get("state") == "unavailable":
                fail("unusable_case", path, "stored guidance is unavailable")

            # Checkpoint agreement, including the artifact hash over file bytes.
            record = checkpoint.get(key)
            if record is None:
                fail("orphan_file", path, "no checkpoint record for this case")
            else:
                if record.get("status") != "success":
                    fail(
                        "status_disagreement",
                        path,
                        f"checkpoint status {record.get('status')!r} but a case "
                        "file exists",
                    )
                if record.get("scientific_content_sha256") not in (
                    "",
                    None,
                    recomputed,
                ):
                    fail(
                        "checkpoint_hash_mismatch",
                        path,
                        "checkpoint scientific hash disagrees with the file",
                    )
                expected_artifact = record.get("artifact_sha256")
                if expected_artifact:
                    actual_artifact = file_sha256(path)
                    if actual_artifact != expected_artifact:
                        fail(
                            "artifact_hash_mismatch",
                            path,
                            f"file bytes hash {actual_artifact[:16]} != "
                            f"checkpointed {str(expected_artifact)[:16]}",
                        )

            records.append(
                {
                    "cache_key": key,
                    "scientific_content_sha256": recomputed,
                    "artifact_sha256": file_sha256(path),
                    "event_id": event_id,
                    "run_time": run_time,
                    "forecast_hour": forecast_hour,
                    "case_class": case.get("case_class"),
                    "anchor_source": case.get("anchor_source"),
                    "resolved_latitude": case.get("resolved_latitude"),
                    "resolved_longitude": case.get("resolved_longitude"),
                    "sampling_status": guidance.get("provenance", {}).get(
                        "toi_sampling_status"
                    )
                    if isinstance(guidance, dict)
                    else None,
                }
            )

        # Checkpointed successes must have a file on disk.
        for key, record in sorted(checkpoint.items()):
            if record.get("status") != "success":
                continue
            if not (self.cases_dir / f"{key}.json").exists():
                fail(
                    "missing_file",
                    self.cases_dir / f"{key}.json",
                    f"checkpoint records success for {key} but no case file exists",
                    event_id=record.get("event_id"),
                )

        statuses: dict[str, int] = {}
        for record in checkpoint.values():
            status = str(record.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
        skips = [
            {
                "event_id": record.get("event_id"),
                "run_time": record.get("run_time"),
                "reason": record.get("reason"),
            }
            for record in checkpoint.values()
            if record.get("status") == "skipped"
        ]
        return {
            "runner_version": TOI_ARCHIVE_RUNNER_VERSION,
            "hash_version": SCIENTIFIC_CONTENT_HASH_VERSION,
            "generated_at": _now(),
            "verified": not failures,
            "case_files": len(files),
            "verified_cases": len(records),
            "checkpoint_entries": len(checkpoint),
            "checkpoint_statuses": statuses,
            "skipped": skips,
            "failure_count": len(failures),
            "failures": failures,
            "sources": archive_source_record(),
            "cases": records,
            "experimental_not_official": True,
        }

    def collect_manifest(self) -> dict[str, Any]:
        """Verify the run and return the manifest, raising on any failure."""

        report = self.verify()
        if not report["verified"]:
            first = report["failures"][0]
            raise TOIDatasetError(
                f"archive verification failed with {report['failure_count']} "
                f"problem(s); first: {first['check']} at {first['path']}: "
                f"{first['detail']}"
            )
        return report


__all__ = [
    "CONUS_ANCHOR_CENTRE",
    "CONUS_ANCHOR_RADIUS_KM",
    "HRRR_F18_AVAILABLE_FROM",
    "HRRR_LEGACY_MAXIMUM_FORECAST_HOUR",
    "maximum_available_forecast_hour",
    "HRRR_ARCHIVE_FIRST_YEAR",
    "HRRR_ARCHIVE_LAST_YEAR",
    "HRRR_OPEN_DATA_BASE_URL",
    "HRRR_OPEN_DATA_BUCKET",
    "HRRR_OPEN_DATA_LICENSE",
    "MEASURED_MESSAGES_PER_FRAME",
    "MEASURED_SUBSET_MIB_BY_ERA",
    "NCEI_STORM_EVENTS_BASE_URL",
    "NCEI_STORM_EVENTS_LICENSE",
    "PLANNING_SUBSET_MIB",
    "TOI_ARCHIVE_RUNNER_VERSION",
    "ArchiveRunner",
    "CaseEstimate",
    "CaseOutcome",
    "FrameNotPublished",
    "ResilientFrameFetcher",
    "RunBudget",
    "RunDirectoryLock",
    "RunReport",
    "TOIArchiveError",
    "archive_source_record",
    "audit_local_resources",
    "case_cache_key",
    "default_case_estimate",
    "resolve_forecast_anchor",
]
