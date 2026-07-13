<div align="center">

# SHARPpy Reimagined

**Modern sounding analysis and SHARPpy-style rendering for Python 3.11+.**

[![Tests](https://github.com/ShianMike/SHARPpy-Reimagined/actions/workflows/tests.yml/badge.svg)](https://github.com/ShianMike/SHARPpy-Reimagined/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Qt6](https://img.shields.io/badge/Qt6-PySide6-41CD52?logo=qt&logoColor=white)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)

</div>

![Example SHARPpy Reimagined sounding](examples/example_sounding.png)

SHARPpy Reimagined is a modernized, standalone fork of
[SHARPpy](https://github.com/sharppy/SHARPpy), focused on packageable Python
3.11+ workflows, Qt6/PySide6 rendering, and reproducible point-sounding tools.
It keeps the familiar SPC-style skew-T, hodograph, hazard, and derived-parameter
views while adding clean command-line entry points, bundled resources, and a
test-backed decoder/extractor layer.

## Highlights

- Headless PNG rendering for `.npz`, SPC tabular, BUFKIT, PECAN, and WRF-ARW
  text sounding inputs.
- Portable `.npz` point-sounding output from UWyo, ERA5, WRF-ARW, and bundled
  model examples.
- Qt6/PySide6 compatibility shims around the upstream SHARPpy widget stack.
- Offline UWyo station catalog plus package-relative bundled fonts.
- Property-based pytest coverage for decoders, derived parameters, hazards,
  renderer-facing widgets, and extraction paths.

## Quick Start

Requires Python 3.11 or newer.

```bash
python -m pip install -e ".[render]"
python -m pip install --no-deps "SHARPpy==1.4.0a5"

sharpmod-render examples/soundings/hrrr_point_36.68N_95.66W_f018.npz out.png
```

`sharpmod-render` writes a 2x HD PNG by default; add `--uhd` for the larger
2.8x export or `--lossless` for the original-size compact/lossless PNG.

The upstream `SHARPpy==1.4.0a5` package is installed with `--no-deps` because
its published metadata pins an old NumPy version. SHARPpy Reimagined provides
the modern runtime dependencies separately.

## Desktop GUI

An interactive, legacy-SHARPpy-style desktop app is included:

```bash
sharpmod-gui          # or: python -m sharpmod.gui
```

The **Sounding Picker** opens with three ways to load a sounding:

- **Station Map** — a clickable map of every UWyo radiosonde station over a
  coastline basemap. Click a dot to select, double-click to open; scroll to
  zoom, drag to pan, and pick a region from the *Map area* menu.
- **Station List** — the full catalogue with live id/name filtering.
- **Open File** — a local `.npz`, SPC, BUFKIT, PECAN, or WRF-ARW text sounding
  (or just drag the file onto the window).

Each sounding opens in the full interactive SPC window (the upstream SHARPpy
widget stack), so every interaction from the
[SHARPpy GUI guide](https://sharppy.github.io/SHARPpy/interacting_gui.html)
works:

- **Right-click the Skew-T** for the readout cursor, *Modify Surface*, parcel
  lifting, and reset.
- **Click + drag** temperature / dewpoint / wind points to edit the profile —
  every index recalculates live.
- **Mouse wheel** zooms; **right-click the hodograph** re-centers it, and
  **double-clicking** the RM/LM markers sets the storm motion.
- **Double-click the lower-left inset** to swap lifted parcels.
- **Keys:** ← / → step in time, ↑ / ↓ change ensemble member, `Space` swaps
  focus, `I` interpolates, `C` collects observed, `W` returns to the picker.
- **File → Preferences** switches the color palette (Standard / Inverted /
  Protanopia), units, and the parcel visualized by default when a Skew-T opens.

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

### Standalone executable (Windows)

A one-folder, no-Python-required build is produced with PyInstaller:

```bash
python -m pip install pyinstaller
pyinstaller packaging/sharpmod_gui.spec --noconfirm
```

The result is `dist/SHARPpy-Reimagined/SHARPpy-Reimagined.exe`. Set
`ONEFILE = True` in the spec for a single self-extracting `.exe` instead.

## Command Line Tools

| Command | Purpose |
| --- | --- |
| `sharpmod-render` | Render a sounding file to a PNG |
| `uwyo-sounding` | List, search, and fetch University of Wyoming soundings |
| `era5-extract` | Extract an ERA5 point sounding to `.npz` |
| `model-extract` | Fetch all pressure levels for a supported forecast-model point sounding |
| `wrf-extract` | Extract a WRF-ARW point sounding to `.npz` |

```bash
# Observed sounding: fetch Norman, OK at 00Z and render it
uwyo-sounding fetch 72357 "2024-05-20 00" --out oun.npz --render oun.png

# Render the mixed-layer parcel on the Skew-T (MU is the default)
sharpmod-render oun.npz oun_ml.png --parcel ML

# Reanalysis / model point soundings
era5-extract "2024-05-20 00:00" 35.18 -97.44 era5.npz --render
model-extract gfs 35.18 -97.44 --run "2024-05-20 00:00" --fxx 6 --render gfs.png
wrf-extract wrfout_d01_2024-05-20_00:00:00 35.18 -97.44 wrf.npz --render
```

`model-extract --render` keeps only the rendered PNG: its downloaded GRIB
subset and transient `.npz`/`.json` files are removed after rendering. The GUI
retains those files while the sounding window is open and removes them when the
window closes.

`sharpmod-render --parcel` accepts `SFC`, `ML`, `FCST`, `MU`, `EFF`, and
`USER`. Parcel keys are case-insensitive.

## Install Extras

| Extra | Installs | Use it for |
| --- | --- | --- |
| `[render]` | SHARPpy runtime companions | PNG rendering |
| `[era5]` | Herbie, cfgrib, xarray | ERA5 point extraction |
| `[wrf]` | xarray, netCDF4 | WRF-ARW NetCDF extraction |
| `[dev]` | pytest, Hypothesis | Test and development work |

```bash
python -m pip install -e ".[dev,era5,wrf,render]"
python -m pip install --no-deps "SHARPpy==1.4.0a5"
pytest
```

For the full setup reference, see [`installation.txt`](installation.txt). For
usage recipes and Python API examples, see [`docs/USAGE.md`](docs/USAGE.md).

## Data Flow

```text
UWyo / ERA5 / WRF / HRRR
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
  sharptab/     derived-parameter and meteorological calculations
  io/           decoders for SPC, BUFKIT, PECAN, WRF-ARW, .npz, and UWyo
  viz/          Qt6/PySide6 rendering widgets
  tools/        UWyo, ERA5, WRF, basemap, and render command-line tools
  resources/    bundled fonts, station catalog, and GUI basemap/icons
  tests/        unit, smoke, and property-based tests

packaging/
  sharpmod_gui.spec   PyInstaller spec for the standalone GUI build

examples/
  example_sounding.png
  soundings/    bundled sample inputs

docs/
  USAGE.md      workflow guide and API examples
```

## Attribution

This project builds on the abandoned upstream
[SHARPpy](https://github.com/sharppy/SHARPpy) project. See [`LICENSE`](LICENSE)
for license terms and attribution.
