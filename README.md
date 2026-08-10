<div align="center">

# SHARPpy Reimagined

**Modern sounding analysis and SHARPpy-style rendering for Python 3.11–3.13.**

[![Tests](https://github.com/ShianMike/SHARPpy-Reimagined/actions/workflows/tests.yml/badge.svg)](https://github.com/ShianMike/SHARPpy-Reimagined/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB?logo=python&logoColor=white)
![Qt6](https://img.shields.io/badge/Qt6-PySide6-41CD52?logo=qt&logoColor=white)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)

</div>

![Example SHARPpy Reimagined sounding](examples/example_sounding.png)

<sub>HRRR forecast point 36.68N 95.66W, F018, in the default Standard (dark)
palette — rendered from
[`examples/soundings/hrrr_point_36.68N_95.66W_f018.npz`](examples/soundings/hrrr_point_36.68N_95.66W_f018.npz).
`TOI = --` because no regional guidance payload is attached to this file.</sub>

<details>
<summary><b>Light and colorblind palettes</b> (OAX 2014-06-16 19Z observed sounding)</summary>

Both palettes below render the same *different* sounding — the bundled OAX
observed profile — so the theme change is visible independently of the data.
Switch palettes with **File → Preferences** (Standard / Inverted / Protanopia);
the choice persists across launches and applies to every panel and inset.

**Inverted (light mode)**

![SHARPpy Reimagined sounding in the Inverted light palette](docs/images/v0.8.0/sounding-light-mode.png)

**Protanopia (colorblind mode)**

![SHARPpy Reimagined sounding in the Protanopia colorblind palette](docs/images/v0.8.0/sounding-protanopia.png)

</details>

SHARPpy Reimagined is a modernized, standalone fork of
[SHARPpy](https://github.com/sharppy/SHARPpy), focused on packageable Python
3.11–3.13 workflows, Qt6/PySide6 rendering, and reproducible point-sounding
tools.
It keeps the familiar SPC-style skew-T, hodograph, hazard, and derived-parameter
views while adding clean command-line entry points, bundled resources, and a
test-backed decoder/extractor layer.

## Highlights

- Headless PNG rendering for `.npz`, SPC tabular, BUFKIT, PECAN, and WRF-ARW
  text sounding inputs.
- Portable `.npz` point-sounding output from UWyo, the independent IEM RAOB
  archive, ERA5, WRF-ARW, Herbie-backed forecast models, and ECCC GeoMet.
- Resumable multi-point/multi-hour jobs with one download per shared model hour,
  bounded concurrency, atomic outputs, and a checksummed versioned manifest.
- Qt6/PySide6 compatibility shims around the upstream SHARPpy widget stack.
- A supported Rust-primary numerical and point-decoding backend, with an
  independently optimized Python fallback and equivalence coverage across all
  13 configured public forecast models.
- A complete inverted/light sounding palette shared by the interactive GUI and
  headless renderer, including contrast-aware labels and derived displays.
- A compact `TOI` row embedded in the composite-index block. Live HRRR guidance
  uses a versioned, non-official public-method reconstruction. The row shows the
  experimental 0-5 score marked `hypothetical`, e.g. `TOI = 4.2 hypothetical`,
  and shows a *probability* only when a
  calibration artifact that passed the promotion gate is selected; missing
  regional inputs remain `TOI = --`. That gate is measured, not stylistic: on a
  337-case archive the shipped probability transform scored a Brier skill of
  -0.561 against climatology, and its 77% bin verified at 7.3%. Click the TOI row
  to inspect every regional input, score component, version, measured skill,
  limitation, and provenance field.
- Offline UWyo station catalog plus package-relative bundled fonts.
- Property-based pytest coverage for decoders, derived parameters, hazards,
  renderer-facing widgets, and extraction paths.

## Quick Start

Requires Python 3.11, 3.12, or 3.13.

```bash
python scripts/install_sharppy_compat.py

sharpmod-render examples/soundings/hrrr_point_36.68N_95.66W_f018.npz out.png
```

`sharpmod-render` writes a 2x HD PNG by default; add `--uhd` for the larger
2.8x export or `--lossless` for the original-size compact/lossless PNG.

The installer hash-verifies the official `SHARPpy==1.4.0a5` wheel, corrects
only its obsolete `numpy==1.15.*` requirement to this project's supported
range, records that provenance, installs the editable project plus render
stack, and requires `pip check` to pass. Use `--source-wheel PATH` for an
offline copy of the exact pinned upstream wheel.

### Rust-primary backend

Official Windows executables bundle the supported `sharpmod_rs` extension,
and the default `auto` mode uses Rust after validating its package version,
backend API, and required operations. The independently optimized Python
implementation remains a fully functional portable fallback, so source and
Python-only installations do not require Rust, Cargo, maturin, or a native
extension to run.

The native API accelerates standard kinematics, SB/MU/ML parcel summaries,
traced surface/forecast/MU/ML/effective and user parcel ascents, DCAPE, and
direct pressure-level GRIB point decoding. The GUI continues to expose
SHARPpy-compatible profile and parcel objects, with automatic Python-oracle
fallback if a native operation is unavailable.

To add the Rust backend to a source installation, first install a stable Rust
toolchain (Rust 1.88 or newer), then run these commands in the same Python
environment as `sharpmod`:

```bash
python -m pip install -e ".[rust-build]"
sharpmod-rust-sync
sharpmod-rust-sync --check
```

`sharpmod-rust-sync` rebuilds only when `sharpmod_rs` is missing or its version
does not match this checkout, then verifies forced-Rust selection in a fresh
Python process. `--check` is non-mutating; use `--force` after editing native
source. Extension developers can still run `maturin develop --release --locked`
directly from `rust/sharpmod-rs`.

Select the backend with `SHARPMOD_BACKEND` before starting the application:

| Value | Behavior |
| --- | --- |
| `auto` | Default. Use Rust when it loads; otherwise use Python and record the fallback reason. |
| `python` | Require the optimized Python implementation. |
| `rust` | Require the Rust extension; report an error if it is unavailable or cannot load. |

Check the resolved backend without running a sounding workflow:

```bash
python -c "from sharpmod.backends import backend_info; print(backend_info())"
```

Rust is the supported primary backend when a compatible extension is present;
official Windows binaries include it. Standalone native wheels are still
CI/build artifacts rather than a separately published Python package. See the
[Rust backend guide](docs/RUST_BACKEND.md) for source-build instructions,
fallback behavior, platform status, limitations, tests, and benchmarks.

### Forecast-decoder performance and validation

Version 0.8.0 includes the two independently optimized GRIB implementations
introduced in v0.4.0. The Python backend reuses a file inventory and
nearest-point selection, reads only the required scalar fields, and keeps
bounded inventory, point-selection, and decoded-sounding caches. The Rust
backend memory-maps each local subset,
iterates ecCodes messages without copying the GRIB payload, and returns one
NumPy-compatible matrix through a single Python call. Neither implementation
requires speculative parallel decoding.

Both decoders understand GRIB multi-field messages in which one physical
message contains separate U- and V-wind fields. Their equivalence preflight
checks selected grid coordinates, pressure ordering, missing masks, and values
within field-appropriate floating-point tolerances. The matrix covers HRRR,
RAP, NAM, NAM 3 km, HRW WRF-ARW, HRW FV3, RRFS-A, GFS, AIGFS, CFS, ECMWF IFS,
ECMWF-AIFS, and GEFS.

Products without a published relative- or absolute-vorticity field can retain
the full xarray compatibility path in the production extractor so the
neighbor-wind vorticity estimate is preserved; their direct point decoder is
still measured separately and is not an end-to-end timing of that production
route. Optional fields published at a single pressure,
such as GEFS omega, are aligned only to that pressure and remain missing at
other levels instead of being broadcast through the sounding. Optimized Python
and Rust return matching pressure-aligned omega values and missing masks. The
old/new GEFS benchmark difference records the correction of the frozen legacy
xarray full-column broadcast; it is not a Rust availability gap.

See the [all-model benchmark table](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.md),
its [raw JSON record](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.json),
and the [benchmark methodology](benchmarks/README.md). Network transfer is
excluded from decoder timings, and the JSON retains fixture hashes, raw
samples, selected coordinates, build fingerprints, and equivalence results.

## Desktop GUI

An interactive, legacy-SHARPpy-style desktop app is included:

```bash
sharpmod-gui          # or: python -m sharpmod.gui
```

On Windows, source-checkout GUI runs use Python 3.11-3.13. If this command is
invoked by Python 3.14 and the checkout has a `.venv` or `.gribenv`, the launcher
automatically hands the GUI to that compatible environment before Qt starts.
The packaged Windows release already bundles Python 3.11.

The **Sounding Picker** opens with five ways to load a sounding:

- **Station Map** — a clickable map of every UWyo radiosonde station over a
  coastline basemap. Click a dot to select, double-click to open; scroll to
  zoom, drag to pan, and pick a region from the *Map area* menu. Observation
  times are selectable every three hours from 00Z through 21Z.
- **Station List** — the full catalogue with live id/name filtering and the
  same three-hourly UTC observation-time choices.
- **Forecast Model** — click a point or enter latitude/longitude, then choose a
  public model, UTC run, forecast hour, and optional ensemble member. The picker
  checks that inventory in the background. If publication is delayed, it offers
  the newest available earlier cycle without silently changing the selection;
  an uncertain check never disables manual Fetch. The fetch runs in the
  background and opens the point sounding in the SPC window. **Timeline…**
  queues a selected range of as many as 72 hours into one viewer with a
  slider, playback, step, and loop controls; completed hours remain available
  after cancellation or a missing hour.
- **Reanalysis (ERA5)** — choose any global point and hourly UTC analysis. The
  picker previews the snapped 0.25-degree grid point, validates the optional
  packages/CDS profile, caches completed point-hours, and keeps Qt responsive
  while the synchronous CDS request runs in a worker.
- **Open File** — a local `.npz`, SPC, BUFKIT, PECAN, or WRF-ARW text sounding
  (or just drag the file onto the window). Its **Raw WRF wrfout** workflow
  inspects a NetCDF domain/times in the background, validates a map point
  against the actual curvilinear grid perimeter, then extracts and opens it.

Each sounding opens in the full interactive SPC window (the upstream SHARPpy
widget stack), so every interaction from the
[SHARPpy GUI guide](https://sharppy.github.io/SHARPpy/interacting_gui.html)
works:

- **Right-click the Skew-T** for the readout cursor, *Modify Surface*, parcel
  lifting, and reset.
- **Click + drag** temperature / dewpoint / wind points to edit the profile —
  every index recalculates live.
- The hodograph defaults to **Mean Wind** centering with a 20%-tighter viewport.
  **Mouse wheel** zooms; **right-click the hodograph** selects Mean Wind,
  Normal, or Storm Relative centering, and **double-clicking** the RM/LM markers
  sets the storm motion. The active profile has colored dots with 0.5, 1, 3, 6,
  9, and 12 inside them, and the locator inset names the active sounding
  location/town in its title.
- **Double-click the lower-left inset** to swap lifted parcels.
- **Keys:** ← / → step in time, ↑ / ↓ change ensemble member, `Space` swaps
  focus, `I` interpolates, `C` collects observed, `W` returns to the picker.
- **Undo / Redo:** `Ctrl+Z` reverses profile, interpolation, and storm-motion
  edits; `Ctrl+Y` reapplies them. Each viewer retains the latest 50 edits.
- **Data → Source & Quality Inspector…** shows the provider/source route,
  backend and decoder, cache status, level and missing-field counts, surface
  vorticity provenance, and non-mutating QC warnings for the focused profile.
- **File → Preferences** switches the color palette (Standard / Inverted /
  Protanopia), units, and the parcel visualized by default when a Skew-T opens.

The picker also provides **File → Downloaded Data Library…** to browse,
validate, reopen/re-extract, pin, delete, or copy provenance for cached model
data. **Locations → Manage Saved Locations…** stores searchable named points,
supports versioned JSON import/export, and displays saved and recent points as
map markers. Only labels and coordinates are persisted.

GUI choices persist across launches, including temperature/wind/PWAT units,
palette, top/bottom readouts, default parcel, multi-sounding behavior, dismissed
tips, recent files, and last selections. On Windows they are stored in
`%APPDATA%\SHARPpy Reimagined\settings.ini`; set `SHARPMOD_SETTINGS_PATH` to
use a different INI file.

The ERA5 and raw-WRF source panels expose **Add to active sounding window**
directly. Leave it enabled, change the point or available time, and fetch again
to overlay several soundings; it stays synchronized with **File → Add New
Soundings to Active Window**.

Forecast, ERA5, and raw-WRF points accept an optional **Location/town** label.
Named saved locations populate it directly. When it is blank, a bundled
52,818-entry U.S. Census place/town index resolves and persistently caches the
nearest title across the contiguous United States and D.C.; state polygons
prevent nearby Canadian, Mexican, Atlantic, and Gulf points from receiving a
U.S. name. The resulting name appears above the hodograph locator-map inset.
A rate-limited OpenStreetMap Nominatim request is only a fallback when the
bundled search has no result. Entering a label skips lookup entirely, and
`SHARPMOD_GEOCODER_URL=off` disables that fallback. Headless rendering resolves
generic labels such as `HRRR 41.53N 88.39W` through the same path. Names are
used only for the title; no town labels are drawn inside the locator map. See
the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
and [OpenStreetMap attribution](https://www.openstreetmap.org/copyright), plus
the [offline CONUS index notes](docs/CONUS_PLACE_INDEX.md). The locator's map
context comes from separately bundled, one-degree Census county-outline tiles;
it performs no live map request and loads only tiles around the sounding.

The **Inverted** palette is a complete light theme. Applying it updates the
Skew-T, hodograph, locator, storm slinky, inset products, IndexBoard, and
Streamwiseness panels in one live configuration change. Theme-dependent text,
rules, legends, and semantic annotations are contrast-adjusted for a light
canvas while plotted scientific values, units, and established dark-theme
colors remain unchanged. Headless rendering uses the same selected palette.

### Analysis sessions

Use **File → Save Analysis Session…** (`Ctrl+Shift+E`) in a sounding window to
save every loaded sounding, the active profile, current profile/interpolation/
storm-motion edits, parcel selection, and viewer state. **Open Analysis
Session…** (`Ctrl+Shift+O`) is available from both the picker and sounding
window and restores the saved soundings together in one viewer.

Session files use the `.sharpmod-session` extension and a versioned, portable
JSON format; they do not execute code or embed source GRIB downloads. Forecast
download directories still follow the normal lifecycle and are deleted when
their original viewer closes.

### Export

The sounding window's **Export** menu saves the current view:

- **Export Image (HD PNG)** (`Ctrl+E`) — a 2x high-density image of the full
  window, including the mounted derived-parameter panels, with a sensible
  default filename (`STATION_YYYYMMDDHHZ_hd.png`) in your Desktop folder.
- **Export Image (UHD PNG)** — a larger 2.8x ultra-high-density image
  (`STATION_YYYYMMDDHHZ_uhd.png`).
- **Export Image (Lossless PNG)** — the original-size compact/lossless image
  for smaller files (`STATION_YYYYMMDDHHZ_lossless.png`).
- **Copy Image to Clipboard** (`Ctrl+Shift+C`) — the same current view, ready
  to paste into another app.
- **Export Text (SHARPpy)** — the focused profile as a text file that loads
  back into the app.

(The upstream `File → Save Image` / `Save Text` actions remain available too.)

External Windows automation can use
`pwsh -NoProfile -File scripts/copy-image-to-clipboard.ps1 IMAGE.png` instead
of embedding `DataObject`, `StringCollection`, and `System.Drawing.Image`
construction inside a quote-sensitive inline `pwsh -Command` string.

### Standalone executable (Windows)

A one-folder, no-Python-required build is produced with PyInstaller:

```bash
python scripts/install_sharppy_compat.py --extras render,era5,wrf
python -m pip install pyinstaller
pyinstaller packaging/sharpmod_gui.spec --noconfirm
```

The result is `dist/SHARPpy-Reimagined/SHARPpy-Reimagined.exe`. Set
`SHARPMOD_ONEFILE=1` in the build environment for a single self-extracting
`dist/SHARPpy-Reimagined.exe` instead.
The official release workflow builds and installs `sharpmod_rs` before
PyInstaller packages the executable, making Rust the `auto` backend in the
published application. For custom local builds, the spec collects a compatible
installed extension when present; otherwise it logs a warning and produces a
fully functional Python-fallback bundle.
Official releases first run the reusable test workflow against the exact source
commit, build with the direct dependency versions in
`constraints/release.txt`, and publish from a separate artifact-only job. Only
that final job receives GitHub `contents: write` permission.

## Command Line Tools

| Command | Purpose |
| --- | --- |
| `sharpmod-render` | Render a sounding file to a PNG |
| `uwyo-sounding` | List, search, and fetch University of Wyoming soundings |
| `observed-sounding` | Fetch from UWyo with an explicit IEM RAOB fallback |
| `era5-extract` | Extract an ERA5 point sounding to `.npz` |
| `model-extract` | Fetch all pressure levels for a supported forecast-model point sounding |
| `model-batch-extract` | Run a resumable multi-point/multi-hour model job |
| `wrf-extract` | Extract a WRF-ARW point sounding to `.npz` |
| `sharpmod-guidance` | Build, collect, verify, compile, train, and evaluate the experimental TOI calibration programme |
| `sharpmod-rust-sync` | Check, rebuild when needed, and verify the local Rust backend |

### Forecast-model extraction (`model-extract`)

Install the GRIB stack before fetching model data. Add the render stack and the
upstream SHARPpy runtime when `--render` is needed:

```bash
# Extraction only
python -m pip install -e ".[era5]"

# Extraction plus PNG rendering
python scripts/install_sharppy_compat.py --extras era5,render
```

Discover the installed CLI and check remote inventory before a large fetch:

```bash
model-extract --help
model-extract --list
model-extract gfs --probe --fxx 0

# Also download and open the pressure-level subset during the probe
model-extract gfs --probe --fxx 0 --open-subset

# Check recent completed cycles and fail until the verified ground contract
# is complete (useful for provider monitoring)
model-extract rrfs-a --probe --lookback-cycles 12 --require-surface-contract
```

Fetch a point sounding by model key, latitude, and longitude:

```bash
# Keep the portable .npz and its .json metadata sidecar
model-extract gfs 35.18 -97.44 gfs_oun.npz --fxx 0 --loc "Norman, OK"

# Select an exact UTC cycle and forecast hour
model-extract gfs 35.18 -97.44 gfs_oun_f006.npz --run "2026-07-14 00:00" --fxx 6

# Render to a named PNG; fetched GRIB/.npz/.json data is removed afterward
model-extract hrrr 35.18 -97.44 --fxx 0 --render hrrr_oun.png

# Omit the PNG name to use the generated point-sounding filename stem
model-extract hrrr 35.18 -97.44 --fxx 0 --render

# Select an ensemble member (GEFS defaults to c00)
model-extract gefs 35.18 -97.44 gefs_p01.npz --fxx 0 --member p01
```

Live HRRR extraction can optionally sample the applicable 18-hour window every
three hours, normally seven compact regional `sfc` subsets of roughly 8-11 MiB
each (eight when the requested forecast hour is off-interval), and embeds an
experimental TOI score and probability in the JSON sidecar. Every decoded frame
is used in valid-time order. Partial sampling still yields TOI when at least two
frames span nine hours or more, marked `degraded` in provenance; otherwise TOI
is unavailable with the exact reason.
It uses 300-hPa jet motion during June-August, 500 hPa otherwise, and
a transparently labeled fixed-layer STP proxy. Its scorecard follows the public
SPC bins and qualitative rules, while its probability transform is anchored to
the public 4.35/87% example. Because SPC did not publish its exact weights or
calibration equation, both are explicitly marked non-official and versioned.
The regional-guidance payload contains only TOI. It is off by default so the
extra frames do not delay the requested sounding. Pass `--regional-guidance`
(or set `SHARPMOD_REGIONAL_GUIDANCE=on`) to opt in.

If `--run` is omitted, the CLI chooses the most recent configured cycle at or
before the current UTC time; upstream publication can lag that cycle, so use
`--probe`, use `--lookback-cycles`, or pass an earlier `--run` when inventory is
not available. `--require-surface-contract` makes a probe fail until surface
pressure/height, 2 m thermodynamics, and both 10 m wind components are present,
and lists the missing components. Without `--render`, the `.npz` and `.json`
outputs remain. With `--render`, only the PNG remains. The GUI instead retains
fetched files until the sounding window closes.

#### Download acceleration and cache

The extractor keeps every pressure level published by the selected model while
avoiding fields that are duplicates for sounding construction. It tries the
smallest compatible route first:

Every forecast extraction requires true surface pressure and terrain height,
2-m temperature/moisture, and 10-m wind. Isobaric records whose pressure
exceeds the selected point's surface pressure are discarded, then the verified
ground row is prepended. ERA5 retrieves the matching single-level fields in a
second colocated CDS request. A provider that does not publish the complete
surface contract fails explicitly instead of emitting a pressure-only profile.
The completed profile must also pass monotonic-pressure/height, thermodynamic,
and wind quality checks before it is written.

1. HRRR F000 analyses use direct point reads from the public HRRR Zarr archive
   and normalize those columns straight into the compact decoder contract;
   Canadian GDPS/RDPS query their six surface layers first, then request only
   pressure layers above that ground pressure with bounded GeoMet fan-out.
2. Indexed subsets at or below 32 MiB use validated, coalesced HTTP byte ranges
   after selecting a healthy equivalent provider. Every indexed model uses up
   to four bounded range workers by default. Large coalesced spans are split
   into balanced fragments, with one session per worker, pinned object
   identity, ordered atomic assembly, resumable fragments, and an automatic
   sequential-range fallback. Live
   [RRFS](benchmarks/results/2026-07-22-rrfs-range-workers.md) and
   [all-model transport/decode](benchmarks/results/2026-07-22-all-model-fetch-decode-optimization.md)
   records retain timing and byte-equivalence evidence for that default.
3. Larger HRRR, RAP, NAM, NAM 3 km, HRW WRF-ARW/FV3, GFS, CFS, and GEFS
   transfers use a small NOAA NOMADS geographic subset; other indexed products
   retain the range route.
4. Any unavailable or incompatible optimization falls back automatically to
   the standard Herbie download path.

Local GRIB files decode directly into compact NumPy columns. Products without
a pressure-level vorticity field use a four-neighbor U/V stencil read directly
from two GRIB messages instead of opening xarray wind cubes. Multi-point batch
jobs vectorize both the sounding columns and those stencils, so each selected
message is unpacked once for all requested points; this is vectorized I/O, not
unsafe decoder threading.

The GUI keeps downloaded model hours under
`%LOCALAPPDATA%\sharpmod\model-cache` on Windows (or the platform cache folder),
up to 3 GB and 48 hours by default. In the File menu, **Prefetch Next Forecast
Hour** optionally warms the next valid hour, **Clear Downloaded Model Cache**
removes retained entries, and the model tab's **Cancel** button stops the active
request. Verified partial files from compatible range downloads are retained so
the same request can resume. Cache paths and metadata carry a contract version;
payloads produced by an older extraction contract remain visible in the data
library but are never reopened as current soundings.

Advanced overrides are available for testing or constrained environments:

| Environment variable | Default | Effect |
| --- | --- | --- |
| `SHARPMOD_HRRR_BACKEND` | `auto` | `auto`, `zarr`, or `grib` for HRRR F000 |
| `SHARPMOD_POINT_BACKENDS` | `auto` | Set to `grib` to bypass point/subregion routes |
| `SHARPMOD_GRIB_DECODER` | `auto` | `auto` uses direct point decoding with xarray fallback; `direct` requires it; `xarray` forces the compatibility path |
| `SHARPMOD_PROVIDER_RACING` | `1` | Set to `0` to disable equivalent-provider probes |
| `SHARPMOD_RANGE_WORKERS` | `4` | HTTP range-request workers for indexed models, clamped to 1-8; decoder execution remains serial |
| `SHARPMOD_GEOMET_WORKERS` | `4` | Concurrent ECCC GeoMet layer-point requests, clamped to 1-8 |
| `SHARPMOD_MODEL_CACHE` | platform cache | Override the GUI model-cache directory |
| `SHARPMOD_MODEL_CACHE_GB` | `3` | Maximum retained cache size in GiB |
| `SHARPMOD_MODEL_CACHE_HOURS` | `48` | Maximum retained entry age |

#### Batch and multi-point extraction

`model-batch-extract` accepts heterogeneous points and forecast hours. Requests
with the same model, UTC run, forecast hour, and member share one decoded
model-hour lease, while different hours run with a bounded 1-4 worker pool.
Single-point hours retain the normal point/subregion route and its GUI-compatible
spatial cache key; multi-point hours fetch one reusable field subset and decode
all local-GRIB points with vector element reads.
Every `.npz` and `.json` sidecar is atomic. The checksummed manifest is also
atomic, so rerunning the same command validates and skips completed outputs.

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

All point and batch extraction paths **skip** the experimental HRRR regional
TOI guidance by default because it costs roughly 60-85 MiB and tens of seconds
per point. Add `--regional-guidance` to opt a CLI job in; Python callers use
`live_regional_guidance=True`.

The Python API is `sharpmod.batch_extract.run_batch(...)`; it accepts ordered
`BatchRequest` values and returns ordered per-request results plus completed
NPZ paths. Call `BatchExtractor.cancel()` for cooperative cancellation.
Pass an existing `ModelHourCache` as `model_hour_cache=` when a GUI or service
owns a longer-lived cache; the batch runner leases it but does not clear it.
`BatchExtractor(live_regional_guidance=True)` is the API equivalent of the flag.

#### Configured models

These are the canonical keys accepted by this checkout. `model-extract --list`
is the runtime source of truth and also reports known models that are not
enabled. Remote run availability still depends on the upstream provider.

| Canonical key | Model / product | Coverage | Configured forecast hours | Aliases / notes |
| --- | --- | --- | --- | --- |
| `hrrr` | HRRR pressure levels | CONUS | 00/06/12/18Z: F000-F048 hourly; other cycles: F000-F018 hourly | — |
| `rap` | RAP 13 km AWIPS pressure levels | CONUS | F000-F051 hourly | — |
| `nam` | NAM 12 km pressure levels | CONUS | F000-F084 every 3 hours | — |
| `nam-3km-conus` | NAM 3 km CONUS nest | CONUS | F000-F060 hourly | `nam3`, `nam-3km` |
| `hrw-wrf-arw` | NOAA HiResW WRF-ARW 5 km | CONUS | F000-F048 hourly | 00/12Z only; `hiresw-arw`, `hrw-arw` |
| `hrw-fv3` | NOAA HiResW FV3 5 km | CONUS | F000-F048 hourly | 00/12Z only; `hiresw-fv3` |
| `gfs` | GFS 0.25-degree pressure levels | Global | F000-F120 hourly, then every 3 hours to F384 | — |
| `cfs` | CFS 6-hourly pressure levels | Global | F000-F384 every 6 hours | Member 1 by default |
| `ecmwf-ifs` | ECMWF IFS Open Data | Global | 00/12Z: F000-F144 every 3 hours, then every 6 hours to F360; 06/18Z short cut-off stops at F144 | `ecmwf`, `ifs` |
| `ecmwf-aifs` | ECMWF-AIFS Open Data | Global | F000-F360 every 6 hours | `aifs` |
| `gefs` | GEFS 0.5-degree pressure levels | Global | F000-F384 every 3 hours | Control member `c00` by default |
| `gdps` | Canadian GDPS 15 km point profile | Global | F000-F240 every 3 hours | 00/12Z; `gem-global`, `cmc-global` |
| `rdps` | Canadian RDPS 10 km point profile | North America / Arctic | F000-F084 hourly | 00/06/12/18Z; `gem-regional`, `cmc-regional` |

Every product in that table was confirmed against live data to return a
sounding with a merged verified surface row. Products that cannot are withheld
from the picker and the CLI rather than offered and then refused; `model-extract
--list` prints them with the measured reason. Two are currently withheld:

| Canonical key | Why it cannot produce a sounding |
| --- | --- |
| `rrfs-a` and its Alaska, Hawaii, and Puerto Rico domains | The published `prslev` index carries pressure levels only, with no surface, 2-m, or 10-m records, and the `natlev`, `testbed`, and `ififip` products are not published. |
| `aigfs` | AIGFS splits pressure and surface products, and its `sfc` product publishes only 2-m temperature, 10-m winds, and mean-sea-level pressure. Surface pressure, terrain height, and 2-m moisture are absent from every AIGFS product. |

```bash
# Observed sounding: try UWyo, then the independent IEM RAOB archive
observed-sounding fetch 72357 "2024-05-20 00" --out oun.npz --render oun.png

# Render the mixed-layer parcel on the Skew-T (MU is the default)
sharpmod-render oun.npz oun_ml.png --parcel ML

# Reanalysis / local WRF point soundings
era5-extract "2024-05-20 00:00" 35.18 -97.44 era5.npz --render
wrf-extract wrfout_d01_2024-05-20_00:00:00 35.18 -97.44 wrf.npz --render

# Canadian point sounding through ECCC GeoMet (no full-grid download)
model-extract gdps 45.50 -73.60 montreal.npz --run "2026-07-22 00" --fxx 6
```

`era5-extract` retrieves all 37 pressure levels from the official Copernicus
Climate Data Store API. Create a free CDS account, accept the ERA5 dataset
licence, and copy the credentials shown on the
[CDS API setup page](https://cds.climate.copernicus.eu/how-to-api) into
`$HOME/.cdsapirc` before the first request. Public forecast models continue to
use Herbie and do not require CDS credentials.

`sharpmod-render --parcel` accepts `SFC`, `ML`, `FCST`, `MU`, `EFF`, and
`USER`. Parcel keys are case-insensitive.

## Install Extras

| Extra | Installs | Use it for |
| --- | --- | --- |
| `[render]` | SHARPpy runtime companions | PNG rendering |
| `[era5]` | CDS API, Herbie, cfgrib, ecCodes, xarray, numcodecs, pyproj | ERA5 and public forecast-model point extraction |
| `[wrf]` | xarray, netCDF4 | WRF-ARW NetCDF extraction |
| `[dev]` | pytest, Hypothesis, pytest-xdist, pytest-timeout, PyYAML | Test and workflow-validation work |
| `[quality]` | Ruff, pip-audit, pytest-cov | Static checks, dependency audit, and coverage |
| `[rust-build]` | maturin | Build the supported Rust backend locally (Rust toolchain installed separately) |

```bash
python scripts/install_sharppy_compat.py --extras dev,quality,era5,wrf,render

# Fast deterministic feedback with bounded, Qt-safe worker grouping.
python scripts/run_test_lane.py fast --workers 4

# Full 100-200-example scientific properties.
python scripts/run_test_lane.py property --workers 4

# Exact non-parallel release gate.
python scripts/run_test_lane.py serial-release

# Optional source-checkout Rust backend
python -m pip install -e ".[rust-build]"
sharpmod-rust-sync
```

For the full setup reference, see [`installation.txt`](installation.txt). For
usage recipes and Python API examples, see [`docs/USAGE.md`](docs/USAGE.md).
Rust-backend setup, fallback behavior, and limitations are documented in
[`docs/RUST_BACKEND.md`](docs/RUST_BACKEND.md).

## Data Flow

```text
UWyo / ERA5 / WRF / public forecast models
                     |
                     v
          portable .npz point sounding
                     |
                     v
              sharpmod-render
                     |
                     v
       SPC-style skew-T + hodograph PNG
```

## Repository Map

```text
sharpmod/
  gui.py        interactive desktop app (sounding picker + SPC window)
  render.py     headless PNG render entry point
  backends/     optimized Python/Rust kernels and direct GRIB point decoders
  sharptab/     derived-parameter and meteorological calculations
  io/           decoders for SPC, BUFKIT, PECAN, WRF-ARW, .npz, and UWyo
  viz/          Qt6/PySide6 rendering widgets
  tools/        UWyo, ERA5, forecast-model, WRF, basemap, and render CLI tools
  resources/    bundled fonts, station catalog, and GUI basemap/icons
  tests/        unit, smoke, and property-based tests

packaging/
  sharpmod_gui.spec   PyInstaller spec for the standalone GUI build

rust/sharpmod-rs/     supported PyO3/maturin backend extension crate
benchmarks/           Python-versus-Rust equivalence-first timing harness

examples/
  example_sounding.png
  soundings/    bundled sample inputs

docs/
  USAGE.md      workflow guide and API examples
  RUST_BACKEND.md  Rust-primary setup, fallback behavior, and limitations
```

## Attribution

This project builds on the abandoned upstream
[SHARPpy](https://github.com/sharppy/SHARPpy) project. See [`LICENSE`](LICENSE)
for license terms and attribution.
