"""Project-owned NOAA RRFS retrieval over NOMADS static GRIB2 files.

Herbie 2026.3.0 still points its ``rrfs`` template at ``noaa-rrfs-pds/rrfs_a/``
on AWS. That prefix no longer carries operational output -- the bucket keeps
only retrospective, DESI, and sample collections -- so ``Herbie(...).grib``
resolves to ``None`` for every RRFS run. Its product list also predates the
current file layout, which splits each cycle into a pressure-level ``prslev``
product and a two-dimensional ``2dfld`` product, and rejects ``2dfld`` outright.

RRFS therefore needs a project-owned route. NOMADS publishes the static files
directly, so this module resolves the release directory, reads the wgrib2
``.idx`` inventory, and pulls exactly the selected GRIB messages over HTTP byte
ranges through the shared transport in :mod:`sharpmod.model_transport`.

Three facts drive the design, each confirmed against live inventories rather
than assumed:

*   ``prslev`` carries 45 isobaric levels (1000 hPa to 2 hPa) and *no* surface,
    2-m, or 10-m record. ``2dfld`` carries the complete verified ground
    contract -- surface pressure and height, 2-m temperature and moisture, and
    10-m winds -- and no isobaric record. A verified sounding needs both files,
    so this module always fetches the pair.
*   Only the 00Z, 06Z, 12Z, and 18Z cycles publish ``prslev``, hourly to F084.
    The off-hour cycles publish a sub-hourly ``2dfld`` product and nothing
    else, so they cannot produce a sounding at any forecast hour.
*   ``prslev`` publishes DZDT and no VVEL, so a written RRFS sounding has no
    pressure vertical velocity. See ``UNUSABLE_PRESSURE_FIELDS`` for why DZDT
    is not fetched as a substitute. No derived parameter depends on VVEL.

NOMADS serves no spatial subsetting for RRFS -- no ``filter_*.pl`` CGI, no
OpenDAP dataset, and no pressure-level AWIPS subset -- so a field plan costs
its full domain footprint. The payload is grid-level rather than point-bound,
so one transfer serves every point at that run and forecast hour.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading

from sharpmod.model_fields import (
    NOAA_SURFACE_SEARCH,
    build_noaa_search,
    choose_noaa_fields,
)
from sharpmod.model_transport import (
    DownloadCancelled,
    RangeTransferMetrics,
    _valid_grib,
    download_ranges,
    parallelize_range_plan,
    plan_ranges,
    range_worker_count,
)


_LOGGER = logging.getLogger(__name__)

NOMADS_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs"

#: Release directories tried in order. ``prod`` is the operational path RRFS
#: occupies from its 12Z 6 October 2026 implementation and answers HTTP 403
#: until then; ``para`` is the pre-implementation parallel path; ``v1.0`` is the
#: versioned directory both alias. Probing in this order migrates the route on
#: implementation day without a code change, and the winner is remembered so
#: the steady state spends no extra request.
RELEASE_PATHS = ("prod", "para", "v1.0")

PRESSURE_PRODUCT = "prslev"
SURFACE_PRODUCT = "2dfld"

#: Cycles that publish the pressure-level product. The off-hour cycles publish
#: only a sub-hourly two-dimensional product, so they carry no sounding.
PRESSURE_CYCLES = (0, 6, 12, 18)

#: Forecast hours published by every pressure-capable cycle.
FORECAST_HOURS = tuple(range(0, 85))

TRANSPORT = "nomads-static-ranges"
PROVIDER = "NOAA NOMADS"

#: NOMADS serves these objects at roughly 1.4 MB/s on one connection and
#: 8.5 MB/s on eight, and a 3-km CONUS field plan is about 370 MB, so this
#: route asks for more concurrency than the shared default. ``range_worker_count``
#: still clamps the value and ``SHARPMOD_RANGE_WORKERS`` still overrides it.
DEFAULT_RANGE_WORKERS = 8

#: A selected RRFS field plan is 315 messages interleaved with roughly as many
#: unselected ones, so the shared 2-MiB/25-percent merge budget would re-read
#: whole unwanted messages: 25 percent of a 13-MB Hawaii plan and 24 MB of a
#: 370-MB CONUS plan. Tightening the budget keeps the request count in the same
#: order (315 messages still collapse to about 50-60 ranges, comfortably more
#: than the worker count) while holding wasted transfer near one percent.
MAX_RANGE_GAP_BYTES = 512 * 1024
MAX_RANGE_OVERHEAD_RATIO = 0.05

#: Fields the planner would otherwise choose but nothing downstream can read.
#:
#: ``choose_noaa_fields`` falls back to DZDT when a product publishes no VVEL,
#: which RRFS does not. cfgrib decodes RRFS DZDT as ``wz``, "geometric vertical
#: velocity" in m/s, while ``Profile.omeg`` is *pressure* vertical velocity in
#: Pa/s with the opposite sign convention. No decode path converts between the
#: two, so fetching DZDT would add 45 messages -- about 12 percent of a field
#: plan, or 37 MB on the 3-km CONUS domain -- that no panel can read, and
#: mapping it through unconverted would render inverted values two orders of
#: magnitude too large. Omega is therefore reported missing for RRFS, the same
#: call already made for Open-Meteo ICON.
UNUSABLE_PRESSURE_FIELDS = frozenset({"DZDT"})

_USER_AGENT = (
    "SHARPpy-Reimagined/1.0 (forecast-model point sounding; "
    "https://github.com/sharppy-reimagined)"
)


class RrfsUnavailable(RuntimeError):
    """RRFS published nothing usable for the requested run and domain."""


@dataclass(frozen=True)
class RrfsDomain:
    """One published RRFS domain and the URL tokens that name it."""

    name: str
    tag: str
    resolution: str
    label: str


#: Keyed by the ``domain`` tag each ``ModelConfig`` already carries.
DOMAINS = {
    "conus": RrfsDomain("conus", "conus", "3km", "3-km CONUS"),
    "alaska": RrfsDomain("alaska", "ak", "3km", "3-km Alaska"),
    "hawaii": RrfsDomain("hawaii", "hi", "2p5km", "2.5-km Hawaii"),
    "puerto rico": RrfsDomain(
        "puerto rico", "pr", "2p5km", "2.5-km Puerto Rico"
    ),
    "north america": RrfsDomain(
        "north america", "na", "13km", "13-km North America"
    ),
}

_IDX_ROW = re.compile(r"^\s*(\d+):(\d+):(.*)$")
_RELEASE_LOCK = threading.Lock()
_RELEASE_CACHE: str | None = None


def domain_for(name) -> RrfsDomain:
    """Return the published domain matching a configured domain tag."""
    if isinstance(name, RrfsDomain):
        return name
    key = re.sub(r"[\s_\-]+", " ", str(name or "").strip().lower())
    try:
        return DOMAINS[key]
    except KeyError:
        raise ValueError("unknown RRFS domain: %r" % (name,)) from None


def publishes_pressure_levels(cycle_hour) -> bool:
    """Return whether a cycle publishes the pressure-level product."""
    try:
        return int(cycle_hour) in PRESSURE_CYCLES
    except (TypeError, ValueError):
        return False


def build_url(release, run_dt, fxx, product, domain) -> str:
    """Return one published RRFS GRIB2 object URL."""
    item = domain_for(domain)
    return (
        f"{NOMADS_ROOT}/{release}/rrfs.{run_dt:%Y%m%d}/{run_dt:%H}/"
        f"rrfs.t{run_dt:%H}z.{product}.{item.resolution}"
        f".f{int(fxx):03d}.{item.tag}.grib2"
    )


def _release_candidates() -> tuple[str, ...]:
    """Return release directories with any remembered winner tried first."""
    with _RELEASE_LOCK:
        cached = _RELEASE_CACHE
    if cached is None:
        return RELEASE_PATHS
    return (cached, *(item for item in RELEASE_PATHS if item != cached))


def _remember_release(release) -> None:
    global _RELEASE_CACHE
    with _RELEASE_LOCK:
        if _RELEASE_CACHE != release:
            _LOGGER.info("rrfs_nomads.release release=%s", release)
            _RELEASE_CACHE = release


def forget_release() -> None:
    """Drop the remembered release directory. Intended for tests."""
    global _RELEASE_CACHE
    with _RELEASE_LOCK:
        _RELEASE_CACHE = None


@dataclass(frozen=True)
class IdxRecord:
    """One wgrib2 inventory row with a resolved inclusive byte range."""

    number: int
    start: int
    end: int
    variable: str
    level: str
    forecast: str
    line: str

    @property
    def size(self) -> int:
        return self.end - self.start + 1


def parse_idx(text, total_size) -> tuple[IdxRecord, ...]:
    """Parse a wgrib2 ``.idx`` inventory into inclusive byte ranges.

    wgrib2 inventories publish a starting offset per record and no length, so
    each record ends one byte before the next distinct offset and the final
    record ends at the object's last byte. ``total_size`` therefore has to come
    from the object itself; the inventory cannot supply it.
    """
    rows = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _IDX_ROW.match(line)
        if match is None:
            continue
        parts = line.split(":")
        rows.append((
            int(match.group(2)),
            int(match.group(1)),
            parts[3] if len(parts) > 3 else "",
            parts[4] if len(parts) > 4 else "",
            parts[5] if len(parts) > 5 else "",
            line,
        ))
    if not rows:
        raise RrfsUnavailable("RRFS inventory published no records")
    total = int(total_size or 0)
    if total <= 0:
        raise RrfsUnavailable("RRFS object published no usable length")

    # Records normally arrive in file order, but sorting defensively keeps an
    # out-of-order inventory from producing a negative-length range.
    rows.sort()
    starts = sorted({row[0] for row in rows})
    following = {
        value: (starts[index + 1] if index + 1 < len(starts) else total)
        for index, value in enumerate(starts)
    }

    records = []
    for start, number, variable, level, forecast, line in rows:
        end = following[start] - 1
        if start < 0 or end < start or end >= total:
            raise RrfsUnavailable(
                "RRFS inventory record %d has an unusable byte range" % number
            )
        records.append(IdxRecord(
            number, start, end, variable, level, forecast, line
        ))
    return tuple(records)


def select_records(records, pattern):
    """Return inventory records whose raw line matches ``pattern``."""
    matcher = re.compile(pattern)
    return tuple(item for item in records if matcher.search(item.line))


def pressure_field_plan(records):
    """Return ``(search, fields)`` for the isobaric records in an inventory.

    ``choose_noaa_fields`` reads a mapping with a ``variable`` column, which is
    how Herbie names it, so the inventory adapter here is a plain dict rather
    than a DataFrame. Restricting it to isobaric rows keeps the level-only
    records from voting on the pressure plan.
    """
    inventory = {
        "variable": [
            item.variable for item in records if item.level.endswith(" mb")
        ]
    }
    fields = tuple(
        name for name in choose_noaa_fields(inventory)
        if str(name).upper() not in UNUSABLE_PRESSURE_FIELDS
    )
    return build_noaa_search(fields), fields


@dataclass(frozen=True)
class RrfsPayload:
    """One downloaded RRFS product subset and its provenance."""

    path: Path
    source_url: str
    fields: tuple[str, ...]
    transferred_bytes: int
    planned_bytes: int
    record_count: int


def provenance_path(combined_path):
    """Return the sidecar path recording what a combined file was built from."""
    combined = Path(combined_path)
    return combined.parent / (combined.name + ".provenance.json")


def write_provenance(combined_path, *, fields, source_url, pressure_fields):
    """Record a combined file's provenance beside it, atomically.

    The two component subsets are deleted once they are combined, because
    together they are about 350 MB on the 3-km domains and the managed cache
    prunes whole model-hour entries -- a surviving component could never be
    reused on its own. That makes this sidecar the only record of which fields
    were actually fetched, so a warm hit can rebuild provenance truthfully
    instead of re-deriving it from a fresh inventory or re-downloading.
    """
    payload = {
        "version": 1,
        "fields": list(fields),
        "pressure_fields": list(pressure_fields),
        "source_url": str(source_url),
        "transport": TRANSPORT,
    }
    target = provenance_path(combined_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
        os.replace(temporary, target)
    except BaseException:
        with suppress(OSError):
            os.remove(temporary)
        raise
    return target


def read_provenance(combined_path):
    """Return a combined file's recorded provenance, or ``None``."""
    try:
        payload = json.loads(
            provenance_path(combined_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    fields = payload.get("fields")
    pressure_fields = payload.get("pressure_fields")
    source_url = payload.get("source_url")
    if not (isinstance(fields, list) and fields):
        return None
    if not (isinstance(pressure_fields, list) and pressure_fields):
        return None
    if not isinstance(source_url, str) or not source_url:
        return None
    return {
        "fields": tuple(str(name) for name in fields),
        "pressure_fields": tuple(str(name) for name in pressure_fields),
        "source_url": source_url,
    }


def _new_session():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT
    return session


def _check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")


def _object_length(session, url, *, timeout, cancelled) -> int:
    """Return the published object length using one one-byte range request."""
    from sharpmod.model_transport import _parse_content_range_details

    _check_cancelled(cancelled)
    with session.get(
        url, headers={"Range": "bytes=0-0"}, stream=True, timeout=timeout
    ) as response:
        status = int(getattr(response, "status_code", 0))
        if status != 206:
            raise RrfsUnavailable(
                "RRFS object did not honor a range request (HTTP %d)" % status
            )
        details = _parse_content_range_details(
            response.headers.get("Content-Range") or ""
        )
        # Drain the single probe byte so the connection returns to the pool.
        for _chunk in response.iter_content(chunk_size=1):
            break
    if details is None:
        raise RrfsUnavailable("RRFS object published no usable length")
    return int(details[2])


def _inventory(session, url, *, timeout, cancelled):
    """Return ``(records, total_size)`` for one published object."""
    _check_cancelled(cancelled)
    with session.get(url + ".idx", timeout=timeout) as response:
        status = int(getattr(response, "status_code", 0))
        if status != 200:
            raise RrfsUnavailable(
                "RRFS inventory is unavailable (HTTP %d)" % status
            )
        text = response.text
    total = _object_length(session, url, timeout=timeout, cancelled=cancelled)
    return parse_idx(text, total), total


def _download_subset(
    session,
    url,
    records,
    output_path,
    *,
    timeout,
    workers,
    cancelled,
    progress,
):
    """Fetch selected GRIB messages into ``output_path``."""
    plan = plan_ranges(
        ((item.start, item.end) for item in records),
        max_gap=MAX_RANGE_GAP_BYTES,
        max_overhead_ratio=MAX_RANGE_OVERHEAD_RATIO,
    )
    # Merging normally leaves far more spans than workers, but a future
    # inventory that collapsed into two or three spans would silently cap
    # concurrency at that count, so split large spans the way the Herbie
    # range path already does.
    plan = parallelize_range_plan(plan, workers)
    metrics = RangeTransferMetrics()
    download_ranges(
        session,
        url,
        plan,
        output_path,
        timeout=timeout,
        cancelled=cancelled,
        progress=progress,
        workers=workers,
        session_factory=_new_session if workers > 1 else None,
        metrics=metrics,
    )
    return metrics


def _subset_name(config_key, run_dt, fxx, product, domain) -> str:
    item = domain_for(domain)
    return (
        f"{config_key}-{run_dt:%Y%m%d%H}-f{int(fxx):03d}"
        f"-{product}-{item.tag}.grib2"
    )


def fetch_pair(
    config_key,
    domain,
    run_dt,
    fxx,
    *,
    download_dir,
    session=None,
    cancelled=None,
    progress=None,
    workers=None,
    timeout=(10, 90),
):
    """Fetch the pressure and ground GRIB subsets for one RRFS forecast hour.

    Returns ``(pressure_payload, surface_payload)``. Both are required: the
    ground product is not an optional enrichment but the only published source
    of the verified surface row, so a failure there is fatal rather than
    something to route around.
    """
    item = domain_for(domain)
    if not publishes_pressure_levels(run_dt.hour):
        raise RrfsUnavailable(
            "RRFS publishes pressure levels only at %s UTC; the %02dZ cycle "
            "carries a sub-hourly two-dimensional product and no sounding"
            % ("/".join("%02dZ" % hour for hour in PRESSURE_CYCLES),
               int(run_dt.hour))
        )

    directory = Path(download_dir).expanduser() if download_dir else Path.cwd()
    directory.mkdir(parents=True, exist_ok=True)
    resolved = range_worker_count(workers, default=DEFAULT_RANGE_WORKERS)

    owned = session is None
    if owned:
        session = _new_session()
    try:
        failures = []
        release = None
        records = ()
        pressure_url = ""
        for candidate in _release_candidates():
            candidate_url = build_url(
                candidate, run_dt, fxx, PRESSURE_PRODUCT, item
            )
            try:
                records, _total = _inventory(
                    session, candidate_url,
                    timeout=timeout, cancelled=cancelled,
                )
            except DownloadCancelled:
                raise
            except RrfsUnavailable as exc:
                failures.append("%s: %s" % (candidate, exc))
                continue
            except Exception as exc:  # noqa: BLE001 - probe every candidate
                failures.append("%s: %s" % (candidate, type(exc).__name__))
                continue
            release, pressure_url = candidate, candidate_url
            _remember_release(candidate)
            break
        if release is None:
            raise RrfsUnavailable(
                "no RRFS release directory publishes %s F%03d for %s (%s)"
                % (run_dt.isoformat(), int(fxx), item.label,
                   "; ".join(failures) or "no candidates")
            )

        search, fields = pressure_field_plan(records)
        selected = select_records(records, search)
        if not selected:
            raise RrfsUnavailable(
                "RRFS %s inventory matched no pressure-level records"
                % item.label
            )
        pressure_path = directory / _subset_name(
            config_key, run_dt, fxx, PRESSURE_PRODUCT, item
        )
        if pressure_path.exists() and _valid_grib(pressure_path):
            pressure_bytes = 0
        else:
            metrics = _download_subset(
                session, pressure_url, selected, pressure_path,
                timeout=timeout, workers=resolved,
                cancelled=cancelled, progress=progress,
            )
            pressure_bytes = int(metrics.transferred_bytes)
        pressure = RrfsPayload(
            path=pressure_path.resolve(),
            source_url=pressure_url,
            fields=tuple(fields),
            transferred_bytes=pressure_bytes,
            planned_bytes=sum(record.size for record in selected),
            record_count=len(selected),
        )

        surface_url = build_url(
            release, run_dt, fxx, SURFACE_PRODUCT, item
        )
        surface_records, _surface_total = _inventory(
            session, surface_url, timeout=timeout, cancelled=cancelled,
        )
        surface_selected = select_records(surface_records, NOAA_SURFACE_SEARCH)
        if not surface_selected:
            raise RrfsUnavailable(
                "RRFS %s ground product published no verified surface records"
                % item.label
            )
        surface_path = directory / _subset_name(
            config_key, run_dt, fxx, SURFACE_PRODUCT, item
        )
        if surface_path.exists() and _valid_grib(surface_path):
            surface_bytes = 0
        else:
            metrics = _download_subset(
                session, surface_url, surface_selected, surface_path,
                timeout=timeout, workers=resolved,
                cancelled=cancelled, progress=progress,
            )
            surface_bytes = int(metrics.transferred_bytes)
        surface = RrfsPayload(
            path=surface_path.resolve(),
            source_url=surface_url,
            fields=tuple(dict.fromkeys(
                record.variable.upper() for record in surface_selected
            )),
            transferred_bytes=surface_bytes,
            planned_bytes=sum(record.size for record in surface_selected),
            record_count=len(surface_selected),
        )
        return pressure, surface
    finally:
        if owned:
            close = getattr(session, "close", None)
            if callable(close):
                close()
