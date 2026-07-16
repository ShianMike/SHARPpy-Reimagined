# sharpmod-rs

`sharpmod-rs` is the supported primary numerical and direct pressure-level GRIB
point-decoding backend for
[SHARPpy Reimagined](https://github.com/ShianMike/SHARPpy-Reimagined). Official
v0.4 Windows binaries bundle it, and the application's default `auto` mode uses
it after validating the versioned backend contract. It uses PyO3,
NumPy-compatible borrowed array views, memory-mapped GRIB input, and ecCodes to
minimize allocations and Python/Rust boundary crossings. The independently
optimized Python implementation remains the portable fallback.

This crate does not contain the PySide6 GUI, SHARPpy widget stack, renderer,
download clients, model-retrieval orchestration, or meteorological parcel
calculations; those shared application layers continue to run in Python.

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
