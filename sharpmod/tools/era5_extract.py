"""ERA5 point-sounding extractor (``ERA5_Extractor``).

Extract a *point* sounding from ERA5 reanalysis at an arbitrary latitude,
longitude, and valid time and write it in the fork's ``.npz`` point-sounding
format so it renders through the **same** code path as the HRRR ``.npz``
sidecar (Requirement 8.4, see :func:`sharpmod.io.decoder.load_npz`).

Behaviour (Requirement 8):

* Select the ERA5 grid point with the smallest horizontal **great-circle**
  distance to the requested location and the ERA5 analysis time closest to the
  requested valid time, then extract the vertical column there
  (Requirements 8.1).
* Populate pressure, height, temperature, dewpoint, and the zonal (``u``) and
  meridional (``v``) wind components for every ERA5 pressure level present in
  the source (Requirement 8.2). The ``u``/``v`` components are converted to the
  wind-direction / wind-speed columns the ``.npz`` point-sounding format stores
  so the output loads through the shared renderer path.
* Mark a level's affected value **missing** (the ``-9999.0`` sentinel the
  ``.npz`` loader understands) when a required field is absent/masked at that
  level, rather than writing an interpolated or placeholder number
  (Requirement 8.3).
* Validate the requested latitude, longitude, and time against ERA5 coverage
  and return an error that names the out-of-range parameter and its permitted
  range **without writing any output file** (Requirement 8.5); a retrieval
  failure likewise leaves no partial file (Requirement 8.6).
* Write to a temporary file and **atomically rename** on success, so a partial
  or corrupt output is never left behind.
* Record the requested source lat/lon/time and the selected grid-point lat/lon
  and analysis time in a ``.json`` metadata sidecar (Requirement 8.7).

The ERA5 tooling (``cdsapi``, ``cfgrib``, ``xarray``) is an optional
``[era5]`` install extra, so those packages are imported **lazily** inside the
functions that need them; importing this module never requires them.
"""

import json
import importlib
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np

from sharpmod import backends as _backends
from sharpmod.export_paths import export_file_path
from sharpmod.models.model_surface import SURFACE_CONTRACT_VERSION, merge_surface_level
from sharpmod.io.portable_sounding import fatal_issues
from sharpmod.upstream.upstream_warnings import xarray_new_combine_defaults

__all__ = [
    "extract",
    "ERA5ExtractionError",
    "ExtractionCancelled",
    "ParameterRangeError",
    "RetrievalError",
    "require_runtime_dependencies",
    "great_circle_distance_km",
    "select_nearest_grid_point",
    "select_nearest_time",
]

# The ``.npz`` point-sounding loader (``load_npz``) treats this value as the
# missing/mask sentinel, so per-level missing fields are written as ``-9999.0``.
MISSING = -9999.0

# Standard gravity: geopotential (m^2 s^-2) / G0 -> geopotential height (m).
G0 = 9.80665

# ERA5 coverage bounds used for input validation before retrieval.
LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 360.0
# ERA5 (including the ERA5 back-extension) begins in 1940; the upper bound is
# resolved against "now" at call time.
ERA5_START = datetime(1940, 1, 1, tzinfo=timezone.utc)

ERA5_CDS_DATASET = "reanalysis-era5-pressure-levels"
ERA5_CDS_SURFACE_DATASET = "reanalysis-era5-single-levels"
ERA5_CDS_VARIABLES = (
    "geopotential",
    "relative_humidity",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
    "vertical_velocity",
)
ERA5_CDS_SURFACE_VARIABLES = (
    "surface_pressure",
    "geopotential",
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
)
ERA5_PRESSURE_LEVELS = (
    "1", "2", "3", "5", "7", "10", "20", "30", "50", "70", "100",
    "125", "150", "175", "200", "225", "250", "300", "350", "400",
    "450", "500", "550", "600", "650", "700", "750", "775", "800",
    "825", "850", "875", "900", "925", "950", "975", "1000",
)


class ERA5ExtractionError(Exception):
    """Base class for all ERA5 extraction failures."""


class ParameterRangeError(ERA5ExtractionError):
    """A requested lat/lon/time lies outside ERA5 coverage (Requirement 8.5)."""


class RetrievalError(ERA5ExtractionError):
    """ERA5 source data could not be retrieved (Requirement 8.6)."""


class ExtractionCancelled(ERA5ExtractionError):
    """A cooperative ERA5/WRF extraction cancellation was requested."""


def _emit_progress(progress_callback, stage):
    if progress_callback is not None:
        progress_callback(str(stage))


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise ExtractionCancelled("point-sounding extraction cancelled")


def _cds_terms_error(exc):
    message = str(exc).lower()
    return any(token in message for token in (
        "accept the terms",
        "licence not accepted",
        "license not accepted",
        "required licences",
        "required licenses",
        "terms of use",
    ))


def require_runtime_dependencies(require_credentials=True):
    """Validate the optional ERA5 runtime and CDS profile without a request.

    The check deliberately never returns or logs the CDS key.  Constructing a
    ``cdsapi.Client`` only reads its configuration; dataset-term acceptance is
    checked by CDS when the first real retrieval is submitted.
    """
    modules = {}
    missing = []
    for name in ("cdsapi", "cfgrib", "xarray"):
        try:
            modules[name] = importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - environment dependent
            missing.append(f"{name} ({exc})")
    if missing:
        raise RetrievalError(
            "ERA5 support is missing optional dependencies: %s. Install "
            "them with: pip install -e \".[era5]\"" % ", ".join(missing)
        )
    if require_credentials:
        try:
            _new_cds_client(modules["cdsapi"])
        except Exception as exc:
            if _is_cds_credential_error(exc):
                raise RetrievalError(
                    "ERA5 retrieval requires CDS API credentials. Create a "
                    "free Climate Data Store account, accept both the ERA5 "
                    "pressure-level and single-level dataset terms, then save "
                    "the API profile as $HOME/.cdsapirc (or configure "
                    "CDSAPI_URL and CDSAPI_KEY)."
                ) from exc
            raise RetrievalError(
                "could not initialize the Copernicus CDS client: %s" % exc
            ) from exc
    return True


# ---------------------------------------------------------------------------
# Geometry / selection helpers
# ---------------------------------------------------------------------------

def great_circle_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in kilometres.

    Accepts scalars or NumPy arrays for ``lat2``/``lon2`` (broadcasts against
    the scalar request point). Longitude wrap-around is handled naturally by
    the haversine formula, so mixed 0..360 / -180..180 conventions are safe.
    """
    r_earth = 6371.0088
    lat1r = np.radians(lat1)
    lat2r = np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2.0) ** 2
         + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2)
    a = np.clip(a, 0.0, 1.0)
    return 2.0 * r_earth * np.arcsin(np.sqrt(a))


def select_nearest_grid_point(lats, lons, lat0, lon0):
    """Return the grid index minimizing great-circle distance to ``(lat0, lon0)``.

    Supports scalar coordinates (a one-point subset), 1-D coordinate vectors
    (a regular lat/lon grid), and 2-D coordinate arrays (a curvilinear grid).
    Returns
    ``(index_tuple, selected_lat, selected_lon)`` where ``index_tuple`` indexes
    the data arrays (``(ilat, ilon)`` for a regular grid, ``(iy, ix)`` for a
    2-D grid).
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)

    if lats.ndim == 0 and lons.ndim == 0:
        # A zero-area CDS request is decoded by cfgrib with scalar horizontal
        # coordinates and level-only data variables.  Keep the conventional
        # two-index return shape; the column extractor ignores it for 1-D
        # level arrays.
        return (0, 0), float(lats), float(lons)

    if lats.ndim <= 1 and lons.ndim <= 1:
        # A geographic subset can be one cell wide on a single axis, and cfgrib
        # then presents that axis as a scalar coordinate while the other stays a
        # vector. Promoting both keeps the separable search below; falling
        # through to the 2-D branch instead broadcast to a 1-D distance array
        # that cannot be unravelled into the two indices it returns.
        lats = np.atleast_1d(lats)
        lons = np.atleast_1d(lons)
        # On a Cartesian product of independent latitude/longitude axes, the
        # spherical dot product is separable.  The longitude that maximizes
        # cos(delta_lon) is optimal for every latitude because cos(latitude)
        # is non-negative over the geographic range.  Choosing that longitude
        # first and then maximizing the remaining latitude score is therefore
        # exactly equivalent to the full great-circle mesh search, without
        # allocating several nlat-by-nlon temporary arrays.
        lat0r = np.radians(float(lat0))
        latr = np.radians(lats)
        lon_delta = np.radians(lons - float(lon0))
        ilon = int(np.argmax(np.cos(lon_delta)))
        lon_score = float(np.cos(lon_delta[ilon]))
        score = (
            np.sin(lat0r) * np.sin(latr)
            + np.cos(lat0r) * np.cos(latr) * lon_score
        )
        ilat = int(np.argmax(score))
        return (int(ilat), int(ilon)), float(lats[ilat]), float(lons[ilon])

    dist = great_circle_distance_km(lat0, lon0, lats, lons)
    iy, ix = np.unravel_index(np.argmin(dist), dist.shape)
    return (int(iy), int(ix)), float(lats[iy, ix]), float(lons[iy, ix])


def select_nearest_time(times, target):
    """Return ``(index, selected_time)`` closest to ``target``.

    ``times`` is a sequence of datetimes (or NumPy datetime64); ``target`` is a
    datetime. Ties resolve to the earlier time.
    """
    target_ts = _to_epoch(target)
    best_i, best_dt, best_delta = 0, None, None
    for i, t in enumerate(times):
        dt = _as_datetime(t)
        delta = abs(_to_epoch(dt) - target_ts)
        if best_delta is None or delta < best_delta:
            best_i, best_dt, best_delta = i, dt, delta
    return best_i, best_dt


# ---------------------------------------------------------------------------
# Thermodynamic helpers
# ---------------------------------------------------------------------------

def uv_to_dir_spd(u, v):
    """Zonal/meridional wind (m/s) -> (met direction degrees, speed knots)."""
    wdir, spd = _backends.components_to_wind(u, v, missing=None)
    wdir = np.asarray(np.ma.filled(wdir, np.nan), dtype=float)
    spd = np.asarray(np.ma.filled(spd, np.nan), dtype=float) * 1.94384449
    return wdir, spd


def dewpoint_from_rh(tmpc, rh):
    """Dewpoint (degrees C) from temperature (deg C) and relative humidity (%).

    Uses the Magnus-Tetens approximation. NaNs propagate as NaN so the caller
    can mark the level missing.
    """
    a, b = 17.625, 243.04
    rh = np.clip(np.asarray(rh, dtype=float), 1e-3, 100.0)
    tmpc = np.asarray(tmpc, dtype=float)
    gamma = np.log(rh / 100.0) + (a * tmpc) / (b + tmpc)
    return (b * gamma) / (a - gamma)


def dewpoint_from_specific_humidity(q, pres_hpa):
    """Dewpoint (deg C) from specific humidity (kg/kg) and pressure (hPa)."""
    q = np.asarray(q, dtype=float)
    pres_hpa = np.asarray(pres_hpa, dtype=float)
    # Vapor pressure (hPa) from specific humidity.
    e = (q * pres_hpa) / (0.622 + 0.378 * q)
    e = np.clip(e, 1e-6, None)
    a, b = 17.625, 243.04
    ln = np.log(e / 6.112)
    return (b * ln) / (a - ln)


# ---------------------------------------------------------------------------
# Dataset field access
# ---------------------------------------------------------------------------

# Candidate names for each field across cfgrib / native ERA5 conventions.
_LEVEL_COORDS = ("isobaricInhPa", "level", "pressure_level", "plev", "levels")
_TIME_COORDS = ("time", "valid_time", "forecast_time")
_LAT_COORDS = ("latitude", "lat")
_LON_COORDS = ("longitude", "lon")

_VAR_TEMP = ("t", "temperature")
_VAR_GEOPOTENTIAL = ("z", "geopotential")
_VAR_GEOPOTENTIAL_HEIGHT = ("gh", "geopotential_height", "hgt")
_VAR_RH = ("r", "relative_humidity")
_VAR_Q = ("q", "specific_humidity", "spfh")
_VAR_U = ("u", "u_component_of_wind", "ugrd")
_VAR_V = ("v", "v_component_of_wind", "vgrd")
_VAR_W = ("w", "vertical_velocity", "vvel", "dzdt")
_VAR_RELATIVE_VORTICITY = ("vo", "vort", "relative_vorticity", "relv")
_VAR_ABSOLUTE_VORTICITY = ("absv", "absolute_vorticity")
_VAR_SURFACE_PRESSURE = ("sp", "surface_pressure")
_VAR_SURFACE_HEIGHT = (
    "orog",
    "surface_geopotential",
    "surface_geopotential_height",
    "surface_height",
)
_VAR_2M_TEMP = ("t2m", "2t", "2_metre_temperature")
_VAR_2M_DEWPOINT = ("d2m", "2d", "2_metre_dewpoint_temperature")
_VAR_2M_RH = ("r2", "rh2m", "2_metre_relative_humidity")
_VAR_2M_Q = ("q2", "sh2", "2_metre_specific_humidity")
_VAR_10M_U = ("u10", "10u", "10_metre_u_wind_component")
_VAR_10M_V = ("v10", "10v", "10_metre_v_wind_component")

EARTH_RADIUS_M = 6371008.8
EARTH_ROTATION_RATE = 7.2921159e-5


def _first_present(container, candidates):
    """Return the first name in ``candidates`` present in ``container``."""
    for name in candidates:
        if name in container:
            return name
    return None


def _coord_values(ds, candidates):
    """Return ``(name, numpy_values)`` for the first present coordinate."""
    name = _first_present(ds.coords, candidates)
    if name is None:
        name = _first_present(ds, candidates)
    if name is None:
        return None, None
    return name, np.asarray(ds[name].values)


def _promote_scalar_level_coordinate(dataset):
    """Make a one-level pressure coordinate a real dimension before merging.

    cfgrib represents a field published at only one pressure level as a scalar
    coordinate.  xarray otherwise drops that field's pressure relationship
    when it is outer-merged with multi-level groups and broadcasts the value as
    though it had no vertical dimension.
    """
    level_name = _first_present(dataset.coords, _LEVEL_COORDS)
    if level_name is None or dataset[level_name].ndim != 0:
        return dataset
    return dataset.expand_dims(level_name)


def _coriolis_parameter(latitude):
    """Return the Coriolis parameter at ``latitude`` in s^-1."""
    return float(2.0 * EARTH_ROTATION_RATE * np.sin(np.radians(latitude)))


def _wrapped_lon_delta(lon1, lon2):
    """Signed longitude delta from ``lon1`` to ``lon2`` in degrees."""
    return ((float(lon2) - float(lon1) + 180.0) % 360.0) - 180.0


def _east_west_distance_m(lat, lon1, lon2):
    """Approximate signed east-west distance between two longitudes."""
    return (
        EARTH_RADIUS_M
        * np.cos(np.radians(float(lat)))
        * np.radians(_wrapped_lon_delta(lon1, lon2))
    )


def _north_south_distance_m(lat1, lat2):
    """Approximate signed north-south distance between two latitudes."""
    return EARTH_RADIUS_M * np.radians(float(lat2) - float(lat1))


def _to_epoch(dt):
    """Seconds since the Unix epoch for a datetime (UTC-normalized)."""
    dt = _as_datetime(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _as_datetime(value):
    """Coerce datetime64 / datetime / ISO string to a ``datetime``."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, np.datetime64):
        ns = value.astype("datetime64[ns]").astype("int64")
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_request(lat, lon, valid_time):
    """Static range validation (Requirement 8.5). Raises before any I/O."""
    if not (LAT_MIN <= lat <= LAT_MAX):
        raise ParameterRangeError(
            "latitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lat, LAT_MIN, LAT_MAX))

    if not (LON_MIN <= lon <= LON_MAX):
        raise ParameterRangeError(
            "longitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees (ERA5 global coverage)"
            % (lon, LON_MIN, LON_MAX))

    vt = _as_datetime(valid_time)
    if vt.tzinfo is None:
        vt = vt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if vt < ERA5_START or vt > now:
        raise ParameterRangeError(
            "valid time %s is out of range; permitted range is [%s, %s] "
            "(ERA5 temporal coverage)"
            % (vt.isoformat(), ERA5_START.isoformat(), now.isoformat()))
    return vt


def _refine_coverage(ds, lon):
    """Tighten longitude validation against the actual dataset coordinates."""
    _, lons = _coord_values(ds, _LON_COORDS)
    if lons is not None and lons.size:
        # A zero-area retrieval contains only the grid point nearest the
        # request.  Its singleton coordinate is a selected result, not the
        # longitude coverage of the global source dataset.
        if lons.size == 1:
            return
        # Compare in a common 0..360 frame so wrapped requests still validate.
        lo, hi = float(np.min(lons)), float(np.max(lons))
        lon360 = lon % 360.0
        lons360 = lons % 360.0
        lo360, hi360 = float(np.min(lons360)), float(np.max(lons360))
        in_native = lo <= lon <= hi
        in_wrapped = lo360 <= lon360 <= hi360
        if not (in_native or in_wrapped):
            raise ParameterRangeError(
                "longitude %.4f is outside the ERA5 grid coverage "
                "[%.4f, %.4f]" % (lon, lo, hi))

    # NB: the ERA5 archive's temporal coverage (1940..now) is enforced by
    # _validate_request. A retrieved dataset typically carries a single
    # analysis slice (or discrete hourly steps); the requested time is snapped
    # to the closest of those by select_nearest_time, so no additional
    # per-slice temporal bound is imposed here (that would wrongly reject
    # requests that fall between/around available analysis times).


# ---------------------------------------------------------------------------
# Column extraction
# ---------------------------------------------------------------------------

def _select_time(ds, valid_time):
    """Return ``(ds_at_time, selected_time)`` for the nearest analysis time."""
    tname, times = _coord_values(ds, _TIME_COORDS)
    if tname is None or times is None or times.ndim == 0 or times.size <= 1:
        # Single-time dataset; recover the scalar time if present.
        if tname is not None and times is not None and times.size:
            return ds, _as_datetime(times.reshape(-1)[0])
        return ds, _as_datetime(valid_time)
    idx, selected = select_nearest_time(list(times), valid_time)
    return ds.isel({tname: idx}), selected


def _horizontal_indexers(ds, index_tuple):
    """Map a selected horizontal index onto xarray dimension names."""
    lat_name = _first_present(ds.coords, _LAT_COORDS)
    lon_name = _first_present(ds.coords, _LON_COORDS)
    if lat_name is None or lon_name is None:
        return {}, (None, None)

    lat_coord = ds[lat_name]
    lon_coord = ds[lon_name]
    if lat_coord.ndim == 0 and lon_coord.ndim == 0:
        return {}, (None, None)

    iy, ix = (int(index_tuple[0]), int(index_tuple[1]))
    if lat_coord.ndim <= 1 and lon_coord.ndim <= 1:
        # cfgrib collapses a one-cell horizontal axis to a scalar coordinate.
        # Keep indexing the surviving vector axis so its selected metadata and
        # its extracted data column cannot disagree.
        ydim = lat_coord.dims[0] if lat_coord.ndim == 1 else None
        xdim = lon_coord.dims[0] if lon_coord.ndim == 1 else None
        indexers = {}
        if ydim is not None:
            indexers[ydim] = iy
        if xdim is not None:
            indexers[xdim] = ix
        return indexers, (ydim, xdim)
    elif lat_coord.ndim >= 2 and lon_coord.ndim >= 2:
        ydim, xdim = lat_coord.dims[-2:]
    else:
        return {}, (None, None)
    return {ydim: iy, xdim: ix}, (ydim, xdim)


def _column(ds, iy, ix, index_is_regular):
    """Return the ``(level, lat, lon)`` -> per-level column extractor.

    Produces a helper that pulls a variable's vertical column at the selected
    grid point, returning a 1-D array over levels or ``None`` if the variable
    is absent.
    """
    def get(candidates):
        name = _first_present(ds, candidates)
        if name is None:
            return None
        data = ds[name]
        indexers, _dims = _horizontal_indexers(ds, (iy, ix))
        applicable = {
            dim: index for dim, index in indexers.items()
            if dim in data.dims
        }
        if applicable:
            # Crucially, index the lazy cfgrib array before asking xarray for
            # NumPy values.  Materializing first decoded/cast every grid cell
            # and returned a tiny view retaining the multi-hundred-MiB base.
            data = data.isel(applicable)
        arr = np.asarray(data.values, dtype=float)
        arr = np.squeeze(arr)
        if arr.ndim == 0:
            return arr.reshape(1)
        if arr.ndim == 1:
            # Already reduced to a level vector.
            return arr
        if arr.ndim == 3:
            return arr[:, iy, ix]
        if arr.ndim == 2:
            # (level, point) or (lat, lon) with a single level.
            return arr[:, ix] if index_is_regular else arr.reshape(-1)
        return None

    return get


def _mark_missing(arr, n_levels):
    """Return a copy with NaN/inf replaced by the MISSING sentinel.

    ``None`` inputs (absent field) become an all-missing column so the level is
    still written but marked missing (Requirement 8.3).
    """
    if arr is None:
        return np.full(n_levels, MISSING, dtype=float)
    out = np.asarray(arr, dtype=float).copy()
    out[~np.isfinite(out)] = MISSING
    return out


def _first_surface_value(values):
    """Return the first finite, non-missing value in a bottom-up column."""
    if values is None:
        return None
    arr = np.asarray(values, dtype=float).reshape(-1)
    for value in arr:
        if np.isfinite(value) and value != MISSING:
            return float(value)
    return None


def _surface_relative_vorticity_from_column(values, levels, latitude=None,
                                            absolute=False,
                                            surface_pressure=None):
    """Return the bottom-most relative-vorticity value from a level column."""
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    if absolute:
        if latitude is None:
            return None
        arr = arr - _coriolis_parameter(latitude)
    if arr.size != np.asarray(levels).size:
        return None
    levels = np.asarray(levels, dtype=float)
    order = np.argsort(-levels)
    ordered = _mark_missing(arr, arr.size)[order]
    if surface_pressure is not None:
        # A pressure level exactly coincident with the verified ground is a
        # valid near-surface vorticity sample even though the aligned sounding
        # row is replaced by the higher-quality 2 m/10 m surface merge.
        ordered = ordered[levels[order] <= float(surface_pressure)]
    return _first_surface_value(ordered)


def _neighbor_pair(index, size):
    """Return adjacent indexes around ``index`` for a finite difference."""
    if size < 2:
        return None
    if index <= 0:
        return 0, 1
    if index >= size - 1:
        return size - 2, size - 1
    return index - 1, index + 1






def _require_profile_qc(columns, label):
    """Reject physically inconsistent output before it reaches disk/cache."""
    result = _backends.basic_sounding_qc(
        columns["pres"],
        columns["hght"],
        columns["tmpc"],
        columns["dwpc"],
        columns["wdir"],
        columns["wspd"],
        missing=MISSING,
    )
    # Only genuinely unusable data is refused; a dewpoint above the temperature
    # is contaminated rather than unusable and travels on in ``qc_issues``.
    fatal = fatal_issues(result.issues)
    if fatal:
        raise RetrievalError(
            "%s sounding failed physical quality control: %s"
            % (label, ", ".join(fatal))
        )
    return result


# ---------------------------------------------------------------------------
# Retrieval (lazy optional dependency)
# ---------------------------------------------------------------------------

def _nearest_era5_grid_point(lat, lon):
    """Snap a request to the regular 0.25-degree CDS ERA5 grid."""
    grid_lat = round(float(lat) * 4.0) / 4.0
    lon180 = ((float(lon) + 180.0) % 360.0) - 180.0
    grid_lon = round(lon180 * 4.0) / 4.0
    return grid_lat, grid_lon


def _cds_pressure_level_request(lat, lon, valid_time):
    """Build the smallest CDS request that contains one sounding column."""
    vt = _as_datetime(valid_time)
    grid_lat, grid_lon = _nearest_era5_grid_point(lat, lon)
    return {
        "product_type": "reanalysis",
        "variable": list(ERA5_CDS_VARIABLES),
        "pressure_level": list(ERA5_PRESSURE_LEVELS),
        "year": vt.strftime("%Y"),
        "month": vt.strftime("%m"),
        "day": vt.strftime("%d"),
        "time": vt.strftime("%H:00"),
        "area": [grid_lat, grid_lon, grid_lat, grid_lon],
        "data_format": "grib",
        "download_format": "unarchived",
    }


def _cds_surface_request(lat, lon, valid_time):
    """Build the colocated single-level request required by the ground merge."""
    vt = _as_datetime(valid_time)
    grid_lat, grid_lon = _nearest_era5_grid_point(lat, lon)
    return {
        "product_type": "reanalysis",
        "variable": list(ERA5_CDS_SURFACE_VARIABLES),
        "year": vt.strftime("%Y"),
        "month": vt.strftime("%m"),
        "day": vt.strftime("%d"),
        "time": vt.strftime("%H:00"),
        "area": [grid_lat, grid_lon, grid_lat, grid_lon],
        "data_format": "grib",
        "download_format": "unarchived",
    }


def _is_cds_credential_error(exc):
    message = str(exc).lower()
    return any(token in message for token in (
        ".cdsapirc",
        "api key",
        "credential",
        "missing/incomplete configuration",
        "401",
        "unauthorized",
    ))


def _new_cds_client(cdsapi):
    """Create a CDS client that is safe in a windowed/frozen process.

    PyInstaller's windowed mode intentionally leaves ``sys.stderr`` unset.
    The CDS client's default tqdm progress renderer writes to that stream and
    otherwise crashes a successful download with ``NoneType.write``. Keep the
    normal CDS console output for command-line users, while disabling both the
    logger and progress renderer when no writable stderr exists.
    """
    stderr = getattr(sys, "stderr", None)
    stderr_unavailable = (
        stderr is None
        or bool(getattr(stderr, "closed", False))
        or not callable(getattr(stderr, "write", None))
    )
    if stderr_unavailable:
        return cdsapi.Client(quiet=True, progress=False)
    return cdsapi.Client()






# ---------------------------------------------------------------------------
# Atomic output writing
# ---------------------------------------------------------------------------

def _atomic_write_npz(out_path, arrays):
    """Write a ``.npz`` file atomically (temp file + ``os.replace``)."""
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".npz", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, **arrays)
        os.replace(tmp, out_path)
    except BaseException:
        _quiet_remove(tmp)
        raise


def _atomic_write_json(path, payload):
    """Write a JSON sidecar atomically (temp file + ``os.replace``)."""
    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        _quiet_remove(tmp)
        raise


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(lat, lon, valid_time, out_path, dataset=None, loc="ERA5pt",
            progress_callback=None, cancelled=None):
    """Extract an ERA5 point sounding and write it as a ``.npz`` sidecar.

    Parameters
    ----------
    lat, lon : float
        Requested source latitude (degrees, [-90, 90]) and longitude (degrees).
    valid_time : datetime or str
        Requested valid time (UTC). Strings are parsed via ``fromisoformat``.
    out_path : str
        Destination ``.npz`` path. A ``.json`` metadata sidecar is written
        alongside it (same stem).
    dataset : optional
        A pre-loaded xarray ``Dataset`` (used mainly for testing). When omitted,
        the ERA5 column is retrieved via Herbie (optional ``[era5]`` extra).
    loc : str
        Location label recorded in the output.

    Returns
    -------
    str
        ``out_path`` on success.

    Raises
    ------
    ParameterRangeError
        If lat/lon/time are outside ERA5 coverage. No file is written.
    RetrievalError
        If the ERA5 source cannot be retrieved. No partial file is written.
    """
    lat = float(lat)
    lon = float(lon)

    # 1. Static range validation -- must happen before any retrieval or I/O so
    #    an out-of-range request writes nothing (Requirement 8.5).
    _emit_progress(progress_callback, "validating")
    _check_cancelled(cancelled)
    valid_dt = _validate_request(lat, lon, valid_time)

    # 2. Acquire the dataset (retrieval failures write nothing -- Req 8.6).
    if dataset is None:
        if progress_callback is None and cancelled is None:
            # Preserve the simple three-argument seam used by integrations
            # that replace the live retriever in tests or private adapters.
            ds = _retrieve_dataset(lat, lon, valid_dt)
        else:
            ds = _retrieve_dataset(
                lat, lon, valid_dt,
                progress_callback=progress_callback,
                cancelled=cancelled,
            )
    else:
        ds = dataset
    _check_cancelled(cancelled)

    # 3. Refine longitude coverage against the real dataset grid.
    _emit_progress(progress_callback, "extracting")
    _refine_coverage(ds, lon)

    # 4. Nearest analysis time, then nearest grid point (great-circle).
    ds_t, selected_time = _select_time(ds, valid_dt)

    _, lats = _coord_values(ds_t, _LAT_COORDS)
    _, lons = _coord_values(ds_t, _LON_COORDS)
    if lats is None or lons is None:
        raise RetrievalError(
            "ERA5 dataset is missing latitude/longitude coordinates")
    index_tuple, glat, glon = select_nearest_grid_point(lats, lons, lat, lon)
    glon = ((glon + 180.0) % 360.0) - 180.0  # normalize to [-180, 180)

    # 5. Extract and convert the vertical column; mark per-level missing fields.
    cols, n_levels = _build_columns(ds_t, index_tuple, latitude=glat)
    surface_merged = bool(cols.pop("_surface_merged", False))
    surface_pressure_hpa = cols.pop("_surface_pressure_hpa", None)
    below_ground_levels_removed = int(cols.pop(
        "_below_ground_levels_removed", 0
    ))
    if not surface_merged:
        raise RetrievalError(
            "ERA5 point sounding has no verified surface merge; refusing "
            "pressure-level values that may be extrapolated below terrain"
        )
    qc = _require_profile_qc(cols, "ERA5")
    _check_cancelled(cancelled)

    # 6. Assemble output arrays + metadata and write atomically.
    run_str = _as_datetime(selected_time).strftime("%Y-%m-%d %H:%M")
    valid_str = run_str  # ERA5 analyses are 0-hour; run == valid.

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"], "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": glat, "lon": glon, "loc": loc, "model": "ERA5",
        "run": run_str, "valid": valid_str, "fxx": 0, "observed": False,
    }
    if "surface_relative_vorticity" in cols:
        arrays["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    requested_valid_str = valid_dt.strftime("%Y-%m-%d %H:%M")
    selected_valid_str = _as_datetime(selected_time).strftime(
        "%Y-%m-%d %H:%M")

    meta = {
        "model": "ERA5",
        "loc": loc,
        "requested_lat": lat,
        "requested_lon": lon,
        "requested_valid": requested_valid_str,
        "selected_lat": glat,
        "selected_lon": glon,
        "selected_valid": selected_valid_str,
        "run": run_str,
        "valid": selected_valid_str,
        "fxx": 0,
        "observed": False,
        "npz": os.path.abspath(out_path),
        "levels": int(n_levels),
        "backend": "xarray/cfgrib",
        "decoder": "ERA5 pressure-level column",
        "surface_merged": True,
        "surface_contract_version": SURFACE_CONTRACT_VERSION,
        "surface_pressure_hpa": float(surface_pressure_hpa),
        "below_ground_levels_removed": below_ground_levels_removed,
        "qc_valid": qc.valid,
        "qc_valid_level_count": qc.valid_level_count,
        "qc_issues": list(qc.issues),
        "cache_hit": False,
    }
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]
        meta["surface_vorticity_source"] = cols.get(
            "_surface_vorticity_source", "unknown"
        )

    # Write the .npz first, then the sidecar; both are atomic renames so a
    # failure never leaves a partial primary output (Requirement 8.6).
    _emit_progress(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        # Roll back the primary output so no orphaned/partial pair remains.
        _quiet_remove(out_path)
        raise

    try:
        _check_cancelled(cancelled)
    except ExtractionCancelled:
        _quiet_remove(out_path)
        _quiet_remove(json_path)
        raise
    _emit_progress(progress_callback, "complete")
    return out_path


def _parse_cli_time(value):
    """Parse a CLI time argument (ISO-8601 or ``YYYY-MM-DD HH:MM``)."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")


def main(argv=None):  # pragma: no cover - thin CLI wrapper
    """CLI: ``era5_extract "YYYY-MM-DD HH:MM" LAT LON [out.npz] [--render [PNG]]``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="era5_extract",
        description="Extract an ERA5 reanalysis point sounding to a .npz")
    parser.add_argument("time", help="valid time (ISO or 'YYYY-MM-DD HH:MM')")
    parser.add_argument("lat", type=float)
    parser.add_argument("lon", type=float)
    parser.add_argument("out", nargs="?", default=None, help="output .npz path")
    parser.add_argument("--loc", default="ERA5pt", help="location label")
    parser.add_argument("--render", nargs="?", const="", default=None,
                        metavar="PNG",
                        help="also render the sounding to a PNG (optional path)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    valid_time = _parse_cli_time(args.time)
    try:
        default_name = "era5_point_%.2fN_%.2fE_%s.npz" % (
            args.lat, args.lon, valid_time.strftime("%Y%m%d%H")
        )
        out = args.out or str(export_file_path(default_name))
        path = extract(args.lat, args.lon, valid_time, out, loc=args.loc)
    except (ERA5ExtractionError, OSError) as exc:
        print("ERROR: %s" % exc)
        return 1
    print("wrote %s" % path)

    if args.render is not None:
        from sharpmod.tools import render_npz
        png = render_npz(path, args.render or None)
        print("rendered %s" % png)
    return 0


from sharpmod.tools.era5_columns import (  # noqa: E402
    _surface_relative_vorticity_from_wind_grid,
    _build_columns,
    _retrieve_dataset,
    _merge_datasets
)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
