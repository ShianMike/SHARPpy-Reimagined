# All-model fetch and decode optimization

Measured 2026-07-22 on Windows with the uncommitted optimization work based on
Git `7dc82825dc742e4f980343574bfcfac0b1612120`.

- CPU: 12th Gen Intel Core i5-1235U
- Python: 3.11.14
- NumPy: 2.4.6
- ecCodes: 2.47.0
- Herbie: 2026.3.0
- Selected scalar backend: Rust 0.4.2 in `auto` mode

## Transport coverage

| Models | Preferred point/small route | Large-transfer route |
| --- | --- | --- |
| HRRR F000 | Public HRRR Zarr point read | GRIB fallback |
| HRRR F001+, RAP, NAM, NAM 3 km, HRW WRF-ARW/FV3, GFS, CFS, GEFS | Four-worker validated byte ranges at or below 32 MiB | NOAA NOMADS geographic subset |
| RRFS domains | Four-worker validated byte ranges | Same range route until a production geographic subset exists |
| AIGFS, ECMWF IFS/AIFS, other indexed Herbie products | Four-worker validated byte ranges | Normal Herbie fallback when incompatible |
| GDPS/RDPS | ECCC GeoMet point values with bounded layer fan-out | Provider fallback |

All range workers use separate sessions. Large coalesced ranges are split into
balanced spans, pinned to an ETag or Last-Modified object identity, assembled
in byte order, and validated as GRIB. Missing validators or rejected parallel
traffic downgrade to sequential requests.

## Live RAP coalesced-range result

Public object:
`https://noaa-rap-pds.s3.amazonaws.com/rap.20260722/rap.t00z.awp130pgrbf00.grib2`

The 223 planned sounding messages coalesced into one 10,005,675-byte span. The
new planner split that span into four contiguous parts. It transferred
10,005,676 wire bytes (the payload plus the one-byte identity probe) in five
requests with no retry or fallback.

| Route | Time | SHA-256 |
| --- | ---: | --- |
| Earlier one-range baseline | 24.273 s | `2ce9e0f1e7971038a5783bfaa870588def5c84781e495d53fde89f07c2b030ce` |
| Four balanced ranges | 11.586 s | `2ce9e0f1e7971038a5783bfaa870588def5c84781e495d53fde89f07c2b030ce` |

That sample was 2.10 times faster and byte-identical. Network and upstream
cache state can vary, so this is retained evidence rather than a performance
guarantee.

## Live HRW server-side subset result

The new `filter_hiresconus.pl` route fetched the 2026-07-22 00Z HRW FV3 F000
sounding neighborhood at 35.18, -97.44. The response was 42,374 bytes, decoded
to all 27 published pressure levels from 1000 to 200 hPa, and completed in
12.350 seconds including NOAA CGI preparation. SHA-256:
`8332d77636fda0d0db20c993ab853a790a77f530776f580ea6841fe9434e20fb`.

## Missing-vorticity model decode

Canonical GRIB fixtures were copied into fresh temporary directories. The new
path used the compact point decoder plus two direct U/V field reads; the legacy
path opened and merged cfgrib pressure groups. The new path was timed first for
each row, and the operating-system file cache was not flushed.

| Model | Direct core + stencil | Legacy cfgrib/xarray | Speedup | Vorticity difference |
| --- | ---: | ---: | ---: | ---: |
| AIGFS | 1.565587 s | 4.453089 s | 2.84x | 1.16e-13 s^-1 |
| ECMWF-AIFS | 1.272137 s | 2.168381 s | 1.70x | 0 |
| GEFS | 0.278901 s | 0.491584 s | 1.76x | 1.92e-12 s^-1 |

The stencil differences are floating-point rounding below 2e-12 s^-1. The
synthetic science-equivalence regression compares the direct stencil against
the established xarray implementation.

## Multi-point RAP decode

Four distinct grid cells from the canonical 9,815,504-byte RAP fixture were
decoded at all 37 pressure levels. Three interleaved application-cache-cold
samples produced medians of 8.1601 seconds for four scalar calls and 2.3142
seconds for one vector call, a 3.53x speedup. Every 9-by-37 matrix, selected
coordinate, and vorticity value was exactly equal; maximum absolute difference
was zero. The OS file cache was not flushed.

The vector path unpacks each selected message once and requests all grid
indexes together. It does not run ecCodes concurrently.

## Verification

- Focused transport, decode, batch, model-source, HRRR Zarr, and extraction
  suites: 94 passing tests after the final implementation changes.
- Full project suite: 720 passing tests in 484.34 seconds.
- Live RAP output matched the earlier sequential SHA-256 exactly.
- Live HRW FV3 used the new official server-side endpoint and decoded all
  published pressure levels in the subset.
