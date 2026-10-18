// The transport abstraction (DESIGN-frontend F2). Implementations only move raw
// values: Tauri in the desktop app, HTTP in live browser development, and
// fixtures in offline builds and tests. BackendClient validates every response.

export type CommandName =
  | "get_storage_usage"
  | "clear_cache"
  | "cancel_request"
  | "get_backend_health"
  | "get_station_catalog"
  | "restart_backend"
  | "get_model_catalog"
  | "get_model_status"
  | "open_sounding"
  | "get_sounding_progress"
  | "inspect_wrf"
  | "get_sounding_analysis"
  | "get_sounding_trends"
  | "get_threshold_metrics"
  | "evaluate_threshold"
  | "export_threshold_csv"
  | "render_sounding"
  | "render_sounding_view"
  | "get_map_catalog"
  | "get_weather_field"
  | "get_cached_forecast_hours"
  | "get_spc_outlook"
  | "get_weather_alerts"
  | "get_radar_frame"
  | "get_radar_site_frame"
  | "get_radar_sites"
  | "get_radar_tilts"
  | "get_satellite_frame"
  | "get_storm_reports"
  | "get_report_options"
  | "get_field_contours"
  | "get_surface_plots"
  | "get_place_labels"
  | "get_station_availability";
export type EventName = "bridge-status-changed";

export interface Transport {
  /** Resolves with the raw response, or rejects with the raw rejection value. */
  call(command: CommandName, args?: Readonly<Record<string, unknown>>): Promise<unknown>;
  /** Resolves with an unlisten function once the handler is registered. */
  listen(event: EventName, handler: (payload: unknown) => void): Promise<() => void>;
}

/** Every transport exports this factory, so main.tsx can pick one by build mode. */
export type TransportFactory = () => Transport;

/** Abort only this operation; the bridge cooperatively stops its worker. */
export async function callWithSignal(transport: Transport, command: CommandName, args: Readonly<Record<string, unknown>> | undefined, signal?: AbortSignal): Promise<unknown> {
  if (signal === undefined) return transport.call(command, args);
  signal.throwIfAborted();
  const requestId = crypto.randomUUID().replaceAll("-", "");
  let abort = (): void => undefined;
  const stopped = new Promise<never>((_resolve, reject) => {
    abort = () => {
      void transport.call("cancel_request", { requestId }).catch(() => undefined);
      reject(signal.reason instanceof Error ? signal.reason : new DOMException("Request cancelled", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
  try { return await Promise.race([transport.call(command, { ...args, requestId }), stopped]); }
  finally { signal.removeEventListener("abort", abort); }
}
