# Rust-primary backend

SHARPpy Reimagined supports `sharpmod_rs` as its primary numerical and
direct pressure-level GRIB point-decoding backend. Official Windows executables
bundle the extension, and default `auto` mode selects Rust after validating its
package version, backend API, and required operations. Rust works behind the
Python facade; the headless Qt renderer, command-line tools, SHARPpy widget
stack, model retrieval, and profile orchestration remain shared Python
application layers. The current application UI is Tauri/React; the old Qt
picker/viewer is retired and remains available in the legacy release.

The [Rust-first performance plan](performance/RUST_MIGRATION_PLAN.md) and
[current architecture audit](performance/PERFORMANCE_AUDIT.md) track measured,
progressive modernization. Native kernels, decoder time, network retrieval,
rendering and IPC are measured separately before selecting a migration.

The Python numerical backend remains available for scientific compatibility.
Map contours and wind barbs, radial radar sampling, velocity tilt availability, the radar site catalogue,
NWS warning/watch processing, nearby surface observations, and IEM report fetching/parsing require the native extension
in browser mode. The desktop calls the shared Rust weather core directly for
radar inventory. These migrated operations have no Python implementation.
UWyo sounding decoding and availability, box-mean soundings, threshold
controls, radar mosaics and satellite processing also use the native extension.
The desktop sidecar and browser bridge require a matching `sharpmod_rs` build
for request validation and framing, including when `SHARPMOD_BACKEND=python`.
Install it before running the application from source.

Sounding file uploads use Rust for request validation, bounded base64 decoding,
portable filename handling and staged cache files. The existing decoder registry
still owns NPZ, SPC, BUFKIT, PECAN, UWyo and custom-format interpretation; profile
values and metadata do not pass through a new scientific calculation. Failed
decoding, summary construction or registration rolls back the files created by
the import. Creation never replaces an existing cache file. Session restoration
shares the same staged-file owner. The public 5 MiB limit and bridge result
shapes are unchanged.

Observed opening now uses Rust for provider fallback, validation before provider
selection, portable arrays and metadata assembly, bridge registration and failed
open rollback. Provider adapters and the atomic NPZ/sidecar transaction retain
their existing public interfaces. Invalid downloaded levels, optional omega,
coordinates or timestamps can fall back to the next configured archive without
combining measurements from different providers.

The v3.8.5 migration batches also move the display thermodynamic workspace,
selected severe indices, portable-file validation and pair publication,
model/ERA5/WRF opening transactions, WRF column transforms, point-provider
orchestration, HRRR F000 Zarr planning/sampling and compatibility transfers into
Rust. Sounding editing and inspection retain Qt's exact transforms while Rust
assembles records and validates edits before mutation. See the
[batch scope and validation report](performance/RUST_MIGRATIONS_3_8_5.md)
for the retained boundaries and reproduction commands.

The opened-profile registry also lives in Rust. The UI synchronizes its open
list through `retain_soundings` (`soundings.retain` on the wire), so background
opens cannot evict an older visible profile. Removing an ID from that list
makes it eligible for ordinary LRU eviction again. An active render or recovery
request keeps its source files alive until its final reference is released;
the registry remains bounded at 24 entries.

The sounding bridge and CLI PNG reports share a native header/dimension check.
It requires the PNG signature, a 13-byte IHDR and nonzero width and height, and
does not decode or modify pixels. It is a header check, not a replacement for
full PNG decoding by the image consumer.

The sounding height charts use the native extension while retaining their
Python result objects and CLI commands.

IGRA sounding header scans, fixed-width level decoding, humidity-derived
dewpoints and decreasing-pressure assembly also require Rust. Their thin
adapters retain the public IGRA objects, timestamps, units and provenance counts.
The native parser borrows immutable archive text and scans canonical LF/CRLF
records without materializing every line; unusual Python line boundaries retain
their existing interpretation. Archive selection, ZIP retrieval and disk-cache
ownership remain in the existing reader.

Accelerated sounding profiles use Rust for SARS hail/supercell analogue
matching when the native operation is available. Without it, they retain the
existing SHARPpy compatibility call. Parsed tables are reused until their source
path, size or modification time changes; unavailable or malformed tables retain
the empty-match behavior.
The compatibility boundary preserves tuple and NumPy array types, match order,
the original loose/quality thresholds, signed helicity, hemisphere aliases and
the hail display limit. SHIP and the existing profile inputs retain their
current scientific helpers; the reference SARS functions are not patched.

Accelerated display profiles also share prepared Rust interpolation for fixed-height
winds, pressure-weighted layer means and endpoint shears. The same layer mean is
reused for right/left storm-relative winds, preserving one-hPa sampling, fractional
layer bounds and separate masks for each component. Bunkers motion, helicity,
Corfidi motion and critical angle retain the existing SHARPpy formulas. Profile
construction and storm-motion edits use this path; source environments without
the native operation keep the reference implementation.

SharpTab's custom-layer `mean_wind`, `mean_wind_npw` and `helicity` also use
optional native operations. Standard cached layer results still take precedence.
Custom pressure means retain the exact requested top, fractional increments,
separate component masks and storm-motion subtraction. Helicity retains the
reported interior levels, endpoint interpolation, positive/negative sums and
the same knots-to-metres-per-second conversion. The existing public tuples and
masked constants are unchanged.

These operations copy mask-aware, potentially strided/read-only input columns
before releasing the interpreter. Mean integration streams pressure samples
through prepared interpolation, without allocating a sampled wind grid. The
native mean rejects more than one million pressure intervals, matching the
existing native display-layer safety budget. Other invalid bounds/increments
keep their missing-value result. Explicit Python science and older compatible
extensions without these optional operations retain the existing implementation;
the required backend API version is unchanged.

Portable-sounding physical quality checks use an optional Rust operation after
the existing archive, dtype, shape and level-count validation. Six owned NumPy
data snapshots feed the same single-pass core as basic QC. This preserves the
portable validator's issue order, `-9999`/non-finite missing values and advisory
dewpoint reports; it does not introduce basic QC's absolute-zero checks into
the portable format. NumPy `asarray` semantics, including underlying masked-array
data, strides and read-only arrays, remain unchanged.

Model-cache entry assembly is also optional Rust. It returns the existing
`CacheEntry` objects with stable newest-first ordering, field coercions, paths,
pins and protection flags. Malformed optional container metadata and null
source-field lists no longer stop Storage from loading, including in the
existing Python fallback. The native path presents non-finite access times as
zero and non-iterable source fields as empty. It does not rewrite metadata or
change cache discovery, pruning, filesystem validation or deletion ownership.
Both adapters retain the existing implementation in explicit Python mode or
when an older compatible extension lacks the new operation.
Cache inspection and portable-file physical validation remain available if the
scientific backend cannot load or its selection is invalid; scientific requests
retain their normal backend error reporting.

Cache pruning optionally runs its retention decisions in Rust too. It preserves
stable LRU ordering (including equal signed-zero times), expiration-before-size
eviction, failed-deletion accounting and the second-pass retry. Filesystem sizes
are accumulated as integers, including limits above floating-point precision;
arbitrarily large Python budgets remain valid. The existing cache owner performs
removal and rechecks active leases immediately beforehand. Timestamp parsing
preserves otherwise valid pin/provenance metadata and falls back to the metadata
file's modification time for malformed/non-finite access times. This does not
make interprocess lease creation and filesystem removal one atomic operation.

## Backend selection

Set `SHARPMOD_BACKEND` before starting Python. This selects the numerical
backend; the migrated weather, map and IGRA operations always use Rust:

| Value | Behavior |
| --- | --- |
| `auto` | Default. Use Rust when the extension loads successfully; otherwise use Python and retain the fallback reason for diagnostics. |
| `python` | Use the Python scientific implementation. Migrated weather, map and IGRA operations still require `sharpmod_rs`. |
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
`sharpmod.backends` for numerical work. Thin weather/map compatibility adapters
call the native-only operations directly.

An importable native module is not accepted on import success alone. Before
activation, the selector requires its package version to match `sharpmod`
exactly, its integer backend API version to equal the Python contract (currently
`7`), and all required operations in `backends/protocol.py` to be callable. In `auto` mode an old
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
- `profile_kinematics` computes Bunkers storm motion plus the standard
  SFC-to-0.5/1/3/4/6 km shear, mean-wind, storm-relative-wind, and SRH
  workspace in one coarse-grained call.
- `profile_parcels` computes surface-based, lowest-300-hPa most-unstable, and
  100-hPa mixed-layer parcel summaries—including CAPE/CIN, LCL/LFC/EL, and
  CAPE through 3/6 km—in one coarse-grained call. Cached 0–6 km CAPE is used
  only when reported levels in that layer are no more than 500 m apart;
  coarser soundings retain the reference integration.
- `profile_convective_parcels` computes surface, forecast, most-unstable,
  mixed-layer, and mean-effective parcel summaries and virtual-temperature
  plotting traces together with effective-inflow-layer pressure bounds.
- `lift_parcel` computes one explicit user-selected parcel summary and plotting
  trace.
- `profile_dcape` computes DCAPE, its source pressure/downrush temperature, and
  the descending parcel-temperature trace.
- `profile_thermodynamics` prepares one owned profile snapshot and shares it
  across the three-parcel, traced convective, and DCAPE workspaces. Native
  traces cross the extension boundary as contiguous NumPy buffers plus offsets;
  the public Python API still exposes the existing immutable tuples.
- `profile_batch_analysis` evaluates complete independent soundings in stable
  input order. Calls below eight profiles are serial; larger calls default to a
  process-wide Rayon pool using all CPUs available to the process,
  remain serial when already inside Rayon, and accept explicit worker/cutoff
  overrides. Sharing the default pool keeps simultaneous GUI callers under the
  same native thread ceiling. Batch inputs normalize their missing sentinel
  during Rust's owned copy, avoiding a separate Python scan and temporary
  array per column. Masks still become NaN at the Python boundary, and native
  workers never borrow Python-owned input memory.
- `decode_grib_point` and `decode_grib_points` reuse a bounded immutable message
  inventory, select the nearest point once per distinct grid definition, and
  return each sounding as one C-contiguous NumPy matrix. The multipoint form
  opens each selected message once and asks ecCodes for every requested grid
  index together while retaining request order and duplicate grid cells. When
  verified surface messages are present, both forms remove below-ground
  isobars and prepend surface pressure/height, 2-m thermodynamics, and 10-m
  wind.
- The remaining required callables enable, clear, and report the native GRIB
  inventory cache so tests and benchmarks can distinguish cold scans from
  inventory reuse.

The shared behavior includes missing values, masks, NaNs, stable ordering,
boundary results, error conditions, units, and output types. Python/Rust
equivalence tests use explicit floating-point tolerances. Unit conversions
remain the caller's responsibility unless an operation's contract says
otherwise.

Native-only map/weather operations are `contour_segments`, `pack_wind_grid`,
`radar_sites`, `radar_velocity_tilts`, `clear_weather_cache`, `weather_alerts`,
`fetch_storm_reports`, `parse_storm_reports`, `parse_surface_stations`,
`parse_surface_observations`, `select_surface_observation`,
`fetch_surface_observations`, `sample_radar_radials`, `fetch_model_bytes`, and
`interpolate_height_grid`. Their PyO3 bindings live in
focused map, wind, weather and report modules. Blocking
native work releases the interpreter with `Python::detach`, following the
[PyO3 performance guidance](https://pyo3.rs/main/performance.html).
The HTTP and alert-processing implementation is shared with Tauri in
`rust/weather-core`; the source distribution includes that crate and its radar
catalogue. NWS requests preserve the official service's
[4000-record limit and current-only semantics](https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer),
including CAP confirmation before accepting an all-clear. Snapshot history
remains under the existing session cache owner.

IEM parsing preserves quoted/multiline CSV fields, UTC report times, natural
units and row-wise rejection of invalid coordinates, dates or hazard codes.
The binding returns the existing `StormReport` objects; filtering, layer
construction and report memory/disk caches retain their Python owner. Wind
packing copies only selected display samples before detaching and uses the existing Rayon pool,
retaining five-knot bins, one-degree directions and the missing-cell sentinel.

Surface fetching follows the NWS point-to-station links, limits queries to the
nearest 32 stations (24 by default), and runs at most six station requests at a
time. Parsing and unit conversion occur before nearest-time selection, so only
matched observations cross back into the existing Python objects. Missing and
failed stations stay distinct, older observations win equal-distance time ties,
and cancellation aborts pending native requests. Supplied Python openers remain
supported at the compatibility boundary. Live data retains the documented
[NWS observation availability and latency limits](https://www.weather.gov/documentation/services-web-api).

Radar sampling uses sorted native radials and the existing cached WGS84 bearing
and range arrays. The Rust loop retains native codes and gate/sector boundaries;
the existing palette and PNG encoding keep calibration metadata and transparency.
Owned snapshots release the interpreter safely, copying only 64 projection rows
at a time (at most 2.1 MB at the 4096-pixel width), plus native radial codes and
the output. The existing single-entry projection cache retains its previous
lifetime.

Forecast-map inventories and GRIB byte ranges use the shared native model HTTP
client. It retains 32 idle connections per host, the existing identity encoding
and Range headers, two transient retries, Retry-After handling, response byte
budgets and the provider's exception classes. Timeouts apply to pending headers
and individual reads; active large transfers are not cut off by a whole-request
deadline. Cancellation is checked while headers, chunks or retry delays are
pending. Range scheduling, decoding and cache ownership remain with their
existing callers. The separate resumable sounding download transport retains
its Python implementation.

Terrain-relative lapse-rate and derived low-level shear maps interpolate their
height stacks in Rust. The boundary validates grid layouts and keeps
float32/float64 source precision; the kernel chooses the first finite bracket
without clamping targets outside the supplied levels. Owned batches of at most
131,072 columns release the interpreter. Contiguous model inputs avoid full-stack
copies and full-grid masks and weight arrays; Fortran and strided inputs are normalized at
the boundary. Product formulas, the lapse-rate CAPE screen and the model's
surface height retain their existing owners.

DCP's downdraft energy and the uncached surface/most-unstable parcel helpers
also delegate to the existing parcel backend. This removes duplicate SHARPpy
profile construction from derived parameters without adding new kernels or
another cache. Normalized CAPE/CIN, EHI and VGP retain their cached workspace
path, finite/missing guards and layer-depth rules. An explicitly selected
Python-science backend still supplies these terms through its existing adapter.

The accelerated sounding profile also uses `parcel_maximum_level` for its
display parcels. It snapshots reported pressure, height, temperature and virtual
temperature, then integrates the legacy maximum-level energy and 1 hPa
refinement with the existing native lift/interpolation routines. Results retain
pressure and AGL height fields and their missing-value guards; the surrounding
SHARPpy parcel objects and explicitly selected Python profile remain intact.

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

The shared Qt renderer and forecast-source orchestration still use Python
compatibility layers. A Python
`AcceleratedConvectiveProfile` keeps SHARPpy's orchestration and public parcel
objects while delegating its standard parcel/effective-layer searches and
DCAPE integrations to the backend. The Skew-T user-parcel action delegates its
explicit ascent through the same boundary. Backend failure falls back to the
pinned SHARPpy routines. Advanced parcel fields that are not part of the traced
parcel summary contract
(for example cap strength and exact upstream MPL bookkeeping) remain
Python-side compatibility concerns. ECAPE also retains a Python MUCAPE check
for its exact pinned-oracle upper-bound contract. The Rust decoder receives a
cache-owned local file from the Python retrieval layer. Rayon is used only for
complete-profile analysis, where the measured 8-profile and 32-profile
workloads benefit. GRIB/ecCodes calls remain behind a process-wide native
mutex; batch analysis never enters ecCodes.

Repository-owned application call sites delegate wind-component creation,
generic profile interpolation, ERA5/WRF wind conversion, pressure sorting and
deduplication, standard SharpTab kinematics, standard/full parcel workspaces,
user parcel ascents, DCAPE, and
forecast-model GRIB point extraction through the facade. A real `Profile`
caches its immutable kinematics and three-parcel summary workspaces; full
convective profiles consume the extended workspace once during construction.
Box fast-tier analysis and built-in ensemble summaries submit complete ordered
profile groups through `profile_batch_analysis`; load failures retain their
original positions and fall back per profile without discarding successful
members. The bounded metric cache fingerprints all source columns and rebuilds
prepared results after either profile replacement or an in-place array edit.
Its native signature snapshots contiguous data and masks into immutable bytes,
then computes SHA-256 digests with the interpreter released. Shape, dtype and
the explicit metrics revision remain part of the signature; absent and opaque
columns retain their compatibility behavior. The private digest representation
is process-local cache state, not a persistent artifact or public API.
Nonstandard layers and any unavailable workspace continue through the existing
Python routines. Basic QC and simple-row parsing remain
equivalence-tested APIs without changing the SPC, UWyo, BUFKIT, WRF, or other
decoder pipelines.

Provider soundings also delegate their verified surface-row merge to Rust.
The native GRIB decoder shares the same ground-row validation, including the
0.5 °C saturation tolerance, wind limits and unit conversions. Provider
columns enter owned buffers before native filtering and assembly release the
interpreter; returned arrays remain contiguous float64 columns in the existing
`SurfaceMergeResult`. Below-ground and equal-pressure levels are removed
without sorting the remaining rows. Explicit Python-science selection and
environments with an older optional extension retain the existing Python merge.

MMP's maximum bulk-shear reduction also uses owned native wind endpoints.
It scans the low/upper endpoint pairs without materializing a pair matrix,
preserving masks and the reference square/sum/square-root overflow behavior.
Endpoint selection, interpolation, regression coefficients, lapse-rate and
mean-wind terms retain their existing owners. Python-science selection and
extensions without this optional operation use the existing reduction.

Ensemble CSV exports also use Rust. The boundary preserves metric order, missing
fields, and ensemble membership and status rules. Python scalar and timestamp
formatting retain their existing spelling. Rust writes quoted CSV records in
batches of at most 64 owned rows while the interpreter is released. It flushes,
syncs and closes a temporary file beside the destination before replacing the
export, with cleanup on failure.
Explicit Python science and older optional extensions retain the existing
export and fingerprint implementations. No new backend API version is needed
for these optional operations.

### Direct GRIB behavior

Forecast retrieval still uses Herbie and the existing provider/subregion
routes. The selected inventory is reused for transfer planning, and a complete
local subset is handed to the active decoder without constructing xarray data.

The Python decoder scans message headers once per file identity, performs one
ecCodes nearest-point lookup per distinct grid definition, vector-reads all
uncached requested indexes from each selected field/level message, and keeps
bounded inventory, nearest-point, and decoded-point LRUs. The Rust decoder
memory-maps the file, locates message boundaries without copying, uses ecCodes
handles that borrow each mapped message, and returns all nine columns in one
NumPy-compatible matrix per point. Its adapter batches unique uncached requests
and keeps a bounded exact-result cache without conflating different surface
grids.

Inventory identity includes the canonical path and size plus replacement-safe
filesystem metadata: volume/file index and creation/write timestamps on
Windows, or device/inode plus modification/change timestamps on Unix. An atomic
same-size replacement therefore invalidates both Python and native state. The
inventory stores immutable offsets and metadata only--never ecCodes handles--so
model-cache leases and mapped-file lifetimes remain unchanged. Python ecCodes
access uses its own re-entrant lock and all native ecCodes access uses one native
mutex.

For HRRR, retrieval includes `PRES` and `HGT` at the surface, `TMP` and `DPT`
at 2 m, and `UGRD` and `VGRD` at 10 m. Both direct decoders and the cfgrib/Zarr
compatibility routes use surface pressure to remove all subterranean isobars
before inserting the real ground row. Model extraction fails closed if this
verified merge is unavailable. The JSON sidecar records
`surface_pressure_hpa`, `surface_merged`, and
`below_ground_levels_removed`.

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
python scripts\install_sharppy_compat.py --extras dev,render
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

Run the native checks from the crate and install the extension before testing.
Some Rust tests call the public Python adapters through the installed extension.
Then run the equivalence tests with Rust required:

```powershell
Set-Location rust\sharpmod-rs
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
maturin develop --release --locked
cargo test --locked
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
vector, a 2,048-by-128 array batch, repeated calls, scalar 700 hPa lookups on
32/128 levels, six repeated fields, and real cached-selector `Profile`
construction. It also measures 32/128-level kinematics, diagnostics-only
parcels, traced convective parcels, DCAPE, shared thermodynamic preparation,
and complete 8-by-32 and 32-by-128 profile batches. Shared thermodynamics are
split into public-adapter, buffered-adapter, and direct-native rows; complete
batches compare serial, four-worker, and all-available-CPU default rows. An equal-work
sequential-versus-two-thread diagnostic remains observational:

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
parallel scaling. Small array kernels retain the Python GIL while borrowing
NumPy input slices; releasing it could allow another Python thread to mutate
storage that Rust is reading. The profile-kinematics and profile-parcel
bindings instead create one Rust-owned snapshot before releasing the GIL; their
internal prepared structures borrow that snapshot instead of copying the same
columns again. Complete-batch workers likewise receive only owned vectors.
GRIB decoding releases the GIL and returns capsule-backed NumPy matrices, but
native ecCodes calls remain serialized. Python ecCodes/cfgrib calls use a
separate lock, so callers should not mix decoder implementations concurrently
in one process.

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

The current API-7 optimization measurements, including the release baselines,
Criterion confidence intervals, complete-adapter JSON, fixed-fixture GRIB cache
states, batch worker evaluation, regressions, and validation commands, are in
[`2026-09-12-rust-backend-optimization.md`](../benchmarks/results/2026-09-12-rust-backend-optimization.md).

In the v3.5.5 candidate, the Rust decoder can retain up to 16 exact
single-point columns from GRIB files of at least 64 MiB. The key includes the
SHA-256 of the mapped file, the normalized coordinates, and the missing-value
convention. The existing cache controls also bypass or clear these columns.
For one 264,080,531-byte ECMWF IFS 9 km F012 subset, the median of three
repeat decodes in the same process went from 6.108 s to 0.279 s. The first
decode went from 7.352 s to 7.767 s, including the new fingerprint. The decoded
matrices, selected coordinates, vorticity, and surface metadata matched.
This measures local decoding of one saved subset, without provider downloads.

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
published as a separate package-index release. The official Windows
release workflow builds and installs the extension before constructing and
runtime-checking the one-folder and one-file PyInstaller applications. Rust is
therefore the supported primary backend in those binaries. The Python-only
package remains the portable fallback for source installs and platforms without
a compatible extension.

## Opt-in GRIB and transport checks

The ordinary Rust suite keeps the SHRF allocation-counter correctness test.
Run the transport timing separately in release mode on an idle machine:

```powershell
cargo test --manifest-path src-tauri/Cargo.toml --locked --release --test shrf_performance -- --ignored --nocapture --test-threads=1
```

`SHARPPY_SHRF_ITERS` controls the sample count (20 by default). The benchmark
uses bundled frame fixtures and measures decoding and frame assembly without
network access or image rendering.

The ignored IFS 9 km regression needs the saved Open Data **2026-11-05 00Z
F012** indexed GRIB subset: pressure fields `gh,t,u,v,r,w,vo`, the F012
surface companion, and F000 terrain from the same run. A surface-only file
cannot validate this regression. The 264,080,531-byte reference payload has
SHA-256 `f77fa5f1de818905fa17833b2432739a98151af4f6b9b7541dd1f2401039cdbf`.
The test opens the file twice and checks the decoded pressure levels, terrain
merge, below-ground filtering and equality after inventory reuse.

```powershell
$env:SHARPMOD_ECMWF_9KM_FIXTURE = 'C:\path\to\ecmwf-ifs-9km-2026110500-f012-verified-surface.grib2'
$env:SHARPMOD_ECCODES_LIBRARY = 'C:\path\to\eccodes.dll'
cargo test --manifest-path rust/sharpmod-rs/Cargo.toml --locked --release --test ecmwf_9km -- --ignored --nocapture
```

On Linux, use the absolute path to the installed `libeccodes.so`. Ensure the
library's dependent binaries are discoverable by the OS loader.

## Additional Python-to-Rust migrations (M9–M44)

While keeping `BACKEND_API_VERSION = 7`, `sharpmod_rs` and `sharpmod-weather`
also own additional compute, planning, and I/O modules registered in
`REQUIRED_RUST_CAPABILITIES`:

- **M9 Model field & HTTP byte-range planning** (`rust/sharpmod-rs/src/model_planning.rs`):
  `choose_noaa_fields`, `choose_ifs_fields`, `build_noaa_search`, `build_ifs_search`,
  `supports_noaa_surface_merge`, `supports_ifs_surface_merge`, `plan_model_ranges`,
  `parallelize_model_range_plan`, `parse_content_range_details`, and `valid_grib_file`
  backing `sharpmod.models.model_fields` and `sharpmod.models.model_transport`.
- **M10 NOAA RRFS / REFS NOMADS inventories & provenance** (`rust/sharpmod-rs/src/rrfs_nomads/`):
  `rrfs_domain_for`, `rrfs_build_url`, `rrfs_release_candidates`, `rrfs_remember_release`,
  `rrfs_forget_release`, `rrfs_parse_idx`, `rrfs_select_records`, `rrfs_pressure_field_plan`,
  `rrfs_write_provenance`, and `rrfs_read_provenance` backing `sharpmod.providers.rrfs_nomads`.
- **M11 Derived indices, fast STP, hazard, winter & fire** (`rust/sharpmod-rs/src/derived_indices/`):
  `fast_effective_stp_cin`, `max_thetae_vertical_velocity`, `derived_dcp`,
  `derived_ncape_ncin`, `derived_ehi`, `derived_vgp`, `derived_hpi`, `derived_peskov`,
  `derived_peskov_profile`, `derived_lscp`, `derived_nstp`, `derived_moshe`,
  `classify_hazard_inputs`, `kuchera_snow_liquid_ratio`, `column_max_temperature_c`, and
  `fire_ventilation_rate` backing `sharpmod.analysis.box_analysis_fast` and
  `sharpmod.sharptab.{derived,derived_thermo,derived_indices,hazard,winter,fire}`.
- **M12 Box grid regions, lattice, transects & statistics** (`rust/sharpmod-rs/src/box_grid_analysis/`):
  `box_region_from_corners`, `box_region_from_bounds`, `box_region_contains`,
  `plan_box_lattice`, `box_transect_column`, `box_transect_columns`, `box_field_statistics`,
  and `box_envelope_stats` backing `sharpmod.analysis.{box_sounding,box_analysis,box_analysis_core}`.
- **M13 Decoder cleanup & bridge gzip/storage cache** (`rust/weather-core/src/bridge_cache.rs`,
  `rust/sharpmod-rs/src/{decoder_clean.rs,bridge_cache_bindings.rs}`):
  `sanitize_profile_keep_mask`, `clean_column`, `thin_profile_indices`, `live_cache_digest`,
  `live_cache_ttl`, `live_cache_read`, `live_cache_write`, `frame_cache_read_gzip`,
  `frame_cache_compress_gzip`, and `storage_scan_files` backing `sharpmod.io.decoder` and
  `sharpmod.bridge.{live_cache,frame_cache,storage}` (`storage._files` retains an `ImportError`
  fallback for Python-only sidecar environments).
- **M14 Batch extraction manifest, containment & SHA-256** (`rust/sharpmod-rs/src/batch_manifest.rs`):
  `batch_safe_identifier`, `batch_contained_path`, `batch_file_sha256`,
  `batch_json_fingerprint`, and `batch_load_manifest` backing `sharpmod.analysis.batch_extract`.
- **M15 Map overlays & raster bounds** (`rust/sharpmod-rs/src/map_overlay_geometry.rs`):
  `overlay_rings_from_geometry`, `overlay_bounds_of`, `overlay_shape_contains`,
  `overlay_looks_like_image`, `overlay_format_age`, and `overlay_raster_intersects`.
- **M16 Map geography, label decluttering & scale bars** (`rust/sharpmod-rs/src/map_geography.rs`):
  `geo_boundary_draw_order`, `geo_boundary_width`, `geo_normalise_label_density`,
  `geo_labels_visible_for_span`, `geo_declutter_labels`,
  `geo_km_per_pixel_at_latitude`, `geo_pick_scale_length`,
  `geo_format_scale_length`, `geo_luminance`, and `geo_contrast_ratio`.
- **M17 Locator geometry & overlay spec rules** (`rust/sharpmod-rs/src/locator_geometry.rs`):
  `locator_normalise_bounds`, `locator_area_to_map_box`, `locator_centred_bounds`,
  `locator_fit_bounds`, `locator_parse_overlay_spec`,
  `locator_enforce_overlay_exclusivity`, `inspection_haversine_km`, and
  `inspection_format_field_value`.
- **M18 ERA5 geodesic, dewpoint & vorticity math** (`rust/sharpmod-rs/src/era5_math.rs`):
  `era5_great_circle_distance_km`, `era5_select_nearest_grid_point_1d`,
  `era5_select_nearest_grid_point_2d`, `era5_dewpoint_from_rh`,
  `era5_dewpoint_from_specific_humidity`, `era5_surface_relative_vorticity`,
  `era5_east_west_distance_m`, and `era5_north_south_distance_m`.
- **M19 Vertical profile interpolation & isotherm crossings** (`rust/sharpmod-rs/src/vertical_profile.rs`):
  `isotherm_pres_and_hght`, `prepare_vertical_interpolator`,
  `NativeVerticalInterpolator`, and `native_six_km_cape_eligible`.
- **M20 Timeline planning & ensemble ledger validation** (`rust/sharpmod-rs/src/timeline_ledger.rs`):
  `timeline_forecast_hour_range`, `timeline_normalize_state`,
  `timeline_frame_describe`, `timeline_coverage_summary`, `animation_frame_caption`,
  `animation_plan_frames`, and `ensemble_validate_ledger`.
- **M21 Theme palette, tier colors & box field ramps** (`rust/sharpmod-rs/src/viz_colors.rs`):
  `color_parse_rgb`, `color_contrast_ratio`, `color_resolve_theme`,
  `color_semantic_palette`, `color_is_missing`, `color_common_gradient`,
  `color_tier_for_param`, `box_field_ramp_rgb`, `box_field_normalize`,
  `box_field_scale_range`, and `box_field_cell_vertices`.
- **M22 Chart marker, Skew-T label & unit layout** (`rust/sharpmod-rs/src/chart_layout.rs`):
  `hodo_label_for_height`, `hodo_prepare_height_wind_samples`,
  `hodo_layout_height_level_markers`, `skewt_fit_rect`, `skewt_surface_label_rect`,
  `skewt_layout_surface_labels`, `skewt_centered_rect`, and `unit_split_value_suffix`.
- **M23 Open-Meteo access, redaction & profile selection** (`rust/sharpmod-rs/src/openmeteo_logic.rs`):
  `openmeteo_redact`, `openmeteo_host_of`, `openmeteo_normalise_base_url`,
  `openmeteo_credential_scope`, `openmeteo_resolve_access`,
  `openmeteo_retry_after_seconds`, `openmeteo_namespaced_key`,
  `openmeteo_api_model_for_key`, `openmeteo_is_selectable_api_model`,
  `openmeteo_hourly_variables`, `openmeteo_normalise_longitude`,
  `openmeteo_point_in_domain`, `openmeteo_floor_cycle_epoch`,
  `openmeteo_select_time_index`, `openmeteo_derive_surface_height`, and
  `openmeteo_weighted_units`.
- **M24 Box-sounding export formatting & GeoJSON geometry** (`rust/sharpmod-rs/src/box_export_format.rs`):
  `box_export_normalize_lon180`, `box_export_format_number`, `box_export_cell_steps`,
  `box_export_cell_geometry`, `box_export_csv_rows`, and `box_export_summarize`.
- **M25 IGRA2 selection & ERA5 grid normalization** (`rust/sharpmod-rs/src/igra2_selection.rs`):
  `igra2_resolve_station_index`, `igra2_select_nearest_header_index`,
  `igra2_y2d_candidate_years`, `era5_grid_surface_relative_vorticity`,
  `era5_select_surface_level_index`, `era5_normalize_surface_pressures_hpa`, and
  `era5_normalize_surface_scalars`.
- **M26 Render density scaling, hour cache keys & IFS checks** (`rust/sharpmod-rs/src/render_and_cache_rules.rs`):
  `render_bounded_float`, `render_bounded_int`, `render_normalise_png_mode`,
  `render_map_copy_axis`, `render_scale_dimensions`, `render_logical_extent`,
  `hour_cache_canonical_key_parts`, `hour_cache_directory_prefix`,
  `ifs_validate_request`, and `ifs_nearest_cycle_hour`.
- **M27 Hodograph locator tiles & inset math** (`rust/sharpmod-rs/src/locator_tiles.rs`):
  `hodo_locator_point_valid`, `hodo_locator_requested_distinct`,
  `hodo_locator_normalise_box`, `hodo_locator_zoom_bounds`,
  `hodo_locator_raster_source_rect`, `hodo_locator_raster_draw_rects`,
  `hodo_locator_decode_county_tile`, `hodo_locator_county_tile_coords`,
  `hodo_locator_filter_global_lines`, `hodo_locator_inset_rect`, and
  `hodo_locator_marker_position`.
- **M28 Locator overlay drawing & label filtering** (`rust/sharpmod-rs/src/locator_overlay_geom.rs`):
  `hodo_overlay_raster_copy_range`, `hodo_overlay_clamp_opacity`,
  `hodo_overlay_project_shape_rings`, `hodo_overlay_project_marker_point`,
  `hodo_overlay_timing_text`, and `hodo_overlay_strip_coordinate_label`.
- **M29 Skew-T buoyancy fills & inset cursor readouts** (`rust/sharpmod-rs/src/skewt_buoyancy.rs`):
  `skew_hgz_band_pixels`, `skew_interp_env_on_parcel`, `skew_cape_fill_polygons`,
  `inset_thetae_readout`, `inset_srwinds_readout`, `inset_slinky_nearest_height`, and
  `inset_readout_box_rect`.
- **M30 Index board, SHIP & custom panel formatting** (`rust/sharpmod-rs/src/board_formatting.rs`):
  `board_format_i0`, `board_format_f1`, `board_format_f2`, `board_format_dirspd`,
  `board_format_uv_dirspd`, `board_format_temp`, `board_format_pwat`,
  `board_barb_components`, `board_cinh_legacy_tier`, `board_cape3_tier`,
  `ship_scale_fraction`, `ship_format_value`, `custom_panel_normalise_unit`, and
  `custom_panel_format_number`.
- **M31 Export composition layout, patch validation & sequence hours** (`rust/sharpmod-rs/src/export_layout_rules.rs`):
  `export_compose_layout`, `export_composition_theme_hex`, `export_valid_png_file`,
  `patch_registry_validate_specs`, `patch_registry_validate_version`,
  `box_extract_sequence_hours`, and `observed_safe_token`.
- **M32 SharpTab clamps, download progress & bridge validation** (`rust/sharpmod-rs/src/profile_and_progress_rules.rs`):
  `profile_clamp_scalar_in_range`, `profile_clamp_vector_in_range`,
  `progress_validate_id`, `progress_stage_phase`, `progress_window_snapshot`,
  `bridge_validate_param_keys`, `bridge_validate_text`, `bridge_validate_number`,
  `bridge_validate_integer`, `bridge_validate_choice`, and `backend_validate_sfc_index`.
- **M33 SPC convective outlook rules** (`rust/sharpmod-rs/src/spc_outlook_rules.rs`):
  `spc_cig_level`, `spc_is_qualifier_label`, `spc_convective_day_start_epoch`,
  `spc_outlook_day_offset`, `spc_format_product_days`, `spc_covers_location`,
  `spc_candidates_for`, `spc_parse_stamp_epoch`, `spc_feature_style`,
  `spc_display_label`, and `spc_validate_colour`.
- **M34 Storm report rules & filtering** (`rust/sharpmod-rs/src/storm_report_rules.rs`):
  `report_magnitude_text`, `report_is_significant`, `report_normalise_filter`,
  `report_window_label`, `report_count_label`, `report_marker_radius`,
  `report_marker_ring`, `report_normalise_view`, `report_build_url`,
  `report_cache_key`, `report_window_is_settled`, and `report_layer_shape_matches`.
- **M35 ECCC GeoMet rotated-grid & capabilities rules** (`rust/sharpmod-rs/src/geomet_grid.rs`):
  `geomet_to_rotated`, `geomet_to_geographic`, `geomet_rotated_contains`,
  `geomet_rotated_outline`, `geomet_point_in_bounds`, `geomet_floor_cycle_epoch`,
  `geomet_feature_info_bbox`, and `geomet_parse_dimension_defaults`.
- **M36 Radar scan selection, mosaic & site rules** (`rust/sharpmod-rs/src/radar_rules.rs`):
  `radar_select_scan_epoch`, `radar_mosaic_covers`, `radar_mosaic_frame_size`,
  `radar_service_exception_text`, `radar_site_bounds`, `radar_site_distance_km`,
  `radar_site_nearest_index`, and `radar_site_frame_size`.
- **M37 Surface observation overlays & CSV export** (`rust/sharpmod-rs/src/surface_overlay_rules.rs`):
  `surface_wind_state`, `surface_wind_uv_kt`, `surface_declutter_indices`,
  `surface_station_ring`, `surface_ring_radii`, `surface_format_observation_text`,
  `surface_format_subtitle`, and `surface_write_csv`.
- **M38 Overlay hit testing & place-name geometry** (`rust/sharpmod-rs/src/overlay_hit_testing.rs`):
  `overlay_marker_hits_select`, `overlay_format_description`,
  `overlay_marker_hit_label`, `place_needs_town_name`,
  `place_from_address_fields`, `place_is_us_address`,
  `place_parse_record_fields`, `place_area_proxy_radius_km`, and
  `place_point_in_polygon`.
- **M39 UWyo sounding & station catalog rules** (`rust/sharpmod-rs/src/uwyo_rules.rs`):
  `uwyo_pressure_spaced_indices`, `uwyo_subsample_indices`,
  `uwyo_looks_unavailable`, `uwyo_parse_metadata`, `uwyo_clean_station_name`,
  `uwyo_search_stations`, and `uwyo_resolve_station_index`.
- **M40 Box-mean profile composition & interpolation** (`rust/sharpmod-rs/src/box_mean_rules.rs`):
  `box_mean_model_label`, `box_mean_badge_lines`, `box_mean_describe`,
  `box_mean_reference_member_index`, `box_mean_interpolate_log_pressure`,
  `box_mean_nanmean_stack`, `box_mean_speed_direction`,
  `box_mean_strictly_stacked_mask`, and `box_mean_circular_centre`.
- **M41 Ensemble threshold validation & reconciliation** (`rust/sharpmod-rs/src/threshold_rules.rs`):
  `threshold_validate_condition`, `threshold_validate_definition`,
  `threshold_derive_member_state`, `threshold_evaluate_member_conditions`,
  `threshold_result_counts_reconcile`, and `threshold_validate_resume_members`.
- **M42 Bridge protocol framing & RFC3339 time rules** (`rust/sharpmod-rs/src/bridge_protocol_rules.rs`):
  `bridge_decode_request_frame`, `bridge_check_request_params`,
  `bridge_encode_frame_check`, `bridge_validate_rfc3339_time`, and
  `bridge_format_iso_utc`.
- **M43 Model domain bounds & forecast hour rules** (`rust/sharpmod-rs/src/model_domain_rules.rs`):
  `model_longitude_segments`, `model_domain_rect_intersects`,
  `model_domain_rect_contains`, `model_outline_sample_points`,
  `model_outline_vertex_in_bounds`, and `model_filter_forecast_hours`.
- **M44 SharpTab lapse rates & export path probes** (`rust/sharpmod-rs/src/sharptab_lapse_and_paths.rs`):
  `sharptab_lapse_rate_from_bounds`, `sharptab_lapse_rate_agl`,
  `sharptab_lapse_rate_pres`, `export_validate_filename`, and
  `export_probe_writable_dir`.

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
  computation. The workspace APIs amortize that cost by returning all standard
  layers or parcels together and caching each result on a `Profile`.
- Borrowed array kernels retain the GIL. Profile kinematics and parcels own
  copies before releasing it. Direct GRIB decoding also releases it, but
  Rust-decoder ecCodes access is serialized; concurrent point decodes are not
  currently expected to scale. Python ecCodes/cfgrib calls are protected
  separately rather than by a shared cross-language lock.
- A `Profile`'s original internal SharpTab caches still follow its normal
  derived-attribute lifecycle. The cross-profile metric cache separately
  fingerprints pressure, height, thermodynamic, and wind columns, so its
  prepared result is discarded if those arrays are edited in place.
- Native parcel results intentionally exclude several advanced `Parcel` fields
  outside the traced parcel summary contract, including cap-strength bookkeeping and
  the pinned ECAPE upper-bound check. The Python compatibility layer derives or
  masks those fields and retains the pinned SHARPpy fallback.
- Direct GRIB caches are process-local and bounded. A truly cold request still
  scans message headers; inventory reuse removes that scan but ecCodes must
  still unpack every selected field/level message. Multipoint calls amortize
  that unpack across all requested indexes rather than making repeated scalar
  passes.
- When a downloaded inventory has no usable relative/absolute-vorticity field,
  model extraction keeps the xarray compatibility path so the existing
  neighbor-wind vorticity estimate is preserved.
- Parsing is intentionally limited to the backend's simple row format. Existing
  SPC, UWyo, BUFKIT, WRF, and other decoders remain Python implementations.
- QC is basic and structural. It does not replace the complete SHARPpy profile
  validation and meteorological analysis paths.
- The PyInstaller spec includes `sharpmod_rs` only when the extension is
  discoverable and collected successfully in the environment performing the
  build. The official release job builds and installs it before packaging;
  custom local builds that omit it log a warning and produce a fully functional
  Python-fallback bundle.
