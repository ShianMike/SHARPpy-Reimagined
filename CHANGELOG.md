# Changelog

All notable changes to SHARPpy Reimagined are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.2] - 2026-08-23

### Fixed

- Restored observed-sounding display from the availability cache by preserving
  the legacy SHARPpy profile metadata required when a raw profile is promoted
  for the interactive viewer. This fixes the `Fetched, but could not display:
  'location'` failure reported for WBGB / station 96441.
- Disabled CDS logging and tqdm output only when the process has no writable
  standard-error stream, preventing ERA5 downloads in the windowed Windows app
  from failing with `NoneType` / `write` after retrieval succeeds.
- Restored every supported local sounding type to the native Open File dialog
  and normalized archive/BUFKIT metadata before adding a second sounding, which
  prevents the viewer from crashing on profiles without explicit model-run
  metadata.
- Made image and text export names follow the sounding currently focused in a
  multi-sounding viewer instead of remaining tied to the first opened profile.
- Preserved forecast classification when an SPC text sounding is paired with a
  generated metadata sidecar, so forecast profiles are not presented as
  observations.

### Changed

- Updated the locked release toolchain to setuptools 84.0.0, wheel 0.48.0,
  PySide6 6.11.2, PyInstaller 6.22.2, and Ruff 0.16.3, incorporating the six
  open dependency/action bump pull requests into one tested patch candidate.
- Updated the pinned SignPath submission action from 2.2 to 2.3.
- Removed the retired experimental forecast subsystem, including its UI
  readout, optional live fetch, offline research tooling, archived datasets,
  and cloud-batch scaffolding.

## [0.8.1] - 2026-08-10

### Fixed

- Reduced Windows first-window time by lazily constructing inactive picker
  tabs, deferring model-cache imports and pruning, importing the analysis stack
  only when a sounding opens, and sharing immutable basemap geometry.
- Reused a successful observed-sounding availability download when Generate is
  pressed instead of fetching and decoding the same profile twice.
- Deleted ordinary sounding viewers when closed, released picker references,
  and collected their Python widget cycles on the next event-loop turn so
  repeated open/close sessions no longer retain one viewer heap per cycle.
- Hardened Windows packaging so source, bundled Python metadata, Rust metadata,
  frozen runtime, and PE file/product versions must agree; official builds now
  reject stale editable metadata and optionally Authenticode-sign when both
  certificate secrets are configured.
- Made the faster one-folder ZIP the prominently labeled recommended Windows
  download while retaining the one-file build as an explicitly slower portable
  option, with signing state recorded in the release manifest.
- Made model downloads and GUI availability checks cancellation-aware and
  bounded, kept stale availability probes single-flight, corrected download
  progress, and disconnected closed sounding windows from live preference
  updates.
- Updated the locked release toolchain to pip 26.2.1, Ruff 0.16.1, and PyO3
  0.29.2, while keeping workflow bootstrap pins sourced from the release
  constraints so patch updates cannot make the release build self-conflicting.

## [0.8.0] - 2026-08-07

### Added

- Added checked JUnit performance budgets and JSON timing artifacts, bounded
  pytest-xdist lanes with one Qt/render worker group, a 3.11/3.12 compatibility
  smoke, one Python 3.13 coverage lane, one full 100-200-example property lane,
  and an exact non-parallel release gate.

### Fixed

- Bounded the 1 hPa layer-mean sampling by the layer it is asked for. Both
  `mean_wind` and `mean_wind_npw` built their sample pressures with
  `arange(pbot, ptop + dp, dp)`, which takes one step **past** the layer top
  whenever the layer depth is not a whole number of hectopascals. When that
  overshoot left the reported profile the sample interpolated to `MISSING` and
  was silently dropped, so the layer mean depended on where the fixed increment
  happened to fall rather than on the requested layer. The samples now end
  exactly at `ptop`, so the integration is bounded by `[pbot, ptop]` and never
  relies on extrapolating outside the profile.

  This is a real error, not a rounding difference: when the layer top *is* the
  profile's top level, the dropped sample removed the top wind from the mean
  entirely. MEASURED on the profile that exposed it, a 33-level sounding whose
  SFC-6 km layer is calm except at its 6 km top, the SFC-6 km non-pressure-weighted
  mean wind moved from `(-0.606, -2.719)` to `(-0.724, -2.850)` kt, which shifted
  the Bunkers right-mover motion by `0.18` kt and SFC-500 m SRH by
  `1.01 m^2/s^2` -- enough to disagree with upstream SHARPpy beyond the
  documented 1% tolerance. Agreement improved from `1.0142` to
  `0.000175 m^2/s^2`, roughly 5800x. Real soundings extend well past 6 km, so
  there the only change is the position of the final sample by at most 1 hPa.

  Fixed identically in the Python path and in the Rust `pressure_samples`
  backend so the two stay in parity, with the invariant pinned by tests on both
  sides. A whole-hectopascal layer keeps its historical sample set exactly.

  The property test that caught this now also compares the SRH **integration**
  on the oracle's own storm motion, which isolates the integration from any
  storm-motion difference. Its end-to-end comparison is skipped in the one state
  where upstream is not a reference: when the oracle's own SFC-6 km sampling
  steps outside its profile, its storm motion is built from a sample set that is
  not the requested layer. Upstream is not self-consistent there -- the
  out-of-domain sample is dropped for some profiles and resolved to the edge
  value for others -- so no single behaviour can match it, and the corrected
  reading is the one that includes the layer top. Requirement 1.5 remains
  covered by `test_winds_storm_motion.py`.

- Sharpened exported PNG text in the scaled image modes. Fonts are now created
  with `PreferAntialias | PreferQuality`, which `index_board` had applied locally
  after finding its bold face rendered pixelated, and with vertical-only hinting
  **while painting a density-scaled export surface**. Horizontal hinting snaps
  stems and advance widths in unscaled design space, and those snapped positions
  then land between physical pixels once the painter is scaled.

  MEASURED on real renders as the share of inked pixels fully on (higher is
  crisper): `hd` 0.357 to 0.372, `uhd` 0.527 to 0.568, with `uhd` now above the
  0.552 scored by rasterising the same face natively at the matching 25px size.

  The setting is deliberately scale-dependent rather than global: at 1x it
  measured **worse** (0.226 to 0.202), because full hinting is exactly what snaps
  stems onto whole pixels when there is no transform. `lossless` therefore keeps
  Qt's default hinting and is unchanged.

  Investigation also established what is *not* wrong, which bounds any further
  work here: HD and UHD are true high-resolution renders rather than upscales of
  a 1x raster, and their text already matched native rasterisation at the
  corresponding physical size before this change. The remaining softness at small
  sizes is a property of the bundled display face - at 9px it leaves only about
  5% of inked pixels fully on - which is a typography choice rather than a
  rendering defect, so it is left alone.
- **Every forecast hour after F000 was broken for five products** because
  terrain height is time-invariant and their providers publish it only at the
  run's analysis step. The verified-surface check read a single forecast-hour
  inventory, saw `surface_height` missing, and refused the sounding, so these
  models worked at F000 and failed at every other lead time. An audit of all
  eighteen configured products at F000 plus three later hours each found:

  | Product | Height field | Published at |
  | --- | --- | --- |
  | ECMWF IFS, ECMWF-AIFS | `z:sfc` (surface geopotential) | F000 only |
  | GEFS | `HGT:surface` | F000 only |
  | Canadian GDPS, RDPS | `*_GeopotentialHeight` WMS layer | analysis instant only |

  When surface height is the *only* missing element, GRIB extraction now
  downloads that one invariant message from the same run's F000 file and
  concatenates it into the subset, mirroring the existing CFS surface-companion
  route; the ECCC point provider requests its height layer at the run time
  instead of the forecast valid time. Verified end to end: terrain height is now
  identical between F000 and the completed forecast hour for all five products
  (IFS 355 m, AIFS 368 m, GEFS 342 m, GDPS 361 m, RDPS 340 m at Norman, OK). The
  refusal remains for genuinely incomplete products and its message now names the
  missing fields. `probe` reports these hours as complete with a
  `surface_contract_invariant_companion` flag, and it confirms the F000 file
  really carries the field rather than assuming it, so availability checks and
  the fetch path agree.
- Fixed a one-byte over-read in ECMWF byte-range downloads. Herbie's wgrib2
  inventories set `end_byte` to a message's last byte, but its eccodes
  inventories (ECMWF open data) set it to `_offset + _length`, which is the
  *first byte of the next message*. An inclusive HTTP `Range` therefore fetched
  one byte too many and the assembled stream did not end at the GRIB `7777`
  trailer. This stayed hidden only because a pressure-level selection happened to
  include the file's last message, where the server clamps the range at EOF;
  requesting any interior message on its own failed range validation and fell
  back to a full-file Herbie download. Eccodes inventories are now normalized to
  inclusive bounds before range planning.
- HiResW WRF-ARW and FV3 no longer advertise 06Z and 18Z cycles. NCEP runs the
  CONUS nests twice a day, so half the offered cycles could only ever fail with
  "no GRIB for run". Both are now configured for 00Z/12Z.
- ECMWF-AIFS forecast hours are 6-hourly, not 3-hourly. AIFS publishes steps
  0-360 by 6 at every cycle, but the picker offered the IFS 3-hourly ladder, so
  24 of 85 selectable hours (F003, F009, F015, ...) could only fail with "no
  ECMWF-AIFS GRIB for run".
- ECMWF IFS 06Z and 18Z now stop at F144. Those cycles are a short cut-off
  forecast; since IFS Cycle 50r1 (13 May 2026) they publish under `stream=oper`
  rather than the retired `scda` stream, but they still carry no step past F144.
  All 85 hours were previously offered at every cycle, so 36 of them always
  failed at 06Z and 18Z. Verified against live data: 00/12Z serve F360, while
  06/18Z serve F144 and nothing beyond.
- A failed ground-field companion download no longer fails the whole sounding.
  The companion path falls back to Herbie's own subset download on
  `OptimizedTransportUnavailable`, as the pressure-level path already did.
- ECMWF field planning was silently disabled, and every model downloaded
  redundant fields. `choose_*_fields` read an inventory column named
  `variable`, which is what Herbie's wgrib2 `.idx` inventories use, but ECMWF
  open data ships eccodes `.index` inventories whose column is `param`. Every
  ECMWF fetch therefore raised `model inventory has no variable column`, fell
  back to the broad configured search, and returned empty field provenance -
  which in turn made `cached_source_fields_compatible` always false, so the
  model disk cache was never reused for IFS or AIFS. Field selection now reads
  either column.

  Inventory narrowing was a second, separate defect: it matched records by
  variable *name*, which cannot express levels. That over-selected for NOAA
  products and would have mis-selected for ECMWF, where `z` is both the
  invariant surface field and a pressure-level field on every isobar. Narrowing
  now matches the chosen search expression, so the planned byte ranges are
  exactly what the download asks for. Measured against real inventories:

  | Product | Planned transfer before | After | Dropped |
  | --- | --- | --- | --- |
  | HRRR `wrfprs` F024 | 295.5 MB | 226.3 MB | 40 redundant pressure-level `SPFH` messages (`RH` is preferred) |
  | ECMWF IFS F036 | 99.9 MB | 91.2 MB | 14 redundant pressure-level `q` messages (`r` is preferred) |

  All 40 HRRR pressure levels are retained for all seven needed fields, and no
  pressure-level `z` is pulled for ECMWF. Verified end to end across all 13
  selectable products: identical level counts and identical surface rows to
  before the change, with field provenance now populated (13 fields for IFS, 12
  for AIFS, 10 for NOAA products) so cached subsets are reusable.

### Changed

- **Products that cannot produce a sounding are no longer offered.** RRFS-A
  (CONUS, Alaska, Hawaii, Puerto Rico) and AIGFS were selectable in the picker
  and the CLI but could only ever end in a verified-surface refusal, because no
  file they publish carries a complete ground row:

  | Withheld | Measured cause |
  | --- | --- |
  | `rrfs-a` and its three domains | The `prslev` index has 675 records, all pressure levels - no `:surface:`, `:2 m above ground:`, or `:10 m above ground:` entries - and `natlev`, `testbed`, and `ififip` return no GRIB. |
  | `aigfs` | The `pres` product has no ground fields, and the companion `sfc` product publishes only four messages: 2-m temperature, 10-m U/V, and mean-sea-level pressure. Surface pressure, terrain height, and 2-m moisture are absent from both. |

  `ModelConfig` now carries an `unavailable_reason`, and `available_models()`
  returns only products that can produce a sounding, which is the single list
  the picker and `--list` both read. The configs stay registered so keys,
  aliases, domains, and archived provenance still resolve, and `--list` reports
  them under "Known but not enabled" with the reason. Asking for one explicitly
  now fails immediately with that reason instead of a generic contract refusal.
  Restoring either product is a one-field change if NOAA publishes the missing
  fields. The remaining thirteen products were each confirmed end to end to
  return a sounding with a merged verified surface row.
- Centralized each test worker's `QApplication`, made explicitly elevated
  Hypothesis example counts respect the ten-example compatibility profile, and
  moved coverage/property compatibility duplication out of CI while preserving
  the complete scientific and release checks.
- Defaulted new hodograph displays to LCL-to-EL mean-wind centering with a
  20%-tighter 160-kt viewport, while preserving user-selected Normal and Storm
  Relative modes across profile and geometry updates.
- Re-rendered the README example sounding and added Inverted (light) and
  Protanopia (colorblind) palette screenshots. Both palette screenshots use the
  bundled OAX 2014-06-16 19Z observed sounding, deliberately a different profile
  from the dark HRRR example, so the palette change is visible independently of
  the data. All three are produced through the shipped `render()` pipeline with
  the persisted `color_style` preference, not by overriding palette internals.
  MEASURED mean luminance: dark `0.050`, Inverted `0.946`, Protanopia `0.051`,
  and Protanopia differs from a Standard render of the same sounding.

## [0.7.0] - 2026-07-30

### Added

- Added a cached Python/Rust SB/MU/100-hPa-ML parcel workspace with CAPE/CIN,
  LCL/LFC/EL, and 3/6-km CAPE summaries.
- Added the API 6 traced convective workspace for forecast, surface, MU, ML,
  and effective parcels, explicit Skew-T user-parcel ascents, and DCAPE with
  Python-oracle fallback.
- Added a coarse-grained Python/Rust profile-kinematics backend that computes
  and caches standard layer shear, mean wind, storm-relative wind, SRH, and
  Bunkers motion while retaining Python fallback for nonstandard layers.
- Added strict, lightweight validation for cached portable-sounding NPZ/JSON
  pairs, including array-shape, metadata, archive-size, and member-count
  checks.
- Added separate fast, full-property, and opt-in live-provider CI lanes with
  bounded test runtimes, branch coverage, focused Ruff checks, and dependency
  vulnerability auditing.
- Added a weekly live HRRR CONUS terrain matrix spanning the Pacific Northwest,
  Intermountain West, Rockies, central Plains, Southeast coast, and Northeast.
  It verifies that the current provider schema yields each point's local model
  surface and removes below-ground isobars rather than special-casing Denver.
- Added an explainable recent-cycle surface-contract probe for provider
  monitoring, including a failing CLI mode that lists missing ground fields.
- Added exact release constraints and release-workflow contract tests for
  source-SHA, action pinning, permissions, and artifact-only publication.

### Changed

- Reuse the standard parcel workspace across eligible SharpTab composite
  indices and cache failed full-convective-oracle construction, avoiding
  repeated Python parcel ascents for one immutable `Profile`.
- Upgrade decoded GUI/render collections to an accelerated convective profile
  that preserves SHARPpy's public objects while removing repeated Python parcel
  and DCAPE integrations.
- Revalidate cached provider mirrors before reuse and key provider decisions
  by the exact source set instead of retaining stale process-wide choices.
- Gate releases on the exact source commit that passed the reusable test
  workflow, build from constrained dependencies, and limit write permission to
  the artifact-only publish job.
- Fetch ECCC surface fields first and skip every pressure-level layer at or
  below the selected point's ground pressure, reducing unnecessary GeoMet
  point requests.
- Namespace persistent forecast caches by contract version; older payloads
  remain inspectable but cannot be reused as current soundings.

### Fixed

- Build ERA5 and every forecast profile from a verified model surface: fetch
  surface pressure/height, 2-m temperature/moisture, and 10-m wind; remove
  every below-ground isobar; and fail closed when a provider does not publish
  the complete ground contract.
- Decode one-point NOMADS subsets reliably when ecCodes cannot select an exact
  nearest grid point from the cropped payload.
- Decode CFS pressure and surface fields on their independently published
  regular and Gaussian grids, including surface dewpoint derived from specific
  humidity.
- Reject physically inconsistent extracted and cached profiles, including
  non-monotonic pressure/height and dewpoint warmer than temperature, before
  they can be written or reused.
- Promote unrecognized test warnings to errors, guard project-owned non-finite
  kinematic arithmetic, opt cfgrib into xarray's new merge defaults, and
  narrowly contain documented warnings from pinned scientific dependencies.
- Keep sparse soundings on the reference 0–6 km CAPE integral instead of
  applying the dense model-profile native shortcut across kilometre-scale gaps.
- Recover expired or orphaned model-cache leases so abandoned entries can be
  pruned.
- Report cache entries as removed only after deletion actually succeeds, and
  ignore malformed or incomplete portable-sounding pairs during cache reuse.
- Stop every picker-owned Qt worker before application teardown, including
  advisory station and model-availability probes that may be blocked in a
  network library.
- Keep the map, forecast-model, ERA5, and raw-WRF control rails usable at the
  picker's minimum size by scrolling them vertically instead of compressing
  their controls.
- Refit native-size sounding windows after realization so menu-bar height does
  not create unnecessary scroll bars or clip the bottom analysis row.
- Treat Windows' `SystemError` wrapper for an invalid `os.kill(pid, 0)` probe
  as a stale model-cache lease instead of failing GUI startup.

## [0.6.0] - 2026-07-28

### Added

- Added compact colored 0.5, 1, 3, 6, 9, and 12 km AGL dots with
  solid black numbers inside the marker and extra edge clearance for the
  `0.5` label, plus optional location/town
  entry and a cached background town lookup for the locator-map title,
  including coordinate-only files rendered outside the picker. A reproducible
  52,818-entry Census place/town index provides offline CONUS title coverage
  without drawing town names inside the map. Separately tiled and compressed
  Census county outlines keep the local locator useful without network I/O.
- Added visible, synchronized multi-sounding controls to the ERA5 and raw WRF
  tabs so repeated point/time selections can be overlaid in one analysis
  window.
- Added an annual Census-index freshness workflow that rebuilds the newest
  official CONUS place, state-boundary, and county-outline data and publishes
  changed generated resources for review.
- Added a hash-pinned SHARPpy compatibility installer that verifies the
  official wheel and its `RECORD`, corrects only its obsolete NumPy dependency
  metadata, records provenance, and requires `pip check` to pass.

### Changed

- Bounded supported source runtimes to Python 3.11–3.13, added Python 3.13 and
  Windows WRF CI coverage, kept comprehensive testing on pull requests instead
  of duplicating it after merge on `main`, and increased the full-suite timeout
  to match measured runtime.
- Reduced expected third-party warning noise while retaining unrecognized
  deprecations and updated the local ECAPE path to MetPy's current
  two-argument specific-humidity API.

### Fixed

- Prevented Herbie Unicode status glyphs from raising `UnicodeEncodeError` on
  redirected CP1252 Windows consoles.
- Replaced RDPS's misleading nearly-global WMS envelope with its operational
  rotated-grid acceptance test and curved picker-map outline.
- Added a file-based Windows clipboard helper for external automation, avoiding
  fragile inline `pwsh -Command` parsing of .NET object construction.
- Replaced Windows-incompatible `grep` instructions with `rg`/ripgrep usage and
  installation guidance.
- Restored raw WRF support in Windows releases by installing and bundling
  `netCDF4`/`cftime`, explicitly selecting a NetCDF4-capable xarray engine, and
  rejecting misleading SciPy-only NetCDF3 setups.
- Disabled pickle loading for user and cache NPZ files, added bounded archive
  and profile validation, and preserved adjacent JSON coordinates/model
  metadata for SPC and other non-NPZ files.
- Added timeouts, response limits, explicit resource closure, and one-download
  decoder dispatch for remote sounding URLs.
- Made town resolution strictly CONUS-only with bundled Census state polygons,
  offline-first lookup, settlement-only online parsing, and expiring positive,
  offline, failure, and negative cache entries.
- Kept coordinate-only recent points eligible for automatic town resolution
  when they are reused from the Forecast, ERA5, or raw-WRF source controls.
- Kept town names only in the locator title, replaced synchronous county
  requests during hodograph painting with bounded local Census tiles, and
  prevented height dots from colliding with one another or being covered by
  the locator inset.
- Painted hodograph height dots on the live, scale-aware widget layer so their
  small black numerals stay crisp in HD/UHD exports instead of being enlarged
  from the one-pixel-density hodograph bitmap cache.
- Rebuilt all cached sounding panels at the requested HD/UHD pixel density so
  titles, axes, parameter tables, insets, and scientific annotations render
  with crisp antialiased text instead of being smoothly enlarged from 1x
  backing pixmaps.
- Centered the hodograph LCL-EL mean-wind square and gave its measured value
  an edge-aware gap, kept height dots clear of vector annotations, and jointly
  packed the near-surface labels from every displayed sounding so their masks
  and numbers cannot overlap.
- Replaced silent locator/height-overlay failures with diagnostic logging and
  made GUI and headless file opens share the same town-title resolution path.

## [0.5.0] - 2026-07-22

### Added

- Added a non-blocking **Reanalysis (ERA5)** GUI workflow with a global point
  map, hourly UTC selection, snapped-grid preview, focused CDS setup errors,
  point/hour caching, cooperative cancellation, and viewer-scoped outputs.
- Added guided raw `wrfout*` extraction under **Open File**, including
  background domain/time inspection, curvilinear grid-edge validation, map
  selection, progress/cancellation, and viewer-scoped output cleanup.
- Added RRFS-A Alaska, Hawaii, and Puerto Rico point-sounding adapters plus a
  provider capability contract for domains, cycles, forecast hours, members,
  fields, levels, archive status, and supported transports.
- Added real Canadian GDPS and RDPS point adapters through ECCC MSC GeoMet,
  with exact run/valid-time checks, bounded layer fan-out, normalized 33-level
  soundings, spatial cache identity, provider capabilities, and CLI/GUI use.
- Added forecast-hour timeline queues with streaming partial results and
  slider, step, play, and loop controls in the sounding viewer.
- Added a downloaded-data library for validating, reopening/re-extracting,
  pinning, deleting, and copying provenance from persistent model-cache entries.
- Added searchable saved and recent points with versioned JSON import/export
  and forecast/ERA5 map markers.
- Added a focused-profile source, quality, and provenance inspector with
  provider/transport, decoder/backend, cache, level/missing-field, vorticity,
  and non-mutating QC details.
- Added resumable multi-point/multi-hour batch extraction with shared model-hour
  downloads, bounded concurrency, atomic outputs, and checksummed manifests.
- Added provider-neutral observed-sounding retrieval with explicit UWyo to IEM
  RAOB fallback in both the GUI and CLI.

### Performance

- Generalized bounded parallel HTTP-range retrieval from RRFS to every indexed
  Herbie model. Large coalesced spans are balanced across up to four workers by
  default, with per-worker sessions, ETag/Last-Modified identity pinning,
  ordered atomic assembly, resumable fragments, cancellation, progress
  aggregation, bounded transient retries, and an automatic sequential-range
  fallback. This does not change decoder parallelism.
- Added official NOAA geographic-subset routes for NAM 3 km CONUS, HRW
  WRF-ARW, HRW FV3, and CFS alongside the existing HRRR, RAP, NAM, GFS, and
  GEFS routes.
- Vectorized multi-point GRIB element reads, added direct four-neighbor wind
  stencils for products without vorticity fields, and removed HRRR Zarr's
  intermediate xarray point construction. Unsupported layouts retain the
  existing compatibility fallbacks.

### Fixed

- Preserved the cancellation signal during raw WRF extraction after the input
  dataset has opened, so cancelled GUI requests do not surface as failures.

## [0.4.2] - 2026-07-18

### Fixed

- Fixed forced-Rust startup and backend-test failures caused by a stale or
  mismatched locally installed `sharpmod_rs` extension. The new synchronization
  command detects version drift, rebuilds the locked native extension when
  needed, and verifies Rust selection in a fresh Python process.
- Fixed the Windows Qt `CO_E_CANTCALLOUT_ININPUTSYNCCALL` COM-apartment warning
  seen during GUI event-dispatch tests by selecting Qt's headless platform
  before any GUI test module is imported.

### Added

- Added `sharpmod-rust-sync` to check, rebuild, and verify the native Rust
  extension for editable source installations.

### Changed

- Moved frozen Windows executable validation out of pull-request CI. Pull
  requests retain Python, Rust, NumPy, and wheel coverage; executable builds
  and runtime checks now run only in the release workflow after merge.
- Defaulted the shared pytest Qt platform to `offscreen` before GUI modules are
  collected, preventing native test windows and collection-order failures.
- Ignored local sounding render and issue-working directories.

## [0.4.1] - 2026-07-16

### Changed

- Updated the Rust backend's `libloading` dependency from 0.8.9 to 0.9.0 and
  raised the source-build minimum supported Rust version from 1.86 to 1.88.
  Official binaries still bundle the native extension, and Python-fallback
  installations still do not require a Rust toolchain.

## [0.4.0] - 2026-07-16

### Added

- Added independently optimized Rust and Python backends for profile-array
  operations and direct pressure-level GRIB point decoding. Rust is the
  supported primary backend in official v0.4 binaries and whenever `auto`
  validates a compatible extension. Python remains the fully functional,
  portable fallback, while explicit modes expose compatibility failures instead
  of silently changing implementations.
- Added an all-model decoder matrix covering all 13 enabled forecast products:
  HRRR, RAP, NAM, NAM 3 km, HRW WRF-ARW, HRW FV3, RRFS-A, GFS, AIGFS, CFS,
  ECMWF IFS, ECMWF-AIFS, and GEFS.
- Added reproducible old-Python-versus-optimized-Python,
  old-hybrid-versus-optimized-Rust, and optimized-Python-versus-optimized-Rust
  benchmark drivers. Dated Markdown and JSON results under
  `benchmarks/results` retain fixture hashes, raw samples, selected grid
  coordinates, build fingerprints, unavailable stages, and equivalence output.
- Added cross-backend tests for selected point, pressure order, deduplication,
  missing masks, metadata, and scientific values within appropriate
  floating-point tolerances, including generated multi-field GRIB fixtures.

### Performance

- Replaced repeated Python cfgrib scans and full-grid xarray construction on
  compatible products with a direct ecCodes point decoder. It scans headers
  once per file identity, reuses the inventory and nearest-point selection,
  reads only required field/level scalars, and keeps bounded inventory,
  nearest-point, and exact decoded-sounding caches.
- Kept cfgrib as a functional compatibility path while reusing persistent
  indexes. Direct-decoder failures reduce split groups to a small point
  neighborhood before merge; products that require the established
  neighbor-wind vorticity calculation retain their full xarray merge.
- Added a Rust decoder that memory-maps the local GRIB subset, borrows message
  storage through ecCodes, releases the GIL during the immutable decode, and
  returns all sounding columns as one contiguous NumPy-compatible matrix in a
  single Python/Rust boundary crossing.
- Avoided speculative decoder parallelism. Profiling did not show a benefit for
  ordinary point soundings, so calls made by the Rust decoder remain serialized
  and the implementation focuses on fewer allocations, copies, and calls.
- Reused the existing model inventory for field planning and transport, passed
  cache-owned local GRIB subsets directly into the selected decoder, and kept
  source, run, valid-time, selected-point, field, unit, and lifecycle metadata
  unchanged through profile construction.

#### All-model decoder benchmark

Network transfer is excluded. Times are application-cold medians;
application caches and cfgrib indexes are cleared, while the operating-system
file cache is not flushed.

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

Across all 13 fixtures, the geometric-mean speedups are **2.61x for Python**
and **2.65x for Rust**. Optimized Python divided by optimized Rust is 1.082x,
so optimized Rust has about 7.6% lower latency overall. The matrix's NAM row
contains an isolated system-wide timing stall; the separate five-repeat
[NAM confirmation](benchmarks/results/2026-07-16-nam-decoding-v0.4.0-windows-amd64.json)
measured 3.36x for Python and 4.04x for Rust.

`Old Rust hybrid` is the frozen historical cfgrib/xarray algorithm followed by
native wind post-processing; the old extension did not decode GRIB. See the
[complete benchmark report](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.md)
and [raw JSON record](benchmarks/results/2026-07-16-all-model-decoding-windows-amd64.json)
for fixture hashes, raw samples, equivalence results, and environment details.

### Fixed

- Decoded every logical field in packed GRIB multi-field messages, preserving
  both U and V winds when they share one physical byte offset. This fixes the
  layouts used by products including RAP, NAM, NAM 3 km, HRW WRF-ARW, HRW FV3,
  and CFS in both Python and Rust.
- Preserved every published pressure level while applying stable descending
  pressure sorting and aligned deduplication consistently across all columns.
- Aligned scalar-pressure variables to their actual pressure before xarray
  merging. In particular, a GEFS omega value published only at 850 hPa remains
  missing at all other levels instead of being broadcast or indexed as a full
  vertical column.
- Applied preference changes to the complete mounted sounding widget tree.
  Switching to **Inverted** now updates the Skew-T, hodograph, locator, storm
  slinky, insets, IndexBoard, and Streamwiseness panels immediately instead of
  leaving custom surfaces in the dark palette.
- Made inverted/light colors readable without changing scientific values or
  established dark palettes. Theme-dependent text and semantic annotations use
  a complete contrast-checked role palette, and headless rendering now honors
  the same selected inverted colors as the GUI.

#### Sounding palette previews

Both previews use the same checked-in HRRR profile, selected parcel, and viewer
state so only the palette changes.

##### Inverted / light mode

![SHARPpy Reimagined sounding in inverted light mode](docs/images/v0.4.0/sounding-light-mode.png)

##### Protanopia colorblind mode

![SHARPpy Reimagined sounding in the Protanopia colorblind palette](docs/images/v0.4.0/sounding-protanopia.png)

### Compatibility and limitations

- Products without a usable published relative- or absolute-vorticity field
  retain the xarray wind-gradient fallback in production so derived
  vorticity behavior is preserved, even though their direct point decoder is
  measured by the benchmark matrix.
- Optimized Python and Rust return matching pressure-aligned omega values and
  missing masks within the cross-backend tolerances. The GEFS cross-generation
  report's `12 -> 1 valid` difference is the intentional correction of the
  frozen legacy xarray scalar-pressure broadcast, not missing Rust
  functionality or a new scientific-value discrepancy.
- Rust is the supported primary backend in official v0.4 binaries and in
  `auto` mode whenever its versioned contract validates. Optimized Python
  remains the fully functional portable fallback for source installs and
  platforms without a compatible native extension; GUI and CLI behavior do not
  change when fallback is required.

## [0.3.1] - 2026-07-15

### Fixed

- ERA5 point extraction now accepts scalar latitude/longitude coordinates from
  zero-area CDS responses and does not mistake a snapped singleton coordinate
  for the source dataset's geographic coverage.
- ECAPE's NCAPE calculation now evaluates only through the equilibrium level,
  avoiding invalid saturation calculations in unused upper-stratospheric
  levels.
- Windows builds now analyze from the actual repository root so the local
  `sharpmod` package is embedded, and frozen runtime verification checks
  `logging.handlers` and the GUI entrypoint in both build formats.

## [0.3.0] - 2026-07-14

### Added features

- Portable `.sharpmod-session` analysis sessions that save and restore every
  sounding in a viewer, the active profile/time/member, profile and storm-motion
  edits, interpolation state, parcel selection, and supported viewer state.
  Sessions use validated, versioned JSON and never embed source GRIB downloads.
- Fifty-step undo/redo history for mouse and numeric profile edits,
  interpolation and reset actions, and storm-motion changes. Use `Ctrl+Z` and
  `Ctrl+Y`, or the new Edit menu actions.
- Availability-aware forecast-model selection. The picker checks the selected
  model, run, forecast hour, and member in the background and offers an explicit
  **Use available cycle** action when a newer selection has not been published.
- Multi-sounding analysis windows with profile focus/removal controls and a
  remembered option to add newly opened soundings to the active viewer.
- A validated **Edit Nearest Level** dialog for pressure, height, temperature,
  dewpoint, wind direction, and wind speed, with immediate recalculation of
  derived displays.
- Maximum Parcel Level (MPL) values in the parcel table and Skew-T level labels.
- Persistent GUI preferences for units, colors, readouts, default parcel,
  multi-sounding behavior, dismissed tips, recent files, and picker selections.
- Worldwide coast, country, and state/province outlines in the hodograph locator
  while retaining the detailed U.S. county overlay.
- Three-hourly observed-sounding selection from 00Z through 21Z on both the
  station map and station list, including special/asynoptic launch times.
- Accelerated forecast retrieval with a direct HRRR analysis-Zarr point path,
  NOAA NOMADS geographic subsetting where supported, adaptive coalesced HTTP
  ranges for other indexed providers, and automatic fallback to Herbie's
  standard downloader.
- A persistent, bounded forecast-model cache plus optional **Prefetch Next
  Forecast Hour** and **Clear Downloaded Model Cache** File-menu actions.

### UX improvements

- Removed calendar dates from sounding titles while retaining compact run and
  valid UTC hours, forecast hour, and coordinates.
- Model availability checks are debounced and ignore stale worker results.
  Unknown or transient probe failures leave manual Fetch available, and the
  selected run is never changed without user confirmation.
- Added forecast-download stage and byte progress reporting, actionable
  GRIB-runtime errors, and a Help action that opens the rotating GUI diagnostic
  log folder.
- Reuses a decoded full-GRIB forecast hour when another point is requested from
  the same model, run, forecast hour, and member. Point/subregion downloads are
  cached by coordinate so data from one location cannot be reused for another.
- Added a dedicated Cancel button for forecast retrieval. Compatible range
  downloads retain verified partial fragments so an interrupted request can
  resume instead of restarting every byte.
- The fast path automatically races equivalent Herbie mirrors, remembers the
  quickest healthy provider for six hours, and reports the chosen transport
  and fields in the point sounding's metadata.
- Small indexed subsets use direct ranges instead of paying the NOMADS CGI
  preparation cost; geographic NOMADS cropping is reserved for transfers above
  32 MiB or inventories whose size cannot be determined safely.
- Optional next-hour prefetch is disabled by default, runs independently of the
  active fetch, and never opens a viewer or replaces the user's selection.
- Kept custom-panel numeric values readable by measuring and drawing compact
  unit suffixes before applying overflow elision.

### Bug fixes

- Replaced the removed Herbie `era5` model path with official Copernicus CDS
  pressure-level retrieval, including all 37 levels, point-sized requests,
  temporary-GRIB cleanup, and actionable CDS credential guidance.
- Restored forecast-model availability checks and downloads on Windows Python
  3.14 by loading the ecCodes DLL bundled in its pure-Python wheel when a
  version-specific `_eccodes` helper wheel is unavailable.
- Moved automatic model-availability GRIB validation onto the main Qt thread
  before starting a probe worker.
- Prevented native Windows GUI startup crashes under Python 3.14 by handing
  source-checkout launches to the project's Python 3.11-3.13 environment before
  `QApplication` starts. Packaged releases already bundle Python 3.11.
- Prevented duplicate concurrent requests for the same model hour with
  single-flight cache loading, while allowing different hours to proceed
  independently.
- Included the point location in persistent cache identity so two locations can
  never reuse the wrong extracted sounding, and protected in-use entries from
  age/size pruning.
- Validated HTTP partial-content responses, entity tags, file size, and GRIB
  boundaries before publishing resumed or coalesced downloads.
- Handled RAP inventories that expose packed U/V wind messages at a shared byte
  offset, preventing the optimized range path from unnecessarily falling back
  to a full Herbie download.

### Code improvements

- Replaced the monolithic `sharpmod.gui` implementation with a small compatible
  facade and focused picker, settings, fetch-worker, map, session, viewer, and
  shared-runtime modules.
- Moved renderer monkeypatch installation into one ordered registry that checks
  for the supported SHARPpy version before applying any patches.
- Analysis-session files contain decoded sounding state only, preserving the
  existing cleanup lifecycle for temporary forecast-model data.
- Added a bounded, lease-aware model-hour cache that owns shared GRIB data
  independently from viewer-scoped point files and safely closes decoded
  datasets on eviction or application shutdown.
- User-facing package, window, and renderer version labels now read from
  `sharpmod._version.__version__` as the single source of truth.
- Added focused field planning that selects one available humidity field and
  one available vertical-motion field while retaining every pressure level
  published by the selected model.
- Split accelerated retrieval into field-planning, range-transport, provider,
  disk-cache, and HRRR-Zarr modules with cancellation and fallback boundaries
  that can be tested independently.

### Packaging and project maintenance

- Added `cdsapi` to the ERA5 optional dependency set and bundled the CDS/ECMWF
  runtime modules in standalone GUI builds.
- Expanded the frozen-launcher dependency check to verify that CDS retrieval is
  available before the GUI starts.
- Bundled and runtime-checked `numcodecs` and `pyproj`, which are required by
  the HRRR Zarr point backend.
- Stopped tracking generated `sharpmod.egg-info` directories, standardized text
  line endings with `.gitattributes` and `.editorconfig`, and archived the stale
  outer wrapper and artifacts so the repository has one obvious project root.
- Excluded AI-agent plans and metadata, the internal engineering backlog,
  legacy `attic` prototypes, local analysis sessions, credentials, logs, and
  scratch files from consumer-facing source releases.

### Tests and documentation

- Added regression coverage for analysis sessions, edit history, model
  availability, GUI module boundaries, settings persistence, multi-sounding
  behavior, level editing, MPL display, patch registration, and versioning.
- Added ERA5 CDS request, missing-credential, forecast-model registry, and frozen
  packaging regressions so removed or unbundled data-provider integrations fail
  during testing instead of at runtime.
- Added retrieval regressions for field pruning, adaptive range coalescing,
  resume validation, provider selection, persistent cache pruning, single-flight
  loading, HRRR Zarr decoding, cancellation, and background prefetch.
- Updated the README, usage guide, and installation notes with CDS account,
  dataset-licence, `.cdsapirc`, and optional-dependency setup instructions.
