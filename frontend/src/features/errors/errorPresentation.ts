// How a backend error is presented (DESIGN-contracts C6): the notice title
// from the operation, the muted provider and resource line, and the action
// each code calls for. Presentation only: the message itself is composed by
// the producer and shown as it is.

import type { AnyBackendError, ErrorContext } from "../../types/contracts";

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
  TRANSPORT_FAILED: NONE,
};

export function errorActions(code: AnyBackendError["code"]): ErrorActions {
  return ACTIONS[code];
}

/**
 * REQUEST §27 asks every error to say whether retrying is possible. With a
 * Retry or Restart button the button says it; without one, this line does.
 */
export function noRetryHint(actions: ErrorActions): string | null {
  if (actions.retry || actions.restart) {
    return null;
  }
  return actions.configure ? "Retrying will not help until this is fixed." : "Retrying will not help.";
}

const TITLES: Readonly<Record<string, string>> = {
  get_station_catalog: "Stations could not be loaded",
  get_backend_health: "The backend status could not be checked",
  restart_backend: "The backend could not be restarted",
  start_backend: "The Python backend could not start",
  get_model_catalog: "Forecast models could not be loaded",
  open_sounding: "The sounding could not be opened",
  get_sounding_analysis: "The sounding parameters could not be computed",
  get_ensemble_summary: "The ensemble summary could not be computed",
  get_threshold_metrics: "The threshold diagnostics could not be loaded",
  evaluate_threshold: "The threshold could not be evaluated",
  export_threshold_csv: "The threshold results could not be exported",
  render_sounding: "The sounding image could not be drawn",
  get_map_catalog: "Map products could not be loaded",
  get_weather_field: "The model field could not be loaded",
  get_spc_outlook: "The SPC outlook could not be loaded",
  get_radar_frame: "The radar mosaic could not be loaded",
  get_place_labels: "Place names could not be loaded",
  get_station_availability: "Station availability could not be checked",
};

/** "Stations could not be loaded" for get_station_catalog. */
export function errorTitle(context: ErrorContext | undefined): string {
  return (context === undefined ? undefined : TITLES[context.operation]) ?? "A backend request failed";
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
