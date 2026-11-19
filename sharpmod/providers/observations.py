"""Provider-neutral observed-sounding retrieval.

The provider boundary in this module is deliberately independent of Qt and of
the command-line front ends.  A request is satisfied by exactly one provider;
fallback never combines levels from separate archives.  The selected provider,
station identifier, request URL, and any failed earlier providers are retained
in both the returned profile metadata and the portable ``.npz`` output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlencode

import numpy as np

from sharpmod.io.uwyo_decoder import (
    RetrievalError as UWyoRetrievalError,
    SoundingParseError as UWyoSoundingParseError,
    StationLookupError as UWyoStationLookupError,
    StationTimeUnavailableError as UWyoStationTimeUnavailableError,
    UWyo_Decoder,
)
from sharpmod.tools.era5_extract import _atomic_write_sounding_pair


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
        import sharpmod_rs

        return sharpmod_rs.fetch_iem_json(url, self.FETCH_TIMEOUT)

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
        import sharpmod_rs

        intermediate = sharpmod_rs.iem_sounding_columns(rows, MISSING)

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


class IGRAObservedProvider:
    """NOAA/NCEI Integrated Global Radiosonde Archive (IGRA v2) adapter.

    IGRA is the only source here with deep history: roughly 2,800 stations,
    some reaching back to 1905, quality assured and updated daily.

    It is deliberately absent from :data:`DEFAULT_PROVIDER_ORDER`. The archive
    publishes no per-sounding endpoint -- the smallest retrievable unit is one
    ZIP per station, a few megabytes for the current year and around eighty for
    a long station's full record. Joining the automatic chain would mean an
    everyday request quietly reaching for that, so this provider is only used
    when it has been asked for by name.
    """

    info = ObservedProviderInfo(
        key="igra2",
        name="NOAA Integrated Global Radiosonde Archive v2",
        homepage=(
            "https://www.ncei.noaa.gov/products/weather-balloon/"
            "integrated-global-radiosonde-archive"
        ),
    )

    def __init__(self, decoder=None, *, allow_full_record: bool = True):
        self._decoder = decoder
        self._allow_full_record = bool(allow_full_record)

    @property
    def decoder(self):
        """The reader, built on first use so importing costs no I/O."""
        if self._decoder is None:
            from sharpmod.io.igra2 import IGRA_Decoder

            self._decoder = IGRA_Decoder(
                allow_full_record=self._allow_full_record
            )
        return self._decoder

    def fetch(self, station: str, when_utc: datetime) -> ObservedSounding:
        from sharpmod.io import igra2

        when = _utc_datetime(when_utc)
        requested = str(station).strip()
        decoder = self.decoder

        try:
            station_meta = decoder.resolve_station(requested)
        except igra2.IGRAStationLookupError as exc:
            raise ObservedStationError(str(exc)) from exc
        except igra2.IGRAError as exc:
            raise ObservedRetrievalError(str(exc)) from exc

        try:
            sounding = decoder.fetch(station_meta.id, when)
        except igra2.IGRAStationLookupError as exc:
            raise ObservedStationError(str(exc)) from exc
        except igra2.IGRAStationTimeUnavailableError as exc:
            raise ObservedUnavailableError(str(exc)) from exc
        except igra2.IGRASoundingParseError as exc:
            raise ObservedParseError(str(exc)) from exc
        except igra2.IGRARetrievalError as exc:
            raise ObservedRetrievalError(str(exc)) from exc
        except igra2.IGRAError as exc:
            raise ObservedRetrievalError(str(exc)) from exc

        station_name = station_meta.name or station_meta.id
        lat = sounding.lat if math.isfinite(sounding.lat) else station_meta.lat
        lon = sounding.lon if math.isfinite(sounding.lon) else station_meta.lon
        metadata: dict[str, object] = {
            "loc": station_name,
            "observed": True,
            "model": "Observed",
            "source": self.info.key,
            "source_provider": self.info.key,
            "source_provider_name": self.info.name,
            "source_station": sounding.station_id,
            "requested_station": requested,
            "source_url": sounding.archive_url,
            "station_name": station_name,
            "lat": float(lat),
            "lon": float(lon),
            "valid": sounding.valid,
            "run": sounding.valid,
            "fxx": 0,
            # IGRA-specific provenance. The requested time is retained
            # alongside the delivered one because the archive carries special
            # releases at non-synoptic hours, so the two can legitimately
            # differ and the user should be able to see that they did.
            "igra_requested_valid": when,
            "igra_archive": sounding.archive_kind,
            "igra_station_id": station_meta.id,
            "igra_wmo_id": station_meta.wmo_id or "",
            "igra_state": station_meta.state,
            "igra_elevation_m": float(station_meta.elev_m),
            "igra_record_years": (
                f"{station_meta.first_year}-{station_meta.last_year}"
            ),
            "igra_pressure_source": sounding.pressure_source,
            "igra_nonpressure_source": sounding.nonpressure_source,
            "igra_dewpoint_from_rh_levels": int(
                sounding.dewpoint_from_rh_levels
            ),
            "igra_dropped_nonpressure_levels": int(sounding.dropped_levels),
        }
        if sounding.release_time is not None:
            metadata["igra_release_time"] = sounding.release_time

        intermediate: dict[str, object] = {
            "pres": np.asarray(sounding.pres, dtype=float),
            "hght": np.asarray(sounding.hght, dtype=float),
            "tmpc": np.asarray(sounding.tmpc, dtype=float),
            "dwpc": np.asarray(sounding.dwpc, dtype=float),
            "wdir": np.asarray(sounding.wdir, dtype=float),
            "wspd": np.asarray(sounding.wspd, dtype=float),
            "omeg": None,
            "meta": metadata,
        }
        # Reuse the UWyo decoder's thinning policy rather than restating it:
        # both archives can serve high-resolution ascents, and two copies of
        # the same level budget would drift apart.
        converter = UWyo_Decoder()
        intermediate = converter._thin_high_res(intermediate)
        try:
            profile = converter.from_intermediate(intermediate)
        except Exception as exc:
            raise ObservedParseError(
                f"IGRA sounding could not be converted to a profile: {exc}"
            ) from exc

        _set_profile_metadata(profile, metadata)
        return ObservedSounding(
            profile=profile,
            provider=self.info.key,
            provider_name=self.info.name,
            station_id=sounding.station_id,
            requested_station=requested,
            valid=sounding.valid,
            source_url=sounding.archive_url,
            metadata=dict(metadata),
        )


_PROVIDER_FACTORIES: dict[str, Callable[[], ObservedSoundingProvider]] = {
    "uwyo": UWyoObservedProvider,
    "iem": IEMObservedProvider,
    "igra2": IGRAObservedProvider,
}


def registered_provider_keys() -> tuple[str, ...]:
    """Return every selectable provider key.

    The automatic fallback chain leads, then anything registered outside it.
    This is deliberately wider than :data:`DEFAULT_PROVIDER_ORDER`: a provider
    can be selectable by name without being something to reach for on its own.
    """
    extra = sorted(
        key for key in _PROVIDER_FACTORIES if key not in DEFAULT_PROVIDER_ORDER
    )
    return tuple(DEFAULT_PROVIDER_ORDER) + tuple(extra)


def available_observed_providers() -> tuple[ObservedProviderInfo, ...]:
    """Return every registered provider identity, fallback chain first."""
    return tuple(
        _PROVIDER_FACTORIES[key]().info for key in registered_provider_keys()
    )


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
    from sharpmod_rs import fetch_observed as native_fetch

    return native_fetch(station, when_utc, providers)


def write_observed_npz(
    result: ObservedSounding,
    out_path,
    *,
    loc: str | None = None,
) -> str:
    """Atomically write one provider result and its provenance sidecar."""
    from sharpmod_rs import write_observed_npz as native_write

    return native_write(result, out_path, loc)


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "IEMObservedProvider",
    "IGRAObservedProvider",
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
    "registered_provider_keys",
    "write_observed_npz",
]
