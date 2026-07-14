# SHARPpy Reimagined Usage Guide

This guide covers **how to use** SHARPpy Reimagined once it is installed. For setting up
the environment and dependencies, see the "Installation" section of the
[README](../README.md) first — installation and usage are intentionally kept
separate.

---

## Two ways to drive it

- **Interactive desktop GUI** (`sharpmod-gui`) — point-and-click: pick a station
  on a map or from a list, or open a local file, and explore/edit the sounding
  live. Start here if you just want to look at soundings. See
  [section 0](#0-desktop-gui-sharpmod-gui).
- **Command-line tools** (`uwyo-sounding`, `era5-extract`, `model-extract`, `wrf-extract`,
  `sharpmod-render`) — scriptable, headless, reproducible. Use these for batch
  extraction and PNG rendering (sections 1–5).

Both share the same portable `.npz` point-sounding format, so anything the CLI
extracts opens in the GUI, and anything you save from the GUI renders on the CLI.

## Mental model

The command-line side has two kinds of capabilities:

1. **Get a sounding** — either *fetch* an observed one (University of Wyoming)
   or *extract* a model/reanalysis point column (forecast models, ERA5,
   WRF-ARW). Each of these
   writes a portable `.npz` point-sounding file.
2. **Render a sounding** — turn any supported sounding file into an SPC-style
   skew-T / hodograph PNG.

```
                 ┌── uwyo-sounding fetch ──┐
observed / model │   era5-extract          │──►  <name>.npz  ──►  sharpmod-render  ──►  <name>.png
   data          │   model-extract         │        (portable point sounding)      (skew-T / hodograph)
                 │   wrf-extract           │
                 └─────────────────────────┘
```

The `.npz` files are all the same format, so anything you extract renders the
same way (and the same way as the bundled HRRR examples).

### Which capability needs what

| You want to… | Needs the SHARPpy render stack? | Needs an extra install? |
|---|---|---|
| List / search / fetch UWyo soundings | No | No |
| Extract an ERA5 point sounding | No | `pip install -e ".[era5]"` |
| Fetch a forecast-model point sounding | No | `pip install -e ".[era5]"` |
| Extract a WRF-ARW point sounding | No | `pip install -e ".[wrf]"` |
| Render any sounding to PNG (`--render`) | **Yes** (`pip install --no-deps SHARPpy==1.4.0a5`) | No |

> Data extraction never requires the render stack. Only rendering does.

---

## 0. Desktop GUI (`sharpmod-gui`)

The interactive app is the fastest way to look at a sounding — no CLI arguments,
no `.npz` bookkeeping. It needs a display (unlike the headless renderer) and the
SHARPpy render stack (see README → Rendering).

```bash
sharpmod-gui             # or: python -m sharpmod.gui
```

### Pick a sounding

The app opens on the **Sounding Picker** with four tabs:

- **Station Map** — a clickable map of UWyo radiosonde stations over a
  coastline basemap. Click a dot to select it, double-click to open it. Scroll
  to zoom, drag to pan, and jump to a region with the *Map area* menu. Set the
  valid time (defaults to the most recent synoptic hour) and open the selection.
  The time menu offers every three-hourly UTC slot from 00Z through 21Z for
  regular and special/asynoptic observations.
- **Station List** — the station catalogue with live id/name filtering; type to
  narrow, pick a station and any of the same three-hourly times, then fetch.
- **Forecast Model** — choose a supported public model, run, forecast hour, and
  map point. The picker checks the selected inventory in the background and,
  when publication is delayed, offers an explicit **Use available cycle**
  button for the newest earlier run. It never changes the run silently, and an
  unknown or failed check does not block manual Fetch. Every published pressure
  level is fetched. The isolated GRIB and point-sounding data remain available
  while the sounding window is open, then are deleted when that window closes.
- **Open File** — load a local `.npz`, SPC (`.spc`/`.OAX`), BUFKIT (`.buf`),
  PECAN, or WRF-ARW text sounding. You can also **drag a file onto the window**.

The station set shown on the map and in the list is refreshed from UWyo for the
**selected observation time** (via the `/wsgi/sounding_json` endpoint), so
stations that were relocated — and had their WMO index change over time — show
up for the period they actually reported. The bundled offline catalogue is used
as a fallback until the live list arrives (or if the network is unavailable).

Fetches run on a background thread, so the window stays responsive while a UWyo
sounding downloads.

By default, each newly fetched or opened sounding is added to the active
sounding window instead of opening another window. Use the sounding window's
**Profiles** menu to focus or remove any loaded profile. Press **C** (*Collect
Observed*) when you want compatible observed soundings displayed together for
comparison. To return to one-window-per-sounding behavior, clear **File → Add
New Soundings to Active Window** in the picker; the choice is remembered.

### Debug a stuck GUI

The GUI writes a small rotating diagnostic log even when launched as the
windowed executable. Use **Help → Open Debug Log Folder**, reproduce the
problem once, then share `sharpmod-gui.log`. On Windows the default location is
`%LOCALAPPDATA%\SHARPpy Reimagined\Logs\sharpmod-gui.log`.

For more detail during a source run, enable debug logging before launch:

```powershell
$env:SHARPMOD_GUI_DEBUG = "1"
python -m sharpmod.gui
```

Set `SHARPMOD_GUI_LOG_DIR` if the log needs to be written to another folder.

### Explore and edit a sounding

Each sounding opens in the full interactive SPC window (the upstream SHARPpy
widget stack), so every gesture from the
[SHARPpy GUI guide](https://sharppy.github.io/SHARPpy/interacting_gui.html)
works — right-click the skew-T for the readout cursor / *Modify Surface* /
parcel lifting, or **Edit Nearest Level…**. The numeric level editor changes
pressure, height, temperature, dewpoint, wind direction, and wind speed at the
level nearest the right-click. It preserves vertical ordering, rejects dewpoint
above temperature, and recalculates all parcel levels and indices. You can also
click-and-drag temperature, dewpoint, or wind points for quicker edits. Mouse-
wheel zooms, and double-clicking the lower-left inset swaps lifted parcels.
**File → Preferences** switches the color palette (Standard / Inverted /
Protanopia), units, and the parcel visualized by default when a Skew-T opens.
The `W` key returns to the picker. A tip bar along the bottom summarizes the
current controls.

Use `Ctrl+Z` / **Edit → Undo** to reverse profile-level, interpolation, and
storm-motion changes, and `Ctrl+Y` / **Edit → Redo** to reapply them. The
history is local to the viewer, retains the latest 50 edits, and clears its redo
branch after a new edit.

Accepted preferences persist across launches: temperature, wind, and PWAT
units; palette; top/bottom readout variables; and the default Skew-T parcel.
The same settings file also retains multi-sounding behavior, dismissed tips,
recent files, and last selections. On Windows it is
`%APPDATA%\SHARPpy Reimagined\settings.ini`; set `SHARPMOD_SETTINGS_PATH` to
override that location.

The parcel table and Skew-T labels include the **maximum parcel level (MPL)**
alongside LCL, LFC, and EL. MPL is derived from the edited profile; it is not a
directly editable observation.

### Save and reopen an analysis session

Choose **File → Save Analysis Session…** (`Ctrl+Shift+E`) in a sounding window
to preserve all loaded soundings in that viewer, the active sounding/time/
member, current profile and interpolation state, storm motion, parcel
selection, and supported viewer state. Choose **Open Analysis Session…**
(`Ctrl+Shift+O`) from the picker or a sounding window to restore everything in
one multi-sounding viewer, independent of the normal combine-soundings setting.

The `.sharpmod-session` file is versioned JSON, not pickle, and is validated
before a viewer is created. It contains decoded profile state only—never source
GRIB downloads—so the existing delete-on-viewer-close cleanup remains intact.

### Save from the GUI

The sounding window's **Export** menu writes the current view:

- **Export Image (HD PNG)** (`Ctrl+E`) — a 2x high-density image of the whole
  window including the mounted derived-parameter panels, defaulting to
  `STATION_YYYYMMDDHHZ_hd.png` on your Desktop.
- **Export Image (UHD PNG)** — a larger 2.8x ultra-high-density image,
  defaulting to `STATION_YYYYMMDDHHZ_uhd.png`.
- **Export Image (Lossless PNG)** — the original-size compact/lossless image,
  defaulting to `STATION_YYYYMMDDHHZ_lossless.png`.
- **Export Text (SHARPpy)** — the focused profile as a text file that loads
  straight back into the app (or into `sharpmod-render`).

### Standalone build (no Python required)

For a distributable Windows build, use the bundled PyInstaller spec:

```bash
python -m pip install pyinstaller
pyinstaller packaging/sharpmod_gui.spec --noconfirm
```

The result is `dist/SHARPpy-Reimagined/SHARPpy-Reimagined.exe`. See the README
for the one-file variant.

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
are typically launched at **00Z** and **12Z** (some sites also 06Z/18Z), while
the GUI additionally offers 03Z/09Z/15Z/21Z for special launches.

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

Requires the `[era5]` extra (`cdsapi`, `cfgrib`, `xarray`), a free Copernicus
Climate Data Store account, and network access. Accept the ERA5 pressure-level
dataset licence and copy the credentials from
<https://cds.climate.copernicus.eu/how-to-api> into `$HOME/.cdsapirc`.

```bash
# era5-extract "<UTC time>" LAT LON [out.npz] [--loc LABEL] [--render [PNG]]

era5-extract "2024-05-20 00:00" 35.18 -97.44 oun_era5.npz
era5-extract "2024-05-20 00:00" 35.18 -97.44 oun_era5.npz --render
```

It selects the nearest ERA5 grid point (great-circle) and the nearest analysis
time, extracts the vertical column, and writes the `.npz` plus a `.json`
metadata sidecar recording the requested vs. selected coordinates/time.
Retrieval uses the official `reanalysis-era5-pressure-levels` CDS dataset and
requests only the nearest 0.25-degree point, six sounding variables, and all 37
pressure levels. It does not depend on a Herbie `era5` model plugin.

### Python API

```python
from datetime import datetime
from sharpmod.tools import era5_extract

era5_extract.extract(lat=35.18, lon=-97.44,
                     valid_time=datetime(2024, 5, 20, 0),
                     out_path="oun_era5.npz")
```

---

## 3. Public forecast-model point soundings (`model-extract`)

Requires the `[era5]` extra (`herbie-data`, `cfgrib`, `xarray`, `numcodecs`,
`pyproj`) and network access. Use `model-extract --list` to see all supported
models and their forecast ranges.

```bash
# model-extract MODEL LAT LON [out.npz] [--run TIME] [--fxx HOUR] [--render [PNG]]

model-extract gfs 35.18 -97.44 --run "2024-05-20 00:00" --fxx 6
model-extract hrrr 35.18 -97.44 --run "2024-05-20 00:00" --fxx 18 --render hrrr.png
```

The extractor requests every pressure level published for the chosen model,
not only the standard mandatory levels. Without `--render`, it keeps the
portable `.npz` and `.json` sidecar. With `--render`, the PNG is the served
artifact: the downloaded GRIB subset and transient `.npz`/`.json` are removed
after rendering, including failure cleanup.

Retrieval automatically uses the smallest compatible source: the public HRRR
Zarr point archive for F000 analyses, a small NOAA NOMADS geographic subset for
large supported NCEP transfers, or validated/coalesced byte ranges from a
healthy Herbie provider. Indexed subsets at or below 32 MiB prefer ranges so
they do not pay the CGI preparation cost. If an optimized route is missing or
incompatible, the normal Herbie downloader is used. These choices reduce
transfer size without reducing the published pressure-level set.

The GUI retains its downloaded model cache for reuse (3 GB / 48 hours by
default), exposes **Clear Downloaded Model Cache** and an opt-in **Prefetch Next
Forecast Hour** action in the File menu, and provides a Cancel button on the
model tab. Set `SHARPMOD_MODEL_CACHE`, `SHARPMOD_MODEL_CACHE_GB`, or
`SHARPMOD_MODEL_CACHE_HOURS` to change retention. Set
`SHARPMOD_POINT_BACKENDS=grib` or `SHARPMOD_HRRR_BACKEND=grib` to bypass the
point routes while troubleshooting.

---

## 4. WRF-ARW model output (`wrf-extract`)

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

## 5. Rendering soundings (`sharpmod-render`)

Requires the SHARPpy render stack (see README). Renders headlessly — no display
is needed.

```bash
# sharpmod-render <input> [output.png]

sharpmod-render oun.npz oun.png
sharpmod-render oun.npz oun_ml.png --parcel ML
sharpmod-render oun.npz oun_uhd.png --uhd
sharpmod-render oun.npz oun_lossless.png --lossless
sharpmod-render examples/soundings/14061619.OAX oax.png
sharpmod-render examples/soundings/hrrr_kbvo_20260625_06z.buf kbvo.png
```

Supported inputs: the `.npz` point sounding (UWyo/ERA5/WRF/HRRR), SPC tabular
(`.spc` / `.OAX`), BUFKIT (`.buf`), PECAN, and WRF-ARW text soundings.
CLI rendering defaults to a 2x HD PNG; pass `--uhd` or `--image-mode uhd` for
the larger 2.8x export, or `--lossless` / `--image-mode lossless` for the
original-size compact/lossless PNG.

Choose the parcel visualized on the Skew-T with `--parcel SFC`, `--parcel ML`,
`--parcel FCST`, `--parcel MU`, `--parcel EFF`, or `--parcel USER`. Parcel keys
are case-insensitive and default to `MU`, matching the GUI's original behavior.

### Python API

```python
from sharpmod.render import render
render("oun.npz", "oun.png")
render("oun.npz", "oun_sfc.png", parcel="SFC")

# Or the thin helper used by the extractor CLIs:
from sharpmod.tools import render_npz
render_npz("oun.npz")                 # -> oun.png (PNG next to the .npz)
```

### Useful environment variables

| Variable | Default | Effect |
|---|---|---|
| `QT_QPA_PLATFORM` | `offscreen` | Qt platform; leave as `offscreen` for headless PNG output |
| `CHART_FONT` | `Space Grotesk` | Chart font family (empty string uses SHARPpy's default) |
| `SHARPMOD_HD_SCALE` | `2.0` | Pixel scale for HD PNG exports |
| `SHARPMOD_UHD_SCALE` | `2.8` | Pixel scale for UHD PNG exports |

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
- **`era5-extract` reports missing CDS credentials** — create a free CDS
  account, accept the ERA5 pressure-level dataset licence, then copy the API
  profile into `$HOME/.cdsapirc` from
  <https://cds.climate.copernicus.eu/how-to-api>.
- **A rendered PNG looks empty / a widget overflows** — extremely degenerate
  input (e.g. constant winds at every level) can overflow the storm-relative
  hodograph; use real data.
