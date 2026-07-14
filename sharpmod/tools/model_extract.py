"""Public forecast-model point-sounding extractor.

Fetches pressure-level forecast grids through Herbie, extracts the nearest
column, and writes the same portable ``.npz`` point-sounding format used by the
ERA5, IFS, HRRR, UWyo, and WRF paths.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from sharpmod.model_fields import choose_search
from sharpmod.model_transport import (
    DownloadCancelled,
    OptimizedTransportUnavailable,
    download_herbie_subset,
    ranges_from_inventory,
)
from sharpmod.model_sources import (
    SourceRoutingUnavailable,
    download_nomads_subset,
    nomads_supported,
    select_herbie_provider,
)
from sharpmod.hrrr_zarr import (
    ZarrBackendUnavailable,
    fetch_hrrr_zarr_point,
)

from sharpmod.tools.era5_extract import (
    ERA5ExtractionError as ModelExtractionError,
    ParameterRangeError,
    RetrievalError,
    _as_datetime,
    _atomic_write_json,
    _atomic_write_npz,
    _build_columns,
    _coord_values,
    _merge_datasets,
    _quiet_remove,
    _select_time,
    select_nearest_grid_point,
    _LAT_COORDS,
    _LON_COORDS,
)

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 360.0

NOAA_PRESSURE_SEARCH = (
    r":(HGT|TMP|RH|SPFH|UGRD|VGRD|VVEL|DZDT|ABSV):"
    r"\d+(?:\.\d+)? mb:"
)
IFS_PRESSURE_SEARCH = r":(gh|t|u|v|r|q|w|vo):\d+:pl:"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelConfig:
    """Herbie-backed forecast model configuration."""

    key: str
    label: str
    herbie_model: str
    product: str
    search: str = NOAA_PRESSURE_SEARCH
    cycles: tuple[int, ...] = (0, 6, 12, 18)
    default_fxx: int = 0
    fxx_values: tuple[int, ...] = ()
    domain: str = "Global"
    domain_bounds: tuple[float, float, float, float] = (
        -180.0, 180.0, -90.0, 90.0)
    kwargs: dict[str, object] = field(default_factory=dict)
    notes: str = ""


GLOBAL_DOMAIN = (-180.0, 180.0, -90.0, 90.0)
CONUS_DOMAIN = (-130.0, -60.0, 20.0, 55.0)


def _hours(stop, step=1):
    return tuple(range(0, int(stop) + 1, int(step)))


def _gfs_hours():
    return tuple(range(0, 121)) + tuple(range(123, 385, 3))


def _ifs_hours():
    return tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))


_CONFIGS = (
    ModelConfig(
        "hrrr", "HRRR", "hrrr", "prs", cycles=tuple(range(24)),
        fxx_values=_hours(48), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="3-km CONUS pressure-level forecast grids"),
    ModelConfig(
        "rap", "RAP", "rap", "awp130pgrb", cycles=tuple(range(24)),
        fxx_values=_hours(51), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="13-km RAP AWIPS pressure-level forecast grids"),
    ModelConfig(
        "nam", "NAM", "nam", "awphys",
        fxx_values=_hours(84, 3), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="12-km NAM CONUS pressure-level forecast grids"),
    ModelConfig(
        "nam-3km-conus", "NAM 3km CONUS", "nam", "conusnest.hiresf",
        fxx_values=_hours(60), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="NAM CONUS nest pressure-level forecast grids"),
    ModelConfig(
        "hrw-wrf-arw", "HRW WRF-ARW", "hiresw", "arw_5km",
        fxx_values=_hours(48), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="NOAA HiResW ARW 5-km pressure-level grids"),
    ModelConfig(
        "hrw-fv3", "HRW FV3", "hiresw", "fv3_5km",
        fxx_values=_hours(48), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="NOAA HiResW FV3 5-km pressure-level grids"),
    ModelConfig(
        "rrfs-a", "RRFS A", "rrfs", "prslev",
        fxx_values=_hours(60), domain="CONUS", domain_bounds=CONUS_DOMAIN,
        notes="RRFS-A 3-km pressure-level grids"),
    ModelConfig(
        "gfs", "GFS", "gfs", "pgrb2.0p25",
        fxx_values=_gfs_hours(),
        notes="0.25-degree GFS pressure-level forecast grids"),
    ModelConfig(
        "aigfs", "AIGFS", "aigfs", "pres",
        fxx_values=_hours(384, 6),
        notes="AI-GFS pressure-level grids; humidity from SPFH"),
    ModelConfig(
        "cfs", "CFS", "cfs", "6_hourly",
        fxx_values=_hours(384, 6),
        kwargs={"member": 1, "kind": "pgbf"},
        notes="CFS 6-hourly pressure-level grids, member 1 by default"),
    ModelConfig(
        "ecmwf-ifs", "ECMWF IFS Open Data", "ifs", "oper",
        search=IFS_PRESSURE_SEARCH, fxx_values=_ifs_hours(),
        notes="ECMWF open-data deterministic IFS pressure levels"),
    ModelConfig(
        "ecmwf-aifs", "ECMWF-AIFS", "aifs", "oper",
        search=IFS_PRESSURE_SEARCH, fxx_values=_ifs_hours(),
        notes="ECMWF open-data AIFS pressure levels"),
    ModelConfig(
        "gefs", "GEFS", "gefs", "atmos.5",
        fxx_values=_hours(384, 3),
        kwargs={"member": "c00"},
        notes="GEFS 0.5-degree control member by default"),
)

_ALIASES = {
    "hrrr": "hrrr",
    "rap": "rap",
    "nam": "nam",
    "nam3": "nam-3km-conus",
    "nam-3km": "nam-3km-conus",
    "nam-3km-conus": "nam-3km-conus",
    "hiresw-arw": "hrw-wrf-arw",
    "hrw-arw": "hrw-wrf-arw",
    "hrw-wrf-arw": "hrw-wrf-arw",
    "hiresw-fv3": "hrw-fv3",
    "hrw-fv3": "hrw-fv3",
    "rrfs": "rrfs-a",
    "rrfs-a": "rrfs-a",
    "gfs": "gfs",
    "aigfs": "aigfs",
    "cfs": "cfs",
    "ecmwf": "ecmwf-ifs",
    "ifs": "ecmwf-ifs",
    "ecmwf-ifs": "ecmwf-ifs",
    "aifs": "ecmwf-aifs",
    "ecmwf-aifs": "ecmwf-aifs",
    "gefs": "gefs",
}

_CONFIG_BY_KEY = {cfg.key: cfg for cfg in _CONFIGS}

UNSUPPORTED_MODELS = {
    "icon": "No Herbie loader is installed for ICON in this environment.",
    "ukmet": "UKMET global GRIB access is not public through Herbie here.",
    "eps": "ECMWF EPS products need separate ensemble handling.",
    "eps-opendata": "ECMWF EPS open data needs separate ensemble handling.",
    "cmce": "CMC ensemble members are not exposed by the installed Herbie loader.",
    "mogreps-g": "MOGREPS-G is not exposed by the installed Herbie loader.",
    "sref": "SREF is not exposed by the installed Herbie loader.",
    "gdps": "GDPS is public, but its files are split by variable/level.",
    "rdps": "RDPS is public, but its files are split by variable/level.",
    "hrdps": "HRDPS is public, but its files are split by variable/level.",
}


def available_models():
    """Return supported forecast model configs as a tuple."""
    return _CONFIGS


def _normalize_lon180(lon):
    return ((float(lon) + 180.0) % 360.0) - 180.0


def _coerce_config(model):
    return model if isinstance(model, ModelConfig) else get_config(model)


def forecast_hours(model, cycle_hour=None):
    """Return selectable forecast hours for ``model``.

    Most products have one cadence. HRRR is the exception here: the major
    synoptic cycles commonly carry longer forecasts than the off-hour cycles.
    """
    cfg = _coerce_config(model)
    values = cfg.fxx_values or (cfg.default_fxx,)
    if cfg.key == "hrrr" and cycle_hour is not None \
            and int(cycle_hour) not in (0, 6, 12, 18):
        return tuple(v for v in values if int(v) <= 18)
    return values


def domain_label(model):
    """Return a reader-facing model-domain label."""
    cfg = _coerce_config(model)
    return cfg.domain


def point_in_domain(model, lat, lon):
    """Return whether ``lat``/``lon`` is inside the model's configured domain."""
    cfg = _coerce_config(model)
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    lat = float(lat)
    lon = _normalize_lon180(lon)
    return lat0 <= lat <= lat1 and lon0 <= lon <= lon1


def domain_intersects_bounds(model, bounds):
    """Return whether a model domain intersects a map extent.

    ``bounds`` is ``(lon0, lon1, lat0, lat1)`` in degrees.
    """
    cfg = _coerce_config(model)
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    blo0, blo1, bla0, bla1 = bounds
    return not (lon1 < blo0 or lon0 > blo1 or lat1 < bla0 or lat0 > bla1)


def domain_contains_bounds(model, bounds):
    """Return whether a model domain fully contains a map extent."""
    cfg = _coerce_config(model)
    lon0, lon1, lat0, lat1 = cfg.domain_bounds
    blo0, blo1, bla0, bla1 = bounds
    return lon0 <= blo0 and lon1 >= blo1 and lat0 <= bla0 and lat1 >= bla1


def unsupported_models():
    """Return known screenshot models that are not selectable here yet."""
    return dict(UNSUPPORTED_MODELS)


def get_config(model):
    """Resolve a model key/alias to a :class:`ModelConfig`."""
    key = str(model).strip().lower()
    canonical = _ALIASES.get(key)
    if canonical is None:
        if key in UNSUPPORTED_MODELS:
            raise RetrievalError("%s is not enabled: %s" % (
                model, UNSUPPORTED_MODELS[key]))
        raise KeyError("unknown forecast model %r" % model)
    return _CONFIG_BY_KEY[canonical]


def _validate_lat_lon(lat, lon):
    if not (LAT_MIN <= lat <= LAT_MAX):
        raise ParameterRangeError(
            "latitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lat, LAT_MIN, LAT_MAX))
    if not (LON_MIN <= lon <= LON_MAX):
        raise ParameterRangeError(
            "longitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lon, LON_MIN, LON_MAX))


def _floor_to_cycle(dt, cycles):
    dt = _as_datetime(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    cycles = tuple(sorted(int(h) for h in cycles))
    hour = max((h for h in cycles if h <= dt.hour), default=cycles[-1])
    if hour > dt.hour:
        from datetime import timedelta
        dt = dt - timedelta(days=1)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def _run_datetime(run_time, config):
    if run_time is None:
        return _floor_to_cycle(datetime.now(timezone.utc), config.cycles)
    return _floor_to_cycle(run_time, config.cycles)


def _herbie_kwargs(config, member=None):
    kwargs = dict(config.kwargs)
    if member is not None:
        kwargs["member"] = member
    return kwargs


def _prepare_windows_eccodes_runtime():
    """Expose a bundled ecCodes DLL when no CPython helper wheel exists.

    ECMWF's Windows wheel normally includes a version-specific ``_eccodes``
    helper.  On Python versions for which that helper is not published, pip
    falls back to the pure-Python wheel even though the same installation still
    contains ``eccodes.dll`` and its dependencies.  Selecting findlibs mode and
    putting that package directory first on ``PATH`` lets the ABI-level CFFI
    bindings load the bundled DLL directly.
    """
    if sys.platform != "win32":
        return None

    try:
        spec = importlib.util.find_spec("eccodes")
        origin = getattr(spec, "origin", None)
        if not origin:
            return None
        package_dir = os.path.dirname(os.path.abspath(origin))
        package_files = os.listdir(package_dir)
    except (ImportError, OSError, ValueError):
        return None

    has_helper = any(
        name.startswith("_eccodes") and name.endswith(".pyd")
        for name in package_files
    )
    if has_helper or not os.path.isfile(os.path.join(package_dir, "eccodes.dll")):
        return None

    os.environ["ECCODES_PYTHON_USE_FINDLIBS"] = "1"
    current_path = os.environ.get("PATH", "")
    path_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    normalized = {os.path.normcase(os.path.abspath(entry)) for entry in path_entries}
    if os.path.normcase(package_dir) not in normalized:
        os.environ["PATH"] = os.pathsep.join([package_dir, *path_entries])
    return package_dir


def require_runtime_dependencies():
    """Load the native ecCodes boundary before starting a worker.

    ecCodes is a native extension.  Importing a partially installed build for
    the first time from a ``QThread`` can terminate the whole process before
    Python can report the import error.  The GUI calls this function on its
    main thread before it creates model-availability or model-fetch workers.
    The slower pure-Python Herbie, cfgrib, and xarray imports stay on those
    background workers.
    """
    _prepare_windows_eccodes_runtime()
    try:
        import eccodes

        # Importing the pure-Python ``eccodes`` wrapper can succeed even when
        # its binary extension is absent.  Calling the API version forces that
        # native boundary to be resolved here, on the main thread.
        eccodes.codes_get_api_version()
    except Exception as exc:  # pragma: no cover - environment-specific path
        hint = "Install the optional model stack with pip install -e \".[era5]\"."
        if sys.platform == "win32" and sys.version_info >= (3, 14):
            hint = (
                "The installed Windows ecCodes package is incomplete. "
                "Reinstall the [era5] extra, or use Python 3.11-3.13 if its "
                "native DLL still cannot be loaded."
            )
        raise RetrievalError(
            "forecast model support could not load its GRIB runtime: %s. %s"
            % (exc, hint)
        ) from exc


def _emit_progress(callback, stage, total_bytes=0):
    """Send one optional, dependency-free extraction progress event."""
    if callback is not None:
        callback(str(stage), max(0, int(total_bytes or 0)))


def _subset_download_bytes(herbie, search):
    """Return the planned coalesced byte-range transfer size."""
    try:
        inventory = herbie.inventory(search).copy()
        if len(inventory) == 0:
            return 0
        return sum(item.size for item in ranges_from_inventory(inventory))
    except Exception:
        # Progress estimation must never make an otherwise valid fetch fail.
        return 0


_NOMADS_MIN_RANGE_BYTES = 32 * 1024 * 1024


def _prefer_nomads_subset(expected_range_bytes):
    """Use CGI subsetting only when indexed ranges would be a large transfer."""
    size = max(0, int(expected_range_bytes or 0))
    return size == 0 or size > _NOMADS_MIN_RANGE_BYTES


def _planned_model_search(herbie, config):
    """Choose one field from each equivalent group without dropping levels."""
    try:
        inventory = herbie.inventory(config.search).copy()
        search, fields = choose_search(config, inventory)
        # Confirm that the narrower expression really matches the provider's
        # index before using it to name a persistent subset file.
        if len(herbie.inventory(search)) == 0:
            raise ValueError("planned model search matched no messages")
        return search, fields
    except Exception as exc:
        _LOGGER.info(
            "model_fields.fallback model=%s reason=%s", config.key, exc
        )
        return config.search, ()


def _point_backends_enabled():
    """Return whether Zarr/NOMADS point and subregion routes are enabled."""
    mode = os.environ.get("SHARPMOD_POINT_BACKENDS", "auto").strip().lower()
    return mode not in {"0", "false", "no", "off", "grib"}


def spatial_cache_key(config, lat, lon):
    """Return a point identity when an enabled backend can subset spatially."""
    if not _point_backends_enabled():
        return None
    if config.key != "hrrr" and not nomads_supported(config):
        return None
    lon180 = ((float(lon) + 180.0) % 360.0) - 180.0
    return f"{float(lat):.4f},{lon180:.4f}"


def hrrr_zarr_candidate(config, fxx, lat=None, lon=None):
    """Whether this request may use the point-only HRRR analysis archive."""
    mode = os.environ.get("SHARPMOD_HRRR_BACKEND", "auto").strip().lower()
    point_mode = os.environ.get("SHARPMOD_POINT_BACKENDS", "auto").strip().lower()
    return (
        config.key == "hrrr"
        and int(fxx) == 0
        and lat is not None
        and lon is not None
        and mode not in {"0", "false", "no", "off", "grib"}
        and point_mode not in {"0", "false", "no", "off", "grib"}
    )


def _retrieve_dataset(config, run_dt, fxx, member=None, download_dir=None,
                      progress_callback=None, cancelled=None, lat=None,
                      lon=None):
    """Fetch a Herbie pressure-level subset for ``config``."""
    _emit_progress(progress_callback, "locating")
    if hrrr_zarr_candidate(config, fxx, lat, lon):
        mode = os.environ.get("SHARPMOD_HRRR_BACKEND", "auto").strip().lower()
        _emit_progress(progress_callback, "downloading")
        try:
            dataset, source = fetch_hrrr_zarr_point(
                run_dt,
                int(fxx),
                float(lat),
                float(lon),
                cache_dir=download_dir,
                cancelled=cancelled,
            )
            _emit_progress(
                progress_callback,
                "decoding",
                getattr(source, "downloaded_bytes", 0),
            )
            return dataset, source
        except ZarrBackendUnavailable as exc:
            if mode == "zarr":
                raise RetrievalError(
                    "forced HRRR Zarr retrieval failed: %s" % exc
                ) from exc
            _LOGGER.info(
                "hrrr_zarr.fallback run=%s fxx=%03d reason=%s",
                run_dt.isoformat(), int(fxx), exc,
            )
    require_runtime_dependencies()
    try:
        from herbie import Herbie
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RetrievalError(
            "forecast model support requires the optional [era5] extra "
            "(herbie-data, cfgrib, xarray): %s" % exc) from exc

    try:  # pragma: no cover - live network / cache path
        H = Herbie(
            run_dt.strftime("%Y-%m-%d %H:%M"),
            model=config.herbie_model,
            product=config.product,
            fxx=int(fxx),
            verbose=False,
            **_herbie_kwargs(config, member=member),
        )
        if H.grib is None:
            raise RetrievalError(
                "no %s GRIB for run %s F%03d"
                % (config.label, run_dt.isoformat(), int(fxx)))
        # Herbie's download layer prints Unicode status glyphs by default.
        # Windows GUI/worker streams can use CP1252, where those glyphs raise
        # UnicodeEncodeError before the download even starts.
        xarray_kwargs = {"remove_grib": False, "verbose": False}
        download_kwargs = {"verbose": False}
        if download_dir is not None:
            xarray_kwargs["save_dir"] = os.fspath(download_dir)
            download_kwargs["save_dir"] = os.fspath(download_dir)
        search, selected_fields = _planned_model_search(H, config)
        expected_bytes = _subset_download_bytes(H, search)
        _emit_progress(progress_callback, "downloading", expected_bytes)
        # Herbie 2026.3.0 unconditionally prints an emoji when it creates its
        # download directory, even with ``verbose=False``.  Capturing stdout
        # keeps that third-party status message from crashing CP1252 Windows
        # GUI and worker processes; retrieval exceptions still propagate.
        transport = None
        source_url = str(H.grib)
        if lat is not None and lon is not None and selected_fields \
                and _point_backends_enabled() and nomads_supported(config) \
                and _prefer_nomads_subset(expected_bytes):
            try:
                _path, transferred_bytes, source_url = download_nomads_subset(
                    H,
                    config,
                    search,
                    selected_fields,
                    float(lat),
                    float(lon),
                    save_dir=download_dir,
                    cancelled=cancelled,
                )
                transport = "nomads-subregion"
                if transferred_bytes:
                    expected_bytes = int(transferred_bytes)
            except SourceRoutingUnavailable as exc:
                _LOGGER.info(
                    "model_sources.nomads_fallback model=%s run=%s fxx=%03d "
                    "reason=%s",
                    config.key, run_dt.isoformat(), int(fxx), exc,
                )
        if transport is None:
            if cancelled is not None and cancelled():
                raise DownloadCancelled("forecast-model download cancelled")
            try:
                select_herbie_provider(H)
            except Exception as exc:
                _LOGGER.info(
                    "model_sources.provider_fallback model=%s reason=%s",
                    config.key, exc,
                )
            source_url = str(H.grib)
            transport = "optimized-ranges"
            try:
                _path, transferred_bytes = download_herbie_subset(
                    H,
                    search,
                    save_dir=download_dir,
                    cancelled=cancelled,
                )
                if transferred_bytes:
                    expected_bytes = int(transferred_bytes)
            except OptimizedTransportUnavailable as exc:
                transport = "herbie"
                _LOGGER.info(
                    "model_transport.fallback model=%s run=%s fxx=%03d "
                    "reason=%s",
                    config.key, run_dt.isoformat(), int(fxx), exc,
                )
                with redirect_stdout(io.StringIO()):
                    H.download(search, **download_kwargs)
        if cancelled is not None and cancelled():
            raise DownloadCancelled("forecast-model download cancelled")
        _emit_progress(progress_callback, "decoding", expected_bytes)
        with redirect_stdout(io.StringIO()):
            ds = H.xarray(search, **xarray_kwargs)
        if isinstance(ds, list):
            ds = _merge_datasets(ds)
        H._sharpmod_fields = selected_fields
        H._sharpmod_search = search
        H._sharpmod_transport = transport
        H._sharpmod_source_url = source_url
        return ds, H
    except (RetrievalError, DownloadCancelled):
        raise
    except Exception as exc:  # pragma: no cover - live failure path
        raise RetrievalError(
            "failed to retrieve %s data for %s F%03d: %s"
            % (config.label, run_dt.isoformat(), int(fxx), exc)) from exc


def _selected_valid(ds_t, selected_time, run_dt, fxx):
    _, vt = _coord_values(ds_t, ("valid_time",))
    if vt is not None and vt.size:
        return _as_datetime(vt.reshape(-1)[0])
    if selected_time is not None:
        return _as_datetime(selected_time)
    from datetime import timedelta
    return run_dt + timedelta(hours=int(fxx))


def extract(model, lat, lon, run_time=None, fxx=0, out_path=None, loc=None,
            member=None, dataset=None, download_dir=None,
            source_grib=None, source_fields=None, source_transport=None,
            progress_callback=None, cancelled=None):
    """Extract a public forecast-model point sounding to ``out_path``.

    Parameters mirror the CLI: choose a supported ``model`` key, a latitude and
    longitude, a model run time/cycle, and a forecast hour.
    """
    config = get_config(model)
    lat = float(lat)
    lon = float(lon)
    fxx = int(fxx if fxx is not None else config.default_fxx)
    _validate_lat_lon(lat, lon)
    if not point_in_domain(config, lat, lon):
        raise ParameterRangeError(
            "%s covers %s (%s); requested point %.4f, %.4f is outside "
            "that domain" % (
                config.label, config.domain, config.domain_bounds, lat, lon))

    run_dt = _run_datetime(run_time, config)
    if out_path is None:
        out_path = "%s_point_%.2fN_%.2fE_%s_f%03d.npz" % (
            config.key.replace("-", "_"),
            lat,
            lon,
            run_dt.strftime("%Y%m%d%H"),
            fxx,
        )

    if dataset is None:
        retrieve_kwargs = {
            "member": member,
            "download_dir": download_dir,
            "lat": lat,
            "lon": lon,
        }
        if progress_callback is not None:
            retrieve_kwargs["progress_callback"] = progress_callback
        if cancelled is not None:
            retrieve_kwargs["cancelled"] = cancelled
        ds, H = _retrieve_dataset(config, run_dt, fxx, **retrieve_kwargs)
    else:
        ds = dataset
        H = None

    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")
    _emit_progress(progress_callback, "extracting")
    ds_t, selected_time = _select_time(ds, run_dt)
    _, lats = _coord_values(ds_t, _LAT_COORDS)
    _, lons = _coord_values(ds_t, _LON_COORDS)
    if lats is None or lons is None:
        raise RetrievalError("%s dataset is missing latitude/longitude coordinates"
                             % config.label)

    lon_req = lon
    try:
        if np.nanmin(lons) >= 0.0 and lon < 0.0:
            lon_req = lon + 360.0
    except Exception:
        pass

    index_tuple, glat, glon = select_nearest_grid_point(
        lats, lons, lat, lon_req)
    glon = ((glon + 180.0) % 360.0) - 180.0

    cols, n_levels = _build_columns(ds_t, index_tuple, latitude=glat)
    valid_dt = _selected_valid(ds_t, selected_time, run_dt, fxx)
    if dataset is None:
        try:
            ds_t.close()
        finally:
            if ds_t is not ds:
                ds.close()
    run_str = run_dt.strftime("%Y-%m-%d %H:%M")
    valid_str = valid_dt.strftime("%Y-%m-%d %H:%M")
    loc_label = loc or "%s %.2f, %.2f" % (config.label, glat, glon)

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"], "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": glat, "lon": glon, "loc": loc_label, "model": config.label,
        "run": run_str, "valid": valid_str, "fxx": fxx,
    }
    if "surface_relative_vorticity" in cols:
        arrays["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    meta = {
        "model": config.label,
        "model_key": config.key,
        "loc": loc_label,
        "requested_lat": lat,
        "requested_lon": lon,
        "selected_lat": glat,
        "selected_lon": glon,
        "run": run_str,
        "valid": valid_str,
        "fxx": fxx,
        "npz": os.path.abspath(out_path),
        "levels": int(n_levels),
        "herbie_model": config.herbie_model,
        "product": config.product,
    }
    if member is not None:
        meta["member"] = str(member)
    elif "member" in config.kwargs:
        meta["member"] = str(config.kwargs["member"])
    if H is not None:
        source_grib = getattr(
            H, "_sharpmod_source_url", getattr(H, "grib", "")
        )
        source_fields = getattr(H, "_sharpmod_fields", source_fields)
        source_transport = getattr(H, "_sharpmod_transport", source_transport)
    if source_grib:
        meta["source_grib"] = str(source_grib)
    if source_fields:
        meta["fields"] = list(source_fields)
    if source_transport:
        meta["transport"] = str(source_transport)
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")
    _emit_progress(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        _quiet_remove(out_path)
        raise
    _emit_progress(progress_callback, "complete")
    return out_path


def cleanup_transient_data(npz_path=None, download_dir=None):
    """Remove fetched model artifacts while preserving a rendered PNG.

    ``npz_path`` and its JSON sidecar may live outside ``download_dir`` (the
    CLI permits an explicit output path), so both are removed before the
    isolated Herbie download tree. Missing paths are intentionally harmless so
    this helper is safe in render/fetch failure ``finally`` blocks.
    """
    if npz_path:
        npz_path = os.fspath(npz_path)
        _quiet_remove(npz_path)
        _quiet_remove(os.path.splitext(npz_path)[0] + ".json")
    if download_dir:
        shutil.rmtree(os.fspath(download_dir), ignore_errors=True)


def probe(model, run_time=None, fxx=0, member=None, open_subset=False):
    """Return a live availability probe dict for one supported model."""
    config = get_config(model)
    run_dt = _run_datetime(run_time, config)
    result = {
        "model": config.key,
        "label": config.label,
        "run": run_dt.strftime("%Y-%m-%d %H:%M"),
        "fxx": int(fxx),
        "available": False,
        "subset_opened": False,
    }
    try:
        require_runtime_dependencies()
        from herbie import Herbie
        H = Herbie(
            run_dt.strftime("%Y-%m-%d %H:%M"),
            model=config.herbie_model,
            product=config.product,
            fxx=int(fxx),
            verbose=False,
            **_herbie_kwargs(config, member=member),
        )
        result["grib"] = str(H.grib)
        inv = H.inventory()
        result["inventory_rows"] = 0 if inv is None else int(len(inv))
        result["available"] = H.grib is not None and inv is not None and len(inv) > 0
        if open_subset and result["available"]:
            ds = H.xarray(config.search, remove_grib=False)
            if isinstance(ds, list):
                ds = _merge_datasets(ds)
            result["subset_opened"] = True
            result["data_vars"] = sorted(str(v) for v in ds.data_vars)
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def _parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")


def main(argv=None):  # pragma: no cover - CLI wrapper
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="model-extract",
        description="Extract public forecast-model point soundings to .npz")
    parser.add_argument("model", nargs="?", help="model key; use --list")
    parser.add_argument("lat", nargs="?", type=float)
    parser.add_argument("lon", nargs="?", type=float)
    parser.add_argument("out", nargs="?", default=None)
    parser.add_argument("--run", default=None,
                        help="model run/cycle time, ISO or 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--fxx", type=int, default=0, help="forecast hour")
    parser.add_argument("--member", default=None,
                        help="ensemble/member override, e.g. GEFS c00 or p01")
    parser.add_argument("--loc", default=None, help="location label")
    parser.add_argument("--render", nargs="?", const="", default=None,
                        metavar="PNG", help="also render the sounding to PNG")
    parser.add_argument("--list", action="store_true",
                        help="list supported and known unsupported models")
    parser.add_argument("--probe", action="store_true",
                        help="check inventory availability for a model")
    parser.add_argument("--open-subset", action="store_true",
                        help="with --probe, open the pressure-level subset too")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        print("Supported forecast models:")
        for cfg in available_models():
            fxx = forecast_hours(cfg)
            fxx_label = "F%03d-F%03d" % (min(fxx), max(fxx)) if fxx else "F---"
            print("  %-15s %-24s %-16s %-11s %s/%s" % (
                cfg.key, cfg.label, cfg.domain, fxx_label,
                cfg.herbie_model, cfg.product))
        print("\nKnown but not enabled:")
        for key, reason in sorted(unsupported_models().items()):
            print("  %-15s %s" % (key, reason))
        return 0

    if not args.model:
        parser.error("model is required unless --list is used")

    run = _parse_time(args.run) if args.run else None
    if args.probe:
        info = probe(
            args.model, run_time=run, fxx=args.fxx, member=args.member,
            open_subset=args.open_subset)
        for key in sorted(info):
            print("%s: %s" % (key, info[key]))
        return 0 if info.get("available") else 1

    if args.lat is None or args.lon is None:
        parser.error("lat and lon are required for extraction")
    transient = args.render is not None
    download_dir = tempfile.mkdtemp(prefix="sharpmod-model-") \
        if transient else None
    path = None
    try:
        try:
            path = extract(
                args.model, args.lat, args.lon, run_time=run, fxx=args.fxx,
                out_path=args.out, loc=args.loc, member=args.member,
                download_dir=download_dir)
        except (ModelExtractionError, KeyError) as exc:
            print("ERROR: %s" % exc)
            return 1
        print("wrote %s" % path)

        if transient:
            from sharpmod.tools import render_npz
            png = render_npz(path, args.render or None)
            print("rendered %s" % png)
        return 0
    finally:
        if transient:
            cleanup_transient_data(path or args.out, download_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
