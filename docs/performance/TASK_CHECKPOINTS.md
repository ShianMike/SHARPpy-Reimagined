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
