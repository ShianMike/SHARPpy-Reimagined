"""Build the bundled University of Wyoming (UWyo) station catalogue.

The modern UWyo radiosonde server (``/wsgi/``) exposes the set of stations that
reported for a given valid time through a JSON endpoint::

    https://weather.uwyo.edu/wsgi/sounding_json?datetime=YYYY-MM-DD HH:00:00

Each entry is ``{stationid, name, lat, lon, src}``. The set returned is *only*
the stations that reported at that time, so this builder **aggregates the union
across many sample datetimes** (00Z/12Z across every month of several years) to
assemble a comprehensive catalogue of fixed radiosonde sites, then writes it to
``sharpmod/resources/uwyo_stations.json`` -- the resource
:class:`sharpmod.io.uwyo_decoder.UWyo_Decoder` loads so every UWyo station is
choosable offline.

Only fixed sites (numeric WMO station ids) are kept; ephemeral mobile/ship
soundings (random alphanumeric ids) are dropped since their ids change per
launch. A station's preferred ``src`` (``FM35``/``BUFR``/...) is recorded so the
fetch path can pass the right source parameter.

Usage (run under an env with ``numpy``/``certifi``, e.g. ``.gribenv``)::

    python -m sharpmod.tools.build_uwyo_catalog [out.json] [--years 2024 2023]

This is a *build-time* utility; it is not imported by the decoder at runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
    _CA_FILE = certifi.where()
except Exception:  # pragma: no cover
    _CA_FILE = None

JSON_URL = "https://weather.uwyo.edu/wsgi/sounding_json"
_UA = {"User-Agent": "Mozilla/5.0 (SHARPpy Reimagined catalogue builder)"}

# Default resource destination (package-relative).
_DEFAULT_OUT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "resources",
                 "uwyo_stations.json"))


def _fetch_stations(datetime_str, timeout=60, retries=2):
    """Return the station list reported at ``datetime_str`` (UTC)."""
    url = JSON_URL + "?" + urlencode({"datetime": datetime_str})
    ctx = ssl.create_default_context(cafile=_CA_FILE)
    last = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=_UA)
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                data = json.load(resp)
            return data.get("stations", []) or []
        except Exception as exc:  # noqa: BLE001 - transient network errors
            last = exc
            time.sleep(1.5 * (attempt + 1))
    print("  ! failed %s (%s)" % (datetime_str, last), file=sys.stderr)
    return []


def _sample_datetimes(years):
    """Yield ``YYYY-MM-DD HH:00:00`` at 00Z/12Z for each month of each year."""
    for year in years:
        for month in range(1, 13):
            # The 15th avoids month-boundary gaps; both main synoptic hours.
            for hour in ("00", "12"):
                yield "%04d-%02d-15 %s:00:00" % (year, month, hour)


def build_catalog(years, out_path=_DEFAULT_OUT):
    """Aggregate the station union across sampled datetimes and write JSON."""
    stations = {}
    datetimes = list(_sample_datetimes(years))
    print("Sampling %d datetimes across years %s ..."
          % (len(datetimes), ", ".join(str(y) for y in years)))

    for i, dt in enumerate(datetimes, 1):
        rows = _fetch_stations(dt)
        added = 0
        for row in rows:
            sid = str(row.get("stationid", "")).strip()
            # Keep only fixed WMO sites (numeric ids); drop ephemeral mobile
            # soundings whose ids change per launch.
            if not sid.isdigit():
                continue
            name = str(row.get("name", "")).strip()
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            src = str(row.get("src", "")).strip() or "UNKNOWN"
            if sid not in stations:
                stations[sid] = {"id": sid, "name": name, "lat": lat,
                                 "lon": lon, "src": src}
                added += 1
            else:
                # Prefer a non-BUFR (native TEMP/FM35) source label if seen.
                if stations[sid]["src"] == "BUFR" and src != "BUFR":
                    stations[sid]["src"] = src
        print("  [%3d/%3d] %s -> %d rows, +%d new (total %d)"
              % (i, len(datetimes), dt, len(rows), added, len(stations)))

    catalog = {
        "generated_from": "weather.uwyo.edu/wsgi/sounding_json",
        "sampled_years": list(years),
        "station_count": len(stations),
        "stations": [stations[k] for k in sorted(stations)],
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=1, ensure_ascii=False)
    print("Wrote %d stations to %s" % (len(stations), out_path))
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default=_DEFAULT_OUT,
                        help="output JSON path")
    parser.add_argument("--years", nargs="+", type=int,
                        default=[2024, 2018, 2010, 2000],
                        help="years to sample (00Z/12Z, 15th of each month)")
    args = parser.parse_args(argv)
    build_catalog(args.years, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
