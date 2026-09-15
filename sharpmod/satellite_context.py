"""Time-addressed NOAA GOES imagery for picker-map context.

The adapter reads the documented NOAA Open Data Dissemination (NODD) ABI
Level-2 Cloud and Moisture Imagery product directly from its public S3 bucket.
It never treats a ``latest`` picture as historical data: candidates are parsed
from their embedded scan-start time and the selected time is retained on the
returned overlay.

The source is on the GOES fixed grid.  :func:`render_goes_frame` projects a
requested lon/lat window into that grid and samples the native data into a
plate-carree PNG.  That makes the result compatible with the existing map
raster architecture without pretending the original pixels were geographic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
import math
import re
from typing import Callable
from urllib.parse import quote
from xml.etree import ElementTree

import numpy as np

from sharpmod.map_overlays import OverlayRaster


GOES_OVERLAY_KEY = "goes_context"
GOES_BUCKETS = {
    "G18": "https://noaa-goes18.s3.amazonaws.com",
    "G19": "https://noaa-goes19.s3.amazonaws.com",
}
CHANNELS = {
    "visible": (2, "Visible (0.64 µm)", 0.5),
    "infrared": (13, "Clean IR (10.3 µm)", 2.0),
}
ATTRIBUTION = "NOAA/NESDIS GOES · NOAA Open Data Dissemination"
MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_OUTPUT_PIXELS = 1_400_000
DEFAULT_TIME_TOLERANCE = timedelta(minutes=20)

_SCAN_RE = re.compile(
    r"_s(?P<year>\d{4})(?P<day>\d{3})(?P<hour>\d{2})"
    r"(?P<minute>\d{2})(?P<second>\d{2})\d?"
)
_CHANNEL_RE = re.compile(r"CMIPC-M\dC(?P<channel>\d{2})_")


class SatelliteContextError(RuntimeError):
    """A provider, format, timing, or coverage error safe to show in the UI."""


class SatelliteCancelled(SatelliteContextError):
    """The user superseded a satellite request."""


@dataclass(frozen=True)
class SatelliteCandidate:
    satellite: str
    channel: str
    scan_start: datetime
    key: str
    url: str
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.satellite not in GOES_BUCKETS:
            raise SatelliteContextError(f"unsupported GOES spacecraft {self.satellite!r}")
        if self.channel not in CHANNELS:
            raise SatelliteContextError(f"unsupported GOES channel {self.channel!r}")
        when = _utc(self.scan_start)
        if when is None:
            raise SatelliteContextError("GOES candidate has no UTC scan time")
        object.__setattr__(self, "scan_start", when)


@dataclass(frozen=True)
class SatelliteOverlay:
    """Rendered map overlay plus the scientific selection information."""

    raster: OverlayRaster
    requested_time: datetime
    selected_time: datetime
    satellite: str
    channel: str
    coverage_fraction: float
    native_resolution_km: float

    @property
    def time_offset_seconds(self) -> float:
        return (self.selected_time - self.requested_time).total_seconds()


def _utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        raise SatelliteContextError("satellite selection time must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else bool(cancel)


def choose_satellite(bounds) -> str:
    """Choose the operational US spacecraft from the view centre.

    GOES-18 is the west spacecraft and GOES-19 is east.  The overlap is broad;
    -114 degrees is merely a deterministic preference line, not a claimed
    coverage edge.  Actual coverage is established from the fixed-grid data.
    """

    try:
        lon0, lon1, _lat0, _lat1 = (float(item) for item in bounds)
    except (TypeError, ValueError) as exc:
        raise SatelliteContextError("map bounds must contain four numbers") from exc
    centre = (lon0 + lon1) / 2.0
    return "G18" if centre < -114.0 else "G19"


def _response_bytes(response, *, limit: int, cancel=None) -> bytes:
    if isinstance(response, (bytes, bytearray)):
        payload = bytes(response)
    elif hasattr(response, "iter_content"):
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if _cancelled(cancel):
                raise SatelliteCancelled("satellite request cancelled")
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise SatelliteContextError("GOES response exceeds the safety limit")
            chunks.append(bytes(chunk))
        payload = b"".join(chunks)
    else:
        content = getattr(response, "content", None)
        if content is None:
            reader = getattr(response, "read", None)
            content = reader() if callable(reader) else None
        if content is None:
            raise SatelliteContextError("GOES provider returned no readable body")
        payload = bytes(content)
    if len(payload) > limit:
        raise SatelliteContextError("GOES response exceeds the safety limit")
    return payload


def _open(url: str, *, timeout: float, opener=None, limit: int, cancel=None) -> bytes:
    if _cancelled(cancel):
        raise SatelliteCancelled("satellite request cancelled")
    if opener is None:
        import requests

        response = requests.get(
            url,
            headers={"User-Agent": "SHARPpy-Reimagined/1.2"},
            timeout=timeout,
            stream=True,
        )
    else:
        try:
            response = opener(url, timeout=timeout)
        except TypeError:
            response = opener(url)
    return _response_bytes(response, limit=limit, cancel=cancel)


def parse_listing(
    payload: bytes,
    *,
    satellite: str,
    channel: str,
) -> tuple[SatelliteCandidate, ...]:
    """Parse one S3 ListObjectsV2 document into time-addressed candidates."""

    if satellite not in GOES_BUCKETS or channel not in CHANNELS:
        raise SatelliteContextError("unknown GOES satellite or channel")
    wanted = CHANNELS[channel][0]
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise SatelliteContextError(f"GOES listing is not valid XML: {exc}") from exc
    candidates = []
    for content in root.iter():
        if content.tag.rsplit("}", 1)[-1] != "Contents":
            continue
        values = {
            child.tag.rsplit("}", 1)[-1]: child.text or "" for child in content
        }
        key = values.get("Key", "")
        channel_match = _CHANNEL_RE.search(key)
        scan_match = _SCAN_RE.search(key)
        if not channel_match or int(channel_match.group("channel")) != wanted:
            continue
        if not scan_match or f"_{satellite}_" not in key or not key.endswith(".nc"):
            continue
        parts = {name: int(value) for name, value in scan_match.groupdict().items()}
        try:
            scan = datetime(parts["year"], 1, 1, tzinfo=timezone.utc) + timedelta(
                days=parts["day"] - 1,
                hours=parts["hour"],
                minutes=parts["minute"],
                seconds=parts["second"],
            )
        except ValueError:
            continue
        try:
            size = int(values.get("Size", ""))
        except ValueError:
            size = None
        candidates.append(
            SatelliteCandidate(
                satellite,
                channel,
                scan,
                key,
                f"{GOES_BUCKETS[satellite]}/{quote(key)}",
                size,
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.scan_start))


def list_candidates(
    when: datetime,
    *,
    satellite: str,
    channel: str,
    timeout: float = 15.0,
    opener=None,
    cancel=None,
) -> tuple[SatelliteCandidate, ...]:
    """List scan candidates in the requested hour and its immediate neighbours."""

    selected = _utc(when)
    if selected is None:
        raise SatelliteContextError("a UTC analysis time is required")
    if satellite not in GOES_BUCKETS:
        raise SatelliteContextError(f"unsupported GOES spacecraft {satellite!r}")
    if channel not in CHANNELS:
        raise SatelliteContextError(f"unsupported imagery choice {channel!r}")
    found = {}
    base = selected.replace(minute=0, second=0, microsecond=0)
    for offset in (-1, 0, 1):
        hour = base + timedelta(hours=offset)
        prefix = (
            f"ABI-L2-CMIPC/{hour.year}/{hour.timetuple().tm_yday:03d}/"
            f"{hour.hour:02d}/OR_ABI-L2-CMIPC-M"
        )
        url = f"{GOES_BUCKETS[satellite]}/?list-type=2&prefix={quote(prefix)}"
        payload = _open(
            url,
            timeout=timeout,
            opener=opener,
            limit=4 * 1024 * 1024,
            cancel=cancel,
        )
        for candidate in parse_listing(
            payload, satellite=satellite, channel=channel
        ):
            found[candidate.key] = candidate
    return tuple(sorted(found.values(), key=lambda item: item.scan_start))


def select_candidate(
    candidates,
    when: datetime,
    *,
    tolerance: timedelta = DEFAULT_TIME_TOLERANCE,
) -> SatelliteCandidate:
    """Select the nearest actual scan, refusing a silent time substitution."""

    requested = _utc(when)
    if requested is None:
        raise SatelliteContextError("a UTC analysis time is required")
    candidates = tuple(candidates)
    if not candidates:
        raise SatelliteContextError("no GOES frame was published near this time")
    winner = min(
        candidates,
        key=lambda item: (abs((item.scan_start - requested).total_seconds()), item.scan_start),
    )
    if abs(winner.scan_start - requested) > tolerance:
        delta = abs(winner.scan_start - requested)
        raise SatelliteContextError(
            f"nearest GOES frame is {delta.total_seconds() / 60.0:.0f} minutes away; "
            f"the allowed mismatch is {tolerance.total_seconds() / 60.0:.0f} minutes"
        )
    return winner


def _nearest_indices(axis, values):
    axis = np.asarray(axis, dtype=float)
    values = np.asarray(values, dtype=float)
    if axis.ndim != 1 or axis.size < 2 or not np.all(np.isfinite(axis)):
        raise SatelliteContextError("GOES fixed-grid coordinate axis is malformed")
    ascending = axis[-1] > axis[0]
    ordered = axis if ascending else -axis
    wanted = values if ascending else -values
    upper = np.searchsorted(ordered, wanted, side="left")
    upper = np.clip(upper, 1, axis.size - 1)
    lower = upper - 1
    choose_upper = np.abs(ordered[upper] - wanted) < np.abs(
        ordered[lower] - wanted
    )
    indices = np.where(choose_upper, upper, lower)
    inside = np.isfinite(values) & (wanted >= ordered[0]) & (wanted <= ordered[-1])
    return indices, inside


def _render_dimensions(bounds, resolution_km: float, max_pixels: int):
    lon0, lon1, lat0, lat1 = bounds
    latitude = math.radians((lat0 + lat1) / 2.0)
    width = max(2, int(abs(lon1 - lon0) * 111.2 * max(0.15, math.cos(latitude)) / resolution_km))
    height = max(2, int(abs(lat1 - lat0) * 111.2 / resolution_km))
    # Maps do not benefit from a raster much larger than the screen, but narrow
    # views retain native-scale pixels until they reach this bounded ceiling.
    scale = min(1.0, math.sqrt(max_pixels / float(width * height)), 1024.0 / width, 768.0 / height)
    return max(2, int(width * scale)), max(2, int(height * scale))


def _colour_visible(values):
    brightness = np.sqrt(np.clip(values, 0.0, 1.2) / 1.2)
    grey = np.asarray(np.rint(brightness * 255.0), dtype=np.uint8)
    return np.stack((grey, grey, grey), axis=-1)


def _colour_infrared(values):
    # A compact perceptually ordered cold-cloud palette. Temperature remains in
    # the source units (kelvin); only the presentation is colour mapped.
    normalized = np.clip((310.0 - values) / 120.0, 0.0, 1.0)
    points = np.asarray((0.0, 0.35, 0.58, 0.78, 1.0))
    red = np.interp(normalized, points, (15, 95, 230, 250, 255))
    green = np.interp(normalized, points, (20, 110, 230, 120, 255))
    blue = np.interp(normalized, points, (30, 175, 255, 80, 255))
    return np.asarray(np.rint(np.stack((red, green, blue), axis=-1)), dtype=np.uint8)


def render_goes_frame(
    payload: bytes,
    *,
    bounds,
    channel: str,
    max_output_pixels: int = MAX_OUTPUT_PIXELS,
) -> tuple[bytes, float, float]:
    """Geolocate a GOES netCDF frame into a plate-carree PNG.

    Returns ``(png_bytes, valid_pixel_fraction, nominal_resolution_km)``.
    Pixels outside the satellite scan are transparent and therefore cannot
    masquerade as a valid frame over an uncovered region.
    """

    if channel not in CHANNELS:
        raise SatelliteContextError(f"unsupported imagery choice {channel!r}")
    try:
        lon0, lon1, lat0, lat1 = (float(item) for item in bounds)
    except (TypeError, ValueError) as exc:
        raise SatelliteContextError("map bounds must contain four numbers") from exc
    if not (-180.0 <= lon0 < lon1 <= 180.0 and -90.0 <= lat0 < lat1 <= 90.0):
        raise SatelliteContextError("map bounds are outside ordered lon/lat limits")
    if not payload or len(payload) > MAX_SOURCE_BYTES:
        raise SatelliteContextError("GOES source payload is empty or too large")
    try:
        from netCDF4 import Dataset
        from PIL import Image
        from pyproj import Proj
    except ImportError as exc:  # pragma: no cover - installation contract
        raise SatelliteContextError(
            "GOES imagery requires netCDF4, pyproj, and Pillow"
        ) from exc

    try:
        dataset = Dataset("goes-memory.nc", mode="r", memory=bytes(payload))
    except Exception as exc:
        raise SatelliteContextError(f"GOES netCDF could not be opened: {exc}") from exc
    try:
        if not {"x", "y", "CMI", "goes_imager_projection"}.issubset(
            dataset.variables
        ):
            raise SatelliteContextError("GOES netCDF lacks fixed-grid imagery fields")
        x_axis = np.asarray(dataset.variables["x"][:], dtype=float)
        y_axis = np.asarray(dataset.variables["y"][:], dtype=float)
        raw = np.ma.asarray(dataset.variables["CMI"][:], dtype=float)
        data = np.asarray(raw.filled(np.nan), dtype=float)
        if data.shape != (y_axis.size, x_axis.size):
            raise SatelliteContextError("GOES image and coordinate dimensions disagree")
        quality = None
        if "DQF" in dataset.variables:
            quality = np.ma.asarray(dataset.variables["DQF"][:]).filled(255)
        projection = dataset.variables["goes_imager_projection"]
        height = float(projection.perspective_point_height)
        lon_origin = float(projection.longitude_of_projection_origin)
        sweep = str(getattr(projection, "sweep_angle_axis", "x"))
        semi_major = float(getattr(projection, "semi_major_axis", 6378137.0))
        semi_minor = float(getattr(projection, "semi_minor_axis", 6356752.31414))
        source_resolution = float(CHANNELS[channel][2])
        width, height_px = _render_dimensions(
            (lon0, lon1, lat0, lat1), source_resolution, int(max_output_pixels)
        )
        lons = np.linspace(lon0, lon1, width, dtype=float)
        lats = np.linspace(lat1, lat0, height_px, dtype=float)
        longitude, latitude = np.meshgrid(lons, lats)
        geos = Proj(
            proj="geos",
            h=height,
            lon_0=lon_origin,
            sweep=sweep,
            a=semi_major,
            b=semi_minor,
            units="m",
        )
        gx, gy = geos(longitude, latitude)
        xi, x_inside = _nearest_indices(x_axis, gx / height)
        yi, y_inside = _nearest_indices(y_axis, gy / height)
        inside = x_inside & y_inside & np.isfinite(gx) & np.isfinite(gy)
        sampled = data[yi, xi]
        valid = inside & np.isfinite(sampled)
        if quality is not None and quality.shape == data.shape:
            valid &= np.asarray(quality[yi, xi], dtype=float) <= 1.0
        coverage = float(np.count_nonzero(valid)) / float(valid.size)
        if coverage < 0.01:
            raise SatelliteContextError(
                "the selected GOES sector does not cover this map view"
            )
        colours = (
            _colour_visible(sampled)
            if channel == "visible"
            else _colour_infrared(sampled)
        )
        rgba = np.empty((height_px, width, 4), dtype=np.uint8)
        rgba[..., :3] = colours
        rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
        output = io.BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(output, format="PNG", optimize=True)
        return output.getvalue(), coverage, source_resolution
    finally:
        dataset.close()


def fetch_satellite_overlay(
    when: datetime,
    *,
    bounds,
    channel: str = "infrared",
    satellite: str | None = None,
    tolerance: timedelta = DEFAULT_TIME_TOLERANCE,
    timeout: float = 30.0,
    opener=None,
    cancel=None,
) -> SatelliteOverlay:
    """Find, download, geolocate, and label one relevant GOES frame."""

    requested = _utc(when)
    if requested is None:
        raise SatelliteContextError("a UTC analysis time is required")
    satellite = satellite or choose_satellite(bounds)
    candidates = list_candidates(
        requested,
        satellite=satellite,
        channel=channel,
        timeout=min(timeout, 20.0),
        opener=opener,
        cancel=cancel,
    )
    candidate = select_candidate(candidates, requested, tolerance=tolerance)
    if candidate.size_bytes is not None and candidate.size_bytes > MAX_SOURCE_BYTES:
        raise SatelliteContextError("GOES source object exceeds the safety limit")
    payload = _open(
        candidate.url,
        timeout=timeout,
        opener=opener,
        limit=MAX_SOURCE_BYTES,
        cancel=cancel,
    )
    if _cancelled(cancel):
        raise SatelliteCancelled("satellite request cancelled")
    png, coverage, resolution = render_goes_frame(
        payload, bounds=bounds, channel=channel
    )
    delta_min = (candidate.scan_start - requested).total_seconds() / 60.0
    channel_title = CHANNELS[channel][1]
    subtitle = (
        f"{candidate.satellite} {channel_title} · actual "
        f"{candidate.scan_start:%Y-%m-%d %H:%M:%SZ} · "
        f"{delta_min:+.0f} min from requested · {coverage:.0%} view coverage"
    )
    raster = OverlayRaster(
        key=GOES_OVERLAY_KEY,
        title="GOES satellite",
        image_bytes=png,
        bounds=tuple(float(item) for item in bounds),
        subtitle=subtitle,
        short_name=channel,
        valid_time=candidate.scan_start,
        retrieved_at=datetime.now(timezone.utc),
        update_interval_s=300.0,
        opacity=0.72,
        source_url=candidate.url,
        attribution=ATTRIBUTION,
    )
    return SatelliteOverlay(
        raster,
        requested,
        candidate.scan_start,
        candidate.satellite,
        channel,
        coverage,
        resolution,
    )


__all__ = [
    "ATTRIBUTION",
    "CHANNELS",
    "DEFAULT_TIME_TOLERANCE",
    "GOES_BUCKETS",
    "GOES_OVERLAY_KEY",
    "SatelliteCandidate",
    "SatelliteCancelled",
    "SatelliteContextError",
    "SatelliteOverlay",
    "choose_satellite",
    "fetch_satellite_overlay",
    "list_candidates",
    "parse_listing",
    "render_goes_frame",
    "select_candidate",
]
