"""Qt-independent, resumable forecast-model batch extraction.

Requests are grouped by model/run/forecast-hour/member.  Each group holds one
``ModelHourCache`` lease and extracts every requested point from that decoded
dataset, so a multi-point job does not download the same model hour repeatedly.
Outputs use ``model_extract``'s atomic NPZ + JSON writer and the versioned job
manifest is atomically replaced after every state transition.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Callable, Iterable, Mapping, Sequence

from sharpmod.backends.grib import (
    DecodedPoint,
    decode_grib_points,
    decode_grib_wind_vorticities,
)
from sharpmod.model_hour_cache import ModelHourCache, ModelHourKey
from sharpmod.model_transport import DownloadCancelled
from sharpmod.tools import model_extract
from sharpmod.tools.era5_extract import _atomic_write_json


BATCH_SPEC_VERSION = 1
MANIFEST_SCHEMA = "sharpmod.batch-manifest"
MANIFEST_VERSION = 1
MAX_CONCURRENCY = 4


class BatchExtractError(Exception):
    """Base class for invalid jobs and batch execution failures."""


class BatchSpecError(BatchExtractError):
    """The input job document or one request is invalid."""


class BatchManifestError(BatchExtractError):
    """An existing manifest cannot safely resume this job."""


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BatchSpecError(f"invalid model run time {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_identifier(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    ).strip("_-")
    return safe or "request"


@dataclass(frozen=True)
class BatchRequest:
    """One point request in a model-hour batch job."""

    id: str
    model: str
    lat: float
    lon: float
    run_time: datetime
    fxx: int = 0
    output: str | None = None
    loc: str | None = None
    member: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "BatchRequest":
        if not isinstance(value, Mapping):
            raise BatchSpecError("each batch request must be an object")
        missing = [
            name for name in ("id", "model", "lat", "lon", "run")
            if name not in value
        ]
        if missing:
            raise BatchSpecError(
                "batch request is missing required fields: " + ", ".join(missing)
            )
        request_id = str(value["id"]).strip()
        if not request_id:
            raise BatchSpecError("batch request id cannot be empty")
        try:
            lat = float(value["lat"])
            lon = float(value["lon"])
            fxx = int(value.get("fxx", 0))
        except (TypeError, ValueError) as exc:
            raise BatchSpecError(
                f"request {request_id!r} has invalid numeric fields: {exc}"
            ) from exc
        output = value.get("output")
        loc = value.get("loc")
        member = value.get("member")
        return cls(
            id=request_id,
            model=str(value["model"]).strip(),
            lat=lat,
            lon=lon,
            run_time=_parse_datetime(value["run"]),
            fxx=fxx,
            output=str(output).strip() if output is not None else None,
            loc=str(loc) if loc is not None else None,
            member=str(member).strip() if member is not None else None,
        )


@dataclass(frozen=True)
class BatchItemResult:
    """Ordered final state for one request."""

    id: str
    status: str
    output_path: Path
    sidecar_path: Path
    resumed: bool
    error: Mapping[str, str] | None = None


@dataclass(frozen=True)
class BatchRunResult:
    """Final manifest snapshot and status counts from a batch run."""

    manifest_path: Path
    job_id: str
    completed: int
    failed: int
    cancelled: int
    skipped: int
    items: tuple[BatchItemResult, ...]
    manifest: Mapping[str, object]

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.cancelled == 0

    @property
    def output_paths(self) -> tuple[Path, ...]:
        """Completed NPZ paths in the same order as the input requests."""
        return tuple(
            item.output_path for item in self.items if item.status == "completed"
        )


@dataclass(frozen=True)
class _PreparedRequest:
    request: BatchRequest
    config: object
    run_dt: datetime
    output_path: Path
    sidecar_path: Path
    fingerprint: str

    @property
    def hour_key(self) -> ModelHourKey:
        spatial = None
        if model_extract.point_only_provider(self.config):
            spatial = model_extract.spatial_cache_key(
                self.config, self.request.lat, self.request.lon
            )
        return ModelHourKey.create(
            self.config.key,
            self.run_dt,
            self.request.fxx,
            self.request.member,
            spatial=spatial,
        )

    def manifest_request(self, root: Path) -> dict[str, object]:
        return {
            "id": self.request.id,
            "model": self.config.key,
            "lat": self.request.lat,
            "lon": self.request.lon,
            "run": _canonical_time(self.run_dt),
            "fxx": self.request.fxx,
            "member": self.request.member,
            "loc": self.request.loc,
            "output": self.output_path.relative_to(root).as_posix(),
        }


def load_batch_spec(path) -> list[BatchRequest]:
    """Load a version-1 JSON batch specification."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchSpecError(f"could not read batch spec {path}: {exc}") from exc
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        version = payload.get("version", BATCH_SPEC_VERSION)
        if version != BATCH_SPEC_VERSION:
            raise BatchSpecError(
                f"unsupported batch spec version {version!r}; "
                f"expected {BATCH_SPEC_VERSION}"
            )
        rows = payload.get("requests")
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        raise BatchSpecError("batch spec must contain a non-empty requests list")
    return [BatchRequest.from_mapping(row) for row in rows]


def _fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise BatchSpecError("batch output paths must be relative to output_dir")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BatchSpecError(
            f"batch output path escapes output_dir: {relative!r}"
        ) from exc
    if resolved.suffix.lower() != ".npz":
        raise BatchSpecError(
            f"batch output path must end in .npz: {relative!r}"
        )
    return resolved


class BatchExtractor:
    """Execute bounded, cancellable, resumable model point jobs."""

    def __init__(
        self,
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ):
        self._progress_callback = progress_callback
        self._cancel_event = threading.Event()
        self._run_lock = threading.Lock()
        self._manifest_lock = threading.RLock()
        self._manifest: dict[str, object] | None = None
        self._manifest_path: Path | None = None
        self._entries: dict[str, dict[str, object]] = {}

    def cancel(self) -> None:
        """Request cooperative cancellation of downloads and pending points."""
        self._cancel_event.set()

    def _cancelled(self, external: Callable[[], bool] | None) -> bool:
        return self._cancel_event.is_set() or (
            bool(external()) if external is not None else False
        )

    def _emit(self, event: str, **details) -> None:
        if self._progress_callback is not None:
            payload = {"event": event}
            payload.update(details)
            self._progress_callback(payload)

    def _write_manifest_locked(self) -> None:
        if self._manifest is None or self._manifest_path is None:
            raise RuntimeError("batch manifest is not initialized")
        _atomic_write_json(self._manifest_path, self._manifest)

    def _update_entry(self, request_id: str, **values) -> None:
        with self._manifest_lock:
            entry = self._entries[request_id]
            entry.update(values)
            self._write_manifest_locked()

    @staticmethod
    def _is_resumable(entry: Mapping[str, object], prepared: _PreparedRequest) -> bool:
        if entry.get("status") != "completed":
            return False
        if entry.get("fingerprint") != prepared.fingerprint:
            return False
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, Mapping):
            return False
        expected = (
            (prepared.output_path, artifacts.get("npz_sha256")),
            (prepared.sidecar_path, artifacts.get("json_sha256")),
        )
        for path, digest in expected:
            if not path.is_file() or not isinstance(digest, str):
                return False
            try:
                if _sha256(path) != digest:
                    return False
            except OSError:
                return False
        return True

    @staticmethod
    def _prepare_requests(
        requests: Iterable[BatchRequest], output_root: Path
    ) -> list[_PreparedRequest]:
        prepared = []
        ids: set[str] = set()
        outputs: set[Path] = set()
        for request in requests:
            if not isinstance(request, BatchRequest):
                raise BatchSpecError("requests must contain BatchRequest values")
            if request.id in ids:
                raise BatchSpecError(f"duplicate batch request id {request.id!r}")
            ids.add(request.id)
            try:
                config = model_extract.get_config(request.model)
            except (KeyError, model_extract.RetrievalError) as exc:
                raise BatchSpecError(
                    f"request {request.id!r} has unknown model {request.model!r}: "
                    f"{exc}"
                ) from exc
            run_dt = model_extract._run_datetime(request.run_time, config)
            output_name = request.output or f"{_safe_identifier(request.id)}.npz"
            output_path = _contained_path(output_root, output_name)
            if output_path in outputs:
                raise BatchSpecError(
                    f"multiple requests target output {output_name!r}"
                )
            outputs.add(output_path)
            sidecar_path = output_path.with_suffix(".json")
            canonical = {
                "id": request.id,
                "model": config.key,
                "lat": request.lat,
                "lon": request.lon,
                "run": _canonical_time(run_dt),
                "fxx": request.fxx,
                "member": request.member,
                "loc": request.loc,
                "output": output_path.relative_to(output_root).as_posix(),
            }
            prepared.append(_PreparedRequest(
                request=request,
                config=config,
                run_dt=run_dt,
                output_path=output_path,
                sidecar_path=sidecar_path,
                fingerprint=_fingerprint(canonical),
            ))
        if not prepared:
            raise BatchSpecError("a batch job needs at least one request")
        return prepared

    def _initialize_manifest(
        self,
        prepared: Sequence[_PreparedRequest],
        output_root: Path,
        manifest_path: Path,
        *,
        resume: bool,
    ) -> tuple[str, int]:
        request_records = [item.manifest_request(output_root) for item in prepared]
        job_id = _fingerprint({
            "version": BATCH_SPEC_VERSION,
            "requests": sorted(request_records, key=lambda item: str(item["id"])),
        })
        previous = None
        if resume and manifest_path.is_file():
            try:
                with manifest_path.open("r", encoding="utf-8") as handle:
                    previous = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise BatchManifestError(
                    f"could not read existing manifest {manifest_path}: {exc}"
                ) from exc
            if (
                previous.get("schema") != MANIFEST_SCHEMA
                or previous.get("version") != MANIFEST_VERSION
            ):
                raise BatchManifestError(
                    f"manifest {manifest_path} has an unsupported schema/version"
                )
            if previous.get("job_id") != job_id:
                raise BatchManifestError(
                    f"manifest {manifest_path} belongs to a different batch job"
                )

        previous_by_id = {}
        if isinstance(previous, dict):
            for entry in previous.get("requests", ()):
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    previous_by_id[entry["id"]] = entry

        entries = []
        skipped = 0
        for item, request_record in zip(prepared, request_records):
            old = previous_by_id.get(item.request.id, {})
            if resume and self._is_resumable(old, item):
                entry = copy.deepcopy(old)
                entry["resumed"] = True
                skipped += 1
            else:
                entry = {
                    **request_record,
                    "fingerprint": item.fingerprint,
                    "status": "pending",
                    "resumed": False,
                    "model_hour_cache_hit": None,
                    "model_hour_reused": None,
                    "error": None,
                    "artifacts": None,
                }
            entries.append(entry)

        self._manifest = {
            "schema": MANIFEST_SCHEMA,
            "version": MANIFEST_VERSION,
            "job_id": job_id,
            "requests": entries,
            "summary": {},
        }
        self._manifest_path = manifest_path
        self._entries = {str(entry["id"]): entry for entry in entries}
        with self._manifest_lock:
            self._write_manifest_locked()
        return job_id, skipped

    def _mark_group_failure(
        self,
        group: Sequence[_PreparedRequest],
        status: str,
        exc: BaseException | None = None,
    ) -> None:
        error = None
        if exc is not None:
            error = {"type": type(exc).__name__, "message": str(exc)}
        for item in group:
            if self._entries[item.request.id].get("status") == "completed":
                continue
            self._update_entry(item.request.id, status=status, error=error)
            self._emit(status, request_id=item.request.id, error=error)

    def _run_group(
        self,
        group: Sequence[_PreparedRequest],
        cache: ModelHourCache,
        external_cancelled: Callable[[], bool] | None,
    ) -> None:
        pending = [
            item for item in group
            if self._entries[item.request.id].get("status") != "completed"
        ]
        if not pending:
            return
        if self._cancelled(external_cancelled):
            self._mark_group_failure(pending, "cancelled")
            return
        first = pending[0]
        same_point = all(
            item.request.lat == first.request.lat
            and item.request.lon == first.request.lon
            for item in pending
        )
        spatial = (
            model_extract.spatial_cache_key(
                first.config, first.request.lat, first.request.lon
            )
            if same_point else None
        )
        key = ModelHourKey.create(
            first.config.key,
            first.run_dt,
            first.request.fxx,
            first.request.member,
            spatial=spatial,
        )

        def loader(download_dir):
            def retrieval_progress(stage, total=0):
                self._emit(
                    "model_hour_progress",
                    model=key.model,
                    run=_canonical_time(key.run_time),
                    fxx=key.fxx,
                    member=key.member,
                    stage=stage,
                    total_bytes=int(total or 0),
                )

            retrieve_kwargs = {}
            if spatial is not None:
                retrieve_kwargs.update({
                    "lat": first.request.lat,
                    "lon": first.request.lon,
                })
            return model_extract._retrieve_dataset(
                first.config,
                first.run_dt,
                first.request.fxx,
                member=first.request.member,
                download_dir=download_dir,
                progress_callback=retrieval_progress,
                cancelled=lambda: self._cancelled(external_cancelled),
                # A one-point group keeps the normal point/subregion route and
                # a spatial cache key compatible with the GUI. Multi-point
                # groups intentionally retrieve one full field subset that is
                # reusable by every point in the group.
                **retrieve_kwargs,
            )

        try:
            with cache.lease(key, loader) as (entry, cache_hit):
                bulk_points = {}
                if (
                    len(pending) > 1
                    and isinstance(
                        entry.dataset, model_extract._LocalGribDataset
                    )
                ):
                    try:
                        decoded_points = decode_grib_points(
                            entry.dataset.path,
                            [
                                (item.request.lat, item.request.lon)
                                for item in pending
                            ],
                        )
                        missing_vorticity = [
                            index for index, decoded
                            in enumerate(decoded_points)
                            if decoded.surface_relative_vorticity is None
                        ]
                        targeted_vorticity = {}
                        if missing_vorticity:
                            if model_extract._direct_grib_required():
                                raise model_extract.RetrievalError(
                                    "the direct decoder found no usable "
                                    "vorticity value"
                                )
                            try:
                                values = decode_grib_wind_vorticities(
                                    entry.dataset.path,
                                    [
                                        (
                                            pending[index].request.lat,
                                            pending[index].request.lon,
                                        )
                                        for index in missing_vorticity
                                    ],
                                )
                            except Exception:
                                values = tuple(
                                    entry.dataset.surface_wind_vorticity(
                                        pending[index].request.lat,
                                        pending[index].request.lon,
                                        pending[index].run_dt,
                                    )
                                    for index in missing_vorticity
                                )
                            targeted_vorticity.update(zip(
                                missing_vorticity, values
                            ))
                        for point_index, (item, decoded) in enumerate(zip(
                                pending, decoded_points)):
                            vorticity_source = (
                                "direct pressure-level vorticity field"
                            )
                            if decoded.surface_relative_vorticity is None:
                                decoded = DecodedPoint(
                                    decoded.matrix,
                                    decoded.selected_lat,
                                    decoded.selected_lon,
                                    targeted_vorticity[point_index],
                                )
                                vorticity_source = (
                                    "targeted horizontal wind-gradient fallback"
                                )
                            bulk_points[item.request.id] = (
                                model_extract.DecodedModelPointDataset(
                                    decoded=decoded,
                                    valid_time=(
                                        item.run_dt
                                        + timedelta(hours=item.request.fxx)
                                    ),
                                    vorticity_source=vorticity_source,
                                )
                            )
                        self._emit(
                            "bulk_decode_complete",
                            model=key.model,
                            points=len(bulk_points),
                        )
                    except Exception as exc:
                        bulk_points = {}
                        self._emit(
                            "bulk_decode_fallback",
                            model=key.model,
                            error={
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                        )
                for index, item in enumerate(pending):
                    if self._cancelled(external_cancelled):
                        self._mark_group_failure(pending[index:], "cancelled")
                        break
                    self._update_entry(
                        item.request.id,
                        status="running",
                        error=None,
                        model_hour_cache_hit=bool(cache_hit),
                        model_hour_reused=bool(cache_hit or index > 0),
                    )
                    self._emit("running", request_id=item.request.id)

                    def point_progress(stage, total=0, request_id=item.request.id):
                        self._emit(
                            "point_progress",
                            request_id=request_id,
                            stage=stage,
                            total_bytes=int(total or 0),
                        )

                    try:
                        model_extract.extract(
                            item.config.key,
                            item.request.lat,
                            item.request.lon,
                            run_time=item.run_dt,
                            fxx=item.request.fxx,
                            out_path=str(item.output_path),
                            loc=item.request.loc,
                            member=item.request.member,
                            dataset=bulk_points.get(
                                item.request.id, entry.dataset
                            ),
                            source_grib=entry.source_grib,
                            source_fields=entry.source_fields,
                            source_transport=entry.source_transport,
                            progress_callback=point_progress,
                            cancelled=lambda: self._cancelled(external_cancelled),
                        )
                        with item.sidecar_path.open(
                            "r", encoding="utf-8"
                        ) as sidecar_file:
                            sidecar = json.load(sidecar_file)
                        if not isinstance(sidecar, dict):
                            raise ValueError(
                                "model extractor sidecar must be a JSON object"
                            )
                        sidecar["cache_hit"] = bool(cache_hit or index > 0)
                        sidecar["model_hour_reused"] = bool(
                            cache_hit or index > 0
                        )
                        _atomic_write_json(item.sidecar_path, sidecar)
                        artifacts = {
                            "npz_sha256": _sha256(item.output_path),
                            "npz_bytes": item.output_path.stat().st_size,
                            "json_sha256": _sha256(item.sidecar_path),
                            "json_bytes": item.sidecar_path.stat().st_size,
                        }
                    except DownloadCancelled:
                        self._update_entry(
                            item.request.id, status="cancelled", error=None
                        )
                        self._emit("cancelled", request_id=item.request.id)
                        self._mark_group_failure(pending[index + 1:], "cancelled")
                        break
                    except Exception as exc:
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        self._update_entry(
                            item.request.id, status="failed", error=error
                        )
                        self._emit(
                            "failed", request_id=item.request.id, error=error
                        )
                        continue
                    self._update_entry(
                        item.request.id,
                        status="completed",
                        error=None,
                        artifacts=artifacts,
                    )
                    self._emit("completed", request_id=item.request.id)
        except DownloadCancelled as exc:
            self._mark_group_failure(pending, "cancelled", exc)
        except Exception as exc:
            self._mark_group_failure(pending, "failed", exc)

    def run(
        self,
        requests: Iterable[BatchRequest],
        *,
        output_dir,
        manifest_path=None,
        max_workers: int = 2,
        resume: bool = True,
        cancelled: Callable[[], bool] | None = None,
        model_hour_cache: ModelHourCache | None = None,
    ) -> BatchRunResult:
        """Run a job and return its final manifest snapshot.

        ``max_workers`` bounds concurrently loaded model hours and is capped at
        four because each model-hour subset can be hundreds of megabytes.
        """
        try:
            workers = int(max_workers)
        except (TypeError, ValueError) as exc:
            raise BatchSpecError("max_workers must be an integer") from exc
        if not 1 <= workers <= MAX_CONCURRENCY:
            raise BatchSpecError(
                f"max_workers must be between 1 and {MAX_CONCURRENCY}"
            )
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("this BatchExtractor is already running")
        self._cancel_event.clear()
        if model_hour_cache is not None and not isinstance(
            model_hour_cache, ModelHourCache
        ):
            self._run_lock.release()
            raise TypeError("model_hour_cache must be a ModelHourCache")
        cache = model_hour_cache
        owns_cache = cache is None
        try:
            output_root = Path(output_dir).expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            prepared = self._prepare_requests(list(requests), output_root)
            manifest = (
                Path(manifest_path).expanduser().resolve()
                if manifest_path is not None
                else output_root / "batch-manifest.json"
            )
            manifest.parent.mkdir(parents=True, exist_ok=True)
            job_id, skipped = self._initialize_manifest(
                prepared, output_root, manifest, resume=bool(resume)
            )
            groups: dict[ModelHourKey, list[_PreparedRequest]] = {}
            for item in prepared:
                groups.setdefault(item.hour_key, []).append(item)
            if cache is None:
                cache = ModelHourCache(max_entries=workers)
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="sharpmod-batch"
            ) as executor:
                futures = [
                    executor.submit(
                        self._run_group, group, cache, cancelled
                    )
                    for group in groups.values()
                ]
                try:
                    for future in as_completed(futures):
                        # _run_group records ordinary failures per request.
                        # Keep programmer/system errors visible rather than
                        # losing them inside the executor.
                        future.result()
                except BaseException:
                    self.cancel()
                    for future in futures:
                        future.cancel()
                    raise

            statuses = [str(entry.get("status")) for entry in self._entries.values()]
            completed = statuses.count("completed")
            failed = statuses.count("failed")
            cancelled_count = statuses.count("cancelled")
            summary = {
                "total": len(statuses),
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled_count,
                "skipped": skipped,
            }
            with self._manifest_lock:
                self._manifest["summary"] = summary
                self._write_manifest_locked()
                snapshot = copy.deepcopy(self._manifest)
            item_results = tuple(
                BatchItemResult(
                    id=item.request.id,
                    status=str(self._entries[item.request.id].get("status")),
                    output_path=item.output_path,
                    sidecar_path=item.sidecar_path,
                    resumed=bool(
                        self._entries[item.request.id].get("resumed", False)
                    ),
                    error=(
                        dict(self._entries[item.request.id]["error"])
                        if isinstance(
                            self._entries[item.request.id].get("error"), Mapping
                        )
                        else None
                    ),
                )
                for item in prepared
            )
            return BatchRunResult(
                manifest_path=manifest,
                job_id=job_id,
                completed=completed,
                failed=failed,
                cancelled=cancelled_count,
                skipped=skipped,
                items=item_results,
                manifest=snapshot,
            )
        finally:
            if owns_cache and cache is not None:
                cache.clear()
            self._run_lock.release()


def run_batch(
    requests: Iterable[BatchRequest],
    *,
    output_dir,
    manifest_path=None,
    max_workers: int = 2,
    resume: bool = True,
    cancelled: Callable[[], bool] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    model_hour_cache: ModelHourCache | None = None,
) -> BatchRunResult:
    """Run heterogeneous model/hour/point requests with one reusable call."""
    return BatchExtractor(progress_callback=progress_callback).run(
        requests,
        output_dir=output_dir,
        manifest_path=manifest_path,
        max_workers=max_workers,
        resume=resume,
        cancelled=cancelled,
        model_hour_cache=model_hour_cache,
    )


__all__ = [
    "BATCH_SPEC_VERSION",
    "MANIFEST_SCHEMA",
    "MANIFEST_VERSION",
    "MAX_CONCURRENCY",
    "BatchExtractError",
    "BatchExtractor",
    "BatchItemResult",
    "BatchManifestError",
    "BatchRequest",
    "BatchRunResult",
    "BatchSpecError",
    "load_batch_spec",
    "run_batch",
]
