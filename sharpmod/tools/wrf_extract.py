"""WRF-ARW point-sounding extractor (``wrfout`` -> ``.npz``).

Extract a *point* sounding from a raw WRF-ARW model-output file (``wrfout*``, a
NetCDF/HDF5 file) at an arbitrary latitude/longitude (and optional valid time)
and write it in the fork's ``.npz`` point-sounding format so it renders through
the **same** code path as the HRRR ``.npz`` sidecar and the ERA5 extractor
(:func:`sharpmod.io.decoder.load_npz`).

This complements the vendored ``arw_decoder`` (which reads the SHARPpy WRF-ARW
*text* sounding format): here we read the native WRF NetCDF output directly,
so a forecaster can pull a sounding straight out of a model run.

WRF fields and the transforms applied (all standard WRF-ARW conventions):

======================  ==================================================
Output column           WRF source
======================  ==================================================
``pres`` (hPa)          ``(P + PB) / 100``
``hght`` (m MSL)        ``(PH + PHB) / g``, destaggered to mass levels
``tmpc`` (deg C)        ``theta * (p/p0)**(Rd/cp) - 273.15`` where
                        ``theta = T + 300``
``dwpc`` (deg C)        Magnus dewpoint from ``QVAPOR`` mixing ratio + p
``wdir``/``wspd``       ``U``/``V`` destaggered to mass points and rotated
                        to earth-relative with ``COSALPHA``/``SINALPHA``
                        (when present), then to met direction / knots
======================  ==================================================

The nearest grid point is selected by great-circle distance over the model's
2-D ``XLAT``/``XLONG`` fields, and the nearest ``Times`` slice to the requested
valid time (when several are present). The output is written atomically (temp
file + rename) with a ``.json`` metadata sidecar recording the requested and
selected coordinates and time, mirroring the ERA5 extractor.

``xarray`` (plus a NetCDF backend such as ``netCDF4`` or ``h5netcdf``) is
required to read ``wrfout`` files; it is imported lazily so importing this
module never requires it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np

# Reuse the ERA5 extractor's tested geometry / IO primitives so both point
# extractors share one implementation and one output format.
from sharpmod.tools.era5_extract import (
    MISSING,
    ERA5ExtractionError,
    ExtractionCancelled,
    ParameterRangeError,
    RetrievalError,
    _as_datetime,
    _atomic_write_json,
    _atomic_write_npz,
    _check_cancelled,
    _emit_progress,
    _quiet_remove,
    great_circle_distance_km,
    select_nearest_grid_point,
    uv_to_dir_spd,
)

__all__ = [
    "extract",
    "inspect_file",
    "point_in_domain",
    "require_runtime_dependencies",
    "WRFExtractionError",
    "ExtractionCancelled",
    "ParameterRangeError",
    "RetrievalError",
]

# Standard gravity and Poisson/Exner constants (WRF-ARW conventions).
G0 = 9.80665
P0 = 100000.0          # reference pressure (Pa)
RD_CP = 287.0 / 1004.0  # Rd/cp for the Exner conversion
EPS = 0.622            # Rd/Rv, for mixing-ratio -> vapor-pressure

LAT_MIN, LAT_MAX = -90.0, 90.0


class WRFExtractionError(ERA5ExtractionError):
    """Base class for WRF extraction failures (shares the ERA5 hierarchy)."""


def require_runtime_dependencies():
    """Validate xarray and a usable NetCDF engine with focused setup help."""
    try:
        import xarray as xr
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RetrievalError(
            "raw wrfout support requires the optional [wrf] extra "
            "(xarray and netCDF4): %s. Install it with: "
            "pip install -e \".[wrf]\"" % exc
        ) from exc
    try:
        engines = xr.backends.list_engines()
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RetrievalError(
            "xarray could not inspect its NetCDF backends: %s" % exc
        ) from exc
    if not any(name in engines for name in ("netcdf4", "h5netcdf", "scipy")):
        raise RetrievalError(
            "raw wrfout support needs a NetCDF backend. Install it with: "
            "pip install -e \".[wrf]\""
        )
    return True


# ---------------------------------------------------------------------------
# Thermodynamics
# ---------------------------------------------------------------------------

def _dewpoint_from_mixing_ratio(qv, pres_hpa):
    """Dewpoint (deg C) from water-vapor mixing ratio (kg/kg) and pressure hPa.

    Vapor pressure from mixing ratio is ``e = w*p/(eps + w)`` (Magnus inverse
    then gives the dewpoint). NaNs propagate so the level can be marked missing.
    """
    qv = np.clip(np.asarray(qv, dtype=float), 0.0, None)
    p = np.asarray(pres_hpa, dtype=float)
    e = (qv * p) / (EPS + qv)
    e = np.clip(e, 1e-6, None)
    a, b = 17.625, 243.04
    ln = np.log(e / 6.112)
    return (b * ln) / (a - ln)


# ---------------------------------------------------------------------------
# Destaggering helpers
# ---------------------------------------------------------------------------

def _destagger(arr, axis):
    """Average adjacent points along ``axis`` to move off a staggered grid."""
    arr = np.asarray(arr, dtype=float)
    slc1 = [slice(None)] * arr.ndim
    slc2 = [slice(None)] * arr.ndim
    slc1[axis] = slice(0, -1)
    slc2[axis] = slice(1, None)
    return 0.5 * (arr[tuple(slc1)] + arr[tuple(slc2)])


def _var(ds, name):
    """Return a variable's values as float, or ``None`` if absent."""
    if name in ds:
        return np.asarray(ds[name].values, dtype=float)
    return None


# ---------------------------------------------------------------------------
# Time / grid selection
# ---------------------------------------------------------------------------

def _wrf_times(ds):
    """Return a list of ``datetime`` for the WRF ``Times`` / ``XTIME`` axis."""
    # WRF stores Times as a (Time, DateStrLen) char array "YYYY-MM-DD_HH:MM:SS".
    if "Times" in ds:
        raw = ds["Times"].values
        out = []
        for row in np.atleast_1d(raw):
            if isinstance(row, bytes):
                s = row.decode("ascii", "replace")
            elif isinstance(row, np.ndarray):
                s = b"".join(
                    x if isinstance(x, bytes) else str(x).encode()
                    for x in row.ravel()).decode("ascii", "replace")
            else:
                s = str(row)
            s = s.strip().replace("_", " ")
            try:
                out.append(datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                           .replace(tzinfo=timezone.utc))
            except ValueError:
                out.append(None)
        return out
    if "XTIME" in ds:
        return [_as_datetime(t) for t in np.atleast_1d(ds["XTIME"].values)]
    return [None]


def _select_time_index(times, valid_time):
    """Return the index of the WRF time closest to ``valid_time``."""
    if valid_time is None:
        return 0
    target = _as_datetime(valid_time)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    best_i, best_delta = 0, None
    for i, t in enumerate(times):
        if t is None:
            continue
        delta = abs((t - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best_i, best_delta = i, delta
    return best_i


def _grid2d(values, time_index=0):
    """Reduce a WRF coordinate variable to one two-dimensional grid."""
    arr = np.asarray(values, dtype=float)
    while arr.ndim > 2:
        arr = arr[time_index] if arr.shape[0] > time_index else arr[0]
    if arr.ndim != 2:
        raise RetrievalError("WRF latitude/longitude coordinates are not 2-D")
    return arr


def _grid_boundary(lat2d, lon2d):
    """Return the ordered exterior points of a curvilinear WRF grid."""
    lat2d = np.asarray(lat2d, dtype=float)
    lon2d = np.asarray(lon2d, dtype=float)
    if lat2d.shape != lon2d.shape or lat2d.ndim != 2:
        raise RetrievalError("WRF XLAT/XLONG grids have incompatible shapes")
    ny, nx = lat2d.shape
    if ny < 2 or nx < 2:
        return [
            (float(lat2d.flat[0]), float(lon2d.flat[0]))
        ] if lat2d.size else []
    indices = []
    indices.extend((0, ix) for ix in range(nx))
    indices.extend((iy, nx - 1) for iy in range(1, ny))
    indices.extend((ny - 1, ix) for ix in range(nx - 2, -1, -1))
    indices.extend((iy, 0) for iy in range(ny - 2, 0, -1))
    return [
        (float(lat2d[iy, ix]), float(lon2d[iy, ix]))
        for iy, ix in indices
        if np.isfinite(lat2d[iy, ix]) and np.isfinite(lon2d[iy, ix])
    ]


def _point_on_segment(x, y, x1, y1, x2, y2, tolerance=1.0e-7):
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    scale = max(1.0, abs(x2 - x1), abs(y2 - y1))
    if abs(cross) > tolerance * scale:
        return False
    return (
        min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    )


def _point_in_boundary(boundary, lat, lon):
    """Ray-cast against a WRF perimeter, unwrapping around the test point."""
    points = [
        (float(plat), float(lon) + ((float(plon) - float(lon) + 180.0) % 360.0) - 180.0)
        for plat, plon in boundary
        if np.isfinite(plat) and np.isfinite(plon)
    ]
    if len(points) < 3:
        if len(points) == 1:
            return abs(points[0][0] - lat) <= 1.0e-7 \
                and abs(points[0][1] - lon) <= 1.0e-7
        return False
    x, y = float(lon), float(lat)
    inside = False
    previous = points[-1]
    for current in points:
        y1, x1 = previous
        y2, x2 = current
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def point_in_domain(domain, lat, lon):
    """Return whether ``lat, lon`` lies inside inspected WRF grid edges."""
    if not domain or "boundary" not in domain:
        return False
    try:
        return _point_in_boundary(domain["boundary"], float(lat), float(lon))
    except (TypeError, ValueError):
        return False


def _domain_info(ds, source_file=None):
    xlat = _var(ds, "XLAT")
    xlong = _var(ds, "XLONG")
    if xlat is None or xlong is None:
        raise RetrievalError("WRF file lacks XLAT/XLONG coordinates")
    lat2d = _grid2d(xlat)
    lon2d = _grid2d(xlong)
    finite = np.isfinite(lat2d) & np.isfinite(lon2d)
    if not finite.any():
        raise RetrievalError("WRF XLAT/XLONG grid contains no finite points")
    boundary = _grid_boundary(lat2d, lon2d)
    center_lat = float(np.nanmean(lat2d[finite]))
    center_lon = float(np.nanmean(lon2d[finite]))
    center_lon = ((center_lon + 180.0) % 360.0) - 180.0
    normalized_lon = (
        center_lon + ((lon2d[finite] - center_lon + 180.0) % 360.0) - 180.0
    )
    lon_min = float(np.nanmin(normalized_lon))
    lon_max = float(np.nanmax(normalized_lon))
    if lon_min < -180.0 or lon_max > 180.0:
        map_bounds = (-180.0, 180.0, float(np.nanmin(lat2d[finite])),
                      float(np.nanmax(lat2d[finite])))
    else:
        map_bounds = (lon_min, lon_max, float(np.nanmin(lat2d[finite])),
                      float(np.nanmax(lat2d[finite])))
    return {
        "source_file": os.path.abspath(source_file) if source_file else None,
        "shape": tuple(int(value) for value in lat2d.shape),
        "bounds": map_bounds,
        "center": (center_lat, center_lon),
        "boundary": tuple(boundary),
        "times": tuple(_wrf_times(ds)),
    }


def inspect_file(wrfout_path, dataset=None, cancelled=None):
    """Inspect one raw wrfout domain and its times without extracting data."""
    _check_cancelled(cancelled)
    close_ds = False
    if dataset is None:
        require_runtime_dependencies()
        import xarray as xr
        try:
            ds = xr.open_dataset(wrfout_path)
            close_ds = True
        except Exception as exc:
            raise RetrievalError(
                "failed to open WRF file %r: %s" % (wrfout_path, exc)
            ) from exc
    else:
        ds = dataset
    try:
        result = _domain_info(ds, wrfout_path)
        _check_cancelled(cancelled)
        return result
    finally:
        if close_ds:
            try:
                ds.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(wrfout_path, lat, lon, out_path, valid_time=None,
            dataset=None, loc="WRFpt", progress_callback=None,
            cancelled=None):
    """Extract a WRF point sounding to a ``.npz`` sidecar.

    Parameters
    ----------
    wrfout_path : str
        Path to the WRF-ARW output file (``wrfout*``). Ignored if ``dataset``
        is supplied.
    lat, lon : float
        Requested source latitude ([-90, 90]) and longitude (degrees).
    out_path : str
        Destination ``.npz`` path; a ``.json`` sidecar is written alongside.
    valid_time : datetime or str, optional
        Requested valid time (UTC). When the file holds several times, the
        nearest is selected; when omitted, the first time is used.
    dataset : xarray.Dataset, optional
        A pre-opened dataset (mainly for testing). When omitted the file at
        ``wrfout_path`` is opened via xarray.
    loc : str
        Location label recorded in the output.

    Returns
    -------
    str
        ``out_path`` on success.

    Raises
    ------
    ParameterRangeError
        If ``lat`` is outside [-90, 90]. No file is written.
    RetrievalError
        If the file cannot be opened / lacks required fields. No partial file.
    """
    lat = float(lat)
    lon = float(lon)
    _emit_progress(progress_callback, "validating")
    _check_cancelled(cancelled)
    if not (LAT_MIN <= lat <= LAT_MAX):
        raise ParameterRangeError(
            "latitude %.4f is out of range; permitted range is [%.1f, %.1f]"
            % (lat, LAT_MIN, LAT_MAX))

    # 1. Open the dataset (retrieval failures write nothing).
    close_ds = False
    if dataset is None:
        require_runtime_dependencies()
        import xarray as xr
        try:
            _emit_progress(progress_callback, "opening")
            ds = xr.open_dataset(wrfout_path)
            close_ds = True
        except Exception as exc:
            raise RetrievalError(
                "failed to open WRF file %r: %s" % (wrfout_path, exc)) from exc
    else:
        ds = dataset

    try:
        _check_cancelled(cancelled)
        _emit_progress(progress_callback, "extracting")
        cols, sel = _build_columns(ds, lat, lon, valid_time)
        _check_cancelled(cancelled)
    except (
        WRFExtractionError,
        ExtractionCancelled,
        ParameterRangeError,
        RetrievalError,
    ):
        raise
    except Exception as exc:  # noqa: BLE001 - any field/shape error -> retrieval
        raise RetrievalError(
            "failed to extract WRF column: %s" % exc) from exc
    finally:
        if close_ds:
            try:
                ds.close()
            except Exception:
                pass

    n = cols["pres"].size
    omeg = np.full(n, MISSING)

    sel_valid = sel["valid"]
    run_str = sel_valid.strftime("%Y-%m-%d %H:%M") if sel_valid else \
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": omeg, "lat": sel["glat"], "lon": sel["glon"],
        "loc": loc, "model": "WRF-ARW", "run": run_str, "valid": run_str,
        "fxx": 0,
    }

    req_valid = None
    if valid_time is not None:
        vt = _as_datetime(valid_time)
        req_valid = vt.strftime("%Y-%m-%d %H:%M")

    meta = {
        "model": "WRF-ARW",
        "loc": loc,
        "source_file": os.path.abspath(wrfout_path) if wrfout_path else None,
        "requested_lat": lat,
        "requested_lon": lon,
        "requested_valid": req_valid,
        "selected_lat": sel["glat"],
        "selected_lon": sel["glon"],
        "selected_valid": run_str,
        "run": run_str,
        "valid": run_str,
        "fxx": 0,
        "npz": os.path.abspath(out_path),
        "levels": int(n),
        "backend": "xarray/NetCDF WRF",
        "decoder": "raw wrfout pressure-column extractor",
        "cache_hit": False,
        "surface_vorticity_source": "not available from this WRF workflow",
    }

    _emit_progress(progress_callback, "writing")
    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
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


def _build_columns(ds, lat, lon, valid_time):
    """Extract and transform the WRF vertical column at the nearest point."""
    xlat = _var(ds, "XLAT")
    xlong = _var(ds, "XLONG")
    if xlat is None or xlong is None:
        raise RetrievalError("WRF file lacks XLAT/XLONG coordinates")

    # Time selection.
    times = _wrf_times(ds)
    it = _select_time_index(times, valid_time)
    sel_valid = times[it] if it < len(times) else None

    # XLAT/XLONG are (Time, south_north, west_east); reduce to this time's 2-D.
    def _selected_grid2d(a):
        return _grid2d(a, it)

    lat2d = _selected_grid2d(xlat)
    lon2d = _selected_grid2d(xlong)

    domain = {"boundary": tuple(_grid_boundary(lat2d, lon2d))}
    if not point_in_domain(domain, lat, lon):
        raise ParameterRangeError(
            "requested point (%.4f, %.4f) lies outside the WRF grid domain"
            % (lat, lon)
        )

    (iy, ix), glat, glon = select_nearest_grid_point(lat2d, lon2d, lat, lon)
    glon = ((glon + 180.0) % 360.0) - 180.0

    def _column(name, destag_axis=None):
        """Return the vertical column of ``name`` at (it, :, iy, ix)."""
        raw = _var(ds, name)
        if raw is None:
            return None
        arr = raw
        # Drop the time axis.
        if arr.ndim == 4:
            arr = arr[it]
        # Now (level[, stag], south_north[, stag], west_east[, stag]).
        if destag_axis is not None and arr.ndim == 3:
            arr = _destagger(arr, destag_axis)
        # Reduce horizontal dims to the selected point; vertical axis is 0.
        if arr.ndim == 3:
            return arr[:, iy, ix]
        if arr.ndim == 2:  # a surface/2-D field broadcast over levels
            return np.array([arr[iy, ix]])
        return None

    # Pressure (Pa -> hPa).
    p = _column("P")
    pb = _column("PB")
    if p is None or pb is None:
        raise RetrievalError("WRF file lacks perturbation/base pressure (P/PB)")
    pres = (p + pb) / 100.0

    # Height: geopotential on staggered levels -> destagger vertically.
    ph = _var(ds, "PH")
    phb = _var(ds, "PHB")
    if ph is None or phb is None:
        raise RetrievalError("WRF file lacks geopotential (PH/PHB)")
    geo = np.asarray(ph, dtype=float) + np.asarray(phb, dtype=float)
    if geo.ndim == 4:
        geo = geo[it]
    geo = _destagger(geo, 0)  # stagger is the vertical axis
    hght = geo[:, iy, ix] / G0

    # Temperature via Exner: theta = T + 300; T = theta*(p/p0)^(Rd/cp).
    t_pert = _column("T")
    if t_pert is None:
        raise RetrievalError("WRF file lacks perturbation potential temp (T)")
    theta = t_pert + 300.0
    pres_pa = pres * 100.0
    tmpk = theta * (pres_pa / P0) ** RD_CP
    tmpc = tmpk - 273.15

    # Dewpoint from QVAPOR mixing ratio.
    qv = _column("QVAPOR")
    dwpc = _dewpoint_from_mixing_ratio(qv, pres) if qv is not None else None

    # Winds: destagger U (west_east_stag, axis 2) and V (south_north_stag,
    # axis 1) to mass points, then rotate grid-relative -> earth-relative.
    u = _column("U", destag_axis=2)
    v = _column("V", destag_axis=1)
    if u is not None and v is not None:
        cosa = _var(ds, "COSALPHA")
        sina = _var(ds, "SINALPHA")
        if cosa is not None and sina is not None:
            ca = _selected_grid2d(cosa)[iy, ix]
            sa = _selected_grid2d(sina)[iy, ix]
            u_earth = u * ca - v * sa
            v_earth = v * ca + u * sa
        else:
            u_earth, v_earth = u, v
        wdir, wspd = uv_to_dir_spd(u_earth, v_earth)
    else:
        wdir = wspd = None

    n = pres.size

    def _mm(arr):
        if arr is None:
            return np.full(n, MISSING)
        a = np.asarray(arr, dtype=float).copy()
        if a.size != n:  # broadcast/short columns -> mark missing
            return np.full(n, MISSING)
        a[~np.isfinite(a)] = MISSING
        return a

    cols = {
        "pres": _mm(pres), "hght": _mm(hght), "tmpc": _mm(tmpc),
        "dwpc": _mm(dwpc), "wdir": _mm(wdir), "wspd": _mm(wspd),
    }
    # Order bottom (highest pressure) -> top.
    order = np.argsort(-cols["pres"])
    for k in cols:
        cols[k] = cols[k][order]

    sel = {"glat": glat, "glon": glon, "valid": sel_valid}
    return cols, sel


def _parse_cli_time(value):
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat(value)
    except ValueError:
        return _dt.strptime(value, "%Y-%m-%d %H:%M")


def main(argv=None):  # pragma: no cover - thin CLI wrapper
    """CLI: ``wrf_extract WRFOUT LAT LON [out.npz] [--time "YYYY-MM-DD HH:MM"]``."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Extract a WRF point sounding")
    parser.add_argument("wrfout", help="path to a wrfout* NetCDF file")
    parser.add_argument("lat", type=float)
    parser.add_argument("lon", type=float)
    parser.add_argument("out", nargs="?", default=None,
                        help="output .npz path")
    parser.add_argument("--time", default=None,
                        help="valid time (ISO or 'YYYY-MM-DD HH:MM')")
    parser.add_argument("--loc", default="WRFpt")
    parser.add_argument("--render", nargs="?", const="", default=None,
                        metavar="PNG",
                        help="also render the sounding to a PNG (optional path)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    vt = _parse_cli_time(args.time) if args.time else None
    out = args.out or "wrf_%.2fN_%.2fE.npz" % (args.lat, args.lon)
    try:
        path = extract(args.wrfout, args.lat, args.lon, out,
                       valid_time=vt, loc=args.loc)
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
