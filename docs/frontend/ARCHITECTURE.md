# Frontend architecture

The desktop frontend of SHARPpy Reimagined is a Tauri 2 application. The legacy
PySide6 interface is retired from this source tree and remains available in
the [v2.0.0 release](https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0).
The current frontend is a React 19 + TypeScript (strict) +
Vite 8 single-page app in `frontend/`, a Rust application bridge in
`src-tauri/`, and a headless Python package, `sharpmod/bridge/`, that serves
semantic operations from the existing `sharpmod` code.

Related documents: [DATA_CONTRACTS.md](DATA_CONTRACTS.md) (commands, DTOs,
errors, the bridge protocol), [MAP_RENDERING.md](MAP_RENDERING.md) (MapLibre,
deck.gl, layers, graphics profiles), [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)
(layout, themes, and tokens), [MIGRATION.md](MIGRATION.md) (phase status), and
[AUDIT.md](AUDIT.md) (the Phase 0 audit), and
[performance/PERFORMANCE_AUDIT.md](../performance/PERFORMANCE_AUDIT.md) (the
current Rust-first execution map, measurements and migration gates).

The current shell uses the SHARPpy backend. Its workspaces are Overview,
Model Viewer, Live Tracking, Soundings, Logs,
and Settings. Analysis, Compare, and Ensemble are removed; ensemble models
are excluded centrally from UI catalogues. The scientific Python tools and
headless Qt renderer remain shared backend components. See [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

## Boundary and ownership

**The backend produces meteorological data. The frontend produces pixels.**

| Concern | Owner | Where |
|---|---|---|
| Sounding, parcel, CAPE/CIN, kinematics, SRH, shear, storm motion, severe parameters | Python `sharpmod` and `sharpmod-rs` | `sharpmod/sharptab/`, `sharpmod/analysis/`, `sharpmod/backends/`, `rust/sharpmod-rs/` |
| Model decoding, GRIB, providers, retrieval, QC, interpolation | Python `sharpmod` | `sharpmod/models/`, `sharpmod/providers/`, `sharpmod/io/` |
| The station catalogue | Python `sharpmod` | `sharpmod/io/uwyo_catalog.py` (`all_stations()`), read from `sharpmod/resources/uwyo_stations.json` |
| Process supervision, operation routing, DTO conversion, IPC | Rust app bridge | `src-tauri/src/` (no numerical code) |
| Maps, layers, colours, hover, selection, tooltips, panels, layout, keyboard | Frontend | `frontend/src/` |
| Basemap geometry | Frontend build | `frontend/tooling/basemapPlugin.ts` converts `sharpmod/resources/basemap.json` into GeoJSON assets |

Rules, each enforced by a check:

- No meteorological computation in TypeScript. Formatting only (coordinates,
  labels, names, and the backend's values at the backend's decimals).
  `frontend/scripts/check-boundaries.mjs` scans `frontend/src` for science
  terms; its allowlist has one entry, the visible control label "Parcel" in
  `features/sounding/AscentPicker.tsx`. Sounding quantities reach the frontend
  as labelled tables and groups, so no TypeScript names a quantity.
- Components never call Tauri. Only `frontend/src/lib/backend/tauriTransport.ts`
  imports `@tauri-apps/api` (ESLint `no-restricted-imports` and
  `check-boundaries.mjs` rule 3).
- `src-tauri` does not link `rust/sharpmod-rs` and duplicates no numerics.
  The scientific backend status comes from Python `backend_info()`
  (`sharpmod/backends/selector.py`).
- Frontend-specific Python glue lives in `sharpmod/bridge/` (plus the fixture exporters
  `scripts/export_frontend_fixtures.py`, `scripts/export_sounding_fixtures.py`,
  and `scripts/export_map_fixtures.py`). Shared decoder and renderer fixes
  belong in their existing modules and retain their regression checks.
- Map fields travel as numbers and are coloured in the frontend from
  sharpmod's palettes; Python never draws a map image for the new app.
- The sounding image is `sharpmod.rendering.cli.render` output, never a
  frontend drawing.

## Process model

```text
React components
  | hooks: useBackendHealth(), useStationCatalog()   (TanStack Query)
  v
BackendClient            frontend/src/lib/backend/client.ts   (parses every reply)
  v
Transport                tauriTransport.ts (desktop) | httpTransport.ts (live browser) | fixtureTransport.ts (tests)
  | invoke / listen
  v
Tauri commands + events  src-tauri/src/commands/, src-tauri/src/events/
  v
Router                   src-tauri/src/backend/router.rs
  +--> Rust-native       app info, bridge status
  +--> cache             the station and map catalogues and place names after their first load
  +--> Python bridge     src-tauri/src/python/supervisor/  --stdio JSON lines-->  python -m sharpmod.bridge
                                                                                   (calls sharpmod functions)
                                                                                     | stdio JSON lines
                                                                                     v
                                                                   python -m sharpmod.bridge.render_worker
                                                                   (Qt offscreen: rendering.cli.render)
```

**The render worker.** Qt widgets must live on a process's main thread, and
the bridge stays Qt-free, so the bridge starts one long-lived child,
`sharpmod/bridge/render_worker.py`, on the first image request
(`sharpmod/bridge/render_client.py`). The worker imports the renderer once and
draws one image at a time with `cli.render(infile, outfile, parcel,
image_mode)`, so successive image requests are serialized. A render that
passes 120 s (plus 60 s for the worker's start) stops the worker; the next
request starts a new one. The worker exits with the bridge: its stdin closes,
and on Windows it is inside the bridge's kill-on-close Job Object. The serial
worker retains one pristine decoded input and independently calculated science,
deep-copies it for each request, and invalidates it on source/sidecar/science
changes. Qt widgets and output images are not shared through that input cache.
After each successful or failed job, the serial worker collects unreachable
Python cycles so deleted Qt wrappers release their native chart buffers before
the reply. Active prepared science remains referenced. The patch's exact-output
and native-memory evidence is in
[V3_2_1_PERFORMANCE_REPORT.md](../performance/V3_2_1_PERFORMANCE_REPORT.md).
The ordinary unprepared renderer remains the reference and fallback. Current
measurements, exact-output checks and the historical cold/warm timing limits
are recorded in [BENCHMARK_RESULTS.md](../performance/BENCHMARK_RESULTS.md);
the older 18 s / 7-8 s figures are not a current performance guarantee.

**The sounding cache.** Opened soundings live in the bridge: a 24-entry LRU
(`sharpmod/bridge/sounding_cache.py`) holding each decoded collection, its
computed parameters, and its rendered PNGs. The files go to a private
directory, `tempfile.mkdtemp(prefix="sharpmod-bridge-")` (under
`SHARPMOD_BRIDGE_CACHE` when that is set), which the bridge deletes when it
exits. Forecast-model GRIBs and their metadata stay here too. The desktop
launcher gives each backend process its own parent cache and removes it after
that process exits, including when shutdown has to stop a busy backend.
A sounding id from before a bridge restart gets `SOUNDING_NOT_FOUND`.
An additional session-owned source-hour cache retains one idle model dataset,
leases active sources and separates model/run/hour/member/spatial identity.
Its source downloads appear in Storage and are cleaned with the session;
point-only providers and HRRR Zarr analyses retain their dedicated paths.

The transport is chosen by Vite mode in `frontend/src/main.tsx`: `--mode live`
(`npm run dev:browser`, or `npm run build:browser` then `npm run preview:browser`)
starts the project's Python interpreter when serving and uses Vite's
same-origin `/__backend` proxy to `sharpmod.bridge.browser` on a private loopback port.
It calls the same live handlers as the desktop bridge, including binary map frames,
and polls backend health using the existing fallback. The interpreter follows
`SHARPPY_PYTHON`, then `.gribenv`, then `.venv`. This is a local development server;
restart Vite to restart Python (`restart_backend` is desktop-only).

`--mode fixture` (`npm run dev:fixture`, `npm run build:fixture`, Playwright) serves the
deterministic fixtures in `src-tauri/contracts/` with `?scenario=` states;
modes other than `live` and `fixture` use Tauri. Fixture code and the `window.__SHARPPY_E2E__`
hooks never reach `frontend/dist` or `frontend/dist-browser`. The layer panel labels
fixture weather as test data. Live layers use the requested time, location, and
product only; a different selection clears the previous layer while loading.
SPC outlooks outside their validity window are not drawn.

## Operation router

`src-tauri/src/backend/router.rs` maps each semantic operation to a provider.
The frontend never learns which one answered.

| Operation | Phase 1 provider |
|---|---|
| App information (name, version, Tauri version, build profile, log file) | Rust-native, read once at startup |
| Bridge status | Rust-native (supervisor state); never waits on Python |
| Python facts (version, executable, sharpmod version) | The supervisor's cached `hello` reply |
| Scientific backend (`backend_info()`) | Python bridge `health`, only while the bridge is `up` |
| Station catalogue | In-memory `Arc<StationCatalog>` cache, else Python bridge `stations.catalog`; `restart_backend` clears it |
| Forecast-model list | In-memory cache for 10 minutes, else Python bridge `models.catalog` |
| Open a sounding, its parameters, its Trends, its image | Python bridge `soundings.*` (`src-tauri/src/backend/sounding_ops.rs`); the image is returned as raw PNG bytes |
| Map catalogue, place names | In-memory cache for the session, else Python bridge `map.catalog`, `places.labels` (`src-tauri/src/backend/map_ops.rs`) |
| Field grid, SPC outlook, radar mosaic, station availability | Python bridge `fields.grid`, `outlooks.spc`, `radar.mosaic`, `stations.availability`; Rust turns grids and radar images into `SHRF` frames |

**Moving an operation from Python to Rust.** Implement it in Rust behind the
router (for a numerical kernel, through `sharpmod-rs`: AUDIT §7 option 3, a
default `python` feature gating PyO3 so `src-tauri` can depend on it with
`default-features = false`), switch that operation's provider in
`router.rs`, and keep the command name and DTO. The frontend, its fixtures,
and its tests do not change.

## Python sidecar

| Topic | Behaviour | Code |
|---|---|---|
| Start | `setup` in `src-tauri/src/lib.rs` starts the supervisor in the background; the window never waits for Python | `lib.rs` |
| Interpreter | `SHARPPY_PYTHON` (absolute path to an existing file); else the packaged `backend/sharppy-backend.exe` in release builds; else, in debug builds only, the repository `.gribenv` then `.venv`; else `PYTHON_NOT_FOUND`. Never `.griblenv`, never a `PATH` search | `src-tauri/src/python/interpreter.rs` |
| Command line | The packaged backend receives `--desktop-bridge`; an explicit Python interpreter receives `-I -u -X utf8 -m sharpmod.bridge`. Arguments use an array without a shell; working directory is the app local data directory | `src-tauri/src/python/launcher.rs` |
| Windows | `CREATE_NO_WINDOW`; a kill-on-close Job Object; the bridge also exits when stdin reaches EOF | `src-tauri/src/python/job_windows.rs`, `sharpmod/bridge/server.py` |
| States | `starting` → `up` → `restarting` → `unavailable`; each transition emits `bridge-status-changed` | `src-tauri/src/python/supervisor/state.rs`, `transitions.rs` |
| Timeouts | spawn + `hello` 15 s, `health` 20 s, `stations.catalog` 15 s, liveness ping 3 s, shutdown 2 s; models 30 s, open observed 90 s, open model 300 s, open file 60 s, parameters 60 s, Trends 180 s, image 200 s; map catalogue 30 s, field 120 s, outlook 60 s, radar 60 s, places 30 s, availability 35 s | `SupervisorConfig` in `src-tauri/src/python/supervisor/mod.rs`; `src-tauri/src/backend/sounding_ops.rs`, `map_ops.rs` |
| Concurrency | eight worker threads; `hello` is answered on the read loop, so a liveness ping never waits behind a long download; per sounding, one lock for the collection and its results and another for images | `sharpmod/bridge/server.py`, `sounding_cache.py` |
| Restarts | backoff 0.5, 1, 2, 4, 8, 16, then 30 s; reset after 60 s `up`; the fifth crash in 60 s gives `BRIDGE_UNAVAILABLE` until Restart backend | `transitions.rs`, `control.rs` |
| Shutdown | `shutdown` request on exit, 2 s grace, then kill | `control.rs`, `lib.rs` run-event loop |
| Stream safety | the bridge moves the protocol to private descriptors before importing handlers, so stray output lands on stderr | `sharpmod/bridge/server.py` |

The app still starts, draws the basemap, and reports a clear state when
Python is missing: the health indicator says "Backend unavailable" and the
station notice gives the `SHARPPY_PYTHON` instruction.

**Developer log.** `tauri-plugin-log` writes
`%LOCALAPPDATA%\io.github.shianmike.sharppy-reimagined\logs\sharppy-reimagined.log`
(5 MB per file, three kept, UTC), plus stdout in debug builds. Python stderr,
including tracebacks, goes there under target `python`; the UI only ever shows
structured errors. The path is shown in the health popover.

## Folder trees

What exists now (generated output, `node_modules`, and lockfiles omitted):

```text
frontend/
  index.html  package.json  vite.config.ts  eslint.config.js  tsconfig*.json
  playwright.config.ts  playwright.desktop.config.ts
  scripts/        tauri.mjs (CLI wrapper), check-boundaries.mjs
  tooling/        basemap.ts, basemapPlugin.ts, csp.ts
  e2e/            browser specs, support/, desktop/ (measure, soundings, map)
  src/
    app/          Shell, overlay slots, workspaces, shortcuts
    components/   GlassSurface, Popover, Button, IconButton, Notice, Slider, Switch, ...
    features/     toolbar (with the forecast controls), rail, search, inspector
                  (station and forecast point), settings, timeline, forecast/
                  (useForecast), errors, fallback, placeholders,
                  sounding/ (source forms, open list, image, ascent picker),
                  operations/ (overview and data status)
    map/          WeatherMap.tsx, mapQueries, panelField, panelScene, usePlayback,
                  runtime/ (MapRuntime, cameraLink, overlay), layers/, field/
                  (colourize), panels/ (MapPanel), basemap/, labels/, hover/,
                  notices/, controls/ (layer panel, legend, availability)
    stations/     stationQueries, stationIndex, stationSearch, stationFormat, availability
    stores/       appStore, mapStore, forecastStore, graphicsStore, soundingStore, uiStore, preferences
    lib/          backend/, contracts/, graphics/, metrics.ts, cx.ts
    types/        contracts.ts, graphics.ts, soundings.ts, map.ts
    styles/       tokens.css, glass.css, base.css
    test/         test helpers
src-tauri/
  Cargo.toml  Cargo.lock  build.rs  tauri.conf.json  capabilities/main.json  icons/
  contracts/      shared fixtures (bridge/, public/, each with soundings/ and map/)
  src/
    commands/     backend.rs, stations.rs, soundings.rs, map.rs
    backend/      router.rs, convert.rs, sounding_ops.rs, map_ops.rs
    python/       interpreter.rs, launcher.rs, job_windows.rs, protocol.rs, supervisor/
    dto/          health.rs, stations.rs, models.rs, soundings.rs, analysis.rs,
                  map.rs, overlays.rs, checks.rs
    state/  events/  error.rs  lib.rs  main.rs
  tests/          contracts.rs, supervisor.rs, commands.rs, python_sidecar.rs
sharpmod/bridge/  __main__.py, server.py, protocol.py, handlers.py, errors.py,
                  params.py, compat.py, soundings.py, sounding_open.py,
                  sounding_summary.py, sounding_analysis.py, sounding_cache.py,
                  render_client.py, render_worker.py, fields.py, map_layers.py
```

The REQUEST §4 target tree also names `src-tauri/src/commands/` modules such
as `fields.rs`, `outlooks.rs`, `radar.rs`, and `settings.rs`. The map
commands share one module, `commands/map.rs`, and there is no settings
command. Map layers not built yet are listed in
[MAP_RENDERING.md](MAP_RENDERING.md#not-built).

## State ownership

Each value lives in exactly one place. Zustand stores use
`subscribeWithSelector`, so the map runtime subscribes outside React.

| Store | Owns | Code |
|---|---|---|
| App | active workspace, `selectedStationId` or `selectedPoint` (the forecast point; never both), `pendingStationId` (restored, not yet confirmed) | `frontend/src/stores/appStore.ts` |
| Map | the first panel's layer visibility, opacity, and product; the other panels' (`panels`); the layout, the active panel, the weather layer order, the radar product, the availability release, layer-panel collapse, camera snapshot (persistence only), `focusRequest` | `frontend/src/stores/mapStore.ts` |
| Forecast | model, run, forecast hour, and whether playback runs; session only | `frontend/src/stores/forecastStore.ts` |
| Graphics | probe results, override, effective profile and reasons, renderer state, `mapAttempt` | `frontend/src/stores/graphicsStore.ts` |
| Hover (map-local) | the hovered station, the hovered outlook area, and the pointer position | `frontend/src/map/hover/hoverStore.ts` |
| Map notices (map-local) | failed and slow basemap sources, runtime error count | `frontend/src/map/notices/mapNoticeStore.ts` |
| Soundings | the open soundings (at most 12, newest first), the active one, the ascent choice, the source tab, and each form's inputs; session only, not persisted | `frontend/src/stores/soundingStore.ts` |
| Server state | `["backend", "health"]`, `["stations", "catalog"]`, the model list, per sounding its parameters, Trends, and one image per ascent (images leave memory five minutes after their last view); the map catalogue, place names, field frames (trimmed to the profile's CPU budget), outlooks, the radar frame, and availability reports | TanStack Query, `frontend/src/lib/backend/queryOptions.ts`, `features/sounding/soundingQueries.ts`, `map/mapQueries.ts` |
| Preferences | `localStorage["sharppy.prefs.v1"]`, validated field by field | `frontend/src/stores/preferences.ts` |

- **Catalogue states** (loading, empty, seed fallback, error) have one owner,
  `frontend/src/features/errors/CatalogueStatusNotice.tsx`. The map portals it
  into the centre overlay slot above the map notices; the reduced-rendering
  view renders it above the station list.
- **Workspace switches keep map state.** The map and its overlay stay mounted
  when another workspace opens: the overlay gets the `hidden` attribute (made
  binding by the `[hidden]` reset in `frontend/src/styles/base.css`), so the
  layer panel, inspector, and selection persist and reappear unchanged on Map.
  Automatic forecast playback preparation and prefetch run only while Model
  Viewer is active. Leaving it releases that effect's query owners and aborts
  obsolete preparation; other live owners and explicit bulk downloads retain
  their existing lifetime.
- The live camera stays inside MapLibre; nothing re-renders per frame. With
  two or four panels, `map/runtime/cameraLink.ts` keeps the cameras together.
- Preferences are keyed to the app identifier and origin; changing the
  identifier resets them.

## Keyboard map

| Key | Action |
|---|---|
| Tab | toolbar (with Model, Run, and Forecast hour), rail, map canvases, map toolbar, map notice action, inspector, layer panel, timeline (DOM order) |
| Arrow keys, + and − | pan and zoom while a map canvas has focus (MapLibre); on the hour rail, the arrow keys step the forecast hour |
| ArrowUp/ArrowDown, Home/End, Enter/Space | move and activate within the rail (one Tab stop, roving tabindex) |
| Ctrl+K, or `/` outside text fields | focus station search |
| ArrowUp/ArrowDown, Enter | move through and choose search results; Enter with no active option picks the first |
| Esc | closes the innermost popover; in search, the second Esc clears the text; otherwise Esc clears the selection (a station or the forecast point) |

Health popover: the indicator is a `<button aria-haspopup="dialog"
aria-expanded>`; the popover is a non-modal `role="dialog"` labelled "Backend
status". Opening it focuses its first button, or the dialog itself. Esc, or
Tab past its last element (Shift+Tab before its first), closes it and returns
focus to the indicator; an outside click closes it without moving focus. The
`Popover` primitive (`frontend/src/components/Popover.tsx`) provides this for
every popover.

## Running the interface

The [user guide](../USAGE.md) documents the v3 navigation, model panels,
sounding imports, profile editing, and settings. The
[README](../../README.md#run-v3-from-source) gives the Windows source setup.

Development requires Python 3.11–3.13 with the verified SHARPpy render stack,
Node.js 24.12 or newer, a stable Rust toolchain, and Tauri's platform build
prerequisites. Windows desktop development needs the MSVC C++ build tools and
WebView2. The scientific extension supports Rust 1.88 or newer; the Tauri
desktop uses the current stable toolchain.

From `frontend/`, after `npm ci`:

```powershell
npm run tauri -- dev
```

The wrapper starts the CLI from the repository root and serves Vite on port
5173. Debug builds discover the repository's `.gribenv`, then `.venv`.
Set `SHARPPY_PYTHON` to an absolute interpreter path to override discovery.
Release builds use the bundled `backend/sharppy-backend.exe` unless that
explicit override is set.

For a browser preview, use `npm run dev:browser` and open
`http://127.0.0.1:5174/`. Its local Python bridge calls the real providers.
Use `npm run dev:fixture` for deterministic sample data instead. Only one
preview can occupy that port at a time.

## Run, test, and build

All frontend and Tauri commands run in `frontend/`; Rust in `src-tauri/`; Python
from the repo root with the repo venv.

| Purpose | Command |
|---|---|
| Install | `npm ci` |
| Browser development (live providers) | `npm run dev:browser`, then http://127.0.0.1:5174; uses the configured Python environment |
| Offline browser development (fixture data) | `npm run dev:fixture`, then http://127.0.0.1:5174 |
| Desktop development | `npm run tauri -- dev` (starts Vite on 5173) |
| Desktop debug build, no installer | `npm run tauri -- build --debug --no-bundle`; output `src-tauri/target/debug/sharppy-reimagined.exe`, or under `CARGO_TARGET_DIR` |
| Typecheck, lint and boundaries, unit tests | `npm run typecheck`, `npm run lint`, `npm test` |
| Web builds | `npm run build` (desktop assets, `dist/`), `npm run build:browser` (live, `dist-browser/`) |
| Live browser preview | `npm run build:browser`, then `npm run preview:browser` |
| Browser end-to-end (installed Edge) | `npm run build:fixture`, then `npm run e2e` |
| Desktop measurements | with the debug app running and WebView2 remote debugging on 9222: `SHARPPY_DESKTOP_CDP=http://127.0.0.1:9222 npm run e2e:desktop -- --grep "@load\|@frames"` |
| Real-backend sounding checks (network) | the same, with `--grep @soundings`: an observed sounding at 72357, an HRRR sounding there, the example BUFKIT file, all through the UI |
| Real-backend map checks (network) | the same, with `--grep @map`: HRRR fields, the next and a prefetched hour, SPC outlooks, the radar mosaic, UWyo availability, a forecast point, and four panels, with timings |
| Rust | `cargo fmt --check`; `cargo clippy --locked --all-targets -- -D warnings`; `cargo test --locked` (add `-- --include-ignored` for the real-sidecar test) |
| Bridge tests | `python -m pytest sharpmod/tests/test_bridge_protocol.py sharpmod/tests/test_bridge_contract.py sharpmod/tests/test_bridge_process.py sharpmod/tests/test_bridge_soundings.py sharpmod/tests/test_bridge_map.py` (offline: the providers and the render worker are stand-ins; the real worker and the real services run in the desktop `@soundings` and `@map` checks) |
| Regenerate golden fixtures | `python scripts/export_frontend_fixtures.py`; sounding fixtures: `python scripts/export_sounding_fixtures.py`; map fixtures: `python scripts/export_map_fixtures.py` |

Unit tests use isolated thread workers, capped at eight and bounded by available
CPUs. Catalogue loading and retry tests advance a fake clock rather than sleep;
they still check the real 1 s and 2 s retry schedule. This follows the
[Vitest performance guidance](https://main.vitest.dev/guide/improving-performance)
without sharing test environments. On 2026-10-15, the same 604 tests passed in
25.8 s versus 28.6 s before tuning; the catalogue tests fell from 5.3 s to 0.6 s.
These are local measurements, not CI thresholds. JSON reports are saved under
`frontend/test-results/unit-{baseline,optimized}.json`.

`npm run tauri` runs `frontend/scripts/tauri.mjs`, which starts the Tauri CLI
from the repo root with `TAURI_APP_PATH=src-tauri` and
`TAURI_FRONTEND_PATH=frontend`, so the two projects stay siblings with no root
manifest. `.github/workflows/frontend.yml` runs the frontend gates and the Rust
gates on `windows-latest` when `frontend/`, `src-tauri/`, or the basemap
changes.

## Security posture

- **CSP** (`src-tauri/tauri.conf.json`, also served by `vite preview` through
  `frontend/tooling/csp.ts`): `default-src 'self'`; scripts and styles `'self'`
  only; `connect-src 'self' ipc: http://ipc.localhost`; `worker-src` and
  `child-src 'self' blob:` for the MapLibre worker; `img-src 'self' data:
  blob:`; `object-src 'none'`; `form-action` and `frame-ancestors 'none'`.
- **Capability** (`src-tauri/capabilities/main.json`): the allowlisted app commands
  plus `core:event:allow-listen` and `core:event:allow-unlisten`. No
  `core:default`, no shell, fs, http, dialog, or log permission. A sounding
  file is read in the webview from the file the user picks and sent as
  base64 (at most 5 MiB). Raw WRF import is a separate operation: the user
  supplies an absolute local dataset path, which the backend validates,
  inspects, and reads without modifying the source.
- **Sidecar:** fixed argv with `-I`, no `PATH` search, a method allowlist, an
  8 MiB frame cap, closed error codes, validated parameters on both sides. The
  render worker has the same fixed argv, no shell, and is only ever given files
  in the bridge's private cache directory.
- **Cancellation and admission:** forecast queries and model opens forward a
  request token through `cancel_request` to the Python worker. Stop is
  cooperative; shared preloads continue while another consumer needs them.
  The bridge admits at most 24 ordinary requests across its eight workers and
  queue. Hello, progress and cancellation bypass that admission limit.
  Native error details retain safe request tokens, wire IDs, methods and child
  generations. Explicit non-retryable cancellation is logged as an expected
  operation; genuine failures, timeouts, late errors and unknown replies remain
  visible. Field range failures stop only their own sibling/queued transfers.
  The shared field HTTP adapter retains up to 32 reusable connections for the
  existing eight-by-four request concurrency; it does not add workers or block
  cancellation on pool admission.
- **Network:** the desktop has no runtime HTTP server or telemetry. Basemap
  tiles and their styles/fonts may load from the CSP-allowed OpenFreeMap host;
  bundled basemap geometry is also available. Meteorological provider requests
  run in Python: when the user opens a sounding, the
  observation providers (UWyo, IEM, IGRA) and the model data sources of
  `tools.model_extract`, plus the renderer's reverse town lookup
  (`sharpmod/maps/place_names.py`), which uses its bundled CONUS index first;
  when a map layer is shown, HRRR GRIB data from NOAA's open data on AWS, SPC
  outlooks, the MRMS mosaic through NCEP GeoServer, and the UWyo station list
  for a chosen release.
  Automatic and manual update checks and installer downloads run through the native Tauri
  updater against GitHub Releases.
  WebView2 remote debugging is enabled only through an environment variable in
  a measurement shell, never in the repo.

## Windows update signing

The shell checks for updates on launch, hourly, and on reconnection. It shows
an Update now / Later notice, with Later suppressing that version for the
session. Automatic check failures are logged without an alert; installation
always requires a click. Settings → Updates uses the same store and official
Tauri updater. The public verification key
and HTTPS GitHub release endpoint live in `src-tauri/tauri.conf.json`. Release
CI requires the encrypted repository secret `TAURI_SIGNING_PRIVATE_KEY` and
stages the installer, its `.exe.sig`, and `latest.json` before checksums and
provenance. The manifest points to the renamed Windows installer in that exact
release. A failed check is never reported as “up to date”; signature verification
must succeed before installation can start.

For each future update, publish a stable GitHub Release with a higher app version
(for example, 3.0.1 to 3.0.2) through the existing release workflow. CI generates
and attaches the feed and signed installer together; uploading an EXE alone does
not provide an update feed. Drafts and prereleases do not use this stable channel.

Keep the private key outside the repository and back it up securely. Replacing
the configured public key prevents existing installations from accepting future
updates signed only by the replacement key. Locally, signing uses the Tauri
`TAURI_SIGNING_PRIVATE_KEY` environment variable; never commit a private key.

## Open items

The 3.2.1 follow-up gives every map runtime a startup/recovery watch. Only
panel 0 owns the aggregate startup/basemap state; secondary failures remain
local notices and recover after a successful render. Image decode failures
are part of panel status, retain a correctly dated fallback only for the same
source, and can retry without reloading unrelated queries. Missing required
decoded fields fail with FIELD_FETCH_FAILED at the shared bridge boundary.

Observed station defaults follow explicit selection actions in appStore,
rather than form mount effects. The UI persist adapter catches storage access
failures once and keeps the serialized session value in memory. Multi-time
file summaries optionally include all times, preserving older summary shapes;
the selected index travels inside the existing reversible view request. View
history keys include the time, and closing a collection checks every time's
local work. The original collection/file is never edited.

CI now provisions Python for live browser boundary checks, explicitly runs
sidecar cleanup tests, and audits production npm/Cargo graphs. Release staging
requires the updater library to verify the actual installer and signed version,
followed by a silent installed upgrade in a disposable runner account. The
runtime CycloneDX graph accompanies artifacts/checksums; its metadata lists
unidentified native/system components rather than claiming complete binary
coverage. Actual signed-candidate and interactive upgrade acceptance remain
release gates, not results inferred from source checks.

- **Windows distribution validation.** The release workflow builds a frozen
  Python backend, bundles it beside the Tauri application, and requires the
  packaged model-fetch and sounding-render diagnostics before staging the
  installer and portable ZIP. A passing source build alone is insufficient.
- **macOS and Linux.** Not built or verified; POSIX code paths compile but are
  untested, and the macOS overlay title bar is not used.
- **Scientific Rust integration.** The Python process loads `sharpmod_rs`;
  the Tauri crate does not link or duplicate those numerical kernels.
- **Sounding ids after a bridge restart.** The open list keeps its entries,
  but their parameters and new images fail with `SOUNDING_NOT_FOUND` ("Open it
  again"); the frontend does not reopen them by itself.
