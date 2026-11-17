# Native migration batches for v3.8.5

These changes are staged for v3.8.5. Public package versions remain at the
published v3.8.4 version until release preparation. The existing backend API
version remains 7: result shapes are unchanged. Required capability checks now
include the newly migrated operations, so a stale source-development wheel
fails the existing compatibility check with rebuild guidance.

## Scope

| Batch | Native responsibility | Retained boundary |
| --- | --- | --- |
| M1 | Display K/total-totals, PWAT, humidity, maximum temperature, lapse rates, inferred advection; TEI, ESP, WNDG, significant-severe, SHIP and MMP | Existing parcel ingredients, SHARPpy objects and special composite reference oracles |
| M2 | Archive limits, numeric arrays/scalars, physical QC, paired generations, staged publication and rollback | NumPy NPY/NPZ codec, ZipFile central-directory parser and established serialization hooks |
| M3 | Model, ERA5 and WRF open transactions, extraction dispatch, metadata, cancellation and registration | Existing extractor interfaces, decoder registry, progress adapter and shared model-hour cache |
| M4 | Selective WRF hyperslabs, adjacent-cell wind destaggering, thermodynamics, rotation and pressure ordering | xarray/NetCDF backend, coordinate/time metadata and existing domain geometry |
| M5 | GeoMet bounded acquisition scheduling, exact run/time identity and point columns; Open-Meteo acquisition sequence and column assembly | Capability manifests and credential-aware/injected HTTP adapters; one Open-Meteo request per point |
| M6 | HRRR F000 Zarr field plans, bounded native HTTP jobs, numeric chunk sampling and point normalization | Compiled numcodecs decompression, projection adapter, existing point cache and GRIB fallback |
| M7 | NOMADS geographic and permissive Herbie transfer, local ranges, streamed output, cancellation and atomic publication | Herbie inventories/source routing and caller-supplied HTTP sessions |
| M8 | Edit validation/application, one copy per changed column, targets, inspection and original values | Qt painting, widget transforms and profile collection interfaces |

This is a migration of selected responsibilities rather than a replacement of
the SHARPpy renderer or a new meteorological convention. Open-Meteo credentials
remain inside the existing access adapter. Its fields travel in one request.
The HRRR Zarr route continues to accept only F000 analyses.

## Regression coverage

New logic has Rust tests. Existing Python tests exercise the unchanged public
facades and provider injection points; no new Python application modules or
dependencies were introduced.

- Scientific comparisons use the pinned SHARPpy functions and their original
  constants, including the `0.514444` knot conversion in SharpTab diagnostics.
  The tests cover thermodynamic sampling, inferred advection, severe-index
  ingredients, SHIP and independently masked MMP shear endpoints.
- Portable-file and extraction tests inject serializer, backup, publication,
  late-cancellation and rollback failures. Previous pairs stay intact, and
  failed restores retain recovery files instead of hiding the failure.
- A NetCDF backend read spy verifies that WRF scientific variables receive a
  horizontal point selection, with only two adjacent cells for staggered wind.
  It would fail if whole scientific grids were materialized first.
- Provider tests retain exact run/time checks, complete required fields,
  optional omega, surface consistency, access redaction and single-request
  billing. Zarr tests retain compressed, partial-edge and storage-order cases.
- Transfer tests cover servers that ignore Range, later full responses that
  replace earlier partial bytes, malformed data, local sources and a stalled
  injected response that must unblock on cancellation.
- Editing tests verify one copy per column and rejection of a mixed invalid
  edit before any collection mutation. Existing view tests retain their widget
  transforms, target IDs and missing inspection values.

## Measured results

The following are local Linux Docker measurements from 2026-11-17. The
reference source is commit `0658a3c` with its previous native extension. Each
entry is the median of five alternating reference/current runs after checking
equivalent outputs. These are individual phases, not total application speed.

| Phase | Reference | Migrated | Ratio |
| --- | ---: | ---: | ---: |
| Full thermodynamic display calculation, bundled HRRR profile | 26.417 ms | 0.740 ms | 35.71× faster |
| Interactive hit-target assembly, 128 levels | 9.364 ms | 0.625 ms | 14.97× faster |
| Interactive inspection-record assembly, 128 levels | 10.711 ms | 0.130 ms | 82.37× faster |
| WRF point column, 180 × 240 grid, warm OS file cache | 19.943 ms | 12.900 ms | 1.55× faster |

The WRF case reads **34 scientific values instead of 1,210,860**. Both paths
still read coordinate grids to find the requested point. This read reduction
does not imply the same ratio for elapsed time, memory use or a complete WRF
forecast workflow. Interactive measurements exclude Qt painting. Network
transfer speed depends on the provider, latency and available bandwidth.

The first hit-target measurement exposed a roughly 8% slowdown. Bulk conversion
of profile columns removed repeated scalar callbacks before the final runs
above. Scientific outputs and calibrated Qt coordinate transforms are retained.

The final matching source/native build passed:

- Existing offline serial-release lane: **3,932 passed, 15 skipped**.
- Science Rust library: **378 passed, 23 ignored**.
- Weather-core Rust library: **148 passed, 7 ignored**.
- Science Clippy with warnings denied, changed-file Rust formatting, configured
  Python Ruff checks, frontend type checking and Git whitespace checks.
- CLI Severe and Winter renders at **4564 × 3077**. Browser validation also
  exercised a real backend temperature edit, undo/redo, mode switching and PNG
  export at the same lossless dimensions.

Ignored tests include opt-in live-provider and manual measurement cases. The
phase benchmark above was run explicitly. Provider contract tests use injected
HTTP responses; these results do not establish a live Internet download gain.

## Reproduction

Build and install the matching source package and native wheel in an isolated
environment before testing. A build that is still running must not be replaced
by a previous wheel with the same version number. Check the wheel hash and the
installed extension path.

Use the existing offline lane and native checks:

```text
python scripts/run_test_lane.py serial-release --workers 4
cargo test --manifest-path rust/sharpmod-rs/Cargo.toml --locked --lib -- --test-threads=1
cargo clippy --manifest-path rust/sharpmod-rs/Cargo.toml --locked --all-targets -- -D warnings
```

The manual `migration_benchmarks::measure_migration_phases` test uses an explicit
previous native extension and a Git source archive. Set
`SHARPMOD_MIGRATION_BASELINE_EXTENSION`,
`SHARPMOD_MIGRATION_BASELINE_ARCHIVE`, and
`SHARPMOD_MIGRATION_BENCHMARK_OUTPUT`, then run that test with `--ignored`.
It checks equivalent outputs before timing and alternates the baseline/current
order over five repeats. The WRF case uses a 180 × 240 NetCDF grid and fresh
xarray handles against a warm operating-system file cache. Backend read counts
report scientific values separately from coordinate arrays.

Interactive timings use a 128-level synthetic profile with fixed widget
transforms. They measure record assembly, not Qt painting or an end-to-end map
render. Network throughput remains provider-dependent; native scheduling alone
does not establish an Internet download speedup.
