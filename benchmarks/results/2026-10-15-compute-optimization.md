# Compute optimization — 2026-10-15

## Measured outcome

Large analysis batches now use all CPUs available to the process instead of
stopping at four. A subsequent pass removes Python preparation overhead and
repeated MMP wind interpolation. Results below measure computation with local
bundled inputs; they do not measure network downloads or image rendering.

| Additional optimization, after enabling 12 workers | Before | After | Less elapsed time |
| --- | ---: | ---: | ---: |
| HRRR fast analysis, 128 profiles × 39 levels | 26.284 ms | 15.788 ms | 39.9% |
| Observed fast analysis, 32 profiles × 151 levels | 12.868 ms | 9.920 ms | 22.9% |
| HRRR MMP calculation | 34.538 ms | 4.866 ms | 85.9% |
| Observed MMP calculation | 140.141 ms | 5.212 ms | 96.3% |
| Complete HRRR convective-profile construction | 244.012 ms | 214.998 ms | 11.9% |
| Complete observed convective-profile construction | 603.436 ms | 472.092 ms | 21.8% |

The preceding worker-count experiment reduced real-input batch time by 36.4%
(HRRR) and 39.5% (observed) when changing four workers to twelve. Those are
separate experiments; their percentages must not be added to the table above.

## What changed

- The default Rayon pool uses the process's available CPU estimate. Windows
  process affinity is checked explicitly because the standard Rust estimate
  overcounted a one-CPU restricted process on this machine. Concurrent default
  callers share a pool; small and nested batches remain serial. Explicit lower
  worker limits remain supported.
- Missing-sentinel normalization moves from Python's per-column scan/copy into
  the existing Rust-owned input snapshot. Normalizing before derived quantities
  are computed also prevents a legitimate derived value from being mistaken
  for an input sentinel. Input buffers remain unchanged.
- Ordinary NumPy arrays no longer acquire a temporary `MaskedArray` wrapper
  twice while being prepared. Actual masks, dtype conversion, dimensionality
  checks, and contiguous-array requirements remain enforced.
- MMP interpolates its lower and upper wind endpoints once, then uses NumPy's
  array operations for each lower endpoint. Previously each pair repeated four
  field interpolations. The pair comparisons still visit every valid pair;
  storage is linear in the upper-layer size, with no full pairwise matrix.

The MMP change uses the installed NumPy implementation; a new Rust API was
unnecessary for that gain. No dependencies, Python modules, approximate
meteorological formulas, fast-math flags, or machine-specific compilation flags
were added. ecCodes serialization and model-download concurrency are unchanged.

## Research and selection

- [PyO3 parallelism, version 0.29](https://pyo3.rs/v0.29.0/parallelism.html)
  explains detaching from Python while Rust workers execute. This project
  already does that; moving more tiny calls across the boundary would not
  remove its Python preparation costs.
- [rust-numpy's read-only array contract](https://docs.rs/numpy/0.29.0/numpy/borrow/struct.PyReadonlyArray.html)
  distinguishes an immutable array borrow from owned storage. We retain the
  input snapshot before detaching and combine cleanup with that required copy.
- [NumPy broadcasting](https://numpy.org/doc/stable/user/basics.broadcasting.html)
  documents compiled array loops and the memory cost of large broadcast
  intermediates. MMP reuses endpoints and reduces one upper vector at a time.
- [The Rust Performance Book: allocations](https://nnethercote.github.io/perf-book/heap-allocations.html)
  recommends measuring allocation costs. The profile identified masked-array
  construction as substantial Python overhead, which justified removing it.
- [Rust available_parallelism limitations](https://doc.rust-lang.org/std/thread/fn.available_parallelism.html)
  documents Windows affinity limitations. The implementation uses
  [GetProcessAffinityMask](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getprocessaffinitymask)
  to correct the measured overcount.

## Method and raw evidence

Environment: Intel Core i5-1235U, 12 logical CPUs, Windows Balanced power plan,
Python 3.11.14, NumPy 2.4.6, Rust backend 2.0.0 / API 7. The final installed
extension matched the release DLL SHA-256:
`3e0b59292b8625bf3eb0fbd404749dce71abd766bb29d22c792607499b6dd378`.

Inputs are `examples/soundings/hrrr_point_36.68N_95.66W_f018.npz` and
`examples/soundings/14061619.OAX`. Batches contain copies of each real sounding,
not independent stations. Loading occurs before timing. Complete profile
construction applies the same existing compatibility initialization used by
the browser bridge, so the observed sounding exercises the supported path.

The second experiment interleaves old and new Python functions in randomized
order (seed 42), with 15 repetitions for batches/MMP and 9 for complete profiles.
Both use the same newly built Rust library, isolating the Python overhead
reduction. The old adapter still performs its historical Python normalization;
the new adapter passes the sentinel into the Rust input snapshot. The saved old
functions are included in the raw JSON. All 26 fast-analysis values matched
exactly. MMP matched at `rtol=atol=1e-12`; complete-profile checks use the same
tolerance for MMP, MUCAPE, MUCIN, DCAPE, and TEI. A separate captured application
call confirmed 128 results with 26 values each and 12 native workers.

- [CPU-capacity experiment and samples](2026-10-15-compute-capacity.json)
- [Additional optimization samples and saved baseline functions](2026-10-15-python-rust-overhead.json)
- [Existing batch benchmark harness output](2026-10-15-batch-harness.json)

The existing harness can be run from the repository's environment without a
new benchmark module:

```python
from benchmarks.benchmark_backends import _load_backends, _record_profile_batch_analysis
python, rust, native = _load_backends()
rows = _record_profile_batch_analysis(
    python, rust, native, calls=1, repeat=3, warmup=1,
)
```

## Verification and limits

340 focused Python tests and 79 Rust tests passed, including native/serial
equivalence, ordered parallel output, shared-pool concurrency, a fresh process
restricted to one CPU, sentinel/mask normalization without input mutation,
MMP scalar-pair equivalence, and bridge sounding/ensemble integration. Ruff,
Clippy with warnings denied, Rust formatting, and forced Rust backend selection
also passed.

The preview on port 5174 restarted, and its Python process loaded the installed
native module with the SHA-256 above. The release compilation succeeded, but
the installer reported failure to remove old DLL backups that were in use.
Forced backend verification passed afterward. Automatic approval review blocked
manual backup removal ("blocked by policy"); those unused backups remain.

These local timings vary with workload, CPU scheduling, and thermal conditions.
Using additional CPUs can consume more power. Release CI, packaged desktop
execution, live provider downloads, and browser interaction were not retested.
The default native pool is sized once per process; changing affinity afterward
requires a process restart to resize it.
