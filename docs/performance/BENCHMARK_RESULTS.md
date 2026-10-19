# Performance measurements

Status: measured implementation and integration in progress, 2026-10-19.
Accepted changes and unmodified baselines are distinguished below. v3.2.0
remains unreleased; these measurements are not release approval.

## Environment and evidence

- Starting candidate: `0b505e649c554dc718ae55498c177434c4bcfe65`;
  PNG/native-transfer/model-source checkpoint: `f651b1c`. Subsequent prepared
  renderer measurements include the explicitly recorded uncommitted source.
- Windows 11 Home Single Language, build 10.0.26200; Intel Core i5-1235U,
  10 cores/12 logical processors, about 15.7 GiB usable RAM.
- Existing `.gribenv`, forced Rust backend 3.1.0, API 7, verified synchronization.
- Real input: `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz`.
- Reference: `sharpmod.rendering.cli.render(..., show_locator=False)`, matching
  the worker's chart configuration. Original HD/UHD density and PNG compression
  settings are unchanged. No concurrent builds or test suites were started.
- Raw evidence: external `CodexReports/v3.2.0/performance/render-baseline.json`,
  `.log`, `.prof`, `render-baseline-profile.txt` and unchanged output PNGs.

## Initial diagnostic, not a final comparison

| Operation | Wall time | Process CPU | Dimensions | PNG bytes |
| --- | ---: | ---: | --- | ---: |
| First HD render, after renderer import | 31.726 s | 8.859 s | 3260 x 2198 | 1,180,940 |
| Subsequent HD render 1 | 4.953 s | 4.594 s | 3260 x 2198 | 1,180,940 |
| Subsequent HD render 2 | 3.954 s | 3.813 s | 3260 x 2198 | 1,180,940 |
| Subsequent UHD render | 8.127 s | 7.438 s | 4564 x 3077 | 1,856,099 |
| Subsequent original-size render | 4.765 s | 4.281 s | 1630 x 1099 | 410,575 |

Renderer import took 5.884 s separately. The first-render wall/CPU gap requires
investigation; it must not be attributed to numerical kernels without a trace.
These are one process in the listed order, not independent mode medians. Peak
working set reached about 809 MiB after UHD, with a cumulative process peak;
the JSON also retains current working set and private committed bytes per run.
Neither peak working set nor private bytes are Python-only allocation metrics.

The separate warm cProfile run is 6.128 s and includes profiler overhead. It
finds Qt window composition about 3.257 s, mounted products about 1.700 s within
that composition, and PNG save about 1.542 s. Derived-index oracle construction
and repeated chart layouts are candidates, not independently additive totals.
No claim is made that changing compression, caching or Rust would automatically
eliminate these costs.

## Required final comparison matrix

Startup, backend initialization, first/warm renderer, single-profile science,
ordered batches, local GRIB/HRRR extraction, model map delivery, radar processing,
forecast playback, IPC, CPU, memory, cache efficacy and export metrics remain to
be measured or explicitly constrained with evidence. Each implemented change
must add raw samples, an end-to-end comparison, correctness results, memory and
tradeoffs here before it is marked complete.

## Accepted change R01a — HD/UHD lossless encoding effort

Research and exact-pixel checks justify using Qt quality 50 for HD/UHD rather
than quality 0 (maximal PNG compression effort). Original-size mode and all
existing environment overrides are preserved. This changes no science, density,
axis, font, palette or pixel; all modes remain lossless. No dependency added.

Before/after used the same candidate, process, real NPZ, scene and backend.
Only the existing `SHARPMOD_HD_PNG_QUALITY` / `SHARPMOD_UHD_PNG_QUALITY` differed.
Five measurements per setting/mode were interleaved in a deterministic shuffled
order after warming each mode. They measure the complete `cli.render` operation,
including reconstruction, composition, cleanup and atomic PNG writing.

| Complete warm operation | Before | After | Less time | Process CPU before/after | PNG bytes before/after |
| --- | ---: | ---: | ---: | --- | --- |
| HD, 3260 x 2198 | 5.167 s | 3.924 s | 1.243 s / 24.1% | 4.938 / 3.766 s | 1,180,940 / 1,228,653 (+4.0%) |
| UHD, 4564 x 3077 | 5.499 s | 3.525 s | 1.974 s / 35.9% | 5.422 / 3.453 s | 1,856,099 / 1,921,257 (+3.5%) |

All 20 output images have identical RGBA hashes within their respective mode:
HD `fca18df97beb358289102eda84a783a6422930b4ff021ec6d3bc194fee2bacdb`,
UHD `9992c1caf6dabcfd36c27a578c0b30291373cb95559156f9c9d926e874c55898`.
Raw interleaved wall/CPU/memory samples and input hash are in external
`performance/render-comparison.json`; full script and log are retained beside it.
Working-set peaks are cumulative within this shared process and cannot isolate
per-setting peak allocation. Repeated composition/GC fluctuations affect both
settings; the experiment does not establish a memory reduction. Dedicated fresh
HD runs reached similar peaks (about 672 MiB), consistent with unchanged image
dimensions and memory policy. Windows power plan was verified as Balanced.

Independent re-encoding trials on the same already-rendered QPixmap isolate
codec effort: HD median 1.070 -> 0.229 s, UHD 1.903 -> 0.385 s. They also confirm
pixel equality. Re-encoding imported PNGs uses different pixel storage and byte
sizes from live widget grabs; do not substitute these microbenchmark sizes for
the whole-render table. `performance/png-compression.json` retains every sample.

Focused regression: all four PNG export checks pass, including parameterized
original/HD/UHD pixel-for-pixel comparisons. Existing atomic file failure behavior
is unchanged. Full integration validation is still pending.

## Existing scientific backend baseline

The complete existing `benchmarks/benchmark_backends.py` ran with three repeats,
one warmup and numerical equivalence before timing. Raw output and environment
are in external `performance/backend-baseline.json` and `.log`. Forced native
selection matched API 7 / package 3.1.0. No new native kernel change was measured.
The run includes ordinary/scalar/repeated interpolation, profile construction,
parcels, kinematics, DCAPE, shared thermodynamics, batches and two-thread
diagnostics. Some tiny operations and two-thread cases are slower in Rust;
retain those results rather than claiming a universal native speedup.

## Accepted change I01a — native frame-buffer ownership

All four raster routes (model field, radar mosaic, site radar, satellite) now
decode base64 directly into the final SHRF allocation. Existing DTO, padding,
body-size, PNG-signature and golden-frame checks remain. This removes one
full-body allocation/copy without changing the wire or pixel data. Three PNG
parsers also avoid slicing an already-owned ordinary buffer before Blob's
immutable snapshot; shared input still gets a single ordinary-buffer snapshot.

The optimized Rust test binary interleaves 100 measurements of the exact old
decode/copy implementation and current shared helper, after three warmups. The
calling-thread allocator counter runs separately from timings; all original
fixture and larger transport frames match byte for byte. A 4 MiB scalar body
represents the current 2048 x 1024 uint16 field size. Repeated PNG fixture bytes
at 1 MiB are synthetic transport bodies, not a PNG-codec or radar benchmark.

| Native decode and frame assembly | Before median | After median | Less time | Peak live requested bytes before/after |
| --- | ---: | ---: | ---: | --- |
| 4 MiB field | 3.717 ms | 2.939 ms | 20.9% | 8,389,618 / 4,195,314 |
| 1 MiB radar body | 0.996 ms | 0.708 ms | 28.9% | 2,098,786 / 1,050,210 |
| 1 MiB site-radar body | 0.999 ms | 0.700 ms | 29.9% | 2,099,178 / 1,050,602 |
| 1 MiB satellite body | 0.988 ms | 0.698 ms | 29.4% | 2,098,162 / 1,049,586 |

Including base64 encode, the 4 MiB case changes from 6.819 to 5.985 ms (12.2%).
This is native transport work; Python JSON serialization, provider retrieval,
browser decode, GPU upload and whole-app latency are outside these samples.
Allocator bytes are requested bytes, not OS RSS or GPU memory. The counter's
balance self-check passes; it reports exactly one fewer allocation per frame.

Evidence: external `rust-first-shrf-measurement.json`, raw samples
`shrf-rust-benchmark.log`, focused test logs. Production checks cover bad base64,
missing/noncanonical padding, all valid padding lengths, incorrect sizes, PNG
signatures, and continued valid replies after rejected responses. The frontend
54-check focused run and typecheck pass. Node Blob timing output was not retained
by the test runner; no frontend timing claim is made. Immutable Blob bytes are
verified on ArrayBuffer, offset typed views, numeric arrays and shared buffers.

## Rejected experiment S01a — substitute completed native convective source

Directly reusing the renderer's completed native convective profile as the
upstream composite oracle fails strict equivalence. Real HRRR LSCP is
8.364086026540242 versus reference 8.364080820477003; NSTP with the test's explicit
source vorticity is -0.8850360474338809 versus -0.8850404193493352. The real OAX
LSCP differs visibly: 30.572757431591803 versus 30.48715795813847. Existing formula
and oracle contracts are retained. Tolerances must not be relaxed to accept it.

The successful two-case fallback verification and original exploratory results
are retained externally as `performance/science-reuse-native-fallback.xml` and
`science-reuse-tests.xml`. The exploratory broader run had two setup errors,
which are not a passing implementation result. This attempt is not an accepted
optimization; an unused guard for non-default callers would not satisfy the
actual performance goal. Subsequent work must optimize repeated construction
without substituting numerically different scientific inputs.

## Backend startup and stdio baseline

Three actual isolated candidate processes were started from outside the source
tree using the externally prepared candidate venv. Every hello and health result
identified the expected package and active native backend. There were no provider
requests, mock handlers or substitute server. Each measured semantic command
had one first call plus ten warm samples; requests use the actual JSON-lines
reader, bounded worker dispatch and output serialization.

| Metric | Current baseline across three runs |
| --- | --- |
| Process start through hello | 0.574-0.695 s |
| First native-backend health | 0.039-0.068 s |
| Warm hello median per run | 0.050-0.150 ms |
| Warm health median per run | 0.088-0.232 ms |
| First stations catalogue | 20.9-56.3 ms, 84,332 response bytes |
| Warm stations catalogue median | 2.38-3.71 ms |
| First map catalogue | 16.8-35.1 ms, 64,079 response bytes |
| Warm map catalogue median | 1.66-2.14 ms |

Evidence: external `performance/bridge-baseline.json`, `.log`, diagnostic script
and per-process stderr. This is Python stdio semantic RPC, not pure IPC transport
or a Tauri/WebView end-to-end measurement. No change or speedup is claimed for
these baseline rows. Processes exited normally and temporary cache roots were
removed. Runtime path was asserted under `-I` before running the probe.

## Model-source reuse validation and local work counts

The existing source-hour cache is now used by the real bridge/extractor seam.
The cache retains one idle source, separates canonical model/run/hour/member
and spatial identity, excludes point-only providers and HRRR Zarr analyses, and
uses exact points for geographically cropped sources. Full-domain RRFS/REFS
sources may serve nearby points; cropped/point sources are never borrowed as
another point's full field. Only one background loader per cache is admitted;
waiting cancellation remains responsive even while a prior provider is blocked.

Focused current checks: 120 model-hour/source/bridge/storage/cancellation/progress
tests pass; 285 extractor, batch, disk-cache and ground-contract checks pass.
Tests cover Stop of either shared consumer, last-consumer cancellation, late
success/failure, retry/new-hour bounds, active leases, concurrent Clear, failed
metadata/decode cleanup and strict scientific output/metadata equivalence.

A real local HRRR pressure fixture was used to count the existing native decoder
work for three requests (same point twice, then a second point). The old unique
source layout performs three source-copy acquisitions and three decoder calls;
reuse performs one acquisition and two decoder calls. Exact returned coordinates
match. Evidence: external `performance/source-reuse-local-counts.json` and probe.
The fixture is 161,875,836 bytes and reports `surface_merged=false`; it is a local
decoder/reuse experiment only, not a successful modern verified-ground sounding
open, live-network speedup, or complete bridge latency measurement. No missing
ground fields were fabricated and no validation was bypassed.

## Real local GRIB baseline

The existing decoding harness ran both optimized implementations with numerical
and scalar/multipoint equivalence enabled, three repetitions and no warmup.
Input: canonical HRRR July 16 F000 pressure subset, 161,875,836 bytes,
SHA-256 `b6e291f5e4b2628f44bc9f2050ec1fb846d196b12adf6d57fdd1d206d3aa69e6`.
Network is excluded; application caches are cleared for cold samples and OS
file cache is not flushed. This fixture lacks the modern mandatory ground
contract, so it is not a full live sounding-open result.

| Existing operation | Python median | Rust median | Interpretation |
| --- | ---: | ---: | --- |
| Application-cache-cold local point | 8.917 s | 9.367 s | Broad range, ~5.9-12.0 s; no universal Rust win |
| Exact point-cache hit | 0.364 ms | 0.267 ms | Exact identity/cache reuse dominates repeat work |
| Four-point native/vector decode | 6.698 s | 7.152 s | Existing batched field reads amortize decoding |
| Same requests as repeated scalar calls | 19.549 s | 17.373 s | Python ~65.7%, Rust ~58.8% less time when batched |

The batch benefit is already implemented, not a new improvement in this program.
The measurements support source reuse and batching, rather than another GRIB
parser rewrite. Raw samples/environment/equivalence and exact commands are in
external `performance/grib-hrrr-baseline.json` and `.log`. No tolerance was relaxed.

## Real archived radar baseline

Three decodes/renders use one actual PGUA Level II volume, archive time
2026-10-17T18:03:58.850Z, 14,942,194 bytes, SHA-256
`ed7025da541cffd4391a371835fa22257accf7641cf90a73bd3aeb9fd412d329`.
The payload was copied outside its expiring session cache before measurement.
Network and file reading are outside decode timing. The decoder validates site
and scan time and returns 12 available reflectivity/correlation cuts. Selecting
reflectivity preserves native codes and quantitative calibration.

| Operation | Median | Observed range |
| --- | ---: | --- |
| Decode local archived volume | 3.519 s | 2.579-5.607 s |
| 4096 px reflectivity, cold WGS84 projection | 5.329 s | 4.760-5.551 s |
| Same render, warm projection | 1.345 s | 1.317-1.618 s |

Every warm PNG is byte-identical to its corresponding cold render, exactly
4096 x 4096 and 1,167,774 bytes, with the `sharppy-radar` value metadata present.
Peak working set reached 888,643,584 bytes (~847 MiB); cold projection uses the
existing four-worker path and process CPU exceeds wall time. The current source
already caches projection and decoded volumes; these are baseline costs, not a
new cache improvement. Numeric hover decode and GPU display are separate work.
The archive reader printed `Unknown message: 32`; this is retained in the raw
log, and supported moments/cuts still passed all stated identity/output checks.
Do not extrapolate one station/reflectivity scan to all products/sites/tilts.
Raw JSON, code, log and original output PNG: external `performance/radar-baseline*`.

## Accepted change R01b — prepared input in the actual render worker

The serial Qt worker keeps one pristine decoded collection and deep-copies it
for each request. Only diagnostics reached by normal rendering are retained,
after a successful export and widget cleanup. They use the ordinary independent
Python oracle, not the numerically different source substitution rejected above.
The unprepared `cli.render` path remains the reference and automatic fallback.

Two actual isolated candidate workers processed 13 paired requests: cold, three
unchanged warm views, alternate chart/panels/ML parcel, temperature/wind/both
motion-vector edits, storm drag, reset, UHD, sidecar add/change and source
replacement. Every pair has identical PNG bytes, decoded RGBA and full returned
view metadata; reset restores the original output without modifying input.

| Worker operation | Reference | Prepared | Interpretation |
| --- | ---: | ---: | --- |
| Cold first RPC, one pair | 7.518 s | 8.216 s | Candidate slower in this single pair; no cold-start gain |
| Unchanged warm RPC, median of three | 2.860 s | 2.638 s | 0.222 s / 7.77% less elapsed time |
| Unchanged warm process CPU, median | 2.750 s | 2.453 s | 0.297 s / 10.80% less CPU |
| Input decodes over 13 requests | 13 | 4 | Source/sidecar changes invalidate correctly |
| Peak working set over the sequence | 988.63 MiB | 968.36 MiB | Whole-process observation, not a memory saving guarantee |

Post-review validation passes 46 prepared-input checks and 215 related science,
decoder, rendering and cancellation regressions. Three warm samples are the two
unchanged initial views and the unchanged reset after edit trials; raw samples
retain that order and cache history. Individual edited cases have only one pair;
they prove equivalence, not a repeatable edited-view speedup. The comparison is
local render-worker RPC, excluding Tauri/UI and network; both workers exit.

File content and replacement identity, both applicable JSON sidecars, backend
selection/version, science environment, masks, optional columns, source metadata,
vorticity and both storm motions govern validity. Unsupported, unstable or
uncopyable input takes the reference path. Failed decode/render is never retained
as a successful derived result. Only one prepared collection is retained, but
expanded collections, their active clone and Qt/native allocations have no hard
RSS byte cap; existing input byte caps are not an RSS guarantee.

The original pre-review experiment is retained in `performance/prepared-input-reuse/`.
Current post-review script, source snapshots, 26 PNGs, raw measurements and test
reports are in `performance/prepared-input-reuse-post-review-20261019T013513Z/`.
The original 19.21% median gain predates the retry fixes; the table above uses
the final 7.77% run instead. The application-level comparison remains a separate
integration gate; no additional GPU or incremental-chart gain is claimed.

Independent review subsequently found two failure-retry defects: an analyzed
parcel fallback or a failed lazy/oracle result could remain cached after a PNG
was successfully produced. The patch now avoids retaining the fallback and
retries missing/failed diagnostics. The three regressions and all thirteen actual
worker pairs pass after that patch; unchanged reset preserves original bytes and
source changes require new decoding. Previous measurements are preserved rather
than being presented as proof of the reviewed source.

## Native desktop integration baseline on Intel Iris Xe

The real Tauri debug app runs the candidate's isolated interpreter (including
its `-I` subprocesses), actual Python bridge and native Rust 3.1.0/API 7.
The existing `@load|@frames` desktop harness passes all six checks. A separate
WebView profile preserves the user's main settings. Viewport is 1920 x 1080,
DPR 1, Windows Balanced power plan, and the active renderer reports ANGLE Intel
Iris Xe Direct3D 11 with WebGL2, no major performance caveat, 16,384 maximum
texture size and float color/filter/blend support. This verifies hardware use;
device detection alone was not used as evidence.

| Current debug/native baseline | Measurement |
| --- | --- |
| Shell-painted mark | 2,162.3 ms after time origin |
| Stations catalogue request/receive | 2,138.3 / 2,427.8 ms; 289.5 ms request duration |
| Stations-rendered mark | 5,069.6 ms |
| Basemap/map-loaded marks | 6,885.9 / 10,176.2 ms |
| 50 real native health RPCs | p50 2.8 ms, p95 4.8 ms, max 5.0 ms |
| 20 cached native catalogue RPCs | p50 8.2 ms, p95 8.7 ms, max 10.1 ms |
| Basemap/stations pan/zoom, 447 rAF intervals | p50 16.7 ms, p95 33.2 ms, max 116.6 ms; 25 over 33 ms |

Health shows expected debug/package/Python/native versions. Real station 72357
loads; CSP checks report no violations or collected console messages. This is
a Vite debug build, not packaged startup. The gesture case has the weather field
disabled: it is deliberately not labeled loaded-weather, four-panel or playback
performance. Those require separate field-ready measurements. External basemap
warnings about a missing `circle-11` sprite and a null boundary filter are in the
runtime log; the stated checks do not prove those provider tiles are fault-free.

Raw measurements/log, actual screenshots and native process log are external
`performance/desktop-measurements*`, `desktop-screenshots/` and
`desktop-runtime.log`. No release installer, tag, publication or version bump
was produced by the debug application build.

## Accepted change P01a — prepare exact fields before playback display

The actual `useFieldImage` hook and complete field-layer registry were measured
in Node 24.12/jsdom at the production 2048 x 1024 shape. The committed numeric
fixture is tiled without changing codes, scale, offset or nodata; alternate
palettes exercise encoding and are not represented as genuine different weather
products. A paired run alternates lazy/prepared order, uses two warmups and five
samples per lane, includes a warm previous frame at 50% blend, and verifies all
image bytes. This measures CPU scene construction, excluding provider retrieval,
GRIB/PNG decode, real browser ImageData validation, GPU upload and presented FPS.

| Four distinct fields, first complete scene | Lazy median | Prepared median | Display work removed | Preparation wall median |
| --- | ---: | ---: | ---: | ---: |
| Sharp | 39.238 ms | 2.377 ms | 36.861 ms / 93.9% | 37.731 ms |
| Smooth | 27.643 ms | 1.921 ms | 25.722 ms / 93.1% | 36.262 ms |

This is rescheduling, not eliminating the packing cost: preparation occurs
before advancing and yields to input/render tasks. Prepared display constructs
zero ImageData objects instead of four. Source codes, masks, colors, palette
limits and Sharp/Smooth behavior remain exact. Four matching panels share one
prepared field, and only the active modes in the bounded rolling window are
prepared; Load hours does not expand every saved hour into resident RGBA.

The existing query owner is retained through preparation, including memory hits.
Abort cancels obsolete work between tasks without publishing partial pixels or
canceling another consumer. Images are keyed weakly by frames; the existing CPU
cache estimate already counts raw uint16 plus both possible RGBA images. Active
observers may temporarily exceed the eviction target, as before. Partial packing
temporarily owns its output array and releases it when aborted; no total-process
or GPU memory reduction is claimed.

The task loop targets 4 ms, checking every 65,536 cells. Measured maximum Sharp
task was 13.802 ms and Smooth 4.247 ms in this CPU run; 4 ms is a cooperative
target, not a hard real-time guarantee. The native scheduler uses its yield
feature when available and falls back to a timer. Cold/manual cache misses still
have the synchronous reference display path; no new dependency/worker is added.

68 focused checks pass; all four checks in the opt-in benchmark run pass;
TypeScript and focused ESLint pass. Correctness includes every committed palette
and every uint16 code with default/non-default nodata, flag/continuous cases,
same-frame sharing, warm reuse, cached-query ownership, abort and stale-window
playback. These are focused results, not a full frontend/native runtime pass.
Raw full samples/summary/log and source snapshots are retained externally under
`performance/field-preparation/`. Native loaded-weather integration remains open.
