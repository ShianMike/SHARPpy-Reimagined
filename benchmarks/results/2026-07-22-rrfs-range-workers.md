# RRFS HTTP range-worker benchmark

Measured 2026-07-22 on Windows against the public NOAA RRFS prototype S3
bucket. The benchmark fetched the same six complete, non-contiguous 1000-hPa
GRIB messages (`HGT`, `TMP`, `RH`, `UGRD`, `VGRD`, and `ABSV`) from F003 for
three 2026-07-21 cycles. Worker order was rotated between cycles. Every output
within a cycle was byte-identical by SHA-256; no request retried or fell back.

| Cycle | Payload | 1 worker | 2 workers | 4 workers | 6 workers |
| --- | ---: | ---: | ---: | ---: | ---: |
| 00Z | 8,717,637 bytes | 7.856 s | 5.552 s | 3.747 s | 3.591 s |
| 06Z | 9,613,368 bytes | 7.144 s | 4.609 s | 3.847 s | 3.963 s |
| 12Z | 9,308,366 bytes | 21.304 s | 6.930 s | 3.973 s | 4.279 s |
| Median | — | **7.856 s** | **5.552 s** | **3.847 s** | **3.963 s** |

Four workers produced the best three-cycle median, about 2.04 times faster
than the sequential path. Six was slightly faster for one cycle but slightly
slower overall, so RRFS defaults to four while `SHARPMOD_RANGE_WORKERS` keeps
the 1-8 override available. Parallel requests transfer one extra byte for the
object-identity probe; the assembled payload is unchanged.

This is a transport benchmark, not a decoder benchmark. It deliberately uses
a bounded representative subset so repeated tests do not transfer full RRFS
pressure-level files. Actual wall time still depends on the user's network,
remote cache state, and the number and layout of selected GRIB messages.
