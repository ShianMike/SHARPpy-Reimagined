# Data contracts

How data and errors cross the two boundaries of the new app. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the process model.

## Two contracts

| Contract | Between | Casing | Stability | Defined in |
|---|---|---|---|---|
| Bridge protocol | Python `sharpmod.bridge` and `src-tauri/src/python/` | snake_case, sharpmod's own keys (`lat`, `lon`, `src`) | Internal, versioned by `protocol_version` | `src-tauri/src/python/protocol.rs`, `sharpmod/bridge/handlers.py`, `src-tauri/contracts/bridge/` |
| Public DTOs | Tauri commands and events, and `frontend/src/lib/` | camelCase | Stable: additive changes, or a migration note here | `src-tauri/src/dto/`, `frontend/src/types/contracts.ts`, `src-tauri/contracts/public/` |

Field names change in exactly one place: `src-tauri/src/backend/convert.rs`.
Both sides use serde `deny_unknown_fields`, and the TypeScript parsers reject
unknown keys too, so drift fails a test instead of passing silently.

## Commands and events

| Name | Kind | Arguments | Returns | Errors | Permission |
|---|---|---|---|---|---|
| `get_backend_health` | command, `src-tauri/src/commands/backend.rs` | none | `BackendHealth` | `INTERNAL` only; bridge problems are reported inside the DTO | `allow-get-backend-health` |
| `get_station_catalog` | command, `src-tauri/src/commands/stations.rs` | none | `StationCatalog` | `PYTHON_NOT_FOUND`, `BRIDGE_SPAWN_FAILED`, `BRIDGE_TIMEOUT`, `BRIDGE_CRASHED`, `BRIDGE_UNAVAILABLE`, `BRIDGE_PROTOCOL`, `METHOD_NOT_FOUND`, `CATALOG_UNAVAILABLE`, `INTERNAL` | `allow-get-station-catalog` |
| `restart_backend` | command, `commands/backend.rs` | none | `BridgeStatus` after the attempt | `INTERNAL` only | `allow-restart-backend` |
| `bridge-status-changed` | event, Rust to the `main` window, `src-tauri/src/events/mod.rs` | n/a | `BridgeStatus` | n/a | `core:event:allow-listen`, `core:event:allow-unlisten` |
| `get_model_catalog` | command, `src-tauri/src/commands/soundings.rs` | none | `ModelCatalog` (cached 10 min) | the bridge codes, `INTERNAL` | `allow-get-model-catalog` |
| `open_sounding` | command, `commands/soundings.rs` | `request: OpenSoundingRequest` | `SoundingSummary` | `INVALID_ARGUMENT`, `SOUNDING_UNAVAILABLE`, `SOUNDING_FETCH_FAILED`, `SOUNDING_INVALID`, the bridge codes, `INTERNAL` | `allow-open-sounding` |
| `inspect_wrf` | command, `commands/soundings.rs` | `filePath` (absolute local path) | `WrfFile` (path, name, UTC times, domain centre) | `INVALID_ARGUMENT`, `SOUNDING_INVALID`, the bridge codes, `INTERNAL` | `allow-inspect-wrf` |
| `get_sounding_analysis` | command, `commands/soundings.rs` | `soundingId` | `SoundingAnalysis` | `INVALID_ARGUMENT`, `SOUNDING_NOT_FOUND`, `SOUNDING_INVALID`, the bridge codes, `INTERNAL` | `allow-get-sounding-analysis` |
| `get_sounding_trends` | command, `commands/soundings.rs` | `soundingId` | `SoundingTrends` | `INVALID_ARGUMENT`, `SOUNDING_NOT_FOUND`, the bridge codes, `INTERNAL` | `allow-get-sounding-trends` |
| `get_ensemble_summary` | command, `commands/soundings.rs` | `model`, `latitude`, `longitude`, `forecastHour` (0 to 1000), `runTime?`, `location?`, `members` (2 to 32 labels) | `EnsembleSummary` (the acquisition ledger and the labelled spreads) | `INVALID_ARGUMENT`, `SOUNDING_FETCH_FAILED` (no member could be retrieved), the bridge codes, `INTERNAL` | `allow-get-ensemble-summary` |
| `get_threshold_metrics` | command, `commands/soundings.rs` | none | `ThresholdMetrics` (every registry diagnostic with its control bounds, the comparison rules in order, and the starting diagnostics) | the bridge codes, `INTERNAL` | `allow-get-threshold-metrics` |
| `evaluate_threshold` | command, `commands/soundings.rs` | the `get_ensemble_summary` fields plus `definition: ThresholdDefinitionInput` (a name and 1 to 3 conditions) | `ThresholdEvaluation` (the classification counts with their denominators and one row per member) | `INVALID_ARGUMENT`, `SOUNDING_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-evaluate-threshold` |
| `export_threshold_csv` | command, `commands/soundings.rs` | the `evaluate_threshold` arguments | `ThresholdCsv` (`fileName`, `csv` — the rows `export_threshold_csv` writes) | `INVALID_ARGUMENT`, `SOUNDING_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-export-threshold-csv` |
| `render_sounding` | command, `commands/soundings.rs` | `soundingId`, `ascent?: "SFC" \| "ML" \| "FCST" \| "MU" \| "EFF"` (MU), `imageMode?: "hd" \| "uhd"` (hd) | raw PNG bytes (`tauri::ipc::Response`; an `ArrayBuffer` in JavaScript) | `INVALID_ARGUMENT`, `SOUNDING_NOT_FOUND`, `RENDER_FAILED`, the bridge codes, `INTERNAL` | `allow-render-sounding` |
| `get_map_catalog` | command, `src-tauri/src/commands/map.rs` | optional `model` (HRRR by default) | `MapCatalog` for the model | the bridge codes, `INTERNAL` | `allow-get-map-catalog` |
| `get_weather_field` | command, `commands/map.rs` | `product`, `runTime`, `forecastHour` (0 to 84), optional `model` (HRRR by default) | an `SHRF` field frame ([Binary frames](#binary-frames)) | `INVALID_ARGUMENT`, `FIELD_UNAVAILABLE`, `FIELD_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-weather-field` |
| `get_spc_outlook` | command, `commands/map.rs` | `product` (`cat`, `torn`, `wind`, `hail`), `validTime` | `SpcOutlook` | `INVALID_ARGUMENT`, `OVERLAY_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-spc-outlook` |
| `get_radar_frame` | command, `commands/map.rs` | `product?` (composite reflectivity) | an `SHRF` radar frame holding the service's PNG | `INVALID_ARGUMENT`, `OVERLAY_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-radar-frame` |
| `get_radar_site_frame` | command, `commands/map.rs` | `product?` (base reflectivity), `site?` (a WSR-88D id) or `latitude` + `longitude` | an `SHRF` `radar-site` frame holding the service's PNG plus the serving `siteId` | `INVALID_ARGUMENT` (an unknown site or a site that does not publish the product), `OVERLAY_FETCH_FAILED` (which is not retryable when no antenna is in range), the bridge codes, `INTERNAL` | `allow-get-radar-site-frame` |
| `get_radar_sites` | command, `commands/map.rs` | none | `RadarSiteCatalog` (cached for the session) | the bridge codes, `INTERNAL` | `allow-get-radar-sites` |
| `get_satellite_frame` | command, `commands/map.rs` | `validTime`, `channel?` (`visible`, `infrared`) | an `SHRF` `satellite` frame holding the rendered PNG, with the scan's provenance | `INVALID_ARGUMENT`, `OVERLAY_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-satellite-frame` |
| `get_storm_reports` | command, `commands/map.rs` | `validTime`, `windowS?` (21600), `hazardFilter?` (`all`, `tornado`, `hail`, `wind`, `significant`) | `StormReports` (typed point records with the provider's marker spec) | `INVALID_ARGUMENT`, `OVERLAY_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-storm-reports` |
| `get_report_options` | command, `commands/map.rs` | none | `ReportOptions` (the provider's window and filter labels; cached for the session) | the bridge codes, `INTERNAL` | `allow-get-report-options` |
| `get_field_contours` | command, `commands/map.rs` | `product`, `runTime`, `forecastHour` (0 to 84), optional `model` (HRRR by default) | `ContourLines` (lon/lat segments at each multiple of the product's interval) | `INVALID_ARGUMENT` (a product with no contour lines included), `FIELD_UNAVAILABLE`, `FIELD_FETCH_FAILED` (which is not retryable when a level set is too dense to draw), the bridge codes, `INTERNAL` | `allow-get-field-contours` |
| `get_surface_plots` | command, `commands/map.rs` | `latitude`, `longitude`, `validTime` | `SurfacePlots` (typed point records with the display slots and the wind-staff mark spec) | `INVALID_ARGUMENT`, `OVERLAY_FETCH_FAILED`, the bridge codes, `INTERNAL` | `allow-get-surface-plots` |
| `get_place_labels` | command, `commands/map.rs` | none | `PlaceLabels` (cached for the session) | the bridge codes, `INTERNAL` | `allow-get-place-labels` |
| `get_station_availability` | command, `commands/map.rs` | `observationTime` | `AvailabilityReport` | `INVALID_ARGUMENT`, `STATION_LIST_UNAVAILABLE`, the bridge codes, `INTERNAL` | `allow-get-station-availability` |

"The bridge codes" are `PYTHON_NOT_FOUND`, `BRIDGE_SPAWN_FAILED`,
`BRIDGE_TIMEOUT`, `BRIDGE_CRASHED`, `BRIDGE_UNAVAILABLE`, `BRIDGE_PROTOCOL`, and
`METHOD_NOT_FOUND`.

The frontend sees these only as `BackendClient` methods
(`frontend/src/lib/backend/client.ts`): `getBackendHealth()`,
`getStationCatalog()`, `restartBackend()`, `onBridgeStatus(handler)`,
`getModelCatalog()`, `openSounding(request)`, `getSoundingAnalysis(id)`,
`getSoundingTrends(id)`, `renderSounding(id, ascent, imageMode)` (which checks
the PNG signature and resolves with a `Blob`), `getMapCatalog()`,
`getWeatherField(product, runTime, forecastHour)`,
`getSpcOutlook(product, validTime)`, `getRadarFrame(product)`,
`getRadarSiteFrame({ site } | { latitude, longitude }, product?)` (which
parses the `radar-site` frame and its serving `siteId`),
`getRadarSites()`, `getSatelliteFrame(validTime, channel)` (which parses the
`satellite` frame and its scan provenance),
`getStormReports(validTime, windowS, hazardFilter)` and `getReportOptions()`,
`getFieldContours(product, runTime, forecastHour)`,
`getPlaceLabels()`, and `getStationAvailability(observationTime)`.
(`get_available_models` and `get_model_runs` became `get_model_catalog`, which
lists recent runs; `analyze_profile` became `get_sounding_analysis`.)

## DTOs

### Local sounding imports

`open_sounding` accepts `kind: "file"` with `fileName` and
`contentBase64`. Both boundaries enforce a decoded size of at most 6 MiB;
failed decoding removes the temporary upload. NPZ, SPC, BUFKIT, PECAN, and
UWyo text or HTML use the existing decoder registry.

Raw WRF datasets stay on disk. `inspect_wrf(filePath)` maps to
`soundings.inspect_wrf({file_path})` and returns `WrfFile` with `filePath`,
`fileName`, `times`, `latitude`, and `longitude`. Inspection supplies
the forecast-time menu and initial domain-centre point.
`open_sounding({kind: "wrf", filePath, time, latitude, longitude})` maps to
`soundings.open_wrf`. The backend requires an existing absolute file path,
a time present in the dataset, and a point inside its grid perimeter. It calls
the existing WRF extractor and registers the extracted NPZ as a local
`kind: "file"` sounding with model `WRF-ARW`. Extraction and decoding
failures remove their temporary NPZ and metadata; the source remains unchanged.


### `BackendHealth`

```ts
export interface BackendHealth {
  app: AppInfo;
  bridge: BridgeStatus;
  python: PythonInfo | null;                          // null unless bridge.status === "up"
  scientificBackend: ScientificBackendStatus | null;  // null unless bridge.status === "up"
  checkedAt: string;                                  // RFC 3339 UTC, whole seconds
}
export interface AppInfo {
  name: string; version: string; tauriVersion: string;
  buildProfile: "debug" | "release";
  logFile: string;                                    // absolute path of the developer log
}
export type BridgeStatus =
  | { status: "starting"; since: string }
  | { status: "up"; protocolVersion: 1; pid: number; since: string; restarts: number }
  | { status: "restarting"; attempt: number; retryInMs: number; lastError: BackendError }
  | { status: "unavailable"; error: BackendError };
export interface PythonInfo {
  version: string;
  executable: string | null;                          // debug builds only
  sharpmodVersion: string;
}
export type ScientificBackendStatus =
  | { status: "ok"; requestedBackend: "auto" | "python" | "rust"; activeBackend: "python" | "rust";
      rustInstalled: boolean; rustVersion: string | null; fallbackReason: string | null }
  | { status: "unavailable"; code: "BACKEND_UNAVAILABLE" | "BACKEND_CONFIG_INVALID"; message: string }
  | { status: "unknown"; error: BackendError };       // the bridge did not answer the health query
```

- `pid` is the interpreter's own id from `hello`, not the venv redirector's.
- `restarts` counts how many times the bridge reached `up` this session,
  minus one: 0 at the first `up`, +1 at every later `up` (automatic or Restart
  backend). Failed spawns do not count, and it never resets. The health
  popover shows "Backend restarts this session: N" when N > 0.
- `attempt` is 1 for the first restart and resets once a child stays `up` 60 s.
- Every timestamp comes from `dto::rfc3339_utc_now()` (`src-tauri/src/dto/mod.rs`),
  for example `2026-10-04T01:02:05Z`.

Example (`src-tauri/contracts/public/health.ok.json`):

```json
{
  "app": {"name": "SHARPpy Reimagined", "version": "3.0.0", "tauriVersion": "2.12.1", "buildProfile": "debug",
          "logFile": "C:\\Users\\example\\AppData\\Local\\io.github.shianmike.sharppy-reimagined\\logs\\sharppy-reimagined.log"},
  "bridge": {"status": "up", "protocolVersion": 1, "pid": 4242, "since": "2026-10-04T01:02:03Z", "restarts": 0},
  "python": {"version": "3.11.14", "executable": "C:\\repo\\.gribenv\\Scripts\\python.exe", "sharpmodVersion": "3.0.0"},
  "scientificBackend": {"status": "ok", "requestedBackend": "auto", "activeBackend": "rust", "rustInstalled": true,
                        "rustVersion": "3.0.0", "fallbackReason": null},
  "checkedAt": "2026-10-04T01:02:05Z"
}
```

Other shapes are in the same folder: `health.python-fallback.json` (no Rust
wheel, `activeBackend: "python"`), `health.python-unavailable.json` (bridge
`unavailable` with `PYTHON_NOT_FOUND`), `health.backend-unavailable.json`,
`health.restarting.json`, and `health.unknown.json`.

Indicator labels (`frontend/src/features/toolbar/healthStatus.ts`): `up` + `ok`
"Backend ready"; `up` + `unavailable` or `unknown` "Backend degraded";
`starting` "Starting backend"; `restarting` "Restarting backend"; `unavailable`
"Backend unavailable"; no answer yet "Checking backend"; a failed health call
"Backend status unknown".

### `StationCatalog`

```ts
export type StationAvailability = "unknown" | "listed" | "notListed";
export interface Station {
  id: string;          // WMO index, 1 to 16 characters
  name: string;        // UWyo free text, up to 200 characters; "" for 28 bundled records
  latitude: number;    // finite, -90 to 90
  longitude: number;   // finite, -180 to 180
  source: string;      // "FM35", "BUFR", "FM37", or "UNKNOWN"; 1 to 16 characters
  availability: StationAvailability;
}
export type AvailabilityBasis =
  | { kind: "catalogOnly" }                          // Phase 1
  | { kind: "listedAt"; observationTime: string };   // Phase 2
export interface StationCatalog {
  stations: Station[];       // sorted by id, ids unique
  origin: "bundled" | "seedFallback";
  availabilityBasis: AvailabilityBasis;
  droppedCount: number;      // records rejected by the record rule
}
```

```json
{"stations": [{"id": "72357", "name": "Norman/Max Westheimer A, Ok., Usa", "latitude": 35.18095,
               "longitude": -97.43787, "source": "FM35", "availability": "unknown"}],
 "origin": "bundled", "availabilityBasis": {"kind": "catalogOnly"}, "droppedCount": 0}
```

- **Source.** `uwyo_catalog.all_stations()`, the call the legacy picker makes
  (`sharpmod/gui_picker.py:490`). The bridge never reads the JSON file itself.
- **Availability.** REQUEST §6's `available: boolean` is replaced: the bundled
  catalogue has no availability flag, so every station is `"unknown"` with
  `availabilityBasis.kind: "catalogOnly"`. `true` is never fabricated.
- **Record rule** (bridge first, then `convert.rs` again): id 1 to 16
  characters after trimming, finite in-range coordinates, name at most 200,
  source 1 to 16; a repeated id keeps the first. A rejected record is logged and
  counted in `droppedCount`; nothing is sent as `null` or `NaN`. The bundled file
  passes entirely: 933 records.
- **Seed fallback.** `load_catalog()` silently falls back to the 10
  `SEED_STATIONS`. The bridge reports `origin: "seed_fallback"` exactly when the
  records equal that list, and the UI shows a warning.
- **Size.** 933 stations are 120,741 bytes of compact JSON in the public DTO
  and about 84 KB on the bridge; Rust logs the size once per load.

### Soundings and forecast models

The TypeScript types are in `frontend/src/types/soundings.ts`, the Rust ones
in `src-tauri/src/dto/{models,soundings,analysis}.rs`, and examples in
`src-tauri/contracts/public/soundings/`.

```ts
export type OpenSoundingRequest =
  | { kind: "observed"; stationId: string; time: string;          // RFC 3339 UTC
      provider: "auto" | "uwyo" | "iem" | "igra" }
  | { kind: "model"; model: string; runTime: string | null;       // null: the model's latest
      forecastHour: number; latitude: number; longitude: number; location: string | null }
  | { kind: "file"; fileName: string; contentBase64: string };    // at most 6 MiB of data
export interface SoundingSummary {
  soundingId: string;                     // the bridge cache key, 16 hex characters
  kind: "observed" | "model" | "file";
  title: string;                          // the bridge's display title, e.g. "HRRR 72357"
  location, stationId, stationName: string | null;
  latitude, longitude: number | null;
  validTime, runTime: string | null;      // RFC 3339 UTC
  forecastHour: number | null;
  model, provider, providerName, sourceUrl, fileName: string | null;
  levelCount: number;                     // levels of the first time
  timeCount: number;                      // valid times in the file (BUFKIT: many)
}
export interface DataTable {              // a table sharpmod labels: title, columns, rows
  title: string;
  columns: { key: string; label: string; units: string; decimals: number }[];
  rows: { key: string; label: string; short: string; values: (number | null)[] }[];
}
export interface SoundingAnalysis {
  soundingId: string; validTime: string | null;
  watchType: string | null;               // prof.right_watch_type, the image's "Psbl Haz. Type"
  hazard: string;                         // sharptab.hazard.classify
  parcelTable: DataTable;                 // the five lifted parcels
  motionTable: DataTable;                 // the Bunkers right and left movers
  groups: { name: string; parameters: { key: string; label: string; units: string;
            decimals: number; notable: "high" | "low"; value: number | null }[] }[];
}
export interface SoundingTrends {         // ProfileMetricsEngine.timeline(DEFAULT_METRIC_KEYS)
  soundingId: string; title: string;
  columns: DataTable["columns"];
  points: { time: string | null; values: (number | null)[]; note: string | null }[];
  truncated: boolean;
}
export interface ModelCatalog {
  models: { key: string; label: string; domain: string;
            bounds: { west: number; east: number; south: number; north: number };
            outline: [number, number][];  // [lon, lat], the grid perimeter
            cycles: number[]; forecastHours: number[]; defaultForecastHour: number;
            gridSpacingKm: number | null; notes: string;
            members: string[];            // the ensemble member labels, control first
            recentRuns: string[];         // newest first
            suggestedRun: string }[];     // the newest run likely published
  generatedAt: string;
}
export interface EnsembleSummary {        // ProfileMetricsEngine.ensemble(DEFAULT_METRIC_KEYS)
  validTime: string | null; reason: string | null;
  requestedMembers: string[]; loadedMembers: string[];
  failures: { member: string; status: "failed" | "cancelled" | "unknown";
              reason: string | null }[];  // the acquisition ledger
  metrics: { key: string; label: string; units: string; decimals: number;
             count: number;               // its own usable denominator
             p10: number | null; median: number | null; p90: number | null;
             minimum: number | null; maximum: number | null }[];
  samples: { member: string;
             outcome: "completed" | "failed" | "cancelled" | "unavailable";
             reason: string | null; values: (number | null)[] }[];  // one row per requested member
}
export interface ThresholdMetrics {     // the registry + metric_controls.control_for
  metrics: { key: string; label: string; units: string; displayLabel: string;
             decimals: number;          // the form's bounds, step, and starting value
             minimum: number; maximum: number; step: number; suggested: number;
             guidance: string }[];
  operators: string[];                  // the six comparison rules, in control order
  defaultMetrics: string[];             // the diagnostics the form starts with
}
export interface ThresholdEvaluation {  // ensemble_thresholds.evaluate_threshold
  definition: { name: string;
                conditions: { metric: string; operator: string; value: number;
                              upper: number | null; units: string }[] };
  definitionSummary: string;            // the rule, as a reader states it
  validTime: string | null;
  requestedMemberCount: number;         // every count below is a member-row tally
  loadedMemberCount: number; usableMemberCount: number;
  qualifyingMemberCount: number; nonqualifyingMemberCount: number;
  loadedUnusableMemberCount: number; notLoadedMemberCount: number;
  resolvedMemberCount: number; notEvaluatedMemberCount: number;
  agreementFraction: number | null; agreement: string;   // with its denominator
  requestedCoverage: number | null; coverage: string;
  reconciles: boolean; cancelled: boolean; label: string; // the interpretation
  members: { member: string; loaded: boolean; usable: boolean;
             satisfied: boolean | null;
             state: "qualifying" | "nonqualifying" | "unusable" | "no_profile"
                  | "failed" | "not_loaded" | "not_evaluated";
             stateLabel: string;        // the provider's own phrase
             values: Record<string, number | null>;  // the rule's diagnostics
             reason: string | null }[]; // one row per requested member
}
export interface ThresholdCsv { fileName: string; csv: string }
```

- **Labels come from sharpmod.** Parameter groups, labels, units, and decimals
  are `sharpmod.analysis.box_analysis.PARAMETERS` in registry order; table
  titles and columns are set by the bridge (`sounding_analysis.py`). A value
  sharpmod cannot compute is `null`, shown as an em dash; it is never `NaN`
  and never filled in.
- **The image.** `render_sounding` returns the PNG `cli.render` wrote,
  3260 by 2198 pixels at `hd` (about 1.2 MB). On the bridge it travels as
  `png_base64` in the JSON reply (protocol 1 has no binary body); Rust decodes
  it, checks the PNG signature, the stated length, and the sounding id, and
  hands JavaScript raw bytes.
- **Multi-time files.** `timeCount > 1` (BUFKIT) means the summary, the
  parameters, and the image describe the first valid time; `get_sounding_trends`
  covers every time.

### Map data

The TypeScript types are in `frontend/src/types/map.ts`, the Rust ones in
`src-tauri/src/dto/{map,overlays}.rs`, and examples in
`src-tauri/contracts/public/map/`.

```ts
export interface Palette {                 // sharpmod's colour scale, in display units
  stops: [number, string][];               // ascending values, "#rrggbb"
  units: string;
  stepped: boolean;                        // banded classes, else RGB between stops
  floor: number | null; ceiling: number | null;   // the drawn range (null: first stop, or clip to the last)
  ticks: number[];
}
export interface MapCatalog {
  fieldModel: string;                      // "hrrr"
  fieldBounds: Bounds;
  maxForecastHours: number[];              // the last hour of each cycle, 00Z to 23Z
  categories: string[]; defaultProduct: string;
  products: { key: string; label: string; shortLabel: string; category: string;
              description: string; caveat: string; palette: Palette }[];
  radarProducts: { key: string; label: string; units: string; description: string }[];
  siteProducts: { key: string; label: string; units: string; description: string }[];  // one antenna's products, distinct
  outlookProducts: { key: string; label: string }[];
}
interface RadarSite { id: string; latitude: number; longitude: number }
interface RadarSiteCatalog { sites: RadarSite[] }
interface ReportPoint {                    // one Local Storm Report
  label: string; description: string;
  fill: string | null; stroke: string;     // the provider's marker colours
  radiusPx: number; symbol: string;        // the provider's screen-marker spec
  caption: string; significant: boolean; category: string;
  valid: string | null; longitude: number; latitude: number;
}
interface StormReports {
  validTime: string; windowS: number; windowLabel: string; hazardFilter: string;
  totalCount: number; shownCount: number;  // before and after the hazard filter
  subtitle: string;
  validFrom: string | null; validTo: string | null;
  sourceUrl: string; attribution: string;
  reports: ReportPoint[];
}
interface ReportOptions {                  // the provider's own labels
  windows: { label: string; seconds: number }[];
  filters: { key: string; label: string }[];
}
interface ContourLines {
  product: string; runTime: string; forecastHour: number;
  unit: string; interval: number;         // the level spacing, in `unit`
  segments: [readonly [number, number], readonly [number, number]][];  // two endpoints each
  wind: { width: number; height: number; bounds: Bounds; data: Uint16Array } | null;
}
interface StaffSpec {                     // the wind mark, as the paint path splits it
  angleDeg: number;                       // staff tip from east, screen degrees, y down
  pennants: number; barbs: number; halves: number;
}
interface SurfacePlot {
  id: string; longitude: number; latitude: number;
  valid: string | null; offsetMin: number | null;  // the observation and its offset
  stale: boolean;
  availability: "available" | "unavailable" | "unknown";
  windState: "available" | "calm" | "missing" | "direction-missing";
  staff: StaffSpec | null;                // present exactly with an "available" wind
  upperText: string; lowerText: string;   // the reading pair, in the plot's own slots
  label: string; description: string;     // the hover block, the provider's own words
}
interface SurfacePlotCounts {             // what the fetch checked and found
  matched: number; stale: number; noObservation: number; failed: number;
  nearby: number; queried: number; unqueried: number;
}
interface SurfacePlots {
  validTime: string;
  counts: SurfacePlotCounts;              // the tallies agree with the records
  plots: SurfacePlot[];
}
export interface SpcOutlook {
  product: string; validTime: string | null;
  outlook: null | {                        // null: SPC published nothing for the time
    title: string; subtitle: string;
    validFrom: string | null; validTo: string | null; issued: string | null;
    covers: boolean;                       // false: the nearest outlook found does not cover the time
    sourceUrl: string; attribution: string;
    areas: { label: string; description: string;
             fill: string | null; stroke: string;   // SPC's colours
             rank: number; hatch: boolean; hatchLevel: number;
             rings: [number, number][][] }[];       // [lon, lat]; exterior ring, then holes
  };
}
export interface PlaceLabels { places: { name: string; latitude: number; longitude: number; tier: 1 | 2 }[] }
export interface AvailabilityReport {
  observationTime: string;
  listedIds: string[];                     // bundled stations UWyo lists
  extraStations: Station[];                // listed stations the bundled catalogue lacks
  droppedCount: number;
}
```

- **Sources.** The catalogue is `hrrr_products.available_products()` with
  their palettes, `hrrr_field.max_forecast_hour`, the mosaic (`radar_mosaic`)
  and single-site (`radar_site`) product lists, and the outlook
  product lists; outlooks are `spc_outlook_fetch.fetch_layer` at the time
  asked; places are the legacy map's `MAJOR_CITIES` (tier 1) and the largest
  incorporated place per quarter degree of the bundled Census index (tier 2,
  largest first); availability is `uwyo_catalog.fetch_stations_for_datetime`,
  the extras filtered by the record rule; the site catalogue is the bundled
  `radar_sites.json` through `radar_site.sites()`.
- **Field and radar frames** are binary ([Binary frames](#binary-frames)).
  A single-site frame carries `kind: "radar-site"` and the serving `siteId`.

## Error model

One shape crosses every boundary (REQUEST §27).

```ts
export interface BackendError {
  code: BackendErrorCode;      // the 21 codes below
  message: string;             // one user-facing sentence; never a traceback
  retryable: boolean;          // true: repeating the request may succeed
  context?: { operation: string; provider?: string; resource?: string };
  details?: Record<string, unknown>;   // developer-only: logged, never rendered
}
export interface FrontendError {       // same shape, raised only in TypeScript
  code: "CONTRACT_VIOLATION" | "TRANSPORT_FAILED";
  message: string;
  retryable: false;
  context?: { operation: string; provider?: string; resource?: string };
  details?: Record<string, unknown>;
}
```

Rust holds `ErrorCode` and `BackendError` in `src-tauri/src/error.rs`; internal
`BridgeFailure` values map to it in exactly one function,
`BridgeFailure::into_backend_error(context)`. Context strings are fixed:
provider `python-bridge`, resources `bundled UWyo station catalogue` and
`scientific backend status`.

| Code | Raised by, when | Retryable | UI action |
|---|---|---|---|
| `PYTHON_NOT_FOUND` | Rust discovery: `SHARPPY_PYTHON` invalid, or no interpreter | No | The configuration instruction |
| `BRIDGE_SPAWN_FAILED` | Rust: spawn I/O error, exit before `hello` (`details.exitCode`), `hello` timeout, Python older than 3.11 | No | Restart backend |
| `BRIDGE_TIMEOUT` | Rust: request deadline passed (`details.phase`, below) | Yes | Retry |
| `BRIDGE_CRASHED` | Rust: child exited with requests in flight, or Restart backend killed it (`details.reason: "restartRequested"`) | Yes | Retry |
| `BRIDGE_UNAVAILABLE` | Rust: fifth crash within 60 s (`details.lastErrorCode`) | No | Restart backend |
| `BRIDGE_PROTOCOL` | Rust (corrupt stream, `id: null`, bad payload, unknown error-frame code, version mismatch) or Python (unparsable request) | No | Restart backend; log path |
| `METHOD_NOT_FOUND` | Python: method not on the allowlist | No | None |
| `INVALID_ARGUMENT` | Rust command validation or Python params check | No | None |
| `CATALOG_UNAVAILABLE` | Python: the catalogue handler failed | Yes | Retry |
| `STATION_LIST_UNAVAILABLE` | Python: `stations.availability` got a `StationListError`; the bundled list stays | Yes | Retry |
| `BACKEND_UNAVAILABLE` | Python, inside health only: `BackendUnavailableError` | No | Shown in the health popover |
| `BACKEND_CONFIG_INVALID` | Python, inside health only: invalid `SHARPMOD_BACKEND` | No | Shown in the health popover |
| `INTERNAL` | Anywhere: an unexpected exception | No | Log path |
| `SOUNDING_UNAVAILABLE` | Python: no provider has the observed sounding | No | None ("Retrying will not help.") |
| `SOUNDING_FETCH_FAILED` | Python: an observation provider could not be reached, or a model sounding could not be retrieved (a run not yet published included) | Yes | Retry |
| `SOUNDING_INVALID` | Python: data arrived but sharpmod cannot read or analyse it (a file that is not a sounding, an empty file) | No | None |
| `SOUNDING_NOT_FOUND` | Python: the sounding id is not in the bridge cache (evicted, or opened before a bridge restart) | No | None; the message says to open it again |
| `RENDER_FAILED` | Python: the renderer failed (not retryable), or the render worker timed out or stopped (retryable) | Either | Retry when retryable; log path |
| `FIELD_UNAVAILABLE` | Python: the model frame is not published, or the hour is past the cycle's last | No | None ("Retrying will not help.") |
| `FIELD_FETCH_FAILED` | Python: the model data could not be downloaded (retryable) or read (not retryable) | Either | Retry when retryable |
| `OVERLAY_FETCH_FAILED` | Python: an SPC outlook or the radar mosaic could not be loaded, or the service sent no usable frame | Yes | Retry |
| `CONTRACT_VIOLATION` | TS client: a reply or event fails its parser, or an error-shaped rejection fails `parseBackendError` | No | Log path |
| `TRANSPORT_FAILED` | TS client: `invoke` rejected with a value that is not error-shaped (an ACL denial string, a missing command) | No | None |

The UI action per code lives in `frontend/src/features/errors/errorPresentation.ts`
and is rendered by `BackendErrorNotice.tsx`: the title comes from
`context.operation` ("Stations could not be loaded"), the body is `message`, a
muted line names provider and resource, and `details` is never rendered.
TanStack Query retries automatically only when `retryable` is true, at most
twice, after 1 s and 2 s. Each failure is logged once with `console.warn`;
`console.error` is reserved for programming errors.

**Error-frame codes.** A Python error frame may carry exactly fourteen codes:
`METHOD_NOT_FOUND`, `INVALID_ARGUMENT`, `BRIDGE_PROTOCOL`, `CATALOG_UNAVAILABLE`,
`STATION_LIST_UNAVAILABLE`, `INTERNAL`, `SOUNDING_UNAVAILABLE`,
`SOUNDING_FETCH_FAILED`, `SOUNDING_INVALID`, `SOUNDING_NOT_FOUND`,
`RENDER_FAILED`, `FIELD_UNAVAILABLE`, `FIELD_FETCH_FAILED`, and
`OVERLAY_FETCH_FAILED`. Each language holds that set:

- Python: `ERROR_CODES` in `sharpmod/bridge/errors.py`; `BridgeError` raises
  `ValueError` for any other code.
- Rust: `BridgeErrorCode` in `src-tauri/src/python/protocol.rs`; any other code
  fails that request with `BRIDGE_PROTOCOL` and the child keeps running.
- Fixtures: `src-tauri/contracts/bridge/error.<CODE>.json`, one per code; pytest
  and `cargo test` compare their sets with it. Vitest compares
  `BACKEND_ERROR_CODES` with the 21 files of `public/errors/`.

**`parseBackendError(value)`** (`frontend/src/lib/contracts/errors.ts`) has
three outcomes:

| Input | Result | Client reports |
|---|---|---|
| Not error-shaped: not a non-array object, or no string `code` | `null` | `TRANSPORT_FAILED` |
| Exactly `code`, `message`, `retryable`, optional `context` and `details`, all well typed, `code` known | a `BackendError` | that error |
| Any other error-shaped value (unknown code, missing `retryable`) | throws `ContractError` | `CONTRACT_VIOLATION` |

## Bridge protocol (version 1)

- **Framing.** One UTF-8 JSON object per `\n`-terminated line (Rust also
  accepts `\r\n`), at most 8 MiB per frame. Python writes
  `json.dumps(obj, allow_nan=False, ensure_ascii=False, separators=(",", ":"))`;
  serde_json rejects `NaN`, so a non-finite value is a bug, never data.
- **Envelope.** Request `{"id", "method", "params"}`, exactly those keys;
  reply `{"id", "result"}` or `{"id", "error"}`. `id` is an integer from 1 to
  2^53 − 1; `null` only in replies to frames Python could not parse. Replies may
  arrive in any order (an eight-thread pool; `hello` is answered on the read
  loop itself) and are matched by id.
- **Methods** (an allowlist; anything else is `METHOD_NOT_FOUND`):

| Method | Params | Result |
|---|---|---|
| `hello` | exactly `{"protocol_version": 1}` | `protocol_version`, `sharpmod_version`, `python_version`, `python_executable`, `pid` |
| `health` | `{}` | `{"scientific_backend": {...}}`: the five `backend_info()` keys plus `status` |
| `stations.catalog` | `{}` | `{"stations": [{"id", "name", "lat", "lon", "src"}], "origin": "bundled" \| "seed_fallback", "dropped_count"}` |
| `shutdown` | `{}` | `{"ok": true}`, then the bridge stops reading and exits 0 |
| `models.catalog` | `{}` | `{"models": [...], "generated_at"}`, from `tools.model_extract.available_models()` |
| `soundings.open_observed` | `station_id`, `time`, optional `provider` (`auto`, `uwyo`, `iem`, `igra`) | the sounding summary (snake_case `SoundingSummary`) |
| `soundings.open_model` | `model`, `forecast_hour`, `latitude`, `longitude`, optional `run_time` and `location` | the sounding summary |
| `soundings.open_file` | `file_name`, `content_base64` (at most 6 MiB of data) | the sounding summary |
| `soundings.analysis` | `sounding_id` | the parameters (snake_case `SoundingAnalysis`) |
| `soundings.trends` | `sounding_id` | the Trends (snake_case `SoundingTrends`) |
| `ensemble.summary` | `model`, `latitude`, `longitude`, `forecast_hour`, `members` (2 to 32 labels), optional `run_time` and `location` | `valid_time`, the acquisition ledger (`requested_members`, `loaded_members`, `failures`), the labelled `metrics` spreads, and one `samples` row per requested member (`sharpmod/bridge/ensemble.py`) |
| `thresholds.metrics` | `{}` | the registry diagnostics with their control bounds (`analysis.metric_controls.control_for`), the `operators` in control order, and the `default_metrics` (`sharpmod/bridge/thresholds.py`) |
| `thresholds.evaluate` | the `ensemble.summary` fields plus `definition` (`name` and 1 to 3 `conditions` of `metric`, `operator`, `value`, optional `upper`) | the evaluator's own `as_dict`: the definition with its registry units, the counts and their denominators, and one `members` row per requested member (`sharpmod/bridge/thresholds.py`) |
| `thresholds.export_csv` | the `thresholds.evaluate` fields | `file_name` and `csv`, the rows `analysis.ensemble_thresholds.export_threshold_csv` writes |
| `soundings.render` | `sounding_id`, optional `parcel` (`SFC`, `ML`, `FCST`, `MU`, `EFF`) and `image_mode` (`hd`, `uhd`) | `sounding_id`, `parcel`, `image_mode`, `width`, `height`, `byte_length`, `png_base64` |
| `map.catalog` | optional `model` (HRRR by default) | the snake_case `MapCatalog` (`sharpmod/bridge/fields.py`) |
| `fields.grid` | `product`, `run_time` (a whole hour), `forecast_hour` (0 to 84), optional `model` | the field header in snake_case plus `data_base64`, the uint16 cells |
| `outlooks.spc` | `product` (`cat`, `torn`, `wind`, `hail`), `valid_time` | `product`, `valid_time`, `outlook` (null, or the snake_case outlook) (`sharpmod/bridge/map_layers.py`) |
| `radar.mosaic` | optional `product` | the radar header in snake_case plus `byte_length` and `png_base64` |
| `radar.site` | optional `product`, `site_id` (a WSR-88D id) or `latitude` + `longitude` | the single-site header in snake_case plus `site_id`, `byte_length`, and `png_base64` (`sharpmod/bridge/map_layers.py`) |
| `radar.sites` | `{}` | `{"sites": [{"id", "lat", "lon"}]}`, the bundled catalogue |
| `satellite.goes` | `valid_time`, optional `channel` (`visible`, `infrared`) | the satellite header in snake_case plus `byte_length` and `png_base64` (`sharpmod/bridge/satellite.py`) |
| `reports.storms` | `valid_time`, optional `window_s` and `hazard_filter` | the counts, window, and filter plus the report records (`sharpmod/bridge/reports.py`) |
| `reports.options` | `{}` | `{"windows": [{"label", "seconds"}], "filters": [{"key", "label"}]}`, the provider's own labels |
| `fields.contours` | `product`, `run_time`, `forecast_hour`, optional `model` | `product`, `run_time`, `forecast_hour`, `unit`, `interval`, `segments` (two `[lon, lat]` endpoints each), and `wind` (nullable packed wind grid) (`sharpmod/bridge/contours.py`) |
| `surface.plots` | `latitude`, `longitude`, `valid_time` | `valid_time`, the station counts, and the plot records with their `staff` mark specs (`sharpmod/bridge/surface_plots.py`) |
| `places.labels` | `{}` | `{"places": [{"name", "lat", "lon", "tier"}]}` |
| `stations.availability` | `observation_time` (a whole hour, at most one hour ahead) | `observation_time`, `listed_ids`, `extra_stations` (station records), `dropped_count` |

Fields support `hrrr`, `rrfs-a`, `nam-3km-conus`, and `refs-m1` through `refs-m5`.
The selected catalogue lists only supported products: 25 for HRRR and RRFS,
24 for NAM (without Lightning Flash Density), and 16 for REFS members.
RRFS runs end at F084; NAM and REFS at F060; HRRR retains
its 18/48-hour cycle limits. Model/member identity is part of field, contour,
catalogue and playback cache keys. `catalog.models.json` fixtures cover the added
models. NAM's 0-1 km shear interpolates pressure-level winds to 1 km above model
terrain and differences them against the 10 m wind; its parameter caveat states
this. Multipart NAM U/V records share a byte range and decode as distinct fields.

The wind grid uses the existing field extent and north-to-south cell centres.
It is half the scalar frame's width and height: 1024 by 512 in the live map.
This supports the fixed barb spacing through the map's maximum zoom of 9
while keeping the response, including dense height contours, below the
bridge's frame cap. Increase the wind grid if that maximum zoom is raised.
On the wire, `wind` contains `width`, `height`, `bounds`, and `data_base64`
(`dataBase64` in the public response). Its body is little-endian uint16:
low 9 bits are earth-relative wind-from degrees (0–359), high 7 bits count
5 kt increments, and 65535 marks missing or off-domain cells. Speeds are
rounded to the nearest 5 kt for the barb glyph; the scalar field and cursor
values retain their existing precision. The client validates and decodes this
once per shared forecast query, then samples visible cells on settled camera
changes. Winds below the shading floor remain eligible for barbs.

Talk to it by hand: `.gribenv\Scripts\python.exe -I -u -X utf8 -m sharpmod.bridge`,
then send `{"id":1,"method":"hello","params":{"protocol_version":1}}`.

### Failure classes

Process failures (`src-tauri/src/python/supervisor/`):

| Failure | Result |
|---|---|
| Child exits, or stdout reaches EOF, after `hello` | Pending requests fail `BRIDGE_CRASHED`; `restarting`; counts as a crash |
| Child exits before `hello` | `unavailable` with `BRIDGE_SPAWN_FAILED` at once, no restart loop |
| Interpreter missing, or spawn I/O error | `unavailable` at once (`PYTHON_NOT_FOUND` or `BRIDGE_SPAWN_FAILED`) |
| `hello` reports another protocol version, or Python < 3.11 | `unavailable` with `BRIDGE_PROTOCOL` or `BRIDGE_SPAWN_FAILED` |
| `hello` not answered in 15 s | Kill; counts as a crash; `restarting` with `lastError` `BRIDGE_SPAWN_FAILED` |

Frame classes (the reader checks the envelope by key presence, then decodes
the payload):

| Condition | Action |
|---|---|
| Corrupt stream: invalid UTF-8 JSON, over 8 MiB, a non-finite token, or a malformed envelope | Pending requests fail `BRIDGE_PROTOCOL`; kill and restart (a crash) |
| Valid envelope whose id is not pending (a late reply) | Logged at `warn` and dropped |
| Valid envelope with `id: null` | Pending requests fail `BRIDGE_PROTOCOL`; no restart |
| Pending id whose payload fails its typed decode, or an error code outside the error-frame set | That request alone fails `BRIDGE_PROTOCOL`; no restart |

### Request phases and timeouts

A request's deadline (catalogue 15 s, health 20 s, models 30 s, open observed
90 s, open model 300 s, open file 60 s, parameters 60 s, Trends 180 s, image
200 s, map catalogue 30 s, field 120 s, outlook 60 s, radar 60 s, places 30 s,
availability 35 s) covers its whole wait.
`details.phase` names the phase it was in when the deadline passed:

| Phase | Meaning | On expiry |
|---|---|---|
| `waitingForBridge` | Queued while the bridge is `starting` or `restarting` | Fails `BRIDGE_TIMEOUT` with `state`; sends no ping, kills nothing, counts no crash |
| `awaitingReply` | Written to an `up` child | Fails `BRIDGE_TIMEOUT`; the supervisor sends one liveness `hello` (3 s) to that child; a failed ping kills it and counts one crash |

**Backoff reset.** Restart delays are 0.5, 1, 2, 4, 8, 16, then 30 s. The
attempt counter resets to 0 once a child has stayed `up` 60 s, checked at the
next crash, so no timer is needed. The fifth crash in a sliding 60 s window
gives `BRIDGE_UNAVAILABLE`; only `restart_backend` leaves it.

**Cancellation.** Version 1 has no per-request cancellation. A timed-out
request leaves the pending map; its Python work finishes and the late reply is
dropped. The frontend does not forward TanStack's `AbortSignal`, because
`invoke` cannot be cancelled. Only `shutdown` (cancels queued work) and a kill
stop Python work. Requests pending when Restart backend kills the child fail
`BRIDGE_CRASHED` with `details.reason: "restartRequested"`, and the client then
refetches.

## Event ordering

Events are not replayed, and the bridge often reaches `up` while the webview is
still loading. `frontend/src/lib/backend/BackendProvider.tsx` therefore:

1. subscribes to `bridge-status-changed`, and only then enables the health
   query, so no transition falls between the two;
2. on each event, patches `bridge` in the cached health and invalidates health;
   on a return to `up`, also refetches a catalogue whose last load failed;
3. polls health every second while the bridge is `starting` or `restarting`;
4. if subscribing fails, warns once and polls every 5 s for the session;
5. unlistens on unmount.

Event payloads go through the same parser as `BridgeStatus` in health. The
catalogue query has `staleTime: Infinity` and `retryOnMount: false`: it reloads
only on Retry, Restart backend, or the bridge returning to `up` after a failure.

## Fixtures and drift prevention

`src-tauri/contracts/` holds `bridge/` (snake_case payloads) and `public/`
(camelCase DTOs), each with an `invalid/` folder named `<target>.<case>`
(`stations.catalog`, `health`, or `error`). Its
[README](../../src-tauri/contracts/README.md) lists the families.

| Runner | Test | Checks |
|---|---|---|
| `cargo test --test contracts` | `src-tauri/tests/contracts.rs` | Every positive fixture round-trips, every invalid one is rejected, the bridge-to-public conversions equal the public fixtures, both code sets equal their file sets |
| Vitest | `frontend/src/lib/contracts/contracts.test.ts` | Every public fixture parses; every invalid file throws a `ContractError` naming the path; `BACKEND_ERROR_CODES` equals `public/errors/` |
| pytest | `sharpmod/tests/test_bridge_contract.py` | Real `hello`, `health`, and `stations.catalog` replies match field schemas and fixture keys; the golden files are byte-identical to the exporter's output; seed detection |
| `cargo test --test python_sidecar -- --include-ignored` | `src-tauri/tests/python_sidecar.rs` | The real sidecar's converted catalogue equals the golden 933-station file |
| pytest | `sharpmod/tests/test_bridge_soundings.py` | The real handlers on the example files match the sounding fixtures (keys and the audited values); the whole registry in order; error mapping for every provider failure; the render client against a stand-in worker, including a crashed and a hung one; no Qt module loaded by the handlers |
| Vitest | `frontend/src/lib/contracts/soundings.test.ts` | The sounding fixtures parse unchanged; malformed tables, values, and PNG bytes are rejected |
| pytest | `sharpmod/tests/test_bridge_map.py` | The committed map fixtures equal the exporter's output; the grid is the derived field quantized without visible loss; an hour past the cycle and every field and overlay failure become structured errors; outlook colours and holes survive; the place selection; availability splits bundled and extra stations; no Qt module loaded |
| `cargo test --test contracts` | `map_fixtures_convert_from_bridge_to_public_form` | Each bridge map result converts to its public fixture; the field and radar frames Rust builds equal the committed ones, header and body |
| Vitest | `frontend/src/lib/contracts/map.test.ts` | The map fixtures and both `SHRF` frames parse; a bad magic, a truncated header, a short body, a zero scale, an extra key, a radar body that is not a PNG, bad colours, unordered stops, missing cycle limits, vertices off the globe, unknown tiers, and extra station keys are rejected |

The sounding fixtures (`bridge/soundings/`, `public/soundings/`) are written
by `scripts/export_sounding_fixtures.py` from the real handlers on the example
files, and the map fixtures (`bridge/map/`, `public/map/`, including
`field.shrf` and `radar.shrf`) by `scripts/export_map_fixtures.py` from the real
handlers with offline stand-ins; see the
[contracts README](../../src-tauri/contracts/README.md). The sounding and map
error files follow the existing one-file-per-code rule.

**Golden files**, generated by `scripts/export_frontend_fixtures.py` and never
edited by hand: `public/stations.catalog.full.json`,
`public/stations.search-cases.json`, `public/stations.catalog.seed-fallback.json`,
and `bridge/stations.catalog.result.seed-fallback.json`. Regenerate them from the
repo root whenever `sharpmod/resources/uwyo_stations.json` changes:

```powershell
.gribenv\Scripts\python.exe scripts/export_frontend_fixtures.py
```

Until then pytest and the real-sidecar test fail, so a stale file cannot pass.
The search cases pin the TypeScript search (`frontend/src/stations/stationSearch.ts`)
to `uwyo_catalog.search_stations()`.

## Validation

| Input | Rule | Owner |
|---|---|---|
| Phase 1 command arguments | None are read | n/a |
| Sounding command arguments | `deny_unknown_fields`; station id 1 to 16 characters; model key 1 to 40; file name 1 to 255; file data 1 byte to 6 MiB; forecast hour at most 1000; finite coordinates in range; sounding id 1 to 64 ASCII letters and digits (the bridge issues 16 hex digits) | `src-tauri/src/dto/soundings.rs`, `backend/sounding_ops.rs` |
| Bridge `params` | an object; `hello` exactly `protocol_version: 1`; the sounding methods check their keys, types, ranges, and choices again | `sharpmod/bridge/protocol.py`, `sharpmod/bridge/params.py` |
| Bridge replies | envelope, frame cap, UTF-8 JSON, routing by id, closed codes, `deny_unknown_fields`, the record rule | `src-tauri/src/python/protocol.rs`, `supervisor/`, `backend/convert.rs` |
| Command replies and events | hand-written parsers (`guards.ts`, `health.ts`, `stations.ts`, `errors.ts`); timestamps must match `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$`; duplicate ids rejected | `frontend/src/lib/contracts/` |
| `SHARPPY_PYTHON` | absolute path to an existing file | `src-tauri/src/python/interpreter.rs` |
| Stored preferences | field by field; a bad field falls back with one warning | `frontend/src/stores/preferences.ts` |
| Search text | at most 64 characters, trimmed with Python's whitespace set, rendered as text | `frontend/src/features/search/StationSearch.tsx`, `stations/stationSearch.ts` |
| Map command arguments | product keys 1 to 40 ASCII letters, digits, and `-`; times 10 to 40 ASCII characters; forecast hour 0 to 48 | `src-tauri/src/backend/map_ops.rs` |
| Map bridge `params` | known keys only; products from sharpmod's lists; `run_time` and `observation_time` on a whole hour, the observation time at most one hour ahead; forecast hour at most the cycle's last | `sharpmod/bridge/fields.py`, `map_layers.py`, `params.py` |
| Map replies | palettes ascending and finite with `#rrggbb` colours, 24 cycle limits, ordered bounds, vertices on the globe, the field body exactly `width * height * 2` bytes, the radar body a PNG of the stated length | `src-tauri/src/dto/map.rs`, `overlays.rs`, `backend/map_ops.rs`; again in `frontend/src/lib/contracts/map.ts`, `frames.ts` |

## Binary frames

Large grids never travel as JSON number arrays to the webview.
`get_weather_field` and `get_radar_frame` return a `tauri::ipc::Response`, so
JavaScript receives one `ArrayBuffer`, an `SHRF` frame (little-endian):

```text
offset 0   magic "SHRF"
offset 4   format version, u16 (1)
offset 6   reserved, u16 (0)
offset 8   header length N, u32
offset 12  N bytes of UTF-8 JSON, padded with spaces so the body starts on an 8-byte boundary
then       the body
```

```ts
interface FieldFrameHeader {           // the body: width * height uint16 cells
  kind: "field"; product: string; runTime: string; forecastHour: number; validTime: string;
  width: number; height: number;       // 2048 by 1024 for HRRR
  bounds: Bounds;                      // row 0 at the north edge, rows evenly spaced in latitude
  units: string;
  scale: number; offset: number;       // value = cell * scale + offset
  nodata: number;                      // 65535
  valueMin: number; valueMax: number;
  sourceUrl: string; attribution: string;
}
interface RadarFrameHeader {           // the body: the radar service's PNG
  kind: "radar"; product: string; label: string; units: string; bounds: Bounds;
  retrievedAt: string | null; validTime: string | null; updateIntervalS: number;
  attribution: string; sourceUrl: string;
}
interface RadarSiteFrameHeader {       // the body: the radar service's PNG
  kind: "radar-site"; product: string; siteId: string; label: string; units: string; bounds: Bounds;
  retrievedAt: string | null; validTime: string | null; updateIntervalS: number;
  attribution: string; sourceUrl: string;
}
interface SatelliteFrameHeader {       // the body: the rendered GOES PNG
  kind: "satellite"; product: string; satellite: string; label: string; units: string; bounds: Bounds;
  requestedTime: string; retrievedAt: string | null; validTime: string;  // validTime: the actual scan
  updateIntervalS: number;
  coverageFraction: number;            // the share of the frame the scan covers
  nativeResolutionKm: number;
  attribution: string; sourceUrl: string;
}
```

- **Quantization.** The bridge quantizes the reprojected field linearly between
  its finite minimum and maximum (65,534 steps), so a cell is within half a
  step of sharpmod's float value; a 2048 by 1024 frame is 4 MiB.
- **Parsing** (`frontend/src/lib/contracts/frames.ts`). The header must have
  exactly its keys; the field body must be exactly `width * height * 2`
  bytes and becomes a `Uint16Array` over the response without a copy; the
  radar body must be a PNG.
- **Python to Rust.** Protocol version 1 has no binary body, so the bridge
  sends the cells as `data_base64` (and the PNG as `png_base64`) in its JSON
  reply. The 8 MiB frame cap therefore holds about 2.9 M cells
  (`ponytail:` in `sharpmod/bridge/fields.py`). Rust decodes the base64, checks
  the length, and builds the frame (`backend/map_ops.rs`); it never interprets
  the values.
- **Not built.** `tauri::ipc::Channel` streams, a `sharppy-data` URI scheme,
  and protocol version 2 binary bodies (`"body": {"bytes": N}` after a success
  frame) were designed for larger or streamed payloads; nothing needs them yet.

## Station availability

`get_station_availability(observationTime)` calls `stations.availability`,
which wraps `uwyo_catalog.fetch_stations_for_datetime()` and returns
`{observationTime, listedIds, extraStations, droppedCount}`, the extras
filtered by the record rule. The request starts only when the user picks a
release in the layer panel (`map/controls/AvailabilityControl.tsx`). The
frontend then marks stations `listed` or `notListed` and adds the extras
(`stations/availability.ts`, through the catalogue query's `select`), with
`availabilityBasis: {kind: "listedAt", observationTime}`. While it loads, the
bundled list stays and the control says it is checking;
`STATION_LIST_UNAVAILABLE` keeps the bundled list and says so, as the legacy
picker does (`sharpmod/ui/picker/availability.py`).
