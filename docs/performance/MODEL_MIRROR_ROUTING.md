# Native model mirror routing — 2026-10-29

Mirror probing, selection and the six-hour, 256-entry exact-object cache now
run in Rust. `sharpmod/models/model_sources.py` keeps thin compatibility calls;
its former HTTP probe, executor, selection and cache loops were removed.
NOMADS geographic subsets and resumable range-transfer orchestration remain
Python responsibilities and are not claimed as migrated here.

The router preserves the four-probe ceiling, 2.5 s connection and 6 s per-read
timeouts, no retries, `bytes=0-65535`, HTTP 206 and numeric total-size checks,
reference-size compatibility, modal-size fallback, name ordering on equal
timings, exact model/product/source URL identity, cache revalidation and expiry.
The former private Python probe/selection helpers had only test callers; they
and four duplicate Python policy tests were retired after their coverage moved
to Rust. The shipped binding exposes only the selection operation.

## Measured complete operations

Windows, CPython 3.11.14, release wheel, GEFS 2026-10-29 00Z, p01, F000,
35°N 97°W. Samples alternate implementation order. Each extraction uses a new
download/output directory and no source cache. Network and operating-system
caches, provider load and power state are uncontrolled; these are local samples.

| Operation | Python median | Final Rust median | Samples |
| --- | ---: | ---: | --- |
| Complete three-mirror selection | 1.4894 s | 1.2303 s | 3 interleaved pairs |
| Complete point extraction | 5.0287 s | 4.8648 s | 5 interleaved pairs |

The complete-extraction median is about 3.3% lower, with overlapping individual
timings and one slower Rust pair. This does not establish a universal network
speedup. Complete samples, Python then Rust, in seconds:

| Pair | Python | Rust |
| --- | ---: | ---: |
| 1 | 5.5638 | 5.0360 |
| 2 | 4.9966 | 4.6136 |
| 3 | 5.0494 | 4.8595 |
| 4 | 5.0287 | 4.8648 |
| 5 | 4.8463 | 5.1409 |

All ten extractions retained the same 12 levels, weather and identity arrays,
missing values, selected coordinates, surface merge, QC, member and valid time.
Each output's unique generation UUID was checked against its own JSON partner.

## Rejected experiment and verification

Reusing probe connections between races produced a misleading 0.518 s median
mirror check. It selected a warm Azure connection while the range downloader
still opened fresh sessions. Complete-extraction medians regressed from 5.234 s
to 7.849 s. The accepted implementation creates one client per race so mirror
timings include the connection setup that the current downloader requires.
Rust's shared async runtime still replaces per-call Python executors.

The decision follows complete-operation evidence. Although reqwest recommends
[reusing clients for pooled connections](https://docs.rs/reqwest/0.13.5/reqwest/struct.Client.html),
using a different connection policy for a ranking probe and its eventual
downloader distorted this workload. Its documented
[connection and read timeout controls](https://docs.rs/reqwest/0.13.5/reqwest/struct.ClientBuilder.html#method.read_timeout)
retain the existing two separate deadlines.

Verification includes 12,288 choices compared with the previous Python policy,
native cache/failover tests, and real HTTP tests for
range consumption, fresh connection measurements, ordering, concurrency,
timeouts, malformed responses and no retries. The final release wheel passes
148 existing model, transfer, ensemble and packaging tests. Scientific kernel,
weather, lint and release-note checks remain in their existing suites.

Detailed local evidence, including the rejected experiment, is retained under
`.test-results/model-routing/`; it is not routine test input or CI infrastructure.
