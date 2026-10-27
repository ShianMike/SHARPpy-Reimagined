import type { AnyBackendError } from "../types/contracts";
import type { CommandName, EventName } from "./backend/transport";

export interface DiagnosticInput {
  operation?: unknown;
  provider?: unknown;
  code?: unknown;
}

type DiagnosticCode = AnyBackendError["code"] | "APPLICATION_ERROR" | "WINDOW_FAILED" | "MAP_RENDER_FAILED" | "UPDATE_FAILED" | "UNKNOWN_ERROR";
export interface DiagnosticMetadata {
  operation: string;
  provider: string;
  code: DiagnosticCode;
}

const OPERATIONS = new Set<string>([
  "get_storage_usage", "clear_cache", "cancel_request", "get_backend_health", "get_station_catalog", "restart_backend",
  "get_model_catalog", "get_model_status", "open_sounding", "get_sounding_progress", "inspect_wrf", "get_sounding_analysis",
  "get_sounding_trends", "get_threshold_metrics", "evaluate_threshold", "export_threshold_csv", "render_sounding", "render_sounding_view",
  "get_map_catalog", "get_weather_field", "get_cached_forecast_hours", "get_spc_outlook", "get_weather_alerts", "get_radar_frame",
  "get_radar_site_frame", "get_radar_sites", "get_radar_tilts", "get_satellite_frame", "get_storm_reports", "get_report_options",
  "get_field_contours", "get_surface_plots", "get_place_labels", "get_station_availability", "start_backend", "bridge-status-changed",
] satisfies (CommandName | EventName | "start_backend")[]);
const PROVIDERS: Readonly<Record<string, string>> = {
  "python-bridge": "Python backend",
  rust: "Rust backend",
  uwyo: "University of Wyoming Upper Air Archive",
  iem: "Iowa Environmental Mesonet RAOB Archive",
  igra2: "NOAA Integrated Global Radiosonde Archive v2",
  cds: "Copernicus Climate Data Store",
  Herbie: "Herbie",
  "Open-Meteo": "Open-Meteo",
  "NOAA NOMADS": "NOAA NOMADS",
  "ECCC MSC GeoMet": "ECCC MSC GeoMet",
  NOAA: "NOAA",
  MRMS: "NOAA/NWS MRMS",
  NEXRAD: "NOAA/NWS NEXRAD",
  GOES: "NOAA/NESDIS GOES",
  SPC: "NOAA/NWS Storm Prediction Center",
  local: "Local file",
  Frontend: "Frontend",
  Updater: "Updater",
};
// Exact aliases only: never derive provider names from endpoints, messages or resources.
const PROVIDER_ALIASES: Readonly<Record<string, string>> = {
  Python: "python-bridge", python: "python-bridge",
  "University of Wyoming Upper Air Archive": "uwyo",
  "Iowa Environmental Mesonet RAOB Archive": "iem",
  "NOAA Integrated Global Radiosonde Archive v2": "igra2",
  igra: "igra2",
  "Copernicus Climate Data Store": "cds",
  herbie: "Herbie", "open-meteo": "Open-Meteo",
  nomads: "NOAA NOMADS", NOMADS: "NOAA NOMADS", "noaa-nomads": "NOAA NOMADS",
  "eccc-geomet": "ECCC MSC GeoMet",
  "NOAA/NWS MRMS via NCEP GeoServer": "MRMS",
  "NOAA/NWS NEXRAD via NCEP GeoServer": "NEXRAD",
  "NOAA/NWS NEXRAD Level III": "NEXRAD",
  "NOAA/NEXRAD Level II · NSF Unidata": "NEXRAD",
  "NOAA/NESDIS GOES · NOAA Open Data Dissemination": "GOES",
  "NOAA/NWS Storm Prediction Center": "SPC",
};
const REQUEST_RECOVERY = "Check your connection, then retry. If the provider is unavailable, try again later.";
const RESTART_RECOVERY = "Restart the backend from the connection status control, then retry.";
const GUIDANCE: Readonly<Record<DiagnosticCode, readonly [string, string]>> = {
  PYTHON_NOT_FOUND: ["Configuration", "Check the Python backend setup or repair the application installation."],
  BRIDGE_SPAWN_FAILED: ["Backend startup", RESTART_RECOVERY],
  BRIDGE_TIMEOUT: ["Timeout", "Retry the operation. If it keeps timing out, restart the backend."],
  BRIDGE_CRASHED: ["Backend connection", RESTART_RECOVERY],
  BRIDGE_UNAVAILABLE: ["Backend connection", RESTART_RECOVERY],
  BRIDGE_PROTOCOL: ["Backend protocol", "Restart the backend. If the problem continues, check that the app and backend versions match."],
  METHOD_NOT_FOUND: ["Compatibility", "Check that the app and backend versions match."],
  INVALID_ARGUMENT: ["Invalid input", "Review the selected inputs before trying again."],
  CATALOG_UNAVAILABLE: ["Data availability", REQUEST_RECOVERY],
  STATION_LIST_UNAVAILABLE: ["Data availability", REQUEST_RECOVERY],
  BACKEND_UNAVAILABLE: ["Backend configuration", "Check the scientific backend configuration, then restart the backend and retry."],
  BACKEND_CONFIG_INVALID: ["Backend configuration", "Correct the backend configuration before trying again."],
  INTERNAL: ["Internal error", "Retry the operation. If it keeps failing, restart the app and share this summary."],
  SOUNDING_UNAVAILABLE: ["Data availability", "Try another time, station, or provider."],
  SOUNDING_FETCH_FAILED: ["Provider request", REQUEST_RECOVERY],
  SOUNDING_INVALID: ["Invalid data", "Try another sounding source or time."],
  SOUNDING_NOT_FOUND: ["Session data", "Reopen the sounding before trying again."],
  RENDER_FAILED: ["Rendering", "Retry rendering. If it keeps failing, reopen the sounding."],
  FIELD_UNAVAILABLE: ["Data availability", "Try another model, run, forecast hour, or product."],
  FIELD_FETCH_FAILED: ["Provider request", REQUEST_RECOVERY],
  OVERLAY_FETCH_FAILED: ["Provider request", REQUEST_RECOVERY],
  CONTRACT_VIOLATION: ["Compatibility", "Check that the app and backend versions match. Restart the app after updating."],
  TRANSPORT_FAILED: ["Backend connection", RESTART_RECOVERY],
  APPLICATION_ERROR: ["Application", "Retry the operation. If it keeps failing, restart the app and share this summary."],
  WINDOW_FAILED: ["Window control", "Try changing fullscreen again. If it keeps failing, restart the app."],
  MAP_RENDER_FAILED: ["Map rendering", "Try the map again or choose a lower graphics profile in Settings."],
  UPDATE_FAILED: ["Application update", "Check your connection and try the update again. If it keeps failing, check for a newer release."],
  UNKNOWN_ERROR: ["Unknown", "Retry the operation. If it keeps failing, restart the app and share this summary."],
};
const FALLBACKS: Readonly<Record<string, DiagnosticMetadata>> = {
  Application: { operation: "Application", provider: "Frontend", code: "APPLICATION_ERROR" },
  Window: { operation: "Window control", provider: "Frontend", code: "WINDOW_FAILED" },
  "Map renderer": { operation: "Map rendering", provider: "Frontend", code: "MAP_RENDER_FAILED" },
  updater: { operation: "Application update", provider: "Updater", code: "UPDATE_FAILED" },
};
const UNKNOWN: DiagnosticMetadata = { operation: "Unknown operation", provider: "Not recorded", code: "UNKNOWN_ERROR" };
const FRONTEND_OPERATIONS = new Set(Object.values(FALLBACKS).map(({ operation }) => operation));

/** Context strings cross a trust boundary too; identifier-shaped secrets are still secrets. */
export function diagnosticMetadata(source: string, input: DiagnosticInput = {}): DiagnosticMetadata {
  const fallback = (Object.hasOwn(FALLBACKS, source) ? FALLBACKS[source] : /^Map panel [1-9]\d*$/u.test(source) ? FALLBACKS["Map renderer"] : undefined) ?? UNKNOWN;
  const operation = typeof input.operation === "string" && (OPERATIONS.has(input.operation) || FRONTEND_OPERATIONS.has(input.operation))
    ? input.operation : OPERATIONS.has(source) ? source : fallback.operation;
  const provider = typeof input.provider === "string" ? Object.hasOwn(PROVIDERS, input.provider) ? input.provider
    : Object.hasOwn(PROVIDER_ALIASES, input.provider) ? PROVIDER_ALIASES[input.provider] : undefined : undefined;
  return {
    operation,
    provider: provider ?? fallback.provider,
    code: typeof input.code === "string" && Object.hasOwn(GUIDANCE, input.code) ? input.code as DiagnosticCode : fallback.code,
  };
}

/** Copy only allowlisted metadata and fixed guidance, never raw messages or resource context. */
export function formatDiagnosticSummary(time: string, input?: DiagnosticInput): string {
  const { operation, provider, code } = diagnosticMetadata("", input);
  const [category, recovery] = GUIDANCE[code];
  const providerName = (Object.hasOwn(PROVIDERS, provider) ? PROVIDERS[provider] : undefined) ?? provider;
  const date = new Date(time);
  return [
    "SHARPpy Reimagined diagnostic summary",
    `Failed operation: ${operation}`,
    `Provider: ${providerName === provider ? provider : `${providerName} (${provider})`}`,
    `Time (UTC): ${Number.isNaN(date.getTime()) ? "Not recorded" : date.toISOString()}`,
    `Error category: ${category}`,
    `Error code: ${code}`,
    `Suggested recovery: ${recovery}`,
  ].join("\n");
}
