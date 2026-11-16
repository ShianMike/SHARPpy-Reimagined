# v3.8.0 baseline measured on 2026-11-15

These are local reference measurements for future comparisons. They do not claim
a speedup over an older version and are not CI thresholds.

The four renderer runs imported the unchanged released source at
`f518ccf56fb4bfd6787f094465abcfec4cc21531` (tree
`354a94bcf7d4493f33924cea0c497ef00fb8f52d`) and the matching native 3.8.0
wheel. Each used the bundled HRRR profile, a fresh process, and five complete
renders: one first render and four warm renders. Every PNG decoded, dimensions
matched, and all five decoded pixel hashes matched within each configuration.
The JSON records include the input/native hashes and exact dependency versions.

Environment: Intel Core i5-1235U, Windows Balanced power plan, SHARPpyBuilder
WSL2 Linux Docker, 4 CPU quota and 6 GiB memory limit, offscreen Qt, Python
3.11.18, Rust 1.90.0 release build. This is a local offscreen-render baseline,
separate from installed Windows/WebView2 and network performance.

| Composition | Output pixels | First render, s | Warm median, s | Peak process RSS, MiB | Raw evidence |
| --- | --- | ---: | ---: | ---: | --- |
| Severe HD | 3260 × 2198 | 2.742 | 2.484 | 643 | [JSON](baseline-severe-hd.json) |
| Severe UHD | 4564 × 3077 | 3.640 | 3.719 | 1041 | [JSON](baseline-severe-uhd.json) |
| Winter HD | 3260 × 2198 | 3.026 | 2.163 | 552 | [JSON](baseline-winter-hd.json) |
| Winter UHD | 4564 × 3077 | 3.088 | 2.801 | 920 | [JSON](baseline-winter-uhd.json) |

Import time is separate. The samples include local profile decoding, scientific
calculation, Qt composition, PNG writing and cleanup. First render means a fresh
process with potentially warm OS caches. RSS is the process high-water value,
not per-frame allocation or GPU memory. Normal offscreen Qt diagnostics are
retained with local validation evidence.

## Browser GIF export

The [raw GIF record](baseline-gif-fullhd.json) measures the released export code
in the offline browser fixture: HRRR composite reflectivity, F000/F001/F002,
1920 × 1080, 750 ms per hour, and three exports from the same page. An independent
Chromium decoder checked all frames, dimensions, delays and infinite looping
after timing. The GIF was 367,387 bytes and its SHA-256 was identical across
the three exports.

The baseline source reports dirty state explicitly: the dialog has fixture-only
observation callbacks; the test, its type declaration and Playwright configuration
are added for measurement. The production capture/composition/encoder source is
unchanged. Fixture and geography hashes are recorded. Decode checks and hashing
are outside timing; no provider latency is measured.

| Export | Load, s | Capture, s | Encode, s | Total, s |
| --- | ---: | ---: | ---: | ---: |
| First | 5.347 | 39.175 | 0.489 | 45.016 |
| Warm 1 | 0.014 | 41.498 | 0.429 | 41.941 |
| Warm 2 | 0.015 | 42.741 | 1.361 | 44.118 |

Browser: Chromium 156.0.8078.79, Node 24.22.2, SwiftShader software WebGL,
1440 × 900 viewport. Software map rendering dominates these samples. Do not use
these times to predict hardware-accelerated desktop export performance, compare
them against online-tile/provider workloads, or turn them into strict CI budgets.

Repeat using the drivers and scope notes in the [benchmark index](../../README.md).
Preserve older dated evidence and compare the same inputs, output density,
build profile, browser renderer and resource limits before drawing conclusions.
