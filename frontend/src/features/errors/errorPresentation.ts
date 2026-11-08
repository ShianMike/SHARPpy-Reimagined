// Operation names and recovery permissions shared by notices and service status.

import type { AnyBackendError, ErrorContext } from "../../types/contracts";
import type { CommandName } from "../../lib/backend/transport";
import { Activity, Archive, Bell, CloudDownload, CloudSun, Database, FileSearch, Layers, Map, MapPin, Radar, Satellite, Server, Wind, type LucideIcon } from "lucide-react";

export interface ErrorActions {
  /** Retry: repeating the same request may succeed. */
  retry: boolean;
  /** Restart backend: the bridge must be restarted by the user. */
  restart: boolean;
  /** The message is a configuration instruction; no button helps. */
  configure: boolean;
  /** Show the developer log path. */
  logPath: boolean;
}

const NONE: ErrorActions = { retry: false, restart: false, configure: false, logPath: false };

const ACTIONS: Readonly<Record<AnyBackendError["code"], ErrorActions>> = {
  PYTHON_NOT_FOUND: { ...NONE, configure: true },
  BRIDGE_SPAWN_FAILED: { ...NONE, restart: true },
  BRIDGE_TIMEOUT: { ...NONE, retry: true },
  BRIDGE_CRASHED: { ...NONE, retry: true },
  BRIDGE_UNAVAILABLE: { ...NONE, restart: true },
  BRIDGE_PROTOCOL: { ...NONE, restart: true, logPath: true },
  METHOD_NOT_FOUND: NONE,
  INVALID_ARGUMENT: NONE,
  CATALOG_UNAVAILABLE: { ...NONE, retry: true },
  STATION_LIST_UNAVAILABLE: { ...NONE, retry: true },
  BACKEND_UNAVAILABLE: NONE,
  BACKEND_CONFIG_INVALID: NONE,
  INTERNAL: { ...NONE, logPath: true },
  SOUNDING_UNAVAILABLE: NONE,
  SOUNDING_FETCH_FAILED: { ...NONE, retry: true },
  SOUNDING_INVALID: NONE,
  SOUNDING_NOT_FOUND: NONE,
  RENDER_FAILED: { ...NONE, retry: true, logPath: true },
  FIELD_UNAVAILABLE: NONE,
  FIELD_FETCH_FAILED: { ...NONE, retry: true },
  OVERLAY_FETCH_FAILED: { ...NONE, retry: true },
  CONTRACT_VIOLATION: { ...NONE, logPath: true },
  TRANSPORT_FAILED: { ...NONE, retry: true },
};

export function errorActions(code: AnyBackendError["code"]): ErrorActions {
  return ACTIONS[code];
}

const TITLES: Readonly<Record<string, string>> = {
  get_cached_forecast_hours: "Saved forecast hours unavailable",
  cancel_request: "Request is still running",
  get_radar_tilts: "Radar elevations unavailable",
  get_storage_usage: "Cache usage unavailable",
  clear_cache: "Cache could not be cleared",
  get_station_catalog: "Stations unavailable",
  get_backend_health: "Service status unavailable",
  restart_backend: "Service restart failed",
  start_backend: "Weather service could not start",
  get_model_catalog: "Forecast models unavailable",
  get_model_status: "Model availability unknown",
  open_sounding: "Sounding unavailable",
  get_sounding_progress: "Download progress unavailable",
  inspect_wrf: "WRF file could not be read",
  get_sounding_analysis: "Sounding calculations unavailable",
  get_sounding_trends: "Sounding trends unavailable",
  get_threshold_metrics: "Threshold diagnostics unavailable",
  evaluate_threshold: "Threshold calculation failed",
  export_threshold_csv: "Threshold export failed",
  render_sounding: "Sounding image unavailable",
  render_sounding_view: "Sounding image unavailable",
  get_map_catalog: "Map parameters unavailable",
  get_weather_field: "Forecast field unavailable",
  get_spc_outlook: "SPC outlook unavailable",
  get_weather_alerts: "Warnings and watches unavailable",
  get_radar_frame: "Radar mosaic unavailable",
  get_radar_sites: "Radar sites unavailable",
  get_radar_site_frame: "Radar image unavailable",
  get_satellite_frame: "Satellite imagery unavailable",
  get_storm_reports: "Storm reports unavailable",
  get_report_options: "Report dates unavailable",
  get_field_contours: "Map contours unavailable",
  get_surface_plots: "Surface observations unavailable",
  get_place_labels: "Place names unavailable",
  get_station_availability: "Station availability unknown",
} satisfies Record<CommandName | "start_backend", string>;

export function errorTitle(context: ErrorContext | undefined): string {
  return (context !== undefined && Object.hasOwn(TITLES, context.operation) ? TITLES[context.operation] : undefined) ?? "Weather request failed";
}

const RECOVERY: Partial<Record<CommandName, readonly [string, LucideIcon]>> = {
  get_cached_forecast_hours: ["Check saved hours", Archive],
  cancel_request: ["Cancel request", Activity],
  get_radar_tilts: ["Check elevations", Radar],
  get_storage_usage: ["Refresh usage", Database],
  clear_cache: ["Clear cache again", Database],
  get_station_catalog: ["Reload stations", MapPin],
  get_backend_health: ["Check connection", Server],
  get_model_catalog: ["Reload models", CloudSun],
  get_model_status: ["Check model", CloudSun],
  open_sounding: ["Try again", CloudDownload],
  inspect_wrf: ["Read file again", FileSearch],
  get_sounding_analysis: ["Recalculate", Activity],
  get_sounding_trends: ["Reload trends", Activity],
  render_sounding: ["Redraw sounding", Activity],
  render_sounding_view: ["Redraw sounding", Activity],
  get_map_catalog: ["Reload parameters", Layers],
  get_weather_field: ["Retry field", Layers],
  get_spc_outlook: ["Retry outlook", Map],
  get_weather_alerts: ["Retry alerts", Bell],
  get_radar_frame: ["Retry radar", Radar],
  get_radar_site_frame: ["Retry radar", Radar],
  get_radar_sites: ["Reload radar sites", Radar],
  get_satellite_frame: ["Retry imagery", Satellite],
  get_storm_reports: ["Retry reports", MapPin],
  get_report_options: ["Reload dates", MapPin],
  get_field_contours: ["Retry contours", Wind],
  get_surface_plots: ["Retry observations", Wind],
  get_place_labels: ["Retry place names", MapPin],
  get_station_availability: ["Check station", MapPin],
};

export function errorRecovery(context: ErrorContext | undefined): readonly [string, LucideIcon] {
  return (context !== undefined && Object.hasOwn(RECOVERY, context.operation)
    ? RECOVERY[context.operation as CommandName] : undefined) ?? ["Try again", Server];
}

const PROVIDERS: Readonly<Record<string, string>> = {
  "python-bridge": "Python backend",
  rust: "Rust backend",
};

/** "Python backend, bundled UWyo station catalogue": what was involved, or null. */
export function errorSourceLine(context: ErrorContext | undefined): string | null {
  if (context === undefined) {
    return null;
  }
  const parts: string[] = [];
  if (context.provider !== undefined) {
    parts.push(PROVIDERS[context.provider] ?? context.provider);
  }
  if (context.resource !== undefined) {
    parts.push(context.resource);
  }
  return parts.length === 0 ? null : parts.join(", ");
}
