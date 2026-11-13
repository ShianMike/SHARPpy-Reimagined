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
import math
import re
from urllib.parse import quote

from sharpmod.maps.map_overlays import OverlayRaster


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
MAX_OUTPUT_PIXELS = 4_000_000
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


def _open(url: str, *, timeout: float, opener=None, limit: int, cancel=None) -> bytes:
    import sharpmod_rs

    return sharpmod_rs.fetch_satellite_bytes(url, timeout, limit, opener, cancel)


def parse_listing(
    payload: bytes,
    *,
    satellite: str,
    channel: str,
) -> tuple[SatelliteCandidate, ...]:
    """Parse one S3 ListObjectsV2 document into time-addressed candidates."""

    import sharpmod_rs

    rows = sharpmod_rs.goes_parse_listing(bytes(payload), satellite, channel)
    return tuple(
        SatelliteCandidate(
            sat_name,
            ch_name,
            datetime.fromtimestamp(scan_unix, tz=timezone.utc),
            key,
            url,
            size_bytes,
        )
        for sat_name, ch_name, scan_unix, key, url, size_bytes in rows
    )


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


def _render_dimensions(bounds, resolution_km: float, max_pixels: int):
    lon0, lon1, lat0, lat1 = bounds
    latitude = math.radians((lat0 + lat1) / 2.0)
    width = max(2, int(abs(lon1 - lon0) * 111.2 * max(0.15, math.cos(latitude)) / resolution_km))
    height = max(2, int(abs(lat1 - lat0) * 111.2 / resolution_km))
    # Retain source detail when zoomed, within a bounded shared texture budget.
    scale = min(1.0, math.sqrt(max_pixels / float(width * height)))
    return max(2, int(width * scale)), max(2, int(height * scale))


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

    import sharpmod_rs

    try:
        lon0, lon1, lat0, lat1 = (float(item) for item in bounds)
    except (TypeError, ValueError) as exc:
        raise SatelliteContextError("map bounds must contain four numbers") from exc
    return sharpmod_rs.goes_render_frame(
        bytes(payload),
        (lon0, lon1, lat0, lat1),
        channel,
        max(1, int(max_output_pixels)),
    )


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
    if (
        opener is None
        and cancel is None
        and satellite is None
        and tolerance == DEFAULT_TIME_TOLERANCE
        and timeout == 30.0
    ):
        try:
            lon0, lon1, lat0, lat1 = (float(item) for item in bounds)
        except (TypeError, ValueError) as exc:
            raise SatelliteContextError("map bounds must contain four numbers") from exc
        import sharpmod_rs

        (
            png,
            sat_name,
            _req_iso,
            retrieved_iso,
            valid_iso,
            coverage,
            resolution,
            attribution,
            source_url,
        ) = sharpmod_rs.goes_fetch_frame(
            requested.isoformat().replace("+00:00", "Z"),
            (lon0, lon1, lat0, lat1),
            channel,
        )
        selected_time = datetime.fromisoformat(valid_iso.replace("Z", "+00:00"))
        retrieved_at = datetime.fromisoformat(retrieved_iso.replace("Z", "+00:00"))
        delta_min = (selected_time - requested).total_seconds() / 60.0
        channel_title = CHANNELS[channel][1]
        subtitle = (
            f"{sat_name} {channel_title} · actual "
            f"{selected_time:%Y-%m-%d %H:%M:%SZ} · "
            f"{delta_min:+.0f} min from requested · {coverage:.0%} view coverage"
        )
        raster = OverlayRaster(
            key=GOES_OVERLAY_KEY,
            title="GOES satellite",
            image_bytes=png,
            bounds=(lon0, lon1, lat0, lat1),
            subtitle=subtitle,
            short_name=channel,
            valid_time=selected_time,
            retrieved_at=retrieved_at,
            update_interval_s=300.0,
            opacity=0.72,
            source_url=source_url,
            attribution=attribution,
        )
        return SatelliteOverlay(
            raster,
            requested,
            selected_time,
            sat_name,
            channel,
            coverage,
            resolution,
        )
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
