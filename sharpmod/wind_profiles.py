"""Observed radar VAD/VWP wind profiles and conservative comparisons.

The public recent-data adapter reads NWS RPCCDS product 48 (NVW) from the NWS
Telecommunications Gateway.  The decoder uses the product's tabular block,
whose altitude is hundreds of feet MSL and whose U/V components are m/s.  The
portable JSON/CSV routes retain a strictly wind-only representation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable, Mapping
import urllib.request

import numpy as np


FORMAT = "sharpmod-observed-wind-profiles"
VERSION = 1
NWS_VWP_BASE = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar/DS.48vwp"
NWS_VWP_ATTRIBUTION = "NOAA/NWS NEXRAD RPCCDS VAD Wind Profile (product 48)"
MAX_PRODUCT_BYTES = 2_000_000
MPS_TO_KT = 1.9438444924406
KT_TO_MPS = 1.0 / MPS_TO_KT


class WindProfileError(ValueError):
    """A wind profile is malformed or unsupported."""


class WindProfileCancelled(WindProfileError):
    """A wind-profile retrieval was cancelled before it could be decoded."""


def _cancelled(cancel) -> bool:
    return bool(cancel is not None and cancel())


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > -9998.0 else None


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value) -> str:
    parsed = _time(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else ""


def _quality(rms_kt: float | None) -> str:
    if rms_kt is None:
        return "unknown"
    if rms_kt <= 6.0:
        return "good"
    if rms_kt <= 10.0:
        return "caution"
    return "poor"


def _uv_from_direction_speed(direction_deg, speed_kt):
    direction = _finite(direction_deg)
    speed = _finite(speed_kt)
    if direction is None or speed is None or speed < 0.0:
        return None, None
    radians = math.radians(direction)
    return -speed * math.sin(radians), -speed * math.cos(radians)


@dataclass(frozen=True)
class WindLevel:
    height_msl_m: float
    height_agl_m: float | None
    u_kt: float
    v_kt: float
    direction_deg: float | None = None
    speed_kt: float | None = None
    rms_kt: float | None = None
    quality: str = "unknown"

    def __post_init__(self):
        height = _finite(self.height_msl_m)
        u_wind = _finite(self.u_kt)
        v_wind = _finite(self.v_kt)
        if height is None or u_wind is None or v_wind is None:
            raise WindProfileError("wind levels require finite height, U, and V")
        agl = _finite(self.height_agl_m)
        direction = _finite(self.direction_deg)
        speed = _finite(self.speed_kt)
        if speed is None:
            speed = math.hypot(u_wind, v_wind)
        if direction is None:
            direction = (math.degrees(math.atan2(-u_wind, -v_wind)) + 360.0) % 360.0
        rms = _finite(self.rms_kt)
        quality = str(self.quality or "").lower()
        if quality in {"", "unknown"} and rms is not None:
            quality = _quality(rms)
        if quality not in {"good", "caution", "poor", "unknown"}:
            raise WindProfileError(f"unsupported VWP quality flag: {quality}")
        object.__setattr__(self, "height_msl_m", height)
        object.__setattr__(self, "height_agl_m", agl)
        object.__setattr__(self, "u_kt", u_wind)
        object.__setattr__(self, "v_kt", v_wind)
        object.__setattr__(self, "direction_deg", direction)
        object.__setattr__(self, "speed_kt", speed)
        object.__setattr__(self, "rms_kt", rms)
        object.__setattr__(self, "quality", quality)

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ObservedWindProfile:
    radar_id: str
    latitude: float
    longitude: float
    radar_elevation_m: float | None
    observed_at: datetime
    levels: tuple[WindLevel, ...]
    retrieved_at: datetime | None = None
    source_url: str = ""
    attribution: str = NWS_VWP_ATTRIBUTION
    height_reference: str = "MSL"
    quality_note: str = "RMS residual: good <=6 kt, caution <=10 kt, poor >10 kt"

    def __post_init__(self):
        radar = str(self.radar_id).strip().upper()
        latitude = _finite(self.latitude)
        longitude = _finite(self.longitude)
        observed = _time(self.observed_at)
        levels = tuple(sorted(self.levels, key=lambda item: item.height_msl_m))
        if not radar:
            raise WindProfileError("radar id is required")
        if latitude is None or not -90.0 <= latitude <= 90.0:
            raise WindProfileError("radar latitude is invalid")
        if longitude is None or not -180.0 <= longitude <= 360.0:
            raise WindProfileError("radar longitude is invalid")
        if observed is None:
            raise WindProfileError("observation time is required")
        if not levels:
            raise WindProfileError("wind profile has no usable levels")
        heights = [item.height_msl_m for item in levels]
        if len(set(heights)) != len(heights):
            raise WindProfileError("wind profile heights must be unique")
        object.__setattr__(self, "radar_id", radar)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", ((longitude + 180.0) % 360.0) - 180.0)
        object.__setattr__(self, "radar_elevation_m", _finite(self.radar_elevation_m))
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "retrieved_at", _time(self.retrieved_at))
        object.__setattr__(self, "levels", levels)
        if str(self.height_reference).upper() != "MSL":
            raise WindProfileError("VWP level heights must be explicitly MSL")
        object.__setattr__(self, "height_reference", "MSL")

    @property
    def coverage_msl_m(self) -> tuple[float, float]:
        return self.levels[0].height_msl_m, self.levels[-1].height_msl_m

    @property
    def coverage_agl_m(self) -> tuple[float, float] | None:
        values = [
            item.height_agl_m for item in self.levels if item.height_agl_m is not None
        ]
        return (min(values), max(values)) if values else None

    @property
    def missing_or_poor_levels(self) -> int:
        return sum(item.quality in {"poor", "unknown"} for item in self.levels)

    def age_seconds(self, now=None) -> float | None:
        now = _time(now) or datetime.now(timezone.utc)
        return (now - self.observed_at).total_seconds()

    def as_dict(self) -> dict[str, object]:
        return {
            "radar_id": self.radar_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "radar_elevation_m": self.radar_elevation_m,
            "observed_at": _iso(self.observed_at),
            "retrieved_at": _iso(self.retrieved_at),
            "source_url": self.source_url,
            "attribution": self.attribution,
            "height_reference": self.height_reference,
            "quality_note": self.quality_note,
            "levels": [item.as_dict() for item in self.levels],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        try:
            levels = tuple(WindLevel(**dict(item)) for item in payload["levels"])
            return cls(
                radar_id=str(payload["radar_id"]),
                latitude=float(payload["latitude"]),
                longitude=float(payload["longitude"]),
                radar_elevation_m=_finite(payload.get("radar_elevation_m")),
                observed_at=_time(payload["observed_at"]),
                retrieved_at=_time(payload.get("retrieved_at")),
                source_url=str(payload.get("source_url", "")),
                attribution=str(payload.get("attribution", NWS_VWP_ATTRIBUTION)),
                height_reference=str(payload.get("height_reference", "MSL")),
                quality_note=str(payload.get("quality_note", "")),
                levels=levels,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WindProfileError(f"wind profile is malformed: {exc}") from exc


@dataclass(frozen=True)
class WindDifferenceLevel:
    height_msl_m: float
    height_agl_m: float | None
    observed_u_kt: float
    observed_v_kt: float
    model_u_kt: float | None
    model_v_kt: float | None
    u_error_kt: float | None
    v_error_kt: float | None
    quality: str


@dataclass(frozen=True)
class WindLayerDiagnostics:
    bottom_agl_m: float
    top_agl_m: float
    shear_u_kt: float | None
    shear_v_kt: float | None
    shear_magnitude_kt: float | None
    storm_relative_helicity_m2_s2: float | None
    mean_storm_relative_u_kt: float | None
    mean_storm_relative_v_kt: float | None
    usable_level_count: int
    storm_motion_source: str | None
    reason: str | None = None


def _tabular_rows(level3) -> tuple[list[str], ...]:
    rows = []
    for page in getattr(level3, "tab_pages", ()) or ():
        text = page if isinstance(page, str) else "\n".join(str(item) for item in page)
        for line in text.splitlines():
            tokens = line.split()
            if len(tokens) != 10 or not re.fullmatch(r"\d{3}", tokens[0]):
                continue
            rows.append(tokens)
    return tuple(rows)


def import_level3(path, *, radar_id=None, source_url="") -> ObservedWindProfile:
    """Decode a NEXRAD Level III product-48 file through MetPy.

    The WSR-88D tabular product contract reports altitude in hundreds of feet
    MSL, U/V in m/s, and scalar speed plus RMS residual in knots.  Keep those
    source units explicit here so component conversion cannot be applied to
    the already-knot scalar fields.
    """
    try:
        from metpy.io import Level3File

        product = Level3File(str(path))
    except Exception as exc:
        raise WindProfileError(
            f"NEXRAD Level III file could not be decoded: {exc}"
        ) from exc
    code = getattr(getattr(product, "header", None), "code", None)
    if code != 48:
        raise WindProfileError(f"NEXRAD product {code!r} is not VWP product 48")
    rows = _tabular_rows(product)
    if not rows:
        raise WindProfileError("VWP product has no tabular wind rows")
    elevation_m = _finite(getattr(product, "height", None))
    elevation_m = elevation_m * 0.3048 if elevation_m is not None else None
    by_height = {}
    for tokens in rows:
        altitude_hundreds_ft = _finite(tokens[0])
        u_mps = _finite(tokens[1])
        v_mps = _finite(tokens[2])
        if altitude_hundreds_ft is None or u_mps is None or v_mps is None:
            continue
        height_msl_m = altitude_hundreds_ft * 100.0 * 0.3048
        rms = _finite(tokens[6])
        level = WindLevel(
            height_msl_m=height_msl_m,
            height_agl_m=(
                max(0.0, height_msl_m - elevation_m)
                if elevation_m is not None
                else None
            ),
            u_kt=u_mps * MPS_TO_KT,
            v_kt=v_mps * MPS_TO_KT,
            direction_deg=_finite(tokens[4]),
            speed_kt=_finite(tokens[5]),
            rms_kt=rms,
            quality=_quality(rms),
        )
        previous = by_height.get(height_msl_m)
        # Product 48 may include legacy and enhanced rows at the same nominal
        # height. Keep the lower RMS residual rather than averaging algorithms.
        level_rms = math.inf if level.rms_kt is None else level.rms_kt
        previous_rms = (
            math.inf
            if previous is None or previous.rms_kt is None
            else previous.rms_kt
        )
        if previous is None or level_rms < previous_rms:
            by_height[height_msl_m] = level
    if not by_height:
        raise WindProfileError("VWP product contains no finite wind vectors")
    metadata = getattr(product, "metadata", {}) or {}
    observed = _time(metadata.get("vol_time") or metadata.get("prod_time"))
    site = str(radar_id or getattr(product, "siteID", "")).strip().upper()
    if len(site) == 3 and site[0] not in {"K", "P", "T"}:
        site = "K" + site
    return ObservedWindProfile(
        radar_id=site,
        latitude=float(product.lat),
        longitude=float(product.lon),
        radar_elevation_m=elevation_m,
        observed_at=observed,
        retrieved_at=datetime.now(timezone.utc),
        source_url=str(source_url or Path(path).resolve()),
        levels=tuple(by_height.values()),
    )


def recent_vwp_url(radar_id: str) -> str:
    """Return the documented NWS RPCCDS ``sn.last`` path for one radar."""
    radar = str(radar_id).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]{3}", radar):
        raise WindProfileError("radar id must be a four-character site identifier")
    return f"{NWS_VWP_BASE}/SI.{radar}/sn.last"


def fetch_recent_vwp(radar_id, *, timeout=20.0, opener=None, cancel=None):
    """Retrieve and decode the latest public product-48 file for ``radar_id``.

    ``cancel`` is polled before opening the request, between bounded response
    reads, and before decoding.  A read already blocked in the operating system
    remains bounded by ``timeout``.
    """
    if _cancelled(cancel):
        raise WindProfileCancelled("VWP retrieval cancelled")
    url = recent_vwp_url(radar_id)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SHARPpy-Reimagined/1.2 VWP retrieval"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(request, timeout=float(timeout))
        with response:
            chunks = []
            received = 0
            while received <= MAX_PRODUCT_BYTES:
                if _cancelled(cancel):
                    raise WindProfileCancelled("VWP retrieval cancelled")
                chunk = response.read(min(64 * 1024, MAX_PRODUCT_BYTES + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            payload = b"".join(chunks)
    except WindProfileCancelled:
        raise
    except Exception as exc:
        raise WindProfileError(
            f"recent VWP retrieval failed for {radar_id}: {exc}"
        ) from exc
    if len(payload) > MAX_PRODUCT_BYTES:
        raise WindProfileError("VWP response exceeds the safety limit")
    if _cancelled(cancel):
        raise WindProfileCancelled("VWP retrieval cancelled")
    descriptor, name = tempfile.mkstemp(prefix="sharpmod-vwp-", suffix=".nids")
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_bytes(payload)
        if _cancelled(cancel):
            raise WindProfileCancelled("VWP retrieval cancelled")
        return import_level3(temporary, radar_id=radar_id, source_url=url)
    finally:
        temporary.unlink(missing_ok=True)


def _interp(levels, field, target_agl_m, *, max_gap_m=750.0, max_rms_kt=10.0):
    accepted = [
        item
        for item in levels
        if item.height_agl_m is not None
        and (item.rms_kt is None or item.rms_kt <= max_rms_kt)
        and item.quality != "poor"
    ]
    x = np.asarray([item.height_agl_m for item in accepted], dtype=float)
    y = np.asarray([getattr(item, field) for item in accepted], dtype=float)
    if not x.size:
        return None
    exact = np.flatnonzero(np.isclose(x, target_agl_m, atol=0.1))
    if exact.size:
        return float(y[int(exact[0])])
    upper = int(np.searchsorted(x, target_agl_m, side="right"))
    lower = upper - 1
    if lower < 0 or upper >= x.size or x[upper] - x[lower] > max_gap_m:
        return None
    weight = (target_agl_m - x[lower]) / (x[upper] - x[lower])
    return float(y[lower] + weight * (y[upper] - y[lower]))


def layer_diagnostics(
    profile: ObservedWindProfile,
    bottom_agl_m: float,
    top_agl_m: float,
    *,
    storm_motion: tuple[float, float, str] | None = None,
    max_gap_m: float = 750.0,
    max_rms_kt: float = 10.0,
) -> WindLayerDiagnostics:
    """Compute supported shear/SR diagnostics without vertical extrapolation."""
    bottom = _finite(bottom_agl_m)
    top = _finite(top_agl_m)
    if bottom is None or top is None or bottom < 0.0 or top <= bottom:
        raise WindProfileError("layer bounds must satisfy 0 <= bottom < top")
    bottom_u = _interp(
        profile.levels, "u_kt", bottom, max_gap_m=max_gap_m, max_rms_kt=max_rms_kt
    )
    bottom_v = _interp(
        profile.levels, "v_kt", bottom, max_gap_m=max_gap_m, max_rms_kt=max_rms_kt
    )
    top_u = _interp(
        profile.levels, "u_kt", top, max_gap_m=max_gap_m, max_rms_kt=max_rms_kt
    )
    top_v = _interp(
        profile.levels, "v_kt", top, max_gap_m=max_gap_m, max_rms_kt=max_rms_kt
    )
    interior = [
        item
        for item in profile.levels
        if item.height_agl_m is not None
        and bottom <= item.height_agl_m <= top
        and item.quality != "poor"
        and (item.rms_kt is None or item.rms_kt <= max_rms_kt)
    ]
    if None in (bottom_u, bottom_v, top_u, top_v):
        return WindLayerDiagnostics(
            bottom,
            top,
            None,
            None,
            None,
            None,
            None,
            None,
            len(interior),
            storm_motion[2] if storm_motion else None,
            "VWP coverage or quality does not support both layer bounds",
        )
    shear_u = top_u - bottom_u
    shear_v = top_v - bottom_v
    if storm_motion is None:
        return WindLayerDiagnostics(
            bottom,
            top,
            shear_u,
            shear_v,
            math.hypot(shear_u, shear_v),
            None,
            None,
            None,
            len(interior),
            None,
            "storm-relative diagnostics require an explicit storm motion",
        )
    storm_u, storm_v, storm_source = storm_motion
    storm_u = _finite(storm_u)
    storm_v = _finite(storm_v)
    if storm_u is None or storm_v is None or not str(storm_source).strip():
        raise WindProfileError("storm motion requires finite U/V and a named source")
    nodes = [
        WindLevel(
            profile.radar_elevation_m + bottom
            if profile.radar_elevation_m is not None
            else bottom,
            bottom,
            bottom_u,
            bottom_v,
        ),
        *[item for item in interior if bottom < item.height_agl_m < top],
        WindLevel(
            profile.radar_elevation_m + top
            if profile.radar_elevation_m is not None
            else top,
            top,
            top_u,
            top_v,
        ),
    ]
    sr_u = np.asarray([item.u_kt - storm_u for item in nodes])
    sr_v = np.asarray([item.v_kt - storm_v for item in nodes])
    # Line integral around the storm-relative hodograph, converted to m2/s2.
    srh_kt2 = sum(
        sr_u[index + 1] * sr_v[index] - sr_u[index] * sr_v[index + 1]
        for index in range(len(nodes) - 1)
    )
    return WindLayerDiagnostics(
        bottom,
        top,
        shear_u,
        shear_v,
        math.hypot(shear_u, shear_v),
        float(srh_kt2 * KT_TO_MPS * KT_TO_MPS),
        float(np.mean(sr_u)),
        float(np.mean(sr_v)),
        len(nodes),
        str(storm_source).strip(),
        None,
    )


def _profile_column(profile, name):
    try:
        array = np.ma.asarray(getattr(profile, name), dtype=float).reshape(-1)
    except (AttributeError, TypeError, ValueError):
        return np.asarray([], dtype=float)
    result = np.asarray(array.filled(np.nan), dtype=float)
    result[~np.isfinite(result) | (result <= -9998.0)] = np.nan
    return result


def _interp_msl(height, values, target, max_gap_m):
    size = min(height.size, values.size)
    mask = np.isfinite(height[:size]) & np.isfinite(values[:size])
    x, y = height[:size][mask], values[:size][mask]
    if not x.size:
        return None
    order = np.argsort(x)
    x, y = x[order], y[order]
    x, indices = np.unique(x, return_index=True)
    y = y[indices]
    exact = np.flatnonzero(np.isclose(x, target, atol=0.1))
    if exact.size:
        return float(y[int(exact[0])])
    upper = int(np.searchsorted(x, target, side="right"))
    lower = upper - 1
    if lower < 0 or upper >= x.size or x[upper] - x[lower] > max_gap_m:
        return None
    weight = (target - x[lower]) / (x[upper] - x[lower])
    return float(y[lower] + weight * (y[upper] - y[lower]))


def compare_model_winds(
    observed: ObservedWindProfile,
    model_profile,
    *,
    max_gap_m=750.0,
    max_rms_kt=10.0,
) -> tuple[WindDifferenceLevel, ...]:
    """Return model-minus-observed U/V on real VWP MSL levels."""
    height = _profile_column(model_profile, "hght")
    model_u = _profile_column(model_profile, "u")
    model_v = _profile_column(model_profile, "v")
    rows = []
    for level in observed.levels:
        usable = level.quality != "poor" and (
            level.rms_kt is None or level.rms_kt <= max_rms_kt
        )
        u_wind = (
            _interp_msl(height, model_u, level.height_msl_m, max_gap_m)
            if usable
            else None
        )
        v_wind = (
            _interp_msl(height, model_v, level.height_msl_m, max_gap_m)
            if usable
            else None
        )
        rows.append(
            WindDifferenceLevel(
                level.height_msl_m,
                level.height_agl_m,
                level.u_kt,
                level.v_kt,
                u_wind,
                v_wind,
                u_wind - level.u_kt if u_wind is not None else None,
                v_wind - level.v_kt if v_wind is not None else None,
                level.quality,
            )
        )
    return tuple(rows)


def write_wind_profiles(path, profiles: Iterable[ObservedWindProfile]) -> Path:
    """Atomically persist one or more wind-only observations."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "profiles": [profile.as_dict() for profile in profiles],
    }
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_wind_profiles(path) -> tuple[ObservedWindProfile, ...]:
    """Read the portable JSON format or a documented wind-profile CSV."""
    source = Path(path)
    if source.suffix.casefold() == ".csv":
        return (read_wind_profile_csv(source),)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WindProfileError(f"wind-profile file could not be read: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("format") != FORMAT
        or payload.get("version") != VERSION
        or not isinstance(payload.get("profiles"), list)
    ):
        raise WindProfileError("unsupported wind-profile JSON file")
    return tuple(ObservedWindProfile.from_dict(item) for item in payload["profiles"])


def read_wind_profile_csv(path) -> ObservedWindProfile:
    """Read a wind-only CSV with repeated profile metadata columns."""
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = tuple(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise WindProfileError(f"wind-profile CSV could not be read: {exc}") from exc
    if not rows:
        raise WindProfileError("wind-profile CSV has no rows")
    first = rows[0]
    elevation = _finite(first.get("radar_elevation_m"))
    levels = []
    for row in rows:
        height = _finite(row.get("height_msl_m"))
        agl = _finite(row.get("height_agl_m"))
        u_wind = _finite(row.get("u_kt"))
        v_wind = _finite(row.get("v_kt"))
        if u_wind is None or v_wind is None:
            u_wind, v_wind = _uv_from_direction_speed(
                row.get("direction_deg"), row.get("speed_kt")
            )
        if height is None or u_wind is None or v_wind is None:
            continue
        if agl is None and elevation is not None:
            agl = max(0.0, height - elevation)
        rms = _finite(row.get("rms_kt"))
        levels.append(
            WindLevel(
                height,
                agl,
                u_wind,
                v_wind,
                _finite(row.get("direction_deg")),
                _finite(row.get("speed_kt")),
                rms,
                str(row.get("quality") or _quality(rms)),
            )
        )
    return ObservedWindProfile(
        radar_id=str(first.get("radar_id", "")),
        latitude=float(first.get("latitude", "nan")),
        longitude=float(first.get("longitude", "nan")),
        radar_elevation_m=elevation,
        observed_at=_time(first.get("observed_at")),
        retrieved_at=_time(first.get("retrieved_at")),
        source_url=str(first.get("source_url", Path(path).resolve())),
        attribution=str(first.get("attribution", NWS_VWP_ATTRIBUTION)),
        levels=tuple(levels),
    )


def export_wind_profile_csv(path, profile: ObservedWindProfile) -> Path:
    """Export all provenance and level quality as an ordinary CSV."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "radar_id",
        "latitude",
        "longitude",
        "radar_elevation_m",
        "observed_at",
        "retrieved_at",
        "source_url",
        "attribution",
        "height_reference",
        "height_msl_m",
        "height_agl_m",
        "u_kt",
        "v_kt",
        "direction_deg",
        "speed_kt",
        "rms_kt",
        "quality",
    )
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            common = {
                "radar_id": profile.radar_id,
                "latitude": profile.latitude,
                "longitude": profile.longitude,
                "radar_elevation_m": profile.radar_elevation_m,
                "observed_at": _iso(profile.observed_at),
                "retrieved_at": _iso(profile.retrieved_at),
                "source_url": profile.source_url,
                "attribution": profile.attribution,
                "height_reference": profile.height_reference,
            }
            for level in profile.levels:
                writer.writerow({**common, **level.as_dict()})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return destination


class WindProfileSeries:
    """Small stepping model for chronologically ordered VWP observations."""

    def __init__(self, profiles=()):
        self._profiles = tuple(sorted(profiles, key=lambda item: item.observed_at))
        self._index = 0

    @property
    def profiles(self):
        return self._profiles

    @property
    def current(self):
        return self._profiles[self._index] if self._profiles else None

    @property
    def index(self):
        return self._index

    def select(self, index):
        if not self._profiles:
            return None
        self._index = max(0, min(int(index), len(self._profiles) - 1))
        return self.current

    def step(self, amount=1):
        return self.select(self._index + int(amount))

    def as_dicts(self):
        return [item.as_dict() for item in self._profiles]


__all__ = [
    "FORMAT",
    "NWS_VWP_ATTRIBUTION",
    "ObservedWindProfile",
    "WindDifferenceLevel",
    "WindLayerDiagnostics",
    "WindLevel",
    "WindProfileCancelled",
    "WindProfileError",
    "WindProfileSeries",
    "compare_model_winds",
    "export_wind_profile_csv",
    "fetch_recent_vwp",
    "import_level3",
    "layer_diagnostics",
    "read_wind_profile_csv",
    "read_wind_profiles",
    "recent_vwp_url",
    "write_wind_profiles",
]
