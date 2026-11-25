import { hashKey, type QueryClient, type QueryKey } from "@tanstack/react-query";
import type { BackendClient } from "../../lib/backend/client";
import { BackendFailure, overlayAvailability } from "../../lib/backend/errors";
import { BASE_QUERY_OPTIONS, mapKeys, RADAR_HISTORY_STALE_MS } from "../../lib/backend/queryOptions";
import { panelLayers, panelReports, panelWeather, type MapState } from "../../stores/mapStore";
import type { RadarSite } from "../../types/map";
import { panelRadar } from "../../map/panelRadar";

export interface LiveRequest {
  queryKey: QueryKey;
  queryFn: () => Promise<unknown>;
}

/** An uncached past alert snapshot cannot appear on a later playback loop. */
export function fetchLiveRequest(queryClient: QueryClient, request: LiveRequest, refresh = false): Promise<unknown> {
  const previous = queryClient.getQueryState(request.queryKey);
  if (previous?.error instanceof BackendFailure && overlayAvailability(previous.error.error) === "historyUnavailable") return Promise.reject(previous.error);
  const radar = request.queryKey[0] === "radar" || request.queryKey[0] === "radar-site";
  const staleTime = refresh || radar && !isLiveQuery(request.queryKey) ? 0 : radar ? RADAR_HISTORY_STALE_MS : Number.POSITIVE_INFINITY;
  return queryClient.query({ ...BASE_QUERY_OPTIONS, ...request, staleTime,
    gcTime: Number.POSITIVE_INFINITY, retry: false });
}

/** Use the same source addresses as the displayed layers. */
export function liveRequests(client: BackendClient, state: MapState, site: string | null, time: string, sites: readonly RadarSite[] = [], followingLive = false): LiveRequest[] {
  const requests: LiveRequest[] = [];
  const liveState: MapState = { ...state, view: "live" };
  const radarTime = followingLive ? null : time;
  for (let panel = 0; panel < state.panelLayout; panel++) {
    const layers = panelLayers(liveState, panel);
    const weather = panelWeather(liveState, panel);
    const reports = panelReports(liveState, panel);
    const { satelliteChannel: channel, radarScope } = weather;
    const { site: selectedSite, product, tilt, supportedProducts } = panelRadar(weather, site, sites);
    if (layers.radar.visible) {
      if (radarScope === "site" && selectedSite !== null && supportedProducts?.length !== 0) requests.push({ queryKey: mapKeys.radarSite(product, selectedSite, radarTime, tilt), queryFn: () => client.getRadarSiteFrame({ site: selectedSite }, product, radarTime, tilt) });
      if (radarScope === "mosaic") requests.push({ queryKey: mapKeys.radar(product, radarTime), queryFn: () => client.getRadarFrame(product, radarTime) });
    }
    if (layers.satellite.visible) requests.push({ queryKey: mapKeys.satellite(channel, time), queryFn: () => client.getSatelliteFrame(time, channel) });
    if (layers.warnings.visible || layers.watches.visible) requests.push({ queryKey: mapKeys.alerts(time), queryFn: () => client.getWeatherAlerts(time) });
    if (layers["storm-reports"].visible) requests.push({ queryKey: mapKeys.reports(time, reports.reportWindowS, reports.reportFilter), queryFn: () => client.getStormReports(time, reports.reportWindowS, reports.reportFilter) });
  }
  return [...new Map(requests.map((request) => [hashKey(request.queryKey), request])).values()];
}

const LIVE_PREFIXES = new Set(["radar", "radar-site", "satellite", "weather-alerts", "reports"]);
export function isLiveQuery(key: QueryKey): boolean {
  return typeof key[0] === "string" && LIVE_PREFIXES.has(key[0]) && key.some((part) => typeof part === "string" && /^\d{4}-\d{2}-\d{2}T/u.test(part));
}

/** Retain the compressed hour for playback; decoded bitmap memory is bounded separately. */
export function trimLiveCache(client: QueryClient, budgetBytes = 128 * 1024 * 1024, keep: readonly QueryKey[] = []): void {
  const prepared = new Set(keep.map((key) => hashKey(key)));
  const retained = (query: { queryHash: string; getObserversCount: () => number; state: { fetchStatus: string } }): boolean =>
    prepared.has(query.queryHash) || query.getObserversCount() > 0 || query.state.fetchStatus === "fetching";
  const queries = client.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) })
    .sort((a, b) => Number(retained(b)) - Number(retained(a)) ||
      Math.max(b.state.dataUpdatedAt, b.state.errorUpdatedAt) - Math.max(a.state.dataUpdatedAt, a.state.errorUpdatedAt));
  let bytes = 0, count = 0;
  for (const query of queries) {
    const data = query.state.data as { png?: unknown } | undefined;
    const size = data?.png instanceof Blob ? data.png.size : 0;
    if (!retained(query) && (bytes + size > budgetBytes || count >= 128)) {
      client.removeQueries({ queryKey: query.queryKey, exact: true });
      continue;
    }
    bytes += size;
    count++;
  }
}
