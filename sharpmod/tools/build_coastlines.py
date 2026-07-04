"""Build the bundled coastline resource for the GUI station map.

Downloads the public-domain Natural Earth 1:110m coastline (a small, low-detail
vector suitable for a picker background) and writes a *compact* JSON resource
``sharpmod/resources/coastlines.json`` consumed by
:class:`sharpmod.gui.StationMapWidget`.

The output schema is intentionally tiny -- a list of polylines, each a list of
``[lon, lat]`` pairs rounded to two decimals::

    {"polylines": [[[lon, lat], [lon, lat], ...], ...]}

Run offline once (or when refreshing the basemap)::

    python -m sharpmod.tools.build_coastlines

Natural Earth is public domain; no attribution is required, though credit to
https://www.naturalearthdata.com is appreciated.
"""

from __future__ import annotations

import json
import os
import ssl
from urllib.request import urlopen

import certifi

# 1:110m coastline (world), GeoJSON, from the Natural Earth vector mirror.
SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_110m_coastline.geojson"
)

_RESOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "resources", "coastlines.json")


def _iter_linestrings(geometry):
    """Yield each LineString coordinate list from a GeoJSON geometry."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        yield from coords


def build(url: str = SOURCE_URL, out_path: str = _RESOURCE) -> str:
    """Download + compact the coastline GeoJSON into the bundled resource."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    print(f"Downloading {url} ...")
    with urlopen(url, timeout=60, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    polylines = []
    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        for line in _iter_linestrings(geom):
            pts = [[round(float(x), 2), round(float(y), 2)] for x, y in line]
            if len(pts) >= 2:
                polylines.append(pts)

    payload = {"polylines": polylines}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    n_pts = sum(len(p) for p in polylines)
    size_kb = os.path.getsize(out_path) / 1024.0
    print(f"Wrote {out_path}: {len(polylines)} polylines, {n_pts} points, "
          f"{size_kb:.0f} KB")
    return out_path


if __name__ == "__main__":
    build()
