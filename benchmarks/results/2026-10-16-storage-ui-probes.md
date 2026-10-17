# Storage, chart interaction and availability checks

Measured on Windows on 2026-10-16 with the project's Python environment and
installed frontend dependencies. These are local measurements, not release gates
or guarantees for other computers and network conditions.

## Storage

Five runs per implementation, each using a fresh temporary cache containing
60 model entries with 10 GRIB files each, 2,000 map images and 500 outlook files.
The benchmark verifies the freed-byte count and the empty cache after clearing.

| Operation | Before, median | After, median |
| --- | ---: | ---: |
| Read usage for 3,100 files | 2,017.73 ms | 1,146.39 ms |
| Clear those files and refresh usage | 1,721.54 ms | 1,108.41 ms |

The revised traversal uses `os.scandir` metadata, avoids resolving each file,
and rejects symbolic links and Windows reparse points. Model metadata traversal
also skips links. Cleanup reuses its initial model-entry list and still validates
each deletion and refreshes usage afterward. One after-clear sample took 11.05 s;
filesystem timing is variable, so the table reports medians rather than promising
a fixed improvement.

Regression checks cover pinned entries, live leases, open profiles, saved files,
partial downloads, failed deletions, symbolic links and Windows directory junctions.
An additional check prevents the redundant intermediate model-entry scans from
returning.

## Chart and map display work

Sounding point titles are formatted when their values, selected display units or
editing mode change. Zoom and point-preview renders reuse the titles; normal
viewing does not build them. The sounding workspace test checks that zoom does
not rerun formatting and a profile edit still updates the title.

Fahrenheit surface labels are cached in a `WeakMap` keyed by the immutable query
result. Panels share the converted result, fresh observations produce new labels,
and unused query results can be garbage-collected. Source and Celsius views keep
the original result. The old description-regex concern was already addressed by
the numeric observation contract.

## Catalogue loading

Five hundred warmed iterations of JSON parsing plus strict contract validation:

| Response | Compact JSON | Median | 95th percentile |
| --- | ---: | ---: | ---: |
| Map catalogue (`get_map_catalog`) | 55,202 bytes | 0.98 ms | 1.79 ms |
| Model catalogue (`get_model_catalog`) | 37,350 bytes | 0.39 ms | 0.73 ms |

The large, indented fixture file is not its HTTP response size. Map and model
catalogues are separate commands; palettes belong to the map catalogue. Existing
query caching already shares each catalogue between observers. No payload or
validation change was justified by these timings.

## Provider concurrency

Each pass checks the same suggested runs and forecast hours for all 26 models.
Providers comprise 12 Herbie models, 10 NOAA NOMADS models, two ECCC GeoMet models
and two Open-Meteo models. Open-Meteo checks remain configuration-only and do not
spend the user's point-request quota.

The original serial passes returned 22 available, two configured and two unknown.
With four concurrent checks, ECMWF AIFS and GEFS incorrectly became unavailable
in both passes. Herbie probes share a protected requests handle; a queued probe's
network deadline was starting before it acquired that protection. The deadline
now starts after acquisition. A deterministic test reproduces a 20-second queue,
allows the subsequent request, and still enforces its eight-second network budget.

| Corrected pass | Serial | Four concurrent checks | Results |
| --- | ---: | ---: | --- |
| 1 | 30.781 s | 21.966 s | Identical |
| 2 | 42.742 s | 26.512 s | Identical |

All four corrected passes returned 22 available, two configured and two unknown.
Every NOMADS and ECCC check succeeded in both serial and concurrent passes. CFS
and ECMWF IFS remained unknown in both modes. Four frontend checks are retained:
this measured workload benefits, and the limit leaves four of the desktop
bridge's eight workers available for foreground requests. Herbie probes still
serialize access to their shared requests handle; their queue is not network time.

## Shutdown

Session cleanup uses Tokio's asynchronous filesystem operation, preserving error
logging and waiting for deletion to finish. The real Python-sidecar test checks
normal and forced exits, removal of session GRIB data and retention of a saved
sounding outside the session cache. No numerical shutdown-speed claim is made.

Raw timings and benchmark runners are retained locally under `.test-results/`:
`storage-before.json`, `storage-after.json`, `catalog-timings.json`,
`model-probe-before.json` and `model-probe-timings.json`.

## Verification

- 169 Python tests, 41 frontend tests and 31 Rust tests passed, including the real
  sidecar's normal and forced shutdown test.
- Frontend typecheck, lint and boundary checks, Python Ruff checks, Rust format
  checks and `git diff --check` passed.
- The isolated browser preview loaded a profile, zoomed, accepted a point drag
  and displayed Fahrenheit point titles without console errors.
- The restarted live backend returned Storage usage and enabled its controls
  without console errors. Real user caches were not cleared for this check.
- The installed Windows application's UI and installer were not rebuilt or
  retested. No pull request, merge or release was created.
