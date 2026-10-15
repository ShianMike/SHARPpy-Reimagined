# v3.0.0 test retirement audit — 2026-10-15

The v2.0.0 Qt application is the final legacy GUI. Its source and tests remain
available in the v2.0.0 tag. This audit removes checks specific to that interface
from the v3 suite; the shared scientific renderer still uses Qt.

## Removed coverage

- `sharpmod/tests/test_gui_box_picker.py`
- `sharpmod/tests/test_gui_map_layers.py`
- `sharpmod/tests/test_gui_map_time.py`
- `sharpmod/tests/test_gui_overlay_controls.py`
- `sharpmod/tests/test_gui_map_panels.py`
- `sharpmod/tests/test_gui_picker_overlay_product.py`
- `sharpmod/tests/test_gui_commands.py`
- `sharpmod/tests/test_gui_coordinates.py`
- `sharpmod/tests/test_gui_locations_dialog.py`
- `sharpmod/tests/test_gui_recents.py`
- `sharpmod/tests/test_gui_picker_layout.py`
- `sharpmod/tests/test_gui_cache_dialog.py`
- `sharpmod/tests/test_gui_palette_preview.py`
- `sharpmod/tests/test_gui_settings_persistence.py`

These modules exercised Qt picker maps, layer controllers, native menus, dialogs,
and QSettings-based interface preferences. The new interface owns those flows in
`frontend/src/map`, `frontend/src/features`, and `frontend/src/stores`. Their
Python providers remain covered by provider, map-data, and bridge tests. No test
helper imported by a retained test module was deleted.

Picker-only cases and unused fixtures were also removed from
`test_gui_chrome_theme.py`. Its application theme, font restoration, style cache,
and font-registration checks remain. Four raw WRF domain, NetCDF engine, runtime,
and cancellation tests were retained in `test_wrf_workflow.py`; only the old
source-tab and Qt worker-control tests were removed.

The pre-change JUnit report contains **413 retired cases**, accounting
for **456.49 worker-seconds**. Worker-seconds are summed setup/call/teardown
times, not an elapsed-time speedup prediction.

## Retained release checks

- All remaining offline tests run in the serial gate without xdist. Scientific
  properties retain 100 examples in the dedicated property lane and 10 in the
  serial and compatibility lanes. No timing limit or coverage threshold changed.
- Shared chart composition, profile edits/reset, Qt font/palette behavior, PNG
  validation, model-provider behavior, process supervision, and packaging checks.
- NPZ, SPC, BUFKIT, PECAN, UWyo HTML, and WRF-derived NPZ uploads, plus raw WRF
  local-path inspection, valid-time/point extraction, and failed-import cleanup.
- Frontend controls, map lifecycle, panel changes, preferences, and typed bridge
  contracts; native Rust DTO, supervisor, and real Python-sidecar tests.
- The Windows WRF lane now runs the new import bridge, retained NetCDF runtime
  cases, and frozen-worker packaging contracts.

The edit/reset integration test uses HD for its five image comparisons and one
UHD render for full-resolution output. It still checks temperature/wind/storm
motion edits, panel selection, identical reset image bytes, and unchanged input
bytes. In the initial profiling run this reduced that case from 92.18s to 41.44s.
Those runs precede the final dependency refresh and are local observations.

## Research

[Pytest fixture guidance](https://docs.pytest.org/en/stable/how-to/fixtures.html#scope-sharing-fixtures-across-classes-modules-packages-or-session)
explains how broader scopes avoid repeated expensive setup when state can stay
isolated. The existing per-worker QApplication and module fixtures are retained.
[pytest-xdist distribution guidance](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
explains file/group locality and fixture reuse. The four-worker limit, loadfile
fast/compatibility scheduling, and property grouping remain unchanged. The serial
gate remains serial, so it can detect state and ordering failures.

Final timings are recorded after the requested dependency upgrades and full runs.
