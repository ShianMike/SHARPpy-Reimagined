"""Point-sounding retrieval and dataset conversion for Open-Meteo model data.

It assembles retrieval metadata and portable sounding output through the stable
``openmeteo`` provider facade so GUI and CLI callers share one extraction contract."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from sharpmod.export_paths import export_file_path
from sharpmod.providers.openmeteo_access import OpenMeteoAccess
from sharpmod.providers.openmeteo_access import ParameterRangeError
from sharpmod.providers.openmeteo_access import RetrievalError
from sharpmod.providers.openmeteo_access import fetch_json
from sharpmod.providers.openmeteo_access import resolve_access
from sharpmod.providers import openmeteo as _api


def fetch_point(
        model_key: str,
        lat: float,
        lon: float,
        *,
        run_time: datetime | None = None,
        fxx: int = 0,
        access: OpenMeteoAccess | None = None,
        session=None,
        request_get=None,
        progress_callback=None,
        cancelled=None,
        now: datetime | None = None,
) -> _api.OpenMeteoPointDataset:
    """Fetch and normalize one Open-Meteo point sounding.

    Exactly one HTTP request is made. Every pressure level and surface field
    travels together, because a request per level would multiply the user's
    billable calls by the length of the ladder for no benefit.
    """
    import numpy as np

    from sharpmod.models.model_surface import merge_surface_level
    from sharpmod.tools.era5_extract import _mark_missing

    capability = _api.get_capability(model_key)
    resolved = resolve_access() if access is None else access
    resolved.require_ready()

    latitude = float(lat)
    if not -90.0 <= latitude <= 90.0:
        raise ParameterRangeError(
            "latitude %.4f is outside -90..90" % latitude)
    longitude = _api.normalise_longitude(lon)
    if not _api.point_in_domain(capability, latitude, longitude):
        raise ParameterRangeError(
            "%.4f, %.4f is outside the %s domain"
            % (latitude, longitude, capability.label))

    run_dt = _api.resolve_run_time(capability, run_time, now=now)
    if not _api.run_is_archived(capability, run_dt):
        raise RetrievalError(
            "%s runs are archived from %s; %s is earlier"
            % (capability.label, capability.archive_start.isoformat(),
               run_dt.strftime("%Y-%m-%d %H:%MZ")))

    hour = int(fxx)
    available = capability.hours_for_cycle(run_dt.hour)
    if hour not in available:
        raise ParameterRangeError(
            "F%03d is not published by the %s %02dZ run (available up to F%03d)"
            % (hour, capability.label, run_dt.hour, max(available)))
    valid_dt = run_dt + timedelta(hours=hour)

    _api._check_cancelled(cancelled)
    _api._emit(progress_callback, "locating")
    query = _api.build_query(capability, latitude, longitude, run_dt, valid_dt)

    _api._emit(progress_callback, "downloading")
    response = fetch_json(
        resolved, query, session=session, request_get=request_get)
    _api._check_cancelled(cancelled)
    _api._emit(progress_callback, "extracting")

    selected_lat = _api._require_float(response.get("latitude"), "latitude")
    selected_lon = _api.normalise_longitude(
        _api._require_float(response.get("longitude"), "longitude"))

    hourly = response.get("hourly")
    if not isinstance(hourly, dict):
        raise RetrievalError(
            "Open-Meteo response carried no hourly block for %s"
            % capability.label)
    index = _api._select_time_index(
        hourly.get("time"), valid_dt, capability, run_dt)

    api_model = capability.api_model
    surface: dict[str, float] = {}
    for name in _api.SURFACE_VARIABLES:
        value = _api._hourly_value(hourly, name, index, api_model)
        if value is None:
            raise RetrievalError(
                "%s verified surface is incomplete at %s: %s is missing"
                % (capability.label,
                   valid_dt.strftime("%Y-%m-%d %H:%MZ"), name))
        surface[name] = value

    surface_pressure = surface["surface_pressure"]
    if surface_pressure > 2000.0:
        # Guard against a Pascal-valued field, as the ECCC adapter does.
        surface_pressure /= 100.0

    # Collect every level that is complete in all five core fields, before any
    # above-ground filtering. Dropping an incomplete level is right -- a gap in
    # temperature, moisture, wind, or height cannot be filled without inventing
    # data -- but the below-ground levels are kept for a moment longer so the
    # surface height can be interpolated between the levels that bracket it.
    complete_levels: list[dict] = []
    for level in capability.pressure_levels:
        values = {}
        complete = True
        for family in _api.PRESSURE_FAMILIES:
            value = _api._hourly_value(
                hourly, "%s_%dhPa" % (family, int(level)), index, api_model)
            if value is None:
                complete = False
                break
            values[family] = value
        if complete:
            values["pressure"] = float(level)
            complete_levels.append(values)

    if len(complete_levels) < 2:
        # Naming the families is what makes this actionable: the usual cause is
        # that one family is absent for this model, which the capability audit
        # is meant to catch before the model is ever offered.
        raise RetrievalError(
            "%s returned no usable pressure level at %s. Every level needs all "
            "of: %s. A whole family may be unpublished for this model."
            % (capability.label, valid_dt.strftime("%Y-%m-%d %H:%MZ"),
               ", ".join(_api.PRESSURE_FAMILIES)))

    surface_height = _api._derive_surface_height(
        [(row["pressure"], row["geopotential_height"])
         for row in complete_levels],
        surface_pressure)

    above_ground = [row for row in complete_levels
                    if row["pressure"] < surface_pressure]
    levels = [row["pressure"] for row in above_ground]
    heights = [row["geopotential_height"] for row in above_ground]
    temps = [row["temperature"] for row in above_ground]
    dewps = [row["dew_point"] for row in above_ground]
    dirs = [row["wind_direction"] for row in above_ground]
    speeds = [row["wind_speed"] for row in above_ground]

    if len(levels) < _api.MIN_USABLE_LEVELS:
        raise RetrievalError(
            "%s returned only %d complete pressure levels above %0.1f hPa; "
            "at least %d are needed"
            % (capability.label, len(levels), surface_pressure,
               _api.MIN_USABLE_LEVELS))
    if min(levels) > _api.REQUIRED_TOP_PRESSURE_HPA:
        raise RetrievalError(
            "%s profile stops at %0.1f hPa and must reach %0.1f hPa"
            % (capability.label, min(levels), _api.REQUIRED_TOP_PRESSURE_HPA))

    count = len(levels)
    pressure = np.asarray(levels, dtype=np.float64)
    speed_ms = np.asarray(speeds, dtype=np.float64)
    direction = np.asarray(dirs, dtype=np.float64)
    radians = np.deg2rad(direction)
    # Meteorological direction is where the wind comes *from*, hence the sign.
    uwnd = -speed_ms * np.sin(radians)
    vwnd = -speed_ms * np.cos(radians)

    columns = {
        "pres": _mark_missing(pressure, count),
        "hght": _mark_missing(np.asarray(heights, dtype=np.float64), count),
        "tmpc": _mark_missing(np.asarray(temps, dtype=np.float64), count),
        "dwpc": _mark_missing(np.asarray(dewps, dtype=np.float64), count),
        "wdir": _mark_missing(direction, count),
        # Knots here while u and v stay in metres per second, which is the
        # convention the rest of the application already writes.
        "wspd": _mark_missing(speed_ms * _api.KNOTS_PER_MS, count),
        "omeg": _mark_missing(None, count),
        "u": _mark_missing(uwnd, count),
        "v": _mark_missing(vwnd, count),
    }

    surface_radians = np.deg2rad(float(surface["wind_direction_10m"]))
    surface_speed = float(surface["wind_speed_10m"])
    merged = merge_surface_level(
        columns,
        {
            "pres": surface_pressure,
            "hght": surface_height,
            "tmpc": float(surface["temperature_2m"]),
            "dwpc": float(surface["dew_point_2m"]),
            "u": float(-surface_speed * np.sin(surface_radians)),
            "v": float(-surface_speed * np.cos(surface_radians)),
        },
        missing=_api.MISSING,
    )
    if merged is None:
        raise RetrievalError(
            "%s verified surface fields failed physical validation "
            "(surface pressure %0.1f hPa, height %0.1f m)"
            % (capability.label, surface_pressure, surface_height))

    skipped = len(capability.pressure_levels) - count
    variables = capability.variable_count()
    return _api.OpenMeteoPointDataset(
        capability=capability,
        columns=merged.columns,
        requested_lat=latitude,
        requested_lon=longitude,
        selected_lat=selected_lat,
        selected_lon=selected_lon,
        surface_height_m=surface_height,
        provider_elevation_m=_api._provider_elevation(response),
        run_time=run_dt,
        valid_time=valid_dt,
        fxx=hour,
        request_count=1,
        variable_count=variables,
        weighted_units=_api.weighted_units(variables),
        surface_pressure_hpa=merged.surface_pressure,
        levels_requested=len(capability.pressure_levels),
        levels_retained=count,
        below_ground_levels_removed=skipped + merged.removed_levels,
        access_mode=resolved.mode,
        generation_time_ms=float(response.get("generationtime_ms") or 0.0),
    )


def build_metadata(
        dataset: _api.OpenMeteoPointDataset,
        *,
        loc: str | None = None,
        npz_path: str = "",
        qc=None,
        cache_hit: bool = False) -> dict:
    """Return the sidecar metadata for one sounding.

    Deliberately complete about provenance and deliberately silent about
    credentials: the access *mode* is recorded so a reader knows whose allowance
    paid for the request, but no key, key digest, or authenticated URL ever
    reaches this dictionary.
    """
    from sharpmod.models.model_surface import SURFACE_CONTRACT_VERSION

    capability = dataset.capability
    meta = {
        "model": capability.label,
        "model_key": capability.model_key,
        "provider_model": capability.api_model,
        "loc": loc or "",
        "requested_lat": dataset.requested_lat,
        "requested_lon": dataset.requested_lon,
        "selected_lat": dataset.selected_lat,
        "selected_lon": dataset.selected_lon,
        "surface_height_m": dataset.surface_height_m,
        "surface_height_source": "derived-from-geopotential-profile",
        "provider_elevation_m": dataset.provider_elevation_m,
        "run": dataset.run_time.strftime("%Y-%m-%d %H:%M"),
        "valid": dataset.valid_time.strftime("%Y-%m-%d %H:%M"),
        "fxx": dataset.fxx,
        "observed": False,
        "npz": npz_path,
        "provider": _api.PROVIDER,
        "origin": capability.origin,
        "resolution": capability.resolution,
        "transport": _api.TRANSPORT,
        "backend": "Open-Meteo Single Runs point adapter",
        "decoder": "forecast pressure-level point values",
        "attribution": _api.ATTRIBUTION,
        "archive_start": capability.archive_start.isoformat(),
        "fields": list(capability.fields),
        "levels_requested": dataset.levels_requested,
        "levels_retained": dataset.levels_retained,
        "levels": dataset.levels_retained,
        "native_cadence_hours": capability.native_cadence_hours,
        # Stated plainly because a value between native steps is the provider's
        # interpolation, not the model's own output, and a sounding should not
        # imply otherwise.
        "hours_interpolated": capability.interpolated_hours,
        "surface_merged": dataset.surface_merged,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "surface_pressure_hpa": dataset.surface_pressure_hpa,
        "below_ground_levels_removed": dataset.below_ground_levels_removed,
        "request_count": dataset.request_count,
        "request_variable_count": dataset.variable_count,
        "estimated_weighted_units": dataset.weighted_units,
        "access_mode": dataset.access_mode,
        "generation_time_ms": dataset.generation_time_ms,
        "omega_available": False,
        "omega_note": (
            "Open-Meteo publishes vertical velocity as geometric velocity in "
            "m/s, which is not the pressure velocity this format's omeg field "
            "carries, so omeg is left missing rather than converted wrongly."
        ),
        "cache_hit": bool(cache_hit),
    }
    if qc is not None:
        meta["qc_valid"] = bool(qc.valid)
        meta["qc_valid_level_count"] = int(qc.valid_level_count)
        meta["qc_issues"] = list(qc.issues)
    return meta


def write_point_dataset(
        dataset: _api.OpenMeteoPointDataset,
        out_path: str,
        *,
        loc: str | None = None,
        progress_callback=None,
) -> str:
    """Write ``dataset`` as an NPZ plus JSON sidecar and return the NPZ path.

    Physical quality control gates the write. A profile that fails it is refused
    rather than saved, because a stored sounding is indistinguishable from a
    trustworthy one once it is on disk.
    """
    from sharpmod import backends as _backends
    from sharpmod.tools.era5_extract import (
        _atomic_write_json,
        _atomic_write_npz,
        _quiet_remove,
    )

    if not isinstance(dataset, _api.OpenMeteoPointDataset):
        raise TypeError(
            "expected an OpenMeteoPointDataset, got %s"
            % type(dataset).__name__)

    capability = dataset.capability
    cols = dataset.columns
    qc = _backends.basic_sounding_qc(
        cols["pres"], cols["hght"], cols["tmpc"],
        cols["dwpc"], cols["wdir"], cols["wspd"],
        missing=_api.MISSING,
    )
    # ``qc.valid`` is false for a contaminated sounding as well as an unusable
    # one, and both backends must keep computing it the same way, so the
    # distinction is drawn here instead: a dewpoint above the temperature is
    # real recorded data and is carried through to ``qc_issues`` for the
    # inspector to warn about.
    #
    # Imported here rather than at module scope because this module is reached
    # while the picker starts up and is deliberately free of NumPy until a
    # retrieval actually runs; ``portable_sounding`` would pull it in.
    from sharpmod.io.portable_sounding import fatal_issues

    fatal = fatal_issues(qc.issues)
    if fatal:
        raise RetrievalError(
            "%s sounding failed physical quality control: %s"
            % (capability.label, ", ".join(fatal)))
    if not dataset.surface_merged:
        raise RetrievalError(
            "%s sounding has no verified surface row; refusing a pressure "
            "ladder that may contain below-ground levels" % capability.label)

    _api._emit(progress_callback, "writing")
    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"],
        # Renamed on the way to disk, matching every other writer.
        "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": dataset.selected_lat,
        "lon": dataset.selected_lon,
        "loc": loc or "",
        "model": capability.label,
        "run": dataset.run_time.strftime("%Y-%m-%d %H:%M"),
        "valid": dataset.valid_time.strftime("%Y-%m-%d %H:%M"),
        "fxx": dataset.fxx,
        "observed": False,
    }

    _atomic_write_npz(out_path, arrays)
    json_path = out_path.rsplit(".", 1)[0] + ".json"
    try:
        _atomic_write_json(
            json_path,
            _api.build_metadata(dataset, loc=loc, npz_path=out_path, qc=qc))
    except BaseException:
        # The pair is the contract, so a half-written pair is worse than none.
        _quiet_remove(out_path)
        raise
    _api._emit(progress_callback, "complete")
    return out_path


def extract(
        model_key: str,
        lat: float,
        lon: float,
        *,
        run_time: datetime | None = None,
        fxx: int = 0,
        out_path: str | None = None,
        loc: str | None = None,
        dataset: _api.OpenMeteoPointDataset | None = None,
        access: OpenMeteoAccess | None = None,
        session=None,
        request_get=None,
        progress_callback=None,
        cancelled=None,
        now: datetime | None = None,
) -> str:
    """Fetch, normalize, and write one Open-Meteo point sounding.

    A ``dataset`` supplied by the caller is reused rather than refetched, which
    is how the model-hour cache turns several soundings from one run and point
    into a single request. Its identity is re-validated first: reusing a payload
    for the wrong model, hour, run, or point would silently mislabel a sounding.
    """
    capability = _api.get_capability(model_key)

    if dataset is not None:
        if not isinstance(dataset, _api.OpenMeteoPointDataset):
            raise TypeError(
                "expected an OpenMeteoPointDataset, got %s"
                % type(dataset).__name__)
        _api._validate_reused(capability, dataset, lat, lon, run_time, fxx, now=now)
        point = dataset
    else:
        point = _api.fetch_point(
            capability.model_key, lat, lon,
            run_time=run_time, fxx=fxx, access=access, session=session,
            request_get=request_get, progress_callback=progress_callback,
            cancelled=cancelled, now=now)

    target = out_path or str(
        export_file_path(
            _api.default_out_path(
                capability,
                point.selected_lat,
                point.selected_lon,
                point.run_time,
                point.fxx,
            )
        )
    )
    return _api.write_point_dataset(
        point, target, loc=loc, progress_callback=progress_callback)


def probe(
        model_key: str,
        run_time: datetime | None = None,
        fxx: int = 0,
        *,
        live: bool = False,
        lat: float = 0.0,
        lon: float = 0.0,
        access: OpenMeteoAccess | None = None,
        session=None,
        request_get=None,
        cancelled=None,
        now: datetime | None = None,
) -> dict:
    """Return an availability verdict for one model, run, and forecast hour.

    Answered from the manifest without touching the network unless ``live`` is
    set. That default is the point: the interface probes availability whenever a
    selection changes, and spending one of the user's metered requests on every
    keystroke would be indefensible when arithmetic can answer the same
    question. A live probe is a deliberate diagnostic, not background chatter.
    """
    result: dict = {
        "model": model_key,
        "provider": _api.PROVIDER,
        "fxx": int(fxx),
        "live": bool(live),
        "available": False,
        "subset_opened": False,
    }
    try:
        capability = _api.get_capability(model_key)
    except (KeyError, RetrievalError) as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    result["label"] = capability.label
    result["provider_model"] = capability.api_model
    resolved = resolve_access() if access is None else access
    result["access_mode"] = resolved.mode
    result["access"] = resolved.describe()
    # Every core field is requested in one call, so the surface contract is
    # complete by construction whenever a response validates at all.
    result["surface_contract_complete"] = True

    try:
        run_dt = _api.resolve_run_time(capability, run_time, now=now)
        result["run"] = run_dt.strftime("%Y-%m-%d %H:%M")
        hours = capability.hours_for_cycle(run_dt.hour)
        if int(fxx) not in hours:
            result["error"] = (
                "F%03d is not published by the %02dZ run (up to F%03d)"
                % (int(fxx), run_dt.hour, max(hours)))
            return result
        if not _api.run_is_archived(capability, run_dt):
            result["error"] = (
                "runs are archived from %s"
                % capability.archive_start.isoformat())
            return result
        result["valid"] = (
            run_dt + timedelta(hours=int(fxx))).strftime("%Y-%m-%d %H:%M")
        result["estimated_weighted_units"] = _api.weighted_units(
            capability.variable_count())

        if not live:
            # A manifest verdict cannot promise ingestion, only that the request
            # is well formed and inside the published envelope. Saying so keeps
            # the interface honest about what it checked.
            result["available"] = True
            result["note"] = (
                "manifest check only; the run's presence is confirmed when the "
                "sounding is fetched")
            return result

        resolved.require_ready()
        point = _api.fetch_point(
            capability.model_key, lat, lon, run_time=run_dt, fxx=int(fxx),
            access=resolved, session=session, request_get=request_get,
            cancelled=cancelled, now=now)
        result["available"] = True
        result["subset_opened"] = True
        result["levels_retained"] = point.levels_retained
        result["surface_pressure_hpa"] = point.surface_pressure_hpa
        point.close()
    except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
        from sharpmod.models.model_transport import DownloadCancelled

        if isinstance(exc, DownloadCancelled):
            raise
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result
