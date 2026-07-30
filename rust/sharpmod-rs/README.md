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

This crate does not contain the PySide6 GUI, SHARPpy widget stack, renderer,
download clients, or model-retrieval orchestration; those shared application
layers continue to run in Python. The native workspace kernels own their small
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
