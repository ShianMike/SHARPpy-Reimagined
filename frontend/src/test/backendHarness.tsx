// Test support: renders a tree inside a fresh QueryClient and BackendProvider
// over a given transport (usually the fixture transport, imported by the test),
// and a scripted transport for replies the fixtures do not cover.

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import { BackendClient } from "../lib/backend/client";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { createQueryClient } from "../lib/backend/queryOptions";
import type { CommandName, Transport } from "../lib/backend/transport";

export interface BackendHarness {
  view: RenderResult;
  queryClient: QueryClient;
}

export function renderWithBackend(tree: ReactNode, transport: Transport): BackendHarness {
  const queryClient = createQueryClient();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <BackendProvider client={new BackendClient(transport)}>{tree}</BackendProvider>
    </QueryClientProvider>,
  );
  return { view, queryClient };
}

/** A reply: a value to resolve with, or { reject } to reject with the raw value. */
export type ScriptedReply = unknown;

/**
 * A transport that answers each command from a list of replies (the last one
 * repeats). A reply of the form { reject: value } rejects with value.
 */
export function scriptedTransport(replies: Partial<Record<CommandName, ScriptedReply[]>>): Transport & {
  calls: (command: CommandName) => number;
} {
  const counts: Record<CommandName, number> = {
    get_storage_usage: 0,
    clear_cache: 0,
    get_backend_health: 0,
    get_station_catalog: 0,
    restart_backend: 0,
    get_model_catalog: 0,
    get_model_status: 0,
    open_sounding: 0,
    get_sounding_progress: 0,
    inspect_wrf: 0,
    get_sounding_analysis: 0,
    get_sounding_trends: 0,
    get_threshold_metrics: 0,
    evaluate_threshold: 0,
    export_threshold_csv: 0,
    render_sounding: 0,
    render_sounding_view: 0,
    get_map_catalog: 0,
    get_weather_field: 0,
    get_spc_outlook: 0,
    get_weather_alerts: 0,
    get_radar_frame: 0,
    get_radar_site_frame: 0,
    get_radar_sites: 0,
    get_radar_tilts: 0,
    get_satellite_frame: 0,
    get_storm_reports: 0,
    get_report_options: 0,
    get_field_contours: 0,
    get_surface_plots: 0,
    get_place_labels: 0,
    get_station_availability: 0,
  };
  return {
    calls: (command) => counts[command],
    call: (command) => {
      const list = replies[command] ?? [];
      const reply: unknown = list[Math.min(counts[command], list.length - 1)];
      counts[command] += 1;
      if (typeof reply === "object" && reply !== null && "reject" in reply) {
        // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- the contract is a plain object
        return Promise.reject(reply.reject);
      }
      return Promise.resolve(reply);
    },
    listen: () => Promise.resolve(() => undefined),
  };
}
