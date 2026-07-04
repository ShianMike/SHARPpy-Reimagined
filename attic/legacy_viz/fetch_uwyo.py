"""Fetch a University of Wyoming observed sounding (NEW /wsgi/ server) and save
it as a .npz point-sounding sidecar the sharpmod renderer can load.

The legacy cgi-bin interface was retired in 2025; the new endpoint is
``/wsgi/sounding?datetime=YYYY-MM-DD HH:00:00&id=<stnm>&src=<src>&type=TEXT:LIST``.
In the new TEXT:LIST table the wind SPEED column is in m/s (the old server used
knots), so it is converted to knots here.

Usage:
    python fetch_uwyo.py STNM "YYYY-MM-DD HH" LAT LON out.npz [loc] [src]

Run under the grib env (has certifi + numpy):
    .gribenv\\Scripts\\python.exe fetch_uwyo.py ...
"""
import sys
import ssl
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np

try:
    import certifi
    _CAFILE = certifi.where()
except Exception:
    _CAFILE = None

BASE = "https://weather.uwyo.edu/wsgi/sounding"
MISSING = -9999.0
MS_TO_KT = 1.9438444924406046


def fetch_text(stnm, when, src):
    params = {
        "datetime": when.strftime("%Y-%m-%d %H:00:00"),
        "id": str(stnm),
        "src": src,
        "type": "TEXT:LIST",
    }
    url = BASE + "?" + urlencode(params)
    print("GET", url)
    ctx = ssl.create_default_context(cafile=_CAFILE)
    with urlopen(url, timeout=30, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_pre(text):
    """Parse the fixed-width <PRE> sounding table of the new UWyo server.

    Columns (7-char fields): 0=PRES 1=HGHT 2=TEMP 3=DWPT 6=DRCT 7=SPED(m/s).
    Returns dict of numpy arrays (pres,hght,tmpc,dwpc,wdir,wspd) with wspd in kt.
    """
    lines = text.split("\n")
    bgn = next((i for i, l in enumerate(lines) if l.strip() == "<PRE>"), -1)
    if bgn == -1:
        raise SystemExit("no <PRE> data block in response")
    end = next((i for i in range(bgn + 1, len(lines))
                if lines[i].strip().startswith("</PRE>")), -1)
    if end == -1:
        raise SystemExit("no closing </PRE> in response")

    field_cols = (0, 1, 2, 3, 6, 7)
    names = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
    cols = [[] for _ in names]
    # skip the 5 header lines after <PRE>
    for i in range(bgn + 6, end):
        row = lines[i]
        if row.strip() == "":
            continue
        for k, j in enumerate(field_cols):
            val = row[7 * j:7 * (j + 1)].strip()
            cols[k].append(float(val) if val != "" else MISSING)
    out = {n: np.asarray(c, dtype=float) for n, c in zip(names, cols)}
    if out["pres"].size == 0:
        raise SystemExit("no reported levels parsed")
    # SPED is m/s on the new server -> convert to knots (leave missing as-is).
    ws = out["wspd"]
    out["wspd"] = np.where(ws == MISSING, MISSING, ws * MS_TO_KT)
    return out


def main():
    stnm = sys.argv[1]
    when = datetime.strptime(sys.argv[2], "%Y-%m-%d %H")
    lat = float(sys.argv[3])
    lon = float(sys.argv[4])
    out = sys.argv[5]
    loc = sys.argv[6] if len(sys.argv) > 6 else str(stnm)
    src = sys.argv[7] if len(sys.argv) > 7 else "FM35"

    arr = parse_pre(fetch_text(stnm, when, src))
    n = arr["pres"].size
    omeg = np.full(n, MISSING)

    np.savez(
        out,
        pres=arr["pres"], hght=arr["hght"], tmpc=arr["tmpc"],
        dwpc=arr["dwpc"], wdir=arr["wdir"], wspd=arr["wspd"], omeg=omeg,
        lat=lat, lon=lon, loc=loc, model="Observed",
        run=when.strftime("%Y-%m-%d %H:%M"),
        valid=when.strftime("%Y-%m-%d %H:%M"), fxx=0,
    )
    print(f"wrote {out}  ({n} levels, sfc p={arr['pres'][0]:.1f} hPa, "
          f"valid {when}, {lat}N {lon}E)")


if __name__ == "__main__":
    main()
