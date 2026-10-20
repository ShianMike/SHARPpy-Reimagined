# v3.2.1 implementation checkpoints

Current status: Checkpoint 16. The user requested completion of the outstanding
native acceptance, live-provider, Cargo warning, SBOM and release work. Preserve
the verified `456dec3` candidate as historical evidence; the final-acceptance
continuation changes the bundled Python runtime and requires new artifacts.
No merge, tag or GitHub Release has been performed.

Latest continuation snapshot (2026-10-20 05:52 UTC): draft PR **#92** is open
and attached to this chat. Head before this documentation/inventory adjustment
is **d23ec71**. Its 34 PR checks pass, three opt-in checks skip. Release candidate
run **39374559150** passes all 27 jobs at exact source
**1e0312bf64d2f5cf2fa3f9de9dd30e8656171249**, with public publishing off.
The sole browser failure of earlier run 39373561821 is retained: its four-panel
fixture download had only three virtual-clock advances in 15 s on hosted
software rendering. The readiness wait is now bounded at 60 s inside the same
120 s functional test, with every assertion retained. This does not change a
scientific, benchmark or hosted NPZ budget. The focused browser check and the
new complete hosted candidate pass afterward.

Active two-hour native run: **39375787049**, job **118473494576**, harness
source **d5c63d0**, installed Python 3.13 candidate from build job of
**39373561821** / source **db9c301**. Its three native preference/restart phases
pass. Loaded HRRR panel identities and repeated sounding/radar/model cycles are
running (six cycles / 730 s observed at the last live read). Do not cancel it
for documentation or inventory-only changes. The first PresentMon sample
contains **zero displayed frames**, even when targeting the app and all its
own WebView2 processes. Preserve this as an unavailable native-display
measurement on Microsoft Hyper-V Video, not a native FPS pass or a zero-FPS
application benchmark. The memory workload continues independently. On
completion, download `native-windows-acceptance`, inspect `soak-complete.json`,
all process samples, raw captures/errors and `native-summary.json`; make a
post-warmup memory plot. The job may fail solely because actual displayed-frame
telemetry is unavailable; inspect the independent memory result.

Earlier native **39375124180** passed preference migration/restarts on the
3.13 candidate and loaded four real HRRR fields, but its GPU-only capture
created no CSV; that measuring failure is preserved. **39373564738** was
cancelled deliberately when superseded after its three preference phases
passed; its partial soak is not a multi-hour result. **39374889523** failed
checkout because actions/checkout treated an abbreviated SHA as a branch;
always pass a complete SHA. **39375013557** was superseded before the latest
trace diagnostics were in place. Preserve every run's separate evidence.

Downloaded earlier 3.13 artifacts are under `artifacts-python313/`; all eight
SHA256SUMS entries match, including installer
`de4fa2564756408125705403654aa7238137c380d0c627aab124ac8008a5d8a7`.
Runtime reports prove Rust 3.2.1, Python 3.13.16, 933 stations, a 39-level
sounding, 1,228,894 PNG bytes and successful saved-file-preserving installation.
Metadata from passing candidate 39374559150 is in
`candidate-metadata-39374559150/`: **5,473 components / 4,981 shipped files /
5,398 dependency records / 14,171 edges**, source clean. Its official CycloneDX
schema check passes. The two Windows VEX statements remain. Native DLL versions
confirm OpenSSL 3.5.9 and Python 3.13.16. The new inventory additionally links
delvewheel's 32-hex-mangled DLL names to their possible unmodified import names;
normal names, aliases, owners, missing imports and reparse rejection have five
focused checks. Semantic static-library coverage is still explicitly bounded.

These builds are **not byte-identical**: 4,964/4,981 shipped files match;
17 executable, native-wheel, base-library and package-metadata files differ.
Package versions agree; the source changes between db9c301 and 1e0312b are
documentation/acceptance tooling, with no scientific/runtime implementation
change. The in-progress soak applies to its stated installed candidate, not
silently to a later installer hash. Six release highlights now group the
background-work summary and include the bundled security update. Any later
public installer still needs exact final updater/version/runtime verification.

## Checkpoint 16 — native acceptance and binary inventory continuation (2026-10-20)

Work remains solo. The user cannot manually launch the native app; local
execution policy rejected launching it, so do not route around that rejection.
The opt-in hosted job in `tests.yml` exercises the unmodified installed app,
real preferences, native restarts and a two-hour loaded maps/soundings/radar
workload. It records child-process memory and PresentMon display telemetry.
Its public-update mode checks the real signed updater and automatic relaunch
after the public feed is available. Silent NSIS upgrade is a separate check.

Current commit `db9c301`: candidate Release run **39373561821**, native harness
run **39373564738** against the earlier signed installer **39367398243**.
Inspect both jobs before claiming results. The final Python runtime candidate
must receive its own native acceptance once built. The native host is Microsoft
Hyper-V Video, not Iris Xe; do not generalize its presentation data.

Live-provider run **39370236498** is complete: all three live ECCC/HRRR
contracts pass in 36.709 seconds. The one collection skip is an unrelated
optional native-equivalence module. Raw logs/JUnit/timing are retained in
`C:\Users\shian\Documents\CodexReports\v3.2.1-final-acceptance`.

The file inventory now includes every shipped file's SHA256/size, available PE
versions, imports (including delayed imports), Python RECORD ownership, and
explicit external Windows/WebView2 requirements. Local analysis of the older
candidate enumerates **4,974 files**, 318 PE DLL/PYD/EXE files, and 5,462 total
SBOM components; it passes the official CycloneDX 1.6 structural schema.
This local graph uses the local installed Python metadata, so it is analysis
evidence, not the new release SBOM. Preserve unresolved static imports and
unknown statically linked sublibraries explicitly. Do not call OS versions
fixed or every transitive native library identified.

Both Windows Cargo graph traversals exclude glib/proc-macro-error, including
build/dev edges. The SBOM retains RUSTSEC-2024-0429 and RUSTSEC-2024-0370 as
Windows `not_affected` / `code_not_present` VEX statements and fails generation
if affected packages enter the Windows graph. The all-target Linux GTK warnings
remain visible; no advisory was suppressed or declared globally fixed.

Binary inspection exposed old CPython 3.11.9/OpenSSL 3.0.13 in the first
candidate. The release bundle now uses CPython **3.13.16**, its **3.5.9** TLS
runtime, a cp313 Rust wheel and bundled Python/OpenSSL licenses. Hosted logs
confirm the new Python's original TLS is already 3.5.9. The first new guard
incorrectly compared Python's OpenSSL tuple to `(3,5,9)`; OpenSSL 3 uses
`(3,5,0,9,...)`. This is corrected to `(3,5,0,9)` and the unnecessary direct
DLL replacement was removed. Source version/metadata stay 3.2.1.

Failed runs retained: native 39371384486 and 39372255979 could not expose CDP
from an elevated WebView2 host. Standard-token task attempt 39373091836 also
failed. Run 39373564738 uses Microsoft's documented app-specific HKLM debug
override on the disposable runner, localhost only, removed in `finally`.
Do not change local-machine policy or ship debugging hooks. Release 39372253035
failed workflow startup because the reusable caller needed `actions:read`;
that permission is fixed and covered by packaging tests. Release 39372387570
and 39373088420 failed the incorrect TLS tuple guard; retain those logs.

Focused verification: 29 packaging/SBOM/updater tests pass before the report
addition, two native-report tests pass, TypeScript and acceptance-spec ESLint
pass, and nine highlights/settings tests pass. New report checks reject short
soaks and present-only traces with no displayed frames. No new dependency was
introduced. Changelog and six grouped in-app highlights reflect the changes.

Continue: inspect the active jobs, resolve any actual failures, download/verify
new artifacts and run the full two-hour workload against those bytes. Validate
both display captures and the post-warmup memory trend. Then finish protected
PR/release validation and public updater verification as authorized; preserve
all evidence and update this checkpoint with actual results.

## Checkpoint 15 — hosted candidate verified (2026-10-20)

Release run [39367398243](https://github.com/ShianMike/SHARPpy-Reimagined/actions/runs/39367398243)
passes against exact source `456dec39dd63007014ec3d69bd711954c7b3240b` on
`release/v3.2.1`: 27 successful jobs, zero failed/cancelled, two intentional
skips (opt-in live-provider contracts and public release publication).
This includes all committed E20 boundary/UI gates and the added real sidecar
cleanup check. No native app UI was opened locally; execution remained solo.

Results inspected from raw job logs/JUnit and actual downloaded artifacts:

- Python 3.13 Linux full offline gate: 3,789 passed, two Windows-only skips
  (process affinity and junction protection), 106.602 s, timing PASS with the
  unchanged github-actions budgets. NPZ smoke 3.706 s. The added optional
  contour-preservation case is included in this complete run.
- Python 3.11/3.12 compatibility, deterministic/coverage, full Python and Rust
  property lanes, Windows WRF runtime and correctness/dependency audit pass.
- Frontend: 831 passed, two opt-in benchmarks skipped; all 77 browser cases
  pass across four shards (20/19/19/19), including all-time selection and the
  1280 x 800 release-highlight footer/scroll regression. TypeScript, full lint,
  359-file boundaries and all three build modes pass.
- Desktop format/Clippy, 66 ordinary Rust cases, real Python sidecar normal/
  forced cleanup (2/2), both Cargo audits and frozen backend checks pass.
  Existing proc-macro-error/glib advisory warnings remain recorded.
- E21: the final installer passes the real updater-library test, including
  its exact URL/name/signature/version and rejection of modified bytes and
  substituted version. Downloaded bytes pass the same no-window verifier
  locally too. Hosted silent in-place v3.2.0 -> v3.2.1 upgrade passes, with
  both installed runtime reports showing correct versions, Rust backend,
  39-level profile and completed PNG rendering. The user-created file and
  saved sounding hashes survive. This is automated installation/runtime
  evidence; interactive preference migration and updater restart are not tested.
- E22: the actual candidate SBOM is uploaded with the other candidate files:
  417 components (414 runtime plus installer/portable ZIP/wheel), 415 graph
  records, 1,019 edges, licenses on all 414 runtime components, artifact hashes,
  clean source identity matching `456dec3` and explicit incomplete binary
  coverage. Official CycloneDX 1.6 structural and graph integrity checks pass;
  optional IRI/IDN format checks remain outside Ajv 6's verification.
- All eight checksummed files match downloaded bytes; all nine files including
  SHA256SUMS match cryptographically verified provenance subjects. `gh
  attestation verify` enforces the repository, release workflow, source/ref/
  signer digests and hosted runner identity. Portable ZIP CRC/content checks
  pass (5,003 entries); installer hash is
  `66f185fb9a9a9454eff9bc215dc79cc36d1631893f21997ded607dadcf72fceb`.

Durable local evidence is in
`C:\Users\shian\Documents\CodexReports\v3.2.1-candidate-ci`: exact completed
run JSON, all workflow logs, failed first-run diagnostics, raw timing/JUnit,
both sets of installed-runtime reports, final nine-file artifact directory,
schema/integrity/provenance reports and candidate/evidence hash manifests.
GitHub candidate artifact IDs are 12240177926 (Windows files) and 12240003163
(runtime reports), retained for 14 days; local copies preserve the evidence.
The first run's browser failures remain historical, not relabeled successful.
No disposable extraction or installation was made on the user's machine;
preserve the downloaded candidate and all reports as validation evidence.

The runtime/source changes are exactly those tested at `456dec3`. The final
documentation-only commit updates this checkpoint, the report and changelog
status; it does not change code, workflows, locks, highlights or version inputs.
No full-suite rerun is needed for these status documents. Changelog and release
highlights remain consistent, version 3.2.1 remains Unreleased, and live latest
stable remains v3.2.0 at `b2b396df766717b6ecbb0758aa83bc1964ddd7dc`.

Remaining limits: E12 stays conditional after measurement; no binary protocol
replacement is justified. Native presentation FPS, multi-hour memory soak,
interactive updater/preference behavior and live-provider runs are unverified.
Candidate CI/SBOM publication is complete within the user's approval; public
release publication, main merge and tags require separate authorization. Keep
the active release branch and preview for review, not branch cleanup. The owned
preview remains on 5184; the measurement server is stopped. Continue only from
the relevant recorded evidence if new work is authorized; do not restart the
completed patch or rerun unchanged gates.

## Checkpoint 14 — candidate CI findings (2026-10-20)

Candidate commit `6473f4fe7a54ade4e1351caa19512a6e63ff6645` was pushed to
`release/v3.2.1`; candidate-only Release run 39366194816 tests that exact source.
The full hosted offline gate, deterministic/coverage, Python and Rust properties,
3.11/3.12 compatibility, Windows WRF and static quality/audit pass. Browser
shards 1/2 pass. Preserve its run and failing browser screenshots/logs in
`C:\Users\shian\Documents\CodexReports\v3.2.1-candidate-ci`.

Shard 4 exposed an obsolete acceptance assertion for "first of 49 times";
both browser and manual desktop sounding checks now select the last time and
return to the first, checking valid-time headers and completed images. Shard 3
exposed a hard-coded five-highlight count and a real minimum-window issue:
six longer highlights scrolled the entire popup, hiding the footer. The count
now follows the installed notes; the existing popup uses fixed header/footer
grid rows and a scrollable notes body. Its pre-existing footer-visibility
regression remains, with an additional check after scrolling the last item.
No shared popover behavior, scientific calculation or timing threshold changes.

Modified after the first commit: three acceptance specs, the local What's new
layout rule, matching changelog/highlight copy and these checkpoint documents.
Focused follow-up checks pass: 23 notes/update/sounding UI cases, six updater/
SBOM packaging cases, TypeScript, full lint/359-file boundaries, and rebuilt
browser/fixture assets. @Browser confirms the rebuilt popup's close control is
inside its bounds; 1280 x 800 scrolling remains an explicit hosted regression.
Next: commit/push the fixes and run candidate CI against the new exact commit.
The first Windows build and desktop audit are still live as this checkpoint
is written; inspect their actual terminal state before replacing a run. Retain
any produced installed-upgrade evidence even though the first overall run fails.
Publication, tagging and main-branch merge remain outside this approval.

## Checkpoint 13 — candidate CI authorized (2026-10-20)

The user approved committing and pushing the reviewed candidate and running
Release with `publish_release=false`, including signed candidate CI uploads.
This does not authorize a tag, GitHub Release, merge to main or public updater
distribution. Continue solo and use @Browser for local interactive work.

Before branching, all 100 reviewed source paths match the saved SHA-256
manifest, `git diff --check` passes, and local/remote main both remain at
`b2b396df766717b6ecbb0758aa83bc1964ddd7dc`. Latest stable remains v3.2.0;
the new candidate branch is `release/v3.2.1`. This checkpoint and the report's
authorization status are the only changes after the reviewed source snapshot.
All runtime/test behavior and the authorized 16.8 s local timing limit remain
as validated at Checkpoint 12.

Next: commit the candidate, push that branch without force, dispatch Release
against its exact commit with tag input v3.2.1 and `publish_release=false`.
Save the source SHA, run ID, every required job outcome and downloaded artifact
hashes under `C:\Users\shian\Documents\CodexReports\v3.2.1-candidate-ci`.
Follow the actual live job state; diagnose failures and rerun only when a fix
or a verified infrastructure problem warrants it. E21 requires the real final
installer verifier and both installed runtime reports to pass, not just compiled
test code. E22 requires the produced runtime SBOM with useful graph coverage,
licenses, artifact hashes and matching source identity. Preserve warnings and
all failed results. Inspect candidate checksums and attestation before reporting
success. Do not claim public release or native interactive acceptance.

## Checkpoint 12 — local follow-up complete; candidate CI pending (2026-10-20)

Completed: the E13–E20 fixes and E22 audit/SBOM preparation described at
Checkpoint 11. E12 was measured and the existing protocol retained because no
binary replacement demonstrates an end-to-end benefit. E21 has runnable final
artifact and installed-upgrade gates, but their actual execution is pending.
This follow-up ran solo and used @Browser; no native GUI was launched.

Final verification:

- Full forced-Rust offline lane: 3,790 passed, zero skips, two workers,
  264.52 s wall time, timing PASS. NPZ smoke 5.15 s versus the user-authorized
  local serial-release limit of 16.8 s. Hosted limits and scientific tolerances
  remain unchanged. Native synchronization passed before this run.
- Final review corrected the partial-field guard to require scalar inputs
  while preserving a usable scalar when an optional contour is missing.
  All 46 field/map/cancellation checks pass afterward, including its new case;
  that case was added after the 3,790-test run and is not counted in it.
- Frontend: 831 passed, two opt-in benchmarks skipped, four workers, 58.56 s.
  TypeScript, full ESLint, 359-file boundaries and all three builds pass.
- Desktop: format and all-target Clippy pass; 66 ordinary tests pass, with two
  real-sidecar cases, one SHRF benchmark and one final-artifact case ignored by
  default. Both sidecar cases pass separately, including forced cleanup.
- Release packaging/highlights/SBOM checks: 17 passed. Workflow YAML and the
  PowerShell upgrade verifier parse. The current 414-component runtime SBOM
  passes official CycloneDX 1.6 structural and dependency-reference checks.
  A second 415-component inventory includes the preserved native wheel and
  its verified SHA-256. Optional IRI/IDN formats are outside the Ajv 6 check.
- npm production, both Cargo locks and installed Python audits: zero known
  vulnerabilities. Retain the proc-macro-error unmaintained and glib
  unsound/Linux Cargo warnings. No license-compliance conclusion is implied.
- The rebuilt production preview opens the real 49-time BUFKIT file, selects
  time 49 by keyboard, and draws F048 / 2026-06-27 06Z at 4564 x 3077 with the
  3.2.1 label. Final screenshot and AX state are preserved. The earlier real
  four-panel RAF recording and 96.7% label-projection reduction remain valid
  with their documented limits; they do not establish native presentation FPS.

Modified files: the components listed at Checkpoint 11, plus its final scalar
preservation regression, timestamp validation, release notes and these final
documents. Exact modified/untracked source paths, bytes and hashes are captured
in `final-source/`, `source-manifest.json` and `working-tree.patch` under
`C:\Users\shian\Documents\CodexReports\v3.2.1-e12-e22`. Evidence hashes are in
`evidence-manifest.json`; the active preview log has a separate frozen copy.
The original `v3.2.1` evidence directory remains intact.

Known unresolved behavior regressions: none established by the completed local
checks. Earlier failed timing runs, concurrent one-second UI waits and initial
implementation regressions remain preserved; no failure was relabeled a pass.
Risks/remaining gates: hosted platform/interpreter/software-rendering lanes,
final signed installer verification, actual installed upgrade and interactive
preference/updater restart, native hardware presentation and multi-hour memory
behavior. SBOM publication remains part of authorized release distribution.

Exact continuation: inspect this checkpoint and the preserved source manifest
before any edits. The only owned server left running is the live production
preview on port 5184 (exec session 32732, @Browser tab 3); the measurement server
on 5186 is stopped. Retain the marked Browser tab for review. HEAD remains
`b2b396df766717b6ecbb0758aa83bc1964ddd7dc`; latest stable is v3.2.0 and no
v3.2.1 tag exists. No commit, push, tag, installer upload, release dispatch or
publication was performed.

Next requires the user's explicit authorization: commit and push a candidate
branch, then run the existing Release workflow for v3.2.1 with
`publish_release=false`. That builds/signs, runs hosted/final-artifact/installed
checks and uploads candidate CI evidence without creating a tag or GitHub
Release. Inspect every required result and retained artifact before claiming
those gates pass. Do not launch the native GUI under the browser-only request;
interactive native acceptance requires a separate change to that instruction.
Do not rerun completed full local suites unless a change or failure warrants it.

## Checkpoint 11 — E12–E22 implementation and verification (2026-10-20)

The follow-up implementation is present on top of the original 3.2.1 patch.
No subagents, native GUI launch, push, tag, release dispatch or publication.
The production browser preview is on port 5184. The optional measurement dev
server uses 5186; stop only the owned servers when their UI is no longer needed.

Completed changes:

- E13: graphicsStore holds panel-specific renderer errors. Every MapRuntime
  starts a watch; constructor, startup, later overlay and context-loss failures
  are visible. Successful rendering clears that panel's fault. Reload preserves
  product/camera state; repeated faults are deduplicated while failed.
- E14: bridge field/wind validation rejects missing decoded records as field
  failures. panelScene reports failed imagery decode, fetch or auxiliary data;
  same-source image fallbacks show their original time, and retries do not
  refetch disabled sources. Obsolete asynchronous failures are ignored.
- E15/E16: appStore selection actions update the observed station, replacing
  the source-mount effect. Zustand UI persistence catches storage access once
  and retains settings in session memory. Old-code regressions failed before
  their fixes; 40 focused checks passed afterward.
- E17: summaries optionally carry every file time. The existing view request
  validates a time index; the renderer selects it before applying edits/science.
  Each time has separate edit history, preserved across navigation and checked
  by discard protection. Contract fixtures include all 49 BUFKIT times.
- E18: Enter on a focused forecast canvas selects its centre, after ordinary
  arrow-key panning; Live Tracking keeps its existing behavior.
- E19: source/view-aware label reuse removes 96.7% of station projections in
  the four-panel, 30-frame work-count check. Real @Browser/Iris Xe playback
  recorded 1,158 RAF samples/20 s, p50 16.7 ms, p95 16.9 ms, max 116.7 ms,
  15 intervals over 33.34 ms, with seven displayed forecast identities.
- E20: repository CI definitions provision Python for browser lifecycle tests,
  require their presence, explicitly run real sidecar cleanup, and audit npm
  production plus both Cargo locks. Python audit uses release constraints.
- E21: release CI now requires updater-library verification of the actual
  staged installer and signed version, rejects tampering/substitution, then
  tests silent in-place installation from prior stable in a disposable runner.
  JSON runtime evidence is retained. Scripts/tests compile or parse locally;
  actual signed-candidate and installed/interactive upgrade results are pending.
- E22: generate_sbom.py follows npm production, installed runtime Python extras
  and Windows Cargo normal graphs with dependency edges/licenses/source identity.
  The schema-valid local SBOM has 414 components / 1,020 edges. Release staging
  generates it before checksums/provenance. Unidentified binary/system resources
  and build-only components are explicitly outside its completeness claim.
  Removed unused vulnerable ZIP updater compatibility; constrained aiohttp
  3.14.5 and its required yarl 1.25.1. Current audit results: zero known
  vulnerabilities; proc-macro-error/glib all-target warnings retained.

E12 decision: retain protocol 1. Thirty real stdio cached-field requests measured
30.470 ms median receipt of a 4 MiB body (5,592,915-byte JSON frame), excluding
parent decode; Python JSON/base64 decode adds 14.700 ms median. No binary
candidate establishes end-to-end benefit sufficient to justify the migration.
Raw source/samples and limits are in bridge-transport.json and its script.

Focused validation: Python maps 35 passed; sounding/view group 62 passed;
frontend source/store group 40 and multi-time/keyboard group 39 passed; map
lifecycle/layer group 47 passed; Rust contracts 17 passed, ordinary desktop
tests passed, and real native sidecar checks 2 passed. Full frontend final:
831 passed, two opt-in performance tests skipped, four local workers, 58.56 s.
The earlier full eight-worker run during concurrent Qt/Rust work failed three
one-second download waits; focused and quiet full runs pass with limits intact.
The expanded focused Python run passed 188/189 but its obsolete step-name scan
mistook updater signature verification for removed Authenticode signing; the
step label is now unambiguous and the full offline gate rechecks that test.
Earlier file-size, missing-zoom invalidation, UTF-8 and formatting failures are
retained in raw logs and fixed in source. No known unresolved behavior regression.

Modified components: frontend sounding forms/view/store/contracts and map
runtime/panels/scene/stores, corresponding tests; optional tooling playback
page; Python bridge fields/contours/sounding summary/view and renderer time
selection; Rust sounding summary validation and updater-artifact test; CI
frontend/tests/release workflows; packaging SBOM/upgrade verifier; release
constraint, CHANGELOG, README, USAGE, architecture and performance documents.
The existing Rust sounding unit tests moved into dto/soundings/tests.rs to keep
the same enforced source size boundary; no test was removed.

In progress at this checkpoint: full offline Python serial-release with two
workers and its unchanged timing budgets, following a passing native sync check.
Evidence: serial-release-final.log/.xml/-timing.json in the follow-up directory.
Next: inspect that result, then finish final typecheck/lint/three builds, Rust
format/Clippy and newly strengthened timestamp check, final SBOM/schema/diff
review, and source/evidence hashes. Run focused diagnosis if needed, not repeated
full suites. Preserve both previous evidence sets. Do not claim release-ready
until signed artifacts, installed upgrade and hosted checks actually pass.

## Checkpoint 10 — E12–E22 follow-up scope (2026-10-20)

Solo execution; interactive verification uses @Browser. Publication, pushes,
tags, release distribution and installer uploads remain unauthorized. New raw
evidence goes in `C:\Users\shian\Documents\CodexReports\v3.2.1-e12-e22`,
separate from the completed original patch snapshot.

| ID | Acceptance / target | Risk | State |
| --- | --- | --- | --- |
| E12 | Measure existing Python/Rust bulk transport; replace base64 only with demonstrated end-to-end benefit and preserved validation | High: protocol compatibility | Pending measurement |
| E13 | Every map panel exposes construction/startup/runtime failure and recovery | Medium: renderer lifecycle | Tracing |
| E14 | Missing wind/contour data and image decode failures remain visible, retryable where appropriate, with correct frame identity | Medium: partial data | Tracing |
| E15 | Source-tab/workspace remount preserves typed station; explicit station selection still updates it | Low: form state | Reproduced from source |
| E16 | Shared UI persistence survives quota/access failures without losing current session state or flooding warnings | Low: storage fallback | Reproduced from source |
| E17 | Every time in a multi-time file can be selected for view, analysis and export; edits remain tied to their time | Medium: collection identity | Tracing |
| E18 | Keyboard users can select an arbitrary forecast point using existing inspection flow | Low: accessibility | Pending |
| E19 | Record real multi-panel playback intervals and remove only measured repeated work | Medium: GPU/cache ownership | Pending measurement |
| E20 | Existing boundary/UI tests execute in committed applicable CI gates | Low: assurance | Tracing |
| E21 | Validate final updater manifest/signature/artifact identity and installed upgrade behavior | Medium: packaging/user data | Pending |
| E22 | Audit resolved shipped graphs and generate useful distributable SBOM with provenance, licenses and graph coverage | Low: build metadata | Pending |

Known regressions: none newly established; E15/E16 are existing defects. No new
performance claims. Next: focused regressions and shared root fixes for E15/E16,
then renderer and partial-data handling. Preserve scientific tolerances; the
previously authorized 20% local NPZ timing headroom remains 16.8 seconds.

## Checkpoint 1 — verified baseline and implementation scope (2026-10-19)

The primary checkout is clean `main` at
`b2b396df766717b6ecbb0758aa83bc1964ddd7dc`. GitHub's latest stable release is
v3.2.0, published 2026-10-19T12:23:30Z from that commit. The historical v3.2.0
performance documents describe earlier candidates and release holds; their
measurements remain historical evidence, not the current repository status.

Read: PERFORMANCE_AUDIT, BENCHMARK_RESULTS, FINAL_PERFORMANCE_REPORT,
RUST_MIGRATION_PLAN, TASK_CHECKPOINTS, ARCHITECTURE_DECISIONS, frontend
ARCHITECTURE, CHANGELOG, CONTRIBUTING and local release instructions.
The repository `.gribenv` imports this checkout under `-I`; native backend
3.2.0 is active and `sharpmod-rust-sync --check` passes.

The user requires solo execution. All three briefly started subagents were
stopped before any repository edits; status/diff remained clean. Do not start
or resume subagents. No push, tag, release dispatch, installer upload or
publication is authorized. Version preparation for 3.2.1 follows validation.

### Prioritized task checklist

| Order | Component / task | Acceptance and evidence | Regression risk | State |
| --- | --- | --- | --- | --- |
| 1 | Request cancellation and diagnostics | Reproduce current behavior; correlate wire/token/method; distinguish explicit cancellation from genuine failures; retain errors and retry semantics | Medium: concurrent/shared consumers | In progress |
| 2 | Obsolete field acquisition / provider resources | Test rapid abandonment, fresh consumers, independent requests, interrupted reads, saturation and recovery; preserve shared work | Medium: provider ownership | Investigating |
| 3 | Qt renderer composition | Profile full cold/warm/edited HD/UHD operations; accept only a repeatable gain with exact PNG/pixel/view parity | Medium-high: chart calibration and native resource lifetime | Pending |
| 4 | Model-hour sources / identity | Review existing reuse and leases for HRRR, NAM, RRFS and REFS; implement only reproduced gaps; preserve science | Medium: active dataset lifetime and member identity | Pending |
| 5 | Map playback / inactive work | Review rolling preparation and observer ownership; focused regressions and actual desktop interaction for accepted changes | Medium: shared queries and rendering | Pending |
| 6 | Memory / boundary efficiency | Target retained resources and redundant work; stress relevant fixes; report native/Qt/GPU limits explicitly | Medium | Pending |
| 7 | Integrated gates | Focused tests first, then full applicable Python/frontend/Rust gates and actual desktop flows; no weakened thresholds | Low for code, material release gate | Pending |
| 8 | Version / release preparation | Consistent 3.2.1 sources/lockfiles, Unreleased changelog, matching highlights, measured report and readiness assessment | Low: metadata consistency | Pending |

### Evidence and continuation

Raw evidence belongs outside the checkout in
`C:\Users\shian\Documents\CodexReports\v3.2.1`. Preserve existing v3.2.0
evidence. Run measured comparisons without concurrent builds or test suites.
No production behavior has changed at this checkpoint. Tests performed:
isolated import/native identity and Rust synchronization only. Benchmarks:
none yet. Known regressions: none identified; validation remains incomplete.

Next: inspect `sharpmod/bridge/{server,cancellation,fields}.py`,
`src-tauri/src/python/supervisor/requests.rs`, shared native router error
logging and frontend transport. Existing queued cancellation, prepared science,
field packing and model-source reuse are baseline, not new improvements.
Reproduce defects before editing, record old-code failures and final focused
results, then update this document with exact modified files and commands.

## Checkpoint 2 — reproduced defects and focused fixes (2026-10-19)

Implemented and locally checked:

- Field single-flight abandonment/rejoin race, shared across HRRR, NAM 3 km,
  RRFS and REFS map routes. Fresh consumers replace fully abandoned work;
  old cleanup can remove only its own flight. Four new regressions fail on
  v3.2.0; 43 cancellation/map tests pass after the fix.
- Request diagnostics: safe request token plus wire ID, method and child
  generation retained in native failure details; exception logs name the wire
  ID. Explicit non-retryable cancellation is classified separately; genuine
  errors retain warning severity and full diagnostic details. Browser baseline
  diagnostic tests fail twice; 81 focused bridge tests pass after the change.
  Native correlation baseline fails; 35 contract/supervisor tests pass.
  Subsequent stricter classifier/message checks await the next native gate.
- Automatic forecast prefetch used to issue two future field requests even
  outside Model Viewer in each of five inactive workspaces. New regressions
  fail in all five baseline cases; workspace-aware scheduling passes all 28
  playback/query-ownership checks. Retained map and explicit user downloads
  keep their existing state/ownership.
- Qt worker collection: four real prepared-render probes retain 50 invalid
  Qt wrappers after each render. Collection frees about 110 MiB with roughly
  0.10-0.13 s overhead. The serial worker now collects unreachable cycles on
  success/failure before replying. Both new regressions fail on v3.2.0; all
  three focused worker checks pass. Full paired actual-worker measurement is
  running; do not infer a rendering speedup from the diagnostic probe.

Changed source: `sharpmod/bridge/{fields,server,browser,render_worker}.py`,
`src-tauri/src/{error.rs,backend/sounding_ops.rs,python/supervisor/{mod,requests}.rs}`,
`frontend/src/map/usePlayback.ts` and the corresponding focused tests.
The native diagnostics had a temporary Rust borrow-check failure during
development; the ownership fix compiles and the 35 checks above pass. It is
not a known remaining regression. No science/formula/resolution change.

Next task: finish actual worker comparison, then reproduce and fix provider
range-failure cleanup with `test_hrrr_field_cancellation.py`. That test is
written but has not yet run. A failing range currently waits on earlier
futures and has no shared failure stop signal for sibling/queued transfers.
Do not overlap timing with builds/tests. Continue solo.

Active measurement: unified exec session 43865, external
`paired_worker_memory.py` and `paired-worker-memory.log`; results are under
`worker-comparison/`. Both production `RenderClient` workers use this checkout
under `-I`; reference executes the published worker source, candidate adds only
the job-boundary collection. Keep all PNG/view/metric/source artifacts.

After measuring: run the provider baseline regression, implement only the
root fix, repeat focused provider/science checks, run the real native app and
full milestone gates, and reconcile report/changelog/highlights/version state.
No release or remote mutation is authorized. Version remains 3.2.0.

## Checkpoint 3 — measured memory and failure cleanup (2026-10-19)

The 15-pair real-worker experiment completes successfully. Every original PNG,
pixel and full view-metadata field matches, including edits/reset/UHD. Peak
working set is 716,488,704 -> 470,183,936 bytes; candidate post-job private
memory remains 1,013,223,424-1,024,528,384 bytes across this sequence. Warm
unchanged medians are 1.901/1.873 s (8 samples each): treat latency as similar,
not a verified speedup. Reference leaves 351 tracked wrappers after the final
job, candidate zero. Both worker processes are closed. See the patch report
for sample scope and exact external evidence.

Range-failure regression fails on v3.2.0: the real provider error is replaced
by an uncancelled sibling's later error. A second regression proves an already
cancelled opener still starts connection work. The provider now observes range
completion order, sets one group-local failure event, cancels queued futures
and checks Stop before connection setup. Its ordinary decode/result order is
unchanged. All 52 focused range/provider/map checks pass. One initial focused
invocation used nonexistent test filenames and collected no tests; the corrected
run is `range-failure-after.xml` and is the passing evidence.

Twenty interleaved fault-injected group samples: median failure cleanup
203.756 -> 1.519 ms, median started requests 8 -> 2, original errors preserved
0/20 -> 20/20, zero active transfers after every invocation. These stand-in
reads are a cleanup benchmark, not live bandwidth/meteorological speed claims.

Further validation: 95 existing source/hour-cache/Storage/field identity checks
pass; Python Ruff passes; frontend typecheck/lint/boundaries pass. The stricter
native diagnostic/classification checks pass (17 contracts + 19 supervisor).
Source additions since checkpoint 2: `sharpmod/providers/hrrr_field{,_fetch}.py`,
`test_hrrr_field_cancellation.py`, stricter native contracts, and the actual
desktop ownership scenario in `frontend/e2e/desktop/reliability.spec.ts`.

Actual Tauri debug app runs on source `.gribenv`, forced native 3.2.0, separate
WebView profile, 1920 x 1080. All six startup/health/IPC/CSP/graphics cases pass.
Dedicated app supervisor session: 69848; Vite 5180, WebView CDP 9234. Real
four-case map plus rapid-selection/inactive-workspace test is running in
session 39954 (verify current unified session if resumed; the live port/log is
authoritative). Log/results: `desktop-map.log`, `desktop-map.json`,
`desktop-screenshots/`, `desktop-runtime.log`. No concurrent timed benchmark
or broad test/build is running. Retain genuine provider errors.

Next: inspect native map result and current warnings. Correct defects or test
assumptions without accepting provider errors as success. Validate BUFKIT/UHD,
stop only this dedicated native runtime, then prepare versions/notes and run
the final integrated release gates on the complete tree. Verify real source
imports again after rebuilding the 3.2.1 native extension. Continue solo and
leave publication/ref mutations for explicitly authorized work.

## Checkpoint 4 — browser-only UI validation at the user's request

The user explicitly requests @Browser instead of opening the native app. Do
not restart the Tauri application. The dedicated native app has been closed;
both prior native supervisor sessions have exited. Preserve the six passing
startup checks and three independently passing native map cases. The first
four-panel run was interrupted by an agent source edit triggering Tauri's
watcher; its playback failure and subsequent CDP failure are invalidated
integration evidence, not passing tests. Logs remain under
`desktop-map-interrupted.log` and `desktop-runtime-before-pool-fix.log`.

The pool warning was reproduced on the real native run. A local regression
demonstrates the actual cause: two 16-request bursts opened 24 connections
because the shared pool retained only eight. Retention now matches the existing
eight-by-four bridge concurrency (32), with no additional workers or blocking
pool admission. The 16- and 32-request burst cases and cancellation checks pass
(four checks). All bytes match and resources close.

@Browser now shows the real live build at `http://127.0.0.1:5184/` in the
in-app browser, tab 1, variable `appTab` in `mcp__cua_repl`. Use only that API
for further interactive UI checks. Browser server session 66613 uses the root
`.gribenv` and forced Rust backend. Port 5174 was already occupied, so the
first attempted server stopped without disturbing that existing process.
Current log: `browser-runtime-5184.log`. Do not close the user's existing
5174 server. The new native-only ownership spec was removed in favor of the
requested browser check and the passing hook regressions.

Browser evidence so far: rapid F000 -> F003 selection and parameter change,
four real fields, point values (50.7 F, 0.00 J/kg, 9.3 kt, 0.00) at the same
HRRR 11Z/F003/14Z identity. Screenshot: `desktop-screenshots/browser-four-panels.png`.
Playback is running; inspect current AX state before further actions. Native
FPS remains unverified for the final patch. Browser results cannot substitute
for that hardware measurement. UWyo timeouts remain visible genuine failures.

Next: finish browser playback/model/workspace/sounding checks, then fix the
observed missing browser error-operation context with a focused regression if
justified. Stop only this dedicated browser server before timing-budgeted
checks. Prepare consistent 3.2.1 version metadata and rebuild/synchronize the
native extension, then run integrated gates. Keep changelog Unreleased and
the 3.2.1 highlights separate from prior versions; neither authorizes release.

## Checkpoint 5 — browser workflows complete; integrated gates next

Completed: real HRRR four-panel playback through F013 and a loop, paused state,
NAM 3 km model switch with all four fields and matching run/hour/valid times,
active-playback exit to Overview, Live Tracking load, bundled NPZ sounding open,
UHD draw/export, close and successful reopen. Expected field cancellation logs
are INFO with tokens. Genuine availability errors remain warnings. Browser
evidence is `browser-{nam-state,live-state,sounding-state}.txt`,
`browser-{nam-four-panels,live-four-panels,sounding-uhd}.png`,
`browser-export-uhd.png` and `browser-runtime-5184.log` in the external report
directory. Export dimensions/bytes/hash are recorded in the patch report.

Browser error responses now carry operation/provider context and validated
request tokens. The two baseline regressions fail; all 48 browser/cancellation
checks pass (`browser-context-{before,after}.xml`). Source additions since
checkpoint 4: `sharpmod/bridge/browser.py`, its existing test file and these
reports. No additional scientific or architectural change. No known assertion
regression in completed focused checks. Final broad gates are still pending.

The dedicated 5184 server was stopped with Ctrl+C; no listener remains, and
its Python/render-worker PIDs 25100/17080/8384 have exited. The pre-existing
5174 server is untouched. Do not launch the native app. Native four-panel
playback/FPS on the final patch, packaged runtime and hosted CI remain explicit
release-readiness limits, not passing tests. Keep all evidence.

Continuation: finish the current frontend typecheck/lint process (session 30571;
check its actual completion), then checkpoint source identities and run the
`serial-release --workers 4` Python lane once with forced Rust and external
JUnit/timing reports, with no simultaneous browser workload/build. Run full
frontend tests/build modes and native/Rust format, Clippy, tests and real-sidecar
cleanup checks. Only after implementation validation passes, prepare consistent
3.2.1 manifests/locks/health fixtures/workflow defaults, rebuild/sync the native
extension, and run focused version/packaging/highlight checks. Update README,
architecture decisions and migration status without implying publication. Keep
the changelog Unreleased. Restart only the dedicated live browser server for
final version/context verification and leave @Browser showing the result.

## Checkpoint 6 — broad checks and native-wheel selection (2026-10-20)

The completed first offline lane has 3,781 passes and one packaging failure in
244.16 s. Its metadata check discovered the pre-existing 3.0.3 wheel under
`rust/sharpmod-rs/dist`; the active source/native backend is 3.2.0. Keep that
older artifact. A locked release wheel for the current 3.2.0 source was built
under the external `baseline-wheel/` evidence directory. All ten packaging
checks pass when `SHARPMOD_RS_WHEEL` names that exact wheel. No test or
scientific tolerance was weakened. Initial failure evidence is retained in
`serial-release.{log,xml}` and `wheel-packaging-corrected.xml`.

Completed broad checks: TypeScript; full ESLint/boundaries (357 source files);
821 frontend tests, with two opt-in performance cases skipped (not passed);
all Python correctness and focused maintainability Ruff checks; `pip check`;
dependency audit with no known vulnerabilities; native Rust format/Clippy and
66 ordinary tests (two real-sidecar cases and one performance case ignored by
default); scientific Rust stable format, all-target/all-feature Clippy and all
target tests (79 assertions plus the existing benchmark smoke targets). The
real-sidecar and final application build/version checks are still pending.

Docs now connect the historical migration plan to this patch and record D10,
the measured job-boundary cleanup and existing ownership/resource policies.
Modified since checkpoint 5: architecture decisions, migration plan, patch
report and this checkpoint. No implementation change since the broad gate.
The performance report still needs final integrated counts and readiness.

The user interrupted the first corrected-lane startup. Its log stops at worker
startup, has no JUnit/timing result and no matching live Python process; it is
not a passed run. The partial log is `serial-release-interrupted.log`. The
corrected lane is restarted with the exact current wheel and timing enforcement,
session 17731. Inspect its real process/result before restarting anything.
Keep heavy builds/browser workloads stopped until this timed lane finishes.

Next: if the corrected gate passes, recheck repository/publication identities
and prepare the authorized 3.2.1 version consistently, including locks and
contracts. Keep explicit Unreleased status in changelog/README and preserve
published 3.2.0 history/downloads. Rebuild/sync the extension, run focused
version/packaging/highlight tests, all frontend build modes and real-sidecar
cleanup checks; verify the final live browser context/version. No native GUI,
push, tag, publication or distribution is authorized. Continue solo.

## Checkpoint 7 — implementation validated; 3.2.1 preparation

The corrected implementation lane first passed all 3,782 assertions in 166.99 s
but failed the NPZ render timing ceiling (18.925 s versus 14 s). A focused cold
case passed its assertions in 14.13 s; the full 19-check render module then
passed all existing timing budgets, NPZ 6.16 s. A fresh full integrated run
passed all 3,782 assertions and the original timing budgets in 181.76 s, NPZ
6.54 s (`serial-release-verified.{log,xml}`, timing JSON). Preserve the earlier
failure and variability; do not label it a rendering speedup.

The user subsequently authorized 20% timing headroom. The final scope changes
only the local serial-release NPZ ceiling from 14 to 16.8 s;
baseline 6.92 s, hosted ceiling, suite budgets, scientific tolerances and image
assertions stay intact. `timing-margin-validation.json` proves the saved 14.13 s
case passes, the 18.925 s full-lane case still fails, and the verified full lane
passes under the new margin. No retrospective failure is hidden.

After implementation validation, the authorized 3.2.1 version is prepared in
Python/native/frontend/Tauri manifests and package locks, eight health/handshake
fixtures and release-workflow defaults. Dependency-lock comparison proves no
dependency changes. Changelog and README explicitly say Unreleased; published
3.2.0 history/downloads and both prior release-highlight versions are preserved.
The version test now accepts a matching explicit pending target in changelog
and README, as well as the existing dated-release path.

The 3.2.1 locked wheel is built and installed; the editable package metadata is
refreshed. Isolated imports resolve to this checkout and native 3.2.1;
`sharpmod-rust-sync --check` passes. Final preparation checks: 165 Python
version/packaging/budget/backend-equivalence checks, Ruff, pip check; 77 frontend
contract/update checks; all three frontend build modes; native Rust format,
Clippy and 66 ordinary tests. Both opt-in real-sidecar checks pass, including
normal and forced session-file cleanup (2/2, none ignored). One native opt-in
performance case remains intentionally unrun; this patch does not change SHRF
encoding. Build chunk-size advisories remain visible and are not failures.

Next: run the complete offline lane once against the prepared 3.2.1 tree and
exact candidate wheel with timing enforcement, with no parallel heavy workload.
Then start only the live browser preview for final version/error-context checks,
record screenshots, finish the report/readiness audit and preserve evidence.
Do not launch the native app or mutate remote refs/publication. No new feature,
scientific formula, dependency or architecture was added.

## Checkpoint 8 — prepared-tree timing diagnosis

All 3,782 prepared 3.2.1 assertions pass in 182.23 s, but the first NPZ smoke
render takes 20.30 s and exceeds even the authorized 16.8 s local ceiling.
This is a failed timing gate (`candidate-serial-release.*`), not release success.
The worker-boundary collection is not called by this direct-render smoke case.
Do not increase the margin again or hide the observation.

The complete lane is now running with two workers, matching the existing
`.github/workflows/tests.yml` release-lane command, to check scheduling context.
No test, Hypothesis example count, scientific assertion or image check is removed.
Session 98785; outputs `candidate-serial-release-two-workers.*`. Inspect the
result before deciding next steps. Keep heavy/browser workloads stopped.

The margin is scoped to the serial-release override only; the global NPZ
ceiling stays 14 s so other/hosted lanes retain their previous limits. Final
docs now use that precise scope. The 18.925 s historical run still exceeds
16.8 s and remains a recorded failure. Further work: resolve/review this gate,
verify built 3.2.1 in @Browser, finish the report and preserve all failed and
passing evidence. Native GUI/publication/remote mutations remain unauthorized.

## Checkpoint 9 — completed local preparation and handoff

The complete prepared 3.2.1 lane passes all 3,782 tests and its timing gate in
154.88 s with two workers, matching the current hosted command; no tests are
skipped and full Hypothesis examples remain. NPZ smoke is 7.08 s. Preserve the
four-worker assertion passes/timing failures and the original pre-version pass;
do not convert scheduling-context differences into application speedup claims.
Final local margin remains 16.8 s only for the serial-release NPZ check.

The built live browser preview verifies 3.2.1 highlights, repeated NAM/HRRR model
changes, rapid forecast hours, a 500 mb wind/height auxiliary field, consistent
point values/times, a deliberate genuine invalid-file error with named operation,
and subsequent successful UHD rendering with the 3.2.1 label. Current screenshots,
AX states and console logs are `browser-3.2.1-*` in the external evidence folder.
Browser event-poll fallback and basemap sprite advisories are recorded honestly.

Native/ordinary and opt-in sidecar gates, frontend tests/builds, scientific Rust,
identity/equivalence, lint/dependency checks and measured before/after results are
listed in the patch report. The default local wheel output now holds the exact
verified 3.2.1 wheel; the pre-existing 3.0.3 wheel is archived unchanged under
external `preexisting-native-wheels/`. Default-discovery packaging checks pass
10/10. No dependency versions changed. All manifests, runtime versions and eight
health/handshake examples agree on 3.2.1. Changelog/README remain Unreleased.

The disposable native WebView profile and duplicate Downloads export were
inspected for absolute paths, reparse points, active ownership and backup hashes,
removed with ordinary PowerShell commands, and verified absent. Keep the report
directory, all raw measurements/images/JUnit/logs and source/evidence manifests.
The live preview is active in session 87796 on port 5184, @Browser tab 1, variable
`appTab`; it is marked as a deliverable and uses the real 3.2.1 backend. Do not
launch the native GUI. Do not disturb unrelated servers or environments.

No implementation task remains for this local patch preparation. Further release
work requires separately authorized protected CI/ref changes, packaged-runtime
checks and final native hardware/playback validation. Follow the patch report's
scope limits and preserve current evidence. No push/tag/publication/distribution
is authorized by this handoff. A fresh local full check uses the normal wheel:

```powershell
Set-Location -LiteralPath 'C:\Users\shian\OneDrive\Desktop\sharppy reimagined\SHARPpy Reimagined'
$env:SHARPMOD_BACKEND = 'rust'
$env:SHARPMOD_REQUIRE_RUST = '1'
.\.gribenv\Scripts\sharpmod-rust-sync.exe --check
.\.gribenv\Scripts\python.exe scripts/run_test_lane.py serial-release --workers 2
```
# Final native acceptance continuation — 2026-10-20

The user requested completion of the outstanding updater/preferences, native
presentation, multi-hour soak, live-provider, Cargo advisory and SBOM work,
including the remaining release work. Work continues solo. Local native launch
was rejected by execution policy, and the user cannot launch it manually now;
do not bypass that restriction. Use the disposable hosted Windows acceptance
job in `tests.yml`, with the signed candidate from Release run 39367398243
(runtime source 456dec39, documentation head 72e2fcaf).

Live provider run 39370236498 passed: three current ECCC/HRRR contracts in
36.709 seconds. Its one collection skip is the optional native-equivalence
module, not a live-provider contract. Raw logs, timing JSON and JUnit are saved
under `C:\Users\shian\Documents\CodexReports\v3.2.1-final-acceptance`.

The new native job is opt-in and has not yet run. It checks installed 3.2.0
preferences across NSIS upgrade and two 3.2.1 starts, then runs a full two-hour
real workload with process samples and two PresentMon captures. Review actual
CSV displayed frames, hardware, workload identities and post-warmup memory
trend before accepting. A second `native_public_update=true` run requires the
public v3.2.1 feed and must verify the updater itself restarts the desktop.
Do not count the silent installer run as the interactive updater test.

Next: dispatch this job, finish shipped native/system inventory and target-aware
Cargo advisory evidence during the soak, then run protected release gates and
verify the public release and interactive updater. No remaining check is
declared passed by this checkpoint. Earlier authorization/status text below is
historical; the current user request governs this continuation.
