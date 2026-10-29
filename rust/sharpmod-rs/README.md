# sharpmod-rs

`sharpmod-rs` is the supported primary numerical and direct pressure-level GRIB
point-decoding backend for
[SHARPpy Reimagined](https://github.com/ShianMike/SHARPpy-Reimagined). Official
v0.4 Windows binaries bundle it, and the application's default `auto` mode uses
it after validating the versioned backend contract. It uses PyO3,
NumPy-compatible array views, a coarse-grained profile-kinematics workspace,
an SB/MU/ML parcel workspace, a traced five-parcel convective workspace,
explicit user-parcel ascents, DCAPE, memory-mapped GRIB input, and ecCodes to
minimize allocations and Python/Rust boundary crossings. The independently
optimized Python implementation remains the portable fallback.

The shared `../weather-core` crate owns velocity directory fetching, radar site
data, NWS warning/watch downloads and polygon processing, and IEM storm-report
downloads and CSV parsing. It also owns NWS surface-station discovery, concurrent
observation fetching, unit conversion, parsing and time matching. Native map modules extract/project contour segments
and sample, rotate and pack wind barbs. Their Python adapters wire the existing
calls to Rust; the replaced Python implementations are removed. Storm-report
objects, filtering and memory/disk caching keep their existing Python owner.
Surface station/observation objects, presentation and exports also retain their
existing interfaces; only selected observations are restored after a native fetch.
The radar sampler processes the shared Level II/III radial-to-pixel loop in
Rust, retaining WGS84 projection, native gate codes, palettes and PNG calibration.
Forecast-map inventory and byte-range HTTP downloads also run in the shared
Rust core, retaining pooled connections, retries, per-read timeouts, budgets and
cancellation. Existing range scheduling, decoding and field caches keep their
current owners.
Height-stack interpolation for terrain-relative lapse-rate and low-level shear
maps also runs in Rust, preserving source rounding and missing brackets
with bounded, owned batches instead of full-grid interpolation temporaries.
Maximum parcel levels for accelerated sounding displays reuse the native lift
and pressure interpolation routines, retaining reported-level energy integration
and the existing 1 hPa refinement before restoring public SHARPpy parcel fields.
Convective-temperature searches reuse prepared interpolation fields and native
thermodynamics, preserving the stripped CAPE/CIN routine's reported levels and
fractional final layer. The default 100 hPa mean mixing ratio is shared with the
displayed profile value. Other thermodynamic indices retain their existing
SHARPpy formulas at the compatibility boundary.
IGRA header scans, level parsing, humidity-derived dewpoints and pressure filtering
also run in Rust, borrowing immutable archive text while detached from Python.
Existing IGRA objects, timestamps, provenance and archive/cache ownership remain
at the compatibility boundary.
SARS analogue matching also uses native loops and parsed-table reuse, preserving
the pinned hail/supercell criteria, result order and public NumPy tuple shapes.
The accelerated profile supplies its existing inputs and SHIP calculation;
upstream scientific reference functions remain available for comparisons.
Source environments without the native operation keep the existing SHARPpy
SARS compatibility call.
Storm Slinky trajectories prepare their environmental, wind, height and parcel
interpolation series once in Rust, then integrate the existing 25-second steps
without Python calls. The profile and widget paths share this kernel, retaining
the 5 m/s nudge, knot conversion, endpoint tolerance, AGL coordinates and masks.
Nonconvergent traces stop after 4095 steps and remain unavailable. Source-only
environments keep the existing SHARPpy trajectory call.

The SHARPpy widget stack, renderer and model-retrieval orchestration remain in
Python. The native workspace kernels own their small
profile columns and release the GIL. Kinematics returns Bunkers motion plus all
requested surface layers in one call. Parcels returns the standard
surface-based, lowest-300-hPa most-unstable, and 100-hPa mixed-layer summaries
in one call. The extended API adds forecast and effective parcels, full plotting
traces, explicit user parcels, and DCAPE; the Python compatibility layer maps
those typed results back to SHARPpy's public objects and falls back to the
pinned oracle on failure. ECAPE also rechecks that oracle for its exact
upper-bound contract.

The direct GRIB decoder also recognizes the HRRR surface pressure/terrain,
2-m temperature/dewpoint, and 10-m wind records. It removes every isobar below
the selected terrain and inserts that verified surface row before returning
the matrix.

Build it with Rust 1.88 or newer on the stable channel into the repository's
Python 3.11 development environment from the repository root:

```powershell
python -m pip install -e ".[dev,rust-build]"
Set-Location rust\sharpmod-rs
python -m maturin develop --release
Set-Location ..\..
```

Then verify the import:

```powershell
python -c "import sharpmod_rs; print(sharpmod_rs.__version__)"
```

Rust-only checks run with:

```powershell
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo bench
```

Application code should import `sharpmod.backends`, not this module directly.
See `docs/RUST_BACKEND.md` in the repository for selection, fallback,
diagnostics, platform targets, equivalence tests, and limitations.
