# Interface startup timings — 0.9.0

Windows AMD64, Python 3.11.14, Qt `offscreen`, `--repeat 7`, medians.
Harness: `benchmarks/benchmark_gui_startup.py`.
Raw samples: `2026-08-27-gui-startup-windows-amd64.json`.

Recorded to establish where the time in the desktop app goes, after the 0.9.0
interface redesign. Elapsed medians only; no speed claims, and no comparison
across machines.

## Theme layer

| operation | median |
| --- | --- |
| `build_chrome_qss[graphite-dark]` | 0.07 ms |
| `build_chrome_qss[paper-light]` | 0.07 ms |
| `build_chrome_qss[protanopia-dark]` | 0.04 ms |
| `apply_theme` (cold — registers the bundled fonts) | 17.7 ms |
| `apply_theme` (warm) | 0.31 ms |

Style-sheet generation is free at this scale. Font registration is the only
non-trivial cost and happens once per process.

## Picker

| operation | median |
| --- | --- |
| `PickerWindow` construct + first layout | 102 ms |

Lazy panel construction (added in 0.8.1) survives the 0.9.0 navigation rail:
only *Station Map* is built eagerly. The other four are built on first visit and
cost 37–308 ms each, so building all five up front would roughly quadruple the
time to first window.

## Sounding viewer

| operation | median | share |
| --- | --- | --- |
| `compose_window(mount=True)` | 1389 ms | 58% |
| render layout passes | 996 ms | 42% |
| `_install_sounding_sidebar` | 4.8 ms | 0.2% |
| `_fit_window_to_screen` | 3.9 ms | 0.2% |
| `_install_view_controls` | 1.4 ms | 0.1% |
| `_install_help_menu` | 0.4 ms | <0.1% |
| `_install_data_inspector` | 0.4 ms | <0.1% |
| `_bind_view_controls` | 0.2 ms | <0.1% |
| sidebar refresh (per state change) | 0.37 ms | — |

Opening a sounding costs about 2.4 s, and **essentially all of it is the vendored
`SPCWindow` construction and the shared render layout passes**. Every piece of
chrome the redesign added totals about 11 ms, or 0.5%.

The sidebar refresh runs once per focus, time, or member change — not per frame.
`modified` is emitted from `mouseReleaseEvent` and the Modify Surface dialog, so
a profile drag triggers exactly one refresh on release.

## Why the layout passes are not reducible

The `processEvents` settling between grow passes is about a third of the layout
phase and looks like easy headroom. It is not. Varying only the pass counts:

| passes | elapsed | canvas |
| --- | --- | --- |
| (2, 2, 6) | 898 ms | 1630x1091 — shipped |
| (2, 2, 3) | 1019 ms | 1630x1091 |
| (2, 2, 1) | 1029 ms | 1630x1091 |
| (1, 1, 1) | 759 ms | **1910x1291** |

Cutting the final settle does not save time: the deferred layout still has to
happen and returns slower elsewhere. Cutting either of the first two produces the
wrong canvas — the layout has not settled when `_grow_for_family_panels` and
`enlarge_canvas` read the current sizes, so they grow from stale numbers. That
canvas size is the geometry contract shared with the PNG renderer, so changing it
is a defect whatever the saving.

## Conclusion

There is no safe optimisation left on this path. The two dominant costs are
vendored `SPCWindow` composition and the render layout passes shared with
`sharpmod-render`, both of which define the canvas geometry that must stay
byte-identical to the rendered output. The interface work added in 0.9.0 is 0.5%
of viewer open and is not worth tuning.
