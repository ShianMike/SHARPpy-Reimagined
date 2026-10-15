// Map data queries (DESIGN-frontend F3). A field frame for one product, run,
// and hour never changes, so it is cached for the session up to the profile's
// field budget (oldest unused frames go first); the catalogue and the place
// names are static; outlooks go stale in ten minutes; the radar mosaic
// refetches at the service's update interval.

import { useQueries, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import type { BackendClient } from "../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { profileSettings } from "../lib/graphics/profileSettings";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useMapStore } from "../stores/mapStore";
import { useForecastStore } from "../stores/forecastStore";
import { OUTLOOK_KEYS, type AvailabilityReport, type ContourLines, type FieldFrame, type MapCatalog, type PlaceLabels, type RadarFrame, type RadarSiteCatalog, type RadarSiteFrame, type ReportOptions, type SatelliteChannel, type SatelliteFrame, type SpcOutlook, type StormReports } from "../types/map";
import { nearestRadarSite } from "./nearestSite";

const OUTLOOK_STALE_MS = 10 * 60_000;
const AVAILABILITY_STALE_MS = 10 * 60_000;
/** Used until the first radar frame states its own update interval. */
const RADAR_DEFAULT_REFRESH_MS = 120_000;

export function useMapCatalog(): UseQueryResult<MapCatalog> {
  const client = useBackendClient();
  const model = useForecastStore((state) => state.modelKey) ?? "hrrr";
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.catalog(model),
    queryFn: () => client.getMapCatalog(model),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** One frame to fetch: a product at a run and hour. */
export interface FrameRequest {
  model: string;
  product: string;
  runTime: string;
  forecastHour: number;
}

/** Drops the oldest field frames no view observes, until the cache is within `budgetBytes`. */
export function trimFieldCache(queryClient: QueryClient, budgetBytes: number, incoming?: FieldFrame | ContourLines): void {
  // Estimate retained contour/point arrays alongside the compact winds and raster textures.
  const bytes = (frame: FieldFrame | ContourLines) => "cells" in frame ? 5 * frame.cells.byteLength
    : (frame.wind?.data.byteLength ?? 0) + frame.segments.length * 192;
  const queries = queryClient
    .getQueryCache()
    .findAll()
    .filter((query) => (query.queryKey[0] === "fields" || query.queryKey[0] === "contours") && query.state.data !== undefined)
    .sort((a, b) => a.state.dataUpdatedAt - b.state.dataUpdatedAt);
  // Budget raw uint16 cells plus both Sharp and Smooth RGBA images, including the arriving frame.
  let total = queries.reduce((sum, query) => sum + bytes(query.state.data as FieldFrame | ContourLines),
    incoming === undefined ? 0 : bytes(incoming));
  for (const query of queries) {
    if (total <= budgetBytes) {
      return;
    }
    if (query.getObserversCount() === 0) {
      total -= bytes(query.state.data as FieldFrame | ContourLines);
      queryClient.removeQueries({ queryKey: query.queryKey, exact: true });
    }
  }
}

export function fieldQueryOptions(client: BackendClient, queryClient: QueryClient, request: FrameRequest) {
  return {
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.field(request.product, request.runTime, request.forecastHour, request.model),
    queryFn: async () => {
      const frame = await client.getWeatherField(request.product, request.runTime, request.forecastHour, request.model);
      const budget = profileSettings(useGraphicsStore.getState().effective.profile).cpuFieldCacheMb;
      trimFieldCache(queryClient, budget * 1024 * 1024, frame);
      return frame;
    },
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
  } as const;
}

/** Only the requested product, run, and hour; no previous selection while loading. */
export function useFieldFrame(request: FrameRequest | null): UseQueryResult<FieldFrame> {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const fallback: FrameRequest = { model: "hrrr", product: "", runTime: "", forecastHour: 0 };
  return useQuery({
    ...fieldQueryOptions(client, queryClient, request ?? fallback),
    enabled: request !== null,
  });
}

/** Several frames at once (the timeline waits on every visible panel's frame). */
export function useFieldFrames(requests: readonly FrameRequest[]): UseQueryResult<FieldFrame>[] {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  return useQueries({ queries: requests.map((request) => fieldQueryOptions(client, queryClient, request)) });
}

/**
 * The time to ask for: when a loaded outlook already covers `validTime`, the
 * time it was asked for, so stepping the timeline through one outlook's
 * window fetches nothing new.
 */
export function coveringTime(
  cached: readonly (readonly [readonly unknown[], SpcOutlook | undefined])[],
  validTime: string,
): string {
  const t = Date.parse(validTime);
  for (const [queryKey, data] of cached) {
    const outlook = data?.outlook;
    const asked = queryKey[2];
    if (
      outlook?.covers === true &&
      outlook.validFrom !== null &&
      outlook.validTo !== null &&
      Date.parse(outlook.validFrom) <= t &&
      t < Date.parse(outlook.validTo) &&
      typeof asked === "string"
    ) {
      return asked;
    }
  }
  return validTime;
}

/** The four outlook products in OUTLOOK_KEYS order; `enabled[i]` turns each on. */
export function useSpcOutlooks(enabled: readonly boolean[], validTime: string | null): UseQueryResult<SpcOutlook>[] {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  return useQueries({
    queries: OUTLOOK_KEYS.map((key, i) => {
      const time = validTime === null ? "" : coveringTime(queryClient.getQueriesData<SpcOutlook>({ queryKey: ["outlooks", key] }), validTime);
      return {
        ...BASE_QUERY_OPTIONS,
        queryKey: mapKeys.outlook(key, time),
        queryFn: () => client.getSpcOutlook(key, time),
        enabled: enabled[i] === true && time !== "",
        staleTime: OUTLOOK_STALE_MS,
      };
    }),
  });
}

export function useRadarFrame(product: string | null, enabled: boolean): UseQueryResult<RadarFrame> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.radar(product),
    queryFn: () => client.getRadarFrame(product),
    enabled,
    refetchInterval: (query) => {
      const seconds = query.state.data?.updateIntervalS ?? 0;
      return seconds > 0 ? seconds * 1000 : RADAR_DEFAULT_REFRESH_MS;
    },
  });
}

/** The bundled WSR-88D catalogue; static for the life of the bridge. */
export function useRadarSites(enabled: boolean): UseQueryResult<RadarSiteCatalog> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.radarSites,
    queryFn: () => client.getRadarSites(),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/**
 * The antenna the single-site scope draws: the user's pinned choice, or the
 * nearest catalogue antenna to the map centre (the legacy controller's auto
 * pick). Null while the catalogue is not loaded; the queries then stay off.
 */
export function useEffectiveRadarSite(): string | null {
  const pinned = useMapStore((state) => state.radarSite);
  const camera = useMapStore((state) => state.cameraSnapshot);
  const sites = useRadarSites(true).data?.sites;
  return pinned ?? nearestRadarSite(sites ?? [], camera?.center[1] ?? 0, camera?.center[0] ?? 0);
}

/**
 * One antenna's own frame, refreshed at the product's cadence like the
 * mosaic. The site is fixed in the key, so a pinned antenna never refetches
 * on pan; the auto choice is resolved by `useEffectiveRadarSite`.
 */
export function useRadarSiteFrame(
  product: string | null,
  site: string | null,
  enabled: boolean,
): UseQueryResult<RadarSiteFrame> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.radarSite(product, site ?? ""),
    queryFn: () => client.getRadarSiteFrame({ site: site ?? "" }, product),
    enabled: enabled && site !== null,
    refetchInterval: (query) => {
      const seconds = query.state.data?.updateIntervalS ?? 0;
      return seconds > 0 ? seconds * 1000 : RADAR_DEFAULT_REFRESH_MS;
    },
  });
}

/**
 * The GOES frame for a channel and valid time. Time-addressed like the
 * outlooks: the frame's own scan time is within the provider's tolerance of
 * the requested one, and the frame states both.
 */
export function useSatelliteFrame(
  channel: SatelliteChannel,
  validTime: string | null,
  enabled: boolean,
): UseQueryResult<SatelliteFrame> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.satellite(channel, validTime ?? ""),
    queryFn: () => client.getSatelliteFrame(validTime ?? "", channel),
    enabled: enabled && validTime !== null,
    staleTime: OUTLOOK_STALE_MS,
  });
}

/**
 * Storm reports in a symmetric window of a valid time, after a hazard filter.
 * Time-addressed like the outlooks: the records state their own event times.
 */
export function useStormReports(
  validTime: string | null,
  windowS: number,
  hazardFilter: string,
  enabled: boolean,
): UseQueryResult<StormReports> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.reports(validTime ?? "", windowS, hazardFilter),
    queryFn: () => client.getStormReports(validTime ?? "", windowS, hazardFilter),
    enabled: enabled && validTime !== null,
    staleTime: OUTLOOK_STALE_MS,
  });
}

/** The provider's own window and filter labels; static for the bridge's life. */
export function useReportOptions(enabled: boolean): UseQueryResult<ReportOptions> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.reportOptions,
    queryFn: () => client.getReportOptions(),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function usePlaceLabels(enabled: boolean): UseQueryResult<PlaceLabels> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: QUERY_KEYS.places,
    queryFn: () => client.getPlaceLabels(),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useStationAvailability(observationTime: string | null): UseQueryResult<AvailabilityReport> {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.availability(observationTime ?? ""),
    queryFn: () => client.getStationAvailability(observationTime ?? ""),
    enabled: observationTime !== null,
    staleTime: AVAILABILITY_STALE_MS,
  });
}
