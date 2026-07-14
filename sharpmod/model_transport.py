"""Resumable adaptive HTTP byte-range transport for GRIB subsets."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Iterable


class OptimizedTransportUnavailable(RuntimeError):
    """The optimized path is incompatible; callers should use a fallback."""


class DownloadCancelled(RuntimeError):
    """A cooperative model download cancellation was requested."""


@dataclass(frozen=True, order=True)
class ByteRange:
    """One inclusive HTTP byte range."""

    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


def _coerce_range(row) -> ByteRange:
    try:
        start, end = row
        if end is None or (isinstance(end, float) and math.isnan(end)):
            raise OptimizedTransportUnavailable(
                "inventory has no ending byte for a selected GRIB message"
            )
        item = ByteRange(int(start), int(end))
    except OptimizedTransportUnavailable:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise OptimizedTransportUnavailable(
            "inventory contains an invalid byte range"
        ) from exc
    if item.start < 0 or item.end < item.start:
        raise OptimizedTransportUnavailable(
            "inventory contains an invalid byte range"
        )
    return item


def _collapse_contiguous(rows: Iterable[tuple[int, int]]) -> list[ByteRange]:
    # Some wgrib2 inventories expose one physical vector message twice (for
    # example UGRD and VGRD) with the same start offset. Herbie calculates the
    # first duplicate's end as ``start - 1`` and the second duplicate carries
    # the real message end. Consolidate equal starts before validating so the
    # shared GRIB message remains downloadable without accepting a genuinely
    # malformed singleton range.
    grouped: dict[int, int] = {}
    for row in rows:
        try:
            start, end = row
            if end is None or (isinstance(end, float) and math.isnan(end)):
                raise OptimizedTransportUnavailable(
                    "inventory has no ending byte for a selected GRIB message"
                )
            start = int(start)
            end = int(end)
        except OptimizedTransportUnavailable:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise OptimizedTransportUnavailable(
                "inventory contains an invalid byte range"
            ) from exc
        if start < 0:
            raise OptimizedTransportUnavailable(
                "inventory contains an invalid byte range"
            )
        grouped[start] = max(grouped.get(start, end), end)

    exact: list[ByteRange] = []
    for item in sorted(_coerce_range(row) for row in grouped.items()):
        if exact and item.start <= exact[-1].end + 1:
            exact[-1] = ByteRange(exact[-1].start, max(exact[-1].end, item.end))
        else:
            exact.append(item)
    if not exact:
        raise OptimizedTransportUnavailable(
            "inventory returned no byte ranges for the selected fields"
        )
    return exact


def plan_ranges(
    rows: Iterable[tuple[int, int]],
    *,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> list[ByteRange]:
    """Merge nearby message spans without exceeding a global byte budget."""
    exact = _collapse_contiguous(rows)
    exact_bytes = sum(item.size for item in exact)
    overhead_limit = max(0, int(exact_bytes * float(max_overhead_ratio)))
    overhead = 0
    planned: list[ByteRange] = []
    for item in exact:
        if not planned:
            planned.append(item)
            continue
        gap = item.start - planned[-1].end - 1
        if gap <= int(max_gap) and overhead + gap <= overhead_limit:
            overhead += gap
            planned[-1] = ByteRange(planned[-1].start, item.end)
        else:
            planned.append(item)
    return planned


def ranges_from_inventory(
    inventory,
    *,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> list[ByteRange]:
    """Create an adaptive range plan from a Herbie inventory DataFrame."""
    try:
        rows = zip(inventory["start_byte"], inventory["end_byte"])
    except (KeyError, TypeError) as exc:
        raise OptimizedTransportUnavailable(
            "inventory does not expose HTTP byte ranges"
        ) from exc
    return plan_ranges(
        rows, max_gap=max_gap, max_overhead_ratio=max_overhead_ratio
    )


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


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_manifest(path: Path, payload: dict) -> None:
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


def _parse_content_range(value: str) -> tuple[int, int] | None:
    try:
        unit, remainder = value.split(" ", 1)
        span, _total = remainder.split("/", 1)
        start, end = span.split("-", 1)
        if unit.lower() != "bytes":
            return None
        return int(start), int(end)
    except (AttributeError, TypeError, ValueError):
        return None


def download_ranges(
    session,
    url: str,
    ranges: Iterable[ByteRange],
    output_path,
    *,
    timeout=(10, 90),
    chunk_size: int = 256 * 1024,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download, resume, validate, and atomically assemble GRIB ranges."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and _valid_grib(output):
        return output

    plan = list(ranges)
    if not plan:
        raise OptimizedTransportUnavailable("byte-range plan is empty")
    total = sum(item.size for item in plan)
    fragments = output.parent / f".{output.name}.ranges"
    manifest_path = fragments / "manifest.json"
    fragments.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(manifest_path)
    signature = [[item.start, item.end] for item in plan]
    if manifest and (
        manifest.get("url") != str(url) or manifest.get("ranges") != signature
    ):
        shutil.rmtree(fragments, ignore_errors=True)
        fragments.mkdir(parents=True, exist_ok=True)
        manifest = {}
    manifest.update({"url": str(url), "ranges": signature})
    _write_manifest(manifest_path, manifest)

    completed = 0
    for item in plan:
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        fragment = fragments / f"{item.start}-{item.end}.part"
        existing = fragment.stat().st_size if fragment.exists() else 0
        if existing > item.size:
            fragment.unlink()
            existing = 0
        if existing == item.size:
            completed += existing
            if progress is not None:
                progress(completed, total)
            continue

        request_start = item.start + existing
        headers = {"Range": f"bytes={request_start}-{item.end}"}
        if manifest.get("etag"):
            headers["If-Range"] = str(manifest["etag"])
        try:
            response_context = session.get(
                str(url), headers=headers, stream=True, timeout=timeout
            )
            with response_context as response:
                if int(getattr(response, "status_code", 0)) != 206:
                    raise OptimizedTransportUnavailable(
                        "source did not honor the HTTP range request"
                    )
                content_range = _parse_content_range(
                    getattr(response, "headers", {}).get("Content-Range", "")
                )
                if content_range != (request_start, item.end):
                    raise OptimizedTransportUnavailable(
                        "source returned a mismatched HTTP range"
                    )
                etag = getattr(response, "headers", {}).get("ETag")
                if existing and manifest.get("etag") and etag \
                        and etag != manifest["etag"]:
                    fragment.unlink(missing_ok=True)
                    raise OptimizedTransportUnavailable(
                        "source changed while a range was being resumed"
                    )
                if etag and etag != manifest.get("etag"):
                    manifest["etag"] = etag
                    _write_manifest(manifest_path, manifest)
                mode = "ab" if existing else "wb"
                with fragment.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        existing += len(chunk)
                        if existing > item.size:
                            raise OptimizedTransportUnavailable(
                                "source returned more bytes than requested"
                            )
                        if progress is not None:
                            progress(completed + existing, total)
                        if cancelled is not None and cancelled():
                            raise DownloadCancelled(
                                "forecast-model download cancelled"
                            )
        except (DownloadCancelled, OptimizedTransportUnavailable):
            raise
        except Exception as exc:
            raise OptimizedTransportUnavailable(
                "optimized HTTP range transfer failed: %s" % exc
            ) from exc
        if existing != item.size:
            raise OptimizedTransportUnavailable(
                "source returned an incomplete HTTP range"
            )
        completed += existing

    fd, temporary = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(fd, "wb") as destination:
            for item in plan:
                fragment = fragments / f"{item.start}-{item.end}.part"
                with fragment.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        temporary_path = Path(temporary)
        if not _valid_grib(temporary_path):
            raise OptimizedTransportUnavailable(
                "assembled byte ranges are not a valid GRIB stream"
            )
        os.replace(temporary_path, output)
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
    shutil.rmtree(fragments, ignore_errors=True)
    return output


def download_herbie_subset(
    herbie,
    search: str,
    *,
    save_dir=None,
    session=None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> tuple[Path, int]:
    """Download a Herbie inventory subset through the optimized transport."""
    source = str(getattr(herbie, "grib", "") or "")
    if not source.startswith(("http://", "https://")):
        raise OptimizedTransportUnavailable(
            "Herbie source is not an HTTP byte-range endpoint"
        )
    if save_dir is not None:
        herbie.save_dir = Path(save_dir).expanduser()
    try:
        inventory = herbie.inventory(search).copy()
        output = Path(herbie.get_localFilePath(search))
    except Exception as exc:
        raise OptimizedTransportUnavailable(
            "Herbie could not provide a subset inventory: %s" % exc
        ) from exc
    ranges = ranges_from_inventory(
        inventory,
        max_gap=max_gap,
        max_overhead_ratio=max_overhead_ratio,
    )
    total = sum(item.size for item in ranges)
    owned_session = session is None
    if owned_session:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - required dependency
            raise OptimizedTransportUnavailable(
                "requests is unavailable for optimized model downloads"
            ) from exc
        session = requests.Session()
    try:
        result = download_ranges(
            session,
            source,
            ranges,
            output,
            cancelled=cancelled,
            progress=progress,
        )
        return result, total
    finally:
        if owned_session:
            close = getattr(session, "close", None)
            if callable(close):
                close()
