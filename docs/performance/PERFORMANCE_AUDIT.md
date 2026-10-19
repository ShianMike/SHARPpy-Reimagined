# Architecture and performance audit

Audit date: 2026-10-19. Baseline candidate: `0b505e649c554dc718ae55498c177434c4bcfe65`.
This is an implementation program in progress, not a release-readiness claim.
See [the plan](RUST_MIGRATION_PLAN.md), [measurements](BENCHMARK_RESULTS.md) and
[recoverable checkpoints](TASK_CHECKPOINTS.md) for execution status.

## Actual architecture

The current application has a React/TypeScript UI, MapLibre/deck.gl maps, Tauri
native routing and supervision, one Python bridge and one persistent offscreen
Qt subprocess for sounding images. Python scientific facades already call the
PyO3 Rust backend by default. Python fallbacks remain available and tested.
The old Qt picker/viewer application is retired. The chart widgets remain a
reference renderer. Statements that the current app requires the old GUI, or
that no scientific computation has moved to Rust, are obsolete.

```text
React / TanStack Query / bounded playback
  -> Tauri command router (or local browser bridge for development)
     -> native metadata/catalogue caches
     -> supervised Python JSON-lines bridge (bounded work, cancellation)
        -> existing model providers, native ecCodes, exact disk/session caches
        -> sharpmod scientific facade -> PyO3 Rust kernels / Python fallback
        -> persistent Qt worker -> calibrated HD/UHD chart -> PNG
     <- SHRF binary grids/imagery; structured JSON geometry and diagnostics
  -> MapLibre/deck.gl / typed grids / bitmap reuse / hover
```

The desktop supervisor launches Python in isolated mode (`-I`), which ignores
`PYTHONPATH`. A candidate source directory and matching version label alone do
not prove desktop execution uses candidate code. Import-path evidence is
required for real integration measurements. Browser development has different
source selection and must be labeled separately.

## Existing scientific coverage

| Calculation | Existing Rust work | Remaining Python / compatibility work |
| --- | --- | --- |
| CAPE/CIN | `parcels.rs` ascent, standard and traced parcel workspaces | Shared orchestration, nonstandard layer requests, upstream compatibility diagnostics |
| Parcel thermodynamics | Surface, forecast, MU, ML and effective parcels; explicit ascent | Public parcel restoration and extended fields outside native contract |
| LCL/LFC/EL | Native dry lift, crossing refinement and ascent diagnostics | Units, masks and parcel object presentation |
| DCAPE | Native source selection, descent and trace | Compatibility orchestration and public result conversion |
| SRH | Native layer integrals and profile kinematics | Generic/unsupported layer fallbacks and display semantics |
| Wind shear | Native cached standard layers and batch engine | Arbitrary-layer call sites may retain upstream routines |
| Bunkers motion | Native kinematics workspace and batch analysis | Profile object compatibility and explicit storm-motion overrides |
| Effective layer | Native effective-inflow search with convective parcels | Effective bulk wind/shear and severe-parameter orchestration |
| Hodograph | Native winds, shear, means, storm-relative quantities | Chart projection, calibrated hit targets, arbitrary user edits |
| Interpolation | Native vector/scalar operation and workspace reuse | Small-call conversion overhead, masked SHARPpy fallbacks |
| Severe parameters | Native batch diagnostics and reused parcel/kinematic ingredients | ECAPE, specialized formulas and upstream composite oracle contracts |
| Ordered profile batches | Rust-owned snapshots, GIL detachment, bounded shared Rayon pool | Preparing source profiles, optional metrics and provenance |

Primary source paths: `rust/sharpmod-rs/src/{parcels,kinematics,batch_analysis,lib}.rs`,
`sharpmod/backends/{rust_backend,python_backend,parcels}.py`,
`sharpmod/sharptab/{accelerated_profile,derived_indices,ecape,profile}.py`,
`sharpmod/analysis/{box_analysis_fast,profile_metrics_engine}.py`.

The existing API is version 7 and validates package version plus required
operations. Complete-profile calls snapshot Python-owned arrays before
detaching; a borrowed NumPy array must not be exposed to concurrent mutation.
Native trace arrays already cross as buffers plus offsets, and batch results
are packed arrays. Prior work removed redundant masked-array wrapping and
combined missing-sentinel cleanup with Rust's required owned copy. Repeating
that migration or adding a different array framework would duplicate work.

The current full backend harness validates numerical equivalence before timing.
Its small scalar/wind operations can be dominated by conversion overhead; a
language label is not a performance result. The full public adapter and actual
profile construction must be included in any new scientific speed claim.

## Model and GRIB processing

### Completed optimizations to preserve

- Provider selection and message inventory are shared with byte-range planning.
- Direct GRIB decoding avoids constructing full xarray cubes for point requests.
- Native decoding mmaps source bytes and uses ecCodes message handles against
  them; inventory and point caches use replacement-safe filesystem identity.
- Multipoint decoding batches unique points and selected grid indexes per
  message. Nearest-point lookup is reused per grid, including mixed surface and
  pressure grids. Batch analysis does not enter ecCodes.
- Compatibility xarray/cfgrib paths reduce spatial neighborhoods before merging.
- Surface insertion, below-ground removal, wind reference and vorticity
  provenance are scientific contracts; the July pressure-only benchmark files
  cannot establish today's mandatory surface/ground correctness.
- E04 fixes above-ground wind stencils. E10 shares cold map acquisitions across
  raster/winds/contours. Neither should be reimplemented as a new optimization.

The active desktop point handler still requests an isolated GRIB directory per
opened sounding. Existing batch `ModelHourCache` leases are not automatically
used by that handler. Repeated points from a shared model hour are therefore a
practical acquisition/caching opportunity, subject to identity, cancellation,
storage-clear and lifecycle correctness.

### Model-specific constraints

| Model/provider family | Constraint and performance consequence |
| --- | --- |
| HRRR | F000 may use chunked Zarr; forecast-hour GRIB and surface merge remain separate routes. Do not benchmark one route as all HRRR behavior. |
| NAM 3 km / NAM | Geographic filtering and full-message range subsetting have different bandwidth/decode costs. Preserve source-grid coverage. |
| RRFS / REFS | Repository catalogue/source configuration is authoritative. NOMADS rate limits remain material; current member/ground metadata needs live verification. |
| RAP / HRW / GFS / CFS | Source availability, pressure inventory and surface conventions differ. Reuse identity includes model/product/run/hour/member and spatial selection. |
| IFS / AIFS | Different inventories and invariant/surface fields require their existing merge and wind-reference handling. |
| ECCC / Open-Meteo point routes | Point-specific retrieval must not be shared as a full spatial model-hour dataset. |

Keep native ecCodes rather than introducing a replacement parser without real
format/packing coverage and measured benefit. Both Python and Rust currently
serialize ecCodes access, and their separate locks must be understood before
combining concurrent entry. Thread-enabled builds do not remove multi-field
global-state concerns. Pure record assembly may be movable outside a native
lock, but measure contention and preserve borrowed/mapped lifetimes first.

Relevant sources: `sharpmod/tools/model_extract*.py`,
`sharpmod/models/{model_hour_cache,model_disk_cache,model_transport,model_sources,model_surface}.py`,
`sharpmod/backends/grib_decode.py`, `rust/sharpmod-rs/src/grib.rs`.

## Sounding renderer

The historical 18 s first / 7-8 s later claim is environment-specific. Current
measurements include warm HD runs around 4-5 s before tuning, while two first
render runs were above 30 s with a large wall/CPU gap. A separate fully cold
cProfile run took about 10.9 s including imports. These are different cold/cache
states; there is no evidence for assigning the entire gap to Python execution.

Source-proven and measured candidates:

1. **PNG encoding effort.** All modes are lossless, but HD/UHD previously used
   maximum compression. Measured moderate effort preserves every decoded pixel
   and dimension with a small size increase. Whole-render comparisons, not just
   codec timings, are required before calling this a user-experience gain.
2. **Duplicate convective oracle.** `_derived_profile` wraps an already completed
   profile but loses its reusable convective workspace. The shared oracle then
   constructs another ordinary convective profile for LSCP/NSTP/Modified SHERBE.
   The warm trace attributes about 0.93 s to that construction under profiling.
   Existing formulas and CIN terms require ingredient-level equivalence before
   reusing source data; `source.left_scp` is not an established substitute.
3. **Composition and redraw.** A warm trace spends about 3.26 s in composition,
   including mounted products, multiple layout sizes and redraws. Disabling
   QWidget updates alone does not prevent explicit `plotData` work. Removing
   redraws without calibrated axis/label/export parity would be incomplete.
4. **First ECAPE dependencies.** Cold `_building_blocks` imports MetPy/xarray
   during derived-index generation. Delaying that import is already intentional;
   simply importing it earlier moves the wait rather than eliminating it.
5. **Lifecycle and memory.** Repeated HD/UHD composition raises transient native
   working set. Cyclic wrappers and retained pixmaps require profiling with a
   real long-lived worker; Python heap alone misses Qt and graphics storage.

Keep science separate from rendering. A progressive interactive renderer can
reuse calibrated chart transforms and reference primitives, with cached axes
and targeted redraw of changed paths. Prototype independently and compare
coordinates, traces, barbs, text readability, masks, parcel choice, storm motion,
editing and full lossless export against Qt before switching any default.
WebGL/WebGPU is not an automatic replacement for mature vector/text rendering.

## Native bridge and serialization

### Current costs

- Python maps quantize to `uint16`, base64-encode into JSON-lines, and native Rust
  validates before emitting SHRF. Binary transport from Tauri is already used.
- At baseline, Rust allocated a decoded body and copied it into a second SHRF
  allocation for fields, radar mosaic, site radar and satellite. Decode into
  final owned storage is a bounded Rust improvement with no protocol change.
- Three frontend PNG parsers explicitly sliced bodies before Blob construction,
  adding a redundant snapshot. Blob's existing snapshot can own immutable bytes;
  shared buffers still need a single ordinary-buffer snapshot.
- Contour/wind JSON still carries base64 winds that Rust validates then sends as
  base64 again for frontend decoding. A new binary geometry format would need a
  versioned contract and measured benefit before adoption.
- Sounding view images still pay base64 inside Python/native JSON before raw PNG
  output; repeated redraw requests also pay full Qt composition. Moving only the
  envelope may save less time than reducing duplicate scientific/render work.
- Python worker admission is bounded at 24 and control requests bypass it. The
  Rust writer queue is not equivalently bounded. Any backpressure change must
  preserve reserved liveness/cancel/progress capacity and timeout semantics.

E06 already implements cooperative cancellation and ownership-aware model
preloads. Live provider cancellation has separate callers and shared-volume
ownership; rejecting a frontend promise alone does not stop source work. Do not
raise worker counts or remove locks in place of tracing queue and provider time.

Source paths: `src-tauri/src/backend/{map_ops,satellite_ops,reports_ops,sounding_ops}.rs`,
`src-tauri/src/python/supervisor/`, `sharpmod/bridge/{server,browser,fields}.py`,
`frontend/src/lib/contracts/frames.ts` and `frontend/src/lib/backend/`.

## Model Viewer and Live Tracking

Existing strengths include typed field buffers, bounded memory/disk caches,
shared query ownership, rolling forecast prefetch, preparation before playback
advance, separate Smooth/Fast behavior, native radar volume reuse, indexed
lossless PNGs, real-site/cut timestamps and value inspection from native data.
Retain scientific palettes, radar gate resolution, range folding and missing
sectors. Increasing resampling or lowering source resolution is not a substitute
for eliminating repeated work.

Remaining candidates to measure:

| Candidate | Practical approach and guard |
| --- | --- |
| Field image preparation in the display path | Measure first colorization and smooth packed-image creation; prepare once off interaction path and preserve byte/pixel equivalence. |
| Hidden-workspace background work | Overview currently mounts map resources; separate first-visit initialization from retaining state after a visit. Verify overview/map return behavior and cached live data. |
| Repeated cache-status/react redraw work | Measure observer invalidation and polling before adding another registry; reuse E11 disk/RAM ownership. |
| Radar first hover | Measure native PNG metadata inflation and lookup preparation, reusing exact scan identity rather than network retrieval. |
| Wind geometry/CPU-to-GPU upload | Reuse stable data and binary attributes; compare upload/rebuild counts, not just JavaScript FPS. |
| Long-session GPU/bitmap memory | Test eviction, context loss, app close, panel switching and multiple graphics profiles with real integrated GPU evidence. |
| Live history download cancellation | Extend shared-consumer ownership through provider boundaries; one panel stopping cannot cancel another panel's volume. |

MapLibre/deck.gl remain appropriate installed technologies. WebGPU support
depends on layer/features and browser/driver; custom shaders, textures and
interleaving need their own migration validation. Keep software/reduced-graphics
fallbacks and test an actual Intel Iris Xe rather than reporting SwiftShader as
hardware acceleration.

## Priority and evidence gates

| Priority | Task | Why | Acceptance evidence |
| --- | --- | --- | --- |
| P0 | Candidate/runtime import identity | Wrong-source benchmarks invalidate all conclusions | `-I` source-path assertion, backend/version evidence |
| P1 | HD/UHD PNG effort | Measured seconds in the real render | Interleaved full renders, identical RGBA hashes, bytes, CPU/memory |
| P1 | Shared convective source | Repeated full scientific construction | Python oracle ingredient/output equivalence and complete-operation timing |
| P1 | Native SHRF + PNG body copies | Proven extra full-body allocations | All routes/contracts, malformed input, final-frame equality, allocation/timing records |
| P1 | Repeated model-hour source reuse | Repeated source retrieval defeats existing native caches | Distinct point correctness, one acquisition, strict key, leases, Clear/Stop/failure checks |
| P2 | Renderer incremental prototype | Full reconstruction dominates interactive changes | Reference visual/numeric parity, real editing/exports, complete timing |
| P2 | Display preparation/background scheduling | Plausible interaction costs | rAF/CPU and actual panel transition traces, bounded memory |
| P2 | Native operation/provider migration | Contract-preserving reduction of actual IPC work | Whole-operation improvement and fallback/cancellation parity |
| P3 | Additional arrays/frameworks/GPU rewrite | No current benefit established | A demonstrated bottleneck the existing stack cannot address |

## Research

- [PyO3 parallelism](https://pyo3.rs/v0.29.0/parallelism.html),
  [rust-numpy borrowing](https://docs.rs/numpy/0.29.0/numpy/borrow/struct.PyReadonlyArray.html),
  [NumPy thread safety](https://numpy.org/doc/stable/reference/thread_safety.html):
  preserve ownership during GIL detachment; additional tiny crossings can cost
  more than a native loop saves.
- [ndarray](https://docs.rs/ndarray/latest/ndarray/) and
  [Rayon](https://docs.rs/rayon/latest/rayon/): views and controlled parallelism
  fit future larger numerical work; both are already represented through the
  installed native stack. No dependency addition is justified by this audit.
- [ecCodes thread safety](https://confluence.ecmwf.int/spaces/UDOC/pages/143041536/Is%2BecCodes%2Bthread-safe%2B-%2BecCodes%2BFAQ),
  [selected-element API](https://sites.ecmwf.int/docs/eccodes/group__get__set.html):
  retain mature format/packing support and global-state safeguards.
- [Herbie subsetting](https://github.com/blaylockbk/Herbie/blob/main/docs/user_guide/background/grib2.md),
  [NOMADS filter guidance](https://nomads.ncep.noaa.gov/info.php?page=gribfilter):
  message subsetting is not geographic cropping; source/network limits are
  separate from decoding and cannot be bypassed by more threads.
- [Qt raster performance](https://doc.qt.io/qt-6/qpainter.html#performance),
  [image writer](https://doc.qt.io/qt-6/qimagewriter.html): optimize retained
  drawings/encoding while preserving output calibration and density.
- [Tauri IPC](https://v2.tauri.app/concept/inter-process-communication/),
  [deck.gl performance](https://deck.gl/docs/developer-guide/performance),
  [WebGPU support](https://deck.gl/docs/developer-guide/webgpu): prefer existing
  binary delivery, stable attributes and measured feature-compatible adoption.

All recommendations are constrained by current source; research is not a local
benchmark. Broader gates, unsupported environments and outstanding experiments
remain explicitly open in the plan/checkpoint documents.
