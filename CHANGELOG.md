# Changelog

All notable changes to SHARPpy Reimagined are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
