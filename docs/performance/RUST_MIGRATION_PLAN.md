# Rust-first performance modernization

Status: in progress, 2026-10-19. v3.2.0 is unreleased and publication requires
the user's explicit instruction. No tag, updater publication or installer build
is part of this work. The application version remains 3.1.0 during validation.

## Scope and preservation requirements

Optimize the real application from scientific computation and model retrieval
through IPC and interactive visualization. Preserve every supported model,
missing-value mask, pressure ordering, unit, model/run/hour/grid identity,
scientific convention, input format, cancellation guarantee and export quality.
Rust is the preferred execution engine when measurements justify a change;
existing native ecCodes, NumPy, Qt and GPU code should not be rewritten merely
to change languages. No speculative dependencies or separate services.

The starting candidate is `0b505e649c554dc718ae55498c177434c4bcfe65`, containing
the previously validated E04-E11 work. Its timings are the new baseline; those
earlier changes must not be counted again as new performance improvements.
Stable main is `206d1885b03543aca6c3976a5b16f49b447d4b4d`.

## Recoverable sequence

Tasks are deliberately smaller than a five-hour session. Read
[TASK_CHECKPOINTS.md](TASK_CHECKPOINTS.md) before resuming. Complete a bounded
implementation, its regression check and measurements before beginning another.
Unmeasured hypotheses remain open; an unsuccessful experiment is not a gain.

| ID | Task and evidence required | Dependencies | Status |
| --- | --- | --- | --- |
| A01 | Verify checkout, native API/version, architecture documents and release hold | None | Verified; native 3.1.0/API 7 active |
| A02 | Map Python/Rust scientific coverage and all priority calculation callers | A01 | Source audit complete; current default already native |
| A03 | Audit model/provider/GRIB, native decoder locks and memory ownership | A01 | Source audit complete |
| A04 | Audit frontend, Tauri, bridge, playback and GPU delivery | A01 | Source audit complete |
| A05 | Reproduce sounding cold/warm render, profile phases and peak memory | A01 | Baseline, cold/warm profiles and interleaved HD/UHD complete |
| A06 | Measure startup, backend initialization, IPC and cache-hit overhead | A01 | Real isolated stdio and actual Tauri/Iris Xe debug baselines complete |
| A07 | Run existing single-profile/batch Python-Rust equivalence benchmarks | A02 | Complete, raw output retained |
| A08 | Establish local GRIB/map/radar and playback baselines with fixture identity | A03, A04 | Local HRRR/PGUA baselines complete; loaded maps/playback in progress |
| L01 | Select and implement measured low-risk repeated-work/copy reductions | A02-A05 | PNG effort and SHRF/Blob copy removal validated |
| S01 | Improve remaining scientific hotspots only after reference profiling | A02, A07 | Native-source reuse rejected on equivalence; retain exact oracle |
| M01 | Optimize complete model/data operation; separate network and local CPU | A03, A08 | Source reuse implemented; 405 focused checks, work counts; elapsed/live pending |
| R01 | Improve measured renderer initialization/redraw/encoding costs | A05 | PNG and prepared-input worker measured; exact output checks pass, integration pending |
| R02 | Evaluate incremental/native/GPU renderer without replacing reference early | R01 | Research complete; exact prepared-science prototype implemented; replacement deferred under D08 |
| I01 | Route suitable operations natively or eliminate unnecessary IPC work | A04, A06 | Native final-buffer decode validated; actual app integration pending |
| P01 | Optimize bounded maps/radar/multi-panel scheduling and memory | A04, A08 | Exact rolling field preparation measured and focused checks pass; loaded native integration pending |
| V01 | Run focused correctness and numerical-equivalence checks per change | Each implementation | Ongoing requirement |
| V02 | Run integrated Python, Rust and frontend gates at major milestones | L01-S01-M01-R01-I01-P01 | Pending |
| V03 | Compare end-to-end results, allocations, memory and tradeoffs | Baseline and implementation | Pending |
| D01 | Keep research, decisions, raw results and exact recovery instructions current | All tasks | In progress |
| D02 | Produce final audit/performance report with every gap explicitly labeled | V02, V03 | Pending |

## Measurement contract

Use bundled real soundings and existing benchmark harnesses. For downloaded
data, record the bytes' SHA-256, model, run, forecast hour, valid time and chosen
grid coordinates. Offline measurements exclude acquisition, not decoding or
application adapter costs. Report network fetch separately; preserve failures.

Record interpreter/native builds, hardware, power mode, environment overrides,
source commit and competing load. Distinguish application-cold, OS-cache-warm,
query-memory hits and disk-cache hits. Never label a Python fallback as Rust or
a SwiftShader/software run as GPU acceleration. Interleave before/after runs
where possible, retain individual samples and compare complete operations.

For each accepted optimization retain baseline, post-change time, absolute and
percentage change, memory/allocation impact, correctness evidence and resource
tradeoffs. Use focused tests while iterating; retain existing final budgets.

## Initial execution map

- `rust/sharpmod-rs`: existing PyO3 0.29/NumPy 0.29 kernels, Rayon batch engine,
  mmap-backed ecCodes point decoder. No missing science should be assumed from
  the fact that public orchestration is Python.
- `sharpmod/backends`, `analysis`, `sharptab`: native selection, Python reference,
  result conversion, profile orchestration and extended diagnostics.
- `sharpmod/models`, `providers`, `tools`: retrieval, range selection, supported
  provider routes, GRIB extraction and exact cache identity.
- `sharpmod/bridge`: bounded request workers, shared field acquisitions,
  cancellation, session cache and one persistent Qt render subprocess.
- `sharpmod/rendering`, `viz`: headless reference Qt composition, calibrated
  chart transforms, high-density pixmaps and atomic PNG export.
- `src-tauri`: native routing/supervision, JSON-line sidecar and SHRF binary
  delivery. Scientific calculations currently remain behind the shared facade.
- `frontend`: React 19, MapLibre 6, deck.gl 9, TanStack Query and bounded rolling
  playback. Keep this stack unless representative evidence supports replacement.

An authoritative source audit and the initial measured bottlenecks will be
recorded alongside this plan; architecture descriptions predating Live Tracking
or the retired GUI must not substitute for current code inspection.

## Research record

See [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md). Research informs a
hypothesis, while local measurements and correctness checks decide adoption.
