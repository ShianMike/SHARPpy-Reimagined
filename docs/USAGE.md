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
  `model-extract`, `model-batch-extract`, `box-extract`, `wrf-extract`,
  `sharpmod-render`) — scriptable, headless, reproducible. Use these for batch
  extraction and PNG rendering (sections 1–5).

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

- **Station Map** — a clickable map of UWyo radiosonde stations over a basemap of
  coastlines, lake shores, national borders, and state lines. Borders are clipped
  to land, so none is ruled straight across a lake. Click a dot to
  select it, double-click to open it. Scroll to zoom, drag to pan, and jump to a
  region with the *Map area* menu. Set the valid time (defaults to the most
  recent synoptic hour) and open the selection. The time menu offers every
  three-hourly UTC slot from 00Z through 21Z for regular and special/asynoptic
  observations.
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
  **Box…** samples an area rather than a point: Shift-drag a rectangle on the
  map (or turn the button on and drag normally) and every model grid point
  inside it is extracted from one download. A confirmation dialog resolves the
  lattice, spacing, point count, and download count first, and the map previews
  the exact points that will be sampled.
  Shift-drag always draws a box; turning the button on makes a plain left-drag
  draw one, and the map still pans on a middle-drag or right-drag so the mode
  never takes the whole mouse. A click without a drag stays a click, and the mode
  releases itself once a box is accepted.
  By default the samples are **averaged into a single sounding** that opens in
  the ordinary analysis window, with the same overlays, town name, and parcel
  logic as a point fetch. Winds are averaged as components and moisture as
  mixing ratio; only the layer every point shares is averaged, so the mean
  begins at the highest ground in the box. The Skew-T title names the average and
  its member count, an amber **BOX MEAN** callout in the top-right of the plot
  says so on the page itself, and the locator inset draws the sampled rectangle
  instead of a crosshair, widening its view until the whole box fits. Note that
  the mean sounding's derived parameters are not the mean of the individual
  points' parameters — the callout's second line says this too, because the CAPE
  printed on the page is the CAPE of the mean column, not the mean CAPE of the
  box. On a very small window the callout is omitted rather than crammed over the
  data; the title still names the average.
  The dialog opens on the forecast hour selected in the sidebar and offers a
  **Forecast hour** picker listing every hour the product publishes, so the hour
  can be changed without redrawing the box.
  Choosing **Explore the box as a parameter field** opens the area workspace
  instead. That workspace shows a
  parameter field map, area statistics with the location of the significant
  extreme, and a ranked list of the twelve most significant points;
  double-clicking any cell opens it as a full Skew-T. The
  instability and kinematic fields resolve immediately; the SPC composites are
  opt-in behind **Add SPC composites** because they cost about 0.4 s per point.
  The **Ingredients** picker hatches the points where every ingredient of a mode
  holds at once and reports how much of the area qualifies. Ticking **Step
  through forecast hours** in the dialog samples the same box at up to twelve
  hours and adds a slider, looping playback, and **Jump to peak**. **Export**
  saves the field map as a PNG, every point as CSV, or the sampled cells as
  GeoJSON. Press **Escape** to abandon a rectangle, and **Cancel** to stop a run
  while keeping the points already completed.
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

### Flat or curved map view

**View → Map Projection** switches every map tab between two views at once, and
the choice is remembered between sessions:

- **Flat** (default) — straight, evenly spaced parallels and meridians. It is
  what every previous version drew, and the only view that stays correct at every
  extent the picker offers.
- **Curved** — a Lambert conformal conic whose standard parallels sit a sixth of
  the way in from the top and bottom of the current view. Parallels bow and
  meridians converge toward the pole, which is the shape a regional forecast map
  normally has.

One thing is worth knowing about the curved view: a cone cannot represent an
extent taller than 75 degrees of latitude, or one centred within 4 degrees of the
equator, so those views quietly fall back to the flat transform; zooming into a
region brings the curve back. Everything else — imagery, vector layers, station
markers, boxes, graticule labels, panning, and clicks — is projected through the
same transform either way.

Imagery is warped into the cone a couple of degrees at a time rather than blitted
as a rectangle, since a conic has no rectangle to blit into. The result agrees
with an exact per-pixel reprojection to under a pixel, and it is cached and
re-blitted during a pan, so a curved view with a model field and a radar frame on
it costs about what the flat view does.

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

### Use the analysis workspace

Choose **View → Analysis Workspace** or press `Ctrl+Shift+A` in a sounding
window:

- **Trends** plots one selected parameter over a forecast timeline, leaves a
  visible gap for every missing hour, activates a valid time when its point is
  clicked, and exports the displayed series to CSV.
- **Compare** uses the reference sounding's exact valid time. It shows MLCAPE,
  MLCIN, 0–6 km shear, 0–1 km SRH, and effective-layer STP plus deltas for each
  loaded model/run; a nonmatching time is reported as unavailable rather than
  silently paired with a nearby hour.
- **Ensemble** shows the number of available members, p10/median/p90 parameter
  distributions, and pressure-level p10–p90 temperature/dewpoint envelopes.
  The envelope's temperature axis is fixed at **-50 to +50 °C** with a zero
  guide so every ensemble uses the same geometry.
- **Notes** stores free-form decisions and uncertainty with the session.

From the forecast picker, **Workspace…** acquires different models, successive
runs, or ensemble members for one point and exact valid time, then opens the
appropriate tab. The batch is bounded, cached, cancellable, and keeps successful
soundings available when another requested member fails.

### Save and reopen an analysis session

Choose **File → Save Analysis Session…** (`Ctrl+Shift+E`) in a sounding window
to preserve all loaded soundings and times, the active sounding/member, current
profile and interpolation state, storm motion, parcel selection, visible panel,
fit/exact zoom, picker map extent, analysis tab/controls/notes, and overlay
descriptors. Choose **Open Analysis Session…** (`Ctrl+Shift+O`) from the picker
or a sounding window to restore everything in one multi-sounding viewer,
independent of the normal combine-soundings setting.

The `.sharpmod-session` file is versioned JSON, not pickle, and is validated
before a viewer is created. It contains decoded profile state and lightweight
overlay provenance only—never source GRIB downloads or raster/vector payloads—
so the existing delete-on-viewer-close cleanup remains intact and overlays can
be refetched from their descriptors.

### Save from the GUI

The sounding window's **Export** menu writes the current view:

- **Export Image (HD PNG)** (`Ctrl+E`) — a 2x high-density image of the whole
  window including the mounted derived-parameter panels, defaulting to
  `STATION_YYYYMMDDHHZ_hd.png`.
- **Export Image (UHD PNG)** — a larger 2.8x ultra-high-density image,
  defaulting to `STATION_YYYYMMDDHHZ_uhd.png`.
- **Export Image (Lossless PNG)** — the original-size compact/lossless image,
  defaulting to `STATION_YYYYMMDDHHZ_lossless.png`.
- **Export Text (SHARPpy)** — the focused profile as a text file that loads
  straight back into the app (or into `sharpmod-render`).

All export/save dialogs begin in `rendered_soundings` beside the source project
or installed application. The same rule covers analysis sessions, trend and
comparison CSV, box PNG/CSV/GeoJSON, saved-location JSON, and the upstream
**File → Save Image** / **Save Text** actions. **Open Export Folder** opens that
exact directory. SHARPpy Reimagined creates it when needed and reports a clear
path-specific error if it cannot create or write it; it never silently falls
back to Desktop, Documents, Downloads, or the user home directory. Choosing a
different destination is a one-export override and is not used to seed the next
dialog or a later application run.

From the command line, `sharpmod-render INPUT` defaults to
`rendered_soundings/sharpmod_sounding.png` beside the application. Supplying
`sharpmod-render INPUT OUTPUT` keeps the explicit output path.
Point-sounding extractors likewise put default `.npz`/`.json` pairs and unnamed
`--render` PNGs there, while preserving every explicit output path.

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
uwyo-sounding fetch 72357 "2024-05-20 00" --render        # PNG in rendered_soundings
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

RRFS-A exposes separate `rrfs-a`, `rrfs-a-alaska`, `rrfs-a-hawaii`,
`rrfs-a-puerto-rico`, and `rrfs-a-north-america` adapters, all F000-F084
hourly. It is the one product this project locates itself instead of through
Herbie, whose `rrfs` template still resolves to a retired AWS prefix and whose
product list predates the current file layout. The adapter reads the published
NOMADS `.idx` inventory and pulls the selected GRIB messages over HTTP byte
ranges with eight workers by default, tuned by `SHARPMOD_RANGE_WORKERS`.

Three RRFS-specific consequences are worth knowing:

- **Only the 00/06/12/18Z cycles are selectable.** The off-hour cycles publish
  a sub-hourly two-dimensional product and no pressure levels, so they cannot
  produce a sounding at any forecast hour.
- **Every sounding needs two files.** `prslev` carries 45 pressure levels
  (1000-2 hPa) and no ground records; `2dfld` carries the complete verified
  ground row and no pressure levels. Both are fetched and concatenated, and a
  provenance sidecar next to the combined file lets a repeat request skip the
  network entirely.
- **There is no omega.** RRFS publishes DZDT (geometric vertical velocity in
  m/s) rather than VVEL, and `omeg` is pressure vertical velocity in Pa/s with
  the opposite sign convention, so those messages are not fetched and vertical
  velocity reads as missing.

NOMADS offers no spatial subsetting for RRFS, so a field plan costs its full
domain footprint: roughly 340 MB for 3-km CONUS or Alaska, 190 MB for 13-km
North America, and 13-35 MB for the 2.5-km Hawaii and Puerto Rico domains. The
payload is cached per model hour rather than per point, so one transfer serves
every point at that run and forecast hour. Prefer `rrfs-a-north-america` for a
cheaper CONUS-covering option.

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

Python callers can use `sharpmod.batch_extract.run_batch(...)` or retain a
`BatchExtractor` and call `cancel()`. `BatchRunResult.items` and
`output_paths` preserve input order, including heterogeneous forecast hours.
Pass `model_hour_cache=` to lease a caller-owned `ModelHourCache`; the batch
runner leaves an external cache alive for timeline/offline reuse.

### Area soundings (`box-extract`)

A *box sounding* samples the model's own grid inside a lat/lon rectangle. Because
every sampled point shares one model, run, forecast hour, and member, the whole
box is one download and one bulk decode.

The desktop app averages those samples into a single sounding by default; the CLI
below extracts and reports them as a field. Both use the same Qt-independent
core, and `sharpmod.box_mean` performs the averaging for either.

Two rules govern the sampling, and both are reported rather than applied
silently. First, the requested spacing is rounded **up** to a whole multiple of
the product's published grid spacing, so no two soundings are drawn from one grid
cell. Second, the lattice is coarsened until it fits the point budget (256 for a
gridded product, 24 for a point-only provider such as Open-Meteo ICON or ECCC
GeoMet, which answer one request per point).

```bash
# Resolve the lattice and cost without downloading
box-extract hrrr 34.0 -99.0 37.0 -95.0 --dry-run

# Extract, then report area statistics and one field as a grid
box-extract hrrr 34.0 -99.0 37.0 -95.0 \
    --output-dir box-output --target-points 64 --field mucape --field srh_3km

# Include the SPC composites and export every point
box-extract hrrr 34.0 -99.0 37.0 -95.0 \
    --output-dir box-output --composites --csv box.csv

# Where do all the ingredients of a mode hold at once, and over how much ground?
box-extract hrrr 34.0 -99.0 37.0 -95.0 \
    --output-dir box-output --screen supercell

# Step the same box through six forecast hours, three hours apart
box-extract hrrr 34.0 -99.0 37.0 -95.0 \
    --output-dir box-output --hours 6 --hour-step 3 --field mucape

# Hand the sampled cells to GIS, tagged with the screen verdict
box-extract hrrr 34.0 -99.0 37.0 -95.0 \
    --output-dir box-output --screen supercell --geojson box.geojson

box-extract --list-fields          # every key; * marks --composites fields
box-extract --list-screens         # every screen and its thresholds
```

Give `--target-points` **or** `--spacing-km`, not both. `--run` accepts ISO 8601
and assumes UTC when no offset is present; omitting it selects the most recent
published cycle, exactly as `model-extract` does. Longitudes may be unwrapped to
express an antimeridian box: `50 170 56 190` is 20 degrees wide, not 340.

`--hours` takes its hours from the model's own published cadence, so it cannot
request an hour the product does not publish; asking past the end of the run is
an argument error rather than a failed download. Each hour is a separate
download — the saving a box gives you applies *within* an hour, not across them —
so `--dry-run` reports the transfer count as well as the sounding count. A
sequence prints a per-hour table with the peak marked, then the peak hour's
field grid, and `--csv`/`--geojson` write every hour into one file with an `fxx`
column.

Exit codes: `0` success, `1` no point could be extracted, `2` invalid arguments
or an unusable box, `130` cancelled.

The Python API is Qt-independent and is what the desktop workspace uses, so a
script and the GUI cannot disagree:

```python
from sharpmod.box_sounding import BoxRegion, plan_box_samples, box_requests
from sharpmod.box_analysis import FAST_TIER, COMPOSITE_TIER, analyze_box

region = BoxRegion.from_corners(34.0, -99.0, 37.0, -95.0)
plan = plan_box_samples("hrrr", region, target_points=64)
print(plan.shape, plan.spacing_km, plan.estimated_downloads)

# plan -> BatchRequest values for sharpmod.batch_extract
requests = box_requests(plan, run_time=run_dt, fxx=0)

# outputs maps each node's request_id to the .npz written for it
analysis = analyze_box(plan, outputs, tiers=(FAST_TIER,))
stats = analysis.statistics("mucape")
print(stats.minimum, stats.mean, stats.maximum, stats.spread)
print(stats.extreme.lat, stats.extreme.lon)     # where the maximum is
print(analysis.field("mucape"))                  # rows x cols, row 0 = north
print(analysis.ranked("mlcin", limit=5))         # most-capped points first
```

`FAST_TIER` covers parcels, effective inflow, kinematics, and DCAPE through the
shared `sharpmod.backends` contract: about 2.3 ms per point, so a 256-point box
resolves in under a second. `COMPOSITE_TIER` adds the SPC composite indices and
the AMS-literature parameters, which require the upstream SHARPpy
`ConvectiveProfile`: about 424 ms per point. Both read the same cached oracle the
Skew-T does, so a box field and a sounding opened from that cell agree.

`analysis.vertical_transect(field="tmpc", orientation="ew")` returns a
pressure-by-distance slice. Levels below a node's terrain stay `None` rather than
being extrapolated, so the underside of the section is the terrain profile.

#### Averaging a box into one sounding

`sharpmod.box_mean` collapses an extracted box into a single composite profile and
writes it as an ordinary portable sounding, so it opens through the same decode
path as anything else:

```python
from datetime import datetime, timezone

from sharpmod.box_mean import box_mean_profile, write_box_mean_sounding

# outputs maps each node's request_id to the .npz written for it
profile = box_mean_profile(
    outputs, lat=plan.region.center_lat, lon=plan.region.center_lon)
print(profile.describe())
print(profile.members, profile.levels, profile.surface_pressure_hpa)
print(profile.trimmed_below, profile.clamped_dewpoints)

write_box_mean_sounding(
    profile, "box-mean.npz",
    model=plan.model_label, model_key=plan.model_key,
    run_time=run_dt, valid_time=valid_dt, fxx=18,
)
```

Four details of the averaging are deliberate, because each is a place a naive
mean gets the physics wrong:

- **Winds are averaged as `u`/`v` components.** The mean of 350° and 10° is 0°,
  not 180°. Components already in the archive are used as-is; otherwise speed and
  direction are resolved first.
- **Moisture is averaged as mixing ratio**, through the application's own
  `sharppy.sharptab.thermo`, and converted back. Averaging dewpoint directly
  biases the column dry.
- **The averaged dewpoint is clamped to the averaged temperature.** Saturation
  mixing ratio is convex in temperature, so by Jensen's inequality the mean of
  several subsaturated members can exceed saturation at the mean temperature.
  `clamped_dewpoints` counts how often that happened.
- **Only the layer every member shares is averaged.** The mean starts at the
  highest ground in the box; `trimmed_below` and `trimmed_above` report the
  levels the deepest member had that the shared layer could not use. A box
  straddling too much terrain to share two levels is refused rather than fudged.

A member that cannot be read is counted in `requested` but excluded from
`members`, so a partly-failed extraction still averages and says so. Fewer than
two readable members raises `BoxMeanError`: that is a point, not an area.

Leaving `loc` unset writes a coordinate label, which is what makes the automatic
town lookup resolve the centre of the box — exactly as a single-point fetch does
when no label is typed. The JSON sidecar records `box_mean`, the member count,
the trim counts, the clamp count, and a note repeating the caveat below.

The written `model` field carries the average — `HRRR box mean of 49` — because
that is the string the Skew-T prints in its own title, so the plot itself says
what it is. The plain product name stays in the sidecar as `model_label` for
anything reading provenance. Passing `box=(lat0, lon0, lat1, lon1)` records the
sampled rectangle as `box_mean_bounds`, which the sounding's locator inset reads
to outline the area and to widen its view until the whole rectangle fits.

**The derived parameters of the mean sounding are not the mean of the members'
parameters.** CAPE of the average column is not the average CAPE. Averaging
smooths the extremes, so treat a mean sounding as a description of the airmass
and use the field map when the extreme is the question.

#### Ingredient screens

One field answers "where is CAPE largest". A forecaster usually wants "where are
all of these true at once". A `Criterion` is one threshold on one parameter, and a
group of them is combined with AND:

```python
from sharpmod.box_analysis import Criterion, screen, screen_names

criteria = (
    Criterion("mucape", minimum=500.0),
    Criterion("shear_6km", minimum=35.0),
    Criterion("srh_3km", minimum=100.0),
)
mask = analysis.mask(criteria)            # rows x cols of True / False / None
result = analysis.coverage(criteria)
print(result.count, result.fraction, result.area_km2)
print(result.describe())

print(screen_names())                     # the seven named screens
analysis.coverage("supercell")            # a screen name works anywhere criteria do
analysis.screens()                        # only screens this box can actually evaluate
```

`Criterion.matches()` is **tri-state**: `True`, `False`, or `None` when the box
has no value for that parameter at that point. A missing field is never counted as
a failure, because "unread" and "unfavourable" are different claims — an
unextracted point must not be shaded as ruled out. A definite `False` still wins
over a missing field, so one satisfied-but-incomplete ingredient cannot rescue a
point that has already failed another.

`CoverageResult.fraction` divides by the points that were actually **evaluated**,
not by every point in the lattice, so a box straddling the domain edge reports the
fraction of the area it could read rather than understating itself. `.unknown`
carries the count that could not be evaluated and `.conclusive` is false when any
remain, so the ambiguity is visible instead of rounded away.

The seven screens in `INGREDIENT_SCREENS` — surface-based storms, organized
convection, supercell, tornado ingredients, large hail ingredients, damaging wind
ingredients, elevated convection — are **screening heuristics for narrowing
attention, not official products and not a forecast**. Every one uses fast-tier
fields only, so a screen resolves without paying for the composite tier, and every
threshold is deliberately permissive so a screen does not hide a marginal signal.

#### Spread across the box

An envelope reduces every sounding in the box to a per-level band, which is what
separates one airmass from a boundary inside the area:

```python
env = analysis.envelope(field="tmpc", percentiles=(10.0, 90.0))
print(env.soundings)                 # how many columns contributed
print(env.spread_at(700.0))          # max - min at 700 hPa
print(env.widest_level)              # where the box disagrees with itself most
```

`minimum`, `low`, `median`, `high`, `maximum`, and `counts` are per-level and
parallel to `levels`. A level below every grid point's terrain gets `None`
statistics and a count of zero rather than a band inferred from nothing, so the
sounding count thins near the ground instead of the band quietly widening there.
`columns` keeps the individual traces, which is what distinguishes a smooth
gradient from two distinct airmasses that happen to share a range.

#### Boxes through forecast time

`box_sequence_requests` samples the same lattice at several hours, and each hour
lands in its own `f###/` subdirectory so a single hour can be analysed alone:

```python
from sharpmod.box_sounding import box_sequence_requests, normalize_hours
from sharpmod.box_analysis import analyze_box_sequence

hours = normalize_hours((0, 3, 6, 9))
requests = box_sequence_requests(plan, run_time=run_dt, hours=hours)

# outputs_by_hour maps each hour to that hour's {request_id: npz path}
seq = analyze_box_sequence(plan, outputs_by_hour, tiers=(FAST_TIER,))
print(seq.summary("mucape"))              # per-hour table, peak marked
print(seq.peak_hour("mucape"))            # honours notable-high vs notable-low
print(seq.peak_coverage_hour("supercell"))
print(seq.statistics_series("mucape"))
print(seq.at(6).field("mucape"))          # one hour behaves like any BoxAnalysis
```

`available_parameters()` is the **intersection** across hours, not the union, so a
field selected in a viewer cannot vanish as the hour changes. Hours are capped at
twelve and the total hour-by-point count at 1024, because each hour is a separate
download.

#### Exporting a box

`sharpmod.box_export` writes files from either a `BoxAnalysis` or a `BoxSequence`,
so a one-hour box and a sequence produce the same shape of output and the CLI and
the GUI cannot drift apart:

```python
from sharpmod.box_export import write_box_csv, write_box_geojson

write_box_csv(analysis, "box.csv", criteria="supercell")
write_box_geojson(seq, "box.geojson")     # every hour, one file
```

GeoJSON features are the sampled **cells** as polygons, not bare points, because
what the model asserts is a value over an area of one grid spacing; a final
feature carries the box outline. A cell crossing the antimeridian is split into a
`MultiPolygon` per RFC 7946. A missing value is an empty CSV field and an explicit
`null` in GeoJSON — never `0` and never `-9999` — and the writer refuses to emit
`NaN` or `Infinity`, so the file is valid JSON for consumers that reject those.

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

### Python API

```python
from sharpmod.render import render
render("oun.npz", "oun.png")
render("oun.npz", "oun_sfc.png", parcel="SFC")

# Or the thin helper used by the extractor CLIs:
from sharpmod.tools import render_npz
render_npz("oun.npz")                 # -> rendered_soundings/oun.png
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
  account, accept the ERA5 pressure-level and single-level dataset licences,
  then copy the API profile into `$HOME/.cdsapirc` from
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
