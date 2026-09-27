"""ERA5 dataset retrieval and atmospheric-column assembly for point soundings.

It merges the required pressure-level and surface data and derives profile columns;
``era5_extract`` coordinates command execution and the common output contract."""

from __future__ import annotations

from sharpmod import backends as _backends
from sharpmod.models.model_surface import merge_surface_level
from sharpmod.upstream.upstream_warnings import xarray_new_combine_defaults
import numpy as np
import os
import tempfile
from sharpmod.tools import era5_extract as _api


def _surface_relative_vorticity_from_wind_grid(
        ds, index_tuple, levels, surface_pressure=None):
    """Estimate surface relative vorticity from the gridded u/v wind fields."""
    if len(index_tuple) != 2:
        return None

    u_name = _api._first_present(ds, _api._VAR_U)
    v_name = _api._first_present(ds, _api._VAR_V)
    if u_name is None or v_name is None:
        return None

    _, lats = _api._coord_values(ds, _api._LAT_COORDS)
    _, lons = _api._coord_values(ds, _api._LON_COORDS)
    if lats is None or lons is None:
        return None

    _point_indexers, (ydim, xdim) = _api._horizontal_indexers(ds, index_tuple)
    if ydim is None or xdim is None:
        return None

    iy, ix = (int(index_tuple[0]), int(index_tuple[1]))
    ysize = int(ds.sizes.get(ydim, 0))
    xsize = int(ds.sizes.get(xdim, 0))
    if iy < 0 or ix < 0 or iy >= ysize or ix >= xsize:
        return None

    try:
        levels = np.asarray(levels, dtype=float)
        eligible = np.isfinite(levels)
        if surface_pressure is not None:
            eligible &= levels <= float(surface_pressure)
        if not np.any(eligible):
            return None
        surface_level = int(np.nanargmax(np.where(eligible, levels, np.nan)))
    except Exception:
        return None
    x_pair = _api._neighbor_pair(ix, xsize)
    y_pair = _api._neighbor_pair(iy, ysize)
    if x_pair is None or y_pair is None:
        return None
    x0, x1 = x_pair
    y0, y1 = y_pair

    level_name = _api._first_present(ds.coords, _api._LEVEL_COORDS)
    if level_name is None:
        return None
    level_dims = ds[level_name].dims
    if not level_dims:
        return None
    level_dim = level_dims[0]
    selection = {
        level_dim: surface_level,
        ydim: slice(y0, y1 + 1),
        xdim: slice(x0, x1 + 1),
    }
    try:
        # Decode one surface message per wind component and retain only the
        # small neighbor window needed by the finite difference.  The previous
        # path loaded/cast both complete 3-D wind cubes a second time.
        u_window = ds[u_name].isel(selection).transpose(ydim, xdim)
        v_window = ds[v_name].isel(selection).transpose(ydim, xdim)
        u2d = np.asarray(u_window.values, dtype=float)
        v2d = np.asarray(v_window.values, dtype=float)
    except Exception:
        return None
    if u2d.ndim != 2 or v2d.shape != u2d.shape:
        return None

    local_y = iy - y0
    local_x = ix - x0
    local_x0, local_x1 = x0 - x0, x1 - x0
    local_y0, local_y1 = y0 - y0, y1 - y0

    if np.asarray(lats).ndim == 1 and np.asarray(lons).ndim == 1:
        lat_center = float(lats[iy])
        dx = _api._east_west_distance_m(lat_center, lons[x0], lons[x1])
        dy = _api._north_south_distance_m(lats[y0], lats[y1])
    else:
        lat_grid = np.asarray(lats)
        lon_grid = np.asarray(lons)
        lat_window = np.asarray(
            lat_grid[y0:y1 + 1, x0:x1 + 1], dtype=float)
        lon_window = np.asarray(
            lon_grid[y0:y1 + 1, x0:x1 + 1], dtype=float)
        if lat_window.shape != u2d.shape or lon_window.shape != u2d.shape:
            return None
        lat_center = float(lat_window[local_y, local_x])
        dx = _api._east_west_distance_m(
            lat_center,
            lon_window[local_y, local_x0],
            lon_window[local_y, local_x1],
        )
        dy = _api._north_south_distance_m(
            lat_window[local_y0, local_x],
            lat_window[local_y1, local_x],
        )

    if not (np.isfinite(dx) and np.isfinite(dy)) or abs(dx) < 1.0 or abs(dy) < 1.0:
        return None

    dvdx = (
        float(v2d[local_y, local_x1])
        - float(v2d[local_y, local_x0])
    ) / dx
    dudy = (
        float(u2d[local_y1, local_x])
        - float(u2d[local_y0, local_x])
    ) / dy
    value = dvdx - dudy
    return float(value) if np.isfinite(value) else None


def _build_columns(ds, index_tuple, latitude=None):
    """Extract and convert every per-level field from the selected column.

    Returns a dict of NumPy arrays (bottom->top ordered) plus the level count.
    """
    lname, levels = _api._coord_values(ds, _api._LEVEL_COORDS)
    if levels is None or levels.size == 0:
        raise _api.RetrievalError("ERA5 dataset has no pressure-level coordinate")
    levels = np.asarray(levels, dtype=float)
    # Pa -> hPa if the coordinate is stored in pascals.
    if np.nanmax(levels) > 2000.0:
        levels = levels / 100.0

    index_is_regular = len(index_tuple) == 2
    iy, ix = index_tuple if index_is_regular else index_tuple
    get = _api._column(ds, iy, ix, index_is_regular)

    surface_pressure = _api._first_surface_value(get(_api._VAR_SURFACE_PRESSURE))
    if surface_pressure is not None and surface_pressure > 2000.0:
        surface_pressure /= 100.0
    surface_height_name = _api._first_present(ds, _api._VAR_SURFACE_HEIGHT)
    surface_height = _api._first_surface_value(get(_api._VAR_SURFACE_HEIGHT))
    if surface_height_name == "surface_geopotential" \
            and surface_height is not None:
        surface_height /= _api.G0
    surface_temperature = _api._first_surface_value(get(_api._VAR_2M_TEMP))
    surface_dewpoint = _api._first_surface_value(get(_api._VAR_2M_DEWPOINT))
    if surface_temperature is not None and surface_temperature > 150.0:
        surface_temperature -= 273.15
    if surface_dewpoint is not None and surface_dewpoint > 150.0:
        surface_dewpoint -= 273.15
    if surface_temperature is not None:
        surface_rh = _api._first_surface_value(get(_api._VAR_2M_RH))
        surface_q = _api._first_surface_value(get(_api._VAR_2M_Q))
        # Specific humidity is tied directly to surface pressure and is the
        # safest moisture source when a provider mixes surface grids (CFS).
        if surface_q is not None and surface_pressure is not None:
            surface_dewpoint = float(_api.dewpoint_from_specific_humidity(
                np.asarray([surface_q]),
                np.asarray([surface_pressure]),
            )[0])
        elif surface_dewpoint is not None:
            pass
        elif surface_rh is not None:
            surface_dewpoint = float(_api.dewpoint_from_rh(
                np.asarray([surface_temperature]),
                np.asarray([surface_rh]),
            )[0])
    surface = {
        "pres": surface_pressure,
        "hght": surface_height,
        "tmpc": surface_temperature,
        "dwpc": surface_dewpoint,
        "u": _api._first_surface_value(get(_api._VAR_10M_U)),
        "v": _api._first_surface_value(get(_api._VAR_10M_V)),
    }

    t_raw = get(_api._VAR_TEMP)
    tmpc = None if t_raw is None else t_raw - 273.15

    # Height: prefer geopotential height, else geopotential / g0.
    gh_raw = get(_api._VAR_GEOPOTENTIAL_HEIGHT)
    if gh_raw is not None:
        hght = gh_raw
    else:
        z_raw = get(_api._VAR_GEOPOTENTIAL)
        hght = None if z_raw is None else z_raw / _api.G0

    # Dewpoint: prefer RH, fall back to specific humidity.
    dwpc = None
    rh_raw = get(_api._VAR_RH)
    if rh_raw is not None and tmpc is not None:
        dwpc = _api.dewpoint_from_rh(tmpc, rh_raw)
    else:
        q_raw = get(_api._VAR_Q)
        if q_raw is not None:
            dwpc = _api.dewpoint_from_specific_humidity(q_raw, levels)

    u_raw = get(_api._VAR_U)
    v_raw = get(_api._VAR_V)
    if u_raw is not None and v_raw is not None:
        wdir, wspd = _api.uv_to_dir_spd(u_raw, v_raw)
    else:
        wdir, wspd = None, None

    w_raw = get(_api._VAR_W)
    # ERA5 vertical velocity and SHARPpy's Profile.omeg are BOTH in Pa/s (the
    # skew-T OMEGA meter / omega read-out do the Pa/s conversions internally).
    # Pass it through unchanged -- an extra *10 (to microbar/s) makes the meter
    # bars overshoot the +/-10 scale by 10x and the read-out read 10x too high.
    omeg = None if w_raw is None else w_raw

    vort_raw = get(_api._VAR_RELATIVE_VORTICITY)
    surface_relative_vorticity = _api._surface_relative_vorticity_from_column(
        vort_raw,
        levels,
        latitude=latitude,
        surface_pressure=surface_pressure,
    )
    surface_vorticity_source = (
        "relative-vorticity pressure-level field"
        if surface_relative_vorticity is not None else None
    )
    if surface_relative_vorticity is None:
        absv_raw = get(_api._VAR_ABSOLUTE_VORTICITY)
        surface_relative_vorticity = _api._surface_relative_vorticity_from_column(
            absv_raw,
            levels,
            latitude=latitude,
            absolute=True,
            surface_pressure=surface_pressure,
        )
        if surface_relative_vorticity is not None:
            surface_vorticity_source = (
                "absolute-vorticity pressure-level field minus Coriolis"
            )
    if surface_relative_vorticity is None:
        surface_relative_vorticity = _api._surface_relative_vorticity_from_wind_grid(
            ds,
            index_tuple,
            levels,
            surface_pressure=surface_pressure,
        )
        if surface_relative_vorticity is not None:
            surface_vorticity_source = "horizontal wind-gradient fallback"

    n = levels.size
    cols = {
        "pres": _api._mark_missing(levels, n),
        "hght": _api._mark_missing(hght, n),
        "tmpc": _api._mark_missing(tmpc, n),
        "dwpc": _api._mark_missing(dwpc, n),
        "wdir": _api._mark_missing(wdir, n),
        "wspd": _api._mark_missing(wspd, n),
        "omeg": _api._mark_missing(omeg, n),
        "u": _api._mark_missing(u_raw, n),
        "v": _api._mark_missing(v_raw, n),
    }

    # Order bottom (highest pressure) -> top (lowest pressure), remove invalid
    # pressure rows, and retain the earliest source row for exact duplicates.
    # Compute this selection once and apply it to every aligned field.
    order = _backends.pressure_sort_dedup_indices(
        cols["pres"], missing=_api.MISSING)
    if np.asarray(order).size == 0:
        raise _api.RetrievalError("dataset has no usable pressure levels")
    for key in cols:
        cols[key] = cols[key][order]
    surface_merge = merge_surface_level(cols, surface, missing=_api.MISSING)
    if surface_merge is not None:
        cols = surface_merge.columns
        cols["_surface_merged"] = True
        cols["_surface_pressure_hpa"] = surface_merge.surface_pressure
        cols["_below_ground_levels_removed"] = surface_merge.removed_levels
    if surface_relative_vorticity is not None:
        cols["surface_relative_vorticity"] = surface_relative_vorticity
        cols["_surface_vorticity_source"] = surface_vorticity_source
    return cols, int(np.asarray(cols["pres"]).size)


def _retrieve_dataset(lat, lon, valid_time, progress_callback=None,
                      cancelled=None):
    """Fetch colocated ERA5 pressure-level and verified surface columns."""
    try:
        import cdsapi
        import cfgrib
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise _api.RetrievalError(
            "ERA5 support requires the optional [era5] extra "
            "(cdsapi, cfgrib, xarray): %s" % exc) from exc

    vt = _api._as_datetime(valid_time)
    pressure_request = _api._cds_pressure_level_request(lat, lon, vt)
    surface_request = _api._cds_surface_request(lat, lon, vt)
    _api._check_cancelled(cancelled)
    temporary_paths = []
    for prefix in ("sharpmod-era5-pressure-", "sharpmod-era5-surface-"):
        fd, path = tempfile.mkstemp(prefix=prefix, suffix=".grib")
        os.close(fd)
        temporary_paths.append(path)
    pressure_path, surface_path = temporary_paths
    source_datasets = []
    try:  # pragma: no cover - live CDS/network path
        _api._emit_progress(progress_callback, "queued")
        client = _api._new_cds_client(cdsapi)
        _api._emit_progress(progress_callback, "retrieving")
        # cdsapi's stable client is synchronous.  A cancellation requested
        # while this call is in flight is observed immediately after it
        # returns; the remote CDS job itself may continue server-side.
        client.retrieve(_api.ERA5_CDS_DATASET, pressure_request, pressure_path)
        _api._check_cancelled(cancelled)
        client.retrieve(
            _api.ERA5_CDS_SURFACE_DATASET,
            surface_request,
            surface_path,
        )
        _api._check_cancelled(cancelled)
        _api._emit_progress(progress_callback, "decoding")
        source_datasets = []
        for path in temporary_paths:
            with xarray_new_combine_defaults():
                source_datasets.extend(cfgrib.open_datasets(
                    path, backend_kwargs={"indexpath": ""}
                ))
        ds = _api._merge_datasets(source_datasets)
        ds.load()
        _api._check_cancelled(cancelled)
        return ds
    except (_api.RetrievalError, _api.ExtractionCancelled):
        raise
    except Exception as exc:  # pragma: no cover - network/auth failure path
        if _api._is_cds_credential_error(exc):
            raise _api.RetrievalError(
                "ERA5 retrieval requires CDS API credentials. Create a free "
                "Climate Data Store account, then copy its API profile into "
                "$HOME/.cdsapirc; original error: %s" % exc) from exc
        if _api._cds_terms_error(exc):
            raise _api.RetrievalError(
                "ERA5 pressure/single-level dataset terms have not been "
                "accepted. "
                "Sign in to the Copernicus Climate Data Store, open both the "
                "ERA5 hourly pressure-level and single-level datasets, and "
                "accept their terms before retrying; original error: %s"
                % exc) from exc
        raise _api.RetrievalError(
            "failed to retrieve ERA5 data from the Copernicus CDS for %s at "
            "(%.4f, %.4f): %s" % (vt.isoformat(), lat, lon, exc)) from exc
    finally:
        for source in source_datasets:
            try:
                source.close()
            except Exception:
                pass
        for path in temporary_paths:
            _api._quiet_remove(path)
            _api._quiet_remove(path + ".idx")


def _merge_datasets(ds_list):  # pragma: no cover - optional dependency path
    """Merge cfgrib's split datasets into one, importing xarray lazily."""
    import xarray as xr
    candidates = tuple(ds_list)
    pressure_sources = [
        _api._promote_scalar_level_coordinate(dataset)
        for dataset in candidates
        if any(coord in dataset.coords for coord in _api._LEVEL_COORDS)
    ]
    if not pressure_sources:
        for candidate in candidates:
            try:
                candidate.close()
            except Exception:
                pass
        raise _api.RetrievalError("no pressure-level ERA5 dataset was returned")
    surface_names = {
        *_api._VAR_SURFACE_PRESSURE,
        *_api._VAR_SURFACE_HEIGHT,
        *_api._VAR_2M_TEMP,
        *_api._VAR_2M_DEWPOINT,
        *_api._VAR_10M_U,
        *_api._VAR_10M_V,
    }
    surface_sources = []
    for dataset in candidates:
        if any(coord in dataset.coords for coord in _api._LEVEL_COORDS):
            continue
        if "z" in dataset.data_vars:
            dataset = dataset.rename({"z": "surface_geopotential"})
        if not surface_names.isdisjoint(dataset.data_vars) \
                or "surface_geopotential" in dataset.data_vars:
            surface_sources.append(dataset)
    sources = tuple(pressure_sources + surface_sources)
    try:
        merged = xr.merge(sources, compat="override", join="outer")
    except BaseException:
        for candidate in candidates:
            try:
                candidate.close()
            except Exception:
                pass
        raise

    # xr.merge does not retain the close callbacks of its inputs.  Without an
    # explicit composite callback, cached cfgrib file handles survive until GC
    # and can keep cache directories locked on Windows.
    def _close_sources():
        for source in candidates:
            try:
                source.close()
            except Exception:
                pass

    merged.set_close(_close_sources)
    return merged
