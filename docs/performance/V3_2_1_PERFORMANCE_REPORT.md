# v3.2.1 performance and reliability report

Status: the latest candidate is [39396569171](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39396569171),
exact runtime source `a9d65e9a6d77805027a8729c7cb72c5541cbc02d` on
`release/v3.2.1`. It passes all 27 hosted jobs and all 34 PR checks.
Publishing is off; no main merge, tag or public v3.2.1 release has occurred.
Earlier candidate results below are historical evidence, with their own exact
artifact identities. Checkpoint 18 records the map cleanup and completed native
diagnostic; fresh two-hour stability and presentation remain unverified.

**Release remains on hold.** The preceding completed two-hour workload
in [39375787049](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39375787049)
ran 59 cycles / 7,271.891 seconds without recorded page errors, but its 241
process samples show post-warmup private growth of **2,466.34 MiB**, a fitted
**2,093.59 MiB/hour** slope and **5,751.04 MiB** peak. The WebView renderer
accounts for most growth (393.3 -> 3,999.4 MiB); GPU and Python processes are
comparatively bounded. This is not accepted long-session stability. PresentMon
produced no CSV, so native FPS also remains unverified. Checkpoint 17 and the
preserved raw artifact 12248600784 record this blocker. Public distribution stays
on hold and release metadata is Unreleased.

The **15-minute** diagnostic [39390340461](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39390340461)
completed seven real workload cycles / 933.684 seconds on the maintained
0d0ae3b installer. Its initial/final heap snapshots show:

| Retained renderer object | Initial | Final |
|---|---:|---:|
| WebGL2 contexts | 4 | 28 |
| Detached MapLibre canvases | 0 | 24 |
| Canvas DPR listener closures | 4 | 28 |
| Array-buffer backing bytes | 201,872,880 | 385,272,136 |

Pending MediaQueryList listeners retain CanvasObserver, its old canvas, and the
map's tile data. Each projection change recreates four panels; the workspace
also keys MapView, so Model Viewer -> Live Tracking -> Model Viewer recreates
eight maps even with both projections flat. The locked deck.gl 9.4.0/luma.gl
9.4.2 cleanup finalizes layers without destroying the default CanvasContext's
observers.
The shared overlay creation path now captures that public context and destroys
it after the existing removal, including failures. No arbitrary cap or quality
reduction is used.

Five focused runtime unit tests, 14 update/highlight UI tests, six Python
packaging/report tests, TypeScript, focused ESLint and all three build modes
pass. All four map-details browser tests pass in 46.7 seconds; the lifetime
regression keeps five resolution queries (four current canvases and the shared
graphics probe) after both projection changes. Collection in that check tests
reachability; it does not establish production memory stability. The rebuilt
live @Browser preview also draws the four maps after projection/workspace
changes.

The fixed installer now completes [diagnostic 39398050023](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39398050023):
seven cycles / **931.869 seconds**, no page errors. Its snapshots show **4 -> 4
WebGL2 contexts, zero detached map canvases, and 4 -> 4 DPR listener closures**.
Array-buffer backing bytes are 201,867,272 initially and 214,595,772 finally,
versus 201,872,880 -> 385,272,136 before the fix. This verifies the targeted
listener/map retention fix in the short installed workload. It does not establish
long-session memory stability: renderer private memory still peaks at 1,183.92
MiB and ends at 1,010.46 MiB, and heap snapshots can force collection. The user
requested no further lengthy validation; no new two-hour soak was started.
Native presentation still produces no usable displayed-frame CSV. Keep this
limitation and the unperformed public-updater restart explicit.

For candidate 39396569171, actual downloaded bytes match all eight checksums,
all nine cryptographically verified provenance subjects and all 4,991 portable
file hashes. The SBOM contains 5,478 components / 5,408 dependency records /
14,337 edges. The updater verifier accepts its exact signed installer/version
and rejects both tampering and version substitution. The complete offline lane
passes 3,794 tests with two Windows-only skips in 213.518 seconds; Windows WRF
passes 66/66, no skips, in 26.226 seconds. Both unchanged timing gates pass;
the frozen/installed upgrade reports also pass. Publication remains off.

The preceding candidate 39382361268 uses CPython 3.13.16/OpenSSL 3.5.9 and NetCDF4 1.7.5 with
its own OpenSSL 3.6.5/curl 8.23.0. Actual frozen/installed rendering and saved-file
upgrade checks pass. The complete offline lane passes 3,794 tests with two
Windows-only skips in 215.374 s; its unchanged timing gate passes. Windows WRF
passes 66/66 with no skips in 29.554 s. Downloaded artifacts match all eight
checksums, nine verified provenance subjects and 4,991 portable file hashes.
The official CycloneDX 1.6 structural schema passes: 5,478 components, 5,408
dependency records and 14,337 edges, with explicit semantic coverage limits.

The latest completed opt-in live-provider contracts pass (run 39386404877): all
three ECCC/HRRR contracts, 42.002 seconds. The one optional native-equivalence
collection skip is not a provider-contract skip. The extended SBOM now hashes
all shipped files and records PE versions/imports, package ownership and external
Windows/WebView2 requirements. Windows VEX records the absence of the two
Linux GTK advisory packages without suppressing their all-target warnings.
Native preference migration and repeated normal restarts pass on the earlier
recorded installer. The two-hour memory trend, native presentation FPS and
interactive public-updater restart remain pending; added test tooling is not
passing measurement evidence.

Final-acceptance update: candidate [39374559150](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39374559150)
passes all 27 jobs at `1e0312b`, including the maintained Windows Python/TLS
bundle. Its downloaded SBOM has 5,473 components, hashes for all 4,981 shipped
files, 5,398 dependency records and 14,171 edges. The official CycloneDX 1.6
structural schema passes; optional internationalized formats retain the earlier
Ajv 6 limitation. The Windows root remains explicitly incomplete for semantic
static-library identification; its shipped-file assembly is fully enumerated.
External Windows/WebView2 requirements are declared separately from bundled
files. Delvewheel DLL aliases are now related to possible native import names;
this static inventory does not claim to be a runtime loader trace.

The later [39377525097 candidate](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39377525097)
also passes all 27 jobs at `c65b882`. It includes the DLL-alias inventory fix:
5,468 components, 4,981 shipped files, 5,398 dependency records and 14,198 edges.
The remaining external `snappy.dll` import is explicitly unresolved. The full
offline lane passes 3,794 tests with two Windows-only skips in 214.701 s and
passes its unchanged hosted timing gate. Downloaded runtime reports, metadata
checksums, official SBOM structural validation and exact-source hosted SBOM
provenance pass. Full-download verification also confirms all eight checksums,
all nine verified provenance subjects and all 4,981
portable file hashes match candidate 39377525097. The real updater verifier
passes against its downloaded installer, including corruption and version
substitution rejection. Its two-hour soak is still not inferred from another
build. All 34 PR checks pass at `8895eb5`, including corrected virtual-clock
ordering in the four-panel browser case.

The unresolved `snappy.dll` originates in the official NetCDF4 1.7.4 Windows
wheel's Blosc library. An isolated verified wheel probe cannot load the optional
Blosc/Bzip2/Zstandard plugins; this is a pre-existing upstream codec limitation,
not a file removed by packaging. Core packaged NetCDF4/WRF dependency checks
pass, but do not establish those optional codecs' availability. The dependency
inventory keeps the unresolved import visible and does not invent its version.

That wheel also includes OpenSSL 3.6.0 and curl 8.17.0. The official
[NetCDF4 1.7.5 patch](https://github.com/Unidata/netcdf4-python/releases/tag/v1.7.5rel)
has Windows wheels for Python 3.11 through 3.15, and the verified 3.13 wheel
ships OpenSSL 3.6.5/curl 8.23.0. The release constraint is now 1.7.5 to take
those upstream native security fixes without replacing individual DLLs.
The [OpenSSL advisory index](https://openssl-library.org/news/vulnerabilities-3.6/)
records 3.6.5 security fixes; this does not claim every older-library advisory
was reachable through this application. Isolated 1.7.5 still lacks the optional
compression dependencies, so that limitation remains. New hosted artifacts and
scientific/WRF checks are required; earlier candidate evidence stays separate.

Installed migration of Light theme, Celsius/km/h, 2x playback and four panels
passes from 3.2.0 to the Python 3.13 candidate and through another normal native
restart, including correct release-highlight presentation/dismissal. Its actual
frozen import/render and saved-file upgrade reports pass with Python 3.13.16,
Rust 3.2.1 and a 39-level sounding. The ongoing two-hour workload is run
39375787049; raw captures show no displayed native frames on Microsoft Hyper-V
Video, so native presentation FPS remains unverified. The memory workload and
its trend review are independent and still pending. See Checkpoint 16 for
exact installer/source identities; later builds are not assumed byte-identical.

First candidate run: `39366194816`, exact commit
`6473f4fe7a54ade4e1351caa19512a6e63ff6645`. Its complete offline,
deterministic/coverage, Python/Rust property, compatibility, Windows WRF and
static quality/audit jobs pass. Two browser shards fail on stale acceptance
expectations and release-highlight footer overflow at 1280 x 800. The acceptance
checks now select all sounding times and follow the installed highlight count;
the popup scrolls its notes while keeping close controls visible. Checkpoint 14
records the fixes and required new candidate run. These failures are preserved;
no timeout or scientific/timing assertion is relaxed to make them pass.

## Earlier hosted candidate verification

[Release run 39367398243](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39367398243)
completes successfully: 27 jobs pass, none fail or cancel. The opt-in live
provider and public publication jobs are intentionally skipped. Downloaded
runtime reports, final bytes and provenance were independently inspected;
job success alone is not the artifact-verification evidence.

| Gate / artifact | Verified result |
| --- | --- |
| Full Linux Python 3.13 offline lane, forced Rust | 3,789 passed, two Windows-only skips (affinity and junction), 106.602 s, unchanged hosted timing gate PASS; NPZ 3.706 s. Includes the final optional-contour preservation regression. |
| Compatibility / science / WRF | Python 3.11/3.12 compatibility, deterministic coverage, complete Python and Rust property lanes and real Windows WRF all pass. |
| Frontend | 831 passed, two opt-in benchmark skips; 77 browser cases pass (four shards), including 1280 x 800 highlights and time 49 -> 1 selection. TypeScript/lint/359-file boundaries/all builds pass. |
| Native boundaries and audits | 66 ordinary tests and both real sidecar cleanup cases pass. Format/Clippy, constrained Python/npm/two Cargo audits and frozen backend pass; recorded Cargo warnings remain. |
| Final updater bytes | Real updater verification passes against final signature sidecar, URL/name/version; corrupt bytes and substituted version fail. Same downloaded bytes pass the no-window verifier locally. |
| Installed upgrade | Previous v3.2.0 and upgraded v3.2.1 runtime reports both succeed with correct Rust/version identity, 39-level sounding and completed PNG. Saved sample and user-created file remain intact. Interactive preferences/updater restart are outside this check. |
| Final SBOM | 417 components: 414 runtime with licenses plus installer, ZIP and wheel. 415 dependency records / 1,019 edges, source SHA `456dec3`, dirty=false, artifact hashes. Official CycloneDX 1.6 structural schema and dependency references pass; optional IRI/IDN formats are not checked by Ajv 6. Explicit incomplete native/system/binary coverage retained. |
| Checksums and provenance | Eight SHA256SUMS entries match downloaded bytes. All nine files match verified attestation subjects, with repository, workflow, source/ref/signer digest and hosted-runner checks. Portable ZIP integrity/content checks pass. |

The final installer SHA-256 is
`66f185fb9a9a9454eff9bc215dc79cc36d1631893f21997ded607dadcf72fceb`.
[Candidate Windows artifacts](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39367398243/artifacts/12240177926)
and [installed runtime evidence](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39367398243/artifacts/12240003163)
are uploaded within the authorized candidate scope and kept for 14 days.
Local copies, all logs, raw JUnit/timing and verification scripts/results are
preserved in `C:\Users\shian\Documents\CodexReports\v3.2.1-candidate-ci`.
No public tag, GitHub Release, main merge or updater distribution was performed;
latest stable is verified still v3.2.0. These are candidate-readiness results,
not public-release or unmeasured native presentation claims.

## E12–E22 follow-up measurements and limits

Raw evidence and the current source checkpoint are separate from the original
patch evidence: `C:\Users\shian\Documents\CodexReports\v3.2.1-e12-e22`.

| Item | Implemented behavior / evidence | Status |
| --- | --- | --- |
| E12 | Production stdio framing measured on a synthetic cached 4 MiB uint16 grid: 30 warm requests, 3 warmups; median 30.470 ms to receive the 5,592,915-byte JSON response, p95 31.572 ms. Python parent JSON/base64 decode is another 14.700 ms median; standalone Python base64 encode 12.028 ms. Provider I/O/science/GPU excluded. | Transport retained: no binary candidate demonstrates end-to-end benefit; a protocol migration remains conditional future work. Existing native single-allocation SHRF assembly is preserved. |
| E13 | All panels have renderer startup/recovery watches; secondary construction/draw failures produce local retry notices; successful draws clear their failure. Duplicate errors do not repeatedly log while failed. | Focused lifecycle checks passed; secondary notice/reload regression included in final frontend gate. |
| E14 | Shared field boundary rejects missing required scalar/wind components as FIELD_FETCH_FAILED while preserving a usable scalar when its optional contour is absent. Partial auxiliary and imagery-decode failures have panel status/retry, and correctly dated same-source fallbacks. Obsolete decode failures cannot replace current status. | Initial 35 Python map checks and 17 frontend image/notice checks passed; final 46 field/map/cancellation checks also pass after the optional-contour correction. |
| E15–E16 | Station changes occur on explicit selection rather than source mount; UI persistence catches access/quota failures once and retains session state. | Both old-code regressions fail; 40 focused state/workspace checks passed, including source and workspace remounts. |
| E17 | Optional ordered file times in summaries; existing reversible view request takes a validated time index. Chart calculations, edits and exports use that profile. Edit histories are separate per time and closing protects all of them. | Real 49-time BUFKIT final profile matches its decoded temperatures; returning to the original preserves it and source SHA-256. Python group 62 passed. @Browser shows final F048 / 2026-06-27 06Z at 4564 x 3077 and keyboard-selects all 49 times. |
| E18 | Arrow-key map panning plus Enter selects the map centre through the existing forecast point inspector. | @Browser confirms an arbitrary 41.22 N / 78.37 W point, correct HRRR F000 identity and four parameter readings; focused regression preserves Live Tracking behavior. |
| E19 | Memoized label inputs/view avoid repeated station/observation layouts on contour-only updates; camera zoom/bearing/pitch, selection, labels and source arrays invalidate them. | Four panels, 933 stations, 30 frames: 111,960 projections before vs 3,732 after (96.7% fewer), exact work-count regression. |
| E20 | CI provisions Python and fails rather than skipping browser process lifecycle tests; native CI explicitly runs ignored normal/forced sidecar cleanup checks. Bridge changes trigger the boundary workflow. | Committed gates pass on hosted candidate `456dec3`, including both real sidecar cases. |
| E21 | The actual updater library verifies staged installer bytes, signature sidecar, exact destination, signed version, corruption rejection and version-substitution rejection. Release CI installs previous stable then candidate in a disposable runner, verifies frozen import/rendering and preserved saved files. | Final candidate verifier and actual hosted v3.2.0 -> v3.2.1 installed upgrade pass. Downloaded bytes/reports verify independently. Interactive preference/restart behavior remains untested. |
| E22 | CycloneDX 1.6 resolved Windows runtime graph: npm production, installed Python runtime extras/SHARPpy/native wheel, and locked Windows Cargo normal edges; available licenses, artifact hashes, source SHA/dirty flag and explicit incomplete binary coverage. Included before release checksums/provenance. | Uploaded candidate graph: 417 components / 1,019 edges, matching artifact hashes and verified provenance. Earlier local 414-component / 1,020-edge graph remains historical environment evidence. |

Actual four-panel playback used the **Intel Iris Xe** ANGLE/D3D11 renderer in
the visible @Browser development page, HRRR 2026-10-20 01Z, height/wind 500 mb,
MLCAPE, 0–1 km shear and EHI. One 20.001-second sample has 1,158 RAF intervals:
p50 16.7 ms, p95 16.9 ms, maximum 116.7 ms, and 15 intervals over 33.34 ms.
The displayed forecast advanced F000 through F006 during recording; playback
continued to F014 before being paused. Four panels were loaded at the paused
identity. The raw intervals and timestamped identities are retained. Provider
waits are visible in multi-second forecast advances. This is **browser RAF
scheduling**, not GPU presentation, a before/after desktop FPS comparison,
or proof of native WebView2 pacing. `frontend/tooling/playback-check.html`
reproduces the check with the real app; production builds omit that tool page.

The initial current audits found three `zip 4.6.1` advisories
(RUSTSEC-2026-0367/0368/0369), and three aiohttp 3.14.3 advisories
(CVE-2026-111063/111064 and GHSA-2g87-pp6c-29x5). Disabling only unused legacy
ZIP updater support removes zip/arbitrary/derive_arbitrary without upgrading
other Cargo packages. Signed NSIS, TLS and proxy features remain. The runtime
constraint is aiohttp 3.14.5 (its required yarl updates to 1.25.1 locally).
Current npm production, both Cargo locks and installed Python audits report
zero known vulnerabilities. Desktop all-target Cargo warnings still identify
proc-macro-error (unmaintained) and glib (unsound/Linux); these remain explicit
findings rather than ignored advisories. Audit date: 2026-10-20. An advisory
scan is not a complete license or binary inventory; SBOM coverage records the
unidentified system, WebView2, native-library, resource and build components.

Final local gates: 3,790 offline Python tests passed, zero skips, timing PASS
at 264.52 s (two workers). The NPZ smoke render is 5.15 s against the authorized
16.8 s ceiling. A final auxiliary-record preservation correction follows that
gate and passes all 46 field/map/cancellation checks, including its new case;
the existing numerical/renderer paths do not change. Full frontend: 831 passed,
two opt-in benchmarks skipped, four workers, 58.56 s. TypeScript, full ESLint,
359-file boundary scan and all desktop/browser/fixture builds pass. Native
format, all-target Clippy and 66 ordinary Rust tests pass; real sidecar checks
pass 2/2 separately. The two sidecar cases, SHRF benchmark and final-artifact
case are ignored by default, not ordinary-test passes. Release packaging/highlights/SBOM checks pass 17/17. Workflow YAML
and PowerShell verifier syntax validate. At that local checkpoint the final
artifact test was ignored because no signed installer existed; the actual
candidate test now passes in CI and on the downloaded bytes. Build chunk-size
advisories remain. The final SBOM structural check records Ajv 6's optional
IRI/IDN-format limitation; dependency reference integrity is checked separately.

Earlier intermediate failures are preserved: original station/persistence and
renderer regressions, the first label-cache attempt missing zoom invalidation,
Windows UTF-8 inventory reads, and source file-size boundaries. Final results
are recorded after resolving them; no failing run is relabeled a pass.

The baseline is published v3.2.0, primary checkout commit
`b2b396df766717b6ecbb0758aa83bc1964ddd7dc`. Windows host, repository `.gribenv`,
native Rust backend 3.2.0; isolated import paths and native synchronization
verified. Historical v3.2.0 gains are not counted as patch improvements.
The paired measurements use that common version label on reference/candidate.
After the implementation gate passed, manifests, locks and contract fixtures
were prepared for 3.2.1 and its matching extension was rebuilt and verified.

## Measurement and acceptance contract

Retain individual before/after samples, source/input identities, environment,
work counts, cancellation/failure classifications and exact test commands in
`C:\Users\shian\Documents\CodexReports\v3.2.1`. Full operation comparisons
include production orchestration. CPU preparation does not establish FPS;
short resource tests do not establish a process-memory cap or long-session
stability. Do not change numerical tolerances, field resolution, chart output
quality, cache retention limits or provider validity to improve a result.

The previous report's 40 field / 19 contour INTERNAL warnings and connection
pool warning are investigation inputs, not reproduced current defects. The
current source already marks cancellation with `details.cancelled = true` but
the shared native router logs every failure at warning level without those
details. Queue cancellation, shared source acquisition and prepared field/science
caches already exist and must be preserved.

## Accepted implementations and measurements

### Request reliability

Fresh map requests could join an acquisition whose existing consumers had all
cancelled. The previous owner's unconditional cleanup could then remove its
replacement's tracking entry. The shared bridge now replaces abandoned work
and removes only the flight it owns. Four deterministic HRRR/NAM/RRFS/REFS
regressions fail on published code; the cancellation/map group passes 43 checks.
Shared live consumers still prevent the producer from stopping.

Native failures now retain a validated request token, wire ID, method and child
generation. Python exception logs include the wire ID. Only an explicit boolean
cancellation flag on a non-retryable INTERNAL error receives expected-operation
classification; other errors remain warnings with details. Browser diagnostics
likewise distinguish the actual cancellation exception. Existing error codes,
protocol and retry contracts remain unchanged. Known successful late replies and
timeout cancellation acknowledgements are debug diagnostics; late errors and
unknown IDs remain warnings. No historical warning is retroactively declared
harmless. Baseline correlation/classification regressions fail; focused Python
and native checks pass. Final gate counts are recorded below when available.

The completed live-browser session records 39 expected cancellations at INFO,
three genuine UWyo availability timeouts at WARNING, zero connection-pool-full
warnings and zero traceback headers. These are session totals, not a matched
before/after error-rate experiment. The historical 40 field / 19 contour warning
counts are not reused as a patch baseline. See `browser-session-diagnostics.json`
and the original log; genuine failures remain visible.

### Failed range-group cleanup

The provider awaited range futures in submission order and had no request-local
failure stop signal. A later range could fail while an earlier read blocked;
queued transfers continued, and a later sibling failure could replace the real
cause. Completion-order observation now stops that group's siblings and queued
work on failure, preserving the original error. Default openers check cancellation
before connecting and between chunks. Each group has its own signal: another
product or consumer's download is not stopped. Decode order, output arrays,
range identities, pool sizes, timeouts and scientific tolerances are unchanged.

Twenty interleaved reference/candidate samples run the complete eight-range
group with two workers, one immediate injected failure and 50 ms sibling
deadlines. This is a deterministic local fault experiment with stand-in network
reads, not a download-bandwidth or successful meteorological-processing benchmark.

| Metric | v3.2.0 | Patch |
| --- | ---: | ---: |
| Median group failure/cleanup latency | 203.756 ms | 1.519 ms |
| Median started range requests | 8 | 2 |
| Original provider error preserved | 0 / 20 | 20 / 20 |
| Active transfers after each group | 0 | 0 |

Evidence: `range-failure-measurement.json`, its script/reference source/log,
the two old-code failures and `range-failure-after.xml` (52 focused provider/map
checks). A socket already inside a blocking connect/read still observes the
existing timeout before the next cooperative check; no instantaneous socket
interruption or live bandwidth gain is claimed.

### Qt worker resource reclamation

The existing renderer deletes native Qt windows, but Python cycles can retain
their invalid wrappers and native pixmaps until a later full garbage collection.
Four real probes each found 50 such wrappers; collection reclaimed about
110 MiB per job in 0.10-0.13 s. The serial worker now performs collection after
render cleanup, including failure, before its response. Active prepared science
remains referenced and is preserved. No chart drawing/calibration code changes.

Two real isolated production `RenderClient` workers process 15 paired requests,
alternating order. Reference executes the published worker source; candidate
adds collection. Cases include cold, three warm, ML/theta, temperature and wind
edits, both storm motions, reset, UHD and five additional unchanged views.
Every PNG byte, decoded RGBA pixel and view-metadata field matches across all
15 pairs. Reset reproduces the original, and the input hash remains unchanged.

| Full worker operation / sequence | v3.2.0 | Patch |
| --- | ---: | ---: |
| Warm unchanged RPC median, 8 samples | 1.901 s | 1.873 s |
| Warm unchanged CPU median | 1.813 s | 1.789 s |
| Sequence peak working set | 683.3 MiB | 448.4 MiB |
| Post-job private-memory range | 1051.3-1547.2 MiB | 966.3-977.1 MiB |
| Tracked widget wrappers after final job | 351 | 0 |

This is a measured memory improvement on this Windows host, not a claim of a
rendering speedup, hard memory cap, all-input bound or multi-hour soak. Warm
times are similar; collection has its own small cost. First renders and single
edited/UHD pairs prove output parity, not repeatable mode-specific acceleration.
Current input: bundled HRRR NPZ, SHA-256
`a0067c6e19b9608160c7786170102d8ff98298fb2df32a4b61731ad26cfe302c`.
HD remains 3260 x 2198; UHD remains 4564 x 3077. Evidence:
`worker-comparison/{results,summary}.json`, all 30 original PNG/view files,
worker metric streams/source snapshots, paired script and log.

The implementation uses Python's existing cyclic collector rather than adding
a memory manager or dependency. [Python's primary documentation](https://docs.python.org/3.11/library/gc.html)
describes reference-cycle collection; the local native-buffer measurements,
rather than Python allocation counts, justify applying it at this worker seam.

### Automatic map work outside Model Viewer

The retained map mounted `usePlayback` in every workspace, so automatic field
prefetch continued outside Model Viewer. Its effect and query ownership now
follow the active workspace; leaving Model Viewer aborts obsolete preparation
and releases that effect's query owners. Existing visible/other consumers keep
their ownership. The map's displayed state and user-requested bulk downloads
retain their existing behavior. Five baseline tests each issue two unnecessary
future-field calls; all five now issue zero. The 28-check playback/query group
passes, including multi-panel delay, both sampling modes and cancellation.
No FPS improvement is inferred from these request counts.

### HTTP connection reuse

The actual native four-panel run reproduced the urllib3 warning: the shared
adapter retained eight connections even though eight existing bridge workers
can each issue four range requests. With non-blocking pools, excess successful
connections were discarded, forcing later requests to reconnect. This is
connection churn, not evidence of a blocked connection-pool waiter.

The adapter now retains up to 32 connections, matching that existing bounded
desktop concurrency. Worker/range counts, retry policy and non-blocking pool
behavior are unchanged. No global semaphore or uninterruptible pool wait is
introduced. Two 16-request loopback bursts using the production adapter
previously opened 24 distinct TCP connections. With the patch they open 16;
two 32-request bursts also reuse all 32 initial connections. Payload bytes
match and all test-server/client resources close. No TLS/network-throughput
speedup is inferred from the loopback count. The final native rerun remains a
separate gate: the user requested browser-only interaction before it completed.

Evidence: `connection-pool-before.xml` (24 versus 16, failed baseline),
`connection-pool-after.xml` (four passing pool/cancellation checks). The capacity
follows today's eight-worker topology; a future worker-count change must review
it again. Explicit socket cleanup during ordinary process exit remains unchanged.

### Model sources and scientific boundaries

The existing one-source model-hour cache already latches abandonment, keeps
strict model/run/hour/member/spatial keys and protects active dataset leases.
Its source/Storage/regression group passes 95 checks, including exact output
arrays, REFS member separation, cropped-point boundaries and clear/shutdown
ownership. No additional cache or decoder rewrite is justified. The shared
field/range fixes above apply to each existing map-model route; they do not
establish live NAM/RRFS/REFS extraction speedups. Scientific kernels, formulas,
NumPy/Rust ownership, masks and numerical tolerances are unchanged.

### Resource policies and deferred experiments

The live-response cache already shares a 128-entry / 128 MiB disk budget across
radar, satellite, observations and alerts, limits each response to 8 MiB, keys by
the full method/parameters, and retains immutable history separately from the
current minute's TTL. Failed calls are not cached. Atomic writes and failed-write
handling preserve live responses when retention is unavailable. Existing tests
cover corrupt data, simultaneous responses, locked files, unwritable storage and
exit cleanup. These budgets are retained.

The model-hour cache protects active leases during clear/eviction; Storage keeps
open sounding files, active partials and pinned/saved data, rejects links and
Windows junctions, and reports partial failures. Map retention reserves space
before admitting a write. Query observers protect active preparations, and
prepared map images are weakly owned by their field frames. The sounding cache
already has a 24-entry LRU and the renderer retains one pristine prepared input.
These are entry/disk policies, not a process-memory bound. Existing regression
checks exercise the protections; a real disk was not deliberately filled.

No additional decoder, NumPy-copy or serialization migration is accepted:
existing Rust kernels, ecCodes, exact prepared science and compact field
transport already cover those paths. The renderer profile still puts composition
and widget construction on the critical path. More geometry/widget reuse needs
new calibration, font/layout, invalidation and memory evidence; this patch stops
at the measured buffer-reclamation fix. Loaded native playback/FPS, software-GPU
fallback, multi-hour memory behavior and cross-platform/interpreter matrices
remain final-runtime or hosted validation work. Browser request counts and CPU
preparation measurements do not prove those outcomes.

## Interactive checks and validation boundary

The user requested @Browser instead of opening the desktop app. The live browser
uses the repository Python environment and real Rust backend, not fixture data.
Rapid HRRR hour/parameter changes, four loaded fields, point inspection and
playback through F013 and a loop were observed. Run/hour/valid-time identities
and point values update together. Switching to NAM 3 km clears the old field
values and loads four NAM fields at 06Z/F000/06Z. Leaving active playback for
Overview cancels four field consumers, explicitly logged at INFO with tokens;
Live Tracking subsequently loads its current CONUS mosaic without a loading
indicator or backend error. This does not prove radar echo values in a quiet
viewport or establish playback FPS.

The bundled HRRR file opens in Soundings, draws a 4564 x 3077 lossless image,
exports a valid full-size PNG, closes to an empty list and reopens successfully.
The exported PNG is 1,921,458 bytes, SHA-256
`35d518161b17999d24dc6cb75eb00acce6ee3e578ddfb1324e954290ee062cae`.
Browser screenshots, AX states and the PNG are saved with the measurement
evidence. The tested browser initially lacked error-operation context; the
localized response fix preserves the existing code/retry/details and adds the
operation/provider plus validated request token. Its old-code regression fails;
48 browser/cancellation checks pass. The final built-browser check verifies it:
a controlled invalid NPZ reports `SOUNDING_INVALID in open_sounding`, stays a
warning/non-retryable error, and opens no profile. Replacing it with the bundled
valid NPZ successfully draws the UHD image with the 3.2.1 version label.

The final browser uses the production live-preview build and rebuilt 3.2.1
backend. Repeated NAM -> HRRR -> NAM -> HRRR changes, F000 -> F002 -> F001,
and a switch to 500 mb Height/Wind complete with wind barbs/height auxiliaries
and matching forecast identity. NAM 18Z/F001 is valid 19Z; point 35.90 N,
88.61 W reports 22.3 kt, 0.00 J/kg, 2.7 kt and 0.00 across the four products.
The subsequent HRRR selection clears the pending wind value and shows its own
00Z/F000 identity and 4.3 kt low-level shear. Screenshots, AX states and console
logs are retained as `browser-3.2.1-*`. One browser-only bridge-event fallback
warning (health polling continues) and the existing `circle-11` basemap sprite
warning remain visible; they are not reported as fixed or as backend shutdowns.

Six earlier native startup/IPC/CSP/health/graphics checks and three independent
map cases passed on Intel Iris Xe. The first native four-panel playback run was
interrupted by an agent edit triggering Tauri's watcher; it and the following
disconnected case are not passing evidence. The native app is closed and will
not be reopened under the user's browser-only instruction. No final-patch native
FPS gain, packaged-runtime pass or multi-hour soak is claimed. Genuine UWyo
availability timeouts and the upstream basemap sprite warning remain visible.

## Original patch validation and preparation (historical scope)

Before changing version metadata, a complete implementation run passed all
3,782 offline assertions and the existing timing budgets in 181.76 s, with
four workers and forced Rust (`serial-release-verified.*`). The lane includes
the original full scientific properties/equivalence, renderer calibration,
missing data, model identity, provider, storage, WRF and packaging regressions.
No scientific tolerance or image assertion changes. It excludes the separately
scheduled live-provider lane by design.

Earlier failed runs are preserved. The first run had 3,781 passes and one
packaging failure because the local output directory contained a pre-existing
3.0.3 wheel. A current locked wheel was built separately, selected explicitly
with the existing `SHARPMOD_RS_WHEEL` option, and passed all ten packaging checks;
the older artifact was preserved. An interrupted startup produced no result.
The next full run passed all 3,782 assertions but failed the 14 s NPZ timing
ceiling at 18.925 s. Focused cold/module checks measured 14.13 / 6.16 s. The
subsequent full run passed the original ceiling at 6.54 s. These variable
observations are retained, not presented as a rendering speedup.

The user then requested 20% timing headroom. The local serial-release NPZ
ceiling is now 16.8 s (14 x 1.20); baseline 6.92 s, hosted limits, other
suite/test limits and scientific tolerances are unchanged.
`timing-margin-validation.json` verifies the 14.13 s case passes while the
18.925 s integrated case still fails under that margin. The final prepared-tree
lane uses timing enforcement and this checked configuration.

| Validation scope | Result / evidence |
| --- | --- |
| Full implementation offline lane, forced Rust, 4 workers | 3,782 passed; original timing budgets pass, 181.76 s |
| Full frontend unit suite | 821 passed; two opt-in performance cases skipped, not passed |
| TypeScript, ESLint and import boundaries | Pass; 357 source files checked |
| Scientific Rust stable format, all-feature/all-target Clippy and tests | Pass; 79 assertions plus existing benchmark smoke targets |
| Prepared 3.2.1 Python versions, packaging, budget checks and Rust/GRIB equivalence | 165 passed |
| Prepared 3.2.1 frontend contracts and release/update highlights | 77 passed |
| Prepared 3.2.1 desktop/live/fixture web builds | All pass; existing large-chunk advisories retained |
| Prepared 3.2.1 native Rust format, Clippy and ordinary tests | Pass; 66 assertions; one opt-in SHRF performance case unrun |
| Real 3.2.1 Python sidecar | 2 passed, including normal/forced cleanup; no ignored sidecar case |
| Python correctness/maintainability Ruff, pip check | Pass |
| Installed dependency audit | No known vulnerabilities in the recorded environment |
| Full prepared 3.2.1 offline lane, forced Rust, 2 workers | 3,782 passed; timing gate passes, 154.88 s; zero skipped tests |

The final prepared-tree four-worker run passes all assertions in 182.23 s but
fails the NPZ ceiling at 20.30 s. This failure remains recorded in
`candidate-serial-release.*`. The final two-worker run matches the existing
hosted release-lane command and passes the same complete test/property set and
all ten tracked timing checks (`candidate-serial-release-two-workers.*`); its
NPZ case is 7.08 s. Run-context variability remains a concern for four-worker
validation. These runs do not isolate a causal hardware/scheduling effect and
are not an application rendering-speed benchmark. No further ceiling increase
or assertion/example reduction was used.

Version preparation updates the canonical Python attribute, scientific/native
manifests and application lock entries, frontend manifests/locks, eight
health/handshake fixtures and the workflow's default/example tag. Lock comparison
proves dependency versions did not change. The changelog target and README say
Unreleased; published 3.2.0 history/downloads remain intact. The existing version
guard now accepts an explicit matching pending target in both documents without
requiring a publication date. Release highlights for 3.2.1 are separate from the
preserved prior versions. Isolated imports resolve to this checkout, Python and
native versions are 3.2.1, and `sharpmod-rust-sync --check` passes. The older
3.0.3 wheel is archived unchanged in external `preexisting-native-wheels/`;
the verified candidate wheel is also copied to the normal local output directory.
All ten packaging checks now pass with default wheel discovery. Source and
evidence manifests identify the final files without requiring a commit or push.

## Release-readiness assessment and deferred work

The expanded local patch is implemented, measured, regression-protected and
prepared consistently for 3.2.1. The current validation counts are at the top of
this report; the original 3,782/821-test tables above are historical evidence.
The final 49-time production Browser check confirms F048 / 2026-06-27 06Z,
lossless 4564 x 3077 output and the 3.2.1 label. Changelog/highlights/README,
USAGE, architecture documents and Checkpoint 15 describe the delivered behavior.
Scientific formulas, numerical tolerances, chart quality and the existing
architecture remain intact. Dependency changes are limited to the justified
transport security fixes recorded in the follow-up audit.

The authorized candidate implementation/verification is complete. Committed
boundary and UI gates execute successfully, the real final updater verifier
accepts correct bytes and rejects tampering, and actual hosted in-place upgrade
reports verify frozen rendering and saved-file preservation. The useful SBOM,
checksums and cryptographically verified provenance are uploaded with candidate
artifacts. These E20–E22 results close the previously pending automated gates.
The conditional E12 migration was measured and rejected without an unproven
protocol rewrite. Scientific, numerical and image parity checks remain intact.

Interactive preference migration/updater restart, native loaded-playback/GPU
presentation, live-provider checks and multi-hour memory behavior remain outside
these candidate results. Native GUI interaction stays deferred under the user's
Browser-only instruction. No native FPS or live NAM/RRFS/REFS extraction speedup
is claimed. Genuine provider timeouts, timing variability, Cargo warnings and
documented browser/basemap advisories remain visible. Public release and native
interactive acceptance require separate authorization; candidate success is not
approval to distribute an update to installed users.

The exact current working tree and evidence hashes are preserved in the
follow-up directory's `final-source/`, `source-manifest.json`,
`working-tree.patch` and `evidence-manifest.json`, separately from the original
patch snapshot. The reviewed source and CI fixes are committed/pushed on
`release/v3.2.1`; final runtime artifacts are from `456dec3`. The subsequent
documentation-only commit records results and changes no runtime, workflow,
lockfile or release-note input. The latest verified published version remains
v3.2.0 at the baseline commit; no v3.2.1 tag exists. The owned production preview
on port 5184 and marked @Browser tab remain for review; the measurement server
is stopped. No main merge, tag, GitHub Release or public updater distribution
was authorized or performed. Final run/artifact evidence is preserved in
`C:\Users\shian\Documents\CodexReports\v3.2.1-candidate-ci`; all candidate
downloads remain needed validation evidence. No disposable installation or
extraction was made locally for this CI handoff. Preserve all previous evidence,
including failed runs and the older native wheel.
