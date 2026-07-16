# Backend benchmarks

`benchmark_backends.py` records comparable timings for the fully functional
Python backend and the optional Rust backend. It does not enforce a speed
threshold or claim that one implementation is faster. Results depend on the
CPU, operating system, Python and NumPy versions, compiler, build profile,
input size, array layout, and Python/native conversion overhead.

## Prerequisites

Create the normal Python environment, install the benchmark build frontend, and
build the release-mode extension into that same environment:

```powershell
.\.gribenv\Scripts\Activate.ps1
python -m pip install -e ".[dev,rust-build]"
Set-Location rust\sharpmod-rs
maturin develop --release
Set-Location ..\..
```

The harness intentionally imports all of these before measuring anything:

- `PythonBackend`, for the authoritative implementation;
- `RustBackend`, for the normal Python adapter around native code; and
- `sharpmod_rs`, to prove that the native extension is actually installed.

It exits with an actionable error instead of benchmarking Python fallback under
a Rust label.

## Local GRIB decoding benchmark

`benchmark_decoding.py` compares the frozen pre-optimization decoder in
`legacy_decoding.py` with the production Python and Rust direct point decoders.
The old Rust case is the historical cfgrib/xarray path with native wind
post-processing; the old extension did not decode GRIB messages itself.

Supply an explicit local fixture so network transfer and provider availability
cannot contaminate the measurement:

```powershell
python benchmarks\benchmark_decoding.py `
  --grib C:\path\to\fixture.grib2 --lat 35.18 --lon -97.44 `
  --repeat 3 --warmup 0 `
  --stages application-cold warm-inventory-point-miss point-cache-hit `
  --output benchmarks\results\decoding.json
```

`application-cold` explicitly disables the legacy cfgrib index and clears the
optimized decoder's application caches before every sample. It does not flush
the operating-system file cache. `warm-inventory-point-miss` retains the
legacy persistent cfgrib index or the Python message inventory but requests a
different point. The Rust direct decoder has no separate inventory cache, so
that stage is reported as unavailable rather than silently measuring a
different condition. `point-cache-hit` measures the bounded exact-point caches;
the frozen decoders correctly report that stage as unavailable.

Before timing, the default run requires exact Python/Rust agreement within the
legacy and optimized generations for values, missing masks, pressure ordering,
and selected grid point. Every legacy pressure level is then compared with the
optimized result; optimized-only published levels are retained and recorded
instead of forcing direct iteration down to a cfgrib hypercube's smaller level
set. Core thermodynamic and wind columns remain strict across generations.
Optional omega is strict between Python and Rust within a generation, while
cross-generation availability/value differences are retained explicitly in
the JSON instead of treating a legacy scalar-pressure xarray broadcast as
ground truth. Optional surface-vorticity metadata is likewise compared between
Python and Rust within each generation; products without a published
vorticity field may retain a production-only xarray wind-gradient fallback.
Implementations are alternated in forward/reverse order across repeats. Use
`--skip-equivalence` only when equivalence was already validated in the same
build, and retain the JSON output when publishing results.

### All enabled forecast models

`benchmark_model_matrix.py` runs that same local-GRIB comparison in a fresh
Python process for every model currently returned by
`model_extract.available_models()`. A fixture manifest must contain either one
complete pressure-level subset or an explicit unavailability reason for every
enabled model. Missing manifest coverage is rejected, while unavailable and
failed models are shown explicitly rather than disappearing from the table.
Start from `model_fixtures.example.json` and keep large GRIB fixtures outside
the repository.

Each fixture must contain one model run, forecast hour, and valid time. Provider
byte-range downloads can contain unrequested messages in the gaps between
ranges. Canonicalize such a download before benchmarking so the direct and
legacy decoders see the same field set:

```powershell
python benchmarks\prepare_grib_fixture.py `
  C:\path\to\provider-subset.grib2 `
  C:\path\to\canonical-fixture.grib2
```

The canonicalizer copies complete physical GRIB messages selected by the
direct inventory. That preserves U/V multi-field messages while removing
unrelated gap records. It writes through a same-directory temporary file and
atomically replaces the destination only after a successful copy.

```powershell
python benchmarks\benchmark_model_matrix.py `
  --manifest C:\path\to\fixtures.json `
  --repeat 3 --warmup 0 `
  --output-json benchmarks\results\all-model-decoding.json `
  --output-markdown benchmarks\results\all-model-decoding.md
```

Network retrieval is excluded from every timing. The generated Markdown table
reports application-cold old/optimized Python and old-hybrid/optimized Rust
times, within-backend speedups, optimized Python-to-Rust ratio, decoded level
count, cross-generation omega status, and the production decode path. The
companion JSON retains fixture hashes, raw samples, selected grid coordinates,
equivalence results and scope, and environment metadata. Add optional
`pressure_level_count` metadata to each
fixture entry when acquisition knows the expected distinct level count; the
matrix rejects a result whose decoded count differs. The
`production_decode_path` string is manifest-declared metadata and must be
populated from the inspected live model route; the benchmark cannot infer it
from arbitrary GRIB bytes. This path distinction matters: products without a
published vorticity field retain the full xarray wind-gradient fallback in the
production model workflow. Their direct point-decoder timing is still measured,
but it is not an end-to-end timing of that production route.

The default matrix asks for both application-cold and point-cache-hit stages.
Old implementations are expected to report the cache-hit stage as unavailable,
but both optimized implementations must produce it. Other requested stages are
validated against their documented availability, and every required timing
must contain the requested number of finite samples. The matrix also rejects
duplicate fixture bytes assigned to different model names.

## Scenarios

| Scenario | Values | Purpose |
| --- | ---: | --- |
| Small profile | 32 | Shows call and conversion overhead on a compact sounding. |
| Ordinary profile | 128 | Represents an ordinary atmospheric profile workload. |
| Large vector | 100,000 | Measures one array-oriented call where native loops may amortize the boundary crossing. |
| Large profile batch | 2,048 profiles x 128 levels | Measures the two shape-aware wind kernels on a real two-dimensional batch. |
| Repeated operations | Configurable, default 1,000 calls | Measures repeated ordinary-profile calls rather than one unusually large array. |
| Scalar pressure interpolation | 32 and 128 levels, one 700 hPa target | Mirrors the scalar lookup shape used by SharpTab pressure helpers instead of using a target vector as large as the profile. |
| Repeated profile fields | Six fields at 700 hPa on 128 levels | Measures the common pattern of resolving height, temperature, dewpoint, `u`, `v`, and omega against one pressure grid. One reported call is one six-field bundle. |
| Cached-selector `Profile` construction | 128 levels | Constructs the real `Profile` type after resolving a forced backend outside the timer, so the public facade and cached selector remain in the measured path. |
| Two-thread diagnostic | Configurable, default 100,000 values and 10 calls per worker | Compares the same total work sequentially and in two persistent Python threads. It is diagnostic output, not a scaling assertion or gate. |

The harness times the shared array kernels
`wind_to_components`, `components_to_wind`, and `interpolate_1d`. It validates
Python/Rust numerical agreement before timing, including both scalar profile
sizes, every repeated field, two-dimensional wind inputs, and threaded return
values. Parsing, QC, and pressure-record normalization should be added with
representative data once their benchmark corpora are stable; synthetic rows
alone are not evidence for real decoder performance. Batched interpolation is
not reported because the current backend contract interpolates one coordinate
series at a time; treating repeated pressure grids as one flattened series
would produce a misleading benchmark.

The concurrency table compares two sequential workers with two persistent
`ThreadPoolExecutor` workers performing the same number of calls. A ratio below
one indicates overlap in that run; it does not establish general parallel
scaling. The current Rust bindings intentionally retain the Python GIL while a
kernel is running. They borrow NumPy storage through `PyReadonlyArray1` and use
the resulting slices directly; releasing the GIL without first owning the data
or otherwise excluding mutation could let another Python thread alter that
storage while Rust reads it. The diagnostic exists to quantify whether a future
safe ownership or copying design is worth its overhead before changing that
contract.

## Run

```powershell
$env:SHARPMOD_BACKEND = "rust"
python benchmarks\benchmark_backends.py
```

Useful options:

```powershell
python benchmarks\benchmark_backends.py --repeat 12 --warmup 3
python benchmarks\benchmark_backends.py --repeated-calls 5000
python benchmarks\benchmark_backends.py --large-size 250000
python benchmarks\benchmark_backends.py --batch-profiles 4096 --batch-levels 128
python benchmarks\benchmark_backends.py --profile-constructions 1000
python benchmarks\benchmark_backends.py --concurrency-size 250000 --concurrency-calls-per-worker 20
python benchmarks\benchmark_backends.py --cpu-model "CPU model" --power-mode "Balanced"
```

The script reports median, minimum, and maximum elapsed time for each backend
and case. Each repeat alternates whether Python or Rust is measured first to
reduce ordering bias; the default ten repeats give each backend the same number
of first measurements. The harness deliberately reports raw measurements
without a pass/fail result, a claimed winner, or a CI performance gate.

## Reproducible records

When citing a result, retain the complete header printed by the script and also
record:

- CPU model and power mode;
- operating system and architecture;
- Python and NumPy versions;
- Rust compiler and `sharpmod_rs` versions;
- whether `maturin develop --release` was used;
- command-line options and whether other heavy processes were running.

Compare like-for-like runs. Debug Rust builds, different NumPy major versions,
virtual machines, thermal throttling, and shared CI runners can materially alter
the result. Re-run after changing an input-normalization layer because an extra
copy can dominate a small-profile measurement.

The complete 2026-07-15 Windows development snapshot used by the Rust backend
guide is retained in
[`results/2026-07-15-windows-amd64.txt`](results/2026-07-15-windows-amd64.txt).
That snapshot predates the scalar, repeated-field, real-`Profile`, and
two-thread scenarios; retain a new full output before making claims from those
rows.

The real-HRRR decoder comparison recorded on 2026-07-16 is retained in
[`results/2026-07-16-decoding-windows-amd64.txt`](results/2026-07-16-decoding-windows-amd64.txt).

The v0.4.0 canonical 13-model matrix is retained as a readable
[`Markdown table`](results/2026-07-16-all-model-decoding-windows-amd64.md) and
complete [`JSON record`](results/2026-07-16-all-model-decoding-windows-amd64.json).
Its NAM row includes an isolated timing stall; the stable five-repeat
confirmation is retained in
[`results/2026-07-16-nam-decoding-v0.4.0-windows-amd64.json`](results/2026-07-16-nam-decoding-v0.4.0-windows-amd64.json).
