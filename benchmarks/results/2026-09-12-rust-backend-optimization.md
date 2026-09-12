# Rust/backend and export optimization report — 2026-09-12

## Outcome

This work adds three parcel-computation modes, prepared scalar interpolation,
buffer-backed trace transfer, shared thermodynamic preparation, an
identity-invalidated GRIB inventory and native multipoint decoder, and bounded
complete-profile parallelism. It also makes the existing
`rendered_soundings` directory the one default for every interactive export and
`sharpmod-render`.

The public scientific return types and the Python fallback remain intact. The
native contract is API 7. The release profile remains:

```toml
[profile.release]
codegen-units = 1
lto = "thin"
strip = true
```

No LTO/configuration change was justified by a measured experiment.

## Environment and inputs

- Source revision at baseline: `d19e3434e4f7f8baa980f478d7f4bdd2e35b9169`
  on `main`, with unrelated local changes intentionally preserved.
- OS reported by Python: `Windows-10-10.0.26200-SP0`, AMD64.
- CPU: 12th Gen Intel Core i5-1235U, 10 cores / 12 logical processors.
- Power plan: Windows **Balanced**.
- Python 3.11.14, NumPy 2.4.6, `sharpmod` 1.1.0,
  `sharpmod_rs` 1.1.0, backend API 7.
- Rust/Cargo 1.97.0; locked release builds through maturin 1.15.0.
- Fixed GRIB fixture:
  `C:\Users\shian\AppData\Local\sharpmod\benchmark-fixtures\20260716\canonical\hrrr.grib2`
  (161,875,836 bytes), SHA-256
  `b6e291f5e4b2628f44bc9f2050ec1fb846d196b12adf6d57fdd1d206d3aa69e6`.
- GRIB point: 35.18 N, 97.44 W. The operating-system file cache was not
  flushed; each stage explicitly controls application cache state.

## What changed

1. Parcel lifting now uses one shared integration body with compile-time
   CAPE/CIN-only, diagnostics-only, and traced modes. Effective-layer searches
   use CAPE/CIN only, batch/summary work omits traces, and public convective and
   explicit parcel calls retain traces. The effective mean-layer sampler also
   matches SHARPpy's fractional-top `np.arange` endpoint exactly.
2. Kinematics prepares each pressure/height interpolation series once and
   returns scalar `f64` values without rebuilding pairs or allocating a
   one-element vector. Surface values are computed once and reused across
   layers. Stable duplicate order, field-specific missing filtering, boundary
   behavior, and monotonic fast paths are regression-tested.
3. Native parcel traces cross PyO3 as contiguous NumPy pressure/temperature
   buffers plus offsets. Buffered internal consumers retain zero-copy read-only
   views; the public API still materializes its historical immutable tuples.
4. Both direct decoders retain bounded inventories. Rust uses an eight-entry
   LRU keyed by canonical path, size, and replacement-safe file identity
   metadata; the cache contains immutable offsets/metadata, never ecCodes
   handles. Native multipoint calls open each selected message once and fetch
   all requested indexes together. The adapter batches unique misses without
   losing request order or conflating points that share a pressure grid but
   differ at the surface. ecCodes remains serialized and model-cache leases are
   unchanged.
5. PyO3 owns one GIL-safe input snapshot; internal parcel structures borrow it
   rather than copying the profile again. Parcel and DCAPE products share one
   preparation. The bounded cross-profile metric cache fingerprints source
   arrays and rebuilds after replacement or in-place edits.
6. Complete profile batches own all inputs before releasing the GIL. They keep
   stable indexed output, deterministic first-error selection, a serial path
   below eight profiles, and a process-wide Rayon pool capped at four available
   CPUs. Simultaneous default GUI calls share that pool, and nested Rayon calls
   remain serial. Box fast-tier and built-in ensemble analysis use this path;
   custom/composite work and per-profile recovery remain serial.
7. `sharpmod.export_paths` resolves `rendered_soundings` from the source project
   or frozen executable, never the process CWD or a user-profile folder. All
   image, sounding text, extracted sounding pairs, CSV, GeoJSON, saved-location,
   session, trend, comparison, and vendored save actions share it. Old
   `export_dir` settings are removed and ignored, one-off GUI and explicit CLI
   paths are not persisted, missing directories are created, and failures
   report the attempted path without a fallback.

## Native Criterion results

Baselines were captured before editing as `goal_pre_optimization` and
`goal_pre_extended`. Final source was measured as
`goal_after_optimization_final`; each kernel used 100 samples. Values below are
Criterion's displayed slope estimate, or the mean where Criterion selected flat
sampling. Negative deltas are improvements.

| Workload | Before (µs) | Final (µs, 95% CI) | Raw delta |
| --- | ---: | ---: | ---: |
| Wind components, 100k (control) | 1,295.706 | 1,394.814 (1,350.111–1,445.954) | +7.65% |
| General interpolation, 100k (control) | 2,631.497 | 2,833.629 (2,770.795–2,898.474) | +7.68% |
| Pressure sort/dedup, 100k (control) | 1,036.566 | 1,228.009 (1,183.943–1,272.410) | +18.47% |
| Scalar interpolation, 32 | 0.314 | 0.324 (0.321–0.327) | +3.18% |
| Kinematics workspace, 32 | 62.718 | 38.347 (38.032–38.658) | **-38.86%** |
| Parcel diagnostics, 32 | 113.182 | 109.934 (109.045–110.822) | **-2.87%** |
| Effective search + traced result, 32 | 464.246 | 422.749 (419.248–426.330) | **-8.94%** |
| DCAPE, 32 | 32.161 | 32.039 (31.722–32.364) | -0.38% |
| Scalar interpolation, 128 | 0.737 | 0.757 (0.750–0.765) | +2.68% |
| Kinematics workspace, 128 | 101.738 | 43.332 (42.938–43.793) | **-57.41%** |
| Parcel diagnostics, 128 | 173.133 | 163.804 (162.549–165.067) | **-5.39%** |
| Effective search + traced result, 128 | 1,904.788 | 1,831.501 (1,814.684–1,846.183) | **-3.85%** |
| DCAPE, 128 | 140.435 | 146.606 (144.827–148.488) | +4.39% |
| Six scalar fields, 128 | 4.373 | 4.495 (4.456–4.536) | +2.77% |

An earlier final run was discarded for speed claims after untouched controls
slowed 36–69% together. A subsequent isolated wind control returned to 1.399 ms
before the table above was collected. The remaining 8–18% control drift means
the small scalar/DCAPE changes are not attributed to the implementation. The
large kinematics gains and the parcel/effective reductions remain visible
despite that adverse drift.

### Complete-profile Rayon evaluation

The final Criterion batch run used ten samples per row. These are complete
thermodynamic, kinematic, and storm-motion results, not one isolated kernel.

| Shape | 1 worker | 2 workers | 4 workers | 4-worker reduction |
| --- | ---: | ---: | ---: | ---: |
| 4 × 32 | 1.987 ms | 1.574 ms | 0.918 ms | 53.8% |
| 8 × 32 | 4.184 ms | 2.130 ms | 1.688 ms | 59.7% |
| 32 × 32 | 20.147 ms | 16.249 ms | 8.637 ms | 57.1% |
| 8 × 128 | 29.155 ms | 14.972 ms | 7.811 ms | 73.2% |
| 32 × 128 | 120.320 ms | 57.441 ms | 28.885 ms | 76.0% |

The isolated 4-profile case benefits on this machine, but the production
default remains serial below eight profiles to preserve a genuinely small path
and limit interaction with the GUI's existing bounded worker. Callers can
override both worker count and cutoff. The 8-profile default is directly
supported by both 32- and 128-level measurements.

## Python adapter and native-boundary results

The complete raw record is
[`2026-09-12-backend-after.json`](2026-09-12-backend-after.json). It contains
five repeats after two warmups and refuses to time until Python/Rust equivalence
passes.

| Workload | Public adapter | Buffered/native comparison | Observation |
| --- | ---: | ---: | --- |
| Shared thermodynamics, 32 | Rust 0.6587 ms | buffered 0.6349 ms; native 0.4606 ms | buffering removes 3.6% of public conversion cost |
| Shared thermodynamics, 128 | Rust 1.9573 ms | buffered 1.8279 ms; native 1.7187 ms | buffering removes 6.6% of public conversion cost |
| Complete batch, 8 × 32 | serial 4.5397 ms | parallel-4 2.3582 ms | **48.1% lower**, adapter included |
| Complete batch, 32 × 128 | serial 65.4719 ms | parallel-4 25.4407 ms | **61.1% lower**, adapter included |
| Native batch, 8 × 32 | serial 4.0244 ms | parallel-4 2.2232 ms | **44.8% lower** |
| Native batch, 32 × 128 | serial 63.4466 ms | parallel-4 22.7413 ms | **64.2% lower** |

At 32 levels, three separate Rust public calls for parcel diagnostics, traced
convective parcels, and DCAPE total 0.8963 ms versus 0.6587 ms for the shared
workspace (26.5% lower). At 128 levels they total 2.4313 ms versus 1.9573 ms
(19.5% lower). This is the measured benefit of sharing preparation; the
single-call 128-level rows remain visibly noisy and are not generalized beyond
this run.

## Fixed-fixture GRIB results

Raw records are retained in
[`2026-09-11-grib-before.json`](2026-09-11-grib-before.json),
[`2026-09-11-grib-cache-before.json`](2026-09-11-grib-cache-before.json), and
[`2026-09-12-grib-after.json`](2026-09-12-grib-after.json). All final
equivalence checks passed for 40 levels, selected coordinates, masks, surface
merge, below-ground removal, vorticity, four ordered point requests, one
duplicate request, and three unique request keys.

| Stage | Python median | Rust median | Calls/sample |
| --- | ---: | ---: | ---: |
| Application cold | 6,130.040 ms | 5,964.637 ms | 1 |
| Warm inventory, point miss | 5,791.794 ms | 5,750.583 ms | 1 |
| Native/vector multipoint | 6,782.714 ms | 6,773.350 ms | 4 |
| Repeated scalar | 17,281.133 ms | 17,282.898 ms | 4 |
| Exact point-cache hit | 0.3361 ms | 0.2845 ms | 1 |

Multipoint reduces the four-request total by 60.75% in Python and 60.81% in
Rust (2.55× throughput versus repeated scalar calls). Cold decoding is
essentially unchanged from the before run: Python +0.11%, Rust +1.84%. Two
preserved pre-change warm-inventory runs were noisy (8.189 s and 16.921 s for
Python); the final 5.792 s is 29.3–65.8% lower. Exact-cache baselines were also
variable, so the final values are reported rather than presented as a single
precise speedup. Native warm-inventory timing was unavailable before this work.

## Export verification

The resolved source-checkout directory is:

```text
C:\Users\shian\OneDrive\Desktop\sharppy reimagined\SHARPpy Reimagined\rendered_soundings
```

An independent Python process launched with `cwd=C:\Windows\Temp` rendered a
real 3260 × 2198 PNG through the extractor helper into that same directory. The
1,193,528-byte PNG decoded successfully and had SHA-256
`1eb969d947c6eca00e4b8bde55aaeea4c7206f61afe14b2c38d349d7904527f2`
before the probe input and output were removed. Focused tests verify every
dialog's initial path, both **Open Export Folder** actions, creation and write
errors, extractor-render defaults, vendored save-hook reset after a one-off
choice, explicit GUI/CLI destinations, stale QSettings removal, a fresh-process
restart, and frozen-executable resolution. There is no
Desktop/Documents/Downloads/home fallback.

## Validation

- `cargo fmt --manifest-path rust\sharpmod-rs\Cargo.toml --check`: pass.
- `cargo clippy --manifest-path rust\sharpmod-rs\Cargo.toml --locked
  --all-targets -- -D warnings`: pass.
- Native `cargo test --locked` with the fixed ecCodes fixture and its DLL
  directory on `PATH`: 71 unit + 7 batch + 1 contract tests passed.
- Forced-Rust Python backend/science/GRIB/box/ensemble matrix on final source:
  256 passed, including the fractional effective-layer endpoint regression.
- Independently forced-Python backend/science/box matrix: 231 passed and
  `backend_info()` confirmed `active_backend='python'`.
- Export GUI/CLI/provider/restart matrix: 377 passed, 1 platform skip.
- Benchmark equivalence gates: pass before every recorded timing.

## Reproduce

From the repository root in PowerShell:

```powershell
& .\.gribenv\Scripts\sharpmod-rust-sync.exe --force

cargo bench --manifest-path rust\sharpmod-rs\Cargo.toml --bench kernels --locked -- --save-baseline goal_after_optimization_final
cargo bench --manifest-path rust\sharpmod-rs\Cargo.toml --bench batch_analysis --locked -- --save-baseline goal_batch_final

$env:SHARPMOD_BACKEND = 'rust'
& .\.gribenv\Scripts\python.exe benchmarks\benchmark_backends.py --repeat 5 --warmup 2 --repeated-calls 100 --cpu-model "12th Gen Intel(R) Core(TM) i5-1235U" --power-mode Balanced --output-json benchmarks\results\2026-09-12-backend-after.json

& .\.gribenv\Scripts\python.exe benchmarks\benchmark_decoding.py `
  --grib 'C:\Users\shian\AppData\Local\sharpmod\benchmark-fixtures\20260716\canonical\hrrr.grib2' `
  --lat 35.18 --lon -97.44 --repeat 3 --warmup 0 `
  --implementations optimized-python optimized-rust `
  --stages application-cold warm-inventory-point-miss multipoint-inventory-reuse repeated-scalar-inventory-reuse point-cache-hit `
  --checkout . --require-all `
  --output benchmarks\results\2026-09-12-grib-after.json

$eccodesDir = (Resolve-Path '.\.gribenv\Lib\site-packages\eccodes').Path
$env:PATH = $eccodesDir + [IO.Path]::PathSeparator + $env:PATH
$env:SHARPMOD_GRIB_TEST_FIXTURE = 'C:\Users\shian\AppData\Local\sharpmod\benchmark-fixtures\20260716\canonical\hrrr.grib2'
$env:SHARPMOD_ECCODES_LIBRARY = Join-Path $eccodesDir 'eccodes.dll'
cargo test --manifest-path rust\sharpmod-rs\Cargo.toml --locked
```

## Deferred or rejected candidates

- DCAPE-only changes were not expanded: its native kernel showed no stable
  improvement, and the shared-preparation path already removes the redundant
  profile setup when DCAPE is consumed with parcel products.
- A separate public prepared-interpolator object was not added. Standalone
  scalar and six-field microbenchmarks did not improve; preparation reuse pays
  inside the coarse kinematics workspace without enlarging the public API.
- Parallel GRIB/ecCodes decoding remains disabled. One field-major multipoint
  call delivered the demonstrated 2.55× throughput while retaining the safer
  serialized ecCodes boundary.
- Parallelism below eight complete profiles is opt-in even though the isolated
  4-profile Criterion row improved. This keeps the default small path serial and
  avoids multiplying the application's existing worker concurrency.
- Thin LTO and the release settings were retained; no measured evidence called
  for a build-configuration experiment.
