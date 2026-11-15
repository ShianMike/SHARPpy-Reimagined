# Height-chart numerical migration

The height-chart numerical layer now lives in
`rust/sharpmod-rs/src/chart_profiles/`. The existing Qt widgets, data classes,
chart selectors, legends and painting remain in their owning presentation
modules. Python entry points dispatch to the native extension.

The migrated work includes:

- Surface-relative height grids, including the reported profile top and grid
  spacing rules.
- Stable sorting and selection of the first usable duplicate-height report.
- Per-series interpolation for theta/theta-e and stepwise parcel inputs, with
  missing output outside each series' observed range.
- Celsius/Kelvin normalization using the existing finite-median rule.
- Storm-relative wind speed using the shared selected storm-motion vector.
- Horizontal vorticity, storm-relative projections, magnitude and signed
  streamwiseness, retaining the existing joint wind mask, endpoint clamping,
  gradient conventions and no-data thresholds.

Theta/theta-e derivation and the existing stepwise parcel ascent remain with
their current implementations. Their height grid and interpolation use Rust.
Profile input conversion, direction/speed fallback and storm-motion selection
reuse the existing compatibility boundary; owned numerical buffers are computed
with the Python GIL released.

## Validation

Validated in the Docker preview environment in `SHARPpyBuilder` on 2026-11-15:

- Six Rust regression tests cover grid endpoints, surface indices, duplicates,
  masks, finite medians, zero shear and rotation signs.
- A Rust-driven differential test compares the original NumPy functions with
  the native entry points in 320 comparisons across 64 profiles. It covers
  both storm motions, unsorted and duplicate heights, separate missing wind
  endpoints, masks, non-contiguous inputs, invalid surface indices and extreme
  arithmetic. Relative tolerance is `2e-12`, absolute tolerance is `1e-10`, and
  missing outcomes must agree.
- Existing height-chart, streamwiseness and sounding-view tests passed. The
  built native wheel passed the packaging version/NumPy metadata check.
- `cargo fmt --check` and release Clippy with all targets/features and warnings
  treated as errors passed.
- Live Barrow 2026-11-15 00Z sounding exports in Severe and Winter at
  4564 × 3077 were byte-identical to the corresponding pre-migration exports.
  The live All height charts view also rendered and exported successfully.

The differential test is ignored in ordinary Rust-only runs because it needs
the pinned Qt/NumPy environment and original source snapshots. Set
`SHARPMOD_CHART_REFERENCE_DIR` to a directory containing
`streamwiseness-reference.txt` and `height-charts-reference.txt`, each copied
from the corresponding `sharpmod/viz/` module before the migration. Run the
existing `native_chart_profiles_match_original_numpy_functions` test with
`--include-ignored`. No Python test or helper script is needed.

No end-to-end speed improvement was measured for this change.
