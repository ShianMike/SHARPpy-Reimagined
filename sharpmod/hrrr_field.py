"""Fetch, decode, reproject and colour one HRRR product into a map overlay.

No Qt. Everything here runs on a worker thread and hands back a frozen
:class:`~sharpmod.map_overlays.OverlayRaster`, which is the contract the map
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
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sharpmod.hrrr_products import (
    MISSING,
    SOURCE_PRESSURE,
    SOURCE_SURFACE,
    HrrrProduct,
    Palette,
    get_product,
)
from sharpmod.map_overlays import MAX_RASTER_BYTES, OverlayRaster

_LOGGER = logging.getLogger("sharpmod.hrrr_field")

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
HRRR_SHAPE = (1059, 1799)
HRRR_SPACING = 3000.0
HRRR_X0 = -2697520.1425219304
HRRR_Y0 = -1587306.1525566636
HRRR_PROJ4 = ("+proj=lcc +lat_0=38.5 +lon_0=262.5 +lat_1=38.5 "
              "+lat_2=38.5 +R=6371229 +units=m +no_defs")

#: Plate-carree frame the fields are rendered into.
#:
#: Fixed rather than derived from the viewport, for the same reasons the radar
#: mosaic is: one render serves every picker map however each is panned, panning
#: and zooming cost nothing because only the frame corners are re-projected, and
#: the image stays glued to the basemap during the wheel-zoom preview.
#:
#: The bounds are the lon/lat envelope of the HRRR domain. Its Lambert edges bow,
#: so an axis-aligned box necessarily includes corners outside the model -- those
#: come back transparent.
COVERAGE_BOUNDS = (-134.5, -60.5, 20.5, 53.0)

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


def grib_url(run: datetime, fxx: int, source: str) -> str:
    kind = "wrfsfc" if source == SOURCE_SURFACE else "wrfprs"
    return (f"{BUCKET}/hrrr.{run:%Y%m%d}/conus/"
            f"hrrr.t{run:%H}z.{kind}f{int(fxx):02d}.grib2")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to the system trust store
        return ssl.create_default_context()


#: How many range requests may be in flight at once.
#:
#: A product's records are spread through a 135-400 MiB object, so they rarely
#: coalesce into one range: the 0-3 km lapse rate needs thirteen records across
#: two files. Those requests are independent and latency-bound, so issuing a few
#: together converts a serial chain of round trips into one. Four rather than
#: thirteen because the bucket is a shared public resource and the gain flattens
#: once the link is busy.
_RANGE_WORKERS = 4

_SESSION_LOCK = threading.Lock()
_SESSION = None


def _session():
    """Return a shared pooled HTTP session, or ``None`` to fall back to urllib.

    Connection reuse is the single biggest win in this module's IO: without it
    every range request pays a fresh TCP and TLS handshake to the same host, and
    a product needing a dozen records pays it a dozen times.
    """
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            session = requests.Session()
            retry = Retry(total=2, connect=2, read=2, backoff_factor=0.3,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset({"GET"}))
            adapter = HTTPAdapter(pool_connections=4,
                                  pool_maxsize=_RANGE_WORKERS * 2,
                                  max_retries=retry)
            session.mount("https://", adapter)
            session.headers.update({"User-Agent": "sharpmod",
                                    "Accept-Encoding": "identity"})
            _SESSION = session
        except Exception:  # noqa: BLE001 - urllib still works, just slower
            _SESSION = False
        return _SESSION


def _default_opener(url: str, timeout: float, limit: int,
                    byte_range: tuple[int, int] | None = None) -> bytes:
    """Fetch ``url``, optionally one byte range, capped at ``limit`` bytes."""
    headers = {}
    if byte_range is not None:
        headers["Range"] = "bytes=%d-%d" % byte_range

    session = _session()
    if session:
        try:
            response = session.get(url, headers=headers, timeout=timeout,
                                   stream=True)
        except Exception as error:  # noqa: BLE001 - requests' own exceptions
            raise HrrrFieldError(f"HRRR request failed: {error}") from error
        with response:
            if response.status_code in (403, 404):
                raise HrrrFieldUnavailable(
                    f"HRRR object is not published yet "
                    f"({response.status_code})")
            if response.status_code >= 400:
                raise HrrrFieldError(
                    f"HRRR request failed: HTTP {response.status_code}")
            payload = response.raw.read(limit + 1, decode_content=True)
        if len(payload) > limit:
            raise HrrrFieldError("HRRR response exceeded the byte budget")
        return payload

    request = urllib.request.Request(
        url, headers={"User-Agent": "sharpmod",
                      "Accept-Encoding": "identity", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_ssl_context()) as response:
            payload = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code in (403, 404):
            raise HrrrFieldUnavailable(
                f"HRRR object is not published yet ({error.code})") from error
        raise HrrrFieldError(f"HRRR request failed: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HrrrFieldError(f"HRRR request failed: {error}") from error
    if len(payload) > limit:
        raise HrrrFieldError("HRRR response exceeded the byte budget")
    return payload


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


_INVENTORY_LOCK = threading.Lock()
_INVENTORY_CACHE: dict[tuple, tuple[float, tuple[InventoryRow, ...]]] = {}


def parse_inventory(text: str) -> tuple[InventoryRow, ...]:
    """Parse a wgrib2 ``.idx`` listing.

    Rows are ``n:offset:d=YYYYMMDDHH:VAR:level:forecast:``. A record's length is
    the next record's offset minus its own; the final record runs to the end of
    the object, whose size the sidecar does not state, so it is left as zero and
    the caller requests an open-ended range for it.
    """
    rows: list[tuple[int, int, str, str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        rows.append((index, int(parts[1]), parts[3], parts[4], parts[5]))
    parsed: list[InventoryRow] = []
    for position, (index, offset, variable, level, forecast) in enumerate(rows):
        following = rows[position + 1][1] if position + 1 < len(rows) else 0
        length = max(0, following - offset) if following else 0
        parsed.append(InventoryRow(index, offset, length, variable, level,
                                   forecast))
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
    payload = call(url + ".idx", timeout or limit_timeout, 4 * 1024 * 1024)
    rows = parse_inventory(payload.decode("utf-8", errors="replace"))
    if not rows:
        raise HrrrFieldUnavailable("HRRR inventory was empty or unreadable")

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
        f"HRRR does not publish {spec.variable}:{spec.level}"
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


def decode_message(payload: bytes, eccodes=None) -> np.ndarray:
    """Decode one GRIB message into the full HRRR grid.

    Takes exactly one message. The caller slices it out of a downloaded span
    using the byte offset and length the inventory already gave us, which is
    both cheaper than letting a GRIB reader scan the buffer and, more usefully,
    means a decoded array is identified by *the row that asked for it*. Matching
    on eccodes' own level vocabulary instead would mean maintaining a second
    spelling of every level next to the ``.idx`` spelling the catalogue uses, and
    the two do not agree -- ``.idx`` says ``REFC:entire atmosphere`` where
    eccodes says ``refc`` at ``entireAtmosphere``.
    """
    api = eccodes or _load_eccodes()
    if not payload.startswith(b"GRIB"):
        raise HrrrFieldError("byte range did not start at a GRIB message")
    handle = api.codes_new_from_message(payload)
    if handle is None:
        raise HrrrFieldError("GRIB message could not be opened")
    try:
        ni = int(api.codes_get(handle, "Ni"))
        nj = int(api.codes_get(handle, "Nj"))
        values = np.asarray(api.codes_get_values(handle), dtype=np.float32)
        try:
            missing = float(api.codes_get(handle, "missingValue"))
        except Exception:  # noqa: BLE001 - not every message declares one
            missing = 9999.0
    finally:
        api.codes_release(handle)
    grid = values.reshape(nj, ni)
    if grid.shape != HRRR_SHAPE:
        raise HrrrFieldError(
            f"unexpected HRRR grid {grid.shape}, wanted {HRRR_SHAPE}")
    return np.where(grid == missing, MISSING, grid)


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
_INDEX_CACHE: dict[tuple, _IndexMap] = {}


def index_map(size: tuple[int, int],
              bounds: tuple[float, float, float, float]) -> _IndexMap:
    """Return the cached output-frame to HRRR-grid index map.

    This is the expensive part of a first render -- a projection of every output
    pixel -- and it depends only on the frame geometry, so it is computed once
    and then shared by every product, every run and every forecast hour.
    """
    key = (int(size[0]), int(size[1]), tuple(round(value, 6) for value in bounds))
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        from pyproj import CRS, Transformer
    except Exception as error:  # noqa: BLE001
        raise HrrrFieldError(
            "model fields need pyproj to place the model grid; install the "
            "'era5' extra to enable them") from error

    width, height = int(size[0]), int(size[1])
    lon0, lon1, lat0, lat1 = bounds
    # Pixel centres, rows running north to south so row 0 is the north edge --
    # the origin OverlayRaster documents.
    lons = lon0 + (np.arange(width, dtype=np.float64) + 0.5) * (lon1 - lon0) / width
    lats = lat1 - (np.arange(height, dtype=np.float64) + 0.5) * (lat1 - lat0) / height
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), CRS.from_proj4(HRRR_PROJ4), always_xy=True)
    xs, ys = transformer.transform(lon_grid.ravel(), lat_grid.ravel())
    col = np.rint((np.asarray(xs) - HRRR_X0) / HRRR_SPACING).astype(np.int32)
    row = np.rint((np.asarray(ys) - HRRR_Y0) / HRRR_SPACING).astype(np.int32)
    inside = ((col >= 0) & (col < HRRR_SHAPE[1])
              & (row >= 0) & (row < HRRR_SHAPE[0]))
    flat = np.where(inside, row.astype(np.int64) * HRRR_SHAPE[1] + col,
                    0).astype(np.int32)

    resolved = _IndexMap(width, height, tuple(bounds), flat, inside)
    with _INDEX_LOCK:
        _INDEX_CACHE[key] = resolved
    return resolved


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


def build_lut(palette: Palette) -> tuple[np.ndarray, float, float]:
    """Return ``(lut, low, high)`` for a palette.

    A banded palette fills each class with its own flat colour, so the map shows
    the same discrete classes the scale is defined in. A continuous one
    interpolates in RGB between stops.
    """
    low = float(palette.minimum)
    high = float(palette.maximum)
    axis = np.linspace(low, high, _LUT_SIZE)
    lut = np.zeros((_LUT_SIZE, 4), dtype=np.uint8)
    positions = np.array([value for value, _colour in palette.stops],
                         dtype=np.float64)
    colours = np.array([_hex_to_rgb(colour) for _value, colour in palette.stops],
                       dtype=np.float64)

    if palette.stepped:
        # Each sample takes the colour of the highest stop at or below it.
        slot = np.searchsorted(positions, axis, side="right") - 1
        slot = np.clip(slot, 0, len(positions) - 1)
        lut[:, :3] = colours[slot].astype(np.uint8)
    else:
        for channel in range(3):
            lut[:, channel] = np.interp(
                axis, positions, colours[:, channel]).clip(0, 255).astype(np.uint8)
    lut[:, 3] = 255
    return lut, low, high


def colourize(field: np.ndarray, palette: Palette) -> np.ndarray:
    """Turn a field in display units into an RGBA image.

    Values outside the palette's drawn range, and anything non-finite, are fully
    transparent: a field overlay has to leave the basemap and the SPC risk areas
    beneath it visible wherever it has nothing to say. ``floor`` suppresses the
    low end and ``ceiling`` the high end, which is what a negative-signed field
    like convective inhibition needs -- its uninteresting values are the ones
    near zero, at the top of its scale.
    """
    lut, low, high = build_lut(palette)
    span = high - low if high > low else 1.0
    normalized = (field - low) / span
    slot = np.clip(normalized, 0.0, 1.0) * (_LUT_SIZE - 1)
    slot = np.nan_to_num(slot, nan=0.0, posinf=_LUT_SIZE - 1, neginf=0.0)
    rgba = lut[slot.astype(np.uint16)]

    hidden = ~np.isfinite(field)
    hidden = hidden | (field < (low if palette.floor is None
                                else palette.floor))
    if palette.ceiling is not None:
        hidden = hidden | (field > palette.ceiling)
    rgba = rgba.copy()
    rgba[..., 3] = np.where(hidden, 0, 255)
    return rgba


def draw_contours(rgba: np.ndarray, field: np.ndarray, interval: float,
                  colour: str, width: float = 1.2) -> None:
    """Stamp isopleths of ``field`` into ``rgba`` in place.

    Marching-squares would give smoother lines, but a contour *band* is what
    reads correctly here and it is far cheaper: a pixel is on a contour when the
    field's value divided by the interval crosses an integer between it and its
    neighbour. That yields closed, connected lines one to two pixels wide with
    two array comparisons and no polygon assembly.
    """
    if not np.isfinite(interval) or interval <= 0:
        return
    with np.errstate(invalid="ignore"):
        level = np.floor(field / float(interval))
    valid = np.isfinite(field)
    edge = np.zeros(field.shape, dtype=bool)
    # A crossing needs a real value on *both* sides. Testing only the near cell
    # would draw the domain outline as a contour: beyond the model edge the field
    # is NaN, every comparison against it is unequal, and the last valid column
    # would light up all the way round the border.
    horizontal = (level[:, :-1] != level[:, 1:]) & valid[:, :-1] & valid[:, 1:]
    vertical = (level[:-1, :] != level[1:, :]) & valid[:-1, :] & valid[1:, :]
    edge[:, :-1] |= horizontal
    edge[:-1, :] |= vertical
    if width >= 2.0:
        thick = edge.copy()
        thick[1:, :] |= edge[:-1, :]
        thick[:, 1:] |= edge[:, :-1]
        edge = thick
    red, green, blue = _hex_to_rgb(colour)
    rgba[edge, 0] = red
    rgba[edge, 1] = green
    rgba[edge, 2] = blue
    rgba[edge, 3] = 255


def encode_png(rgba: np.ndarray) -> bytes:
    """Encode RGBA to PNG, preferring Pillow and falling back to Qt.

    ``compress_level=1`` is deliberate: these frames are transient, and a
    smaller file is not worth the extra encode time on a worker the user is
    waiting on. The result is checked against the overlay contract's byte cap
    here, where the size is still adjustable, rather than at construction.
    """
    payload = None
    try:
        from PIL import Image
        buffer = io.BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(
            buffer, format="PNG", optimize=False, compress_level=1)
        payload = buffer.getvalue()
    except Exception:  # noqa: BLE001 - Qt can do it too
        payload = None
    if payload is None:
        try:
            from qtpy.QtCore import QBuffer, QByteArray
            from qtpy.QtGui import QImage
            height, width = rgba.shape[:2]
            contiguous = np.ascontiguousarray(rgba)
            image = QImage(contiguous.data, width, height, 4 * width,
                           QImage.Format_RGBA8888).copy()
            store = QByteArray()
            buffer = QBuffer(store)
            buffer.open(QBuffer.WriteOnly)
            image.save(buffer, "PNG")
            payload = bytes(store)
        except Exception as error:  # noqa: BLE001
            raise HrrrFieldError(f"could not encode the field image: {error}")
    if not payload:
        raise HrrrFieldError("field image encoding produced no bytes")
    if len(payload) > MAX_RASTER_BYTES:
        raise HrrrFieldError(
            f"field image is {len(payload)} bytes, over the "
            f"{MAX_RASTER_BYTES}-byte overlay limit")
    return payload


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


def _disk_read(path: Path) -> bytes | None:
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        return None
    return None


def _disk_write(path: Path, payload: bytes) -> None:
    """Write a frame atomically, and never fail the render if we cannot."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise
    except OSError:
        _LOGGER.debug("hrrr_field.disk_write_failed path=%s", path, exc_info=True)


# --------------------------------------------------------------------------- #
# The public entry point
# --------------------------------------------------------------------------- #

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, OverlayRaster | None]] = {}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    with _INVENTORY_LOCK:
        _INVENTORY_CACHE.clear()


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


def _download_fields(product: HrrrProduct, run: datetime, fxx: int, *,
                     opener, timeout: float, should_cancel) -> dict[str, np.ndarray]:
    """Fetch and decode every record the product needs, grouped per source file.

    Records are located in the inventory, coalesced into as few HTTP ranges as
    possible, then sliced back apart by offset so each message is decoded as
    itself. A product spanning both output files makes one inventory request and
    one range request per file in the common case.
    """
    eccodes = _load_eccodes()
    decoded: dict[str, np.ndarray] = {}

    # Plan every range across both files first, so they can all go out together
    # rather than one file's requests waiting on the other's.
    jobs: list[tuple[str, int, int, tuple[InventoryRow, ...], dict[int, str]]] = []
    for source in (SOURCE_SURFACE, SOURCE_PRESSURE):
        specs = [spec for spec in product.all_fields if spec.source == source]
        if not specs:
            continue
        if should_cancel and should_cancel():
            return {}
        url = grib_url(run, fxx, source)
        rows = fetch_inventory(url, opener=opener, timeout=timeout)
        located = [(locate(rows, spec), spec.name) for spec in specs]
        by_offset = {row.offset: name for row, name in located}
        for start, end, members in _merge_ranges([row for row, _ in located]):
            stop = (start + _TAIL_BUDGET) if not end else end
            jobs.append((url, start, stop, tuple(members), by_offset))

    if should_cancel and should_cancel():
        return {}

    def fetch(job):
        url, start, stop, _members, _names = job
        return opener(url, timeout, stop - start + 4096, (start, stop))

    payloads: list[bytes | None] = [None] * len(jobs)
    if len(jobs) == 1:
        payloads[0] = fetch(jobs[0])
    else:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(_RANGE_WORKERS, len(jobs))
        with ThreadPoolExecutor(max_workers=workers,
                               thread_name_prefix="hrrr-range") as pool:
            futures = {pool.submit(fetch, job): position
                       for position, job in enumerate(jobs)}
            for future in futures:
                position = futures[future]
                payloads[position] = future.result()

    if should_cancel and should_cancel():
        return {}

    # Decoding stays serial: it is CPU-bound rather than latency-bound, and one
    # eccodes handle at a time keeps this independent of the C library's
    # threading guarantees.
    for (url, start, _stop, members, by_offset), payload in zip(jobs, payloads):
        if payload is None:
            continue
        for row in members:
            name = by_offset.get(row.offset)
            if name is None:
                continue
            begin = row.offset - start
            finish = (begin + row.length) if row.length else len(payload)
            if begin < 0 or begin >= len(payload):
                continue
            decoded[name] = decode_message(payload[begin:finish], eccodes)

    missing = [spec.name for spec in product.all_fields
               if spec.name not in decoded]
    if missing:
        raise HrrrFieldUnavailable(
            "HRRR did not return " + ", ".join(sorted(missing)))
    return decoded


def render_field(product: HrrrProduct, decoded: dict[str, np.ndarray], *,
                 size: tuple[int, int], bounds=COVERAGE_BOUNDS) -> bytes:
    """Derive, reproject, colour and encode a product from decoded records."""
    field = np.asarray(product.derive(decoded), dtype=np.float32)
    if field.shape != HRRR_SHAPE:
        raise HrrrFieldError(
            f"{product.key} derived a {field.shape} field, wanted {HRRR_SHAPE}")
    mapping = index_map(size, bounds)
    projected = reproject(field, mapping)
    rgba = colourize(projected, product.palette)

    if product.contour is not None:
        spec = product.contour
        raw = decoded.get(spec.name)
        if raw is not None:
            values = raw if spec.convert is None else spec.convert(raw)
            draw_contours(rgba, reproject(np.asarray(values, dtype=np.float32),
                                          mapping),
                          spec.interval, spec.colour, spec.width)
    return encode_png(rgba)


def fetch_field(product_key: str | None, *, valid_time: datetime | None = None,
                run: datetime | None = None, fxx: int | None = None,
                size=DEFAULT_FRAME_SIZE, opacity: float = 0.75,
                now: datetime | None = None, opener=None,
                should_cancel=None) -> OverlayRaster | None:
    """Produce one product as a map overlay.

    The forecast is chosen one of two ways. Passing ``run`` pins the request to
    that exact cycle and ``fxx``, which is what the Forecast Model tab does so
    the field matches the run the sounding will come from. Passing only
    ``valid_time`` asks for the freshest run that reaches that hour, which is
    what the observed tab wants: there is no cycle selection there, only a
    moment. Either way the returned raster's subtitle states the run, the
    forecast hour, and the valid time, so the map cannot imply a currency it
    does not have.

    Returns ``None`` only when ``should_cancel`` asked us to stop; every real
    failure raises :class:`HrrrFieldError`. That split is what lets the calling
    worker tell "the user moved on" apart from "this product is broken".
    """
    product = get_product(product_key)
    frame = _clamp_size(size)
    if run is not None:
        run, fxx = pin_request(run, fxx)
    else:
        run, fxx = resolve_request(valid_time, now=now)
    timeout, _limit = _remote_limits()
    call = opener or _default_opener

    cache_key = (product.key, run.isoformat(), fxx, frame)
    moment = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        age = moment - cached[0]
        raster = cached[1]
        if raster is None:
            if age < FAILURE_CACHE_TTL_S:
                raise HrrrFieldError(
                    f"{product.label} was not available for "
                    f"{run:%Y-%m-%d %HZ} F{fxx:02d}")
        elif age < FRAME_CACHE_TTL_S:
            return raster if raster.opacity == opacity \
                else raster.at_opacity(opacity)

    disk = _disk_path(product.key, run, fxx, frame)
    payload = _disk_read(disk)
    from_disk = payload is not None

    if payload is None:
        if should_cancel and should_cancel():
            return None
        try:
            decoded = _download_fields(product, run, fxx, opener=call,
                                       timeout=timeout,
                                       should_cancel=should_cancel)
        except HrrrFieldError:
            with _CACHE_LOCK:
                _CACHE[cache_key] = (moment, None)
            raise
        if not decoded:
            return None
        if should_cancel and should_cancel():
            return None
        payload = render_field(product, decoded, size=frame)

    valid = run + timedelta(hours=int(fxx))
    subtitle = (f"{run:%d %b %HZ} run \u00b7 F{int(fxx):02d} \u00b7 "
                f"valid {valid:%d %b %HZ}")
    try:
        raster = OverlayRaster(
            key=OVERLAY_KEY,
            title=f"HRRR {product.label}",
            image_bytes=payload,
            bounds=COVERAGE_BOUNDS,
            subtitle=subtitle,
            short_name=product.key,
            valid_time=valid,
            retrieved_at=datetime.now(timezone.utc),
            update_interval_s=3600.0,
            opacity=opacity,
            source_url=grib_url(run, fxx, product.sources[0]),
            attribution=ATTRIBUTION,
        )
    except ValueError as error:
        raise HrrrFieldError(f"field image rejected: {error}") from error

    if not from_disk:
        # Only a finished run is immutable enough to keep.
        if run < latest_run(now):
            _disk_write(disk, payload)
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
            _CACHE.pop(oldest, None)
        _CACHE[cache_key] = (time.monotonic(), raster)
    return raster
