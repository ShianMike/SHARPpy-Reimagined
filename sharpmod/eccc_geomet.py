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

from sharpmod.model_transport import DownloadCancelled
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
        fields=_REQUIRED_VARIABLES + ("VerticalVelocity",),
        archive_window="server-advertised rolling reference-time window",
        transports=("wms-getfeatureinfo-point",),
    ),
    "rdps": GeoMetCapability(
        model_key="rdps",
        label="Canadian RDPS 10 km",
        provider="ECCC MSC GeoMet",
        layer_prefix="RDPS_10km",
        domain="North America and Arctic",
        # The rotated grid crosses the antimeridian; this is the GeoMet layer
        # bounding box.  A missing feature still produces a clear domain error.
        domain_bounds=(-180.0, 180.0, -3.825, 90.0),
        cycles=(0, 6, 12, 18),
        forecast_hours=tuple(range(0, 85)),
        pressure_levels=PRESSURE_LEVELS,
        omega_levels=(850, 700, 500, 250),
        fields=_REQUIRED_VARIABLES + ("VerticalVelocity",),
        archive_window="server-advertised rolling reference-time window",
        transports=("wms-getfeatureinfo-point",),
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
    level: int
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


def _default_get(url, *, cancelled=None, **kwargs):
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
    if cancelled is None:
        return requests.get(url, **kwargs)

    session = requests.Session()
    response_holder = []
    done = threading.Event()

    def monitor():
        while not done.wait(0.05):
            try:
                requested = bool(cancelled())
            except Exception:
                return
            if not requested:
                continue
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
        if cancelled():
            raise DownloadCancelled("ECCC GeoMet extraction cancelled")
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
):
    """Issue one retry-bounded GeoMet request."""
    last_error = None
    for attempt in range(max(0, int(retries)) + 1):
        if cancelled is not None and cancelled():
            raise DownloadCancelled("ECCC GeoMet extraction cancelled")
        try:
            response = request_get(
                GEOMET_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                **({"cancelled": cancelled} if request_get is _default_get else {}),
            )
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
            last_error = exc
        if attempt < int(retries):
            deadline = time.monotonic() + min(1.0, 0.2 * (2 ** attempt))
            while time.monotonic() < deadline:
                if cancelled is not None and cancelled():
                    raise DownloadCancelled("ECCC GeoMet extraction cancelled")
                time.sleep(min(0.05, deadline - time.monotonic()))
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
        level=int(level),
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


def latest_reference_time(model, *, request_get=None, cancelled=None) -> datetime:
    """Return the latest run advertised by layer-specific capabilities."""
    capability = get_capability(model)
    response = _request(
        request_get or _default_get,
        params=_capabilities_params(capability),
        cancelled=cancelled,
    )
    defaults = _dimension_defaults(_response_text(response))
    value = defaults.get("reference_time")
    if not value:
        raise RetrievalError("GeoMet capabilities omit reference_time")
    return _as_utc(value)


def _emit(progress_callback, stage, total=0):
    if progress_callback is not None:
        progress_callback(str(stage), max(0, int(total or 0)))


def fetch_point(
    model,
    lat,
    lon,
    *,
    run_time=None,
    fxx=0,
    max_workers=None,
    request_get=None,
    progress_callback=None,
    cancelled=None,
) -> GeoMetPointDataset:
    """Fetch and normalize one GDPS/RDPS pressure-level point profile."""
    capability = get_capability(model)
    lat = float(lat)
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    lon0, lon1, lat0, lat1 = capability.domain_bounds
    if not -90.0 <= lat <= 90.0:
        raise ParameterRangeError("latitude %.4f is outside [-90, 90]" % lat)
    if not lat0 <= lat <= lat1 or not lon0 <= lon <= lon1:
        raise ParameterRangeError(
            "%s does not cover %.4f, %.4f" % (capability.label, lat, lon)
        )
    fxx = int(fxx)
    if fxx not in capability.forecast_hours:
        raise ParameterRangeError(
            "%s forecast hour F%03d is unavailable" % (capability.label, fxx)
        )
    request_get = request_get or _default_get
    _emit(progress_callback, "locating")
    run_dt = latest_reference_time(
        capability, request_get=request_get, cancelled=cancelled
    ) if run_time is None else _floor_cycle(run_time, capability.cycles)
    valid_dt = run_dt + timedelta(hours=fxx)

    tasks = [
        (variable, level)
        for level in capability.pressure_levels
        for variable in _REQUIRED_VARIABLES
    ]
    tasks.extend(
        ("VerticalVelocity", level) for level in capability.omega_levels
    )
    workers = worker_count(max_workers)
    _emit(progress_callback, "downloading")
    values = {name: {} for name in capability.fields}
    points = []
    errors = []
    lock = threading.Lock()

    def load(variable, level):
        return _fetch_value(
            capability,
            variable,
            level,
            lat,
            lon,
            valid_dt,
            run_dt,
            request_get=request_get,
            cancelled=cancelled,
        )

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="sharpmod-geomet",
    ) as executor:
        futures = {
            executor.submit(load, variable, level): (variable, level)
            for variable, level in tasks
        }
        for future in as_completed(futures):
            if cancelled is not None and cancelled():
                for pending in futures:
                    pending.cancel()
                raise DownloadCancelled("ECCC GeoMet extraction cancelled")
            variable, level = futures[future]
            try:
                point = future.result()
            except Exception as exc:
                # Vertical velocity is published only on a subset and remains
                # optional; thermodynamic and wind fields are required.
                if variable == "VerticalVelocity":
                    continue
                errors.append((variable, level, exc))
                continue
            with lock:
                values[variable][level] = point.value
                points.append(point)

    if errors:
        variable, level, exc = errors[0]
        raise RetrievalError(
            "%s point profile is incomplete at %d mb (%s): %s"
            % (capability.label, level, variable, exc)
        ) from exc
    if not points:
        raise RetrievalError("GeoMet returned no point data")

    selected_lat = float(np.median([point.selected_lat for point in points]))
    selected_lon = float(np.median([point.selected_lon for point in points]))
    if any(
        abs(point.selected_lat - selected_lat) > 0.25
        or abs(
            ((point.selected_lon - selected_lon + 180.0) % 360.0) - 180.0
        ) > 0.25
        for point in points
    ):
        raise RetrievalError("GeoMet layers selected inconsistent grid points")

    levels = np.asarray(capability.pressure_levels, dtype=float)
    tmpc = np.asarray(
        [values["AirTemp"][int(level)] for level in levels], dtype=float
    )
    hght = np.asarray(
        [values["GeopotentialHeight"][int(level)] for level in levels],
        dtype=float,
    )
    q = np.asarray(
        [values["SpecificHumidity"][int(level)] for level in levels],
        dtype=float,
    )
    wdir = np.asarray(
        [values["WindDir"][int(level)] for level in levels], dtype=float
    )
    speed_ms = np.asarray(
        [values["WindSpeed"][int(level)] for level in levels], dtype=float
    )
    radians = np.deg2rad(wdir)
    uwnd = -speed_ms * np.sin(radians)
    vwnd = -speed_ms * np.cos(radians)
    omeg = np.asarray(
        [values["VerticalVelocity"].get(int(level), np.nan) for level in levels],
        dtype=float,
    )
    n_levels = int(levels.size)
    columns = {
        "pres": _mark_missing(levels, n_levels),
        "hght": _mark_missing(hght, n_levels),
        "tmpc": _mark_missing(tmpc, n_levels),
        "dwpc": _mark_missing(
            dewpoint_from_specific_humidity(q, levels), n_levels
        ),
        "wdir": _mark_missing(wdir, n_levels),
        "wspd": _mark_missing(speed_ms * 1.94384449, n_levels),
        "omeg": _mark_missing(omeg, n_levels),
        "u": _mark_missing(uwnd, n_levels),
        "v": _mark_missing(vwnd, n_levels),
    }
    _emit(progress_callback, "extracting")
    return GeoMetPointDataset(
        capability=capability,
        columns=columns,
        requested_lat=lat,
        requested_lon=lon,
        selected_lat=selected_lat,
        selected_lon=selected_lon,
        run_time=run_dt,
        valid_time=valid_dt,
        fxx=fxx,
        request_count=len(tasks),
        max_workers=workers,
    )


def write_point_dataset(dataset, out_path, *, loc=None, progress_callback=None):
    """Atomically serialize one normalized GeoMet dataset to NPZ + JSON."""
    if not isinstance(dataset, GeoMetPointDataset):
        raise TypeError("dataset must be GeoMetPointDataset")
    capability = dataset.capability
    out_path = os.fspath(out_path)
    loc_label = loc or "%s %.2f, %.2f" % (
        capability.label, dataset.selected_lat, dataset.selected_lon
    )
    run_str = dataset.run_time.strftime("%Y-%m-%d %H:%M")
    valid_str = dataset.valid_time.strftime("%Y-%m-%d %H:%M")
    cols = dataset.columns
    arrays = {
        "pres": cols["pres"],
        "hght": cols["hght"],
        "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"],
        "wdir": cols["wdir"],
        "wspd": cols["wspd"],
        "omeg": cols["omeg"],
        "uwnd": cols["u"],
        "vwnd": cols["v"],
        "lat": dataset.selected_lat,
        "lon": dataset.selected_lon,
        "loc": loc_label,
        "model": capability.label,
        "run": run_str,
        "valid": valid_str,
        "fxx": dataset.fxx,
    }
    meta = {
        "model": capability.label,
        "model_key": capability.model_key,
        "loc": loc_label,
        "requested_lat": dataset.requested_lat,
        "requested_lon": dataset.requested_lon,
        "selected_lat": dataset.selected_lat,
        "selected_lon": dataset.selected_lon,
        "run": run_str,
        "valid": valid_str,
        "fxx": dataset.fxx,
        "npz": os.path.abspath(out_path),
        "levels": int(np.asarray(cols["pres"]).size),
        "provider": capability.provider,
        "source_url": GEOMET_URL,
        "transport": "wms-getfeatureinfo-point",
        "fields": list(capability.fields),
        "request_count": dataset.request_count,
        "max_workers": dataset.max_workers,
        "backend": "ECCC GeoMet point adapter",
        "decoder": "forecast pressure-level point values",
        "cache_hit": False,
    }
    if progress_callback is not None:
        _emit(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        _quiet_remove(out_path)
        raise
    _emit(progress_callback, "complete")
    return out_path


def extract(
    model,
    lat,
    lon,
    *,
    run_time=None,
    fxx=0,
    out_path=None,
    loc=None,
    dataset=None,
    max_workers=None,
    request_get=None,
    progress_callback=None,
    cancelled=None,
):
    """Extract an ECCC GeoMet point sounding to the portable NPZ format."""
    capability = get_capability(model)
    if dataset is None:
        dataset = fetch_point(
            capability,
            lat,
            lon,
            run_time=run_time,
            fxx=fxx,
            max_workers=max_workers,
            request_get=request_get,
            progress_callback=progress_callback,
            cancelled=cancelled,
        )
    else:
        if not isinstance(dataset, GeoMetPointDataset):
            raise TypeError("ECCC extraction dataset must be GeoMetPointDataset")
        if dataset.capability.model_key != capability.model_key:
            raise RetrievalError("cached GeoMet dataset belongs to another model")
        if int(fxx) != dataset.fxx:
            raise RetrievalError(
                "cached GeoMet dataset belongs to another forecast hour"
            )
        if run_time is not None and _floor_cycle(
            run_time, capability.cycles
        ) != dataset.run_time:
            raise RetrievalError("cached GeoMet dataset belongs to another run")
        if (
            abs(float(lat) - dataset.requested_lat) > 1.0e-6
            or abs(
                ((float(lon) - dataset.requested_lon + 180.0) % 360.0) - 180.0
            ) > 1.0e-6
        ):
            raise RetrievalError("cached GeoMet dataset belongs to another point")
    if out_path is None:
        out_path = "%s_point_%.2fN_%.2fE_%s_f%03d.npz" % (
            capability.model_key,
            float(lat),
            float(lon),
            dataset.run_time.strftime("%Y%m%d%H"),
            int(fxx),
        )
    if cancelled is not None and cancelled():
        raise DownloadCancelled("ECCC GeoMet extraction cancelled")
    return write_point_dataset(
        dataset, out_path, loc=loc, progress_callback=progress_callback
    )


def probe(model, run_time=None, fxx=0, *, request_get=None, cancelled=None):
    """Return a lightweight layer-capability availability probe."""
    capability = get_capability(model)
    result = {
        "model": capability.model_key,
        "label": capability.label,
        "fxx": int(fxx),
        "available": False,
        "subset_opened": False,
        "provider": capability.provider,
        "transport": "wms-getfeatureinfo-point",
    }
    try:
        latest = latest_reference_time(
            capability,
            request_get=request_get,
            cancelled=cancelled,
        )
        run_dt = latest if run_time is None else _floor_cycle(
            run_time, capability.cycles
        )
        result["run"] = run_dt.strftime("%Y-%m-%d %H:%M")
        result["valid"] = (run_dt + timedelta(hours=int(fxx))).strftime(
            "%Y-%m-%d %H:%M"
        )
        result["latest_run"] = latest.strftime("%Y-%m-%d %H:%M")
        result["inventory_rows"] = (
            len(capability.pressure_levels) * len(_REQUIRED_VARIABLES)
            + len(capability.omega_levels)
        )
        result["available"] = (
            int(fxx) in capability.forecast_hours
            and run_dt <= latest
            and run_dt >= latest - timedelta(days=2)
        )
        result["grib"] = GEOMET_URL
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


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
