# Performance measurements

Status: baseline collection in progress, 2026-10-19. No new speedup claimed.

## Environment and evidence

- Candidate: `0b505e649c554dc718ae55498c177434c4bcfe65`.
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
