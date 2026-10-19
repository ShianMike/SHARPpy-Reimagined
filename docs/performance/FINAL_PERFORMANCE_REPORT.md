# Rust-first performance implementation report

Status: integration validation in progress, 2026-10-19. This report is a
recoverable working record, not a release approval. v3.2.0 remains Unreleased;
application version metadata remains 3.1.0. Stable main has not moved.

## Findings that determined the implementation

The application already runs its principal parcel, thermodynamic, kinematic,
interpolation, batch-analysis and direct GRIB operations through the existing
Rust backend. The source audit traced those public callers rather than treating
Python orchestration as evidence of Python numerical execution. ecCodes, NumPy
and Qt also execute substantial native work. Another language rewrite would
duplicate working components without demonstrating a better complete operation.

The practical targets were repeated source acquisition and decoding, duplicate
scientific construction, lossless image encoding effort, full-body copies and
field preparation during display. The current implementation keeps the existing
scientific facade, Qt reference charts, React/MapLibre/deck.gl UI, binary Tauri
contracts, provider routes, cancellation and error handling. No new production
dependency or separate service is introduced.

Detailed source coverage and caller paths are in
[PERFORMANCE_AUDIT.md](PERFORMANCE_AUDIT.md). Decisions and primary research
references are in [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md).

## Implemented improvements and measured scope

| Change | Baseline and result | Correctness and tradeoff |
| --- | --- | --- |
| HD lossless PNG effort | Complete warm render median 5.167 -> 3.924 s, 24.1% less elapsed time | Exact decoded pixels/dimensions; PNG bytes +4.0%; original-size mode and explicit overrides retained |
| UHD lossless PNG effort | Complete warm render median 5.499 -> 3.525 s, 35.9% less elapsed time | Exact decoded pixels/dimensions; PNG bytes +3.5%; no resolution reduction |
| Prepared input and exact science in the serial Qt worker | Post-review warm unchanged worker RPC median 2.860 -> 2.638 s, 7.77% less elapsed time; decodes 13 -> 4 over thirteen paired requests | Exact PNG bytes/pixels/metadata including edits/reset/source changes; one pristine collection plus active clone; no hard Qt/native RSS ceiling |
| Final-buffer native SHRF decoding | Synthetic 4 MiB field decode/assembly median 3.717 -> 2.939 ms, 20.9% less; one allocation and exactly 4 MiB peak requested bytes removed | Complete frame/body equality on all four routes; preserves malformed-input rejection; this is transport work, not provider/renderer latency |
| Frontend PNG snapshots | Removes explicit pre-Blob body copies on three routes | Immutable Blob bytes tested, including offset views and shared buffers; no unsupported frontend timing claim |
| Shared model-hour source | Three local acquisitions -> one, three native decodes -> two for same/same/nearby requests | Complete array/identity/ground/QC behavior tested at the real extractor seam; pressure-only local work-count probe is not a live-speed or verified-ground-open benchmark |
| Prepare upcoming full-resolution fields | Four-field first CPU scene Sharp 39.238 -> 2.377 ms; Smooth 27.643 -> 1.921 ms | Exact pixels/codes/masks/palettes; shifts 36-38 ms packing into yielding preparation, not a CPU-throughput or GPU FPS claim; native playback validation pending |

PNG tuning and prepared input were measured in separate controlled comparisons;
their percentages must not be added. Native SHRF allocation counters measure
requested allocator bytes, not total process/GPU memory. Prepared-worker memory
observations are retained, but do not establish a general memory-saving promise.
Raw individual samples, input hashes, commands, CPU/memory observations and limits
are in [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

## Reference-preserving decisions

An attempted native convective-source substitution changed LSCP/NSTP and an
upstream parcel ingredient. It was rejected and removed. Existing equivalence
tolerances and the independent Python reference remain unchanged. Prepared
science reuses that same authoritative calculation, rather than introducing a
different formula or accepting an approximate result.

The real local HRRR benchmark does not show a universal Rust win on cold point
extraction. Existing exact-point cache hits are sub-millisecond, and existing
four-point batch reads reduce repeated-scalar elapsed work by roughly 59-66% in
this fixture. Those are current architecture strengths, not new improvements.
Source reuse and batching are better justified than a second GRIB parser.

Native/WebGPU sounding replacement is deferred under decision D08. Plotters,
tiny-skia and wgpu do not supply verified Skew-T/log-pressure calibration, fonts,
all insets, masks, edit semantics and lossless export parity automatically. The
implemented prepared-science prototype improves the current worker while
retaining the reference/fallback and its calibrated interactive transforms.

## Actual desktop and scientific validation

The isolated candidate interpreter is used by the actual Tauri application and
its `-I` subprocesses. Reusing an editable install from stable main would have
invalidated the result; source paths and native version/API were checked.

The six native startup/health/catalogue/CSP/graphics checks pass at 1920 x 1080
on an Intel i5-1235U, Intel Iris Xe, Windows Balanced power plan. The WebView
reports hardware ANGLE Direct3D 11, WebGL2 and no major performance caveat.
Current debug startup and real IPC baselines are recorded separately from local
stdio and Node measurements. The initial pan/zoom case has no weather field;
it is not evidence of loaded-weather four-panel playback performance.

Focused implemented-change evidence so far: four PNG defaults/regressions,
27 native transfer checks, 54 frontend contract checks, 405 model-source,
extractor/batch/storage/cancellation checks, and post-review 46 prepared-render
plus 215 related regressions. These counts are component evidence, not an aggregate full-suite result;
some shared test scopes overlap. Thirteen real worker pairs preserve exact
outputs. Full integrated validation and loaded-weather results must be recorded
at the final milestone before this report is marked complete.

## Remaining bottlenecks and next justified work

| Priority | Work | Evidence needed before changing it |
| --- | --- | --- |
| P1 | Reduce repeated Qt composition/layout and transient native memory | Complete edited-view timings, full calibrated pixel/metadata parity and a long-lived worker memory trace; do not mistake compression gains for geometry reuse |
| P1 | Measure real source reuse on current complete HRRR/RRFS/REFS datasets | Validated ground fields, run/member/valid-time identity, captured network bytes and complete extraction latency; July pressure-only fixtures cannot satisfy that gate |
| P2 | Radar first-volume decode/projection and hover preparation | More sites/products/tilts, separate acquisition/decode/project/upload, exact scan/code/calibration preservation; the PGUA sample is a baseline only |
| P2 | Reduce remaining sounding/geometry base64 serialization | Attribute actual whole-command cost before adding a versioned binary protocol; preserve contract errors and cancellation |
| P2 | Bound Rust request backpressure without harming control traffic | Saturated-queue experiments preserving reserved liveness/progress/Stop capacity; replacing the queue with an unbounded blocking pool is insufficient |
| P2 | Longer graphics/cache soak and fallback coverage | Loaded multi-panel and workspace changes, context-loss recovery, query/bitmap eviction, process memory and real integrated GPU evidence |
| P3 | Additional native kernels or renderer framework migration | A demonstrated complete-operation hotspot, exact reference equivalence, and a benefit exceeding its boundary/integration cost |

These are explicit deferred investigations, not completed tasks or speedup
promises. Existing provider rate limits, unavailable history, formats and
scientific missing-value conventions remain authoritative. More concurrency
cannot remove external rate limits or make a missing dataset valid.

## Recovery and release boundary

[RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md) lists task dependencies and
current statuses. [TASK_CHECKPOINTS.md](TASK_CHECKPOINTS.md) records tested
checkpoints, changed files, exact continuation paths and unresolved work.
Raw evidence and source snapshots are retained outside the checkout under
`C:\Users\shian\Documents\CodexReports\v3.2.0`.

The primary checkout receives the Unreleased changelog only. Candidate code
remains in its detached validation worktree. No release, push, version bump,
tag, updater publication or installer packaging is included. Hosted release
gates and the user's explicit authorization remain required for publication.
