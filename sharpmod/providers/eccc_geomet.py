"""Point-sounding adapters for Environment Canada forecast models.

The ECCC Datamart publishes GDPS and RDPS as one GRIB2 file per
variable/level.  Downloading every complete global field is wasteful for a
single sounding, so this adapter uses the official GeoMet WMS
``GetFeatureInfo`` point route instead.  Each response is a few hundred bytes
and identifies both the selected grid point and model reference time.

GeoMet currently accepts only one layer per request.  Profile retrieval is
therefore a bounded fan-out of point requests, never an unbounded thread per
level.  The resulting arrays follow the same portable NPZ contract as the
other SHARPpy Reimagined extractors.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os
import threading
import time
from xml.etree import ElementTree

import numpy as np

from sharpmod import backends as _backends
from sharpmod.export_paths import export_file_path
from sharpmod.io.portable_sounding import fatal_issues
from sharpmod.models.model_surface import (
    SURFACE_CONTRACT_FIELDS,
    SURFACE_CONTRACT_VERSION,
    merge_surface_level,
)
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.tools.era5_extract import (
    ParameterRangeError,
    RetrievalError,
    _atomic_write_json,
    _atomic_write_npz,
    _mark_missing,
    _quiet_remove,
    dewpoint_from_specific_humidity,
)


GEOMET_URL = "https://geo.weather.gc.ca/geomet"
USER_AGENT = (
    "SHARPpy-Reimagined/0.4 "
    "(+https://github.com/ShianMike/SHARPpy-Reimagined)"
)

PRESSURE_LEVELS = (
    1015, 1000, 985, 970, 950, 925, 900, 875, 850, 800, 750,
    700, 650, 600, 550, 500, 450, 400, 350, 300, 275, 250,
    225, 200, 175, 150, 100, 50, 30, 20, 10, 5, 1,
)

_REQUIRED_VARIABLES = (
    "AirTemp",
    "GeopotentialHeight",
    "SpecificHumidity",
    "WindDir",
    "WindSpeed",
)
_SURFACE_VARIABLES = (
    "SurfacePressure",
    "SurfaceHeight",
    "SurfaceTemperature",
    "SurfaceDewpoint",
    "SurfaceWindDir",
    "SurfaceWindSpeed",
)
_SURFACE_LAYER_SUFFIX = {
    "SurfacePressure": "Pressure",
    "SurfaceHeight": "GeopotentialHeight",
    "SurfaceTemperature": "AirTemp_2m",
    "SurfaceDewpoint": "DewPoint_2m",
    "SurfaceWindDir": "WindDir_10m",
    "SurfaceWindSpeed": "WindSpeed_10m",
}
# Terrain height never changes through a run, and GeoMet advertises the
# surface geopotential-height layer with a single-instant time dimension (the
# analysis). Requesting it at a later valid time returns a WMS
# ServiceException, so these variables are always read at the run time itself.
_ANALYSIS_ONLY_SURFACE = frozenset({"SurfaceHeight"})
_SURFACE_CONTRACT_REQUIREMENTS = (
    ("surface_pressure", ("SurfacePressure",)),
    ("surface_height", ("SurfaceHeight",)),
    ("two_metre_temperature", ("SurfaceTemperature",)),
    ("two_metre_moisture", ("SurfaceDewpoint",)),
    ("ten_metre_u_wind", ("SurfaceWindDir", "SurfaceWindSpeed")),
    ("ten_metre_v_wind", ("SurfaceWindDir", "SurfaceWindSpeed")),
)


@dataclass(frozen=True)
class GeoMetCapability:
    """Normalized, UI-independent contract for one ECCC provider adapter."""

    model_key: str
    label: str
    provider: str
    layer_prefix: str
    domain: str
    domain_bounds: tuple[float, float, float, float]
    cycles: tuple[int, ...]
    forecast_hours: tuple[int, ...]
    pressure_levels: tuple[int, ...]
    omega_levels: tuple[int, ...]
    fields: tuple[str, ...]
    archive_window: str
    transports: tuple[str, ...]
    domain_outline: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class RotatedGridDomain:
    """Geographic limits expressed in an ECCC rotated-lat/lon coordinate grid."""

    north_pole_lat: float
    north_pole_lon: float
    rotated_lon_min: float
    rotated_lon_max: float
    rotated_lat_min: float
    rotated_lat_max: float

    def to_rotated(self, lat: float, lon: float) -> tuple[float, float]:
        """Convert one geographic coordinate to this grid's rotated degrees."""

        phi = math.radians(float(lat))
        delta_lon = math.radians(
            ((float(lon) - self.north_pole_lon + 180.0) % 360.0) - 180.0
        )
        pole_lat = math.radians(self.north_pole_lat)
        rotated_lat = math.asin(
            math.sin(phi) * math.sin(pole_lat)
            + math.cos(phi) * math.cos(pole_lat) * math.cos(delta_lon)
        )
        rotated_lon = -math.atan2(
            math.cos(phi) * math.sin(delta_lon),
            math.sin(phi) * math.cos(pole_lat)
            - math.cos(phi) * math.sin(pole_lat) * math.cos(delta_lon),
        )
        return math.degrees(rotated_lat), math.degrees(rotated_lon)

    def to_geographic(
        self, rotated_lat: float, rotated_lon: float
    ) -> tuple[float, float]:
        """Convert one rotated-grid coordinate to ``(lat, lon)`` degrees."""

        phi_r = math.radians(float(rotated_lat))
        lon_r = math.radians(float(rotated_lon))
        pole_lat = math.radians(self.north_pole_lat)
        lat = math.asin(
            math.sin(phi_r) * math.sin(pole_lat)
            + math.cos(phi_r) * math.cos(pole_lat) * math.cos(lon_r)
        )
        delta_lon = math.atan2(
            -math.cos(phi_r) * math.sin(lon_r),
            math.sin(phi_r) * math.cos(pole_lat)
            - math.cos(phi_r) * math.sin(pole_lat) * math.cos(lon_r),
        )
        lon = self.north_pole_lon + math.degrees(delta_lon)
        lon = ((lon + 180.0) % 360.0) - 180.0
        return math.degrees(lat), lon

    def contains(self, lat: float, lon: float) -> bool:
        """Return whether a geographic point lies on the rotated model grid."""

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if not -90.0 <= lat <= 90.0:
            return False
        rotated_lat, rotated_lon = self.to_rotated(lat, lon)
        return (
            self.rotated_lat_min <= rotated_lat <= self.rotated_lat_max
            and self.rotated_lon_min <= rotated_lon <= self.rotated_lon_max
        )

    def outline(self, samples_per_edge: int = 28) -> tuple[
        tuple[float, float], ...
    ]:
        """Return a sampled geographic perimeter as ``(lon, lat)`` points."""

        count = max(2, int(samples_per_edge))

        def values(start, stop):
            return tuple(
                start + (stop - start) * index / count
                for index in range(count)
            )

        rotated = []
        rotated.extend(
            (self.rotated_lat_min, lon)
            for lon in values(self.rotated_lon_min, self.rotated_lon_max)
        )
        rotated.extend(
            (lat, self.rotated_lon_max)
            for lat in values(self.rotated_lat_min, self.rotated_lat_max)
        )
        rotated.extend(
            (self.rotated_lat_max, lon)
            for lon in values(self.rotated_lon_max, self.rotated_lon_min)
        )
        rotated.extend(
            (lat, self.rotated_lon_min)
            for lat in values(self.rotated_lat_max, self.rotated_lat_min)
        )
        rotated.append(rotated[0])
        return tuple(
            (lon, lat) for lat, lon in (
                self.to_geographic(rotated_lat, rotated_lon)
                for rotated_lat, rotated_lon in rotated
            )
        )


# The operational RLatLon0.09 grid metadata published by ECCC.  GeoMet reports
# a nearly global geographic bounding box because this rotated rectangle wraps
# across the antimeridian and around the Arctic.  Testing only that envelope
# incorrectly advertises RDPS in places such as the Philippines.  Validate in
# native rotated coordinates and expose the real curved perimeter to the map.
RDPS_ROTATED_DOMAIN = RotatedGridDomain(
    north_pole_lat=31.758312,
    north_pole_lon=87.597031,
    rotated_lon_min=-53.858588,
    rotated_lon_max=48.990594,
    rotated_lat_min=-48.805954,
    rotated_lat_max=45.464939,
)


_CAPABILITIES = {
    "gdps": GeoMetCapability(
        model_key="gdps",
        label="Canadian GDPS 15 km",
        provider="ECCC MSC GeoMet",
        layer_prefix="GDPS_15km",
        domain="Global",
        domain_bounds=(-180.0, 180.0, -90.0, 90.0),
        cycles=(0, 12),
        # Pressure-level GeoMet layers advertise a three-hour valid-time grid.
        forecast_hours=tuple(range(0, 241, 3)),
        pressure_levels=PRESSURE_LEVELS,
        omega_levels=(850, 700, 600, 500, 250, 200),
        fields=_REQUIRED_VARIABLES + ("VerticalVelocity",) + _SURFACE_VARIABLES,
        archive_window="server-advertised rolling reference-time window",
        transports=("wms-getfeatureinfo-point",),
    ),
    "rdps": GeoMetCapability(
        model_key="rdps",
        label="Canadian RDPS 10 km",
        provider="ECCC MSC GeoMet",
        layer_prefix="RDPS_10km",
        domain="North America and Arctic",
        # The envelope crosses the antimeridian. Precise acceptance uses
        # ``RDPS_ROTATED_DOMAIN`` below rather than this coarse map extent.
        domain_bounds=(-180.0, 180.0, -3.825, 90.0),
        cycles=(0, 6, 12, 18),
        forecast_hours=tuple(range(0, 85)),
        pressure_levels=PRESSURE_LEVELS,
        omega_levels=(850, 700, 500, 250),
        fields=_REQUIRED_VARIABLES + ("VerticalVelocity",) + _SURFACE_VARIABLES,
        archive_window="server-advertised rolling reference-time window",
        transports=("wms-getfeatureinfo-point",),
        domain_outline=RDPS_ROTATED_DOMAIN.outline(),
    ),
}

_ALIASES = {
    "gdps": "gdps",
    "gem-global": "gdps",
    "cmc-global": "gdps",
    "rdps": "rdps",
    "gem-regional": "rdps",
    "cmc-regional": "rdps",
}


@dataclass(frozen=True)
class _PointValue:
    variable: str
    level: int | None
    value: float
    selected_lat: float
    selected_lon: float
    valid_time: datetime
    reference_time: datetime


@dataclass
class GeoMetPointDataset:
    """Cacheable normalized point data returned by :func:`fetch_point`."""

    capability: GeoMetCapability
    columns: dict[str, np.ndarray]
    requested_lat: float
    requested_lon: float
    selected_lat: float
    selected_lon: float
    run_time: datetime
    valid_time: datetime
    fxx: int
    request_count: int
    max_workers: int
    surface_pressure_hpa: float
    below_ground_levels_removed: int
    surface_merged: bool = True

    def close(self):
        """Match the model-hour cache dataset protocol (there is no handle)."""


def available_models() -> tuple[GeoMetCapability, ...]:
    """Return all enabled ECCC point-provider capabilities."""
    return tuple(_CAPABILITIES.values())


def get_capability(model) -> GeoMetCapability:
    """Resolve an ECCC model key or alias."""
    if isinstance(model, GeoMetCapability):
        return model
    key = _ALIASES.get(str(model).strip().lower())
    if key is None:
        raise KeyError("unknown ECCC GeoMet model %r" % model)
    return _CAPABILITIES[key]


def point_in_domain(model, lat, lon) -> bool:
    """Return whether a point is inside the provider's actual model grid."""

    capability = get_capability(model)
    if capability.model_key == "rdps":
        return RDPS_ROTATED_DOMAIN.contains(lat, lon)
    try:
        lat = float(lat)
        lon = ((float(lon) + 180.0) % 360.0) - 180.0
    except (TypeError, ValueError):
        return False
    if not math.isfinite(lat) or not math.isfinite(lon):
        return False
    lon0, lon1, lat0, lat1 = capability.domain_bounds
    longitude_ok = lon0 <= lon <= lon1 if lon0 <= lon1 \
        else lon >= lon0 or lon <= lon1
    return lat0 <= lat <= lat1 and longitude_ok


def worker_count(value=None) -> int:
    """Return the bounded GeoMet point-request worker count (1 through 8)."""
    if value is None:
        value = os.environ.get("SHARPMOD_GEOMET_WORKERS", "4")
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(8, value))


def _as_utc(value) -> datetime:
    if isinstance(value, np.datetime64):
        value = value.astype("datetime64[us]").astype(datetime)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError("expected a datetime or ISO8601 string")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _floor_cycle(value, cycles) -> datetime:
    value = _as_utc(value)
    cycles = tuple(sorted(int(hour) for hour in cycles))
    earlier = [hour for hour in cycles if hour <= value.hour]
    if earlier:
        hour = earlier[-1]
    else:
        value -= timedelta(days=1)
        hour = cycles[-1]
    return value.replace(hour=hour, minute=0, second=0, microsecond=0)


def _deadline_passed(deadline) -> bool:
    return deadline is not None and time.monotonic() >= float(deadline)


def _timeout_within_deadline(timeout, deadline):
    if deadline is None:
        return timeout
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("GeoMet availability deadline expired")

    def clamp(value):
        return max(0.001, min(float(value), remaining))

    if timeout is None:
        return max(0.001, remaining)
    if isinstance(timeout, (tuple, list)):
        return tuple(clamp(value) for value in timeout)
    return clamp(timeout)


def _default_get(url, *, cancelled=None, deadline=None, **kwargs):
    """Issue one request, closing active I/O when cancellation is requested.

    ``requests.get`` is otherwise a blocking call: the surrounding executor
    cannot observe cancellation until its connect/read timeout expires.  GUI
    calls provide ``cancelled``, so use a private Session and a lightweight
    monitor that closes both the response and session while headers or content
    are being read.  The fully buffered response remains JSON/text-readable
    after the session is closed.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - core dependency in project
        raise RetrievalError("ECCC GeoMet extraction requires requests") from exc
    if cancelled is None and deadline is None:
        return requests.get(url, **kwargs)

    session = requests.Session()
    response_holder = []
    done = threading.Event()
    timed_out = threading.Event()

    def monitor():
        while not done.wait(0.05):
            requested = False
            if cancelled is not None:
                try:
                    requested = bool(cancelled())
                except Exception:
                    return
            deadline_reached = _deadline_passed(deadline)
            if not requested and not deadline_reached:
                continue
            if deadline_reached and not requested:
                timed_out.set()
            for response in list(response_holder):
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            try:
                session.close()
            except Exception:
                pass
            # Keep watching until the request exits: Session.get may return a
            # response just after the first close call.

    monitor_thread = threading.Thread(
        target=monitor,
        name="sharpmod-geomet-cancel",
        daemon=True,
    )
    monitor_thread.start()
    response = None
    try:
        response = session.get(url, stream=True, **kwargs)
        response_holder.append(response)
        # Buffer the small XML/JSON response while the cancellation monitor is
        # still active; Response.json()/text then use this cached content.
        _ = response.content
        if cancelled is not None and cancelled():
            raise DownloadCancelled("ECCC GeoMet extraction cancelled")
        if timed_out.is_set() or _deadline_passed(deadline):
            raise TimeoutError("GeoMet availability deadline expired")
        return response
    finally:
        done.set()
        monitor_thread.join(timeout=0.2)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        try:
            session.close()
        except Exception:
            pass


def _response_text(response) -> str:
    try:
        return str(response.text)
    except Exception:
        return ""


def _request(
    request_get,
    *,
    params,
    cancelled=None,
    retries=2,
    timeout=(10, 30),
    deadline=None,
):
    """Issue one retry-bounded GeoMet request."""
    last_error = None
    for attempt in range(max(0, int(retries)) + 1):
        if cancelled is not None and cancelled():
            raise DownloadCancelled("ECCC GeoMet extraction cancelled")
        if _deadline_passed(deadline):
            raise RetrievalError("GeoMet availability probe timed out")
        try:
            effective_timeout = _timeout_within_deadline(timeout, deadline)
            response = request_get(
                GEOMET_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=effective_timeout,
                **(
                    {"cancelled": cancelled, "deadline": deadline}
                    if request_get is _default_get
                    else {}
                ),
            )
            if _deadline_passed(deadline):
                raise RetrievalError("GeoMet availability probe timed out")
            status = int(getattr(response, "status_code", 0))
            if status == 200:
                return response
            message = _response_text(response).strip().replace("\n", " ")
            last_error = RetrievalError(
                "GeoMet returned HTTP %d: %s" % (status, message[:240])
            )
            if status not in {429, 500, 502, 503, 504}:
                raise last_error
        except DownloadCancelled:
            raise
        except RetrievalError:
            raise
        except Exception as exc:  # network exceptions are safe to retry
            if cancelled is not None and cancelled():
                raise DownloadCancelled(
                    "ECCC GeoMet extraction cancelled"
                ) from exc
            if _deadline_passed(deadline):
                raise RetrievalError(
                    "GeoMet availability probe timed out"
                ) from exc
            last_error = exc
        if attempt < int(retries):
            pause_until = time.monotonic() + min(
                1.0, 0.2 * (2 ** attempt)
            )
            if deadline is not None:
                pause_until = min(pause_until, float(deadline))
            while time.monotonic() < pause_until:
                if cancelled is not None and cancelled():
                    raise DownloadCancelled("ECCC GeoMet extraction cancelled")
                time.sleep(max(
                    0.0,
                    min(0.05, pause_until - time.monotonic()),
                ))
            if _deadline_passed(deadline):
                raise RetrievalError("GeoMet availability probe timed out")
    raise RetrievalError("GeoMet request failed: %s" % last_error) from last_error


def build_feature_info_params(
    capability,
    variable,
    level,
    lat,
    lon,
    valid_time,
    reference_time,
) -> dict[str, str]:
    """Build one WMS 1.3 point request with the required axis order."""
    capability = get_capability(capability)
    lat = float(lat)
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    if level is None:
        try:
            suffix = _SURFACE_LAYER_SUFFIX[str(variable)]
        except KeyError as exc:
            raise ValueError(
                "unknown GeoMet surface variable %r" % variable
            ) from exc
        layer = f"{capability.layer_prefix}_{suffix}"
    else:
        layer = "%s_%s_%dmb" % (
            capability.layer_prefix, str(variable), int(level)
        )
    # EPSG:4326 uses latitude,longitude axis order in WMS 1.3.  A 3x3
    # request with the middle pixel avoids boundary ambiguity at exact grid
    # coordinates while GetFeatureInfo still returns only one nearest value.
    margin = 0.20
    return {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "STYLES": "",
        "CRS": "EPSG:4326",
        "BBOX": "%.6f,%.6f,%.6f,%.6f" % (
            max(-90.0, lat - margin),
            max(-180.0, lon - margin),
            min(90.0, lat + margin),
            min(180.0, lon + margin),
        ),
        "WIDTH": "3",
        "HEIGHT": "3",
        "I": "1",
        "J": "1",
        "INFO_FORMAT": "application/json",
        "FEATURE_COUNT": "1",
        "TIME": _iso(valid_time),
        "DIM_REFERENCE_TIME": _iso(reference_time),
    }


def _fetch_value(
    capability,
    variable,
    level,
    lat,
    lon,
    valid_time,
    reference_time,
    *,
    request_get,
    cancelled=None,
):
    params = build_feature_info_params(
        capability, variable, level, lat, lon, valid_time, reference_time
    )
    response = _request(
        request_get,
        params=params,
        cancelled=cancelled,
    )
    try:
        payload = response.json()
        feature = payload["features"][0]
        props = feature["properties"]
        coords = feature["geometry"]["coordinates"]
        value = float(props["value"])
        selected_lon = float(coords[0])
        selected_lat = float(coords[1])
        returned_valid = _as_utc(props["time"])
        returned_reference = _as_utc(props["dim_reference_time"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        layer = params["LAYERS"]
        raise RetrievalError(
            "GeoMet returned no usable point value for %s" % layer
        ) from exc
    if not math.isfinite(value):
        raise RetrievalError(
            "GeoMet returned a non-finite value for %s" % params["LAYERS"]
        )
    if returned_valid != _as_utc(valid_time):
        raise RetrievalError(
            "GeoMet selected valid time %s instead of %s"
            % (_iso(returned_valid), _iso(valid_time))
        )
    if returned_reference != _as_utc(reference_time):
        raise RetrievalError(
            "GeoMet selected run %s instead of %s"
            % (_iso(returned_reference), _iso(reference_time))
        )
    return _PointValue(
        variable=str(variable),
        level=(int(level) if level is not None else None),
        value=value,
        selected_lat=selected_lat,
        selected_lon=((selected_lon + 180.0) % 360.0) - 180.0,
        valid_time=returned_valid,
        reference_time=returned_reference,
    )


def _capabilities_params(capability) -> dict[str, str]:
    layer = "%s_AirTemp_850mb" % capability.layer_prefix
    return {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetCapabilities",
        # GeoMet's layer-specific extension avoids the 38 MB full document.
        "LAYERS": layer,
    }


def _dimension_defaults(xml_text: str) -> dict[str, str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise RetrievalError("GeoMet returned invalid capabilities XML") from exc
    values = {}
    for element in root.iter():
        if str(element.tag).rsplit("}", 1)[-1] != "Dimension":
            continue
        name = element.attrib.get("name")
        default = element.attrib.get("default")
        if name and default:
            values[str(name)] = str(default)
    return values


def latest_reference_time(
    model,
    *,
    request_get=None,
    cancelled=None,
    timeout=(10, 30),
    retries=2,
    deadline=None,
) -> datetime:
    """Return the latest run advertised by layer-specific capabilities."""
    capability = get_capability(model)
    response = _request(
        request_get or _default_get,
        params=_capabilities_params(capability),
        cancelled=cancelled,
        timeout=timeout,
        retries=retries,
        deadline=deadline,
    )
    defaults = _dimension_defaults(_response_text(response))
    value = defaults.get("reference_time")
    if not value:
        raise RetrievalError("GeoMet capabilities omit reference_time")
    return _as_utc(value)


def _emit(progress_callback, stage, total=0):
    if progress_callback is not None:
        progress_callback(str(stage), max(0, int(total or 0)))










__all__ = [
    "GEOMET_URL",
    "GeoMetCapability",
    "GeoMetPointDataset",
    "available_models",
    "build_feature_info_params",
    "extract",
    "fetch_point",
    "get_capability",
    "latest_reference_time",
    "probe",
    "worker_count",
    "write_point_dataset",
]


from sharpmod.providers.eccc_geomet_runtime import (  # noqa: E402
    fetch_point,
    write_point_dataset,
    extract,
    probe
)
