# All-model local GRIB decoding benchmark

Network transfer is excluded. Times are application-cold medians; application caches and cfgrib indexes are cleared, while the operating-system file cache is not flushed.

| Model | Production decode path | Levels old / optimized | Old/new omega | Old Python ms | Optimized Python ms | Python speedup | Old Rust hybrid ms | Optimized Rust ms | Rust speedup | Py/Rust optimized |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HRRR | direct GRIB (F000 may use point Zarr in auto mode) | 40 / 40 | matched | 13,656.191 | 6,666.527 | 2.05x | 13,623.801 | 6,325.879 | 2.15x | 1.054x |
| RAP | direct GRIB | 37 / 37 | matched | 4,468.287 | 2,213.136 | 2.02x | 4,442.193 | 2,095.314 | 2.12x | 1.056x |
| NAM | direct GRIB | 39 / 39 | matched | 8,174.641 | 1,233.397 | 6.63x | 4,003.166 | 980.808 | 4.08x | 1.258x |
| NAM 3km CONUS | direct GRIB | 42 / 42 | matched | 19,334.120 | 9,848.533 | 1.96x | 19,112.263 | 9,541.045 | 2.00x | 1.032x |
| HRW WRF-ARW | direct GRIB | 27 / 27 | matched | 7,927.530 | 3,963.836 | 2.00x | 7,966.092 | 3,858.457 | 2.06x | 1.027x |
| HRW FV3 | direct GRIB | 27 / 27 | matched | 7,553.566 | 3,518.836 | 2.15x | 7,719.979 | 3,432.609 | 2.25x | 1.025x |
| RRFS A | direct GRIB | 45 / 45 | matched | 14,380.822 | 6,821.914 | 2.11x | 14,533.194 | 6,379.690 | 2.28x | 1.069x |
| GFS | direct GRIB | 33 / 41 | matched | 14,502.366 | 3,609.185 | 4.02x | 14,454.516 | 3,333.053 | 4.34x | 1.083x |
| AIGFS | xarray vorticity fallback (direct point decoder benchmarked) | 13 / 13 | matched | 3,021.279 | 1,144.266 | 2.64x | 2,884.502 | 1,090.785 | 2.64x | 1.049x |
| CFS | direct GRIB | 37 / 37 | matched | 4,944.596 | 2,517.830 | 1.96x | 4,885.038 | 2,365.896 | 2.06x | 1.064x |
| ECMWF IFS Open Data | direct GRIB | 14 / 14 | matched | 3,487.357 | 1,330.818 | 2.62x | 3,438.142 | 1,239.230 | 2.77x | 1.074x |
| ECMWF-AIFS | xarray vorticity fallback (direct point decoder benchmarked) | 14 / 14 | matched | 2,926.893 | 1,123.699 | 2.60x | 2,930.174 | 1,049.424 | 2.79x | 1.071x |
| GEFS | xarray vorticity fallback (direct point decoder benchmarked) | 12 / 12 | different (12 -> 1 valid) | 927.236 | 246.907 | 3.76x | 900.190 | 201.013 | 4.48x | 1.228x |

Measured fixtures: 13; explicitly unavailable: 0; failed: 0.

NAM repeatability note: the matrix's three-sample NAM row contains an isolated
system-wide timing stall in its raw samples. A separate five-repeat confirmation
([JSON](2026-07-16-nam-decoding-v0.4.0-windows-amd64.json)) produced stable
medians of 3,938.427 / 1,170.639 ms for old/optimized Python (3.36x) and
3,974.929 / 984.382 ms for old-hybrid/optimized Rust (4.04x). The matrix row is
retained unchanged so the primary Markdown remains consistent with its companion
JSON.

`Old Rust hybrid` is the historical cfgrib/xarray decoder followed by native wind post-processing; the old extension did not decode GRIB. `Py/Rust optimized` is Python time divided by Rust time, so values above 1 mean Rust was faster.
`Old/new omega` is diagnostic across generations. Python and Rust remain strict within each generation for all columns, including omega; old/new pressure, height, temperature, dewpoint, and wind columns are strict at every common pressure level.
Production decode paths are manifest-declared metadata, not inferred by the benchmark driver.

Complete fixture hashes, raw samples, equivalence output, environment metadata, and unavailable-stage reasons are retained in the companion JSON.
