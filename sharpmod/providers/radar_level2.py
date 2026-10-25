"""Native Level II reflectivity and correlation, sharing session volumes and cuts."""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import numpy as np

from sharpmod.maps.map_overlays import OverlayRaster
from sharpmod.providers import radar_level3, radar_site

BASE = "https://unidata-nexrad-level2.s3.amazonaws.com"
MAX_VOLUME_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 32
MOMENTS = {"reflectivity": b"REF", "correlation-coefficient": b"RHO"}
_LOCK = threading.Lock()
_LISTINGS = OrderedDict()
_VOLUMES: OrderedDict[str, Future] = OrderedDict()
log = logging.getLogger(__name__)


def disk_cache_root():
    from sharpmod.bridge.sounding_cache import cache

    return cache().root / "radar-volumes"


def clear_cache():
    with _LOCK:
        _VOLUMES.clear()
        _LISTINGS.clear()


@dataclass(frozen=True)
class Sweep:
    angle: float
    when: datetime
    lat: float
    lon: float
    starts: np.ndarray
    ends: np.ndarray
    raw: np.ndarray
    rgba: np.ndarray
    values: np.ndarray
    gate_width: float
    gate_start: float


def _listing(site, day, cutoff, call, timeout):
    prefix = f"{day:%Y/%m/%d}/{site.id}/"
    with _LOCK:
        cached = _LISTINGS.get(prefix)
    if cached is not None and time.monotonic() - cached[0] < 45:
        return cached[1]
    query = urlencode(
        {
            "list-type": 2,
            "prefix": prefix,
            "max-keys": 1000,
            "start-after": f"{prefix}{site.id}{cutoff:%Y%m%d_%H%M%S}",
        }
    )
    payload = call(f"{BASE}/?{query}", timeout, 512 * 1024)
    if len(payload) > 512 * 1024:
        raise radar_site.RadarSiteError("Level II listing exceeded the byte budget")
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise radar_site.RadarSiteError("Level II returned an unsafe volume listing")
    try:
        root = ET.fromstring(payload)
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        if (
            root.tag != f"{ns}ListBucketResult"
            or root.findtext(f"{ns}IsTruncated") != "false"
        ):
            raise ValueError("incomplete listing")
        scans = []
        for item in root.findall(f"{ns}Contents"):
            key = item.findtext(f"{ns}Key", "")
            match = re.fullmatch(
                re.escape(prefix + site.id) + r"(\d{8}_\d{6})_V\d{2}", key
            )
            if match is None:
                continue
            size = int(item.findtext(f"{ns}Size", "0"))
            if 0 < size <= MAX_VOLUME_BYTES:
                when = datetime.strptime(match[1], "%Y%m%d_%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                scans.append((when, key, size))
    except (ET.ParseError, ValueError, TypeError) as exc:
        raise radar_site.RadarSiteError(
            "Level II returned an invalid volume listing"
        ) from exc
    with _LOCK:
        _LISTINGS[prefix] = time.monotonic(), tuple(scans)
        while len(_LISTINGS) > 32:
            _LISTINGS.popitem(last=False)
    return tuple(scans)


def _select(site_id, valid_time, now, opener):
    site = radar_site.site_by_id(site_id)
    if site is None:
        raise radar_site.RadarSiteError("unknown radar site")
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
        raise radar_site.RadarSiteError(
            "radar history is available only for the past 60 minutes"
        )
    call = opener or radar_site._default_opener
    timeout, _ = radar_site._remote_limits()
    cutoff = reference - timedelta(minutes=70)
    days = sorted({cutoff.date(), reference.date()})
    scans = [
        scan
        for day in days
        for scan in _listing(site, day, cutoff, call, timeout)
        if cutoff <= scan[0] <= reference
    ]
    if target < reference - timedelta(minutes=2):
        scans = [
            scan for scan in scans if abs(scan[0] - target) <= timedelta(minutes=10)
        ]
    else:
        scans = [scan for scan in scans if scan[0] >= reference - timedelta(minutes=15)]
    selected = min(scans, key=lambda scan: abs(scan[0] - target), default=None)
    return site, reference, selected, call, timeout


def _decode(payload, site, expected):
    from metpy.io import Level2File
    from metpy.io.nexrad import nexrad_to_datetime

    # MetPy normally merges range-folded gates with missing data.
    class NativeFile(Level2File):
        RANGE_FOLD = float("-inf")

    if len(payload) < 24 or not payload.startswith(b"AR2V"):
        raise ValueError("not a Level II archive volume")
    file = NativeFile(io.BytesIO(payload))
    if file.stid.decode("ascii") != site.id or abs(
        file.dt.replace(tzinfo=timezone.utc) - expected
    ) > timedelta(seconds=1):
        raise ValueError("volume site or time does not match the requested scan")
    cuts = {}
    vcp = getattr(file, "vcp_info", None)
    for product, moment in MOMENTS.items():
        for index, sweep in enumerate(file.sweeps):
            rays = [
                ray
                for ray in sweep
                if hasattr(ray, "moments") and moment in ray.moments
            ]
            if not rays:
                continue
            # Use the commanded angle: individual rays wobble across cut boundaries in high winds.
            angle = float(
                vcp.els[index].el_angle
                if vcp and index < len(vcp.els)
                else np.median([ray.header.el_angle for ray in rays])
            )
            cut = next(
                (
                    key
                    for key, (low, high) in radar_level3.TILTS.items()
                    if low <= angle < high
                ),
                None,
            )
            if cut is None or cut in {"x", "y"}:
                continue
            header = rays[0].moments[moment][0]
            lat, lon = float(rays[0].vol_consts.lat), float(rays[0].vol_consts.lon)
            starts = (
                np.asarray(
                    [ray.header.az_angle - ray.header.az_spacing / 2 for ray in rays]
                )
                % 360
            )
            widths = np.asarray([ray.header.az_spacing for ray in rays])
            geometry = (
                header.num_gates,
                header.first_gate,
                header.gate_width,
                header.scale,
                header.offset,
                header.data_size,
            )
            if (
                not 1 <= len(rays) <= 1440
                or not 1 <= header.num_gates <= 2400
                or not 0 < header.gate_width <= 1
                or not 0 <= header.first_gate <= 10
                or header.first_gate + header.num_gates * header.gate_width > 500
                or header.data_size != 8
                or not np.isfinite(header.scale)
                or header.scale <= 0
                or not np.isfinite(header.offset)
                or not np.all(np.isfinite(starts))
                or not np.all(np.isin(widths, (0.5, 1.0)))
                or not np.isfinite(lat + lon)
                or abs(lat - site.lat) > 1
                or abs(lon - site.lon) > 1
            ):
                raise ValueError("unsupported Level II moment geometry")
            rows = []
            for ray in rays:
                h, values = ray.moments[moment]
                if (
                    h.num_gates,
                    h.first_gate,
                    h.gate_width,
                    h.scale,
                    h.offset,
                    h.data_size,
                ) != geometry or len(values) != header.num_gates:
                    raise ValueError("inconsistent Level II moment gates")
                codes = np.nan_to_num(
                    values * h.scale + h.offset, nan=0, neginf=1, posinf=0
                )
                if np.any(codes < 0) or np.any(codes > 255):
                    raise ValueError("invalid Level II moment values")
                rows.append(np.rint(codes).astype(np.uint8))
            raw = np.stack(rows)
            lookup = (np.arange(256) - header.offset) / header.scale
            rgba = radar_level3.colours(lookup, product)
            rgba[0], rgba[1] = (
                0,
                radar_level3.RANGE_FOLDED if product == "reflectivity" else 0,
            )
            when = nexrad_to_datetime(
                rays[0].header.date, rays[0].header.time_ms
            ).replace(tzinfo=timezone.utc)
            if not expected <= when <= expected + timedelta(minutes=15):
                raise ValueError("invalid Level II sweep time")
            candidate = Sweep(
                angle,
                when,
                lat,
                lon,
                starts,
                (starts + widths) % 360,
                raw,
                rgba,
                lookup,
                header.gate_width * 1000,
                (header.first_gate - header.gate_width / 2) * 1000,
            )
            previous = cuts.get((product, cut))
            # Split cuts repeat the same elevation; keep the moment's full range, then the newest repeat.
            if previous is None or (raw.shape[1], when) > (
                previous.raw.shape[1],
                previous.when,
            ):
                cuts[product, cut] = candidate
    if not cuts:
        raise ValueError("volume contains no usable radar cuts")
    return cuts


def _store(path, payload):
    temporary = None
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            entries = [
                (item.stat().st_mtime, item.stat().st_size, item)
                for item in path.parent.glob("*.ar2")
                if item != path and not item.is_symlink()
            ]
            total = sum(size for _, size, _ in entries)
            count = len(entries)
            for _, size, item in sorted(entries):
                if (
                    count < MAX_CACHE_ENTRIES
                    and total + len(payload) <= MAX_CACHE_BYTES
                ):
                    break
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    continue
                count -= 1
                total -= size
            if count >= MAX_CACHE_ENTRIES or total + len(payload) > MAX_CACHE_BYTES:
                return
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=".part", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(payload)
            os.replace(temporary, path)
    except OSError:
        log.debug("could not retain Level II volume", exc_info=True)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _volume(site, selected, call, timeout):
    when, key, size = selected
    with _LOCK:
        future = _VOLUMES.get(key)
        owner = future is None
        if owner:
            future = _VOLUMES[key] = Future()
        _VOLUMES.move_to_end(key)
    if not owner:
        return future.result()
    try:
        path = None
        try:
            path = disk_cache_root() / (key.rsplit("/", 1)[1] + ".ar2")
            if not path.is_symlink() and path.stat().st_size == size:
                with path.open("rb") as stream:
                    payload = stream.read(MAX_VOLUME_BYTES + 1)
                try:
                    result = _decode(payload, site, when)
                except Exception:
                    path.unlink(missing_ok=True)
                else:
                    future.set_result(result)
                    return result
        except OSError:
            pass
        payload = call(f"{BASE}/{key}", max(timeout, 45), MAX_VOLUME_BYTES)
        if len(payload) != size or len(payload) > MAX_VOLUME_BYTES:
            raise ValueError("incomplete or oversized Level II volume")
        result = _decode(payload, site, when)
        if path is not None:
            _store(path, payload)
        future.set_result(result)
        return result
    except Exception as exc:
        error = radar_site.RadarSiteError(f"Level II volume could not be loaded: {exc}")
        future.set_exception(error)
        with _LOCK:
            if _VOLUMES.get(key) is future:
                _VOLUMES.pop(key)
        raise error from exc
    finally:
        with _LOCK:
            # shortcut: retain two decoded volumes; increase only if multi-site profiling warrants it.
            completed = [key for key, entry in _VOLUMES.items() if entry.done()]
            for old in completed[:-2]:
                _VOLUMES.pop(old, None)


def availability(
    site_id, valid_time=None, *, product="reflectivity", now=None, opener=None
):
    if product not in MOMENTS:
        raise radar_site.RadarSiteError("unknown Level II product")
    site, reference, selected, call, timeout = _select(site_id, valid_time, now, opener)
    cuts = {} if selected is None else _volume(site, selected, call, timeout)
    return {
        "site_id": site.id,
        "product": product,
        "checked_at": reference.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tilts": [
            {
                "tilt": cut,
                "status": "available" if (product, cut) in cuts else "unavailable",
            }
            for cut in radar_level3.TILTS
            if cut not in {"x", "y"}
        ],
    }


def fetch_frame(
    site_id,
    *,
    product="reflectivity",
    tilt="0",
    valid_time=None,
    pixels=4096,
    opener=None,
    now=None,
):
    if product not in MOMENTS:
        raise radar_site.RadarSiteError("unknown Level II product")
    if tilt not in radar_level3.TILTS or tilt in {"x", "y"}:
        raise radar_site.RadarSiteError("unknown radar tilt")
    site, reference, selected, call, timeout = _select(site_id, valid_time, now, opener)
    if selected is None:
        raise radar_site.RadarScanMissing(
            f"{site.id} has no recent Level II volume for this time"
        )
    key = ("level2", product, selected[1], tilt, pixels)
    with radar_site._CACHE_LOCK:
        cached = radar_site._CACHE.get(key)
    if cached is not None:
        return cached[1]
    sweep = _volume(site, selected, call, timeout).get((product, tilt))
    if sweep is None:
        raise radar_site.RadarScanMissing(
            f"{site.id} does not have this {radar_site.get_product(product).label} tilt in the selected volume"
        )
    if (
        not reference - timedelta(minutes=70)
        <= sweep.when
        <= reference + timedelta(minutes=1)
    ):
        raise radar_site.RadarSiteError(
            "Level II sweep time is outside the current radar window"
        )
    bounds = site.bounds()
    image = radar_level3.render_radials(
        sweep.raw,
        sweep.starts,
        sweep.ends,
        sweep.lat,
        sweep.lon,
        bounds,
        max(256, min(4096, int(pixels))),
        sweep.rgba,
        gate_width=sweep.gate_width,
        gate_start=sweep.gate_start,
        values=sweep.values,
        folded=1 if product == "reflectivity" else None,
    )
    raster = OverlayRaster(
        key=radar_site.OVERLAY_KEY,
        title=f"{site.id} {radar_site.get_product(product).label} · {sweep.angle:.1f}°",
        short_name=site.id,
        subtitle=f"Elevation {sweep.angle:.1f}°",
        elevation_angle=sweep.angle,
        image_bytes=image,
        bounds=bounds,
        retrieved_at=reference,
        valid_time=sweep.when,
        update_interval_s=150,
        source_url=f"{BASE}/{selected[1]}",
        attribution="NOAA/NEXRAD Level II · NSF Unidata",
        opacity=0.85,
    )
    with radar_site._CACHE_LOCK:
        if len(radar_site._CACHE) >= radar_site._CACHE_MAX_ENTRIES:
            oldest = min(radar_site._CACHE, key=lambda key: radar_site._CACHE[key][0])
            radar_site._CACHE.pop(oldest, None)
        radar_site._CACHE[key] = (time.monotonic(), raster)
    return raster
