// Deterministic fixture transport (DESIGN-frontend F2) for explicit fixture builds
// and tests. It serves the shared contract fixtures, the golden 933-station
// catalogue included, and rejects with plain BackendError objects, so the
// client's mapping is the one under test. A ?scenario= value, read once at
// startup, selects one state; see SCENARIOS.
import catalogFull from "@contracts/public/stations.catalog.full.json";
import catalogSeedFallback from "@contracts/public/stations.catalog.seed-fallback.json";
import catalogUnavailable from "@contracts/public/errors/CATALOG_UNAVAILABLE.json";
import internalError from "@contracts/public/errors/INTERNAL.json";
import pythonNotFound from "@contracts/public/errors/PYTHON_NOT_FOUND.json";
import healthOk from "@contracts/public/health.ok.json";
import healthPythonUnavailable from "@contracts/public/health.python-unavailable.json";
import healthRestarting from "@contracts/public/health.restarting.json";
import soundingUnavailable from "@contracts/public/errors/SOUNDING_UNAVAILABLE.json";
import soundingAnalysis from "@contracts/public/soundings/analysis.example.json";
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import modelStatus from "@contracts/public/soundings/models.status.json";
import renderExampleUrl from "@contracts/public/soundings/render.example.png?url";
import viewExample from "@contracts/public/soundings/view.example.json";
import soundingSummary from "@contracts/public/soundings/summary.example.json";
import multiTimeSummary from "@contracts/public/soundings/summary.multi-time.json";
import soundingTrends from "@contracts/public/soundings/trends.example.json";
import fieldUnavailable from "@contracts/public/errors/FIELD_UNAVAILABLE.json";
import overlayFailed from "@contracts/public/errors/OVERLAY_FETCH_FAILED.json";
import stationListUnavailable from "@contracts/public/errors/STATION_LIST_UNAVAILABLE.json";
import availabilityExample from "@contracts/public/map/availability.json";
import mapCatalog from "@contracts/public/map/catalog.json";
import { fixtureFrameCache } from "./fixtureFrameCache";
import modelMapCatalogs from "@contracts/public/map/catalog.models.json";
// no-inline: a small file would otherwise become a data: URL, which the CSP's connect-src refuses.
import fieldShrfUrl from "@contracts/public/map/field.shrf?url&no-inline";
import outlookCat from "@contracts/public/map/outlook.cat.json";
import weatherAlerts from "@contracts/public/map/alerts.json";
import outlookTorn from "@contracts/public/map/outlook.torn.json";
import outlookWind from "@contracts/public/map/outlook.wind.json";
import placeLabels from "@contracts/public/map/places.json";
import radarShrfUrl from "@contracts/public/map/radar.shrf?url&no-inline";
import { encodeShrf, parseShrf } from "../contracts/frames";
import type { CommandName, Transport } from "./transport";
import radarSites from "@contracts/public/map/radar-sites.json";
import radarTilts from "@contracts/public/map/radar-tilts.json";
import { contoursFor, hoursLater, reportsFor, satelliteFrameFor, siteFrameFor, surfacePlotsFor, thresholdFor } from "./fixtureFrames";
import reportOptionsFixture from "@contracts/public/map/reports-options.json";
import thresholdMetricsFixture from "@contracts/public/soundings/threshold-metrics.json";
import thresholdCsvFixture from "@contracts/public/soundings/threshold-csv.example.json";
import storageFixture from "@contracts/public/storage/usage.json";
import clearedStorageFixture from "@contracts/public/storage/clear.json";
export const SCENARIOS = [
  "python-unavailable",
  "catalog-error",
  "seed-fallback",
  "restarting",
  "slow-catalog",
  "health-error",
  // Map-side (DESIGN-frontend F4); the transport serves the healthy defaults.
  "basemap-missing",
  // Soundings: opening one fails as no provider has data.
  "sounding-error",
  "render-disconnected",
  // Map data: every field frame is unpublished; outlooks, radar, and the UWyo list fail.
  "field-error",
  "overlay-error",
] as const;
export type Scenario = (typeof SCENARIOS)[number];
/** Rejections the catalog-error scenario makes before it succeeds: the call and two automatic retries. */
export const CATALOG_ERROR_FAILURES = 3;
/** Delay of the slow-catalog scenario. */
export const SLOW_CATALOG_MS = 1500;
function isScenario(value: string): value is Scenario {
  return (SCENARIOS as readonly string[]).includes(value);
}
/** Resolves a ?scenario= value; an unknown value is a test-authoring error. */
export function resolveScenario(value: string | null): Scenario | null {
  if (value === null || value === "") {
    return null;
  }
  if (isScenario(value)) {
    return value;
  }
  console.error(`[fixture] unknown scenario ${JSON.stringify(value)}; serving the defaults`);
  return null;
}
function scenarioFromLocation(): string | null {
  return typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("scenario");
}
/** A fresh copy, so no caller can alias or mutate the shared fixture. */
function copy(value: unknown): unknown {
  return structuredClone(value);
}
/**
 * A catalogue request while Python is unavailable: the stored error with the
 * request's own context, as the Rust supervisor returns it (PROGRESS I9), so
 * the notice is titled from get_station_catalog.
 */
const catalogPythonNotFound = {
  ...pythonNotFound,
  context: { operation: "get_station_catalog", provider: "python-bridge", resource: "bundled UWyo station catalogue" },
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * A fixture summary for an open request: the example sounding, retitled for
 * the request. A file whose name ends in .buf gets the 49-time BUFKIT summary.
 */
function summaryFor(id: string, args: Readonly<Record<string, unknown>> | undefined): unknown {
  const request = (args?.request ?? {}) as Record<string, unknown>;
  const text = (key: string): string | null => (typeof request[key] === "string" ? request[key] : null);
  const multiTime = request.kind === "file" && (text("fileName") ?? "").toLowerCase().endsWith(".buf");
  const base = copy(multiTime ? multiTimeSummary : soundingSummary) as Record<string, unknown>;
  base.soundingId = id;
  if (request.kind === "observed") {
    const station = text("stationId") ?? "fixture";
    Object.assign(base, { kind: "observed", title: `${station} (example data)`, location: station, stationId: station });
    Object.assign(base, { fileName: null, model: null, runTime: null, validTime: text("time") ?? base.validTime });
  } else if (request.kind === "model") {
    Object.assign(base, { kind: "model", title: `${text("model") ?? "Model"} (example data)`, fileName: null });
  } else if (request.kind === "era5") {
    Object.assign(base, { kind: "era5", title: "ERA5 (example data)", model: "ERA5", provider: "cds", providerName: "Copernicus Climate Data Store", fileName: null, runTime: null, forecastHour: null, validTime: text("time"), latitude: request.latitude, longitude: request.longitude });
  } else if (request.kind === "file") {
    Object.assign(base, { title: text("fileName") ?? "example", fileName: text("fileName") });
  } else if (request.kind === "wrf") {
    Object.assign(base, { kind: "file", title: "WRF (example data)", model: "WRF-ARW", provider: "local", providerName: "Local WRF NetCDF", fileName: "wrfout_d01.nc", validTime: text("time"), latitude: request.latitude, longitude: request.longitude });
  }
  return base;
}

/** Delay of a fixture field frame, so the loading states show. */
export const FIELD_DELAY_MS = 250;

/**
 * The synthetic fixture field, relabelled for the request and rescaled onto the
 * requested product's palette, so every product draws something in a fixture build.
 */
async function fieldFor(args: Readonly<Record<string, unknown>> | undefined): Promise<ArrayBuffer> {
  const frame = parseShrf(await (await fetch(fieldShrfUrl)).arrayBuffer());
  const product = String(args?.product);
  const runTime = String(args?.runTime);
  const forecastHour = Number(args?.forecastHour);
  const palette = mapCatalog.products.find((item) => item.key === product)?.palette;
  const stops = palette?.stops ?? [];
  const top = Number(stops[stops.length - 1]?.[0] ?? frame.header.valueMax);
  // The lowest third falls under the palette, so it stays uncoloured as real fields do.
  const low = Number(stops[0]?.[0] ?? frame.header.valueMin) - (top - Number(stops[0]?.[0] ?? 0)) / 2;
  const header = {
    ...frame.header,
    product,
    runTime,
    forecastHour,
    validTime: hoursLater(runTime, forecastHour),
    units: palette?.units ?? frame.header.units,
    scale: (top - low) / 65534,
    offset: low,
    valueMin: low,
    valueMax: top,
  };
  await delay(FIELD_DELAY_MS);
  return encodeShrf(header, frame.body);
}

const OUTLOOKS: Readonly<Record<string, unknown>> = { cat: outlookCat, torn: outlookTorn, wind: outlookWind };

/** The fixture outlook of a product, for the requested time; hail has none. */
function outlookFor(args: Readonly<Record<string, unknown>> | undefined): unknown {
  const product = String(args?.product);
  const validTime = String(args?.validTime);
  const base = copy(OUTLOOKS[product] ?? { ...outlookWind, product }) as Record<string, unknown>;
  const outlook = base.outlook as { validFrom: string; validTo: string; covers: boolean } | null;
  if (outlook !== null) {
    const t = Date.parse(validTime);
    outlook.covers = Date.parse(outlook.validFrom) <= t && t < Date.parse(outlook.validTo);
  }
  return { ...base, validTime };
}

/** Rejects with the raw fixture value, as Tauri rejects with the serialized BackendError. */
function rejectWith(fixture: unknown): Promise<never> {
  // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- the contract is a plain object
  return Promise.reject(copy(fixture));
}

export function createTransport(scenarioParam: string | null = scenarioFromLocation()): Transport {
  const scenario = resolveScenario(scenarioParam);
  let catalogCalls = 0, opened = 0, renderAttempts = 0;
  let storage = structuredClone(storageFixture);
  const frames = fixtureFrameCache();

  const health = (): unknown => {
    if (scenario === "python-unavailable") {
      return healthPythonUnavailable;
    }
    return scenario === "restarting" ? healthRestarting : healthOk;
  };

  const handlers: Record<CommandName, (args?: Readonly<Record<string, unknown>>) => Promise<unknown>> = {
    cancel_request: () => Promise.resolve(null),
    get_cached_forecast_hours: (args) => Promise.resolve(frames.hours(args)),
    get_storage_usage: () => Promise.resolve(copy(storage)),
    clear_cache: (args) => {
      if (args?.cache === "all" || args?.cache === "maps") frames.clear();
      let freedBytes = 0;
      storage = { caches: storage.caches.map((row) => {
        if (args?.cache !== "all" && args?.cache !== row.key) return row;
        freedBytes += row.clearableBytes;
        return { ...clearedStorageFixture.usage.caches.find((item) => item.key === row.key) ?? row };
      }) };
      return Promise.resolve({ usage: copy(storage), freedBytes, failedFiles: 0 });
    },
    get_backend_health: () =>
      scenario === "health-error" ? rejectWith(internalError) : Promise.resolve(copy(health())),
    get_station_catalog: () => {
      catalogCalls += 1;
      switch (scenario) {
        case "python-unavailable":
          return rejectWith(catalogPythonNotFound);
        case "catalog-error":
          return catalogCalls <= CATALOG_ERROR_FAILURES
            ? rejectWith(catalogUnavailable)
            : Promise.resolve(copy(catalogFull));
        case "seed-fallback":
          return Promise.resolve(copy(catalogSeedFallback));
        case "slow-catalog":
          return delay(SLOW_CATALOG_MS).then(() => copy(catalogFull));
        default:
          return Promise.resolve(copy(catalogFull));
      }
    },
    restart_backend: () => Promise.resolve(copy((health() as { bridge: unknown }).bridge)),
    get_model_catalog: () => Promise.resolve(copy(modelCatalog)),
    get_model_status: (args) => Promise.resolve({ ...copy(modelStatus) as Record<string, unknown>, model: args?.model, runTime: args?.runTime, forecastHour: args?.forecastHour }),
    open_sounding: (args) => {
      if (scenario === "sounding-error") {
        return rejectWith(soundingUnavailable);
      }
      opened += 1;
      return delay(SLOW_CATALOG_MS / 3).then(() => summaryFor(`fixture${String(opened)}`, args));
    },
    get_sounding_progress: () => Promise.resolve({ stage: "waiting", downloadedBytes: 0, bytesPerSecond: 0 }),
    inspect_wrf: (args) => Promise.resolve({ filePath: args?.filePath, fileName: "wrfout_d01.nc", times: ["2026-06-25T06:00:00Z", "2026-06-25T07:00:00Z"], latitude: 35, longitude: -97 }),
    get_sounding_analysis: (args) =>
      Promise.resolve({ ...(copy(soundingAnalysis) as Record<string, unknown>), soundingId: args?.soundingId }),
    get_sounding_trends: (args) =>
      Promise.resolve({ ...(copy(soundingTrends) as Record<string, unknown>), soundingId: args?.soundingId }),
    get_threshold_metrics: () => Promise.resolve(copy(thresholdMetricsFixture)),
    evaluate_threshold: (args) => Promise.resolve(thresholdFor(args)),
    export_threshold_csv: () => Promise.resolve(copy(thresholdCsvFixture)),
    render_sounding: () => fetch(renderExampleUrl).then((response) => response.arrayBuffer()),
    render_sounding_view: async () => {
      if (scenario === "render-disconnected" && renderAttempts++ === 0) throw new Error("Simulated connection loss");
      const response = await fetch(renderExampleUrl);
      const buffer = await response.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      const dimensions = new DataView(bytes.buffer);
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      return { ...copy(viewExample) as object, image: btoa(binary), width: dimensions.getUint32(16), height: dimensions.getUint32(20) };
    },
    get_map_catalog: (args) => {
      const catalog = copy((modelMapCatalogs as Readonly<Record<string, unknown>>)[String(args?.model)] ?? mapCatalog) as typeof mapCatalog;
      if (String(args?.model).startsWith("hrw-") && args?.forecastHour === 0) catalog.products = catalog.products.filter((product) => product.key !== "uh-0-3km" && product.key !== "maxuvv");
      return Promise.resolve(catalog);
    },
    get_weather_field: (args) => (scenario === "field-error" ? rejectWith(fieldUnavailable) : fieldFor(args)),
    get_weather_alerts: (args) => scenario === "overlay-error" ? rejectWith(overlayFailed) : Promise.resolve({ ...copy(weatherAlerts) as Record<string, unknown>, validTime: args?.validTime }),
    get_spc_outlook: (args) =>
      scenario === "overlay-error" ? rejectWith(overlayFailed) : Promise.resolve(outlookFor(args)),
    get_radar_frame: () =>
      scenario === "overlay-error"
        ? rejectWith(overlayFailed)
        : fetch(radarShrfUrl).then((response) => response.arrayBuffer()),
    get_radar_site_frame: (args) =>
      scenario === "overlay-error"
        ? rejectWith(overlayFailed)
        : siteFrameFor(args),
    get_radar_sites: () => Promise.resolve(copy(radarSites)),
    get_radar_tilts: (args) => Promise.resolve({ ...copy(radarTilts) as object, siteId: args?.site, product: args?.product }),
    get_satellite_frame: (args) =>
      scenario === "overlay-error" ? rejectWith(overlayFailed) : satelliteFrameFor(args),
    get_storm_reports: (args) =>
      scenario === "overlay-error" ? rejectWith(overlayFailed) : Promise.resolve(reportsFor(args)),
    get_report_options: () => Promise.resolve(copy(reportOptionsFixture)),
    get_field_contours: (args) =>
      scenario === "field-error" ? rejectWith(fieldUnavailable) : Promise.resolve(contoursFor(args)),
    get_surface_plots: (args) =>
      scenario === "overlay-error" ? rejectWith(overlayFailed) : Promise.resolve(surfacePlotsFor(args)),
    get_place_labels: () => Promise.resolve(copy(placeLabels)),
    get_station_availability: (args) =>
      scenario === "overlay-error"
        ? rejectWith(stationListUnavailable)
        : Promise.resolve({ ...(copy(availabilityExample) as Record<string, unknown>), observationTime: args?.observationTime }),
  };

  return {
    call: async (command, args) => { const result = await handlers[command](args); frames.record(command, args); return result; },
    // Fixture data never changes, so no event is ever emitted.
    listen: () => Promise.resolve(() => undefined),
  };
}
