# GRIB loading: v3.8.4 integration comparison

Measured on 2026-11-17 in `perf/grib-p1-p5`, based on frozen source
`2879acc1ae79c9c919a027e91b70bb04f0b71bcd`. App and native versions remain
3.8.4. The user subsequently authorized including these changes in the November
17 v3.8.4 release; this record preserves the original pre-integration measurement.
The source fingerprints in [sources.json](sources.json) identify the measured
map-loading implementation. This comparison does not replace the complete
rendering/GIF baseline.

The existing provider loads four products in order: 2 m theta-e with 10 m
winds, 2 m temperature, 2 m dewpoint and 10 m wind. They use five distinct
GRIB ingredients. The reference uses the unchanged provider, inventory and
ecCodes modules from the frozen checkout. The optimized path calls the new
Rust map loader directly with those same Python metadata/decoder modules.
Both paths run from the same release-built Rust test driver and pinned
Python environment.

Three fresh processes per path each perform a first load and two reuse
passes. Path order alternates between repetitions. All 18 samples have
identical SHA-256 hashes for all nine output ingredient arrays, including
the repeated fields used by different products. Hashing occurs outside
the timed region. The optimized cold passes also assert that decoding
is active while another HTTP response body is still arriving.

| Work | Reference median | Optimized median | Time reduction |
| --- | ---: | ---: | ---: |
| First product group, 3 samples per path | 801.820 ms | 303.802 ms | 62.1% |
| Repeated product group, 6 samples per path | 578.190 ms | 32.839 ms | 94.3% |

| HTTP response bodies per product group | Reference | Optimized |
| --- | ---: | ---: |
| First load | 15,811,294 bytes / 10 requests | 8,661,364 bytes / 9 requests |
| Reuse pass | 15,810,339 bytes / 9 requests | 4 bytes / 4 requests |

The first group transfers 45.2% fewer response-body bytes. Reuse passes
still make one one-byte source-revision check per product; they reuse
the five retained decoded fields. HTTP/TCP headers are excluded from byte
counts. The raw reference `decodedFields: 0` is an unavailable native
instrumentation counter, not evidence that the reference performs no decoding.

[baseline-1.json](baseline-1.json), [baseline-2.json](baseline-2.json) and
[baseline-3.json](baseline-3.json) retain every reference sample.
[optimized-1.json](optimized-1.json), [optimized-2.json](optimized-2.json) and
[optimized-3.json](optimized-3.json) retain every optimized sample.

## Fixed input and limits

The fixture contains 17 complete original HRRR CONUS 2026-11-17 00Z F001
surface-file messages, preserving the intervening records and original
relative byte offsets. It is a 18,625,139-byte span starting at source offset
35,626,208, with SHA-256
`2a3b76ccad81c26ea03b488b63f35b9fdc8339aa2b7f396d75403084cf40affb`.
The rebased sidecar inventory is retained in [fixture.json](fixture.json).
Original source:
[NOAA HRRR public S3 object](https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20261117/conus/hrrr.t00z.wrfsfcf01.grib2).

The HTTP fixture serves the real compressed messages on loopback in 128 KiB
chunks with a 4 ms pause between chunks. It supports byte ranges and a
strong validator. Thus, these measurements exercise actual GRIB decompression,
field reuse, request planning and the download/decode pipeline under fixed
transfer pacing. They do not measure changing Internet bandwidth, NOMADS
response times, PNG rendering, exports or installed Windows performance.
Point-route policy, companion overlap/ownership, adaptive worker limits and
mirror selection are covered by separate correctness tests; this table does
not quantify their production speedup.

Environment: SHARPpyBuilder WSL Docker; four-CPU and six-GiB container limit;
Rust 1.90.0 release profile with thin LTO; CPython 3.11.18; NumPy 2.4.6;
ecCodes Python 2.50.1 and eccodeslib 2.50.0.34. Existing user-requested previews
remained running, and host background load was not controlled. First loads
are process/cache cold, with potentially warm OS file/code caches.

## Reproduce

Use the feature source to build the driver. Point `PYTHONPATH` at an unchanged
frozen checkout and its original scientific dependencies/native wheel for
both paths; do not put the newly installed feature wheel ahead of that pinned
environment. Supply the exact GRIB fixture and its rebased sidecar at
`<fixture>.idx`. Ordinary tests skip this manual workload.

```bash
export PYO3_PYTHON=/path/to/pinned-environment/bin/python
export PYTHONPATH=/path/to/frozen-checkout:/path/to/pinned-environment/lib/python3.11/site-packages
export SHARPMOD_MAP_BENCH_FIXTURE=/path/to/hrrr-layout.grib2
export SHARPMOD_MAP_BENCH_MODE=baseline
export SHARPMOD_MAP_BENCH_OUTPUT=/path/to/new-baseline.json
cargo test --release --locked --manifest-path rust/sharpmod-rs/Cargo.toml \
  --lib compare_real_map_provider_loading -- --ignored --nocapture --test-threads=1
# Repeat in a fresh process with MODE=optimized and a different OUTPUT path.
```
