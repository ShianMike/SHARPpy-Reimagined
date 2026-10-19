# Performance task checkpoints

## Checkpoint 0 — baseline and audits started (2026-10-19)

Status: in progress; this checkpoint does not claim the modernization complete.
The active objective is the full Rust-first performance implementation described
by the user's attached specification, including all eight phases and final
measurement/correctness deliverables. v3.2.0 publication remains prohibited.

### Completed

- Read the complete attached objective and verified current worktrees.
- Preserved detached E04-E11 candidate `0b505e6` and stable main `206d188`.
- Copied its previously missing Unreleased v3.2.0 changelog section into the
  primary checkout without changing any released entry or application version.
- Verified the installed native backend with
  `python -m sharpmod.tools.rust_backend_sync --check`: Rust 3.1.0 active.
- Started independent source audits of scientific coverage, model processing,
  and UI/Tauri/IPC. Parent owns sounding-renderer measurement.
- Recorded initial real HRRR point-sounding HD/UHD/original-size renders and a
  warm cProfile trace. No product optimization has been accepted yet.

### Files changed

- Primary checkout: `CHANGELOG.md` only, Unreleased documentation, uncommitted.
- Candidate: the four files under `docs/performance/` introduced at this checkpoint.
- Outside repository: diagnostic script, raw JSON, cProfile statistics and
  original PNGs under `C:\Users\shian\Documents\CodexReports\v3.2.0\performance`.

### Tests and benchmark evidence

Native synchronization check passes. The diagnostic asserts it imports this
candidate and decodes each real PNG. Initial measurements and their limitations
are in [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md). No new implementation means
the prior E04-E11 full pass is context, not proof of future modifications.

### Outstanding work and known regressions

All unchecked tasks in [RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md) remain
required. No known new regression: only documentation has changed. Initial cold
timing is an outlier that requires phase attribution and repeat measurement.
Do not extrapolate from one cold run or from cProfile-instrumented wall time.

### Exact continuation

1. Work in `C:\Users\shian\Documents\CodexReports\v3.2.0\workspace`.
2. Use `C:\Users\shian\OneDrive\Desktop\sharppy reimagined\SHARPpy Reimagined\.gribenv\Scripts\python.exe`.
3. Set `PYTHONPATH` to the candidate directory. A `-I` subprocess ignores this
   override, so verify its actual imported source before claiming a candidate
   end-to-end result. Do not silently profile the editable install on main.
4. Read all four performance documents and inspect `git status` / `git diff`.
5. Inspect the three source-audit reports in the parent report directory.
6. Repeat/attribute renderer cold cost, then choose the largest practical
   demonstrated hotspot. Keep before/after data under the external performance
   directory and validate shared callers, tests and fallback paths.
7. Update checkpoint, benchmarks, plan and changelog before another phase.

Do not release, tag, push or package v3.2.0 without a new explicit instruction.

## Checkpoint 1 — first measured implementation (2026-10-19)

Completed: all three source audits; comprehensive tracked
`PERFORMANCE_AUDIT.md`; existing full backend benchmark/equivalence; new reusable
`benchmarks/benchmark_rendering.py`; interleaved complete-render PNG comparison.
HD/UHD moderate lossless encoding is implemented in `rendering/density.py`.
The existing PNG regression check now covers defaults in all three image modes.
Four focused checks pass; 20 real output images have identical RGBA hashes.
Measured median whole-render improvements: HD 24.1%, UHD 35.9%; file-size tradeoff
+4.0%/+3.5%. See the results document for full scope and memory limitations.

A dedicated external candidate venv is now available at
`C:\Users\shian\Documents\CodexReports\v3.2.0\performance\venv\Scripts\python.exe`.
It reuses installed packages without copying/installing dependencies, with a
single `.pth` selecting candidate source. Its `-I -B` import-path assertion and
Rust backend selection both pass. Use it for any real desktop/worker validation;
the main environment's editable install is not authoritative for this candidate.

In progress: bounded native SHRF/body-copy reduction and malformed-input tests;
source-oracle reuse with strict numerical equivalence; model-hour reuse
feasibility/cancellation review. The SHRF worker owns the compute slot after the
renderer timing run. Other agents prepare patches without timing/build contention.

Next: complete SHRF focused tests/benchmark, verify scientific reuse against the
independent oracle, then run source-data baselines in the available slot. Do not
count a prepared patch as validated. All later phases remain open. Cold renderer
outliers and repeated-composition transient memory are unresolved, not hidden.

## Checkpoint 2 — native transfer and model-source milestone (2026-10-19)

Completed and checked:

- Four native SHRF routes decode into final storage, three frontend PNG parsers
  eliminate a redundant copy. 27 focused Rust checks, 54 frontend checks,
  typecheck, targeted lint and formatting pass. All 16 release benchmark rows
  preserve complete-frame bytes and remove exactly one allocation. Detailed
  results and limitations are recorded in the results document.
- Reused existing `ModelHourCache` in `model_extract_run.extract` and the active
  bridge open flow. Session ownership/Storage accounting now cover the source.
  Captured Event callbacks preserve cancellation across threads. One physical
  background producer bounds abandoned-provider/new-hour churn. Model/run/hour/
  member/exact-spatial identity, active leases, concurrent Clear and point-only
  provider exclusions are tested. 120 focused and 285 extractor/batch checks pass.
- A real local pressure-only GRIB probe verifies source acquisitions 3 -> 1 and
  decoder calls 3 -> 2 with matching selected coordinates. It is intentionally
  not a verified-ground or live-speed measurement.
- Real candidate isolated stdio startup and semantic RPC baselines are recorded.
- Native-source composite reuse was rejected after numerical mismatch. The
  experimental production/test patch was removed; original results are retained.

Changed files since checkpoint 1 include native `map_ops`/`satellite_ops`, their
command/contracts/benchmark checks, frontend frame parsers/contract checks,
`models/model_hour_cache.py`, `tools/model_extract_run.py`, bridge sounding
open/cache/storage and focused regressions. Native dependency compilation used
two jobs and completed before timing; no installer or release was built.

In progress: local HRRR decoding baseline (existing harness, exact input hash and
equivalence), and an independent prepared-render-input implementation. Prototype
output matches for base/alternate chart/temperature edits, but edit latency did
not consistently improve. It is not a production acceptance result. Keep exact
Python oracle science and clone isolation; no native-source substitution.

Exact next action: inspect `git diff`, current subagent results and active exec
handles before starting another workload. Do not restart the running GRIB
benchmark because its output is buffered. Complete/verify prepared-input work,
then run broader integration and actual application/GPU/playback checks. Update
the plan requirements individually; most system-level/final gates remain open.

## Checkpoint 3 — recovered implementation and real runtime (2026-10-19)

Committed checkpoint: `f651b1c` on the detached candidate. It contains accepted
PNG tuning, final-buffer SHRF decoding/Blob copy removal, model-source reuse,
their checks and the audit/plan/results. Stable main remains `206d188`; its only
local modification is the synchronized Unreleased v3.2.0 changelog. Application
metadata remains 3.1.0; no v3.2.0 release action is authorized.

Completed since checkpoint 2:

- Existing real HRRR decoder comparison, numerical/scalar/multipoint equivalence
  and archived PGUA radar baseline. Full hashes, identity, times and limitations
  are in the results document. Pressure-only HRRR is not a verified-ground open.
- Prepared renderer implementation with 135 distinct focused checks and thirteen
  exact PNG/pixel/metadata worker pairs, including edits/reset and invalidation.
  Repeated unchanged warm worker RPC median improves 19.21%; no general edit,
  cold-start or memory-saving guarantee is inferred. Production changes remain
  uncommitted until independent review and the next integrated validation.
- Actual Tauri debug application, isolated candidate interpreter, separate
  WebView profile and real Rust backend. Six existing native startup/IPC/graphics
  checks pass at 1920 x 1080 on hardware Intel Iris Xe. The gesture measurement
  is basemap/stations only; loaded weather/playback remains a separate gate.
- Added prepared renderer and release-hold details to the Unreleased changelog.

Changed files after `f651b1c`: `rendering/prepared.py`, renderer CLI/worker,
`viz/SPCWindow.py`, focused prepared-input tests, desktop measurement viewport,
the frame benchmark's trailing newline, changelog and performance documents.
Parallel work owns frontend field preparation/measurement and native map test
harness; inspect their status/diff before editing those files.

Outstanding: independent prepared-cache review, field/playback preparation
benchmark and justified implementation, real loaded-weather/native sounding
integration, complete offline/Rust/frontend milestone gates and final report.
No accepted scientific tolerance or resolution changes. External basemap warnings
and the pressure-only fixture limitation remain recorded rather than suppressed.

Exact continuation:

1. Use candidate `C:\Users\shian\Documents\CodexReports\v3.2.0\workspace` and
   `performance\venv\Scripts\python.exe` for runtime and subprocess tests.
2. Inspect active subagents and unified exec handles. Native debug runtime was
   started in session 74439, Vite port 5180, CDP `http://127.0.0.1:9234`, with
   external `performance/tauri-performance.json` and a dedicated WebView profile.
   Verify it is still alive before restarting; avoid duplicate supervisors.
3. Retain one heavy compute slot. Do not overlap scientific timing, native builds
   and browser playback timing. Source/doc review can proceed in parallel.
4. Read `prepared-input-reuse/HANDOFF.md`, source snapshots and raw paired results
   before redoing experiments. Review all live agent patches; run focused checks
   first, then the final milestone. Do not reuse older full-suite counts as a
   validation claim for new work.
5. Commit the next coherent candidate checkpoint only after checks succeed;
   stop on nonzero check exit codes. Update the external recovery bundle without
   deleting the older E04-E11 bundle. Keep main/release refs unchanged.

## Checkpoint 4 — reviewed retry recovery and rolling preparation (2026-10-19)

Completed source/worker validation after independent review:

- The renderer now retries analyzed parcel fallback, failed ordinary oracle
  workspaces and missing lazy diagnostics instead of persisting a transient
  failure. All 46 prepared-input and 215 related regressions pass.
- A fresh thirteen-pair actual worker run after those fixes preserves exact PNG
  bytes, pixels and metadata, including reset and source/sidecar invalidation.
  Current warm unchanged/reset RPC median is 2.860 -> 2.638 s (7.77% less), CPU
  2.750 -> 2.453 s (10.80% less), decodes 13 -> 4. These supersede the initial
  pre-review 19.21% figure. The cold candidate is slower in its single pair;
  no cold-start or general edited-view benefit is claimed.
- Upcoming fields now use the shared exact Sharp/Smooth packing before playback
  advances, yielding between tasks and retaining the existing query observer.
  Old windows abort without publishing partial pixels. Weak per-frame caches and
  the existing raw-plus-both-RGBA memory estimate are preserved. No all-hour RGBA
  retention or new worker/service/dependency is introduced.
- 68 focused frontend checks pass, and all four opt-in benchmark checks pass;
  TypeScript/focused lint pass. Paired CPU scene median for four distinct fields
  changes Sharp 39.238 -> 2.377 ms and Smooth 27.643 -> 1.921 ms. Preparation still
  takes about 36-38 ms in yielding tasks; this is not a GPU/FPS/throughput gain.

Files: prepared renderer module/CLI/worker/shared derived seam and its tests;
frontend `fieldImages.ts`, shared raster/panel callers, playback/query ownership
and focused tests; current changelog, architecture/map documents and performance
report. The native map harness has separately been updated for current controls,
real field identity, four actual weather panels and loaded-playback measurements;
its runtime validation remains in progress and is not a passing gate yet.

Evidence remains external: `performance/prepared-input-reuse-post-review-20261019T013513Z/`
contains the reviewed source, 26 PNGs, paired JSON and two passing XML reports.
`performance/field-preparation/` retains all field CPU baseline/paired samples,
correctness log and source snapshots. The older artifacts remain available.

Next: finish real native map/sounding integration, preserve external-provider
failures and harness corrections, then stop the dedicated debug runtime before
the broad timing-budgeted offline lane. Run frontend/native gates on the final
integrated tree; record actual counts, skipped coverage and any regression.
The full Rust-first objective remains active until those gates/report are done.
No release/version/ref/publication action is authorized.
