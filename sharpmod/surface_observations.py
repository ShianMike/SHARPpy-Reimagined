"""Nearby time-matched surface observations from the documented NWS API.

The NWS point response links to the observation-station collection for that
forecast grid.  This adapter follows those links, then requests a narrow UTC
window for each nearby station.  It retains the *actual* observation time and
never converts a missing wind or dewpoint into zero.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlencode

from sharpmod.map_overlays import OverlayShape, build_layer


SURFACE_OVERLAY_KEY = "surface_observations"
ATTRIBUTION = "NOAA/NWS API surface observations"
NWS_API = "https://api.weather.gov"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_STATIONS = 32
MAX_WORKERS = 6
DEFAULT_TOLERANCE = timedelta(minutes=60)
STALE_OFFSET = timedelta(minutes=30)


class SurfaceObservationError(RuntimeError):
    """A provider, format, matching, or persistence error safe for the UI."""


class SurfaceObservationCancelled(SurfaceObservationError):
    """A surface-observation request was superseded."""


def _utc(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise SurfaceObservationError(f"invalid observation time {value!r}") from exc
    if parsed.tzinfo is None:
        raise SurfaceObservationError("surface-observation times must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value) -> str | None:
    parsed = _utc(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else bool(cancel)


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    radius = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(
        dlon / 2.0
    ) ** 2
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(a)))


def _quantity(value, target: str) -> float | None:
    """Convert an NWS ``QuantitativeValue`` to the requested display unit."""

    if not isinstance(value, Mapping):
        return None
    number = _finite(value.get("value"))
    if number is None:
        return None
    unit = str(value.get("unitCode") or "").rsplit(":", 1)[-1]
    unit = unit.replace("_", "").lower()
    if target == "degC":
        if "degc" in unit or unit in {"c", "celsius"}:
            return number
        if "degf" in unit or unit in {"f", "fahrenheit"}:
            return (number - 32.0) * 5.0 / 9.0
        if unit in {"k", "kelvin"}:
            return number - 273.15
    elif target == "kt":
        if "m/s" in unit or "ms-1" in unit or "ms-1" in unit.replace("^", ""):
            return number * 1.9438444924406
        if "kmh-1" in unit or "km/h" in unit:
            return number * 0.539956803
        if "kt" in unit or "knot" in unit:
            return number
        if "mih-1" in unit or "mph" in unit:
            return number * 0.868976242
    elif target == "deg":
        if "degree" in unit or unit in {"deg", "degree(angle)"}:
            return number % 360.0
    elif target == "m":
        if unit in {"m", "meter", "metre"}:
            return number
        if "ft" in unit or "foot" in unit:
            return number * 0.3048
    return None


@dataclass(frozen=True)
class SurfaceStation:
    station_id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None = None
    source_url: str = ""

    def __post_init__(self) -> None:
        station_id = str(self.station_id).strip().upper()
        lat = _finite(self.latitude)
        lon = _finite(self.longitude)
        if not station_id or lat is None or lon is None:
            raise SurfaceObservationError("surface station identity is incomplete")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise SurfaceObservationError("surface station coordinate is out of range")
        object.__setattr__(self, "station_id", station_id)
        object.__setattr__(self, "latitude", lat)
        object.__setattr__(self, "longitude", lon)


@dataclass(frozen=True)
class SurfaceObservation:
    station: SurfaceStation
    observed_at: datetime
    temperature_c: float | None
    dewpoint_c: float | None
    wind_direction_deg: float | None
    wind_speed_kt: float | None
    wind_gust_kt: float | None = None
    text_description: str = ""
    source_url: str = ""

    def __post_init__(self) -> None:
        when = _utc(self.observed_at)
        if when is None:
            raise SurfaceObservationError("surface observation has no UTC timestamp")
        object.__setattr__(self, "observed_at", when)
        for name in (
            "temperature_c",
            "dewpoint_c",
            "wind_direction_deg",
            "wind_speed_kt",
            "wind_gust_kt",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name)))

    @property
    def wind_uv_kt(self) -> tuple[float | None, float | None]:
        if self.wind_direction_deg is None or self.wind_speed_kt is None:
            return None, None
        angle = math.radians(self.wind_direction_deg)
        return -self.wind_speed_kt * math.sin(angle), -self.wind_speed_kt * math.cos(
            angle
        )

    def offset_seconds(self, requested_time) -> float:
        return (self.observed_at - _utc(requested_time)).total_seconds()

    def is_stale(
        self, requested_time, *, threshold: timedelta = STALE_OFFSET
    ) -> bool:
        return abs(self.offset_seconds(requested_time)) > threshold.total_seconds()

    def as_dict(self) -> dict[str, object]:
        return {
            "station": {
                **self.station.__dict__,
            },
            "observed_at": _iso(self.observed_at),
            "temperature_c": self.temperature_c,
            "dewpoint_c": self.dewpoint_c,
            "wind_direction_deg": self.wind_direction_deg,
            "wind_speed_kt": self.wind_speed_kt,
            "wind_gust_kt": self.wind_gust_kt,
            "text_description": self.text_description,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        station = value.get("station")
        if not isinstance(station, Mapping):
            raise SurfaceObservationError("surface observation station is malformed")
        try:
            return cls(
                SurfaceStation(**dict(station)),
                _utc(value.get("observed_at")),
                value.get("temperature_c"),
                value.get("dewpoint_c"),
                value.get("wind_direction_deg"),
                value.get("wind_speed_kt"),
                value.get("wind_gust_kt"),
                str(value.get("text_description") or ""),
                str(value.get("source_url") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise SurfaceObservationError(
                f"surface observation is malformed: {exc}"
            ) from exc


@dataclass(frozen=True)
class SurfaceObservationSet:
    requested_time: datetime
    centre_latitude: float
    centre_longitude: float
    observations: tuple[SurfaceObservation, ...]
    discovered_station_count: int
    unmatched_station_ids: tuple[str, ...] = ()
    retrieved_at: datetime | None = None
    source_url: str = ""

    def __post_init__(self) -> None:
        requested = _utc(self.requested_time)
        if requested is None:
            raise SurfaceObservationError("surface observation set needs a UTC time")
        object.__setattr__(self, "requested_time", requested)
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(
            self, "retrieved_at", _utc(self.retrieved_at) or datetime.now(timezone.utc)
        )

    @property
    def stale_count(self) -> int:
        return sum(item.is_stale(self.requested_time) for item in self.observations)

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "sharpmod-surface-observations",
            "version": 1,
            "requested_time": _iso(self.requested_time),
            "centre_latitude": self.centre_latitude,
            "centre_longitude": self.centre_longitude,
            "discovered_station_count": self.discovered_station_count,
            "unmatched_station_ids": list(self.unmatched_station_ids),
            "retrieved_at": _iso(self.retrieved_at),
            "source_url": self.source_url,
            "attribution": ATTRIBUTION,
            "observations": [item.as_dict() for item in self.observations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]):
        if value.get("format") != "sharpmod-surface-observations" or int(
            value.get("version", -1)
        ) != 1:
            raise SurfaceObservationError("unsupported surface-observation document")
        return cls(
            _utc(value.get("requested_time")),
            float(value.get("centre_latitude")),
            float(value.get("centre_longitude")),
            tuple(
                SurfaceObservation.from_dict(item)
                for item in value.get("observations", ())
                if isinstance(item, Mapping)
            ),
            int(value.get("discovered_station_count", 0)),
            tuple(str(item) for item in value.get("unmatched_station_ids", ())),
            _utc(value.get("retrieved_at")),
            str(value.get("source_url") or ""),
        )


def parse_stations(payload: Mapping[str, object]) -> tuple[SurfaceStation, ...]:
    stations = []
    for feature in payload.get("features", ()) if isinstance(payload, Mapping) else ():
        if not isinstance(feature, Mapping):
            continue
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, Mapping) or not isinstance(properties, Mapping):
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
            continue
        station_id = properties.get("stationIdentifier")
        if not station_id:
            continue
        elevation = _quantity(properties.get("elevation"), "m")
        try:
            stations.append(
                SurfaceStation(
                    str(station_id),
                    str(properties.get("name") or station_id),
                    float(coordinates[1]),
                    float(coordinates[0]),
                    elevation,
                    str(feature.get("id") or ""),
                )
            )
        except (SurfaceObservationError, TypeError, ValueError):
            continue
    return tuple(stations)


def parse_observations(
    payload: Mapping[str, object], station: SurfaceStation
) -> tuple[SurfaceObservation, ...]:
    observations = []
    for feature in payload.get("features", ()) if isinstance(payload, Mapping) else ():
        if not isinstance(feature, Mapping):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, Mapping):
            continue
        try:
            when = _utc(properties.get("timestamp"))
        except SurfaceObservationError:
            continue
        if when is None:
            continue
        observations.append(
            SurfaceObservation(
                station,
                when,
                _quantity(properties.get("temperature"), "degC"),
                _quantity(properties.get("dewpoint"), "degC"),
                _quantity(properties.get("windDirection"), "deg"),
                _quantity(properties.get("windSpeed"), "kt"),
                _quantity(properties.get("windGust"), "kt"),
                str(properties.get("textDescription") or ""),
                str(feature.get("id") or ""),
            )
        )
    return tuple(sorted(observations, key=lambda item: item.observed_at))


def select_observation(observations, when, *, tolerance=DEFAULT_TOLERANCE):
    requested = _utc(when)
    values = tuple(observations)
    if requested is None or not values:
        return None
    selected = min(
        values,
        key=lambda item: (
            abs((item.observed_at - requested).total_seconds()),
            item.observed_at,
        ),
    )
    return (
        selected
        if abs(selected.observed_at - requested) <= tolerance
        else None
    )


def _response_json(response) -> Mapping[str, object]:
    if isinstance(response, Mapping):
        return response
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    if isinstance(response, (bytes, bytearray)):
        raw = bytes(response)
    else:
        raw = getattr(response, "content", None)
        if raw is None:
            reader = getattr(response, "read", None)
            raw = reader() if callable(reader) else None
    if raw is not None:
        raw = bytes(raw)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SurfaceObservationError("NWS API response exceeds the safety limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SurfaceObservationError(f"NWS API returned invalid JSON: {exc}") from exc
    else:
        decoder = getattr(response, "json", None)
        value = decoder() if callable(decoder) else None
    if not isinstance(value, Mapping):
        raise SurfaceObservationError("NWS API response root is not an object")
    return value


def _open_json(url: str, *, timeout: float, opener=None):
    if opener is None:
        import requests

        response = requests.get(
            url,
            headers={"User-Agent": "SHARPpy-Reimagined/1.2"},
            timeout=timeout,
        )
    else:
        try:
            response = opener(url, timeout=timeout)
        except TypeError:
            response = opener(url)
    return _response_json(response)


def fetch_surface_observations(
    latitude: float,
    longitude: float,
    when: datetime,
    *,
    tolerance: timedelta = DEFAULT_TOLERANCE,
    max_stations: int = 24,
    workers: int = MAX_WORKERS,
    timeout: float = 15.0,
    opener=None,
    cancel=None,
) -> SurfaceObservationSet:
    """Fetch nearby stations and match each to the explicit requested time."""

    lat = _finite(latitude)
    lon = _finite(longitude)
    requested = _utc(when)
    if lat is None or lon is None or requested is None:
        raise SurfaceObservationError("surface context needs a location and UTC time")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise SurfaceObservationError("surface context location is out of range")
    if _cancelled(cancel):
        raise SurfaceObservationCancelled("surface-observation request cancelled")
    point_url = f"{NWS_API}/points/{lat:.4f},{lon:.4f}"
    point = _open_json(point_url, timeout=timeout, opener=opener)
    properties = point.get("properties")
    stations_url = (
        properties.get("observationStations")
        if isinstance(properties, Mapping)
        else None
    )
    if not isinstance(stations_url, str) or not stations_url.startswith(NWS_API):
        raise SurfaceObservationError(
            "NWS does not publish an observation-station collection for this point"
        )
    station_payload = _open_json(stations_url, timeout=timeout, opener=opener)
    discovered = parse_stations(station_payload)
    ordered = sorted(
        discovered,
        key=lambda item: _distance_km(
            lat, lon, item.latitude, item.longitude
        ),
    )[: min(MAX_STATIONS, max(1, int(max_stations)))]
    start = requested - tolerance
    end = requested + tolerance
    query = urlencode({"start": _iso(start), "end": _iso(end), "limit": 50})

    def load(station):
        if _cancelled(cancel):
            raise SurfaceObservationCancelled(
                "surface-observation request cancelled"
            )
        url = f"{NWS_API}/stations/{station.station_id}/observations?{query}"
        payload = _open_json(url, timeout=timeout, opener=opener)
        return station, select_observation(
            parse_observations(payload, station), requested, tolerance=tolerance
        )

    matched = []
    unmatched = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, int(workers)))) as pool:
        futures = {pool.submit(load, station): station for station in ordered}
        for future in as_completed(futures):
            station = futures[future]
            if _cancelled(cancel):
                for pending in futures:
                    pending.cancel()
                raise SurfaceObservationCancelled(
                    "surface-observation request cancelled"
                )
            try:
                _station, observation = future.result()
            except SurfaceObservationCancelled:
                raise
            except Exception as exc:  # one station does not blank all neighbours
                failures.append(f"{station.station_id}: {exc}")
                unmatched.append(station.station_id)
                continue
            if observation is None:
                unmatched.append(station.station_id)
            else:
                matched.append(observation)
    matched.sort(
        key=lambda item: _distance_km(
            lat,
            lon,
            item.station.latitude,
            item.station.longitude,
        )
    )
    if not matched and failures:
        raise SurfaceObservationError(
            "nearby surface observations were unavailable: " + failures[0]
        )
    return SurfaceObservationSet(
        requested,
        lat,
        lon,
        tuple(matched),
        len(discovered),
        tuple(unmatched),
        datetime.now(timezone.utc),
        stations_url,
    )


def declutter_observations(
    observations,
    bounds,
    *,
    pixel_size=(900, 600),
    minimum_spacing_px: int = 46,
    max_stations: int = 24,
) -> tuple[SurfaceObservation, ...]:
    """Greedily retain nearby station plots in screen-space grid cells."""

    lon0, lon1, lat0, lat1 = (float(item) for item in bounds)
    width, height = (max(1, int(item)) for item in pixel_size)
    cell = max(10, int(minimum_spacing_px))
    occupied = []
    kept = []
    for observation in observations:
        lon = observation.station.longitude
        lat = observation.station.latitude
        if not (lon0 <= lon <= lon1 and lat0 <= lat <= lat1):
            continue
        x = (lon - lon0) / max(1e-9, lon1 - lon0) * width
        y = (lat1 - lat) / max(1e-9, lat1 - lat0) * height
        if any(math.hypot(x - old_x, y - old_y) < cell for old_x, old_y in occupied):
            continue
        occupied.append((x, y))
        kept.append(observation)
        if len(kept) >= max(1, int(max_stations)):
            break
    return tuple(kept)


def surface_overlay_layer(
    dataset: SurfaceObservationSet,
    bounds,
    *,
    pixel_size=(900, 600),
    minimum_spacing_px: int = 46,
    max_stations: int = 24,
):
    """Build decluttered station plots for the existing vector-overlay map."""

    shown = declutter_observations(
        dataset.observations,
        bounds,
        pixel_size=pixel_size,
        minimum_spacing_px=minimum_spacing_px,
        max_stations=max_stations,
    )
    lon0, lon1, lat0, lat1 = (float(item) for item in bounds)
    # The polygon is only the hit/dot footprint; wind staff and labels are drawn
    # in screen pixels by the map so they stay readable through navigation.
    radius_lon = max(0.018, abs(lon1 - lon0) * 3.5 / max(200.0, pixel_size[0]))
    radius_lat = max(0.018, abs(lat1 - lat0) * 3.5 / max(150.0, pixel_size[1]))
    shapes = []
    for observation in shown:
        station = observation.station
        points = []
        for index in range(13):
            angle = math.tau * index / 12.0
            points.append(
                (
                    station.longitude + radius_lon * math.cos(angle),
                    station.latitude + radius_lat * math.sin(angle),
                )
            )
        u, v = observation.wind_uv_kt
        offset = observation.offset_seconds(dataset.requested_time) / 60.0
        temperature = (
            f"{observation.temperature_c:.1f} °C"
            if observation.temperature_c is not None
            else "missing"
        )
        dewpoint = (
            f"{observation.dewpoint_c:.1f} °C"
            if observation.dewpoint_c is not None
            else "missing"
        )
        wind = (
            f"{observation.wind_direction_deg:.0f}° at "
            f"{observation.wind_speed_kt:.0f} kt"
            if observation.wind_direction_deg is not None
            and observation.wind_speed_kt is not None
            else "missing"
        )
        # The gust was being fetched and then dropped: it reached neither the plot
        # nor this description, so the one reading that distinguishes a benign
        # surface wind from an outflow-driven one was unreachable in the GUI.
        if observation.wind_gust_kt is not None:
            wind += f", gusting {observation.wind_gust_kt:.0f} kt"
        stale = observation.is_stale(dataset.requested_time)
        description = (
            f"{station.station_id} — {station.name}\n"
            f"Observed {observation.observed_at:%Y-%m-%d %H:%M:%SZ} "
            f"({offset:+.0f} min from requested)\n"
            f"Temperature {temperature}; dewpoint {dewpoint}; wind {wind}"
        )
        if stale:
            description += "\nStale/mismatched: more than 30 minutes from requested time"
        shapes.append(
            OverlayShape(
                rings=(tuple(points),),
                bounds=(
                    station.longitude - radius_lon,
                    station.longitude + radius_lon,
                    station.latitude - radius_lat,
                    station.latitude + radius_lat,
                ),
                stroke="#16212b" if not stale else "#8a4b20",
                fill="#f4f7fa" if not stale else "#f5c17d",
                label=station.station_id,
                description=description,
                marker=True,
                station_id=station.station_id,
                station_longitude=station.longitude,
                station_latitude=station.latitude,
                temperature_c=observation.temperature_c,
                dewpoint_c=observation.dewpoint_c,
                wind_u_kt=u,
                wind_v_kt=v,
                wind_gust_kt=observation.wind_gust_kt,
                stale=stale,
            )
        )
    subtitle = (
        f"{len(shown)} displayed / {len(dataset.observations)} time-matched / "
        f"{dataset.discovered_station_count} nearby stations · requested "
        f"{dataset.requested_time:%Y-%m-%d %H:%MZ}"
    )
    if dataset.stale_count:
        subtitle += f" · {dataset.stale_count} stale/mismatched"
    return build_layer(
        SURFACE_OVERLAY_KEY,
        "Surface observations",
        shapes,
        subtitle=subtitle,
        short_name="T/Td/wind",
        valid_from=dataset.requested_time - DEFAULT_TOLERANCE,
        valid_to=dataset.requested_time + DEFAULT_TOLERANCE,
        issued=dataset.retrieved_at,
        source_url=dataset.source_url,
        attribution=ATTRIBUTION,
        legend=False,
    )


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_surface_observations(path, dataset: SurfaceObservationSet) -> Path:
    return _atomic_json(Path(path), dataset.as_dict())


def read_surface_observations(path) -> SurfaceObservationSet:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceObservationError(
            f"surface-observation document could not be read: {exc}"
        ) from exc
    if not isinstance(value, Mapping):
        raise SurfaceObservationError("surface-observation document is malformed")
    return SurfaceObservationSet.from_dict(value)


def export_surface_csv(path, dataset: SurfaceObservationSet) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "station_id",
                    "station_name",
                    "latitude_deg",
                    "longitude_deg",
                    "elevation_m",
                    "requested_time_utc",
                    "observed_time_utc",
                    "offset_minutes",
                    "temperature_c",
                    "dewpoint_c",
                    "wind_direction_deg",
                    "wind_speed_kt",
                    "wind_gust_kt",
                    "stale_or_mismatched",
                    "source_url",
                )
            )
            for observation in dataset.observations:
                station = observation.station
                writer.writerow(
                    (
                        station.station_id,
                        station.name,
                        station.latitude,
                        station.longitude,
                        station.elevation_m,
                        _iso(dataset.requested_time),
                        _iso(observation.observed_at),
                        observation.offset_seconds(dataset.requested_time) / 60.0,
                        observation.temperature_c,
                        observation.dewpoint_c,
                        observation.wind_direction_deg,
                        observation.wind_speed_kt,
                        observation.wind_gust_kt,
                        observation.is_stale(dataset.requested_time),
                        observation.source_url,
                    )
                )
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


__all__ = [
    "ATTRIBUTION",
    "DEFAULT_TOLERANCE",
    "NWS_API",
    "STALE_OFFSET",
    "SURFACE_OVERLAY_KEY",
    "SurfaceObservation",
    "SurfaceObservationCancelled",
    "SurfaceObservationError",
    "SurfaceObservationSet",
    "SurfaceStation",
    "declutter_observations",
    "export_surface_csv",
    "fetch_surface_observations",
    "parse_observations",
    "parse_stations",
    "read_surface_observations",
    "select_observation",
    "surface_overlay_layer",
    "write_surface_observations",
]
