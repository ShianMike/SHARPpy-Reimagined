# Streamwiseness Inset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SHARPpy-themed 0-6 km streamwiseness profile immediately left of the Effective Layer STP inset while narrowing only the STP allocation enough to make room.

**Architecture:** A focused `sharpmod.viz.streamwiseness` module will own both the decoder-agnostic wind-profile calculation and the Qt inset. `mount_products()` will mount that widget into `grid3` column 3, move the existing STP widget to column 4, and split the former STP width between the two charts. The widget will follow SHARPpy's `setProf`, `setPreferences`, and `setDeviant` conventions so headless and interactive displays share one implementation.

**Tech Stack:** Python 3.14, NumPy masked arrays, qtpy/PySide6, SHARPpy profile objects, pytest.

---

### Task 1: Streamwiseness calculation

**Files:**
- Create: `sharpmod/viz/streamwiseness.py`
- Create: `sharpmod/tests/test_viz_streamwiseness.py`

- [ ] **Step 1: Write the failing pure-streamwise calculation test**

```python
def test_streamwiseness_profile_is_near_100_for_streamwise_circle():
    height = np.arange(0.0, 6000.0 + 500.0, 500.0)
    phase = height / 6000.0 * (np.pi / 2.0)
    prof = SimpleNamespace(
        hght=height,
        u=20.0 * np.cos(phase),
        v=20.0 * np.sin(phase),
        sfc=0,
        srwind=(0.0, 0.0, 0.0, 0.0),
    )
    result = streamwiseness_profile(prof)
    assert result is not None
    assert np.nanmean(result.percent[1:-1]) > 98.0
    assert np.all(result.signed_percent[1:-1] > 0.0)
```

- [ ] **Step 2: Run the test and confirm the missing module failure**

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py::test_streamwiseness_profile_is_near_100_for_streamwise_circle -q`

Expected: collection fails because `sharpmod.viz.streamwiseness` does not exist.

- [ ] **Step 3: Implement the calculation contract**

Implement `StreamwisenessData(height_km, percent, signed_percent)` and `streamwiseness_profile(prof, use_left=False, max_height_m=6000.0, step_m=100.0)`. Clean finite, monotonic `hght/u/v` rows; convert height to AGL and knots to m/s; interpolate onto a 100 m grid; obtain Bunkers motion from `prof.srwind` (`0:2` right, `2:4` left); compute the curl-consistent `omega_h=(-dv/dz, du/dz)`; project it onto the storm-relative unit-wind vector; and return `abs(omega_s)/abs(omega_h)*100` clipped to 0-100 plus its sign. Return `None` for incomplete profiles, invalid motion, less than two usable levels, negligible shear, or a profile shallower than the grid step.

- [ ] **Step 4: Add and run missing-data, range, and anticyclonic tests**

```python
def test_streamwiseness_profile_returns_none_without_storm_motion():
    assert streamwiseness_profile(SimpleNamespace(hght=[0, 1000], u=[1, 2], v=[2, 3])) is None

def test_streamwiseness_profile_is_bounded():
    result = streamwiseness_profile(_representative_profile())
    assert np.all((result.percent >= 0.0) & (result.percent <= 100.0))

def test_streamwiseness_profile_preserves_anticyclonic_sign():
    result = streamwiseness_profile(_circular_profile(clockwise=False))
    assert np.all(result.signed_percent[1:-1] < 0.0)
```

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py -q`

Expected: all calculation tests pass.

### Task 2: SHARPpy-themed Qt inset

**Files:**
- Modify: `sharpmod/viz/streamwiseness.py`
- Modify: `sharpmod/tests/test_viz_streamwiseness.py`

- [ ] **Step 1: Write the failing widget contract test**

```python
def test_plot_streamwiseness_accepts_sharppy_widget_contract(qt_app):
    widget = plotStreamwiseness()
    widget.resize(250, 360)
    widget.setProf(_representative_profile())
    widget.setPreferences(update_gui=True, bg_color="#000000", fg_color="#ffffff")
    widget.setDeviant("left")
    assert widget.use_left is True
    assert widget.data is not None
    assert widget.grab().toImage().isNull() is False
```

- [ ] **Step 2: Run the widget test and confirm it fails before the widget exists**

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py::test_plot_streamwiseness_accepts_sharppy_widget_contract -q`

Expected: import/attribute failure for `plotStreamwiseness`.

- [ ] **Step 3: Implement `plotStreamwiseness`**

Create a `QFrame` with the same black background and cyan one-pixel border as SHARPpy's STP inset. Paint title `STREAMWISENESS`, x range 0-100%, y range 0-6 km AGL, muted dashed grid lines, a mint profile line, translucent red cyclonic and blue anticyclonic fills, and 500 m/1 km/3 km dashed markers with percentage labels. Include compact cyclonic/anticyclonic legend swatches and render `--` when the profile cannot resolve. Rebuild its pixmap in `resizeEvent`, `setProf`, `setPreferences`, and `setDeviant`.

- [ ] **Step 4: Verify chart pixels, labels, and missing state**

Patch `QtGui.QPainter` in the test to capture `drawText` calls, assert the title/axis/legend/marker strings are emitted, and inspect the grabbed RGB image for mint, red-fill, blue-fill, and cyan-border pixels.

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py -q`

Expected: all widget and calculation tests pass.

### Task 3: Mount beside the Effective Layer STP inset

**Files:**
- Modify: `sharpmod/viz/SPCWindow.py`
- Modify: `sharpmod/tests/test_viz_streamwiseness.py`

- [ ] **Step 1: Write the failing composed-layout test**

Compose a real offscreen SHARPpy window with the packaged example sounding, call `mount_products()`, and assert:

```python
assert sw.grid3.indexOf(sw.streamwiseness) >= 0
assert sw.grid3.getItemPosition(sw.grid3.indexOf(sw.streamwiseness))[1] == 3
assert sw.grid3.getItemPosition(sw.grid3.indexOf(sw.right_inset_ob))[1] == 4
assert result.streamwiseness is sw.streamwiseness
assert sw.streamwiseness.width() < sw.index_board.width()
assert sw.right_inset_ob.width() < sw.index_board.width()
```

- [ ] **Step 2: Run the layout test and confirm the absent-widget failure**

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py -k mounted -q`

Expected: `MountResult` and the composed widget have no `streamwiseness` member.

- [ ] **Step 3: Add the widget and split the former STP allocation**

Extend `MountResult` with `streamwiseness`. In `mount_products()`, construct `plotStreamwiseness`, call `setProf(prof)`, register it in `sw.insets` for normal SHARPpy profile/preference refreshes, add it at `grid3 (0, 3)`, remove and re-add the existing Effective Layer STP widget at `grid3 (0, 4)`, and set column stretches to `4, 4, 4, 2, 6`. Store it as `sw.streamwiseness` and include it in `reapply_color_scheme()` discovery. Wrap the chart's deviant selection through the same right/left motion state used by the STP inset.

- [ ] **Step 4: Run layout and existing display tests**

Run: `python -m pytest sharpmod/tests/test_viz_streamwiseness.py sharpmod/tests/test_viz_renderer_display_colors.py sharpmod/tests/test_packaging_render_smoke.py -q`

Expected: all selected tests pass.

### Task 4: End-to-end rendering and visual QA

**Files:**
- Modify only if visual QA exposes a verified clipping/layout defect in: `sharpmod/viz/streamwiseness.py`, `sharpmod/viz/SPCWindow.py`, or `sharpmod/render.py`

- [ ] **Step 1: Render a real profile at lossless and UHD sizes**

Run:

```powershell
python -m sharpmod.render --lossless examples/soundings/hrrr_point_36.68N_95.66W_f018.npz .tmp/streamwiseness-lossless.png
python -m sharpmod.render --uhd examples/soundings/hrrr_point_36.68N_95.66W_f018.npz .tmp/streamwiseness-uhd.png
```

Expected: both commands exit 0 and write non-empty PNGs.

- [ ] **Step 2: Inspect both images**

Confirm the order is IndexBoard, Streamwiseness, Effective Layer STP; the Streamwiseness title, axes, fills, line, markers, and legend are legible; the STP title, category labels, box plots, and probability box remain unclipped; and no existing panel moved vertically.

- [ ] **Step 3: Run final verification**

Run:

```powershell
python -m pytest sharpmod/tests/test_viz_streamwiseness.py sharpmod/tests/test_viz_renderer_display_colors.py sharpmod/tests/test_packaging_render_smoke.py -q
python -m py_compile sharpmod/viz/streamwiseness.py sharpmod/viz/SPCWindow.py
git diff --check -- sharpmod/viz/streamwiseness.py sharpmod/viz/SPCWindow.py sharpmod/tests/test_viz_streamwiseness.py
```

Expected: zero failures, zero syntax errors, and no whitespace errors.
