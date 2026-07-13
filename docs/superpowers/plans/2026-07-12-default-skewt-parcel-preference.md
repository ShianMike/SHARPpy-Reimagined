# Default Skew-T Parcel Preference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let GUI users choose which parcel is visualized by default on the Skew-T and persist that choice across launches.

**Architecture:** Extend the existing SHARPpy Preferences dialog with a Parcel tab while storing the app-specific choice in the GUI's existing `QSettings`. A focused helper applies the saved parcel through `SPCWidget.updateParcel`, preserving SHARPpy's normal Skew-T and storm-slinky update path. The preference is applied once when a sounding window is composed and immediately to open viewers when the user accepts a changed preference; later manual parcel clicks remain window-local overrides.

**Tech Stack:** Python 3.14, qtpy/PySide6, SHARPpy `SPCWidget`, `QSettings`, pytest.

---

### Task 1: Default-parcel preference helpers

**Files:**
- Modify: `sharpmod/gui.py`
- Create: `sharpmod/tests/test_gui_default_parcel.py`

- [x] **Step 1: Write failing normalization and application tests**

Add tests proving that `MU` remains the fallback, all six parcel keys are accepted, and `_apply_default_parcel_to_window()` routes the selected object through `updateParcel()` while synchronizing the legacy parcel-row index when possible.

- [x] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest sharpmod/tests/test_gui_default_parcel.py -q`

Expected: collection fails because the new helpers do not exist.

- [x] **Step 3: Implement the minimal helpers**

Add `_normalize_default_parcel()`, `_add_default_parcel_tab()`, and `_apply_default_parcel_to_window()`. Use `_PARCEL_OPTIONS` as the single source for labels and keys, and return safely when a window or parcel is unavailable.

- [x] **Step 4: Run the focused helper tests**

Run: `python -m pytest sharpmod/tests/test_gui_default_parcel.py -q`

Expected: all tests pass.

### Task 2: Persist and apply the GUI preference

**Files:**
- Modify: `sharpmod/gui.py`
- Modify: `sharpmod/tests/test_gui_default_parcel.py`

- [x] **Step 1: Add controller persistence tests**

Exercise the `PickerWindow` preference accessors with a temporary `QSettings` store and verify invalid saved values normalize to `MU`.

- [x] **Step 2: Wire the existing Preferences dialog**

Add a Parcel tab to `PrefDialog`, save `parcel/default_skewt` only when the dialog is accepted, and apply the selected parcel to every currently open viewer.

- [x] **Step 3: Apply the saved default during composition**

After parcel-selector wiring in `compose_interactive()`, call `_apply_default_parcel_to_window(win, controller._default_parcel())` so the initial Skew-T and storm slinky use the saved choice.

- [x] **Step 4: Verify focused and adjacent GUI behavior**

Run: `python -m pytest sharpmod/tests/test_gui_default_parcel.py sharpmod/tests/test_viz_renderer_display_colors.py -q`

Expected: all tests pass.

- [x] **Step 5: Compile the changed modules**

Run: `python -m py_compile sharpmod/gui.py sharpmod/tests/test_gui_default_parcel.py`

Expected: command exits successfully with no output.
