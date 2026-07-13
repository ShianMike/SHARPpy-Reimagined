"""Extract an HRRR point sounding and write it as a .npz file.

Fetches HRRR model data from the 2026-04-27 00Z run, forecast hour 24
(valid 2026-04-28 00Z) at lat=37.671072, lon=-90.201396 and writes a
portable .npz sounding compatible with sharpmod-render and sharpmod-gui.

Usage:
    # From the .gribenv virtual environment (has herbie + cfgrib + xarray):
    .gribenv\\Scripts\\python.exe hrrr_extract.py

    # Or activate the venv first:
    .gribenv\\Scripts\\activate
    python hrrr_extract.py
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import numpy as np

from sharpmod.tools.era5_extract import (
    _surface_relative_vorticity_from_column,
    _surface_relative_vorticity_from_wind_grid,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LAT = 37.671072
LON = -90.201396
RUN_DATE = "2026-06-11"
RUN_HOUR = 0       # 00Z model run
FXX = 24           # forecast hour -> valid 2026-06-12 00Z

OUT_NPZ = "hrrr_point_37.67N_90.20W_20260612_f024.npz"

# Missing sentinel (same as the project's .npz loader expects)
MISSING = -9999.0
G0 = 9.80665


# ---------------------------------------------------------------------------
# Helpers (adapted from sharpmod/tools/era5_extract.py)
# ---------------------------------------------------------------------------

def great_circle_distance_km(lat1, lon1, lat2, lon2):
    """Haversine great-circle distance in km."""
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
    """Return (index_tuple, selected_lat, selected_lon) for nearest point."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)

    if lats.ndim == 1 and lons.ndim == 1:
        lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
        dist = great_circle_distance_km(lat0, lon0, lat_grid, lon_grid)
        ilat, ilon = np.unravel_index(np.argmin(dist), dist.shape)
        return (int(ilat), int(ilon)), float(lats[ilat]), float(lons[ilon])

    # 2-D coordinate arrays (HRRR uses these)
    dist = great_circle_distance_km(lat0, lon0, lats, lons)
    idx = np.unravel_index(np.argmin(dist), dist.shape)
    return idx, float(lats[idx]), float(lons[idx])


def uv_to_dir_spd(u, v):
    """Zonal/meridional wind (m/s) -> (met direction degrees, speed knots)."""
    spd = np.sqrt(np.asarray(u) ** 2 + np.asarray(v) ** 2) * 1.94384449
    wdir = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    return wdir, spd


def dewpoint_from_rh(tmpc, rh):
    """Dewpoint (°C) from temperature (°C) and relative humidity (%)."""
    a, b = 17.625, 243.04
    rh = np.clip(np.asarray(rh, dtype=float), 1e-3, 100.0)
    tmpc = np.asarray(tmpc, dtype=float)
    gamma = np.log(rh / 100.0) + (a * tmpc) / (b + tmpc)
    return (b * gamma) / (a - gamma)


def mark_missing(arr, n_levels):
    """Replace NaN/inf with the MISSING sentinel; None -> all-missing."""
    if arr is None:
        return np.full(n_levels, MISSING, dtype=float)
    out = np.asarray(arr, dtype=float).copy()
    out[~np.isfinite(out)] = MISSING
    return out


# ---------------------------------------------------------------------------
# HRRR retrieval via Herbie
# ---------------------------------------------------------------------------

def retrieve_hrrr(run_date, run_hour, fxx):
    """Fetch HRRR pressure-level data via Herbie.

    Returns an xarray Dataset with the vertical column fields.
    """
    from herbie import Herbie
    import xarray as xr

    run_str = f"{run_date} {run_hour:02d}:00"
    print(f"Fetching HRRR run={run_str} fxx={fxx}...")

    H = Herbie(run_str, model="hrrr", product="prs", fxx=fxx)

    # Fetch all pressure-level fields in one pass using a combined regex.
    # This downloads a single subset of the GRIB file instead of 6 separate ones.
    pattern = ":(TMP|HGT|RH|UGRD|VGRD|VVEL|ABSV):\\d+ mb:"
    print(f"  Downloading with pattern: {pattern}")

    ds = H.xarray(pattern, remove_grib=False)
    if isinstance(ds, list):
        # cfgrib may split into multiple datasets; merge them
        level_coords = ("isobaricInhPa", "level", "pressure_level", "plev")
        prs_datasets = [d for d in ds if any(c in d.coords for c in level_coords)]
        if not prs_datasets:
            raise RuntimeError("No pressure-level datasets found in HRRR output")
        ds = xr.merge(prs_datasets, compat="override", join="outer")

    print("  Download complete.")
    return ds, H


# ---------------------------------------------------------------------------
# Column extraction
# ---------------------------------------------------------------------------

def extract_column(ds, lat, lon):
    """Extract the vertical column nearest to (lat, lon) from the dataset."""

    # Find coordinate names
    lat_candidates = ("latitude", "lat", "gridlat_0")
    lon_candidates = ("longitude", "lon", "gridlon_0")
    level_candidates = ("isobaricInhPa", "level", "pressure_level", "plev")

    lat_name = None
    for c in lat_candidates:
        if c in ds.coords:
            lat_name = c
            break
    lon_name = None
    for c in lon_candidates:
        if c in ds.coords:
            lon_name = c
            break
    level_name = None
    for c in level_candidates:
        if c in ds.coords:
            level_name = c
            break

    if lat_name is None or lon_name is None:
        raise RuntimeError(f"Cannot find lat/lon coords. Available: {list(ds.coords)}")
    if level_name is None:
        raise RuntimeError(f"Cannot find level coord. Available: {list(ds.coords)}")

    lats = np.asarray(ds[lat_name].values, dtype=float)
    lons = np.asarray(ds[lon_name].values, dtype=float)

    # HRRR longitudes may be 0..360; convert request if needed
    lon_req = lon
    if np.nanmin(lons) >= 0 and lon < 0:
        lon_req = lon + 360.0

    idx, glat, glon = select_nearest_grid_point(lats, lons, lat, lon_req)
    # Normalize selected lon back to -180..180
    if glon > 180:
        glon -= 360.0

    print(f"  Nearest grid point: ({glat:.4f}, {glon:.4f})")
    dist = great_circle_distance_km(lat, lon, glat, glon)
    print(f"  Distance from request: {dist:.2f} km")

    # Extract column at this grid point
    levels = np.asarray(ds[level_name].values, dtype=float)
    # If levels are in Pa, convert to hPa
    if np.nanmax(levels) > 2000:
        levels = levels / 100.0

    n_levels = levels.size

    # Variable name candidates for HRRR GRIB output
    temp_names = ("t", "TMP_P0_L100_GLC0", "t2m", "tmp")
    hgt_names = ("gh", "HGT_P0_L100_GLC0", "z", "hgt")
    rh_names = ("r", "RH_P0_L100_GLC0", "rh")
    u_names = ("u", "UGRD_P0_L100_GLC0", "ugrd", "u10")
    v_names = ("v", "VGRD_P0_L100_GLC0", "vgrd", "v10")
    w_names = ("w", "VVEL_P0_L100_GLC0", "vvel")
    rel_vort_names = ("vo", "vort", "VORT_P0_L100_GLC0", "relative_vorticity")
    absv_names = ("absv", "ABSV_P0_L100_GLC0", "absolute_vorticity")

    def get_var(candidates):
        for name in candidates:
            if name in ds:
                arr = np.asarray(ds[name].values, dtype=float)
                arr = np.squeeze(arr)
                if arr.ndim == 1:
                    return arr
                elif arr.ndim == 3:
                    # (level, y, x) for 2D grids
                    if len(idx) == 2:
                        return arr[:, idx[0], idx[1]]
                    return arr[:, idx[0]]
                elif arr.ndim == 2:
                    if len(idx) == 2:
                        return arr[idx[0], idx[1]] if arr.shape[0] != n_levels else arr[:, idx[0]]
                    return arr[:, idx[0]] if arr.shape[0] == n_levels else None
                return None
        return None

    t_raw = get_var(temp_names)
    # HRRR TMP is in Kelvin
    tmpc = None if t_raw is None else t_raw - 273.15

    hght = get_var(hgt_names)

    rh_raw = get_var(rh_names)
    dwpc = None
    if rh_raw is not None and tmpc is not None:
        dwpc = dewpoint_from_rh(tmpc, rh_raw)

    u_raw = get_var(u_names)
    v_raw = get_var(v_names)
    if u_raw is not None and v_raw is not None:
        wdir, wspd = uv_to_dir_spd(u_raw, v_raw)
    else:
        wdir, wspd = None, None

    w_raw = get_var(w_names)
    omeg = w_raw  # Pa/s, same convention as the .npz loader expects

    rel_vort_raw = get_var(rel_vort_names)
    surface_relative_vorticity = _surface_relative_vorticity_from_column(
        rel_vort_raw, levels, latitude=glat)
    if surface_relative_vorticity is None:
        absv_raw = get_var(absv_names)
        surface_relative_vorticity = _surface_relative_vorticity_from_column(
            absv_raw, levels, latitude=glat, absolute=True)
    if surface_relative_vorticity is None:
        surface_relative_vorticity = _surface_relative_vorticity_from_wind_grid(
            ds, idx, levels)

    # Build output columns, ordered bottom (high pressure) to top (low pressure)
    cols = {
        "pres": mark_missing(levels, n_levels),
        "hght": mark_missing(hght, n_levels),
        "tmpc": mark_missing(tmpc, n_levels),
        "dwpc": mark_missing(dwpc, n_levels),
        "wdir": mark_missing(wdir, n_levels),
        "wspd": mark_missing(wspd, n_levels),
        "omeg": mark_missing(omeg, n_levels),
        "u": mark_missing(u_raw, n_levels),
        "v": mark_missing(v_raw, n_levels),
    }

    # Sort bottom-up (highest pressure first)
    order = np.argsort(-cols["pres"])
    for key in cols:
        cols[key] = cols[key][order]
    if surface_relative_vorticity is not None:
        cols["surface_relative_vorticity"] = surface_relative_vorticity

    return cols, n_levels, glat, glon


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("HRRR Point Sounding Extraction")
    print("=" * 70)
    print(f"  Location:    ({LAT}, {LON})")
    print(f"  Model run:   {RUN_DATE} {RUN_HOUR:02d}Z")
    print(f"  Forecast hr: {FXX}")
    print(f"  Valid time:  2026-06-12 {RUN_HOUR + FXX:02d}Z" if RUN_HOUR + FXX < 24
          else f"  Valid time:  2026-06-12 {(RUN_HOUR + FXX) % 24:02d}Z")
    print(f"  Output:      {OUT_NPZ}")
    print("=" * 70)

    # Retrieve HRRR data
    ds, H = retrieve_hrrr(RUN_DATE, RUN_HOUR, FXX)

    print(f"\nDataset variables: {list(ds.data_vars)}")
    print(f"Dataset coords: {list(ds.coords)}")

    # Extract column
    print(f"\nExtracting column at ({LAT}, {LON})...")
    cols, n_levels, glat, glon = extract_column(ds, LAT, LON)

    print(f"\n  Levels extracted: {n_levels}")
    print(f"  Pressure range: {cols['pres'][0]:.1f} - {cols['pres'][-1]:.1f} hPa")

    # Valid time and run time strings
    run_dt = datetime(2026, 6, 11, RUN_HOUR, 0, tzinfo=timezone.utc)
    valid_dt = datetime(2026, 6, 12, (RUN_HOUR + FXX) % 24, 0, tzinfo=timezone.utc)
    run_str = run_dt.strftime("%Y-%m-%d %H:%M")
    valid_str = valid_dt.strftime("%Y-%m-%d %H:%M")

    loc_label = f"HRRR {glat:.2f}N {abs(glon):.2f}W"

    # Assemble output
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
        "lat": glat,
        "lon": glon,
        "loc": loc_label,
        "model": "HRRR",
        "run": run_str,
        "valid": valid_str,
        "fxx": FXX,
    }
    if "surface_relative_vorticity" in cols:
        arrays["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    # Write .npz atomically
    out_dir = os.path.dirname(os.path.abspath(OUT_NPZ)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".npz", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez(fh, **arrays)
        os.replace(tmp, OUT_NPZ)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    # Write JSON metadata sidecar
    meta = {
        "model": "HRRR",
        "loc": loc_label,
        "requested_lat": LAT,
        "requested_lon": LON,
        "selected_lat": glat,
        "selected_lon": glon,
        "run": run_str,
        "valid": valid_str,
        "fxx": FXX,
        "levels": n_levels,
        "npz": os.path.abspath(OUT_NPZ),
    }
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]
    json_path = os.path.splitext(OUT_NPZ)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Wrote: {OUT_NPZ}")
    print(f"  Wrote: {json_path}")
    print("\nDone! Open with:")
    print(f"  sharpmod-gui {OUT_NPZ}")
    print(f"  sharpmod-render {OUT_NPZ} output.png")


if __name__ == "__main__":
    main()
