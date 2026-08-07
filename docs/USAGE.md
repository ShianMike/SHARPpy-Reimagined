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
- **Command-line tools** (`observed-sounding`, `uwyo-sounding`, `era5-extract`,
  `model-extract`, `model-batch-extract`, `wrf-extract`, `sharpmod-render`) —
  scriptable, headless, reproducible. Use these for batch extraction and PNG
  rendering (sections 1–5).

Both share the same portable `.npz` point-sounding format, so anything the CLI
extracts opens in the GUI, and anything you save from the GUI renders on the CLI.

## Mental model

The command-line side has two kinds of capabilities:

1. **Get a sounding** — either *fetch* an observed one (UWyo or IEM RAOB)
   or *extract* a model/reanalysis point column (forecast models, ERA5,
   WRF-ARW). Each of these
   writes a portable `.npz` point-sounding file.
2. **Render a sounding** — turn any supported sounding file into an SPC-style
   skew-T / hodograph PNG.

```
                 ┌── observed-sounding fetch ─┐
observed / model │   era5-extract             │──►  <name>.npz  ──►  sharpmod-render  ──►  <name>.png
   data          │   model-extract / batch    │        (portable point sounding)      (skew-T / hodograph)
                 │   wrf-extract              │
                 └────────────────────────────┘
```

The `.npz` files are all the same format, so anything you extract renders the
same way (and the same way as the bundled HRRR examples).

### Which capability needs what

| You want to… | Needs the SHARPpy render stack? | Needs an extra install? |
|---|---|---|
| List / search / fetch UWyo soundings | No | No |
| Fetch with UWyo → IEM RAOB fallback | No | No |
| Extract an ERA5 point sounding | No | `pip install -e ".[era5]"` |
| Fetch a forecast-model point sounding | No | `pip install -e ".[era5]"` |
| Run a resumable model batch | No | `pip install -e ".[era5]"` |
| Extract a WRF-ARW point sounding | No | `pip install -e ".[wrf]"` |
| Render any sounding to PNG (`--render`) | **Yes** (`python scripts/install_sharppy_compat.py`) | No |

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

The app opens on the **Sounding Picker** with five tabs:

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
  Enter an optional **Location/town** label, or choose a named saved location,
  to display that name above the sounding's hodograph locator-map inset. If
  left blank, Fetch resolves and caches the town in its background worker.
  **Timeline…** queues an inclusive range of up to 72 published hours into one
  sounding viewer. Its slider, previous/next, play, and loop controls update as
  results arrive; already completed hours survive cancellation and unavailable
  hours are reported explicitly.
- **Reanalysis (ERA5)** — choose a global map point and hourly UTC analysis.
  The tab shows the requested and snapped 0.25-degree point, checks the local
  CDS setup, runs retrieval outside the Qt event loop, and reuses a completed
  cached point/hour. Cancel suppresses display and cleans the local output;
  the stable synchronous `cdsapi` call itself may need to return first.
  Keep **Add to active sounding window** enabled and fetch another point/hour
  to overlay several ERA5 profiles in the same analysis window.
- **Open File** — load a local `.npz`, SPC (`.spc`/`.OAX`), BUFKIT (`.buf`),
  PECAN, or WRF-ARW text sounding. The nested **Raw WRF wrfout** workflow
  inspects domain coordinates and available times in a worker, provides a
  domain-aware point map, rejects points outside the real curvilinear grid
  perimeter, and extracts without blocking Qt. You can also **drag a file onto
  the window**; raw `wrfout*`/NetCDF files route to this workflow. Its matching
  **Add to active sounding window** control lets repeated available-time or
  point selections build one multi-sounding WRF analysis.

The station set shown on the map and in the list is refreshed from UWyo for the
**selected observation time** (via the `/wsgi/sounding_json` endpoint), so
stations that were relocated — and had their WMO index change over time — show
up for the period they actually reported. The bundled offline catalogue is used
as a fallback until the live list arrives (or if the network is unavailable).

Observed fetches run on a background thread and try UWyo first, followed by the
independent IEM RAOB archive when UWyo fails. The successful provider is shown
in the sounding metadata and viewer title.

Use **File → Downloaded Data Library…** to inspect cached model entries, reopen
or re-extract them offline, pin them against automatic cleanup, delete them, or
copy their source metadata. In **Locations**, manage searchable saved points,
reuse recent forecast/ERA5 points, and import/export the versioned JSON format;
saved and recent points appear as map markers.

In a sounding window, **Data → Source & Quality Inspector…** reports the
provider URL/transport, decoder/backend, cache reuse, pressure-level and missing
field counts, vorticity source, and read-only QC warnings for the focused
profile.

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
The hodograph defaults to centering the display on the LCL-to-EL mean-wind
vector instead of the zero-wind origin, with a viewport 20% tighter than the
previous 200-kt full-width view. Right-click it to choose Mean Wind, Normal, or
Storm Relative centering. It puts 0.5, 1, 3, 6, 9, and 12 inside colored dots on
the active profile; its locator-map inset displays the active location/town in
the title.
Blank forecast, ERA5, and WRF location fields first use a bundled U.S. Census
index with 31,540 incorporated/CDP places and 21,278 named towns/townships
across CONUS and D.C. State polygons reject nearby points in Canada, Mexico,
the Atlantic, and the Gulf. Only when the bundled lookup has no result can a
cached, rate-limited OpenStreetMap Nominatim fallback run; entering a label
skips lookup, and `SHARPMOD_GEOCODER_URL=off` disables the online fallback.
Headless rendering follows the same path for model/coordinate-only labels.
Town names are used only in the title and are never drawn inside the map. See
the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
and [OpenStreetMap attribution](https://www.openstreetmap.org/copyright).
The [offline CONUS index notes](CONUS_PLACE_INDEX.md) document its annual
refresh command and provenance. The map itself reads nearby geometry from
separately bundled Census county-outline tiles, with no live map request or
town-label layer.
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

### Redundant observed source (`observed-sounding`)

The provider-neutral command defaults to an explicit UWyo → Iowa
Environmental Mesonet (IEM) RAOB fallback. A result always comes wholly from
one provider: levels are never merged across archives. The `.npz` and JSON
sidecar retain the actual provider, provider station, exact request URL, and
any failed earlier attempt.

```bash
observed-sounding providers
observed-sounding fetch 72357 "2024-05-20 00" --out oun.npz

# Pin one source and disable fallback
observed-sounding fetch KOUN "2024-05-20 00" --provider iem --out oun_iem.npz
```

The IEM adapter uses Iowa State University's public RAOB JSON service and its
RAOB station catalogue. It accepts IEM station IDs, WMO numbers, or an
unambiguous station name. Programmatic callers can pass an explicit provider
order to `sharpmod.observations.fetch_observed(...)`.

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
and single-level dataset licences and copy the credentials from
<https://cds.climate.copernicus.eu/how-to-api> into `$HOME/.cdsapirc`.
The GUI exposes the same extractor on **Reanalysis (ERA5)** and never displays
credential values.

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
pressure levels. A colocated `reanalysis-era5-single-levels` request supplies
surface pressure/geopotential, 2-m temperature/dewpoint, and 10-m wind. The
extractor drops pressure levels below terrain, inserts that verified ground
row, and fails without writing when the merge or physical profile QC fails. It
does not depend on a Herbie `era5` model plugin.

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
model-extract gdps 45.50 -73.60 --run "2026-07-22 00:00" --fxx 6

# Provider-contract monitoring across recent completed cycles
model-extract aigfs --probe --lookback-cycles 8 --require-surface-contract
```

For a normal live HRRR extraction, regional TOI guidance is generated and
embedded automatically. The bounded workflow requests compact HRRR `sfc` field
subsets every three hours across the applicable 18-hour window — normally seven
frames (`0,3,6,9,12,15,18`), or eight when the requested forecast hour falls off
that interval — analyzes a 1400-km radius at roughly 12-km sampling, tracks the
300-hPa jet in June-August (500 hPa otherwise), and uses the nearest connected
fixed-layer STP proxy region at or above 0.5. It records the exact run,
requested and successful hours, frame count, time coverage, sampling interval,
source URLs, risk-mask method, proxy equation, and shear interpretation in
`regional_guidance.provenance`.

Each subset is about 8-11 MiB, so a cold-cache TOI fetch adds roughly 60-85 MiB
across the sampled window (measured against the official archive on
2026-08-05). The plan is capped at eight sequential requests, and the workflow is
failure-soft: a delayed field or a benign environment leaves TOI explicitly
`UNAVAILABLE` without failing the sounding, and partial sampling is marked
`degraded` rather than presented as complete. Use `--no-regional-guidance` to
omit the supplemental fetch.

The extractor requests every pressure level published for the chosen model,
not only the standard mandatory levels. Without `--render`, it keeps the
portable `.npz` and `.json` sidecar. With `--render`, the PNG is the served
artifact: the downloaded GRIB subset and transient `.npz`/`.json` are removed
after rendering, including failure cleanup.

The contract probe reports every present and missing ground component.
`--require-surface-contract` returns a failing exit status until all six
components are published, while `--lookback-cycles` avoids mistaking a
not-yet-published wall-clock cycle for provider schema drift.

Every forecast request must include surface pressure/height, 2-m
temperature/moisture, and both 10-m wind components. The extractor drops every
isobar whose pressure is greater than the selected point's surface pressure
and prepends this verified ground row. It refuses any profile when those
surface fields are missing, preventing provider below-terrain fill from
becoming SHARPpy's surface. Products whose current public inventory lacks this
complete contract report that limitation explicitly rather than producing a
plausible-looking unsafe sounding.

Retrieval automatically uses the smallest compatible source: the public HRRR
Zarr point archive for F000 analyses, a small NOAA NOMADS geographic subset for
large supported NCEP transfers, or validated/coalesced byte ranges from a
healthy Herbie provider. Indexed subsets at or below 32 MiB prefer ranges so
they do not pay the CGI preparation cost. If an optimized route is missing or
incompatible, the normal Herbie downloader is used. These choices reduce
transfer size without reducing the published pressure-level set.

All indexed Herbie models default to four bounded range workers. A coalesced
span is split into balanced fragments when it is large enough to benefit, then
reassembled in byte order under a pinned ETag or Last-Modified identity. A
server without a validator, a rejected parallel transfer, or a partial failure
downgrades to the validated sequential route. Set
`SHARPMOD_RANGE_WORKERS=1` for that compatibility path, or 2-8 to tune network
concurrency. This setting never adds decoder threads.

The direct decoder also covers products such as AIGFS, ECMWF-AIFS, and GEFS
that omit a pressure-level vorticity field. It reads the four surface U/V
neighbors needed by the existing finite-difference calculation directly from
two GRIB messages, with cfgrib retained only for unsupported grid layouts.
Multi-point batch groups vectorize both normal columns and wind stencils so a
selected message is unpacked once for every point. HRRR Zarr columns likewise
normalize directly into the compact point contract without constructing an
intermediate xarray dataset.

RRFS-A exposes separate `rrfs-a`, `rrfs-a-alaska`, `rrfs-a-hawaii`, and
`rrfs-a-puerto-rico` adapters. Its 00/06/12/18Z cycles advertise F000-F084;
off-hour cycles advertise F000-F018. RRFS uses the same four-worker validated
transport as the other indexed models; its retained multi-cycle benchmark is
documented in `benchmarks/results/2026-07-22-rrfs-range-workers.md`.

Canadian `gdps` and `rdps` use ECCC MSC GeoMet's point-value route. The adapter
fetches six surface layers first, skips isobaric layers at or below the point's
surface pressure, then fans out the remaining variable/pressure layers with
four bounded workers,
checks the exact model reference and valid times returned by every layer, and
normalizes the verified ground plus all above-terrain published levels into
the same portable sounding contract. Set
`SHARPMOD_GEOMET_WORKERS` from 1-8 to tune that network fan-out. GDPS supports
00/12Z through F240 every three hours; RDPS supports 00/06/12/18Z through F084
hourly.

The GUI retains its downloaded model cache for reuse (3 GB / 48 hours by
default), exposes **Clear Downloaded Model Cache** and an opt-in **Prefetch Next
Forecast Hour** action in the File menu, and provides a Cancel button on the
model tab. Set `SHARPMOD_MODEL_CACHE`, `SHARPMOD_MODEL_CACHE_GB`, or
`SHARPMOD_MODEL_CACHE_HOURS` to change retention. Set
`SHARPMOD_POINT_BACKENDS=grib` or `SHARPMOD_HRRR_BACKEND=grib` to bypass the
point routes while troubleshooting. The cache namespace is versioned with the
extraction contract; old entries are shown for inspection but are not reused.
Portable profiles are physically revalidated before cache reuse.

### Resumable batch API and CLI (`model-batch-extract`)

A version-1 JSON job can mix points, forecast hours, models, and members.
Requests sharing the same model/run/hour/member reuse one model-hour download;
local-GRIB point values within that lease are vector-read in one decoder pass,
while distinct hours use a bounded 1-4 worker pool.
Single-point hours retain the optimized point/subregion route and its
GUI-compatible spatial cache key. Multi-point hours retrieve one reusable
field subset and vector-decode all points in that hour.

```json
{
  "version": 1,
  "requests": [
    {"id": "oun-f000", "model": "gfs", "lat": 35.18, "lon": -97.44,
     "run": "2026-07-14T00:00:00Z", "fxx": 0, "output": "oun/f000.npz"},
    {"id": "ict-f000", "model": "gfs", "lat": 37.65, "lon": -97.43,
     "run": "2026-07-14T00:00:00Z", "fxx": 0, "output": "ict/f000.npz"},
    {"id": "oun-f006", "model": "gfs", "lat": 35.18, "lon": -97.44,
     "run": "2026-07-14T00:00:00Z", "fxx": 6, "output": "oun/f006.npz"}
  ]
}
```

```bash
model-batch-extract job.json --output-dir batch-output --workers 2
```

`batch-output/batch-manifest.json` is atomically updated and stores the schema
version, per-request state, cache reuse, checksums, sizes, and errors. A rerun
validates both the `.npz` and JSON checksums before skipping completed work;
failed, cancelled, interrupted, missing, or corrupt requests are retried. Use
`--no-resume` to force every request. Output paths in the job must be relative
to `--output-dir`.

Experimental HRRR regional TOI guidance is **off in batch by default**. It needs
seven extra regional frames per point, roughly 60-85 MiB and tens of seconds, and
measured AUC 0.462 on the 339-case archive, so an unattended job should not pay
for it silently. Interactive paths are unchanged — the GUI and single-point
`model-extract` still follow the `auto` policy, and `SHARPMOD_REGIONAL_GUIDANCE`
still overrides globally. Add `--regional-guidance` to opt a batch job in:

```bash
model-batch-extract job.json --output-dir batch-output --regional-guidance
```

Python callers can use `sharpmod.batch_extract.run_batch(...)` or retain a
`BatchExtractor` and call `cancel()`. `BatchRunResult.items` and
`output_paths` preserve input order, including heterogeneous forecast hours.
Pass `model_hour_cache=` to lease a caller-owned `ModelHourCache`; the batch
runner leaves an external cache alive for timeline/offline reuse.

---

## 4. WRF-ARW model output (`wrf-extract`)

Requires the `[wrf]` extra (`xarray`, `netCDF4`). Reads a raw `wrfout*` NetCDF
file, selects the nearest grid point, destaggers the vertical and wind grids,
rotates winds to earth-relative (`COSALPHA`/`SINALPHA`), and writes the `.npz`.
The GUI's **Open File → Raw WRF wrfout** workflow performs the same operation
after asynchronous domain/time inspection and inside-grid validation.

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

### Regional Tornado Outbreak Indicator

Tornado Outbreak Indicator (TOI) is a regional product, not a parameter that a
point profile can calculate. Its compact readout uses one previously unused
right-hand row of the composite-index block and follows the neighboring index
design: `TOI = 4.2 hypothetical` for the experimental 0-5 score, or `TOI = --` when
regional guidance is unavailable. Live HRRR extraction supplies an explicitly
experimental score from a versioned public-method reconstruction.

The `hypothetical` marker keeps the readout from reading as a settled index beside
validated ones. It renders in the same smaller font used for unit suffixes, so it
stays subordinate to the value and attached to it. This row clips rather than
elides and font substitution varies by platform, so the marker is drawn only when
it is measured to fit the column at the resolved face and is dropped whole
otherwise — a half-drawn qualifier would be worse than none. Experimental status
is also carried by the row tooltip, the accessible description, and the details
dialog, none of which depend on column width.

The marker fits only because it is a *registered* suffix. MEASURED inside the
render at Space Grotesk 13px: the TOI cell is 122px, the `TOI = ` label takes
34px and the value `4.2` another 19px, leaving a 67px suffix budget. Listed in
`_UNIT_SUFFIXES`, the marker draws at `UNIT_FONT_SCALE` (10px) where
` hypothetical` measures 65px and fits with 2px spare. An *unregistered* suffix is
measured at full size instead, which is why the spelled-out word first appeared to
be 20px too wide.

The marker is attached only to an unvalidated number. A validated calibration
earns its percentage, so calling that hypothetical would be wrong, and the same
rule keeps the widest string out of the cell: `68% hypothetical` needs 127px
against the 122px column, while `4.2 hypothetical` needs 120px.

**A percentage is shown only when a validated calibration supports one.** This
is measured, not stylistic. On the 337-case archive the shipped probability
transform scored a Brier skill of -0.561 against climatology, and its most
confident bin forecast 77% while verifying at 7.3% — below the base rate. A
number rendered as a percentage beside real thermodynamic parameters reads as a
calibrated probability whether or not it is one, and a caveat inside a
click-through dialog does not reach a reader who never clicks. So
`toi_probability_is_supported()` gates the display on evidence: a percentage
appears only when an offline artifact that passed the promotion gate is selected,
and the score is shown otherwise. If an artifact is ever validated the percentage
and its colour ramp return with no code change. The row, its position, its width,
and its white/yellow/red/pink ramp are unchanged; only the claim the number makes
has changed.

In the interactive sounding viewer, click the `TOI` row to open its explanation
panel. It leads with the measured skill of whatever it is showing, then the
raw probability (labelled `uncalibrated` unless validated) and colour tier,
experimental score,
jet layer and motion, maximum jet, jet/risk distance and bearing, peak STP,
seasonal month, score-component weights, method and probability versions,
valid period, source, limitations, and all embedded provenance. An unavailable
TOI opens the same panel with `--` values and the exact missing-input reason;
the panel does not add a footer or alter the sounding layout.

The producer needs multiple regional forecast times, not one sounding. It uses
the 500-hPa jet outside June-August and the 300-hPa jet during those months,
jet-object translation speed, maximum jet speed, total/east-west distance and
bearing from the objective risk centroid, month, and peak fixed-layer STP in
that risk region. Translation dominates below about 45 kt; position matters
more for faster jets; a maximum jet near 90 kt is favoured; and July receives
the published seasonal treatment. Peak STP changes the probability but not the
TOI score.

#### Temporal sampling

Published TOI evaluates the midlevel jet across an 18-hour window, nominally
06Z the day before the event through 00Z on the event day. The live producer
requests HRRR frames every three hours across that window, so a normal run
samples seven forecast hours (`0,3,6,9,12,15,18`) plus the requested forecast
hour when it falls off the interval. Duplicates are removed, the plan is
capped at eight frames so denser sampling can never become an unbounded
download, requests stay sequential, and every successfully decoded frame is
used in valid-time order.

Sampling is failure-soft. Missing hours do not stop the remaining requests. As
long as at least two frames decoded and they still span at least nine hours,
TOI is produced and marked `degraded`; otherwise TOI is returned unavailable
with the exact reason. Every run records its sampling audit in provenance:

| Provenance key | Meaning |
|---|---|
| `toi_requested_forecast_hours` | The planned three-hourly sample |
| `toi_successful_forecast_hours` | Hours that decoded, in valid-time order |
| `toi_failed_forecast_hours` | Hours that failed, or `none` |
| `toi_frame_count` | Frames used in jet tracking |
| `toi_time_coverage_hours` | Span between the first and last used frame |
| `toi_sampling_interval_hours` | Requested sampling interval |
| `toi_maximum_sampling_gap_hours` | Largest hole between used frames |
| `toi_sampling_status` | `complete` or `degraded` |
| `toi_sampling_degraded_reason` | Present only when degraded |

The regional-guidance contract and sounding display contain only TOI.

Supply the payload either as the `regional_guidance` object in the sounding's
adjacent `.json` sidecar, or as a separate document:

```bash
sharpmod-render point.npz point.png --guidance-json regional-guidance.json
```

Probabilities are fractions from 0 through 1. This illustrative payload shows
the versioned TOI schema and provenance state:

```json
{
  "schema_version": 2,
  "experimental_not_official": true,
  "source": "regional-workflow-example",
  "toi": {
    "state": "experimental",
    "score": 4.2,
    "high_risk_probability": 0.68,
    "method_version": "toi_omega2024_experimental_v1",
    "calibration_version": "example-only",
    "features": {
      "pressure_level_hpa": 500,
      "translation_speed_kt": 43.8,
      "maximum_jet_speed_kt": 92.1,
      "jet_to_risk_distance_km": 318.0,
      "jet_to_risk_bearing_deg": 329.0,
      "maximum_stp": 7.3,
      "month": 4
    }
  }
}
```

The experimental Tornado Outbreak Indicator helpers in `sharpmod.guidance`
extract the publicly described regional features (500/300-hPa jet-object
translation, jet/risk geometry, and peak STP), apply the public calculator's
published bins, and generate a versioned probability. The live HRRR adapter uses
surface CAPE, 0-1-km SRH, 0-6-km bulk shear, and a Bolton surface-LCL estimate
in SHARPpy's fixed-layer STP equation to construct an explicitly named proxy
risk mask. The scorecard and probability transform are SHARPpy reconstructions,
not official SPC output: unpublished SPC weights are replaced by documented
public-rule weights, and the transform is anchored to the paper's April 27,
2024 example (TOI 4.35, peak STP 8-or-9, 87%). Official SPC weights and
calibration remain unpublished. The method reference is SPC's
[OMEGA Project paper](https://www.spc.noaa.gov/publications/broyles/omega.pdf).

### Offline TOI calibration (`sharpmod-guidance`)

`sharpmod-guidance` is an offline research pipeline. It never runs during
rendering, and it does not change the shipped default probability transform.
Its purpose is to make a *fitted* TOI calibration possible, reproducible, and
auditable — and to make an unvalidated one impossible to mistake for a
validated one.

```bash
# 1. Extract one row per independent historical forecast case.
sharpmod-guidance build-toi-dataset \
  --manifest manifests/toi_cases_2015_2024.json \
  --output data/toi_dataset.json \
  --csv data/toi_dataset.csv \
  --weights population \
  --download-dir /data/hrrr-cache

# 2. Fit the regularized logistic calibrator, holding out a test period.
sharpmod-guidance train-toi \
  --dataset data/toi_dataset.json \
  --output models/toi_logistic_v1.json \
  --calibration-version toi_logistic_2015_2022_v1 \
  --test-years 2023,2024 \
  --scheme leave-one-year-out \
  --l2 1.0 \
  --bootstrap 1000 \
  --report reports/toi_training.json

# 3. Verify against the shipped transform and climatology.
sharpmod-guidance evaluate-toi \
  --dataset data/toi_dataset.json \
  --artifact models/toi_logistic_v1.json \
  --scheme expanding-year \
  --report reports/toi_evaluation.json
```

**Feature extraction reuses the operational code.** `build-toi-dataset` calls
the same `build_live_hrrr_guidance` producer the GUI uses, so archived cases see
identical jet tracking, objective risk-region selection, fixed-layer STP proxy,
scorecard, and three-hourly temporal sampling. There is no separate
training-only feature path to drift out of sync. Archived HRRR input comes from
the NOAA Open Data archive (2014 onward) through the same Herbie-backed
fetcher; `--fetcher module:function` can substitute a local archive reader.

**Labels are either supplied or transparently derived, and never claimed to be
official.** SHARPpy does define one outcome of its own, but it is a documented,
versioned proxy rule over public observations rather than an official label.
Two target definitions exist:

| Target | Meaning |
|---|---|
| `manifest_label_v1` | Binary outcome supplied verbatim by the manifest |
| `high_risk_worthy_proxy_v1` | A named, versioned SHARPpy screen over NCEI Storm Events tornado counts, intensities, and path lengths |

`high_risk_worthy_proxy_v1` is SHARPpy's own definition, stated openly as a
rule you can read and disagree with: an EF4+ tornado, two or more EF3+
tornadoes, twenty or more tornadoes including an EF2+, or an EF2+ tornado with
a path of 40 miles or more. It is a coarse screen, not a reconstruction of any
SPC quantity.

Official Risk Impact Value is not an available target. Its weights, impact
terms, and event-separation rules are unpublished, so no artifact here may
claim to predict official RIV, and the proxy is never labelled RIV.

A manifest must contain outbreak, ordinary severe-weather, and null/control
cases. Either the natural sampled frequency is kept (`--weights natural`) or a
documented `population_base_rate` restores it (`--weights population`), so
fitted probabilities are not inflated by oversampling outbreaks.

**Leakage is blocked, not just discouraged.** Every case declares how its
forecast anchor point was chosen, and anchors derived from observed tornado
locations (`observed_tornado_locations`, `storm_report_centroid`, and similar)
are rejected. The risk region itself is always the objective forecast proxy-STP
region derived at issuance.

A minimal manifest looks like this:

```json
{
  "target_definition": "high_risk_worthy_proxy_v1",
  "label_source": "NCEI Storm Events export 2025-01, tornado segments",
  "dataset_kind": "historical",
  "population_base_rate": 0.02,
  "cases": [
    {
      "event_id": "2023-03-31-midsouth",
      "case_class": "outbreak",
      "run_time": "2023-03-30T06:00:00+00:00",
      "forecast_hour": 6,
      "latitude": 35.1,
      "longitude": -90.0,
      "anchor_source": "spc_outlook_centroid_at_issuance",
      "observed": {
        "tornado_count": 146,
        "ef2_plus_count": 25,
        "ef3_plus_count": 9,
        "ef4_plus_count": 1,
        "longest_ef2_plus_path_miles": 59.0
      }
    }
  ]
}
```

**Validation is blocked by year and event, never by random row.** Successive
cycles inside one outbreak are not independent samples. Each event id is
assigned one `event_year` — taken from its earliest issuance — and every split
and fold uses that, so an event whose cycles straddle 31 December and 1 January
stays inside a single fold instead of appearing in both training and test. A
dataset whose rows disagree about an event's blocking year is rejected outright.
`train-toi` runs leave-one-year-out or expanding-year cross-validation on the
training years and keeps `--test-years` completely untouched. Reports contain
Brier score, Brier
skill, reliability bins on SPC-style outlook edges, calibration intercept and
slope, POD, FAR, CSI, frequency bias, ROC area, average precision, and
event-blocked bootstrap confidence intervals — always alongside the shipped
public-anchor transform and climatology on the same cases.

**Artifacts are portable and honest.** The exported JSON carries coefficients,
standardization statistics, the feature schema, training and test years, the
target definition, base rate, full metrics, the dataset content hash, method
version, and calibration version. Runtime inference is plain arithmetic:
scikit-learn is never required to evaluate a calibrated probability.

#### The promotion gate

An artifact is marked `"validated": true` only when it clears a configurable,
pre-registered gate. The default is `research-target`, not a convenience
setting, so an unconfigured run is held to the strict bar.

| Gate | Purpose |
|---|---|
| `research-target` | The scientific bar. `scientific: true`. |
| `pipeline-smoke` | Exercises the code path only. `scientific: false`, so it **can never promote anything**, whatever the metrics say. |

The research gate requires all of the following, and reports every unmet item as
a named blocker:

- **Sample size.** At least 8 chronological development years, 3 untouched test
  years, 200 independent event groups, 30 positive event groups, and 100
  negative event groups.
- **Per-fold floors.** Every cross-validation fold needs at least 3 positive and
  10 negative event groups, and no fold may fail to evaluate. The untouched test
  period needs at least 10 positive and 40 negative event groups. This is what
  stops a nominally large dataset with a handful of positives from passing.
- **Chronology.** The test period must start strictly after the last development
  year.
- **Uncertainty, not point estimates.** A grouped, *paired* bootstrap of the
  Brier difference against both climatology and the public-anchor transform must
  have its whole confidence interval above zero. Two separately computed
  intervals that happen not to overlap is a weaker claim, and a point-estimate
  gain is no claim at all.
- **Stratified behaviour.** Calibration and skill must be reported by region,
  season, forecast lead, and HRRR operational era, each with its own sample
  counts, and no stratum above `minimum_stratum_cases` may degrade past
  `minimum_stratum_brier_skill_score`.
- **Pre-registration.** A frozen plan must exist and the realized split and
  criteria must match it.
- **Prospective evidence.** A reserved future severe-weather season must be
  evaluated with the frozen artifact.

Freeze the pre-registration *before* looking at any held-out number:

```bash
sharpmod-guidance freeze-toi-plan \
  --output plans/toi_hrrr_2015_2022_v1.json \
  --plan-version toi_hrrr_2015_2022_v1 \
  --development-years 2015,2016,2017,2018,2019,2020,2021,2022 \
  --test-years 2023,2024,2025 \
  --prospective-season "2026 spring severe-weather season" \
  --criteria research-target

sharpmod-guidance train-toi \
  --dataset data/toi_dataset.json \
  --output models/toi_logistic_v1.json \
  --calibration-version toi_logistic_2015_2022_v1 \
  --plan plans/toi_hrrr_2015_2022_v1.json \
  --prospective reports/prospective_2026_spring.json \
  --report reports/toi_training.json
```

The plan hashes the target definition, case-selection rules, feature schema,
split years, and every promotion threshold. Editing any of them afterwards —
shrinking the test period, loosening a minimum count — changes the hash and is
rejected on load. The artifact records the plan hash, so a reviewer can tie a
result back to the pre-registration that produced it.

Synthetic fixtures exercise the whole pipeline but can never set the validated
flag, and the shipped transform stays the default until a real dataset earns the
replacement. Select a validated artifact explicitly:

```python
from sharpmod.guidance import TOICalibrationArtifact, build_live_hrrr_guidance

artifact = TOICalibrationArtifact.load("models/toi_logistic_v1.json")
guidance = build_live_hrrr_guidance(
    run_time, 6, 35.2, -97.4, calibrator=artifact
)
```

The selected calibration's version, training years, target, and validation
state then appear in TOI provenance and in the click-through TOI details panel.

#### Building the real 2015-2025 archive dataset

Four commands cover the data-collection half of the programme. They are separate
from `build-toi-dataset` because a multi-thousand-download job needs its own
resumable, bounded runner.

```bash
# 1. Audit before transferring anything: disk, caches, sources, estimates.
sharpmod-guidance audit-archive --work-dir archive/toi --report reports/audit.json

# 2. Download the versioned NCEI Storm Events files and record their hashes.
#    Keeping the raw CSVs is what makes label provenance auditable: NCEI
#    republishes corrected years, so d2018_c20260323 is a different dataset
#    from a later build of the same year. Re-running skips existing files.
python scripts/fetch_ncei_storm_events.py \
  --out-dir archive/ncei --first-year 2015 --last-year 2025

# 3. Generate the stratified 2015-2025 catalogue from those exact files.
sharpmod-guidance build-toi-catalog \
  --output archive/catalog-2015-2025.json \
  --first-year 2015 --last-year 2025 \
  --positive-cases 60 --severe-cases 240 --null-cases 300 \
  --outcomes-dir archive/ncei

# 4. Pilot a bounded batch first, spanning classes, years, leads, and eras.
sharpmod-guidance run-toi-archive \
  --catalog archive/pilot-catalog.json --work-dir archive/pilot \
  --max-cases 8 --max-transfer-gib 2 --max-seconds 1800 --min-free-gib 20

# 5. Split into event-indivisible shards and run them in parallel. This is what
#    makes the collection practical; see the note below on why it is not cloud
#    work. Re-running resumes every shard from its own checkpoint.
python infra/gcp/toi-batch/toi_batch.py shard \
  --catalog archive/catalog-2015-2025.json --shards 6 --out-dir archive/shards

python scripts/run_toi_archive_parallel.py \
  --shard-dir archive/shards --work-root archive/toi \
  --max-transfer-gib 12 --max-seconds 28800 --allow-failures \
  --report archive/full-run-timing.json

# 6. Or stay single-process, in resumable batches, if wall time does not matter.
sharpmod-guidance run-toi-archive \
  --catalog archive/catalog-2015-2025.json --work-dir archive/toi \
  --max-cases 100 --max-transfer-gib 10 --max-seconds 21600 --min-free-gib 20

sharpmod-guidance verify-toi-archive \
  --work-dir archive/toi --output reports/archive-manifest.json
```

**Parallel collection is local work, not cloud work.** The ~15 h single-worker
figure is dominated by ~24,000 sequential archive requests, not computation, so
moving the same parallelism-1 job to a cloud VM takes just as long at additional
cost. Speed comes from running shards concurrently, which behaves identically on
a laptop. `run_toi_archive_parallel.py` uses *processes*, not threads: the
runner's correctness rests on a single-writer `run.lock`, atomic `.partial`
writes, and a per-run checkpoint, and threading would move all three onto shared
mutable state. Each shard instead owns its catalogue, work directory, checkpoint,
and lock, so N workers are N independent already-tested runs. **Measured on 6
workers: 230 s wall against 882 s of summed shard time, a 3.8x speedup.**

Two budget details matter. Every budget is applied **per shard**, so
`--max-transfer-gib 12` allows 12 GiB each. And an unset `--max-cases` means
"the whole shard": the underlying runner defaults to 12 cases, so forwarding
nothing would silently stop each shard at 12 with `case_budget_reached`.

**Measured resource costs** (official archive, 2026-08-05):

| Quantity | Measured |
|---|---|
| GRIB messages matched per frame | 8, identical in every HRRR era |
| Bounded subset per frame | 7.98 MiB (v1) to 10.78 MiB (v4); full `wrfsfc` file is 86-151 MiB |
| Frames per case | 7 sampled + 1 anchor frame |
| Transfer per case | 60-85 MiB (pilot mean 69.7 MiB) |
| Wall time per case | 60-124 s (pilot mean 84.8 s) |
| Requests per case | ~40 |
| Retained output per case | ~4.7 KiB of JSON |
| Peak raw disk | ~18 MiB, because each subset is deleted after extraction |

Raw GRIB is discarded immediately after extraction, which is what makes the
programme feasible: a 600-case run transfers ~43 GiB but retains under 3 MiB.
`--keep-raw` disables that and is only for debugging.

**Projected full runs** at measured rates: 200 cases ≈ 14.3 GiB / 5.0 h; 400 ≈
28.7 GiB / 9.9 h; 600 ≈ 43.0 GiB / 14.9 h; 900 ≈ 64.5 GiB / 22.3 h. The binding
constraint is wall time, not disk.

**Runner guarantees.** Deterministic cache keys hash every feature-changing
input including the method version, so an incompatible cached extract is never
reused. Artifacts are written to `.partial` then renamed. A JSONL checkpoint
records each completed case, and a truncated final line from a crash is skipped
rather than trusted. A `run.lock` file enforces a single writer per run
directory. Transfer bytes, case count, wall time, and free-disk headroom are all
capped, and the run stops with a named reason such as
`transfer_budget_reached` or `disk_headroom_exhausted`. Retries use exponential
backoff with full jitter under a seeded RNG, and a minimum request interval
respects the service. Every case ends `success`, `skipped`, or `failed` with a
stated reason.

**Two measured findings that shape the science:**

1. **No F18 before HRRRv2.** At 06Z the archive publishes forecasts only through
   F15 until 2016-08-23. A 2015-2016 case therefore reaches six sampled frames
   and 15 h of coverage and is reported `degraded`, never as complete. This is an
   era-dependent sampling non-stationarity, which is exactly why per-era
   stratified skill is a promotion requirement. Starting development at 2017
   buys uniform 18-h sampling at the cost of two years of cases.
2. **Quiet days are outside TOI's domain.** A genuinely quiet control day has no
   connected forecast proxy-STP region, so TOI is *undefined* rather than
   negative, and the runner correctly skips it. Negative cases must therefore
   come from days where TOI is computable but the outcome was not high-end.
   Null controls are drawn from convective-season days without tornado reports;
   remaining skips are still recorded with their reason.
3. **A single grid maximum is a noise detector.** The first pilot anchored cases
   on the largest CONUS proxy-STP grid value. That put 2018-11-05 at 30.30N
   76.69W in the Atlantic and 2023-03-31 at 27.79N 94.06W in the Gulf, the
   latter with a peak proxy STP of 0.31 during a major outbreak. Anchors are now
   selected as *objects*: connected components are thresholded, filtered by
   minimum area, intensity, and land fraction against the bundled Census county
   land domain, and ranked by an integrated score (summed intensity over area,
   discounted by non-land fraction). Re-running those two cases moved
   2023-03-31 to 33.70N 98.66W in north Texas with land fraction 1.0 over
   209,635 km², and correctly reported 2018-11-05 as `anchor unavailable`
   (26 candidates, none qualifying) rather than inventing an offshore point.
   **The original pilot was therefore not regionally representative**, and its
   seven case files are superseded.
4. **Era limits must constrain case *generation*, not just reporting.** The
   catalogue builder recorded the pre-HRRRv2 F15 ceiling but still assigned
   forecast hours round-robin, so 32 of 600 real cases were 06Z F018 requests in
   2015-2016 that the archive never published. Each consumed four retries with
   backoff before failing. Knowing a limitation and enforcing it are different
   things: requested hours are now clamped at generation, the fetcher refuses an
   unpublished hour without issuing a request, and a missing extra anchor hour
   degrades the anchor search instead of discarding the case. That last point
   matters more than it looks, because failing those cases would have quietly
   removed 2015-2016 from an 8-development-year requirement that has no slack.
5. **Case yield varies sharply by class, and that is the binding constraint.**
   MEASURED over the first 88 cases of the real run: outbreak days resolve an
   anchor 83% of the time (48/58) but ordinary-severe days only 37% (11/30),
   because many have no region clearing the minimum area, intensity, and land
   support. That is the object rule working, not failing. But it means the
   *negative* sample is the scarce resource, not the positive one, and the
   honest response is to sample more candidate days rather than to relax the
   anchor rule after seeing which cases failed. Loosening a threshold chosen
   against observed attrition is the researcher-degrees-of-freedom leakage that
   makes a held-out test period meaningless.

#### Compiling the archive into a trainable dataset

Archive collection is a one-time cost. `compile-toi-dataset` turns verified
extracted case JSON into a `TOIDataset` with **zero network access**, so
retraining never refetches HRRR:

```bash
sharpmod-guidance verify-toi-archive --work-dir archive/toi \
  --output reports/archive-manifest.json

sharpmod-guidance compile-toi-dataset \
  --archive-work-dir archive/toi \
  --catalog archive/catalog-2015-2025.json \
  --label-source "NCEI Storm Events export d2015-d2025 c20260728" \
  --weights population --population-base-rate 0.02 \
  --output data/toi_dataset.json --report reports/compile.json
```

Compilation refuses unverified input by default, rejects duplicate cache keys,
preserves skip reasons, and carries event/year/region/season/lead/HRRR-era plus
source and hash provenance into the dataset. The output is accepted directly by
`train-toi` and `evaluate-toi`.

#### Deploying the collection on Google Cloud Batch (planned only)

`infra/gcp/toi-batch/toi_batch.py` renders a cost-bounded deployment. It is a
planner: every mutating subcommand is dry-run by default and each real mutation
needs its own flag (`--confirm-enable-apis`, `--confirm-create-bucket`,
`--confirm-build`, `--confirm-submit`, `--confirm-delete`). Configuration never
implies permission.

```bash
cd infra/gcp/toi-batch
python toi_batch.py preflight --offline          # read-only audit
python toi_batch.py bundle                       # dry-run source bundle
python toi_batch.py shard --catalog ../../../archive/catalog-2015-2025.json
python toi_batch.py plan --total-cases 600 --mib-per-case 73.4 \
    --seconds-per-case 89.3 --shards 4 --parallelism 1
```

Design points: Batch **script** runnables (no container image, no Artifact
Registry, no Cloud Build, no Cloud Run); `us-east1`, SPOT `e2-standard-4`, 50 GiB
`pd-balanced`, Cloud Logging, 24 h max duration, automatic retry on Spot
preemption, labels `app=sharppy` / `workload=toi-archive`; deterministic
event-indivisible sharding where all cycles of one `event_id` stay in one shard;
raw GRIB stays VM-local and is never uploaded, while the compact case JSON and
checkpoint are mirrored to a dedicated run prefix with explicit upload plus
checksum verification (no Cloud Storage FUSE rename semantics); a dedicated new
bucket only, with a 14-day lifecycle on temporary prefixes and no delete rule for
final manifests, source records, validation reports, or promoted artifacts.

Because a billing alert is not a cap, hard caps on cases, input bytes, wall
time, task count, retries, disk, and output bytes are enforced *inside* each
task, and `plan` exits non-zero if a projection breaches them.

#### What is enforced today, and what still needs real data

The gate machinery is complete and tested. What is missing is the archive, and
no amount of code can substitute for it. Keeping these two lists separate is the
point of this section.

**Enforced in code today** — every item below is implemented and covered by
tests, and each produces a named blocker when unmet:

| Enforced now | Mechanism |
|---|---|
| Model stays simple | One regularized logistic fit on TOI score and peak-STP bin; no epochs, no architecture search |
| Event-indivisible blocking | `event_year` per event id, rejected if rows disagree |
| Chronological split | Test period must start after the last development year |
| Sample-size floors | Development years, test years, event groups, positive and negative event groups |
| Per-fold floors | Minimum positive and negative event groups in every fold and in the test set |
| Unevaluated folds | A fold that cannot be fitted blocks promotion instead of being skipped |
| Interval-based improvement | Grouped paired bootstrap over climatology and the anchor, lower bound must exceed zero |
| Stratified reporting | Region, season, forecast lead, HRRR era, each with sample counts and a degradation floor |
| Pre-registration | Hashed frozen plan; split and criteria must match it |
| Prospective requirement | A shadow-season record is required and cannot be synthesized here |
| Synthetic isolation | `dataset_kind` and the non-scientific `pipeline-smoke` flag both block promotion |
| Deterministic scientific hashing | `scientific_content_sha256` over versioned scientific inputs only, plus `artifact_sha256` over final file bytes |
| Real verification | `verify-toi-archive` recomputes every hash and cache key and fails with counts and paths |
| Zero-network compilation | `compile-toi-dataset` reads verified case JSON only; no HRRR refetch |
| Object-based anchors | Connected proxy-STP components, land mask, area/intensity/support floors, integrated score |
| Bounded, resumable retrieval | Deterministic keys, atomic writes, checkpoints, single-writer lock, transfer/case/time/disk caps, backoff, explicit outcomes |
| Documented provenance | Source URLs, licenses, retrieval dates, file names, and SHA-256 recorded per run |

**Requires collecting real historical data** — these are data-gathering tasks,
not code tasks:

1. **Build the case catalogue.** Roughly 8-10 years of archived HRRR cases
   (2015-2022 development, 2023-2025 untouched test), hundreds of independent
   event groups, and dozens of positive high-end cases, mixing outbreak,
   ordinary severe, and null/control dates with explicit sampling weights.
   HRRR archives start in 2014, so this is close to the maximum a current
   archive supports.
2. **Export verified outcomes.** An NCEI Storm Events / SPC report export
   covering the same period, versioned, feeding either a manifest label or
   `high_risk_worthy_proxy_v1`.
3. **Retrieve the archive at scale.** Seven frames per case across the sampling
   window, several hundred cases.
4. **Run the shadow season.** Freeze the artifact, then evaluate a reserved
   future season with no refitting and produce the prospective record.
5. **Expect era-dependent behaviour.** HRRR v1-v4 span the development window.
   The per-era report exists specifically so a model that only works under the
   configuration dominating the training years is visible rather than hidden by
   a pooled average.

Until items 1-4 exist, the honest answer stays the same: TOI is an experimental
reconstruction with an uncalibrated public-anchor transform. The gate is
designed so that answer is produced automatically rather than asserted.

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
| `SHARPMOD_REGIONAL_GUIDANCE` | `auto` | Live experimental HRRR TOI fetch; set `off` to disable or `on` to force |

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
observed-sounding fetch 72357 "2024-05-20 00" --out oun.npz --render oun.png
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
  `python scripts/install_sharppy_compat.py` (see README → Rendering).
- **`uwyo-sounding fetch` says the station/time is unavailable** — that site
  didn't report at that hour; try 00Z or 12Z, a nearby date, or use
  `observed-sounding fetch` for the explicit IEM fallback.
- **A batch manifest belongs to a different job** — choose a new manifest or
  output directory; the resume guard intentionally refuses to mix job specs.
- **`era5-extract` / `wrf-extract` import errors** — install the matching extra:
  `pip install -e ".[era5]"` or `pip install -e ".[wrf]"`.
- **`era5-extract` reports missing CDS credentials** — create a free CDS
  account, accept the ERA5 pressure-level dataset licence, then copy the API
  profile into `$HOME/.cdsapirc` from
  <https://cds.climate.copernicus.eu/how-to-api>.
- **A rendered PNG looks empty / a widget overflows** — extremely degenerate
  input (e.g. constant winds at every level) can overflow the storm-relative
  hodograph; use real data.
- **Herbie prints a checkmark/emoji and Windows reports
  `UnicodeEncodeError: 'charmap' codec can't encode...`** — importing
  `sharpmod` now configures Windows stdout/stderr for non-throwing UTF-8 output.
  For a standalone script that never imports this package, launch Python with
  `python -X utf8 ...`.
- **An inline `pwsh -Command` containing `DataObject`, `StringCollection`, or
  `System.Drawing.Image` fails to parse** — keep the statements in a `.ps1`
  file. For sounding images, run
  `pwsh -NoProfile -File scripts/copy-image-to-clipboard.ps1 IMAGE.png`.
- **`grep` is not found on Windows** — repository search examples use ripgrep:
  install it with `winget install BurntSushi.ripgrep.MSVC`, then run `rg`.
