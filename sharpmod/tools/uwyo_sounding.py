"""Command-line front end for University of Wyoming (UWyo) soundings.

Ties the full bundled UWyo station catalogue and the modernized
:class:`sharpmod.io.uwyo_decoder.UWyo_Decoder` together so any UWyo station is
choosable, fetchable, and openable in the app:

* ``list``   -- list (or filter) every station in the bundled catalogue.
* ``search`` -- resolve a query (id or name substring) to matching stations.
* ``fetch``  -- fetch an observed sounding by station + UTC time, write it as a
  ``.npz`` point-sounding sidecar, and optionally render it to a PNG
  ("open it in app").

Examples
--------
List/search the full catalogue::

    python -m sharpmod.tools.uwyo_sounding list --grep norman
    python -m sharpmod.tools.uwyo_sounding search 72357

Fetch a sounding and render it::

    python -m sharpmod.tools.uwyo_sounding fetch 72357 "2024-05-20 00" \\
        --out oun.npz --render oun.png

Run under an env with ``numpy``/``certifi`` (e.g. ``.gribenv``); ``--render``
additionally requires the renderer stack (PySide6 + the vendored ``sharppy``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import numpy as np

from sharpmod.io import uwyo_catalog
from sharpmod.io.uwyo_decoder import (
    MS_TO_KT,  # noqa: F401  (re-exported for callers/tests)
    StationLookupError,
    UWyo_Decoder,
    UWyoError,
)

MISSING = -9999.0


def _print_stations(rows):
    if not rows:
        print("(no matching stations)")
        return
    print("%-8s  %-40s  %9s  %10s  %s" %
          ("ID", "NAME", "LAT", "LON", "SRC"))
    for r in rows:
        print("%-8s  %-40.40s  %9.3f  %10.3f  %s" %
              (r["id"], r["name"], r["lat"], r["lon"], r.get("src", "")))
    print("\n%d station(s)." % len(rows))


def _cmd_list(args):
    rows = uwyo_catalog.all_stations()
    if args.grep:
        q = args.grep.casefold()
        rows = [r for r in rows
                if q in r["id"].casefold() or q in r["name"].casefold()]
    _print_stations(rows)
    return 0


def _cmd_search(args):
    rows = uwyo_catalog.search_stations(args.query, limit=args.limit)
    _print_stations(rows)
    return 0 if rows else 1


def _parse_when(value):
    """Parse a fetch time: ISO-8601 or ``YYYY-MM-DD HH`` (UTC)."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("could not parse time %r: %s" % (value, exc))


def _write_npz(prof, out_path, meta, loc):
    """Write a decoded Profile to the shared ``.npz`` point-sounding format."""
    def col(name):
        arr = np.ma.asarray(getattr(prof, name), dtype=float)
        return np.asarray(arr.filled(MISSING), dtype=float)

    n = col("pres").size
    omeg = getattr(prof, "omeg", None)
    if omeg is None:
        omeg = np.full(n, MISSING)
    else:
        omeg = np.asarray(np.ma.asarray(omeg, dtype=float).filled(MISSING))

    valid = meta.get("valid")
    valid_str = valid.strftime("%Y-%m-%d %H:%M") if isinstance(valid, datetime) \
        else str(meta.get("valid_str", ""))
    lat = meta.get("lat", float("nan"))
    lon = meta.get("lon", float("nan"))
    np.savez(
        out_path,
        pres=col("pres"), hght=col("hght"), tmpc=col("tmpc"),
        dwpc=col("dwpc"), wdir=col("wdir"), wspd=col("wspd"), omeg=omeg,
        lat=float(lat) if lat == lat else 0.0,
        lon=float(lon) if lon == lon else 0.0,
        loc=loc, model="Observed",
        run=valid_str, valid=valid_str, fxx=0,
    )
    return out_path


def _cmd_fetch(args):
    decoder = UWyo_Decoder(full_catalog=True)

    # Resolve the station so we can label the output and use its source.
    try:
        meta = decoder.resolve_station(args.station)
    except StationLookupError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    when = _parse_when(args.time)
    print("Fetching %s (%s) at %s UTC ..."
          % (meta.id, meta.name, when.strftime("%Y-%m-%d %H:00")))
    try:
        prof = decoder.fetch(meta.id, when)
    except UWyoError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    # Prefer the station's catalogue coordinates for the output label.
    prof_meta = dict(getattr(prof, "meta", {}) or {})
    if prof_meta.get("lat") != prof_meta.get("lat") or "lat" not in prof_meta:
        prof_meta["lat"] = meta.lat
    if prof_meta.get("lon") != prof_meta.get("lon") or "lon" not in prof_meta:
        prof_meta["lon"] = meta.lon
    prof_meta.setdefault("valid", when)

    out = args.out or "uwyo_%s_%s.npz" % (meta.id, when.strftime("%Y%m%d%H"))
    loc = args.loc or meta.name.split(",")[0].split()[0]
    _write_npz(prof, out, prof_meta, loc)
    n = int(np.ma.asarray(prof.pres).size)
    print("wrote %s (%d levels, station %s)" % (out, n, meta.id))

    if args.render is not None:
        from sharpmod.tools import render_npz
        png = args.render or None
        path = render_npz(out, png)
        print("rendered %s" % path)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="uwyo_sounding", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("list", help="list all catalogue stations")
    pl.add_argument("--grep", default=None, help="filter by id/name substring")
    pl.set_defaults(func=_cmd_list)

    ps = sub.add_parser("search", help="resolve a station query")
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=None)
    ps.set_defaults(func=_cmd_search)

    pf = sub.add_parser("fetch", help="fetch + write .npz (and optionally render)")
    pf.add_argument("station", help="station id or name query")
    pf.add_argument("time", help="UTC time, 'YYYY-MM-DD HH' or ISO-8601")
    pf.add_argument("--out", default=None, help="output .npz path")
    pf.add_argument("--loc", default=None, help="location label")
    pf.add_argument("--render", nargs="?", const="", default=None,
                    metavar="PNG",
                    help="also render to PNG (optional path)")
    pf.set_defaults(func=_cmd_fetch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
