import type { QueryClient, QueryKey } from "@tanstack/react-query";
import type { BackendClient } from "../../lib/backend/client";
import { BackendFailure, overlayAvailability } from "../../lib/backend/errors";
import { BASE_QUERY_OPTIONS, mapKeys } from "../../lib/backend/queryOptions";
import { panelLayers, panelWeather, type MapState } from "../../stores/mapStore";
import type { SurfaceAnchor } from "../../map/surfaceQueries";
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
  return queryClient.query({ ...BASE_QUERY_OPTIONS, ...request, staleTime: refresh ? 0 : Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY, retry: false });
}

/** Use the same source addresses as the displayed layers. */
export function liveRequests(client: BackendClient, state: MapState, site: string | null, anchor: SurfaceAnchor | null, time: string, sites: readonly RadarSite[] = [], panel?: number): LiveRequest[] {
  if (panel === undefined) {
    const requests = Array.from({ length: state.panelLayout }, (_, index) => liveRequests(client, state, site, anchor, time, sites, index)).flat();
    return [...new Map(requests.map((request) => [JSON.stringify(request.queryKey), request])).values()];
  }
  const requests: LiveRequest[] = [];
  const layers = panelLayers({ ...state, view: "live" }, panel);
  const weather = state.livePanels[panel] ?? panelWeather(state, panel);
  const { satelliteChannel: channel, radarScope } = weather;
  const { site: selectedSite, product, tilt, supportedProducts } = panelRadar(weather, site, sites);
  if (layers.radar.visible) {
    if (radarScope === "site" && selectedSite !== null && supportedProducts?.length !== 0) requests.push({ queryKey: mapKeys.radarSite(product, selectedSite, time, tilt), queryFn: () => client.getRadarSiteFrame({ site: selectedSite }, product, time, tilt) });
    if (radarScope === "mosaic") requests.push({ queryKey: mapKeys.radar(product, time), queryFn: () => client.getRadarFrame(product, time) });
  }
  if (layers.satellite.visible) requests.push({ queryKey: mapKeys.satellite(channel, time), queryFn: () => client.getSatelliteFrame(time, channel) });
  if (layers.warnings.visible || layers.watches.visible) requests.push({ queryKey: mapKeys.alerts(time), queryFn: () => client.getWeatherAlerts(time) });
  if (layers["storm-reports"].visible) requests.push({ queryKey: mapKeys.reports(time, state.reportWindowS, state.reportFilter), queryFn: () => client.getStormReports(time, state.reportWindowS, state.reportFilter) });
  if (layers["surface-plots"].visible && anchor !== null) requests.push({ queryKey: mapKeys.surface(anchor.latitude, anchor.longitude, time), queryFn: () => client.getSurfacePlots(anchor.latitude, anchor.longitude, time) });
  return requests;
}

const LIVE_PREFIXES = new Set(["radar", "radar-site", "satellite", "weather-alerts", "surface", "reports"]);
export function isLiveQuery(key: QueryKey): boolean {
  return typeof key[0] === "string" && LIVE_PREFIXES.has(key[0]) && key.some((part) => typeof part === "string" && /^\d{4}-\d{2}-\d{2}T/u.test(part));
}

/** Retain the compressed hour for playback; decoded bitmap memory is bounded separately. */
export function trimLiveCache(client: QueryClient, budgetBytes = 128 * 1024 * 1024): void {
  const queries = client.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) })
    .sort((a, b) => Math.max(b.state.dataUpdatedAt, b.state.errorUpdatedAt) - Math.max(a.state.dataUpdatedAt, a.state.errorUpdatedAt));
  let bytes = 0, count = 0;
  for (const query of queries) {
    const data = query.state.data as { png?: unknown } | undefined;
    const size = data?.png instanceof Blob ? data.png.size : 0;
    if ((bytes + size > budgetBytes || count >= 128) && query.getObserversCount() === 0 && query.state.fetchStatus !== "fetching") client.removeQueries({ queryKey: query.queryKey, exact: true });
    else { bytes += size; count++; }
  }
}
