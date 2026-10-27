"""Open soundings through the common decoder and process-owned cache."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sharpmod.bridge import cancellation, params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_cache import cache
from sharpmod.bridge.sounding_progress import track
from sharpmod.bridge.sounding_summary import decode, meta, register, summary
from sharpmod.models.model_transport import DownloadCancelled

log = logging.getLogger("sharpmod.bridge")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9_-]{1,12}$")


def _observed_failure(exc: Exception, station: str, when: datetime) -> BridgeError:
    from sharpmod.providers import observations as obs

    label = f"{station} at {when:%Y-%m-%d %HZ}"
    if isinstance(exc, obs.ObservedFallbackError):
        attempts = list(getattr(exc, "attempts", ()) or ())
        kinds = {
            str(item.get("error_type", ""))
            for item in attempts
            if isinstance(item, dict)
        }
        details = {
            "attempts": [
                {
                    "provider": str(a.get("provider", "")),
                    "error": str(a.get("error", ""))[:200],
                }
                for a in attempts
                if isinstance(a, dict)
            ]
        }
        if "ObservedRetrievalError" in kinds:
            return BridgeError(
                "SOUNDING_FETCH_FAILED",
                f"No provider could be reached for the {label} sounding. "
                "Check the connection and try again.",
                True,
                details,
            )
        return BridgeError(
            "SOUNDING_UNAVAILABLE",
            f"No provider has a sounding for {label}. Try another time or station.",
            False,
            details,
        )
    if isinstance(exc, obs.ObservedRetrievalError):
        return BridgeError(
            "SOUNDING_FETCH_FAILED",
            f"The {label} sounding could not be downloaded: {exc}",
            True,
        )
    if isinstance(exc, obs.ObservedParseError):
        return BridgeError(
            "SOUNDING_INVALID",
            f"The {label} sounding was received but could not be read: {exc}",
            False,
        )
    return BridgeError(
        "SOUNDING_UNAVAILABLE", f"No sounding is available for {label}: {exc}", False
    )


def open_observed(params: dict[str, Any]) -> dict[str, Any]:
    """An observed radiosonde sounding from UWyo, IEM, or IGRA."""
    p.keys(params, ("station_id", "time"), ("provider",))
    station = p.text(params, "station_id", 1, 16)
    when = p.time(params, "time")
    from sharpmod.providers import observations as obs

    provider = p.choice(
        params, "provider", ("auto", *obs.registered_provider_keys()), "auto"
    )
    providers = tuple(obs.DEFAULT_PROVIDER_ORDER) if provider == "auto" else (provider,)
    try:
        result = obs.fetch_observed(station, when, providers=providers)
    except obs.ObservedProviderError as exc:
        log.info("observed sounding %s %s: %s", station, when, exc)
        raise _observed_failure(exc, station, when) from None
    entry_id = cache().new_id()
    path = cache().data_path(entry_id, ".npz")
    obs.write_observed_npz(result, path)
    collection, _ = decode(path)
    metadata = dict(result.metadata)
    name = str(metadata.get("station_name") or "").strip() or None
    return register(
        entry_id,
        path,
        summary(
            entry_id,
            "observed",
            collection,
            title=f"{result.station_id} {name}" if name else result.station_id,
            location=result.station_id,
            station_id=result.station_id,
            station_name=name,
            latitude=p.num(metadata.get("lat")),
            longitude=p.num(metadata.get("lon")),
            valid_time=p.iso(result.valid),
            provider=result.provider,
            provider_name=result.provider_name,
            source_url=result.source_url or None,
        ),
        collection,
    )


def open_model(params: dict[str, Any]) -> dict[str, Any]:
    """A forecast-model point sounding (HRRR, RAP, GFS, and the rest)."""
    p.keys(params, ("model", "forecast_hour", "latitude", "longitude"),
           ("run_time", "location", "progress_id"))
    with track(params.get("progress_id")) as progress:
        entry_id = cache().new_id()
        try:
            return _open_model(params, progress, entry_id)
        except BaseException:
            cache()._remove_files(entry_id)
            raise


def _open_model(params, progress, entry_id):
    key = p.text(params, "model", 1, 40)
    fxx = p.integer(params, "forecast_hour", 0, 1000)
    lat = p.number(params, "latitude", -90.0, 90.0)
    lon = p.number(params, "longitude", -180.0, 180.0)
    run = p.time(params, "run_time") if params.get("run_time") is not None else None
    location = p.optional_text(params, "location", 64)
    from sharpmod.tools import era5_extract
    from sharpmod.tools import model_extract as mx

    try:
        config = mx._require_selectable(mx.get_config(key))
    except (KeyError, era5_extract.ERA5ExtractionError) as exc:
        raise BridgeError(
            "INVALID_ARGUMENT",
            f"The forecast model {key!r} is not available: {exc}",
            False,
        ) from None
    path = cache().data_path(entry_id, ".npz")
    stop = cancellation.event()
    try:
        written = mx.extract(
            config.key,
            lat,
            lon,
            run_time=run,
            fxx=fxx,
            out_path=str(path),
            loc=location,
            download_dir=str(cache().root / f"{entry_id}-grib"),
            progress_callback=progress.stage,
            on_bytes=progress.receive,
            cancelled=stop.is_set if stop is not None else (lambda: False),
            source_cache=cache().model_hours,
        )
    except DownloadCancelled:
        raise cancellation.Cancelled() from None
    except era5_extract.ParameterRangeError as exc:
        raise BridgeError("INVALID_ARGUMENT", str(exc), False) from None
    except era5_extract.ERA5ExtractionError as exc:
        log.info("model sounding %s: %s", config.key, exc)
        first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
        raise BridgeError(
            "SOUNDING_FETCH_FAILED",
            f"The {config.label} sounding could not be retrieved: {first}",
            True,
        ) from None
    data_path = Path(written) if written else path
    cancellation.check()
    progress.stage("calculating")
    collection, _ = decode(data_path)
    cancellation.check()
    north, east = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    point = f"{abs(lat):.2f}{north} {abs(lon):.2f}{east}"
    return register(
        entry_id,
        data_path,
        summary(
            entry_id,
            "model",
            collection,
            title=f"{config.label} {location or point}",
            location=location or point,
            latitude=lat, longitude=lon,
            run_time=p.iso(meta(collection, "run") or meta(collection, "base_time")),
            forecast_hour=fxx,
            model=config.label,
            provider=config.key, provider_name=config.label,
        ),
        collection,
    )


def open_era5(params: dict[str, Any]) -> dict[str, Any]:
    """An hourly ERA5 analysis through the existing CDS extractor."""
    p.keys(params, ("time", "latitude", "longitude"), ("location",))
    when = p.time(params, "time")
    if when.minute or when.second or when.microsecond:
        raise p.invalid("ERA5 time must be a whole UTC hour")
    lat = p.number(params, "latitude", -90.0, 90.0)
    lon = p.number(params, "longitude", -180.0, 180.0)
    location = p.optional_text(params, "location", 64)
    from sharpmod.tools import era5_extract

    entry_id = cache().new_id()
    path = cache().data_path(entry_id, ".npz")
    try:
        written = era5_extract.extract(
            lat, lon, when, str(path), loc=location or "ERA5pt"
        )
    except era5_extract.ParameterRangeError as exc:
        raise BridgeError("INVALID_ARGUMENT", str(exc), False) from None
    except era5_extract.ERA5ExtractionError as exc:
        first = (
            str(exc).strip().splitlines()[0][:300]
            if str(exc).strip()
            else type(exc).__name__
        )
        raise BridgeError(
            "SOUNDING_FETCH_FAILED",
            f"The ERA5 sounding could not be retrieved: {first}",
            True,
        ) from None
    data_path = Path(written) if written else path
    collection, _ = decode(data_path)
    point = f"{abs(lat):.2f}{'N' if lat >= 0 else 'S'} {abs(lon):.2f}{'E' if lon >= 0 else 'W'}"
    return register(
        entry_id,
        data_path,
        summary(
            entry_id,
            "era5",
            collection,
            title=f"ERA5 {location or point}",
            location=location or point,
            model="ERA5",
            provider="cds",
            provider_name="Copernicus Climate Data Store",
            source_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels",
        ),
        collection,
    )


def open_file(params: dict[str, Any]) -> dict[str, Any]:
    """A sounding file from the user's disk, sent as base64."""
    p.keys(params, ("file_name", "content_base64"))
    name = Path(p.text(params, "file_name", 1, 255)).name
    encoded = params.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_UPLOAD_BYTES * 4) // 3 + 8:
        raise p.invalid("content_base64 must be base64 text of at most 5 MiB of data")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise p.invalid("content_base64 is not valid base64") from None
    if len(data) > MAX_UPLOAD_BYTES:
        raise p.invalid("the file must be at most 5 MiB")
    if not data:
        raise BridgeError("SOUNDING_INVALID", f"{name} is empty.", False)
    suffix = Path(name).suffix.lower()
    suffix = suffix if _SAFE_SUFFIX.match(suffix) else ".txt"
    entry_id = cache().new_id()
    path = cache().data_path(entry_id, suffix)
    path.write_bytes(data)
    try:
        collection, station = decode(path, display=name)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    model = meta(collection, "model")
    forecast = bool(model) and not meta(collection, "observed")
    return register(
        entry_id,
        path,
        summary(
            entry_id,
            "file",
            collection,
            title=f"{station} {name}".strip() if station else name,
            location=station or None,
            file_name=name,
            model=str(model) if forecast else None,
            run_time=p.iso(meta(collection, "run")) if forecast else None,
        ),
        collection,
    )
