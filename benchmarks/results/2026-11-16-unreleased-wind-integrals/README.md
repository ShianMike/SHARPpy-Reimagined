# Custom wind-layer migration: local Unreleased measurements

Measured 2026-11-16 on the dirty `work/forecast-export-locator` branch based on
`e673eae13a527d0d2f5978c4b813c486e6205099`. This is an Unreleased source
measurement, separate from the published v3.8.1 release and the complete
v3.8.0 rendering/GIF baseline. App and native package versions remain 3.8.1.

The [raw samples and source hashes](wind-adapters.json) retain seven interleaved
repeats of 1,000 calls for a fixed synthetic 128-level profile. Timings include
the public Python facade, input normalization/copying, interpolation, integration
and conversion back to public tuples. The reference disables only the optional
custom-layer operations; the pre-existing Rust interpolation remains active.
Both paths use the same forced Rust backend and installed release extension.

| Public operation | Reference median | Migrated median | Local ratio |
| --- | ---: | ---: | ---: |
| Pressure-weighted mean wind | 410.94 us | 126.50 us | 3.25x |
| Unweighted mean wind | 239.79 us | 104.44 us | 2.30x |
| Custom-layer storm-relative helicity | 364.27 us | 87.06 us | 4.18x |

Environment: SHARPpyBuilder WSL Docker, four-CPU/six-GiB container, CPython
3.11.18, NumPy 2.4.6, on an Intel Core i5-1235U host. Inputs and commands remain local. The driver validates
paired numerical output before timing and alternates path order between repeats.
No build or other test run was started concurrently; the user's existing
previews remained running. Host background load was not otherwise controlled.

These are warm, operation-level measurements. They do not establish a startup,
whole-render, provider-download, standard cached-layer or Windows speedup.
The preceding fixed-layer cache still takes precedence. Complete Severe and
Winter HD CLI images were byte-identical before and after this migration;
scientific/API compatibility checks are separate from timing.

After building/installing the matching release extension, run the existing
Rust test driver manually; ordinary checks skip this timing workload:

```powershell
$env:SHARPMOD_BACKEND = 'rust'
$env:SHARPMOD_WIND_BENCHMARK_OUTPUT = 'C:/path/to/new-wind-adapters.json'
cargo test --manifest-path rust/sharpmod-rs/Cargo.toml --locked --lib measure_public_wind_adapters -- --ignored --nocapture --test-threads=1
```

The harness itself may use Cargo's debug test profile; its timed functions
come from the installed release extension. A missing optional operation fails
the driver rather than being reported as a native timing.
