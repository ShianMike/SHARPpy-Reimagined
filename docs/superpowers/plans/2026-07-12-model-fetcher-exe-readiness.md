# Weather-Model Fetcher EXE Readiness Implementation Plan

> **For agentic workers:** Execute inline in the existing checkout. Do not commit, push, or rebuild the release executable unless the user explicitly requests those state changes.

**Goal:** Prove every enabled forecast-model fetcher can reach a live inventory, retrieve pressure-level data, write a decodable point-sounding NPZ, and remain available in the rebuilt Windows executable.

**Architecture:** `sharpmod.tools.model_extract` is the single model registry and extraction path. Live checks run through the existing Python 3.11 `.gribenv`, which has functional ecCodes support; packaging readiness is checked separately because the current PyInstaller specification excludes the GRIB stack. Model-specific failures are fixed in the registry/search configuration and covered by focused tests before another live attempt.

**Tech Stack:** Python 3.11, Herbie, cfgrib, ecCodes, xarray, NumPy, pytest, PyInstaller.

---

### Task 1: Establish the supported-model and runtime baseline

**Files:**
- Inspect: `sharpmod/tools/model_extract.py`
- Inspect: `pyproject.toml`
- Inspect: `packaging/sharpmod_gui.spec`

- [ ] Run `\.gribenv\Scripts\python.exe -m sharpmod.tools.model_extract --list` and record every enabled model.
- [ ] Confirm `.gribenv` imports `herbie`, `cfgrib`, `eccodes`, and `xarray`.
- [ ] Confirm the default runtime result separately so runtime-specific failures are not mistaken for model failures.
- [ ] Record the PyInstaller inclusions/exclusions for the same dependency chain.

### Task 2: Probe live inventories for every enabled model

**Files:**
- Inspect or modify on failure: `sharpmod/tools/model_extract.py`
- Create diagnostic output: `.tmp/model_fetcher_readiness/inventory_results.json`

- [ ] Select a completed synoptic cycle and F000 for all enabled configurations.
- [ ] Call `model_extract.probe(...)` for each model and record the resolved run, product, GRIB URL, index URL, and error.
- [ ] Retry a failed probe once with the model's previous supported cycle before classifying it.
- [ ] Separate unavailable upstream products from invalid local model/product/member configuration.

### Task 3: Perform real point extraction for every model with inventory

**Files:**
- Inspect or modify on failure: `sharpmod/tools/model_extract.py`
- Test: `sharpmod/tests/test_model_extract.py`
- Create artifacts: `.tmp/model_fetcher_readiness/<model>.npz` and `.json`

- [ ] Use `39.756703, -97.192531` for CONUS and global models.
- [ ] Retrieve the configured pressure-level subset and write one NPZ per model.
- [ ] Validate every NPZ has monotonic pressure, temperature, dewpoint, height, wind, model/run/valid metadata, and at least two usable levels.
- [ ] Decode every NPZ through `sharpmod.io.decoder.load_npz`.
- [ ] Record optional-field availability for omega and vorticity without treating their absence as a core-fetch failure.

### Task 4: Repair model-specific failures with focused tests

**Files:**
- Modify only when evidence identifies a defect: `sharpmod/tools/model_extract.py`
- Modify: `sharpmod/tests/test_model_extract.py`

- [ ] Add a failing test that reproduces each confirmed configuration/search/member defect.
- [ ] Run the focused test and confirm the expected failure.
- [ ] Apply the smallest registry or extraction correction.
- [ ] Run the focused test and the complete `test_model_extract.py` file.
- [ ] Repeat that model's live probe and extraction.

### Task 5: Make the PyInstaller build include the working GRIB runtime

**Files:**
- Modify: `packaging/sharpmod_gui.spec`
- Modify if dependency metadata is incomplete: `pyproject.toml`
- Test: `sharpmod/tests/test_packaging_render_smoke.py`

- [ ] Add `herbie`, `cfgrib`, `eccodes`, `eccodeslib` when installed, and `xarray` to PyInstaller collection instead of excluding the fetch stack.
- [ ] Keep test trees and unrelated scientific extras excluded.
- [ ] Run a PyInstaller analysis/build smoke check from `.gribenv` and inspect warnings for missing ecCodes DLLs or definitions.
- [ ] Launch the frozen GUI and perform one live HRRR fetch plus one global-model fetch through the actual model-picker worker.

### Task 6: Final readiness verification

**Files:**
- Create report: `.tmp/model_fetcher_readiness/report.md`

- [ ] Run `python -m pytest sharpmod/tests/test_model_extract.py sharpmod/tests/test_model_data_lifetime.py sharpmod/tests/test_packaging_render_smoke.py -q` in the build environment.
- [ ] Summarize each enabled model as PASS, UPSTREAM UNAVAILABLE, or LOCAL DEFECT with exact evidence.
- [ ] State separately whether source-runtime fetching and rebuilt-EXE fetching are ready.
- [ ] Do not declare EXE readiness unless the frozen application successfully performs live regional and global fetches.
