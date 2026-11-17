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
    from sharpmod_rs import fetch_openmeteo_point

    return fetch_openmeteo_point(model_key, lat, lon, locals())


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
        cancelled=None,
) -> str:
    """Write ``dataset`` as an NPZ plus JSON sidecar and return the NPZ path.

    Physical quality control gates the write. A profile that fails it is refused
    rather than saved, because a stored sounding is indistinguishable from a
    trustworthy one once it is on disk.
    """
    from sharpmod import backends as _backends
    from sharpmod.models.model_transport import DownloadCancelled
    from sharpmod.tools.era5_extract import (
        ExtractionCancelled,
        _atomic_write_sounding_pair,
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

    metadata = _api.build_metadata(dataset, loc=loc, npz_path=out_path, qc=qc)
    try:
        _atomic_write_sounding_pair(
            out_path, arrays, metadata, cancelled=cancelled)
    except ExtractionCancelled as exc:
        raise DownloadCancelled("Open-Meteo request cancelled") from exc
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
        point, target, loc=loc, progress_callback=progress_callback,
        cancelled=cancelled)


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
