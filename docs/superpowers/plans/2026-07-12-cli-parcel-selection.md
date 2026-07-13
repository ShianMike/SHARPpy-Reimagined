# CLI Parcel Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `sharpmod-render` select the parcel visualized on the rendered Skew-T with `--parcel`.

**Architecture:** `sharpmod.render` will expose one canonical tuple of parcel keys and a small window-selection helper that uses SHARPpy's existing `SPCWidget.updateParcel()` path. The public `render()` API receives a parcel keyword, applies it after composing the sounding and before capture, and the CLI parser validates and forwards `--parcel`. The default remains `MU`, preserving current output.

**Tech Stack:** Python 3.14, argparse, SHARPpy `SPCWidget`, qtpy/PySide6, pytest.

---

### Task 1: Parser and render contract

**Files:**
- Modify: `sharpmod/render.py`
- Modify: `sharpmod/tests/test_packaging_render_smoke.py`

- [x] **Step 1: Write failing CLI forwarding tests**

Extend the existing fake-render CLI test to record `parcel`, assert omitted `--parcel` forwards `MU`, and assert `--parcel ML` forwards `ML` together with the selected image mode.

- [x] **Step 2: Run the focused CLI test and confirm failure**

Run: `python -m pytest sharpmod/tests/test_packaging_render_smoke.py::test_render_cli_defaults_to_hd_and_accepts_lossless -q`

Expected: FAIL because the fake renderer receives no `parcel` keyword.

- [x] **Step 3: Add the validated CLI option**

Define `PARCEL_TYPES = ("SFC", "ML", "FCST", "MU", "EFF", "USER")`, add `--parcel` with `type=str.upper`, those choices, and default `MU`, then pass `ns.parcel` into `render()`.

- [x] **Step 4: Run parser tests**

Run: `python -m pytest sharpmod/tests/test_packaging_render_smoke.py::test_render_cli_defaults_to_hd_and_accepts_lossless -q`

Expected: PASS.

### Task 2: Apply the parcel before PNG capture

**Files:**
- Modify: `sharpmod/render.py`
- Modify: `sharpmod/tests/test_packaging_render_smoke.py`
- Modify: `README.md`
- Modify: `docs/USAGE.md`

- [x] **Step 1: Write the failing window-selection test**

Add a fake `SPCWidget` whose profile exposes distinct parcel objects. Assert `_apply_render_parcel(win, "EFF")` routes the EFF object through `updateParcel()`, and assert an unavailable requested parcel raises a clear `ValueError`.

- [x] **Step 2: Implement selection in the render API**

Add `_apply_render_parcel(win, parcel)` and a `parcel="MU"` keyword to `render()`. Normalize programmatic values to uppercase, reject unsupported keys, resolve the selected profile parcel via `getParcelObj()` or its profile attribute, and call `updateParcel()` immediately after `compose_window()`.

- [x] **Step 3: Document the CLI option**

Add `--parcel MU` to the CLI examples and list all accepted keys in the README and usage guide.

- [x] **Step 4: Run focused and adjacent verification**

Run: `python -m pytest sharpmod/tests/test_packaging_render_smoke.py sharpmod/tests/test_gui_default_parcel.py -q`

Expected: all tests pass.

- [x] **Step 5: Verify the installed-style CLI help and compilation**

Run: `python -m sharpmod.render --help` and `python -m py_compile sharpmod/render.py sharpmod/tests/test_packaging_render_smoke.py`

Expected: help lists `--parcel {SFC,ML,FCST,MU,EFF,USER}` and compilation exits successfully.
