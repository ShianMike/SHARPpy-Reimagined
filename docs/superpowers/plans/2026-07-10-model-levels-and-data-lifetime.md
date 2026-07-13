# Forecast Model Levels and Data Lifetime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch every available pressure level for every supported forecast model, delete transient model data after headless PNG generation, and retain GUI model data only until its sounding window closes.

**Architecture:** Replace the NOAA mandatory-level filter with an all-isobaric-level filter while leaving the already-complete ECMWF pressure-level filter intact. Put each render or GUI fetch in an isolated temporary data tree; headless rendering removes that tree in a `finally` block, while the GUI binds removal to the viewer window's destruction and enables delete-on-close.

**Tech Stack:** Python 3.11, Herbie/cfgrib/xarray, PySide6 through qtpy, pytest.

---

### Task 1: Lock down all-level retrieval

**Files:**
- Modify: `sharpmod/tools/model_extract.py:36-44`
- Test: `sharpmod/tests/test_model_extract.py`

- [ ] **Step 1: Write the failing search-pattern test**

```python
def test_every_model_search_accepts_non_mandatory_pressure_levels():
    for cfg in model_extract.available_models():
        sample = ":t:975:pl:" if cfg.key.startswith("ecmwf-") else ":TMP:975 mb:"
        assert re.search(cfg.search, sample), cfg.key
```

- [ ] **Step 2: Run the test and confirm the NOAA models fail**

Run: `python -m pytest sharpmod/tests/test_model_extract.py::test_every_model_search_accepts_non_mandatory_pressure_levels -q`

Expected: failure for a NOAA-backed model because `975 mb` is not in the current mandatory-level list.

- [ ] **Step 3: Generalize the NOAA pressure-level search**

```python
NOAA_PRESSURE_SEARCH = (
    r":(HGT|TMP|RH|SPFH|UGRD|VGRD|VVEL|DZDT|ABSV):"
    r"\d+(?:\.\d+)? mb:"
)
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run: `python -m pytest sharpmod/tests/test_model_extract.py::test_every_model_search_accepts_non_mandatory_pressure_levels -q`

Expected: `1 passed`.

### Task 2: Isolate and clean headless render data

**Files:**
- Modify: `sharpmod/tools/model_extract.py:295-320,339-439,500-554`
- Test: `sharpmod/tests/test_model_extract.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_extract_forwards_isolated_download_directory(tmp_path, monkeypatch):
    seen = {}
    def fake_retrieve(config, run_dt, fxx, member=None, download_dir=None):
        seen["download_dir"] = download_dir
        return _dataset(), SimpleNamespace(grib="memory://gfs")
    monkeypatch.setattr(model_extract, "_retrieve_dataset", fake_retrieve)
    model_extract.extract("gfs", 35, -99, out_path=str(tmp_path / "x.npz"),
                          dataset=None, download_dir=str(tmp_path / "cache"))
    assert seen["download_dir"] == str(tmp_path / "cache")

def test_render_mode_removes_npz_json_and_download_tree(...):
    # Stub extraction/rendering, invoke the render lifecycle, and assert the
    # PNG remains while the NPZ, JSON, and Herbie tree no longer exist.
```

- [ ] **Step 2: Run the lifecycle tests and confirm they fail**

Run: `python -m pytest sharpmod/tests/test_model_extract.py -q`

Expected: failures because extraction has no download-directory contract and render mode leaves the NPZ/JSON/GRIB cache behind.

- [ ] **Step 3: Add scoped retrieval and cleanup**

Pass `save_dir=download_dir` to `Herbie.xarray(..., remove_grib=False)` when a directory is supplied. Add a cleanup helper that removes the extracted `.npz`, its `.json` sidecar, and the isolated download tree. In `main`, create the isolated tree only for `--render`, render the PNG, and run cleanup in `finally`, including failure paths.

- [ ] **Step 4: Run the focused model tests**

Run: `python -m pytest sharpmod/tests/test_model_extract.py -q`

Expected: all tests pass.

### Task 3: Tie GUI model data to the sounding window

**Files:**
- Modify: `sharpmod/gui.py:1538-1574,3268-3338,3476-3484`
- Test: `sharpmod/tests/test_model_data_lifetime.py`

- [ ] **Step 1: Write the failing GUI lifetime test**

```python
def test_model_data_is_removed_only_when_viewer_is_destroyed(tmp_path, qapp):
    data_dir = tmp_path / "fetch"
    data_dir.mkdir()
    (data_dir / "sounding.npz").write_bytes(b"data")
    viewer = QWidget()
    gui._retain_model_data_until_close(viewer, str(data_dir))
    assert data_dir.exists()
    viewer.close()
    qapp.processEvents()
    assert not data_dir.exists()
```

- [ ] **Step 2: Run the test and confirm the helper is missing**

Run: `python -m pytest sharpmod/tests/test_model_data_lifetime.py -q`

Expected: failure because the lifecycle helper does not exist.

- [ ] **Step 3: Implement GUI retention**

Create one temporary directory per model request and place both the NPZ/JSON and Herbie subset inside it. Return the viewer from `_show_sounding`, set `Qt.WA_DeleteOnClose`, connect `destroyed` to removal of the request directory, remove the current immediate `finally: os.remove(npz_path)`, and clean immediately on fetch/display failure.

- [ ] **Step 4: Run the GUI lifetime and model tests**

Run: `python -m pytest sharpmod/tests/test_model_data_lifetime.py sharpmod/tests/test_model_extract.py -q`

Expected: all tests pass.

### Task 4: Verify the integrated application

**Files:**
- Inspect: `sharpmod/tools/model_extract.py`
- Inspect: `sharpmod/gui.py`
- Inspect: `sharpmod/tests/test_model_extract.py`
- Inspect: `sharpmod/tests/test_model_data_lifetime.py`

- [ ] **Step 1: Compile the changed modules**

Run: `python -m py_compile sharpmod/tools/model_extract.py sharpmod/gui.py`

Expected: exit code 0.

- [ ] **Step 2: Run focused regressions**

Run: `python -m pytest sharpmod/tests/test_model_extract.py sharpmod/tests/test_model_data_lifetime.py sharpmod/tests/test_packaging_render_smoke.py -q`

Expected: all focused tests pass.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`

Expected: zero failures.

- [ ] **Step 4: Inspect the final diff and status**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; only intended new edits plus the user's pre-existing work remain.
