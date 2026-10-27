// Query keys and the options every backend query shares (DESIGN-frontend F3).

import { QueryClient } from "@tanstack/react-query";
import type { ThresholdEvaluationRequest } from "../../types/thresholds";
import type { RadarTilt } from "../../types/map";
import { BackendFailure } from "./errors";

export const QUERY_KEYS = {
  storage: ["storage", "usage"],
  health: ["backend", "health"],
  catalog: ["stations", "catalog"],
  models: ["models", "catalog"],
  mapCatalog: ["map", "catalog"],
  places: ["map", "places"],
} as const;

/** Map data queries; a field frame for one run and hour never changes. */
export const mapKeys = {
  catalog: (model: string, runTime?: string, forecastHour?: number) => [...QUERY_KEYS.mapCatalog, model, ...(runTime === undefined ? [] : [runTime, forecastHour])] as const,
  field: (product: string, runTime: string, hour: number, model = "hrrr") => ["fields", product, runTime, hour, model] as const,
  outlook: (product: string, validTime: string) => ["outlooks", product, validTime] as const,
  alerts: (validTime: string) => ["weather-alerts", validTime] as const,
  radar: (product: string | null, time: string | null = null) => ["radar", product ?? "default", ...(time === null ? [] : [time])] as const,
  radarSites: ["radar", "sites"] as const,
  radarTilts: (product: string, site: string, time: string | null = null) => ["radar-tilts", product, site, time] as const,
  radarSite: (product: string | null, site: string, time: string | null = null, tilt: RadarTilt = "0") => ["radar-site", product ?? "reflectivity", site, ...(time === null ? [] : [time]), tilt] as const,
  satellite: (channel: string, validTime: string) => ["satellite", channel, validTime] as const,
  reports: (validTime: string, windowS: number, hazardFilter: string) =>
    ["reports", validTime, windowS, hazardFilter] as const,
  reportOptions: ["reports", "options"] as const,
  contours: (product: string, runTime: string, forecastHour: number, model = "hrrr") =>
    ["contours", product, runTime, forecastHour, model] as const,
  surface: (latitude: number, longitude: number, validTime: string) =>
    ["surface", latitude, longitude, validTime] as const,
  availability: (observationTime: string) => ["stations", "availability", observationTime] as const,
};

/** Per-sounding queries; a sounding's id never changes, so its data never goes stale. */
export const soundingKeys = {
  analysis: (soundingId: string) => ["soundings", soundingId, "analysis"] as const,
  trends: (soundingId: string) => ["soundings", soundingId, "trends"] as const,
  image: (soundingId: string, choice: string) => ["soundings", soundingId, "image", choice] as const,
};

export const thresholdKeys = {
  metrics: ["threshold", "metrics"] as const,
  evaluation: (request: ThresholdEvaluationRequest) =>
    [
      "threshold",
      request.model,
      request.runTime,
      request.forecastHour,
      request.latitude,
      request.longitude,
      request.members.join(","),
      request.definition.name,
      request.definition.conditions
        .map((condition) => `${condition.metric}${condition.operator}${String(condition.value)}`)
        .join(","),
    ] as const,
};

/** Automatic retries for a retryable BackendError: at most two, after 1 s and 2 s. */
export const MAX_RETRIES = 2;

export function shouldRetry(failureCount: number, error: Error): boolean {
  return failureCount < MAX_RETRIES && error instanceof BackendFailure && error.error.retryable;
}

export function retryDelayMs(failureCount: number): number {
  return 1000 * 2 ** failureCount;
}

/** Health polls every second while the bridge is starting or restarting. */
export const TRANSITIONAL_POLL_MS = 1000;
/** Health polls every 5 s for the session when the status subscription failed. */
export const FALLBACK_POLL_MS = 5000;

/**
 * Shared by every backend query. IPC is not network, so queries never pause
 * offline; focus refetches would only add load.
 */
export const BASE_QUERY_OPTIONS = {
  networkMode: "always",
  refetchOnWindowFocus: false,
  retry: shouldRetry,
  retryDelay: retryDelayMs,
} as const;

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: BASE_QUERY_OPTIONS } });
}
