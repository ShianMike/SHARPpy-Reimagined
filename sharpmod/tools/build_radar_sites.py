"""Build the bundled NEXRAD single-site catalogue from NCEP's WMS.

NCEP's GeoServer publishes one workspace per WSR-88D, but its global capabilities
document does not list them -- asking for every workspace at once returns only the
mosaic layers, and the web listing that would enumerate them is forbidden. So the
catalogue is built by asking each candidate site for its own capabilities.

That is not just a workaround, it is also the validation: a site that answers with
a reflectivity layer is a site this application can actually draw, and one that
does not is omitted rather than shipped as a dead entry. The radar's position
comes from the layer's own advertised bounding box, whose centre is the antenna --
checked against the published coordinates for KLOT and KTLX, which agree exactly.

Run this only when the catalogue needs refreshing; the result is committed:

    python -m sharpmod.tools.build_radar_sites

Writes ``sharpmod/resources/radar_sites.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: Candidate WSR-88D identifiers. Deliberately a superset: anything that does not
#: answer is dropped, so an identifier that is decommissioned or absent from this
#: service costs one failed request rather than a wrong entry.
CANDIDATES = (
    # Contiguous United States
    "KABR", "KABX", "KAKQ", "KAMA", "KAMX", "KAPX", "KARX", "KATX", "KBBX",
    "KBGM", "KBHX", "KBIS", "KBLX", "KBMX", "KBOX", "KBRO", "KBUF", "KBYX",
    "KCAE", "KCBW", "KCBX", "KCCX", "KCLE", "KCLX", "KCRP", "KCXX", "KCYS",
    "KDAX", "KDDC", "KDFX", "KDGX", "KDIX", "KDLH", "KDMX", "KDOX", "KDTX",
    "KDVN", "KDYX", "KEAX", "KEMX", "KENX", "KEOX", "KEPZ", "KESX", "KEVX",
    "KEWX", "KEYX", "KFCX", "KFDR", "KFDX", "KFFC", "KFSD", "KFSX", "KFTG",
    "KFWS", "KGGW", "KGJX", "KGLD", "KGRB", "KGRK", "KGRR", "KGSP", "KGWX",
    "KGYX", "KHDX", "KHGX", "KHNX", "KHPX", "KHTX", "KICT", "KICX", "KILN",
    "KILX", "KIND", "KINX", "KIWA", "KIWX", "KJAX", "KJGX", "KJKL", "KLBB",
    "KLCH", "KLGX", "KLIX", "KLNX", "KLOT", "KLRX", "KLSX", "KLTX", "KLVX",
    "KLWX", "KLZK", "KMAF", "KMAX", "KMBX", "KMHX", "KMKX", "KMLB", "KMOB",
    "KMPX", "KMQT", "KMRX", "KMSX", "KMTX", "KMUX", "KMVX", "KMXX", "KNKX",
    "KNQA", "KOAX", "KOHX", "KOKX", "KOTX", "KPAH", "KPBZ", "KPDT", "KPOE",
    "KPUX", "KRAX", "KRGX", "KRIW", "KRLX", "KRTX", "KSFX", "KSGF", "KSHV",
    "KSJT", "KSOX", "KSRX", "KTBW", "KTFX", "KTLH", "KTLX", "KTWX", "KTYX",
    "KUDX", "KUEX", "KVAX", "KVBX", "KVNX", "KVTX", "KVWX", "KYUX",
    # Alaska
    "PABC", "PACG", "PAEC", "PAHG", "PAIH", "PAKC", "PAPD", "PABL",
    # Hawaii
    "PHKI", "PHKM", "PHMO", "PHWA",
    # Territories
    "TJUA", "PGUA",
)

BASE = "https://opengeo.ncep.noaa.gov/geoserver"

#: The single-site layer whose bounding box locates the antenna. Base
#: reflectivity, because every site publishes it.
ANCHOR_SUFFIX = "_sr_bref"

_BBOX = re.compile(
    r"<westBoundLongitude>([-\d.]+)</westBoundLongitude>\s*"
    r"<eastBoundLongitude>([-\d.]+)</eastBoundLongitude>\s*"
    r"<southBoundLatitude>([-\d.]+)</southBoundLatitude>\s*"
    r"<northBoundLatitude>([-\d.]+)</northBoundLatitude>")


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def probe(site: str, *, timeout: float = 45.0) -> dict | None:
    """Return the catalogue entry for one site, or ``None`` if it has none."""
    workspace = site.lower()
    url = (f"{BASE}/{workspace}/ows?service=WMS&version=1.3.0"
           f"&request=GetCapabilities")
    request = urllib.request.Request(
        url, headers={"User-Agent": "sharpmod-build-radar-sites"})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_context()) as response:
            text = response.read(8 * 1024 * 1024).decode("utf-8",
                                                         errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError):
        return None

    anchor = f"{workspace}{ANCHOR_SUFFIX}"
    if f"<Name>{anchor}</Name>" not in text:
        return None
    match = _BBOX.search(text)
    if not match:
        return None
    west, east, south, north = (float(value) for value in match.groups())
    if not (east > west and north > south):
        return None

    products = sorted(set(re.findall(rf"<Name>{workspace}_([a-z_]+)</Name>",
                                     text)))
    return {
        "id": site.upper(),
        "lat": round((south + north) / 2.0, 4),
        "lon": round((west + east) / 2.0, 4),
        # The advertised half-span, so a fetch can request the site's own extent
        # rather than assuming every site uses the same radius.
        "half_span_deg": round(min(east - west, north - south) / 2.0, 4),
        "products": products,
    }


def build(sites=CANDIDATES, *, workers: int = 8) -> dict:
    found: list[dict] = []
    missing: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for site, entry in zip(sites, pool.map(probe, sites)):
            if entry is None:
                missing.append(site)
            else:
                found.append(entry)
    found.sort(key=lambda entry: entry["id"])
    return {
        "schema": 1,
        "source": BASE,
        "anchor_layer_suffix": ANCHOR_SUFFIX,
        "sites": found,
        "unavailable": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="output path (defaults to the bundled resource)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)

    destination = args.out or (Path(__file__).resolve().parents[1]
                               / "resources" / "radar_sites.json")
    catalogue = build(workers=max(1, args.workers))
    if not catalogue["sites"]:
        print("no sites answered; refusing to write an empty catalogue",
              file=sys.stderr)
        return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(catalogue, indent=1) + "\n",
                           encoding="utf-8")
    print("wrote %d sites to %s (%d candidates had no layer)"
          % (len(catalogue["sites"]), destination,
             len(catalogue["unavailable"])))
    kinds: dict[str, int] = {}
    for entry in catalogue["sites"]:
        for product in entry["products"]:
            kinds[product] = kinds.get(product, 0) + 1
    for product, count in sorted(kinds.items(), key=lambda item: -item[1]):
        print("   %-12s %d sites" % (product, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
