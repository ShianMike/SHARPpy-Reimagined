# Rust-first performance implementation report

Status: measured implementation and local validation checkpoint, 2026-10-19.
Deferred scope and remaining risks are explicit below. This is not release approval. v3.2.0 remains Unreleased;
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

| Requested phase | Delivered evidence / current boundary |
| --- | --- |
| Audit and baseline | Three independent source audits, tracked execution map, science/renderer/stdio/GRIB/radar/native desktop baselines with input identity and environment |
| Low-risk improvements | Lossless PNG effort, native final-buffer decode, immutable Blob copies and exact repeated-input reuse |
| Rust scientific expansion | Existing priority-kernel coverage verified and benchmarked; numerically different substitution rejected; preserve authoritative science instead of duplicating kernels |
| Model/data processing | Shared source-hour acquisition at the real bridge/extractor seam, independent Stop, strict source identity, bounded producer, leases and Storage cleanup |
| Renderer modernization | Actual Qt phase profiles and exact prepared-science implementation/prototype; alternate native/GPU renderer deferred with parity/integration blockers documented |
| Native application integration | All four SHRF routes optimized without contract changes, real native runtime/import verification and existing native catalogue cache reuse |
| System performance | Bounded rolling field preparation/query ownership, exact code/palette checks, radar projection/decode baseline; actual native four-weather-panel/pan/playback check passes |
| Final validation/report | Full local Python/frontend/browser/Rust gates pass; real native loaded-weather and sounding flows pass; final evidence index and explicit deferrals below |

## Implemented improvements and measured scope

| Change | Baseline and result | Correctness and tradeoff |
| --- | --- | --- |
| HD lossless PNG effort | Complete warm render median 5.167 -> 3.924 s, 24.1% less elapsed time | Exact decoded pixels/dimensions; PNG bytes +4.0%; original-size mode and explicit overrides retained |
| UHD lossless PNG effort | Complete warm render median 5.499 -> 3.525 s, 35.9% less elapsed time | Exact decoded pixels/dimensions; PNG bytes +3.5%; no resolution reduction |
| Prepared input and exact science in the serial Qt worker | Post-review warm unchanged worker RPC median 2.860 -> 2.638 s, 7.77% less elapsed time; decodes 13 -> 4 over thirteen paired requests | Exact PNG bytes/pixels/metadata including edits/reset/source changes; one pristine collection plus active clone; no hard Qt/native RSS ceiling |
| Final-buffer native SHRF decoding | Synthetic 4 MiB field decode/assembly median 3.717 -> 2.939 ms, 20.9% less; one allocation and exactly 4 MiB peak requested bytes removed | Complete frame/body equality on all four routes; preserves malformed-input rejection; this is transport work, not provider/renderer latency |
| Frontend PNG snapshots | Removes explicit pre-Blob body copies on three routes | Immutable Blob bytes tested, including offset views and shared buffers; no unsupported frontend timing claim |
| Shared model-hour source | Three complete repeated current-HRRR local extractions: median 21.880 -> 6.994 s, 68.0% less; acquisitions and native decodes 3 -> 1 | Real verified surface/39 QC-valid levels; exact weather arrays/stable metadata and valid atomic pairs. Imports/network excluded; one model/run/point, similar peak memory, no bridge/UI or download-speed claim |
| Prepare upcoming full-resolution fields | Four-field first CPU scene Sharp 39.238 -> 2.377 ms; Smooth 27.643 -> 1.921 ms | Exact pixels/codes/masks/palettes; shifts 36-38 ms packing into yielding preparation, not a CPU-throughput or GPU FPS claim; actual native loaded playback passes |

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

A final uninterrupted native run passes all four map cases in 108.217 s: actual
HRRR fields/hours/values, SPC categorical/quiet-tornado contract, live station
availability, MRMS, and four loaded temperature/dewpoint/humidity/theta-e panels
with pixel, point and valid-time checks through F002 -> F003 and pause. Its
loaded-pan rAF p50/p95 is 16.7/33.4 ms; loaded-transition p50/p95 is 16.7/17.0 ms
with a 133.3 ms maximum. The earlier faster-pan sample remains in the results;
neither establishes a native FPS improvement or fixed 60-FPS guarantee.

The first serial failure is retained: its success-only tornado assertion did
not represent SPC's real quiet-product/null-outlook contract. The corrected
check rejects errors/timeouts and verifies the requested forecast identity.
Final availability reports 554 bundled plus 16 new reporting stations for the
00Z release, 949 merged stations. Earlier UWyo TLS/reset failures remain in the
log; the later successful fetch is not evidence that upstream never fails. A
real imported BUFKIT/UHD rendering flow also passes separately.

Focused implemented-change evidence so far: four PNG defaults/regressions,
27 native transfer checks, 54 frontend contract checks, 405 model-source,
extractor/batch/storage/cancellation checks, and post-review 46 prepared-render
plus 215 related regressions. These component counts overlap and must not be
summed into a full-suite total. Thirteen real worker pairs preserve exact outputs.

| Final local validation | Result and actual coverage |
| --- | --- |
| Offline Python serial-release lane | 3,764 passed, one skipped in 213.12 s; unchanged default timing budget passes. Skip: no newly built native wheel for metadata inspection. Uses isolated candidate imports and forced Rust. |
| Python correctness / maintainability lint | Both CI Ruff scopes pass. Seven long lines were wrapped after the offline run; AST equality proves no behavior change, and 54 affected disk-cache/Storage checks pass afterward. |
| Desktop Rust | Format and strict all-target Clippy pass; 64 regular checks pass. Both opt-in real Python sidecar/normal-and-forced cleanup checks pass separately. The opt-in transfer benchmark was measured separately. |
| Scientific Rust | Format, strict all-target/all-feature Clippy and all-target tests pass: 79 unit/integration checks plus benchmark smoke cases. Optional direct GRIB benchmark has no configured fixture in this invocation; real GRIB evidence is recorded separately. |
| Frontend | Typecheck, lint and unchanged boundary limits pass; 808 tests pass, two opt-in performance cases skipped in the routine run and exercised separately. Desktop/browser/fixture builds all pass. |
| Browser workflows | All 70 pass in 3.7 min, one worker, zero retries, fixture transport and explicitly enabled SwiftShader. These are regression/fallback checks, not native GPU benchmarks. |
| Actual native UI | Six existing startup/IPC/CSP/graphics cases; final four-case native map invocation all green; separate real BUFKIT/UHD import/render pass. Hardware Iris Xe is verified. Provider histories and earlier failures are retained. |

Raw milestone logs/results use `performance/integrated-offline*`,
`integrated-frontend-final-*`, `integrated-rust-final-*`,
`integrated-quality-final-*` and `integrated-browser-final*` outside the checkout.
The first boundary-lint failure and maintainability-lint failure remain alongside
their passing corrected runs. No threshold, scientific tolerance, source
resolution, failure classification or production dependency was relaxed.

The desktop map harness is split into its existing spec and a helper module to
meet the existing 300-line ceiling. SHRF alignment/base64 checks moved from the
native production module into the existing contracts integration tests to meet
the existing 400-line ceiling. Assertions and validation remain intact.

The native runtime still logs 40 field/19 contour INTERNAL warnings during
selection/prefetch, with no Python traceback, and one connection-pool warning.
Cancellation uses an INTERNAL bridge code too, but logs lack the per-request
details needed to classify every warning. This remains an explicit reliability
investigation; passing UI cases do not erase failed/abandoned background work.

## Remaining bottlenecks and next justified work

| Priority | Work, benefit and feasible approach | Complexity / evidence blocking acceptance |
| --- | --- | --- |
| P1 | Reduce repeated Qt composition/layout and transient native memory: extend the exact prototype toward retained calibrated geometry for unchanged chart elements | High; requires complete edited-view timings, pixel/metadata parity and a long-lived worker trace. Current prepared input does not eliminate full widget composition. |
| P1 | Extend complete source measurements beyond HRRR: capture current ground-valid NAM 3 km/RRFS/REFS members and run the same exact complete-operation comparison | Medium; representative complete inputs/member identities are missing from the existing July fixture set. No all-model claim can be accepted from pressure-only files. |
| P1 | Correlate native background failures: retain safe request IDs and explicit cancelled flags in diagnostics, then separate real failures from abandoned work | Low-medium; current logs cannot establish the cause of all 59 INTERNAL warnings. Preserve the returned error contract and genuine failures. |
| P2 | Radar first-volume and hover work: profile acquisition, native gate decode, projection, PNG and GPU preparation on more sites/products/tilts | Medium; PGUA is one reflectivity baseline, and no wider decoded/code/calibration equivalence matrix is captured. Keep resolution and range-folding semantics. |
| P2 | Reduce residual sounding/geometry base64 work through the existing native boundary | Medium-high; whole-command attribution must justify a versioned binary format and its compatibility/cancellation costs. Transport microbenchmarks alone are insufficient. |
| P2 | Bound Rust writer backpressure while reserving liveness/progress/Stop traffic | Medium-high; a saturated-queue experiment is missing. A new unbounded blocking pool would preserve the risk rather than resolve it. |
| P2 | Longer graphics/cache soak: exercise loaded workspace/parameter changes, runtime context loss, bitmap/query eviction and native/GPU memory | Medium; short hardware and fallback runs are not an extended-session or cross-device memory bound. CPU/no-WebGL startup fallback is already covered. |
| P3 | Additional kernels or a native/WebGPU chart engine, only for a demonstrated complete-operation bottleneck | High; current principal kernels are native, one proposed substitution failed equivalence, and a new chart engine lacks verified fonts/axes/insets/edit/export parity. No rewrite is accepted merely for its language. |

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

Automatic approval review rejected deletion of a byte-identical 223,970,241-byte
temporary benchmark copy outside the repository, returning only `blocked by
policy`. After the user requested a manual PowerShell command, a read-only check
confirmed that directory is absent and canonical evidence remains. Its initial
block/hash/size and final resolution are retained in the external audit record.
