"""Provider-neutral observed-sounding retrieval.

The provider boundary in this module is deliberately independent of Qt and of
the command-line front ends.  A request is satisfied by exactly one provider;
fallback never combines levels from separate archives.  The selected provider,
station identifier, request URL, and any failed earlier providers are retained
in both the returned profile metadata and the portable ``.npz`` output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import socket
import ssl
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

try:
    import certifi

    _CA_FILE = certifi.where()
except Exception:  # pragma: no cover - certifi is a runtime dependency
    _CA_FILE = None

from sharpmod.io.uwyo_decoder import (
    RetrievalError as UWyoRetrievalError,
    SoundingParseError as UWyoSoundingParseError,
    StationLookupError as UWyoStationLookupError,
    StationTimeUnavailableError as UWyoStationTimeUnavailableError,
    UWyo_Decoder,
)
from sharpmod.tools.era5_extract import _atomic_write_json, _atomic_write_npz


MISSING = -9999.0
DEFAULT_PROVIDER_ORDER = ("uwyo", "iem")


class ObservedProviderError(Exception):
    """Base class for a failure from one observed-sounding provider."""


class ObservedStationError(ObservedProviderError):
    """The requested station cannot be resolved by a provider."""


class ObservedUnavailableError(ObservedProviderError):
    """A provider has no report for the requested station and time."""


class ObservedRetrievalError(ObservedProviderError):
    """A provider could not be reached over its public API."""


class ObservedParseError(ObservedProviderError):
    """A provider returned a response that is not a usable sounding."""


class ObservedFallbackError(ObservedProviderError):
    """Every explicitly requested provider failed."""

    def __init__(self, attempts: Sequence[Mapping[str, str]]):
        self.attempts = tuple(dict(item) for item in attempts)
        details = "; ".join(
            f"{item['provider']}: {item['error']}" for item in self.attempts
        )
        super().__init__(f"all observed-sounding providers failed ({details})")


@dataclass(frozen=True)
class ObservedProviderInfo:
    """Stable provider identity exposed to CLIs and future GUI consumers."""

    key: str
    name: str
    homepage: str


@dataclass(frozen=True)
class ObservedSounding:
    """One sounding fetched wholly from one provider."""

    profile: object
    provider: str
    provider_name: str
    station_id: str
    requested_station: str
    valid: datetime
    source_url: str
    metadata: Mapping[str, object]


@runtime_checkable
class ObservedSoundingProvider(Protocol):
    """Contract implemented by a single, non-merging sounding source."""

    info: ObservedProviderInfo

    def fetch(self, station: str, when_utc: datetime) -> ObservedSounding:
        """Fetch one station/time report or raise ``ObservedProviderError``."""


def _utc_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("observation time must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _set_profile_metadata(profile, metadata: Mapping[str, object]) -> None:
    current = dict(getattr(profile, "meta", {}) or {})
    current.update(metadata)
    profile.meta = current


class UWyoObservedProvider:
    """University of Wyoming provider adapter around ``UWyo_Decoder``."""

    info = ObservedProviderInfo(
        key="uwyo",
        name="University of Wyoming Upper Air Archive",
        homepage="https://weather.uwyo.edu/upperair/",
    )

    def __init__(self, decoder: UWyo_Decoder | None = None):
        self._decoder = decoder or UWyo_Decoder(full_catalog=True)

    def fetch(self, station: str, when_utc: datetime) -> ObservedSounding:
        when = _utc_datetime(when_utc)
        requested = str(station).strip()
        try:
            station_meta = self._decoder.resolve_station(requested)
        except UWyoStationLookupError as exc:
            raise ObservedStationError(str(exc)) from exc

        source_code = self._decoder._src.get(  # provider-owned catalogue data
            station_meta.id, self._decoder.DEFAULT_SRC
        )
        source_url = self._decoder._build_url(
            station_meta.id, when.replace(tzinfo=None), source_code
        )
        try:
            profile = self._decoder.fetch(
                station_meta.id, when.replace(tzinfo=None), src=source_code
            )
        except UWyoStationTimeUnavailableError as exc:
            raise ObservedUnavailableError(str(exc)) from exc
        except UWyoRetrievalError as exc:
            raise ObservedRetrievalError(str(exc)) from exc
        except UWyoSoundingParseError as exc:
            raise ObservedParseError(str(exc)) from exc

        profile_meta = dict(getattr(profile, "meta", {}) or {})
        valid = profile_meta.get("valid")
        valid = _utc_datetime(valid) if isinstance(valid, datetime) else when
        lat = profile_meta.get("lat", station_meta.lat)
        lon = profile_meta.get("lon", station_meta.lon)
        if not isinstance(lat, (int, float)) or not math.isfinite(float(lat)):
            lat = station_meta.lat
        if not isinstance(lon, (int, float)) or not math.isfinite(float(lon)):
            lon = station_meta.lon
        metadata = {
            "observed": True,
            "model": "Observed",
            "source": self.info.key,
            "source_provider": self.info.key,
            "source_provider_name": self.info.name,
            "source_station": station_meta.id,
            "requested_station": requested,
            "source_url": source_url,
            "station_name": station_meta.name,
            "lat": float(lat),
            "lon": float(lon),
            "valid": valid,
            "run": valid,
            "fxx": 0,
        }
        _set_profile_metadata(profile, metadata)
        return ObservedSounding(
            profile=profile,
            provider=self.info.key,
            provider_name=self.info.name,
            station_id=station_meta.id,
            requested_station=requested,
            valid=valid,
            source_url=source_url,
            metadata=dict(getattr(profile, "meta", {}) or {}),
        )


class IEMObservedProvider:
    """Iowa Environmental Mesonet public RAOB JSON archive adapter."""

    info = ObservedProviderInfo(
        key="iem",
        name="Iowa Environmental Mesonet RAOB Archive",
        homepage="https://mesonet.agron.iastate.edu/archive/raob/",
    )
    NETWORK_URL = "https://mesonet.agron.iastate.edu/api/1/network/RAOB.json"
    SOUNDING_URL = "https://mesonet.agron.iastate.edu/json/raob.py"
    FETCH_TIMEOUT = 30

    def __init__(self, http_json: Callable[[str], object] | None = None):
        self._http_json_override = http_json
        self._stations: tuple[dict, ...] | None = None

    def _http_json(self, url: str):
        if self._http_json_override is not None:
            return self._http_json_override(url)
        context = ssl.create_default_context(cafile=_CA_FILE)
        request = Request(
            url,
            headers={"User-Agent": "SHARPpy-Reimagined/observed-sounding"},
        )
        try:
            with urlopen(
                request, timeout=self.FETCH_TIMEOUT, context=context
            ) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code in {404, 422}:
                raise ObservedUnavailableError(
                    f"IEM has no sounding matching this request ({exc.code})"
                ) from exc
            raise ObservedRetrievalError(
                f"IEM request failed with HTTP {exc.code}: {url}"
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ObservedRetrievalError(
                f"IEM request timed out after {self.FETCH_TIMEOUT}s: {url}"
            ) from exc
        except URLError as exc:
            raise ObservedRetrievalError(
                f"IEM service could not be reached ({exc.reason}): {url}"
            ) from exc
        except OSError as exc:
            raise ObservedRetrievalError(
                f"IEM service could not be reached ({exc}): {url}"
            ) from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ObservedParseError(
                f"IEM returned invalid JSON for {url}: {exc}"
            ) from exc

    def _station_catalog(self) -> tuple[dict, ...]:
        if self._stations is not None:
            return self._stations
        payload = self._http_json(self.NETWORK_URL)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ObservedParseError("IEM RAOB station response has no data list")
        self._stations = tuple(row for row in rows if isinstance(row, dict))
        return self._stations

    @staticmethod
    def _station_aliases(row: Mapping[str, object]) -> set[str]:
        station_id = str(row.get("id", "")).strip().upper()
        aliases = {station_id}
        if station_id.startswith("_"):
            aliases.add(station_id[1:])
        if len(station_id) == 4 and station_id[0] in "KPTC":
            aliases.add(station_id[1:])
        synop = row.get("synop")
        try:
            if synop is not None and math.isfinite(float(synop)):
                aliases.add(str(int(float(synop))))
        except (TypeError, ValueError):
            pass
        return {alias for alias in aliases if alias}

    def resolve_station(self, query: str) -> dict:
        requested = str(query).strip()
        normalized = requested.upper()
        rows = self._station_catalog()
        exact = [
            row for row in rows
            if normalized in self._station_aliases(row)
        ]
        if not exact:
            folded = requested.casefold()
            exact = [
                row for row in rows
                if folded in str(row.get("name", "")).casefold()
                or folded in str(row.get("plot_name", "")).casefold()
            ]
        if not exact:
            raise ObservedStationError(
                f"station query {query!r} matched no IEM RAOB station"
            )
        if len(exact) > 1:
            ids = ", ".join(sorted(str(row.get("id", "")) for row in exact))
            raise ObservedStationError(
                f"station query {query!r} matched multiple IEM RAOB stations: "
                f"{ids}"
            )
        return dict(exact[0])

    @staticmethod
    def _parse_valid(value) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ObservedParseError(
                f"IEM sounding has invalid valid time {value!r}"
            ) from exc
        return _utc_datetime(parsed)

    def fetch(self, station: str, when_utc: datetime) -> ObservedSounding:
        when = _utc_datetime(when_utc)
        requested = str(station).strip()
        station_meta = self.resolve_station(requested)
        station_id = str(station_meta.get("id", "")).strip().upper()
        params = urlencode({
            "ts": when.strftime("%Y%m%d%H00"),
            "station": station_id,
        })
        source_url = f"{self.SOUNDING_URL}?{params}"
        payload = self._http_json(source_url)
        profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if not isinstance(profiles, list) or not profiles:
            raise ObservedUnavailableError(
                f"IEM has no sounding for {station_id} at "
                f"{when:%Y-%m-%d %H:%M} UTC"
            )

        selected = None
        for candidate in profiles:
            if not isinstance(candidate, dict):
                continue
            try:
                valid = self._parse_valid(candidate.get("valid"))
            except ObservedParseError:
                continue
            candidate_station = str(candidate.get("station", "")).upper()
            if valid == when and candidate_station == station_id:
                selected = candidate
                break
        if selected is None:
            raise ObservedUnavailableError(
                "IEM response did not contain the exact requested "
                f"station/time ({station_id}, {when.isoformat()})"
            )

        rows = selected.get("profile")
        if not isinstance(rows, list) or not rows:
            raise ObservedUnavailableError(
                f"IEM sounding for {station_id} contains no levels"
            )
        fields = {
            "pres": "pres",
            "hght": "hght",
            "tmpc": "tmpc",
            "dwpc": "dwpc",
            "wdir": "drct",
            "wspd": "sknt",
        }
        columns = {name: [] for name in fields}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ObservedParseError(
                    f"IEM sounding level {index} is not an object"
                )
            pressure = row.get("pres")
            if pressure is None:
                continue
            try:
                float(pressure)
            except (TypeError, ValueError) as exc:
                raise ObservedParseError(
                    f"IEM sounding level {index} has invalid pressure"
                ) from exc
            for output_name, input_name in fields.items():
                value = row.get(input_name)
                try:
                    parsed = MISSING if value is None else float(value)
                except (TypeError, ValueError) as exc:
                    raise ObservedParseError(
                        f"IEM sounding level {index} has invalid {input_name}"
                    ) from exc
                columns[output_name].append(parsed)
        if not columns["pres"]:
            raise ObservedParseError("IEM sounding contains no pressure levels")

        valid = self._parse_valid(selected.get("valid"))
        lat = station_meta.get("latitude", float("nan"))
        lon = station_meta.get("longitude", float("nan"))
        station_name = str(
            station_meta.get("plot_name") or station_meta.get("name")
            or station_id
        )
        metadata = {
            "loc": station_name,
            "valid": valid,
            "run": valid,
            "lat": float(lat),
            "lon": float(lon),
            "model": "Observed",
            "fxx": 0,
            "observed": True,
            "source": self.info.key,
            "source_provider": self.info.key,
            "source_provider_name": self.info.name,
            "source_station": station_id,
            "requested_station": requested,
            "source_url": source_url,
            "station_name": station_name,
        }
        intermediate = {
            name: np.asarray(values, dtype=float)
            for name, values in columns.items()
        }
        intermediate["omeg"] = None
        intermediate["meta"] = metadata
        try:
            profile = UWyo_Decoder().from_intermediate(intermediate)
        except Exception as exc:
            raise ObservedParseError(
                f"IEM sounding could not be converted to a profile: {exc}"
            ) from exc
        return ObservedSounding(
            profile=profile,
            provider=self.info.key,
            provider_name=self.info.name,
            station_id=station_id,
            requested_station=requested,
            valid=valid,
            source_url=source_url,
            metadata=dict(metadata),
        )


_PROVIDER_FACTORIES: dict[str, Callable[[], ObservedSoundingProvider]] = {
    "uwyo": UWyoObservedProvider,
    "iem": IEMObservedProvider,
}


def available_observed_providers() -> tuple[ObservedProviderInfo, ...]:
    """Return provider identities in the default fallback order."""
    return tuple(_PROVIDER_FACTORIES[key]().info for key in DEFAULT_PROVIDER_ORDER)


def get_observed_provider(key: str) -> ObservedSoundingProvider:
    """Construct one registered provider by key."""
    normalized = str(key).strip().lower()
    try:
        return _PROVIDER_FACTORIES[normalized]()
    except KeyError as exc:
        raise KeyError(f"unknown observed-sounding provider {key!r}") from exc


def fetch_observed(
    station: str,
    when_utc: datetime,
    *,
    providers: Sequence[str | ObservedSoundingProvider] = DEFAULT_PROVIDER_ORDER,
) -> ObservedSounding:
    """Try providers in order and return one unmerged sounding.

    Fallback is explicit in ``providers``.  Failures from earlier providers are
    recorded as ``fallback_attempts`` metadata on the successful result.
    """
    if not providers:
        raise ValueError("at least one observed-sounding provider is required")
    attempts: list[dict[str, str]] = []
    for provider_value in providers:
        provider = (
            get_observed_provider(provider_value)
            if isinstance(provider_value, str)
            else provider_value
        )
        if not isinstance(provider, ObservedSoundingProvider):
            raise TypeError("provider does not implement ObservedSoundingProvider")
        try:
            result = provider.fetch(station, when_utc)
        except ObservedProviderError as exc:
            attempts.append({
                "provider": provider.info.key,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            continue
        if attempts:
            metadata = dict(result.metadata)
            metadata["fallback_attempts"] = tuple(dict(item) for item in attempts)
            _set_profile_metadata(result.profile, metadata)
            result = replace(result, metadata=metadata)
        return result
    raise ObservedFallbackError(attempts)


def _profile_column(profile, name: str) -> np.ndarray:
    values = np.ma.asarray(getattr(profile, name), dtype=float)
    return np.asarray(values.filled(MISSING), dtype=float)


def _json_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    def normalize(value):
        if isinstance(value, datetime):
            return _utc_datetime(value).isoformat().replace("+00:00", "Z")
        if isinstance(value, Mapping):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, np.generic):
            return value.item()
        return value

    return {str(key): normalize(value) for key, value in metadata.items()}


def write_observed_npz(
    result: ObservedSounding,
    out_path,
    *,
    loc: str | None = None,
) -> str:
    """Atomically write one provider result and its provenance sidecar."""
    profile = result.profile
    metadata = dict(result.metadata)
    valid = _utc_datetime(result.valid)
    loc_label = str(loc or metadata.get("loc") or metadata.get("station_name")
                    or result.station_id)
    n_levels = int(_profile_column(profile, "pres").size)
    omeg = getattr(profile, "omeg", None)
    if omeg is None:
        omeg_array = np.full(n_levels, MISSING, dtype=float)
    else:
        omeg_array = np.asarray(
            np.ma.asarray(omeg, dtype=float).filled(MISSING), dtype=float
        )
    fallback_from = [
        str(item.get("provider", ""))
        for item in metadata.get("fallback_attempts", ())
        if isinstance(item, Mapping)
    ]
    arrays = {
        "pres": _profile_column(profile, "pres"),
        "hght": _profile_column(profile, "hght"),
        "tmpc": _profile_column(profile, "tmpc"),
        "dwpc": _profile_column(profile, "dwpc"),
        "wdir": _profile_column(profile, "wdir"),
        "wspd": _profile_column(profile, "wspd"),
        "omeg": omeg_array,
        "lat": float(metadata.get("lat", 0.0)),
        "lon": float(metadata.get("lon", 0.0)),
        "loc": loc_label,
        "model": "Observed",
        "run": valid.strftime("%Y-%m-%d %H:%M"),
        "valid": valid.strftime("%Y-%m-%d %H:%M"),
        "fxx": 0,
        "observed": True,
        "source": result.provider,
        "source_provider": result.provider,
        "source_provider_name": result.provider_name,
        "source_station": result.station_id,
        "source_url": result.source_url,
        "requested_station": result.requested_station,
        "fallback_from": np.asarray(fallback_from, dtype=str),
    }
    out_path = str(out_path)
    _atomic_write_npz(out_path, arrays)
    sidecar_path = out_path.rsplit(".", 1)[0] + ".json"
    sidecar = _json_metadata(metadata)
    sidecar.update({
        "provider": result.provider,
        "provider_name": result.provider_name,
        "source_station": result.station_id,
        "requested_station": result.requested_station,
        "source_url": result.source_url,
        "valid": valid.isoformat().replace("+00:00", "Z"),
        "levels": n_levels,
        "backend": f"{result.provider} observed provider",
        "decoder": "provider-neutral observed-sounding adapter",
        "cache_hit": False,
        "surface_vorticity_source": "not provided by observed archive",
        "npz": str(out_path),
    })
    try:
        _atomic_write_json(sidecar_path, sidecar)
    except BaseException:
        try:
            import os

            os.remove(out_path)
        except OSError:
            pass
        raise
    return out_path


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "IEMObservedProvider",
    "ObservedFallbackError",
    "ObservedParseError",
    "ObservedProviderError",
    "ObservedProviderInfo",
    "ObservedRetrievalError",
    "ObservedSounding",
    "ObservedSoundingProvider",
    "ObservedStationError",
    "ObservedUnavailableError",
    "UWyoObservedProvider",
    "available_observed_providers",
    "fetch_observed",
    "get_observed_provider",
    "write_observed_npz",
]
