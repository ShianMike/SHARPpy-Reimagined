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
import os
import tempfile
from datetime import datetime, timezone

import numpy as np

__all__ = [
    "extract",
    "ERA5ExtractionError",
    "ParameterRangeError",
    "RetrievalError",
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
ERA5_CDS_VARIABLES = (
    "geopotential",
    "relative_humidity",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
    "vertical_velocity",
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

    if lats.ndim == 1 and lons.ndim == 1:
        lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
        dist = great_circle_distance_km(lat0, lon0, lat_grid, lon_grid)
        ilat, ilon = np.unravel_index(np.argmin(dist), dist.shape)
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
    spd = np.sqrt(np.asarray(u) ** 2 + np.asarray(v) ** 2) * 1.94384449
    wdir = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
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
        arr = np.asarray(ds[name].values, dtype=float)
        arr = np.squeeze(arr)
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
                                            absolute=False):
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
    ordered = _mark_missing(arr, arr.size)[np.argsort(-np.asarray(levels))]
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


def _surface_relative_vorticity_from_wind_grid(ds, index_tuple, levels):
    """Estimate surface relative vorticity from the gridded u/v wind fields."""
    if len(index_tuple) != 2:
        return None

    u_name = _first_present(ds, _VAR_U)
    v_name = _first_present(ds, _VAR_V)
    if u_name is None or v_name is None:
        return None

    try:
        u = np.squeeze(np.asarray(ds[u_name].values, dtype=float))
        v = np.squeeze(np.asarray(ds[v_name].values, dtype=float))
    except Exception:
        return None
    if u.ndim != 3 or v.ndim != 3 or u.shape != v.shape:
        return None

    _, lats = _coord_values(ds, _LAT_COORDS)
    _, lons = _coord_values(ds, _LON_COORDS)
    if lats is None or lons is None:
        return None

    iy, ix = (int(index_tuple[0]), int(index_tuple[1]))
    if iy < 0 or ix < 0 or iy >= u.shape[1] or ix >= u.shape[2]:
        return None

    try:
        surface_level = int(np.nanargmax(np.asarray(levels, dtype=float)))
    except Exception:
        return None
    u2d = u[surface_level]
    v2d = v[surface_level]

    x_pair = _neighbor_pair(ix, u2d.shape[1])
    y_pair = _neighbor_pair(iy, u2d.shape[0])
    if x_pair is None or y_pair is None:
        return None
    x0, x1 = x_pair
    y0, y1 = y_pair

    if np.asarray(lats).ndim == 1 and np.asarray(lons).ndim == 1:
        lat_center = float(lats[iy])
        dx = _east_west_distance_m(lat_center, lons[x0], lons[x1])
        dy = _north_south_distance_m(lats[y0], lats[y1])
    else:
        lat_grid = np.asarray(lats, dtype=float)
        lon_grid = np.asarray(lons, dtype=float)
        if lat_grid.shape != u2d.shape or lon_grid.shape != u2d.shape:
            return None
        lat_center = float(lat_grid[iy, ix])
        dx = _east_west_distance_m(
            lat_center, lon_grid[iy, x0], lon_grid[iy, x1])
        dy = _north_south_distance_m(lat_grid[y0, ix], lat_grid[y1, ix])

    if not (np.isfinite(dx) and np.isfinite(dy)) or abs(dx) < 1.0 or abs(dy) < 1.0:
        return None

    dvdx = (float(v2d[iy, x1]) - float(v2d[iy, x0])) / dx
    dudy = (float(u2d[y1, ix]) - float(u2d[y0, ix])) / dy
    value = dvdx - dudy
    return float(value) if np.isfinite(value) else None


def _build_columns(ds, index_tuple, latitude=None):
    """Extract and convert every per-level field from the selected column.

    Returns a dict of NumPy arrays (bottom->top ordered) plus the level count.
    """
    lname, levels = _coord_values(ds, _LEVEL_COORDS)
    if levels is None or levels.size == 0:
        raise RetrievalError("ERA5 dataset has no pressure-level coordinate")
    levels = np.asarray(levels, dtype=float)
    # Pa -> hPa if the coordinate is stored in pascals.
    if np.nanmax(levels) > 2000.0:
        levels = levels / 100.0

    index_is_regular = len(index_tuple) == 2
    iy, ix = index_tuple if index_is_regular else index_tuple
    get = _column(ds, iy, ix, index_is_regular)

    t_raw = get(_VAR_TEMP)
    tmpc = None if t_raw is None else t_raw - 273.15

    # Height: prefer geopotential height, else geopotential / g0.
    gh_raw = get(_VAR_GEOPOTENTIAL_HEIGHT)
    if gh_raw is not None:
        hght = gh_raw
    else:
        z_raw = get(_VAR_GEOPOTENTIAL)
        hght = None if z_raw is None else z_raw / G0

    # Dewpoint: prefer RH, fall back to specific humidity.
    dwpc = None
    rh_raw = get(_VAR_RH)
    if rh_raw is not None and tmpc is not None:
        dwpc = dewpoint_from_rh(tmpc, rh_raw)
    else:
        q_raw = get(_VAR_Q)
        if q_raw is not None:
            dwpc = dewpoint_from_specific_humidity(q_raw, levels)

    u_raw = get(_VAR_U)
    v_raw = get(_VAR_V)
    if u_raw is not None and v_raw is not None:
        wdir, wspd = uv_to_dir_spd(u_raw, v_raw)
    else:
        wdir, wspd = None, None

    w_raw = get(_VAR_W)
    # ERA5 vertical velocity and SHARPpy's Profile.omeg are BOTH in Pa/s (the
    # skew-T OMEGA meter / omega read-out do the Pa/s conversions internally).
    # Pass it through unchanged -- an extra *10 (to microbar/s) makes the meter
    # bars overshoot the +/-10 scale by 10x and the read-out read 10x too high.
    omeg = None if w_raw is None else w_raw

    vort_raw = get(_VAR_RELATIVE_VORTICITY)
    surface_relative_vorticity = _surface_relative_vorticity_from_column(
        vort_raw, levels, latitude=latitude)
    if surface_relative_vorticity is None:
        absv_raw = get(_VAR_ABSOLUTE_VORTICITY)
        surface_relative_vorticity = _surface_relative_vorticity_from_column(
            absv_raw, levels, latitude=latitude, absolute=True)
    if surface_relative_vorticity is None:
        surface_relative_vorticity = _surface_relative_vorticity_from_wind_grid(
            ds, index_tuple, levels)

    n = levels.size
    cols = {
        "pres": _mark_missing(levels, n),
        "hght": _mark_missing(hght, n),
        "tmpc": _mark_missing(tmpc, n),
        "dwpc": _mark_missing(dwpc, n),
        "wdir": _mark_missing(wdir, n),
        "wspd": _mark_missing(wspd, n),
        "omeg": _mark_missing(omeg, n),
        "u": _mark_missing(u_raw, n),
        "v": _mark_missing(v_raw, n),
    }

    # Order bottom (highest pressure) -> top (lowest pressure).
    order = np.argsort(-cols["pres"])
    for key in cols:
        cols[key] = cols[key][order]
    if surface_relative_vorticity is not None:
        cols["surface_relative_vorticity"] = surface_relative_vorticity
    return cols, n


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


def _retrieve_dataset(lat, lon, valid_time):
    """Fetch one ERA5 pressure-level column through the official CDS API.

    The requested GRIB contains only the nearest 0.25-degree point, the six
    sounding variables, and all 37 published pressure levels. It is fully
    loaded before the temporary download is removed.
    """
    try:
        import cdsapi
        import cfgrib
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise RetrievalError(
            "ERA5 support requires the optional [era5] extra "
            "(cdsapi, cfgrib, xarray): %s" % exc) from exc

    vt = _as_datetime(valid_time)
    request = _cds_pressure_level_request(lat, lon, vt)
    fd, grib_path = tempfile.mkstemp(prefix="sharpmod-era5-", suffix=".grib")
    os.close(fd)
    source_datasets = []
    try:  # pragma: no cover - live CDS/network path
        client = cdsapi.Client()
        client.retrieve(ERA5_CDS_DATASET, request, grib_path)
        source_datasets = list(cfgrib.open_datasets(
            grib_path, backend_kwargs={"indexpath": ""}))
        ds = _merge_datasets(source_datasets)
        ds.load()
        return ds
    except RetrievalError:
        raise
    except Exception as exc:  # pragma: no cover - network/auth failure path
        if _is_cds_credential_error(exc):
            raise RetrievalError(
                "ERA5 retrieval requires CDS API credentials. Create a free "
                "Climate Data Store account, then copy its API profile into "
                "$HOME/.cdsapirc; original error: %s" % exc) from exc
        raise RetrievalError(
            "failed to retrieve ERA5 data from the Copernicus CDS for %s at "
            "(%.4f, %.4f): %s" % (vt.isoformat(), lat, lon, exc)) from exc
    finally:
        for source in source_datasets:
            try:
                source.close()
            except Exception:
                pass
        _quiet_remove(grib_path)
        _quiet_remove(grib_path + ".idx")


def _merge_datasets(ds_list):  # pragma: no cover - optional dependency path
    """Merge cfgrib's split datasets into one, importing xarray lazily."""
    import xarray as xr
    ds_list = [d for d in ds_list
               if any(c in d.coords for c in _LEVEL_COORDS)]
    if not ds_list:
        raise RetrievalError("no pressure-level ERA5 dataset was returned")
    return xr.merge(ds_list, compat="override", join="outer")


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

def extract(lat, lon, valid_time, out_path, dataset=None, loc="ERA5pt"):
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
    valid_dt = _validate_request(lat, lon, valid_time)

    # 2. Acquire the dataset (retrieval failures write nothing -- Req 8.6).
    if dataset is None:
        ds = _retrieve_dataset(lat, lon, valid_dt)
    else:
        ds = dataset

    # 3. Refine longitude coverage against the real dataset grid.
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

    # 6. Assemble output arrays + metadata and write atomically.
    run_str = _as_datetime(selected_time).strftime("%Y-%m-%d %H:%M")
    valid_str = run_str  # ERA5 analyses are 0-hour; run == valid.

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"], "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": glat, "lon": glon, "loc": loc, "model": "ERA5",
        "run": run_str, "valid": valid_str, "fxx": 0,
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
        "npz": os.path.abspath(out_path),
        "levels": int(n_levels),
    }
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    # Write the .npz first, then the sidecar; both are atomic renames so a
    # failure never leaves a partial primary output (Requirement 8.6).
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
        # Roll back the primary output so no orphaned/partial pair remains.
        _quiet_remove(out_path)
        raise

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
    out = args.out or "era5_point_%.2fN_%.2fE_%s.npz" % (
        args.lat, args.lon, valid_time.strftime("%Y%m%d%H"))
    try:
        path = extract(args.lat, args.lon, valid_time, out, loc=args.loc)
    except ERA5ExtractionError as exc:
        print("ERROR: %s" % exc)
        return 1
    print("wrote %s" % path)

    if args.render is not None:
        from sharpmod.tools import render_npz
        png = render_npz(path, args.render or None)
        print("rendered %s" % png)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
