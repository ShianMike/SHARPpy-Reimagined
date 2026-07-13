"""ECMWF IFS open-data point-sounding extractor (``IFS_Extractor``).

Extract a *point* sounding from the ECMWF **IFS open data** forecast at an
arbitrary latitude, longitude, and valid time and write it in the fork's
``.npz`` point-sounding format so it renders through the **same** code path as
the HRRR ``.npz`` sidecar and the ERA5 extractor
(:func:`sharpmod.io.decoder.load_npz`).

The ECMWF IFS open dataset publishes pressure-level ``gh`` (geopotential
height), ``t`` (temperature), ``r`` (relative humidity), ``u``/``v`` (winds),
and ``w`` (vertical velocity) on the 0.25-degree global grid at 14 mandatory
levels (1000..10 hPa). Those fields map directly onto the coordinate/variable
name candidates the shared ERA5 column-builder already understands, so this
module retrieves the IFS column via Herbie and reuses
:mod:`sharpmod.tools.era5_extract`'s selection / conversion / atomic-write
machinery -- only the retrieval and the recorded model label differ.

Behaviour mirrors the ERA5 extractor:

* Snap the requested valid time to the closest available IFS cycle
  (00/06/12/18 UTC) and forecast hour, select the nearest grid point by
  great-circle distance, and extract the vertical column there.
* Convert ``u``/``v`` to the wind-direction / wind-speed columns the ``.npz``
  format stores, derive dewpoint from relative humidity, and mark any absent /
  masked per-level field with the ``-9999.0`` missing sentinel.
* Validate lat/lon before any retrieval and never leave a partial output file
  behind (temp file + atomic rename).
* Record the requested and selected lat/lon and run/valid time in a ``.json``
  metadata sidecar.

The ECMWF tooling (``herbie-data``, ``cfgrib``, ``xarray``) is the same optional
``[era5]`` install extra, imported lazily inside :func:`_retrieve_dataset`.
"""

import os
from datetime import datetime, timezone

from sharpmod.tools import era5_extract as _e5
from sharpmod.tools.era5_extract import (
    ERA5ExtractionError as IFSExtractionError,  # re-export under IFS names
    ParameterRangeError,
    RetrievalError,
    MISSING,
    _as_datetime,
    _atomic_write_json,
    _atomic_write_npz,
    _build_columns,
    _coord_values,
    _quiet_remove,
    _select_time,
    great_circle_distance_km,
    select_nearest_grid_point,
    select_nearest_time,
    _LAT_COORDS,
    _LON_COORDS,
)

__all__ = [
    "extract",
    "IFSExtractionError",
    "ParameterRangeError",
    "RetrievalError",
]

MODEL_LABEL = "ECMWF-IFS"

# IFS open data is a global 0.25-degree grid.
LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 360.0

# Pressure-level fields to pull (gh=height, t, r=RH, u, v, w=vertical velocity,
# vo=relative vorticity for NSTP).
_IFS_PL_SEARCH = r":(gh|t|u|v|r|w|vo):\d+:pl:"

# IFS open-data operational cycles.
_CYCLE_HOURS = (0, 6, 12, 18)


def _validate_request(lat, lon):
    """Range-validate lat/lon before any retrieval or I/O."""
    if not (LAT_MIN <= lat <= LAT_MAX):
        raise ParameterRangeError(
            "latitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees" % (lat, LAT_MIN, LAT_MAX))
    if not (LON_MIN <= lon <= LON_MAX):
        raise ParameterRangeError(
            "longitude %.4f is out of range; permitted range is "
            "[%.1f, %.1f] degrees (IFS global coverage)"
            % (lon, LON_MIN, LON_MAX))


def _nearest_cycle(dt):
    """Return the most recent IFS cycle datetime at or before ``dt`` (UTC)."""
    dt = _as_datetime(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    hour = max(h for h in _CYCLE_HOURS if h <= dt.hour) \
        if dt.hour >= _CYCLE_HOURS[0] else _CYCLE_HOURS[0]
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def _retrieve_dataset(valid_time):
    """Fetch the IFS pressure-level column dataset via Herbie.

    IFS open data only runs the 00/06/12/18 UTC cycles, so an arbitrary
    requested valid time (e.g. 09 UTC) is served as a **forecast step** off the
    most recent cycle at or before it (e.g. the 06 UTC run at F03). Walks recent
    cycles back from ``valid_time`` -- increasing the forecast hour by 6 each
    step -- until an available run is found. Returns
    ``(dataset, run_datetime, fxx)``. Any failure surfaces as a
    :class:`RetrievalError` so no partial output is written.
    """
    try:
        from herbie import Herbie  # noqa: F401  (optional [era5] extra)
    except Exception as exc:  # pragma: no cover - depends on optional extra
        raise RetrievalError(
            "ECMWF IFS support requires the optional [era5] extra "
            "(herbie-data, cfgrib, xarray): %s" % exc) from exc

    from datetime import timedelta

    run = _nearest_cycle(valid_time)
    fxx = int(round(
        (_as_datetime(valid_time).astimezone(timezone.utc) - run)
        .total_seconds() / 3600.0))
    last_exc = None
    # IFS open data is typically available within a few hours of the cycle;
    # step back up to ~2 days of cycles to find the newest usable run/step.
    for _ in range(8):
        try:  # pragma: no cover - network / optional dependency path
            H = Herbie(run.strftime("%Y-%m-%d %H:%M"), model="ifs",
                       product="oper", fxx=fxx)
            if H.grib is None:
                raise RetrievalError(
                    "no IFS grib for run %s F%03d" % (run.isoformat(), fxx))
            ds = H.xarray(_IFS_PL_SEARCH, remove_grib=False)
            if isinstance(ds, list):
                ds = _merge_datasets(ds)
            return ds, run, fxx
        except Exception as exc:  # pragma: no cover - network failure path
            last_exc = exc
            run = run - timedelta(hours=6)
            fxx += 6

    raise RetrievalError(
        "failed to retrieve an available ECMWF IFS run near %s: %s"
        % (_as_datetime(valid_time).isoformat(), last_exc))


def _merge_datasets(ds_list):  # pragma: no cover - optional dependency path
    """Merge cfgrib's split datasets into one, importing xarray lazily."""
    import xarray as xr
    from sharpmod.tools.era5_extract import _LEVEL_COORDS
    ds_list = [d for d in ds_list
               if any(c in d.coords for c in _LEVEL_COORDS)]
    if not ds_list:
        raise RetrievalError("no pressure-level IFS dataset was returned")
    return xr.merge(ds_list, compat="override", join="outer")


def extract(lat, lon, valid_time=None, out_path=None, dataset=None,
            run_time=None, loc="IFSpt"):
    """Extract an ECMWF IFS point sounding and write it as a ``.npz`` sidecar.

    Parameters
    ----------
    lat, lon : float
        Requested source latitude (degrees, [-90, 90]) and longitude (degrees).
    valid_time : datetime or str, optional
        Requested valid time (UTC). Defaults to "now" (latest available run).
    out_path : str
        Destination ``.npz`` path. A ``.json`` metadata sidecar is written
        alongside it (same stem).
    dataset : optional
        A pre-loaded xarray ``Dataset`` (used mainly for testing). When omitted,
        the IFS column is retrieved via Herbie (optional ``[era5]`` extra).
    run_time : datetime, optional
        The IFS cycle the ``dataset`` belongs to (used with ``dataset``).
    loc : str
        Location label recorded in the output.

    Returns
    -------
    str
        ``out_path`` on success.
    """
    lat = float(lat)
    lon = float(lon)
    if out_path is None:
        raise ValueError("out_path is required")

    if valid_time is None:
        valid_dt = datetime.now(timezone.utc)
    else:
        valid_dt = _as_datetime(valid_time)
        if valid_dt.tzinfo is None:
            valid_dt = valid_dt.replace(tzinfo=timezone.utc)

    # 1. Static range validation before any retrieval or I/O.
    _validate_request(lat, lon)

    # 2. Acquire the dataset (retrieval failures write nothing).
    if dataset is None:
        ds, run_dt, ret_fxx = _retrieve_dataset(valid_dt)
    else:
        ds = dataset
        run_dt = _as_datetime(run_time) if run_time is not None else valid_dt
        ret_fxx = None

    # 3. Nearest analysis time within the dataset, then nearest grid point.
    ds_t, selected_time = _select_time(ds, valid_dt)

    _, lats = _coord_values(ds_t, _LAT_COORDS)
    _, lons = _coord_values(ds_t, _LON_COORDS)
    if lats is None or lons is None:
        raise RetrievalError(
            "IFS dataset is missing latitude/longitude coordinates")
    index_tuple, glat, glon = select_nearest_grid_point(lats, lons, lat, lon)
    glon = ((glon + 180.0) % 360.0) - 180.0  # normalize to [-180, 180)

    # 4. Extract and convert the vertical column; mark per-level missing fields.
    cols, n_levels = _build_columns(ds_t, index_tuple, latitude=glat)

    # 5. Assemble output arrays + metadata and write atomically.
    #    IFS forecast steps carry the run time in the ``time`` coord and the
    #    real valid time in ``valid_time``; ``_select_time`` returns the former,
    #    so resolve the valid time from the retrieved forecast hour (or the
    #    dataset's ``valid_time`` coord for the pre-loaded ``dataset`` path).
    from datetime import timedelta

    run_dt = _as_datetime(run_dt)
    if ret_fxx is not None:
        fxx = int(ret_fxx)
        valid_sel = run_dt + timedelta(hours=fxx)
    else:
        _, vt = _coord_values(ds_t, ("valid_time",))
        if vt is not None and vt.size:
            valid_sel = _as_datetime(vt.reshape(-1)[0])
        else:
            valid_sel = _as_datetime(selected_time)
        fxx = int(round(
            (valid_sel - run_dt).total_seconds() / 3600.0))

    run_str = run_dt.strftime("%Y-%m-%d %H:%M")
    valid_str = valid_sel.strftime("%Y-%m-%d %H:%M")

    arrays = {
        "pres": cols["pres"], "hght": cols["hght"], "tmpc": cols["tmpc"],
        "dwpc": cols["dwpc"], "wdir": cols["wdir"], "wspd": cols["wspd"],
        "omeg": cols["omeg"], "uwnd": cols["u"], "vwnd": cols["v"],
        "lat": glat, "lon": glon, "loc": loc, "model": MODEL_LABEL,
        "run": run_str, "valid": valid_str, "fxx": fxx,
    }
    if "surface_relative_vorticity" in cols:
        arrays["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    meta = {
        "model": MODEL_LABEL,
        "loc": loc,
        "requested_lat": lat,
        "requested_lon": lon,
        "requested_valid": valid_dt.strftime("%Y-%m-%d %H:%M"),
        "selected_lat": glat,
        "selected_lon": glon,
        "selected_valid": valid_str,
        "run": run_str,
        "valid": valid_str,
        "fxx": fxx,
        "npz": os.path.abspath(out_path),
        "levels": int(n_levels),
    }
    if "surface_relative_vorticity" in cols:
        meta["surface_relative_vorticity"] = cols["surface_relative_vorticity"]

    _atomic_write_npz(out_path, arrays)
    json_path = os.path.splitext(out_path)[0] + ".json"
    try:
        _atomic_write_json(json_path, meta)
    except BaseException:
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
    """CLI: ``ifs_extract LAT LON [time] [out.npz] [--render [PNG]]``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="ifs_extract",
        description="Extract an ECMWF IFS open-data point sounding to a .npz")
    parser.add_argument("lat", type=float)
    parser.add_argument("lon", type=float)
    parser.add_argument("time", nargs="?", default=None,
                        help="valid time (ISO or 'YYYY-MM-DD HH:MM'); "
                             "defaults to the latest available run")
    parser.add_argument("out", nargs="?", default=None, help="output .npz path")
    parser.add_argument("--loc", default="IFSpt", help="location label")
    parser.add_argument("--render", nargs="?", const="", default=None,
                        metavar="PNG",
                        help="also render the sounding to a PNG (optional path)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    valid_time = _parse_cli_time(args.time) if args.time else None
    stamp = (valid_time or datetime.now(timezone.utc)).strftime("%Y%m%d%H")
    out = args.out or "ifs_point_%.2fN_%.2fE_%s.npz" % (
        args.lat, args.lon, stamp)
    try:
        path = extract(args.lat, args.lon, valid_time, out, loc=args.loc)
    except IFSExtractionError as exc:
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
