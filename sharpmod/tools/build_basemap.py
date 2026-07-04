"""Build the bundled HD basemap resource for the GUI station map.

Downloads the public-domain Natural Earth 1:50m vector layers -- coastline,
country boundary lines, and state/province boundary lines -- and writes a
compact multi-layer JSON resource ``sharpmod/resources/basemap.json`` consumed
by :class:`sharpmod.gui.StationMapWidget`.

The output schema is a dict of layer-name -> list of polylines, each polyline a
list of ``[lon, lat]`` pairs rounded to two decimals (~1 km, plenty for a
picker) to keep the file small::

    {"coastline": [[[lon, lat], ...], ...],
     "countries": [...],
     "states":    [...]}

Run offline once (or when refreshing the basemap)::

    python -m sharpmod.tools.build_basemap

Natural Earth is public domain (credit: https://www.naturalearthdata.com).
"""

from __future__ import annotations

import json
import os
import ssl
from urllib.request import urlopen

import certifi

_BASE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
         "master/geojson/")

# layer name -> Natural Earth 1:50m GeoJSON file.
LAYERS = {
    "coastline": "ne_50m_coastline.geojson",
    "countries": "ne_50m_admin_0_boundary_lines_land.geojson",
    "states": "ne_50m_admin_1_states_provinces_lines.geojson",
}

_RESOURCE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "resources", "basemap.json")


def _iter_linestrings(geometry):
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        yield from coords


def _fetch_layer(url: str, ctx) -> list:
    print(f"Downloading {url} ...")
    with urlopen(url, timeout=90, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    polylines = []
    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        for line in _iter_linestrings(geom):
            pts = [[round(float(x), 2), round(float(y), 2)] for x, y in line]
            # Drop consecutive duplicate points created by rounding.
            dedup = [pts[0]] if pts else []
            for p in pts[1:]:
                if p != dedup[-1]:
                    dedup.append(p)
            if len(dedup) >= 2:
                polylines.append(dedup)
    return polylines


def build(out_path: str = _RESOURCE) -> str:
    ctx = ssl.create_default_context(cafile=certifi.where())
    payload = {}
    for name, fname in LAYERS.items():
        try:
            payload[name] = _fetch_layer(_BASE + fname, ctx)
        except Exception as exc:  # keep whatever layers succeeded
            print(f"  ! {name} failed: {exc}")
            payload[name] = []

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    total = sum(len(v) for v in payload.values())
    size_kb = os.path.getsize(out_path) / 1024.0
    print(f"Wrote {out_path}: "
          + ", ".join(f"{k}={len(v)}" for k, v in payload.items())
          + f" ({total} polylines, {size_kb:.0f} KB)")
    return out_path


if __name__ == "__main__":
    build()
