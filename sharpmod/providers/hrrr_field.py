"""Fetch, decode, reproject and colour one HRRR product into a map overlay.

No Qt. Everything here runs on a worker thread and hands back a frozen
:class:`~sharpmod.maps.map_overlays.OverlayRaster`, which is the contract the map
widgets already use for the radar mosaic.

The pipeline, and why each step is where it is
----------------------------------------------
1. **Inventory.** HRRR publishes a ``.idx`` sidecar listing every GRIB message
   with its byte offset. Fetching that (a few tens of kilobytes) tells us exactly
   which bytes a product needs.
2. **Byte-range download.** Only the messages a product uses are transferred.
   Composite reflectivity is 257 KiB out of a 135 MiB file; the run-maximum
   updraft helicity is 16 KiB. This is what makes native-resolution CONUS fields
   affordable, and it is why this module talks to the AWS open-data bucket rather
   than NOMADS, whose GRIB filter is globally throttled to one request every ten
   seconds.
3. **Decode.** eccodes turns each message into the full 1059x1799 Lambert
   conformal grid.
4. **Derive.** The product's own arithmetic combines its records, vectorised over
   the whole grid.
5. **Reproject.** HRRR's grid is Lambert conformal; the overlay contract is
   plate carree with pixel (0, 0) at the north-west corner. The index map that
   relates the two depends only on the output frame geometry, never on the data,
   so it is computed once and reused by every product and every forecast hour.
6. **Colour and encode.** A lookup table turns the field into RGBA in one
   vectorised pass, and PNG is what ``OverlayRaster`` accepts.

Caching is layered to match what actually changes: the inventory per file, the
reprojection index per frame geometry, and the finished frame per
product/run/hour. A past run's frame never changes, so those are also written to
disk; the current run's are held in memory only.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sharpmod.providers.hrrr_products import (
    MISSING,
    SOURCE_PRESSURE,
    SOURCE_SURFACE,
    HrrrProduct,
    Palette,
    ECMWF_MODELS,
    GLOBAL_MODELS,
    get_product,
)
from sharpmod.models.lambert_grid import HRRR_GRID, HRRR_PROJ4, NAM_GRID, NAM_PROJ4, HRW_GRID
from sharpmod.providers.hrrr_products import HRW_MODELS
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.maps.map_overlays import MAX_RASTER_BYTES, OverlayRaster

_LOGGER = logging.getLogger("sharpmod.providers.hrrr_field")

#: One registry key for every product, which is what makes them mutually
#: exclusive on the map: selecting a different product replaces this slot rather
#: than stacking a second field on top of the first. The radar and SPC overlays
#: use their own keys and so continue to compose with whichever field is showing.
OVERLAY_KEY = "hrrr_field"

OVERLAY_TITLE = "HRRR model field"
ATTRIBUTION = "NOAA/NCEP HRRR via AWS Open Data"

#: AWS Open Data mirror of the operational HRRR. Public, un-throttled, and it
#: honours HTTP range requests, which the whole design depends on.
BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

#: HRRR CONUS Lambert conformal grid. Verified against the ``latitudes`` and
#: ``longitudes`` arrays encoded in a live GRIB message: agreement is exact at
#: all 1,905,141 grid points, so these constants are not an approximation of the
#: grid, they are the grid.
#:
#: Held in :mod:`sharpmod.models.lambert_grid` rather than here, because the picker needs
#: the same grid to draw the domain's real boundary and cannot import this module
#: -- it would pull NumPy into the interface's startup path. These names stay as
#: aliases so the reprojection below still reads as arithmetic on plain numbers.
HRRR_SHAPE = HRRR_GRID.shape
HRRR_SPACING = HRRR_GRID.spacing
HRRR_X0 = HRRR_GRID.x0
HRRR_Y0 = HRRR_GRID.y0

#: Plate-carree frame the fields are rendered into.
#:
#: Fixed rather than derived from the viewport: one render serves every picker
#: map however each is panned, panning and zooming cost nothing because only the
#: frame corners are re-projected, and the image stays glued to the basemap
#: during the wheel-zoom preview.
#:
#: Unlike the radar mosaic, there is also nothing to gain by narrowing it. That
#: frame is about 1.5 km per pixel against MRMS's native 1 km, so it genuinely
#: discards detail and a single-site request recovers it. This frame is already
#: finer than its source -- see the oversampling note below -- so a smaller,
#: denser window would interpolate rather than reveal.
#:
#: The bounds are the lon/lat envelope of the HRRR domain. Its Lambert edges bow,
#: so an axis-aligned box necessarily includes corners outside the model -- those
#: come back transparent.
COVERAGE_BOUNDS = (-134.5, -60.5, 20.5, 53.0)
NAM_COVERAGE_BOUNDS = NAM_GRID.bounds()


def field_grid(model: str = "hrrr"):
    """Return the grid encoded by this model's native field products."""
    if model in GLOBAL_MODELS:
        from sharpmod.providers.ecmwf_fields import grid
        return grid(model)
    return HRW_GRID if model in HRW_MODELS else NAM_GRID if model == "nam" else HRRR_GRID


def field_bounds(model: str = "hrrr"):
    if model in GLOBAL_MODELS:
        from sharpmod.providers.ecmwf_fields import BOUNDS
        return BOUNDS
    return HRW_GRID.bounds() if model in HRW_MODELS else NAM_COVERAGE_BOUNDS if model == "nam" else COVERAGE_BOUNDS

#: Output frame size. Chosen at roughly the native 3 km resolution of the source:
#: coarser would throw away model detail the user asked to see, finer would only
#: interpolate. 3072 columns across 74 degrees is about 2.2 km per pixel at the
#: domain's mid-latitude, so the grid is slightly oversampled rather than lossy.
DEFAULT_FRAME_SIZE = (3072, 1536)
MIN_FRAME_PIXELS = 256
MAX_FRAME_PIXELS = 6144

#: A finished frame stays useful until the next run lands. HRRR runs hourly, so
#: the live frame is refreshed on a shorter cycle than that to pick up a late
#: publication without hammering the bucket.
FRAME_CACHE_TTL_S = 600.0
FAILURE_CACHE_TTL_S = 90.0
_CACHE_MAX_ENTRIES = 24

#: Inventories are immutable once a file is published.
_INVENTORY_CACHE_TTL_S = 3600.0
_INVENTORY_CACHE_MAX = 64

#: HRRR publishes hourly; a run needs roughly this long before its later
#: forecast hours are on the bucket.
PUBLICATION_LAG = timedelta(minutes=50)

#: Forecast hours HRRR publishes. Runs at 00/06/12/18Z go to 48 hours, the rest
#: to 18.
MAX_FXX_EXTENDED = 48
MAX_FXX_STANDARD = 18
EXTENDED_CYCLES = (0, 6, 12, 18)


class HrrrFieldError(RuntimeError):
    """A field could not be produced. Cancellation is not one of these."""


class HrrrFieldUnavailable(HrrrFieldError):
    """The requested run, hour, or record is not published."""


class HrrrFieldOffline(HrrrFieldError):
    """The transport could not reach the provider; data absence is unknown."""


# --------------------------------------------------------------------------- #
# Run and hour resolution
# --------------------------------------------------------------------------- #


def max_forecast_hour(cycle: int) -> int:
    return MAX_FXX_EXTENDED if int(cycle) in EXTENDED_CYCLES else MAX_FXX_STANDARD


def latest_run(now: datetime | None = None) -> datetime:
    """Return the most recent run likely to be fully published."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = (moment - PUBLICATION_LAG).replace(
        minute=0, second=0, microsecond=0)
    return candidate


def resolve_request(valid_time: datetime | None, *,
                    now: datetime | None = None) -> tuple[datetime, int]:
    """Return the ``(run, forecast_hour)`` that best serves ``valid_time``.

    Prefers the newest run that reaches the requested time, which is what a
    forecaster wants: the freshest guidance for that hour rather than a long lead
    from an older cycle.
    """
    reference = latest_run(now)
    if valid_time is None:
        return reference, 0
    wanted = valid_time.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0)
    if wanted <= reference:
        # An hour that has already passed is depicted by its own run's analysis.
        # Searching forward from the newest run cannot reach backwards, and an
        # observed sounding from last week would otherwise fall through to the
        # clamp below and resolve to today's run at F000 -- a field valid days
        # away from the profile it is meant to sit beside.
        return wanted, 0
    # Long leads exist only on the extended 00/06/12/18Z cycles. When the newest
    # run cannot reach the hour, step back to a cycle that can rather than clamp
    # to a lead nobody asked for.
    run = reference
    lead = int((wanted - run).total_seconds() // 3600)
    while lead <= MAX_FXX_EXTENDED:
        if lead <= max_forecast_hour(run.hour):
            return run, lead
        run -= timedelta(hours=1)
        lead += 1
    # Nothing published reaches that far ahead; clamp to the newest run's limit.
    return reference, max_forecast_hour(reference.hour)


def pin_request(run: datetime, fxx: int | None = None) -> tuple[datetime, int]:
    """Return an exact ``(run, forecast_hour)`` normalised to HRRR's publication.

    Used when the caller has chosen a *cycle* rather than a moment. The map then
    depicts the very grid the sounding will be cut from, instead of the freshest
    run that happens to reach the same hour: a 12Z F18 profile and an 18Z F12
    field are valid at one time but are two different forecasts, and reading one
    against the other is a mistake the overlay should not invite.
    """
    anchored = (run.replace(tzinfo=timezone.utc) if run.tzinfo is None
                else run.astimezone(timezone.utc))
    anchored = anchored.replace(minute=0, second=0, microsecond=0)
    hour = max(0, int(fxx or 0))
    limit = max_forecast_hour(anchored.hour)
    if hour > limit:
        # A non-extended cycle stops at F18. Clamping keeps the request on
        # something HRRR publishes; the returned hour is what the legend states,
        # so the map never claims a lead it did not fetch.
        _LOGGER.debug("hrrr_field.fxx_clamped run=%s requested=%d limit=%d",
                      anchored.isoformat(), hour, limit)
        hour = limit
    return anchored, hour


def grib_url(run: datetime, fxx: int, source: str, model: str = "hrrr", *, release=None) -> str:
    if model == "gfs":
        return (f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run:%Y%m%d}/{run:%H}/atmos/"
                f"gfs.t{run:%H}z.pgrb2.0p25.f{int(fxx):03d}")
    if model == "aigfs":
        product = "sfc" if source == SOURCE_SURFACE else "pres"
        return (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/aigfs/prod/aigfs.{run:%Y%m%d}/"
                f"{run:%H}/model/atmos/grib2/aigfs.t{run:%H}z.{product}.f{int(fxx):03d}.grib2")
    if model in HRW_MODELS:
        core = "fv3" if model == "hrw-fv3" else "arw"
        domain = "conusmem2" if model == "hrw-wrf-nssl" else "conus"
        return (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hiresw/prod/hiresw.{run:%Y%m%d}/"
                f"hiresw.t{run:%H}z.{core}_5km.f{int(fxx):02d}.{domain}.grib2")
    if model in ECMWF_MODELS:
        from sharpmod.providers.ecmwf_fields import grib_url as ecmwf_url
        return ecmwf_url(run, fxx, source, model)
    if model == "nam":
        product = "awip12" if source == SOURCE_SURFACE else "awphys"
        return (f"https://noaa-nam-pds.s3.amazonaws.com/nam.{run:%Y%m%d}/"
                f"nam.t{run:%H}z.{product}{int(fxx):02d}.tm00.grib2")
    if model == "nam-3km-conus":
        return (f"https://noaa-nam-pds.s3.amazonaws.com/nam.{run:%Y%m%d}/"
                f"nam.t{run:%H}z.conusnest.hiresf{int(fxx):02d}.tm00.grib2")
    if model == "rrfs-a" or model.startswith("refs-m"):
        from sharpmod.providers import rrfs_nomads
        return rrfs_nomads.build_url(
            release or rrfs_nomads._release_candidates()[0], run, fxx,
            rrfs_nomads.SURFACE_PRODUCT if source == SOURCE_SURFACE else rrfs_nomads.PRESSURE_PRODUCT,
            "conus", member=int(model[-1]) if model.startswith("refs-m") else None,
        )
    kind = "wrfsfc" if source == SOURCE_SURFACE else "wrfprs"
    return (f"{BUCKET}/hrrr.{run:%Y%m%d}/conus/"
            f"hrrr.t{run:%H}z.{kind}f{int(fxx):02d}.grib2")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


#: Retain the existing per-product concurrency limit for range orchestration.
_RANGE_WORKERS = 4


def _default_opener(url: str, timeout: float, limit: int,
                    byte_range: tuple[int, int] | None = None, *, should_cancel=None) -> bytes:
    """Use native pooled HTTP, byte budgets, retries and cancellation."""
    import sharpmod_rs

    return sharpmod_rs.fetch_model_bytes(url, timeout, limit, byte_range, should_cancel)


def _remote_limits() -> tuple[float, int]:
    """Timeout and byte cap, shared with the rest of the project's remote IO."""
    try:
        from sharpmod.io.decoder import _max_remote_bytes, _remote_timeout
        return float(_remote_timeout()), int(_max_remote_bytes())
    except Exception:  # noqa: BLE001 - defaults are fine if that moves
        return 30.0, 64 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InventoryRow:
    """One GRIB message: where it starts, how long it is, and what it holds."""

    index: int
    offset: int
    length: int
    variable: str
    level: str
    forecast: str
    field_index: int = 0


_INVENTORY_LOCK = threading.Lock()
_INVENTORY_CACHE: dict[tuple, tuple[float, tuple[InventoryRow, ...]]] = {}


def parse_inventory(text: str) -> tuple[InventoryRow, ...]:
    """Parse a wgrib2 ``.idx`` listing.

    Rows are ``n:offset:d=YYYYMMDDHH:VAR:level:forecast:``. A record's length is
    the next record's offset minus its own; the final record runs to the end of
    the object, whose size the sidecar does not state, so it is left as zero and
    the caller requests an open-ended range for it.
    """
    rows: list[tuple[int, int, str, str, str, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        try:
            number, _, part = parts[0].partition(".")
            index = int(number)
            field_index = int(part) - 1 if part else 0
            if field_index < 0:
                continue
        except ValueError:
            continue
        rows.append((index, int(parts[1]), parts[3], parts[4], parts[5], field_index))
    offsets = tuple(dict.fromkeys(row[1] for row in rows))
    next_offset = dict(zip(offsets, (*offsets[1:], 0)))
    parsed: list[InventoryRow] = []
    for index, offset, variable, level, forecast, field_index in rows:
        following = next_offset[offset]
        length = max(0, following - offset) if following else 0
        parsed.append(InventoryRow(index, offset, length, variable, level,
                                   forecast, field_index))
    return tuple(parsed)


def fetch_inventory(url: str, *, opener=None, timeout=None,
                    now: float | None = None) -> tuple[InventoryRow, ...]:
    """Return the parsed inventory for a GRIB object, cached by URL."""
    moment = time.monotonic() if now is None else now
    with _INVENTORY_LOCK:
        cached = _INVENTORY_CACHE.get(url)
        if cached is not None and moment - cached[0] < _INVENTORY_CACHE_TTL_S:
            return cached[1]

    limit_timeout, _limit_bytes = _remote_limits()
    call = opener or _default_opener
    is_ecmwf = "/ifs/" in url
    index_url = url.removesuffix(".grib2") + ".index" if is_ecmwf else url + ".idx"
    payload = call(index_url, timeout or limit_timeout, 4 * 1024 * 1024)
    if is_ecmwf:
        from sharpmod.providers.ecmwf_fields import parse_inventory as parse_ecmwf
        rows = parse_ecmwf(payload.decode("utf-8", errors="replace"))
    else:
        rows = parse_inventory(payload.decode("utf-8", errors="replace"))
    if not rows:
        raise HrrrFieldUnavailable("model inventory was empty or unreadable")

    with _INVENTORY_LOCK:
        if len(_INVENTORY_CACHE) >= _INVENTORY_CACHE_MAX:
            oldest = min(_INVENTORY_CACHE,
                         key=lambda key: _INVENTORY_CACHE[key][0])
            _INVENTORY_CACHE.pop(oldest, None)
        _INVENTORY_CACHE[url] = (moment, rows)
    return rows


def locate(rows: tuple[InventoryRow, ...], spec) -> InventoryRow:
    """Return the inventory row a :class:`FieldSpec` addresses."""
    for row in rows:
        if spec.matches(row.variable, row.level, row.forecast):
            return row
    raise HrrrFieldUnavailable(
        f"model does not publish {spec.variable}:{spec.level}"
        + (f":{spec.forecast}" if spec.forecast else ""))


def _merge_ranges(rows: list[InventoryRow], *, slack: int = 65536
                  ) -> list[tuple[int, int, list[InventoryRow]]]:
    """Group records into as few HTTP ranges as possible.

    Adjacent and near-adjacent messages are fetched together: one request for a
    contiguous span beats several for its pieces, and pulling up to ``slack``
    bytes of records we do not need is cheaper than the extra round trips. A
    product needing seven pressure levels typically collapses to two or three
    requests this way.
    """
    ordered = sorted(rows, key=lambda row: row.offset)
    groups: list[tuple[int, int, list[InventoryRow]]] = []
    for row in ordered:
        end = row.offset + row.length - 1 if row.length else 0
        if groups and end and groups[-1][1]:
            start_prev, end_prev, members = groups[-1]
            if row.offset - end_prev - 1 <= slack:
                groups[-1] = (start_prev, max(end_prev, end), members + [row])
                continue
        groups.append((row.offset, end, [row]))
    return groups


# --------------------------------------------------------------------------- #
# Decode
# --------------------------------------------------------------------------- #


def _load_eccodes():
    try:
        from sharpmod.backends.grib import load_eccodes
        return load_eccodes()
    except Exception as error:  # noqa: BLE001
        raise HrrrFieldError(
            "model fields need the GRIB runtime (eccodes); install the "
            "'era5' extra to enable them") from error


def decode_message(payload: bytes, eccodes=None, *, field_index: int = 0,
                   model: str = "hrrr") -> np.ndarray:
    """Decode one GRIB message into the selected model's native grid.

    Takes exactly one message. The caller slices it out of a downloaded span
    using the byte offset and length the inventory already gave us, which is
    both cheaper than letting a GRIB reader scan the buffer and, more usefully,
    means a decoded array is identified by *the row that asked for it*. Matching
    on eccodes' own level vocabulary instead would mean maintaining a second
    spelling of every level next to the ``.idx`` spelling the catalogue uses, and
    the two do not agree -- ``.idx`` says ``REFC:entire atmosphere`` where
    eccodes says ``refc`` at ``entireAtmosphere``.
    """
    from contextlib import ExitStack
    from sharpmod.backends.grib import _ECCODES_LOCK

    if not payload.startswith(b"GRIB"):
        raise HrrrFieldError("byte range did not start at a GRIB message")
    # ecCodes' definition parser is global, including across browser tabs.
    with _ECCODES_LOCK, ExitStack() as stack:
        api = eccodes or _load_eccodes()
        if field_index:
            # Python omits ecCodes' in-memory multi-field reader; reuse its installed C library.
            from gribapi import gribapi
            ffi, lib = gribapi.ffi, gribapi.lib
            try:
                reader = lib.grib_handle_new_from_multi_message
            except AttributeError:
                ffi.cdef("grib_handle *grib_handle_new_from_multi_message(grib_context *, void **, size_t *, int *);")
                reader = lib.grib_handle_new_from_multi_message
            buffer = ffi.new("char[]", payload)
            data = ffi.new("void **", buffer)
            length = ffi.new("size_t *", len(payload))
            error = ffi.new("int *")
            api.codes_grib_multi_support_on()
            stack.callback(api.codes_grib_multi_support_off)
            for index in range(field_index + 1):
                message = reader(ffi.NULL, data, length, error)
                if message == ffi.NULL:
                    raise HrrrFieldError(f"GRIB field {field_index} could not be opened ({error[0]})")
                handle = gribapi.put_handle(message)
                if index < field_index:
                    api.codes_release(handle)
        else:
            handle = api.codes_new_from_message(payload)
        if handle is None:
            raise HrrrFieldError("GRIB message could not be opened")
        try:
            ni = int(api.codes_get(handle, "Ni"))
            nj = int(api.codes_get(handle, "Nj"))
            if model in GLOBAL_MODELS:
                native = field_grid(model)
                expected_header = {
                    "gridType": "regular_ll", "Ni": native.shape[1], "Nj": native.shape[0],
                    "latitudeOfFirstGridPointInDegrees": 90.0, "longitudeOfFirstGridPointInDegrees": 180.0 if model in ECMWF_MODELS else 0.0,
                    "iDirectionIncrementInDegrees": native.spacing, "jDirectionIncrementInDegrees": native.spacing,
                    "jScansPositively": 0, "iScansNegatively": 0, "uvRelativeToGrid": 0,
                    "jPointsAreConsecutive": 0, "alternativeRowScanning": 0,
                }
                if any(api.codes_get(handle, key) != value for key, value in expected_header.items()):
                    raise HrrrFieldError(f"{model} field does not match its published latitude/longitude grid")
            values = np.asarray(api.codes_get_values(handle), dtype=np.float32)
            scale = 1000 if model in ECMWF_MODELS and api.codes_get(handle, "shortName") == "tp" else 1
            try:
                missing = float(api.codes_get(handle, "missingValue"))
            except Exception:  # noqa: BLE001 - not every message declares one
                missing = 9999.0
        finally:
            api.codes_release(handle)
    grid = values.reshape(nj, ni)
    expected = field_grid(model).shape
    if grid.shape != expected:
        raise HrrrFieldError(
            f"unexpected model grid {grid.shape}, wanted {expected}")
    if model in {"aigfs", "gfs"}:
        grid = np.roll(grid, ni // 2, axis=1)  # Match the map's -180 to 180 longitude order.
    return np.where(grid == missing, MISSING, grid) * scale


# --------------------------------------------------------------------------- #
# Reprojection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _IndexMap:
    """Nearest-neighbour mapping from an output frame to HRRR grid cells.

    ``flat`` indexes a raveled HRRR grid; ``inside`` marks the output pixels that
    fall within the model domain at all. Both are flat arrays of the output
    frame's pixel count.

    Nearest neighbour rather than bilinear on purpose: the output frame is
    slightly finer than the source grid, so bilinear would smooth model detail
    without adding information, and for a banded scale it would invent
    intermediate classes along every boundary.
    """

    width: int
    height: int
    bounds: tuple[float, float, float, float]
    flat: np.ndarray
    inside: np.ndarray


_INDEX_LOCK = threading.Lock()
# Each default 3072x1536 mapping holds roughly 24 MiB of indices/mask.  An
# export can request a different size, so an unbounded geometry-keyed cache
# grows by another full frame for every custom dimension.  Keep the two most
# recently used geometries (the live map plus one export), never an entire size
# history.  The lock also serializes a cold miss: four field panels all asking
# for the same frame must pay for one projection, not four in parallel.
_INDEX_CACHE_MAX_ENTRIES = 2
_INDEX_CACHE: OrderedDict[tuple, _IndexMap] = OrderedDict()




def reproject(field: np.ndarray, mapping: _IndexMap) -> np.ndarray:
    """Sample a HRRR grid onto the output frame."""
    sampled = field.reshape(-1)[mapping.flat]
    sampled = np.where(mapping.inside, sampled, MISSING)
    return sampled.reshape(mapping.height, mapping.width)


# --------------------------------------------------------------------------- #
# Colour
# --------------------------------------------------------------------------- #

#: Resolution of the colour lookup table. 1024 entries is finer than any display
#: can distinguish on a continuous ramp and keeps the banded scales exact.
_LUT_SIZE = 1024


def _hex_to_rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))










# --------------------------------------------------------------------------- #
# Disk cache for immutable frames
# --------------------------------------------------------------------------- #


def disk_cache_root() -> Path:
    override = os.environ.get("SHARPMOD_HRRR_FIELD_CACHE", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "SHARPpy Reimagined" / "hrrr-fields"


def _disk_path(product_key: str, run: datetime, fxx: int,
               size: tuple[int, int]) -> Path:
    stem = (f"{product_key}_{run:%Y%m%d%H}_f{int(fxx):02d}"
            f"_{int(size[0])}x{int(size[1])}")
    return disk_cache_root() / f"{stem}.png"


MAX_MAP_DISK_BYTES = 2 * 1024 ** 3
MAX_MAP_DISK_AGE_SECONDS = 48 * 3600
_DISK_LOCK = threading.RLock()
_NEXT_DISK_PRUNE = 0.0
_MANAGED_FRAME = re.compile(
    r"(?:[a-z0-9-]+_\d{10}_f\d{2,3}_\d+x\d+\.png|"
    r"(?:grid|contours)-v1_[a-z0-9-]+_[a-z0-9-]+_\d{10}_f\d{3}_\d+x\d+\.json\.gz|"
    r"tmp[a-z0-9_]{8}\.part)"
)


def _prune_disk_cache(*, keep: Path | None = None, incoming: int = 0) -> bool:
    """Retain recent managed frames, reserving space before an atomic write."""
    root = disk_cache_root()
    if incoming > MAX_MAP_DISK_BYTES:
        return False
    with _DISK_LOCK:
        try:
            if keep is not None and keep.parent.resolve() != root.resolve():
                return False
            info = root.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return False
            now = time.time()
            entries = []
            with os.scandir(root) as children:
                for child in children:
                    try:
                        info = child.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if (not stat.S_ISREG(info.st_mode)
                            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                            or _MANAGED_FRAME.fullmatch(child.name) is None):
                        continue
                    path = Path(child.path)
                    if path == keep:
                        continue
                    partial = child.name.endswith(".part")
                    protected = partial and now - info.st_mtime < 3600
                    expired = now - info.st_mtime > (3600 if partial else MAX_MAP_DISK_AGE_SECONDS)
                    entries.append((info.st_mtime, info.st_size, path, protected, expired))
            total = incoming + sum(entry[1] for entry in entries)
            # shortcut: scan this flat cache on writes; add an index if directory scans become material.
            for _, size, path, protected, expired in sorted(entries):
                if protected or (not expired and total <= MAX_MAP_DISK_BYTES):
                    continue
                try:
                    path.unlink()
                    total -= size
                except OSError:
                    pass  # Locked files still count; refuse a new cache write if necessary.
            return total <= MAX_MAP_DISK_BYTES
        except FileNotFoundError:
            return True
        except OSError:
            return False


def _disk_read(path: Path) -> bytes | None:
    global _NEXT_DISK_PRUNE
    try:
        with _DISK_LOCK:
            if time.monotonic() >= _NEXT_DISK_PRUNE:
                _prune_disk_cache()
                _NEXT_DISK_PRUNE = time.monotonic() + 60
            if path.is_file():
                data = path.read_bytes()
                with suppress(OSError):
                    os.utime(path, None)
                return data
    except OSError:
        return None
    return None


def _disk_write(path: Path, payload: bytes) -> None:
    """Write a frame atomically, and never fail the render if we cannot."""
    try:
        with _DISK_LOCK:
            if not _prune_disk_cache(keep=path, incoming=len(payload)):
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                os.replace(temporary, path)
                # Reconcile frames published by other backend processes during this write.
                _prune_disk_cache()
            except BaseException:
                with suppress(OSError):
                    os.unlink(temporary)
                raise
    except OSError:
        _LOGGER.debug("hrrr_field.disk_write_failed path=%s", path, exc_info=True)


# --------------------------------------------------------------------------- #
# Derived-value cache: numeric fields beside the rendered PNG
# --------------------------------------------------------------------------- #

#: The most recent derived grid per ``(product, run_iso, fxx, size)``, kept
#: beside the rendered frame so synchronous map inspection can read numbers
#: from the same forecast the map depicts without a second download or a
#: colour lookup. Bounded beside :data:`_CACHE`; entries are replaced (not
#: accumulated) when the same cell is re-derived.
_DERIVED_LOCK = threading.Lock()
_DERIVED_CACHE: dict[tuple, tuple[float, object]] = {}
_DERIVED_CACHE_MAX_ENTRIES = 8


def _derived_key(product_key: str, run, fxx: int, size) -> tuple:
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, OverflowError):
        width, height = DEFAULT_FRAME_SIZE
    try:
        iso = run.isoformat()
    except AttributeError:
        iso = str(run)
    return (str(product_key), str(iso), int(fxx), (width, height))


def remember_derived(product_key: str, run, fxx: int, size,
                     field) -> None:
    """Remember a derived display-units grid for later numeric sampling."""
    from copy import deepcopy

    try:
        snapshot = deepcopy(field)
    except Exception:  # noqa: BLE001 - a failed copy still caches nothing
        return
    key = _derived_key(product_key, run, fxx, size)
    with _DERIVED_LOCK:
        if len(_DERIVED_CACHE) >= _DERIVED_CACHE_MAX_ENTRIES:
            oldest = min(_DERIVED_CACHE, key=lambda entry: _DERIVED_CACHE[entry][0])
            _DERIVED_CACHE.pop(oldest, None)
        _DERIVED_CACHE[key] = (time.monotonic(), snapshot)


def cached_derived(product_key: str, run, fxx: int, size):
    """Return the remembered derived grid for this render, or ``None``."""
    key = _derived_key(product_key, run, fxx, size)
    with _DERIVED_LOCK:
        entry = _DERIVED_CACHE.get(key)
    if entry is None:
        return None
    return entry[1]


def has_derived(product_key: str, run, fxx: int, size) -> bool:
    """Return whether a derived grid is remembered for this render."""
    try:
        key = _derived_key(product_key, run, fxx, size)
    except Exception:  # noqa: BLE001 - an unusable key reads as absent
        return False
    with _DERIVED_LOCK:
        return key in _DERIVED_CACHE




def field_valid_label(run, fxx: int) -> str:
    """Return ``"valid 04 Sep 18Z · 12Z run F06"``-style provenance text."""
    try:
        valid = run + timedelta(hours=int(fxx))
        return (f"valid {valid:%d %b %HZ} · {run:%HZ} run F{int(fxx):02d}")
    except Exception:  # noqa: BLE001 - provenance text is advisory
        return ""


def clear_derived_cache() -> None:
    """Drop remembered derived grids (tests and explicit refresh)."""
    with _DERIVED_LOCK:
        _DERIVED_CACHE.clear()


# --------------------------------------------------------------------------- #
# The public entry point
# --------------------------------------------------------------------------- #

_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class _UnpublishedFrame:
    """Small negative-cache entry, preserving a real no-data classification."""

    message: str


_CACHE: dict[tuple, tuple[float, OverlayRaster | _UnpublishedFrame]] = {}
# The frame cache is populated *after* download, decode, reprojection and PNG
# encoding.  Its ordinary lock protects the dictionary but did not coalesce a
# cold miss: four same-product panels could all pass the lookup and repeat the
# expensive pipeline.  A temporary per-key lock shares only identical work;
# different fields may still fetch concurrently.  Entries leave this registry
# as soon as the last caller returns, even after a failed/cancelled request.
_FLIGHTS_LOCK = threading.Lock()
_FLIGHTS: dict[tuple, tuple[threading.Lock, int]] = {}


def _singleflight_field(cache_key, produce, should_cancel):
    with _FLIGHTS_LOCK:
        flight, users = _FLIGHTS.get(cache_key, (threading.Lock(), 0))
        _FLIGHTS[cache_key] = (flight, users + 1)
    acquired = False
    try:
        while not flight.acquire(timeout=0.1):
            if should_cancel and should_cancel():
                return None
        acquired = True
        if should_cancel and should_cancel():
            return None
        return produce()
    finally:
        if acquired:
            flight.release()
        with _FLIGHTS_LOCK:
            _same, users = _FLIGHTS[cache_key]
            if users <= 1:
                _FLIGHTS.pop(cache_key, None)
            else:
                _FLIGHTS[cache_key] = (flight, users - 1)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    with _DERIVED_LOCK:
        _DERIVED_CACHE.clear()
    with _INVENTORY_LOCK:
        _INVENTORY_CACHE.clear()


def invalidate_frame(product_key: str, *, valid_time=None, run=None,
                     fxx=None, size=DEFAULT_FRAME_SIZE,
                     failed_only: bool = False) -> bool:
    """Forget just one selected frame, leaving other panels' caches intact.

    Retry removes only an unpublished negative entry. Refresh expires a
    successful memory frame without dropping its raster, so a failed remote
    replacement cannot erase the last useful result. Immutable disk frames and
    numeric grids remain reusable while a replacement is in flight.
    """
    product = get_product(product_key)
    if run is None:
        run, fxx = resolve_request(valid_time)
    else:
        run, fxx = pin_request(run, fxx)
    key = (product.key, run.isoformat(), fxx, _clamp_size(size))
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None or (failed_only and not isinstance(
                cached[1], _UnpublishedFrame)):
            return False
        if isinstance(cached[1], _UnpublishedFrame):
            _CACHE.pop(key, None)
        else:
            # ``monotonic()`` can be less than the TTL on a fresh CI runner.
            # Negative infinity is expired regardless of machine uptime.
            _CACHE[key] = (float("-inf"), cached[1])
    return True


def covers(view: tuple[float, float, float, float]) -> bool:
    """Whether the HRRR domain intersects a lon/lat view at all."""
    lon0, lon1, lat0, lat1 = view
    west, east, south, north = COVERAGE_BOUNDS
    return not (lon1 < west or lon0 > east or lat1 < south or lat0 > north)


def _clamp_size(size) -> tuple[int, int]:
    width, height = size
    width = max(MIN_FRAME_PIXELS, min(MAX_FRAME_PIXELS, int(width)))
    height = max(MIN_FRAME_PIXELS, min(MAX_FRAME_PIXELS, int(height)))
    return width, height


#: Cap on a single open-ended range request, used for the last record in an
#: object because the ``.idx`` sidecar does not state the object's total size.
_TAIL_BUDGET = 8 * 1024 * 1024


from sharpmod.providers.hrrr_field_images import (  # noqa: E402
    index_map,
    build_lut,
    colourize,
    draw_contours,
    encode_png,
    sample_derived
)


from sharpmod.providers.hrrr_field_fetch import (  # noqa: E402
    _download_fields,
    render_field,
    fetch_field,
    _fetch_resolved_field
)
