"""Point-fetch and dataset-conversion workflow for ECCC GeoMet model data.

It resolves provider data, writes intermediate datasets, extracts a SHARPpy-ready
sounding, and is exposed through the stable ``eccc_geomet`` provider module."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import timedelta
from sharpmod import backends as _backends
from sharpmod.export_paths import export_file_path
from sharpmod.models.model_surface import SURFACE_CONTRACT_FIELDS
from sharpmod.models.model_surface import SURFACE_CONTRACT_VERSION
from sharpmod.models.model_surface import merge_surface_level
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.io.portable_sounding import fatal_issues
from sharpmod.tools.era5_extract import ParameterRangeError
from sharpmod.tools.era5_extract import RetrievalError
from sharpmod.tools.era5_extract import _atomic_write_json
from sharpmod.tools.era5_extract import _atomic_write_npz
from sharpmod.tools.era5_extract import _mark_missing
from sharpmod.tools.era5_extract import _quiet_remove
from sharpmod.tools.era5_extract import dewpoint_from_specific_humidity
import numpy as np
import os
import threading
import time
from sharpmod.providers import eccc_geomet as _api


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
) -> _api.GeoMetPointDataset:
    """Fetch and normalize one GDPS/RDPS pressure-level point profile."""
    capability = _api.get_capability(model)
    lat = float(lat)
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    if not -90.0 <= lat <= 90.0:
        raise ParameterRangeError("latitude %.4f is outside [-90, 90]" % lat)
    if not _api.point_in_domain(capability.model_key, lat, lon):
        raise ParameterRangeError(
            "%s does not cover %.4f, %.4f" % (capability.label, lat, lon)
        )
    fxx = int(fxx)
    if fxx not in capability.forecast_hours:
        raise ParameterRangeError(
            "%s forecast hour F%03d is unavailable" % (capability.label, fxx)
        )
    request_get = request_get or _api._default_get
    _api._emit(progress_callback, "locating")
    run_dt = _api.latest_reference_time(
        capability, request_get=request_get, cancelled=cancelled
    ) if run_time is None else _api._floor_cycle(run_time, capability.cycles)
    valid_dt = run_dt + timedelta(hours=fxx)

    workers = _api.worker_count(max_workers)
    _api._emit(progress_callback, "downloading")
    values = {name: {} for name in capability.fields}
    points = []
    lock = threading.Lock()

    def load(variable, level):
        # Time-invariant surface fields exist only at the analysis instant.
        when = (
            run_dt
            if level is None and str(variable) in _api._ANALYSIS_ONLY_SURFACE
            else valid_dt
        )
        return _api._fetch_value(
            capability,
            variable,
            level,
            lat,
            lon,
            when,
            run_dt,
            request_get=request_get,
            cancelled=cancelled,
        )

    def run_tasks(executor, tasks):
        errors = []
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
        return errors

    surface_tasks = [(variable, None) for variable in _api._SURFACE_VARIABLES]
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="sharpmod-geomet",
    ) as executor:
        errors = run_tasks(executor, surface_tasks)
        if errors:
            variable, _level, exc = errors[0]
            raise RetrievalError(
                "%s verified surface is incomplete (%s): %s"
                % (capability.label, variable, exc)
            ) from exc
        surface_pressure = float(values["SurfacePressure"][None])
        if surface_pressure > 2000.0:
            surface_pressure /= 100.0
        eligible_levels = tuple(
            level for level in capability.pressure_levels
            if float(level) < surface_pressure
        )
        if not eligible_levels:
            raise RetrievalError(
                "%s surface pressure %.1f hPa leaves no atmospheric levels"
                % (capability.label, surface_pressure)
            )
        pressure_tasks = [
            (variable, level)
            for level in eligible_levels
            for variable in _api._REQUIRED_VARIABLES
        ]
        pressure_tasks.extend(
            ("VerticalVelocity", level)
            for level in capability.omega_levels
            if level in eligible_levels
        )
        errors = run_tasks(executor, pressure_tasks)
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

    levels = np.asarray(eligible_levels, dtype=float)
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
    surface_direction = float(values["SurfaceWindDir"][None])
    surface_speed = float(values["SurfaceWindSpeed"][None])
    surface_radians = np.deg2rad(surface_direction)
    surface_merge = merge_surface_level(
        columns,
        {
            "pres": surface_pressure,
            "hght": values["SurfaceHeight"][None],
            "tmpc": values["SurfaceTemperature"][None],
            "dwpc": values["SurfaceDewpoint"][None],
            "u": -surface_speed * np.sin(surface_radians),
            "v": -surface_speed * np.cos(surface_radians),
        },
        missing=-9999.0,
    )
    if surface_merge is None:
        raise RetrievalError(
            "%s verified surface fields failed physical validation"
            % capability.label
        )
    columns = surface_merge.columns
    skipped_levels = len(capability.pressure_levels) - len(eligible_levels)
    _api._emit(progress_callback, "extracting")
    return _api.GeoMetPointDataset(
        capability=capability,
        columns=columns,
        requested_lat=lat,
        requested_lon=lon,
        selected_lat=selected_lat,
        selected_lon=selected_lon,
        run_time=run_dt,
        valid_time=valid_dt,
        fxx=fxx,
        request_count=len(surface_tasks) + len(pressure_tasks),
        max_workers=workers,
        surface_pressure_hpa=surface_merge.surface_pressure,
        below_ground_levels_removed=(
            skipped_levels + surface_merge.removed_levels
        ),
    )


def write_point_dataset(dataset, out_path, *, loc=None, progress_callback=None):
    """Atomically serialize one normalized GeoMet dataset to NPZ + JSON."""
    if not isinstance(dataset, _api.GeoMetPointDataset):
        raise TypeError("dataset must be GeoMetPointDataset")
    capability = dataset.capability
    out_path = os.fspath(out_path)
    loc_label = loc or "%s %.2f, %.2f" % (
        capability.label, dataset.selected_lat, dataset.selected_lon
    )
    run_str = dataset.run_time.strftime("%Y-%m-%d %H:%M")
    valid_str = dataset.valid_time.strftime("%Y-%m-%d %H:%M")
    cols = dataset.columns
    if not dataset.surface_merged:
        raise RetrievalError(
            "%s cached point dataset has no verified surface merge"
            % capability.label
        )
    qc = _backends.basic_sounding_qc(
        cols["pres"],
        cols["hght"],
        cols["tmpc"],
        cols["dwpc"],
        cols["wdir"],
        cols["wspd"],
        missing=-9999.0,
    )
    # Only genuinely unusable data is refused; a dewpoint above the temperature
    # is contaminated rather than unusable and travels on in ``qc_issues``.
    fatal = fatal_issues(qc.issues)
    if fatal:
        raise RetrievalError(
            "%s sounding failed physical quality control: %s"
            % (capability.label, ", ".join(fatal))
        )
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
        "observed": False,
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
        "observed": False,
        "npz": os.path.abspath(out_path),
        "levels": int(np.asarray(cols["pres"]).size),
        "provider": capability.provider,
        "source_url": _api.GEOMET_URL,
        "transport": "wms-getfeatureinfo-point",
        "fields": list(capability.fields),
        "request_count": dataset.request_count,
        "max_workers": dataset.max_workers,
        "backend": "ECCC GeoMet point adapter",
        "decoder": "forecast pressure-level point values",
        "surface_merged": True,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "surface_pressure_hpa": dataset.surface_pressure_hpa,
        "below_ground_levels_removed": dataset.below_ground_levels_removed,
        "qc_valid": qc.valid,
        "qc_valid_level_count": qc.valid_level_count,
        "qc_issues": list(qc.issues),
        "cache_hit": False,
    }
    if progress_callback is not None:
        _api._emit(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        _quiet_remove(out_path)
        raise
    _api._emit(progress_callback, "complete")
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
    capability = _api.get_capability(model)
    if dataset is None:
        dataset = _api.fetch_point(
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
        if not isinstance(dataset, _api.GeoMetPointDataset):
            raise TypeError("ECCC extraction dataset must be GeoMetPointDataset")
        if dataset.capability.model_key != capability.model_key:
            raise RetrievalError("cached GeoMet dataset belongs to another model")
        if int(fxx) != dataset.fxx:
            raise RetrievalError(
                "cached GeoMet dataset belongs to another forecast hour"
            )
        if run_time is not None and _api._floor_cycle(
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
        out_path = str(
            export_file_path(
                "%s_point_%.2fN_%.2fE_%s_f%03d.npz"
                % (
                    capability.model_key,
                    float(lat),
                    float(lon),
                    dataset.run_time.strftime("%Y%m%d%H"),
                    int(fxx),
                )
            )
        )
    if cancelled is not None and cancelled():
        raise DownloadCancelled("ECCC GeoMet extraction cancelled")
    return _api.write_point_dataset(
        dataset, out_path, loc=loc, progress_callback=progress_callback
    )


def probe(
    model,
    run_time=None,
    fxx=0,
    *,
    request_get=None,
    cancelled=None,
    request_timeout=None,
    deadline_seconds=None,
):
    """Return a lightweight layer-capability availability probe."""
    capability = _api.get_capability(model)
    provider_fields = set(capability.fields)
    contract_present = [
        name
        for name, required_fields in _api._SURFACE_CONTRACT_REQUIREMENTS
        if all(field in provider_fields for field in required_fields)
    ]
    contract_missing = [
        name for name in SURFACE_CONTRACT_FIELDS
        if name not in contract_present
    ]
    result = {
        "model": capability.model_key,
        "label": capability.label,
        "fxx": int(fxx),
        "available": False,
        "subset_opened": False,
        "provider": capability.provider,
        "transport": "wms-getfeatureinfo-point",
        "surface_contract_complete": not contract_missing,
        "surface_contract_present": contract_present,
        "surface_contract_missing": contract_missing,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
    }
    try:
        deadline = (
            None
            if deadline_seconds is None
            else time.monotonic() + max(0.001, float(deadline_seconds))
        )
        latest = _api.latest_reference_time(
            capability,
            request_get=request_get,
            cancelled=cancelled,
            timeout=(10, 30) if request_timeout is None else request_timeout,
            deadline=deadline,
        )
        run_dt = latest if run_time is None else _api._floor_cycle(
            run_time, capability.cycles
        )
        result["run"] = run_dt.strftime("%Y-%m-%d %H:%M")
        result["valid"] = (run_dt + timedelta(hours=int(fxx))).strftime(
            "%Y-%m-%d %H:%M"
        )
        result["latest_run"] = latest.strftime("%Y-%m-%d %H:%M")
        result["inventory_rows"] = (
            len(capability.pressure_levels) * len(_api._REQUIRED_VARIABLES)
            + len(capability.omega_levels)
            + len(_api._SURFACE_VARIABLES)
        )
        result["available"] = (
            int(fxx) in capability.forecast_hours
            and run_dt <= latest
            and run_dt >= latest - timedelta(days=2)
        )
        result["grib"] = _api.GEOMET_URL
    except DownloadCancelled:
        raise
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result
