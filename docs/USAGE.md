# SHARPpy Reimagined Usage Guide

This guide covers **how to use** SHARPpy Reimagined once it is installed. For setting up
the environment and dependencies, see the "Installation" section of the
[README](../README.md) first — installation and usage are intentionally kept
separate.

---

## Mental model

SHARPpy Reimagined has two kinds of capabilities:

1. **Get a sounding** — either *fetch* an observed one (University of Wyoming)
   or *extract* a model/reanalysis point column (ERA5, WRF-ARW). Each of these
   writes a portable `.npz` point-sounding file.
2. **Render a sounding** — turn any supported sounding file into an SPC-style
   skew-T / hodograph PNG.

```
                 ┌── uwyo-sounding fetch ──┐
observed / model │   era5-extract          │──►  <name>.npz  ──►  sharpmod-render  ──►  <name>.png
   data          │   wrf-extract           │        (portable point sounding)      (skew-T / hodograph)
                 └─────────────────────────┘
```

The `.npz` files are all the same format, so anything you extract renders the
same way (and the same way as the bundled HRRR examples).

### Which capability needs what

| You want to… | Needs the SHARPpy render stack? | Needs an extra install? |
|---|---|---|
| List / search / fetch UWyo soundings | No | No |
| Extract an ERA5 point sounding | No | `pip install -e ".[era5]"` |
| Extract a WRF-ARW point sounding | No | `pip install -e ".[wrf]"` |
| Render any sounding to PNG (`--render`) | **Yes** (`pip install --no-deps SHARPpy==1.4.0a5`) | No |

> Data extraction never requires the render stack. Only rendering does.

---

## 1. University of Wyoming soundings (`uwyo-sounding`)

Every fixed UWyo upper-air station is bundled offline (933 stations), so you can
browse without network access. Fetching a sounding does require network.

### List / search stations

```bash
# List every station, or filter by id/name substring
uwyo-sounding list
uwyo-sounding list --grep norman
uwyo-sounding list --grep ", Japan"

# Resolve a specific query (exact id returns just that station)
uwyo-sounding search 72357
uwyo-sounding search "Dodge City" --limit 5
```

Output columns are `ID  NAME  LAT  LON  SRC` (SRC is the UWyo data source, e.g.
`FM35` / `BUFR`).

### Fetch an observed sounding

```bash
# uwyo-sounding fetch <station> <UTC time> [--out FILE.npz] [--loc LABEL] [--render [PNG]]

# Station id + time -> writes uwyo_72357_2024052000.npz
uwyo-sounding fetch 72357 "2024-05-20 00"

# You can pass a name query instead of an id
uwyo-sounding fetch "Norman" "2024-05-20 00" --out oun.npz

# Fetch AND open it in the app (render to PNG) in one step
uwyo-sounding fetch 72357 "2024-05-20 00" --out oun.npz --render oun.png
uwyo-sounding fetch 72357 "2024-05-20 00" --render        # PNG next to the .npz
```

Time accepts `YYYY-MM-DD HH` (UTC), `YYYY-MM-DD HH:MM`, or ISO-8601. Radiosondes
are typically launched at **00Z** and **12Z** (some sites also 06Z/18Z).

### Python API

```python
from datetime import datetime
from sharpmod.io.uwyo_decoder import UWyo_Decoder

dec = UWyo_Decoder(full_catalog=True)      # resolve against all 933 stations
meta = dec.resolve_station("Norman")        # -> StationMeta(id='72357', ...)
UWyo_Decoder.search_stations("denver")      # -> [{'id','name','lat','lon','src'}, ...]

prof = dec.fetch("72357", datetime(2024, 5, 20, 0))   # -> Profile
print(prof.pres[0], prof.tmpc[0], prof.wspd[1])       # wind speed already in knots
```

### Rebuilding the station catalogue (rarely needed)

The bundled catalogue lives at `sharpmod/resources/uwyo_stations.json`. To
refresh it from the live UWyo server:

```bash
python -m sharpmod.tools.build_uwyo_catalog --years 2024 2015
```

---

## 2. ERA5 reanalysis point soundings (`era5-extract`)

Requires the `[era5]` extra (`herbie-data`, `cfgrib`, `xarray`) and network
access to the ERA5 archive.

```bash
# era5-extract "<UTC time>" LAT LON [out.npz] [--loc LABEL] [--render [PNG]]

era5-extract "2024-05-20 00:00" 35.18 -97.44 oun_era5.npz
era5-extract "2024-05-20 00:00" 35.18 -97.44 oun_era5.npz --render
```

It selects the nearest ERA5 grid point (great-circle) and the nearest analysis
time, extracts the vertical column, and writes the `.npz` plus a `.json`
metadata sidecar recording the requested vs. selected coordinates/time.

### Python API

```python
from datetime import datetime
from sharpmod.tools import era5_extract

era5_extract.extract(lat=35.18, lon=-97.44,
                     valid_time=datetime(2024, 5, 20, 0),
                     out_path="oun_era5.npz")
```

---

## 3. WRF-ARW model output (`wrf-extract`)

Requires the `[wrf]` extra (`xarray`, `netCDF4`). Reads a raw `wrfout*` NetCDF
file, selects the nearest grid point, destaggers the vertical and wind grids,
rotates winds to earth-relative (`COSALPHA`/`SINALPHA`), and writes the `.npz`.

```bash
# wrf-extract WRFOUT LAT LON [out.npz] [--time "<UTC>"] [--loc LABEL] [--render [PNG]]

wrf-extract wrfout_d01_2024-05-20_00:00:00 35.18 -97.44 wrf_oun.npz
wrf-extract wrfout_d01_2024-05-20_00:00:00 35.18 -97.44 wrf_oun.npz \
    --time "2024-05-20 00:00" --render
```

If the file holds several times, `--time` picks the nearest; omit it to use the
first time in the file.

### Python API

```python
from sharpmod.tools import wrf_extract

wrf_extract.extract("wrfout_d01_2024-05-20_00:00:00",
                    lat=35.18, lon=-97.44,
                    out_path="wrf_oun.npz",
                    valid_time="2024-05-20 00:00")
```

---

## 4. Rendering soundings (`sharpmod-render`)

Requires the SHARPpy render stack (see README). Renders headlessly — no display
is needed.

```bash
# sharpmod-render <input> [output.png]

sharpmod-render oun.npz oun.png
sharpmod-render examples/soundings/14061619.OAX oax.png
sharpmod-render examples/soundings/hrrr_kbvo_20260625_06z.buf kbvo.png
```

Supported inputs: the `.npz` point sounding (UWyo/ERA5/WRF/HRRR), SPC tabular
(`.spc` / `.OAX`), BUFKIT (`.buf`), PECAN, and WRF-ARW text soundings.

### Python API

```python
from sharpmod.render import render
render("oun.npz", "oun.png")

# Or the thin helper used by the extractor CLIs:
from sharpmod.tools import render_npz
render_npz("oun.npz")                 # -> oun.png (PNG next to the .npz)
```

### Useful environment variables

| Variable | Default | Effect |
|---|---|---|
| `QT_QPA_PLATFORM` | `offscreen` | Qt platform; leave as `offscreen` for headless PNG output |
| `CHART_FONT` | `Space Grotesk` | Chart font family (empty string uses SHARPpy's default) |

```bash
# Example: force headless explicitly (the renderer already defaults to it)
QT_QPA_PLATFORM=offscreen sharpmod-render oun.npz oun.png
```

On Windows PowerShell, set env vars with `$env:QT_QPA_PLATFORM = "offscreen"`
before the command.

---

## End-to-end recipes

**Observed sounding for Norman, OK at 00Z and open it:**
```bash
uwyo-sounding fetch 72357 "2024-05-20 00" --out oun.npz --render oun.png
```

**Find a station by name, then fetch + render:**
```bash
uwyo-sounding search "Dodge City"          # note the id (72451)
uwyo-sounding fetch 72451 "2024-05-20 12" --render
```

**Reanalysis sounding at an arbitrary point:**
```bash
era5-extract "2024-05-20 00:00" 39.77 -104.87 dnr_era5.npz --render
```

**Model sounding from your own WRF run:**
```bash
wrf-extract wrfout_d02_2024-05-20_00:00:00 41.32 -96.37 oax_wrf.npz --render
```

---

## Troubleshooting

- **`sharpmod-render` errors about `sharppy` / `sutils` / a Qt enum** — the
  render stack isn't installed (or not Qt6-compatible). Run
  `pip install --no-deps "SHARPpy==1.4.0a5"` (see README → Rendering).
- **`uwyo-sounding fetch` says the station/time is unavailable** — that site
  didn't report at that hour; try 00Z or 12Z, or a nearby date.
- **`era5-extract` / `wrf-extract` import errors** — install the matching extra:
  `pip install -e ".[era5]"` or `pip install -e ".[wrf]"`.
- **A rendered PNG looks empty / a widget overflows** — extremely degenerate
  input (e.g. constant winds at every level) can overflow the storm-relative
  hodograph; use real data.
