"""Numeric NEXRAD Level III reflectivity and velocity at the requested elevation."""

from __future__ import annotations

import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.error import HTTPError

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from sharpmod.maps.map_overlays import OverlayRaster
from sharpmod.providers.radar_site import (
    OVERLAY_KEY,
    RadarScanMissing,
    RadarSiteError,
    _CACHE,
    _CACHE_LOCK,
    _CACHE_MAX_ENTRIES,
    _default_opener,
    _remote_limits,
    site_by_id,
)

BASE = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar"
# https://www.weather.gov/media/tg/rpccds_radar_products.pdf (VCP-dependent cut groups).
TILTS = {
    "x": (-0.5, -0.1),
    "y": (-0.1, 0.25),
    "z": (0.25, 0.45),
    "0": (0.45, 0.85),
    "a": (0.85, 1.15),
    "1": (1.15, 1.65),
    "b": (1.65, 2.15),
    "2": (2.15, 2.75),
    "3": (2.75, 3.75),
}
MPH_PER_MPS = 3600 / 1609.344
# Sampled at 5 mph from the user's reference; negative is toward the radar.
STOPS = (
    (-200, "#ffdadc"),
    (-195, "#ffc9d8"),
    (-190, "#ffb9d5"),
    (-185, "#ffa9d2"),
    (-180, "#ff98ce"),
    (-175, "#ff87cb"),
    (-170, "#ff77c8"),
    (-165, "#ff66c4"),
    (-160, "#ff55c1"),
    (-155, "#ff44be"),
    (-150, "#ff35bb"),
    (-145, "#ff23b7"),
    (-140, "#ff14b3"),
    (-135, "#fe10a7"),
    (-130, "#fc0c9a"),
    (-125, "#fb088e"),
    (-120, "#f70482"),
    (-115, "#d70485"),
    (-110, "#b40388"),
    (-105, "#92038a"),
    (-100, "#72038d"),
    (-95, "#44018d"),
    (-90, "#19048f"),
    (-85, "#1f3aa4"),
    (-80, "#246cb7"),
    (-75, "#2aa3cc"),
    (-70, "#38e2e5"),
    (-65, "#56e5e8"),
    (-60, "#72e9ec"),
    (-55, "#8fecef"),
    (-50, "#b1f0f0"),
    (-45, "#6df696"),
    (-40, "#22fd33"),
    (-35, "#0bdf20"),
    (-30, "#0cc61e"),
    (-25, "#0cae1c"),
    (-20, "#0d9519"),
    (-15, "#0e7c17"),
    (-10, "#487047"),
    (-5, "#587657"),
    (0, "#757370"),
    (5, "#7e4d58"),
    (10, "#7a313b"),
    (15, "#7f0001"),
    (20, "#960002"),
    (25, "#ad0003"),
    (30, "#c30104"),
    (35, "#da0105"),
    (40, "#f2060c"),
    (45, "#fb567e"),
    (50, "#fd70a6"),
    (55, "#ff8cd2"),
    (60, "#ffdbb1"),
    (65, "#ffd497"),
    (70, "#ffc081"),
    (75, "#ffac6c"),
    (80, "#ff9756"),
    (85, "#ec7a47"),
    (90, "#d8693d"),
    (95, "#c45933"),
    (100, "#b04829"),
    (105, "#9e3920"),
    (110, "#8a2816"),
    (115, "#76180c"),
    (120, "#640803"),
    (125, "#590502"),
    (130, "#4f0301"),
    (135, "#460201"),
    (140, "#3d0000"),
    (145, "#3b0000"),
    (150, "#3a0000"),
    (155, "#380000"),
    (160, "#370000"),
    (165, "#360000"),
    (170, "#350000"),
    (175, "#330000"),
    (180, "#320000"),
    (185, "#310000"),
    (190, "#300000"),
    (195, "#2e0000"),
    (200, "#2d0000"),
)
# NOAA reflectivity.png, calibrated to its labelled ticks (0 dBZ at 137.5/500).
NOAA_REFLECTIVITY_STOPS = (
    (-27.5, "#8d817f"),
    (-22.69038, "#969154"),
    (-22.48998, "#979254"),
    (-20.48597, "#a29f68"),
    (-18.68236, "#acab78"),
    (-18.08116, "#b0af7f"),
    (-17.88076, "#b0b080"),
    (-13.27154, "#ced0ae"),
    (-12.46994, "#d2d4b4"),
    (-7.45992, "#afb6b4"),
    (-7.25952, "#afb5b4"),
    (-3.85271, "#9ba2b5"),
    (-3.6523, "#9ba1b5"),
    (-2.4499, "#939bb5"),
    (2.56012, "#6276a8"),
    (7.36974, "#445f9f"),
    (7.57014, "#4460a0"),
    (8.57214, "#496faa"),
    (11.77856, "#5aa2c8"),
    (11.97896, "#5ca5cb"),
    (12.58016, "#5eaece"),
    (17.38978, "#52d5a3"),
    (17.79058, "#4ed69a"),
    (22.3998, "#10d617"),
    (22.6002, "#0ed514"),
    (27.40982, "#0b8a0f"),
    (29.01303, "#0a7b0d"),
    (29.21343, "#0b7a0d"),
    (32.41984, "#095f09"),
    (32.62024, "#0e6109"),
    (37.22946, "#f1da01"),
    (37.42986, "#fadf00"),
    (37.63026, "#ffe101"),
    (42.03908, "#ecb82a"),
    (42.64028, "#ebb32d"),
    (47.4499, "#ffb101"),
    (47.6503, "#fd0101"),
    (52.45992, "#a41011"),
    (52.66032, "#a21011"),
    (57.46994, "#b10000"),
    (57.67034, "#fffdff"),
    (62.27956, "#e57cfc"),
    (62.68036, "#e474fc"),
    (67.48998, "#ff75ff"),
    (67.69038, "#ab00fc"),
    (69.09319, "#9300ef"),
    (72.5, "#5b00d3"),
)
# BR HiRes.pal: Color entries interpolate; Step: 5 sets the legend ticks.
REFLECTIVITY_STOPS = (
    (0.0, "#323232"),
    (5.0, "#0e0e5a"),
    (10.0, "#034f8c"),
    (15.0, "#07a2b6"),
    (20.0, "#11e51f"),
    (25.0, "#0ca914"),
    (34.5, "#066808"),
    (35.0, "#ffff00"),
    (40.0, "#ffc200"),
    (45.0, "#ff8c00"),
    (50.0, "#dd0000"),
    (57.5, "#6b0000"),
    (62.5, "#ffa3ff"),
    (67.5, "#ee1df4"),
    (72.5, "#7500eb"),
    (77.5, "#00ffdb"),
    (82.5, "#004c4a"),
    (95.0, "#000000"),
)
RANGE_FOLDED = (128, 0, 255, 255)
_DIRECTORIES: dict[str, tuple[float, tuple[tuple[datetime, str], ...]]] = {}
_PUBLISHED: dict[str, tuple[float, set[str]]] = {}
_LOCK = threading.Lock()
_PROJECTION_LOCK = threading.Lock()

CORRELATION_STOPS = (
    (0.2, "#132b8c"),
    (0.5, "#087cdb"),
    (0.7, "#00c8eb"),
    (0.8, "#00c659"),
    (0.9, "#e6df00"),
    (0.95, "#ff8c00"),
    (0.98, "#f52e22"),
    (1.0, "#b41b57"),
    (1.05, "#e3afff"),
)
ECHO_TOPS_STOPS = (
    (0.0, "#17305a"),
    (10.0, "#246db2"),
    (20.0, "#27bad0"),
    (30.0, "#36b34a"),
    (40.0, "#e9df32"),
    (50.0, "#f38b29"),
    (60.0, "#d72f36"),
    (70.0, "#b72bbd"),
    (80.0, "#f0d3ff"),
)


def colours(values, product="velocity"):
    """Colour measured radar values without filling missing gates."""
    values = np.ma.asarray(values, dtype=float).filled(np.nan)
    positions, hexes = zip(*palette(product)["stops"])
    rgb = np.array([tuple(bytes.fromhex(colour[1:])) for colour in hexes])
    finite = np.isfinite(values)
    safe = np.where(finite, values, 0)
    return np.stack(
        [
            *(np.interp(safe, positions, rgb[:, band]) for band in range(3)),
            finite * 255,
        ],
        axis=-1,
    ).astype(np.uint8)


def palette(product="velocity") -> dict:
    if product == "echo-tops":
        return {
            "stops": ECHO_TOPS_STOPS,
            "units": "kft",
            "display": "fixed",
            "stepped": False,
            "floor": None,
            "ceiling": None,
            "ticks": [float(value) for value in range(0, 81, 10)],
        }
    if product == "correlation-coefficient":
        return {
            "stops": CORRELATION_STOPS,
            "units": "",
            "display": "fixed",
            "stepped": False,
            "floor": None,
            "ceiling": None,
            "ticks": [0.2, 0.5, 0.7, 0.8, 0.9, 1.0, 1.05],
        }
    velocity = product == "velocity"
    mosaic = product == "mosaic"
    return {
        "stops": STOPS
        if velocity
        else NOAA_REFLECTIVITY_STOPS
        if mosaic
        else REFLECTIVITY_STOPS,
        "units": "mph" if velocity else "dBZ",
        "display": "auto" if velocity else "fixed",
        "stepped": False,
        "floor": None,
        "ceiling": None,
        "ticks": [-200, -100, 0, 100, 200]
        if velocity
        else list(range(-20, 71, 10))
        if mosaic
        else [float(value) for value in range(0, 96, 5)],
    }


def scans(payload: bytes) -> tuple[tuple[datetime, str], ...]:
    """NOAA's rolling directory uses UTC modification times, not scan times."""
    if len(payload) > 256 * 1024:
        raise RadarSiteError("velocity directory exceeded its byte budget")
    entries = []
    for name, day, month, year, clock in re.findall(
        rb'href="(sn\.\d{4})"[^\n]*?(\d{2})-([A-Za-z]{3})-(\d{4}) (\d{2}:\d{2})',
        payload,
    ):
        try:
            date = parsedate_to_datetime(
                b" ".join((day, month, year, clock, b"GMT")).decode("ascii")
            )
            entries.append((date, name.decode("ascii")))
        except (ValueError, UnicodeError):
            continue
    return tuple(entries)


def directory_scans(directory, call, timeout):
    """Share small, short-lived scan listings between availability and imagery."""
    with _LOCK:
        entry = _DIRECTORIES.get(directory)
    if entry is not None and time.monotonic() - entry[0] < 45:
        return entry[1]
    try:
        payload = call(directory, timeout, 256 * 1024)
        available = scans(payload)
        if not available and b"Index of /" not in payload:
            raise RadarSiteError("radar scan directory was not a directory listing")
    except RadarSiteError as exc:
        cause = exc.__cause__
        if not isinstance(cause, HTTPError) or cause.code not in {403, 404}:
            raise
        if cause.code == 403:
            parent, name = directory.rstrip("/").rsplit("/", 1)
            with _LOCK:
                published = _PUBLISHED.get(parent)
            if published is None or time.monotonic() - published[0] >= 45:
                payload = call(parent + "/", timeout, 256 * 1024)
                names = {
                    item.decode("ascii")
                    for item in re.findall(rb'href="(SI\.[a-z0-9]{4})/"', payload)
                }
                if not names:
                    raise RadarSiteError(
                        "radar product directory could not be verified"
                    ) from exc
                published = (time.monotonic(), names)
                with _LOCK:
                    _PUBLISHED[parent] = published
            if name in published[1]:
                raise
        available = ()
    with _LOCK:
        if len(_DIRECTORIES) >= 128:
            _DIRECTORIES.pop(next(iter(_DIRECTORIES)))
        _DIRECTORIES[directory] = (time.monotonic(), available)
    return available


@lru_cache(maxsize=1)
def _projection(latitude: float, longitude: float, bounds: tuple, pixels: int):
    """One antenna's WGS84 lookup, shared across products and scans (128 MiB maximum)."""
    from pyproj import Geod

    west, east, south, north = bounds
    columns = west + (np.arange(pixels) + 0.5) * (east - west) / pixels
    bearings = np.empty((pixels, pixels), dtype=np.float32)
    ranges = np.empty_like(bearings)
    geod = Geod(ellps="WGS84")

    def strip(first):
        rows = np.arange(first, min(first + 64, pixels))
        lon, lat = np.meshgrid(columns, north - (rows + 0.5) * (north - south) / pixels)
        azimuth, _, distance = geod.inv(
            np.full(lon.shape, longitude), np.full(lat.shape, latitude), lon, lat
        )
        return first, (azimuth % 360).astype(np.float32), distance.astype(np.float32)

    with ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="radar-projection"
    ) as pool:
        for first, azimuth, distance in pool.map(strip, range(0, pixels, 64)):
            bearings[first : first + len(azimuth)] = azimuth
            ranges[first : first + len(distance)] = distance
    bearings.setflags(write=False)
    ranges.setflags(write=False)
    return bearings, ranges


def render(file, bounds, pixels: int) -> bytes:
    """Nearest native radial/gate; missing sectors stay transparent."""
    packet = file.sym_block[0][0]
    raw = np.asarray(packet["data"], dtype=np.uint8)
    starts = np.asarray(packet["start_az"], dtype=float) % 360
    ends = np.asarray(packet["end_az"], dtype=float) % 360
    echo_tops = getattr(getattr(file, "header", None), "code", None) == 135
    units = "kft" if echo_tops else file.map_data.units
    if (
        raw.ndim != 2
        or not 1 <= raw.shape[0] <= 1440
        or not 1 <= raw.shape[1] <= 2400
        or len(starts) != raw.shape[0]
        or len(ends) != len(starts)
        or packet["first"] != 0
        or not np.all(np.isfinite(starts))
        or not np.all(np.isfinite(ends))
        or not np.isfinite(file.max_range)
        or not 0 < file.max_range <= 500
        or not np.isfinite(file.lat)
        or not -90 <= file.lat <= 90
        or not np.isfinite(file.lon)
        or not -180 <= file.lon <= 180
        or units not in {"m/s", "dBZ", "kft"}
    ):
        raise RadarSiteError("unsupported NEXRAD geometry or units")
    velocity = units == "m/s"
    values = file.map_data(np.arange(256, dtype=np.uint8))
    topped = None
    if echo_tops:
        values, topped = values
        # The high bit marks a lower-bound height; it must not add 128 kft.
        values = np.where(values >= 0, values, np.nan)
    values = values * (MPH_PER_MPS if velocity else 1)
    rgba = colours(
        values,
        "velocity" if velocity else "echo-tops" if echo_tops else "reflectivity",
    )
    rgba[0] = 0
    rgba[1] = 0 if echo_tops else RANGE_FOLDED
    return render_radials(
        raw,
        starts,
        ends,
        file.lat,
        file.lon,
        bounds,
        pixels,
        rgba,
        gate_width=1000 if echo_tops else file.max_range * 1000 / raw.shape[1],
        values=values,
        folded=None if echo_tops else 1,
        lower_bounds=topped,
    )


def render_radials(
    raw, starts, ends, lat, lon, bounds, pixels, rgba, *, gate_width, gate_start=0,
    values=None, folded=None, lower_bounds=None,
):
    """Shared lossless polar renderer; distances and gate edges are in metres."""
    import sharpmod_rs

    order = np.argsort(starts)
    starts, ends, raw = starts[order], ends[order], raw[order]
    with _PROJECTION_LOCK:
        bearings, ranges = _projection(lat, lon, tuple(bounds), pixels)
    try:
        output = sharpmod_rs.sample_radar_radials(
            np.ascontiguousarray(raw, dtype=np.uint8),
            np.ascontiguousarray(starts, dtype=np.float64),
            np.ascontiguousarray(ends, dtype=np.float64),
            np.ascontiguousarray(bearings, dtype=np.float32),
            np.ascontiguousarray(ranges, dtype=np.float32),
            float(gate_width), float(gate_start),
        )
    except ValueError as error:
        raise RadarSiteError(str(error)) from None
    out = io.BytesIO()
    # Native gates have only 256 codes; a palette preserves every RGBA value losslessly.
    image = Image.fromarray(output)
    image.putpalette(rgba[:, :3].tobytes())
    metadata = PngInfo()
    if values is not None:
        lookup = np.ma.asarray(values, dtype=float).filled(np.nan).copy()
        lookup[:2] = np.nan
        # The PNG indices retain the native gate codes, including values clipped by the colour table.
        metadata.add_text("sharppy-radar", json.dumps({
            "values": [float(value) if np.isfinite(value) else None for value in lookup],
            "folded": folded,
            "lowerBounds": [] if lower_bounds is None else np.flatnonzero(lower_bounds).tolist(),
        }, allow_nan=False, separators=(",", ":")))
    image.save(out, format="PNG", transparency=rgba[:, 3].tobytes(), compress_level=3, pnginfo=metadata)
    return out.getvalue()


def fetch_frame(
    site_id: str,
    *,
    product="velocity",
    tilt="0",
    valid_time: datetime | None = None,
    pixels=2048,
    opener=None,
    now=None,
) -> OverlayRaster:
    from metpy.io import Level3File

    echo_tops = product == "echo-tops"
    if (
        product not in {"reflectivity", "velocity", "echo-tops"}
        or tilt not in TILTS
        or (echo_tops and tilt != "0")
    ):
        raise RadarSiteError("unknown radar product or tilt")
    site = site_by_id(site_id)
    if site is None:
        raise RadarSiteError("unknown radar site")
    reference = now or datetime.now(timezone.utc)
    reference = (
        reference.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None
        else reference.astimezone(timezone.utc)
    )
    target = valid_time or reference
    target = (
        target.replace(tzinfo=timezone.utc)
        if target.tzinfo is None
        else target.astimezone(timezone.utc)
    )
    if (
        not reference - timedelta(minutes=70)
        <= target
        <= reference + timedelta(minutes=1)
    ):
        raise RadarSiteError("radar history is available only for the past 60 minutes")
    timeout, _ = _remote_limits()
    call = opener or _default_opener
    family = (
        "135et" if echo_tops else f"{'p99v' if product == 'velocity' else 'p94r'}{tilt}"
    )
    directory = f"{BASE}/DS.{family}/SI.{site.id.lower()}/"
    available = directory_scans(directory, call, timeout)
    recent = [
        (date, name)
        for date, name in available
        if reference - timedelta(minutes=65) <= date <= reference
    ]
    if not recent:
        raise RadarScanMissing(f"{site.id} has no recent {product} scans at tilt {tilt}")
    modified, name = min(recent, key=lambda scan: abs(scan[0] - target))
    if target < reference - timedelta(minutes=2) and abs(modified - target) > timedelta(
        minutes=10
    ):
        raise RadarScanMissing("no radar scan within 10 minutes of the requested time")
    url = directory + name
    cache_key = ("numeric-radar", site.id, product, tilt, name, modified, pixels)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None and cached[1] is not None:
        raster = cached[1]
        if target >= reference - timedelta(minutes=2) or abs(
            raster.valid_time - target
        ) <= timedelta(minutes=10):
            return raster
    payload = call(url, timeout, 2 * 1024 * 1024)
    try:
        file = Level3File(io.BytesIO(payload))
        actual = file.metadata["vol_time"].replace(tzinfo=timezone.utc) + timedelta(
            seconds=float(file.metadata.get("delta_time", 0))
        )
        angle = None if echo_tops else float(file.metadata["el_angle"])
        if (
            file.siteID.upper() != site.id[-3:]
            or file.header.code
            != (135 if echo_tops else 99 if product == "velocity" else 94)
            or (
                not echo_tops
                and (
                    not np.isfinite(angle)
                    or not TILTS[tilt][0] <= angle <= TILTS[tilt][1]
                )
            )
            or not reference - timedelta(minutes=70) <= actual <= reference
            or (
                target < reference - timedelta(minutes=2)
                and abs(actual - target) > timedelta(minutes=10)
            )
        ):
            raise ValueError(
                "radar identity, product or actual scan time does not match"
            )
        image = render(file, site.bounds(), max(256, min(4096, int(pixels))))
    except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
        raise RadarSiteError(f"numeric radar scan could not be decoded: {exc}") from exc
    raster = OverlayRaster(
        key=OVERLAY_KEY,
        title=f"{site.id} Enhanced Echo Tops"
        if echo_tops
        else f"{site.id} {'Velocity' if product == 'velocity' else 'Base Reflectivity'} · {angle:.1f}°",
        short_name=site.id,
        subtitle="18 dBZ echo height · full volume"
        if echo_tops
        else f"Elevation {angle:.1f}°",
        elevation_angle=angle,
        image_bytes=image,
        bounds=site.bounds(),
        retrieved_at=reference,
        valid_time=actual,
        update_interval_s=150,
        source_url=url,
        attribution="NOAA/NWS NEXRAD Level III",
        opacity=0.85,
    )
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            _CACHE.pop(min(_CACHE, key=lambda key: _CACHE[key][0]), None)
        _CACHE[cache_key] = (time.monotonic(), raster)
    return raster
