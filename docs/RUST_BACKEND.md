# Rust-primary backend

SHARPpy Reimagined v0.4 supports `sharpmod_rs` as its primary numerical and
direct pressure-level GRIB point-decoding backend. Official Windows executables
bundle the extension, and default `auto` mode selects Rust after validating its
package version, backend API, and required operations. Rust works behind the
Python facade; the PySide6 GUI, renderer, command-line tools, SHARPpy widget
stack, model retrieval, and profile orchestration remain shared Python
application layers.

The independently optimized Python backend remains fully functional and is the
portable fallback. Source and Python-only installations therefore do not need
Rust, Cargo, or maturin, and a missing or unloadable extension does not prevent
the application from starting in default `auto` mode.

## Backend selection

Set `SHARPMOD_BACKEND` before starting Python:

| Value | Behavior |
| --- | --- |
| `auto` | Default. Use Rust when the extension loads successfully; otherwise use Python and retain the fallback reason for diagnostics. |
| `python` | Always use the optimized Python implementation. The Rust extension is not required. |
| `rust` | Require the Rust implementation. Missing or unloadable native code produces a clear error instead of silently falling back. |

PowerShell examples:

```powershell
$env:SHARPMOD_BACKEND = "auto"
$env:SHARPMOD_BACKEND = "python"
$env:SHARPMOD_BACKEND = "rust"
```

POSIX shell examples:

```bash
export SHARPMOD_BACKEND=auto
export SHARPMOD_BACKEND=python
export SHARPMOD_BACKEND=rust
```

An unset variable is equivalent to `auto`. An unsupported value is an error;
it is not treated as `auto`.

Inspect the resolved state through the Python backend layer:

```python
from sharpmod.backends import backend_info

print(backend_info())
```

The diagnostic reports the requested and active backends, whether
`sharpmod_rs` is discoverable, its version when it was loaded, and the fallback
reason when `auto` selected Python. In forced `python` mode the selector does
not import the native module, so `rust_installed` can be true while
`rust_version` remains `None`. Application code should import through
`sharpmod.backends`; direct `sharpmod_rs` imports are reserved for extension
development and benchmark verification.

An importable native module is not accepted on import success alone. Before
activation, the selector requires its package version to match `sharpmod`
exactly, its integer backend API version to equal the Python contract (currently
`2`), and all seven operations listed below to be callable. In `auto` mode an old
or incomplete extension falls back to Python and records the compatibility
failure in `fallback_reason`; in forced `rust` mode the same mismatch raises
`BackendUnavailableError` instead of reaching an operation with stale native
code.

Backend selection is process-local. Set the environment variable before the
first backend operation rather than changing it while the GUI or workers are
already running.

## Accelerated scope

Both backends expose the same named operations:

- `wind_to_components` converts meteorological wind direction and speed to
  unit-preserving `u` and `v` components.
- `components_to_wind` converts `u` and `v` components to meteorological wind
  direction and unit-preserving speed.
- `interpolate_1d` performs the backend's one-dimensional profile
  interpolation contract.
- `basic_sounding_qc` performs the documented structural and basic-value checks
  for sounding columns.
- `pressure_sort_dedup_indices` returns the ordering/selection needed to sort
  pressure levels and remove duplicates consistently across all aligned
  columns.
- `parse_sounding_rows` parses the deliberately narrow, simple sounding-row
  representation supported by the backend contract.
- `decode_grib_point` scans a local GRIB inventory, selects the nearest point
  once per distinct grid definition, verifies a consistent selected point, and
  returns all sounding columns as one C-contiguous NumPy matrix.

The shared behavior includes missing values, masks, NaNs, stable ordering,
boundary results, error conditions, units, and output types. Python/Rust
equivalence tests use explicit floating-point tolerances. Unit conversions
remain the caller's responsibility unless an operation's contract says
otherwise.

The wind and interpolation operations default to no explicit sentinel. The QC,
pressure ordering, and row-parser operations default to `-9999`; passing
`missing=None` disables explicit-sentinel matching. Parser blanks and
non-finite values are normalized to `NaN` in that mode. Both backends apply
these rules identically.

The simple-row parser follows Python `float()` for Unicode decimal digits:
the `Nd` blocks in the target interpreter's Unicode table—including
Arabic-Indic, Devanagari, fullwidth, and mathematical digits—are normalized to
ASCII before the native parse. CPython 3.12's Unicode 15 table additionally
accepts Kawi and Nag Mundari digits; the CPython 3.11 wheel follows its Unicode
14 table and rejects those two newer blocks. Numeric characters outside the
decimal-digit category, such as Roman numerals and superscripts, remain errors.
This deliberately mirrors Python `float()` rather than Rust's broader
`char::is_numeric` predicate.

No Qt, rendering, forecast downloading, or broad meteorological calculation has
been ported. The Rust decoder receives a cache-owned local file from the Python
retrieval layer. Rayon is not a dependency: profiling did not justify parallel
decoding for ordinary point soundings, and calls made by the Rust decoder remain
serialized.

Repository-owned application call sites delegate wind-component creation,
generic profile interpolation, ERA5/WRF wind conversion, pressure sorting and
deduplication, and forecast-model GRIB point extraction through the facade.
Basic QC and simple-row parsing remain equivalence-tested APIs without changing
the SPC, UWyo, BUFKIT, WRF, or other decoder pipelines.

### Direct GRIB behavior

Forecast retrieval still uses Herbie and the existing provider/subregion
routes. The selected inventory is reused for transfer planning, and a complete
local subset is handed to the active decoder without constructing xarray data.

The Python decoder scans message headers once per file identity, performs one
ecCodes nearest-point lookup, reads one scalar per selected field/level message,
and keeps bounded inventory, nearest-point, and decoded-point LRUs. The Rust
decoder memory-maps the file, locates message boundaries without copying, uses
ecCodes handles that borrow each mapped message, and returns all nine columns in
one NumPy-compatible matrix through one Python call. Its adapter keeps a bounded
exact-point cache. Both caches include file size and modification time in their
keys and invalidate when the downloaded file changes.

Optimized Python and Rust return matching omega values and missing masks at the
published pressure levels. For GEFS, the value published only at 850 hPa stays
missing at every other level. The old/new benchmark's `12 -> 1 valid` omega
difference records removal of the frozen legacy xarray full-column broadcast;
it is not missing Rust functionality or a Python/Rust equivalence exception.

`SHARPMOD_GRIB_DECODER=auto` is the default. Unsupported layouts fall back to
cfgrib with its persistent on-disk index; split groups are reduced to at most a
3-by-3 neighborhood before xarray merging. This preserves the existing
neighbor-wind surface-vorticity calculation without materializing whole model
grids. Use `xarray` to force that compatibility path or `direct` to make a
direct-decoder error explicit.

## Python-only installation

Existing setup continues to work unchanged:

```powershell
py -3.11 -m venv .gribenv
.\.gribenv\Scripts\Activate.ps1
python -m pip install -e ".[dev,render]"
python -m pip install --no-deps "SHARPpy==1.4.0a5"
```

With no extension installed, `auto` uses Python. `python` also works normally,
while an explicit `rust` request reports that the extension is unavailable.

## Build the local Rust extension

Install Rust 1.88 or newer from the stable channel and make `cargo` available
on `PATH`. Then install the optional build frontend into the same `.gribenv`
that will import the extension:

```powershell
.\.gribenv\Scripts\Activate.ps1
python -m pip install -e ".[dev,rust-build]"
sharpmod-rust-sync
sharpmod-rust-sync --check
```

`sharpmod-rust-sync` compares the installed native distribution with the
checkout version. It performs a locked release rebuild only when the extension
is missing or stale, installs it into the active virtual environment, and then
requires forced-Rust selection in a fresh Python process. `--check` performs
the version and selector checks without modifying the environment. Use
`sharpmod-rust-sync --force` after changing native source even when the package
version did not change.

The lower-level extension-development path remains available:

```powershell
Set-Location rust\sharpmod-rs
maturin develop --release --locked
Set-Location ..\..
```

`maturin develop` installs `sharpmod_rs` into the active environment; it does
not modify the normal setuptools build backend for `sharpmod`. From a POSIX
shell, activate `.gribenv/bin/activate` and use the same sync command or crate
directory commands. Windows source builds also require the MSVC C/C++ linker
toolchain (normally installed through Visual Studio Build Tools).

Confirm the extension and selector:

```powershell
python -c "import sharpmod_rs; print(sharpmod_rs.__version__)"
$env:SHARPMOD_BACKEND = "rust"
python -c "from sharpmod.backends import backend_info; print(backend_info())"
```

## Tests and checks

Python-only tests must pass without building Rust:

```powershell
Remove-Item Env:SHARPMOD_BACKEND -ErrorAction SilentlyContinue
python -m pytest
$env:SHARPMOD_BACKEND = "python"
python -m pytest sharpmod/tests -k "backend or rust"
```

Run the native checks from the crate and then run the equivalence tests with
Rust required:

```powershell
Set-Location rust\sharpmod-rs
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
maturin develop --release --locked
Set-Location ..\..

$env:SHARPMOD_BACKEND = "rust"
python -m pytest sharpmod/tests -k "backend or rust"
```

Rust-specific Python tests skip with an explicit reason when `sharpmod_rs` is
not installed during a normal Python-only run. Forced-Rust selector tests must
still verify the error path without depending on a globally installed
extension.

## Benchmarks

The cross-backend harness requires a built extension and compares the Python
and Rust backend methods on small profiles, ordinary profiles, a 100,000-value
vector, a 2,048-by-128 batch of profiles, repeated calls, scalar 700 hPa
lookups on 32/128 levels, six repeated fields, and real cached-selector
`Profile` construction. It also prints an equal-work sequential-versus-two-
thread diagnostic:

```powershell
$env:SHARPMOD_BACKEND = "rust"
python benchmarks\benchmark_backends.py
```

See [the benchmark guide](../benchmarks/README.md) for methodology and
reproducibility guidance. Benchmark output is evidence to inspect, not a pass
criterion. No operation is described as faster without recorded results on the
relevant workload, and common single-profile operations may remain preferable
in Python when boundary-crossing or conversion overhead dominates.

The two-thread table is observational, not a pass/fail result or a promise of
parallel scaling. Array kernels retain the Python GIL while borrowing NumPy
input slices; releasing it could allow another Python thread to mutate storage
that Rust is reading. GRIB decoding is different: its inputs are immutable path
and scalar values, so PyO3 releases the GIL for the decode and returns one
capsule-backed NumPy matrix. Calls made by the Rust decoder are serialized
because the local point workload did not show a reason to add speculative
parallel decoding. Python ecCodes/cfgrib calls use a separate lock, so callers
should not mix the two decoder implementations concurrently in one process.

### Recorded local result

The following snapshot was recorded on 2026-07-15 on a 12th Gen Intel Core
i5-1235U in Windows Balanced mode, with Python 3.11.14, NumPy 2.4.6,
`sharpmod_rs` 0.3.1, and Rust 1.97.0. The harness alternated measurement order
across 20 repeats after five warmups. The complete header, command, medians,
minima, and maxima for every measured row are retained in the
[raw result](../benchmarks/results/2026-07-15-windows-amd64.txt).

Representative median milliseconds per call were:

| Workload | Operation | Python | Rust | Local observation |
| --- | --- | ---: | ---: | --- |
| 100,000 values | `wind_to_components` | 4.619510 | 3.816955 | Rust median about 17% lower |
| 100,000 values | `components_to_wind` | 5.626450 | 4.531640 | Rust median about 19% lower |
| 100,000 values | `interpolate_1d` | 3.766315 | 4.706360 | Python median about 20% lower |
| 2,048 x 128 values | `wind_to_components` | 11.361770 | 7.825710 | Rust median about 31% lower |
| 2,048 x 128 values | `components_to_wind` | 16.628920 | 12.481090 | Rust median about 25% lower |

This is a noisy local observation, not a cross-platform guarantee; several
small and repeated-operation ranges overlapped substantially and are not used
for speed claims. Large wind-vector batches showed the clearest Rust benefit,
while NumPy remained preferable for the large interpolation case. `auto`
selects one backend for all operations rather than making a per-call performance
decision, so benchmark the real workload and use `python` explicitly when its
mix favors the Python implementation.

### Recorded GRIB decoder result

On 2026-07-16, a three-sample run used a 181,267,625-byte HRRR pressure-level
subset at 35.18 N, 97.44 W (40 output levels). The OS file cache was warm, while
application decoder caches and cfgrib indexes were disabled for each cold
sample. "Old Rust" is the historical hybrid path: cfgrib/xarray still performs
the complete decode and Rust only converts the extracted wind column.

| Comparison | Old median | Optimized median | Speedup |
| --- | ---: | ---: | ---: |
| Python | 33.961649 s | 6.601793 s | 5.14x |
| Rust / historical hybrid | 31.398744 s | 6.614370 s | 4.75x |

The optimized cold decoders were effectively tied in this run (Python was
0.19% lower). With a persistent cfgrib index versus a warm direct-Python
inventory at a new point, the medians were 19.569670 s and 7.254023 s (2.70x).
For an exact decoded-point cache hit, 100 samples had medians of 0.165650 ms in
Python and 0.110250 ms in Rust. See the dated raw result in
`benchmarks/results` for minima, maxima, environment details, and the exact
command.

## Platform status

The crate is portable Rust stable code. The dedicated
`.github/workflows/rust.yml` workflow is configured to build, install,
smoke-test, equivalence-test, and retain CPython 3.11 and 3.12 wheel artifacts
on:

- Windows x86_64
- Linux x86_64
- macOS arm64
- macOS x86_64

Those jobs prepare downloadable native-wheel CI artifacts; the wheels are not
published as a separate package-index release. The official v0.4 Windows
release workflow builds and installs the extension before constructing and
runtime-checking the one-folder and one-file PyInstaller applications. Rust is
therefore the supported primary backend in those binaries. The Python-only
package remains the portable fallback for source installs and platforms without
a compatible extension.

## Current limitations

- Native wheels are CI/build artifacts rather than a separately published
  package-index release. Official Windows desktop binaries bundle the native
  extension directly.
- The normal `sharpmod` wheel and sdist contain the Python backend layer but not
  the Rust crate or extension. The `[rust-build]` extra installs maturin only;
  a repository checkout is required for the documented source build.
- The prepared wheels target CPython 3.11 and 3.12 separately and are not
  `abi3` wheels. Each additional Python minor version requires its own native
  wheel matrix entry.
- NumPy masked arrays, non-contiguous views, non-`float64` data, and sentinel
  normalization can require Python-side validation or copies. Zero-copy is an
  optimization for compatible arrays, not an API guarantee.
- The declared NumPy range spans NumPy 1.x and 2.x; native compatibility must be
  tested across that range before publishing wheels.
- Small arrays can cost more to cross the Python/Rust boundary than they save in
  computation.
- Array kernels retain the GIL while they borrow NumPy input slices. Direct
  GRIB decoding releases it, but Rust-decoder ecCodes access is serialized;
  concurrent point decodes are not currently expected to scale. Python
  ecCodes/cfgrib calls are protected separately rather than by a shared
  cross-language lock.
- Direct GRIB caches are process-local and bounded. A cold request still scans
  message headers and ecCodes must unpack one selected element per field/level.
- When a downloaded inventory has no usable relative/absolute-vorticity field,
  model extraction keeps the xarray compatibility path so the existing
  neighbor-wind vorticity estimate is preserved.
- Parsing is intentionally limited to the backend's simple row format. Existing
  SPC, UWyo, BUFKIT, WRF, and other decoders remain Python implementations.
- QC is basic and structural. It does not replace the complete SHARPpy profile
  validation and meteorological analysis paths.
- The PyInstaller spec includes `sharpmod_rs` only when the extension is
  discoverable and collected successfully in the environment performing the
  build. The official v0.4 release job builds and installs it before packaging;
  custom local builds that omit it log a warning and produce a fully functional
  Python-fallback bundle.
