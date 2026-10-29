import type { QueryClient, QueryKey } from "@tanstack/react-query";
import type { BackendClient } from "../../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys } from "../../lib/backend/queryOptions";
import { panelLayers, panelWeather, type MapState } from "../../stores/mapStore";
import type { SurfaceAnchor } from "../../map/surfaceQueries";
import { RADAR_TILTABLE_PRODUCTS, RADAR_TILTS, type RadarSite, type RadarSiteCatalog, type RadarTilts } from "../../types/map";
import { radarTiltQuery } from "../../map/radarTiltQueries";
import { isAustralianRadarSite, panelRadar } from "../../map/panelRadar";

export interface LiveRequest {
  queryKey: QueryKey;
  queryFn: () => Promise<unknown>;
}

/** Use the same addresses as the displayed layers, keeping native source quality. */
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
  if (layers["surface-plots"].visible && anchor !== null) requests.push({ queryKey: mapKeys.surface(anchor.latitude, anchor.longitude, time), queryFn: () => client.getSurfacePlots(anchor.latitude, anchor.longitude, time) });
  return requests;
}

/** Warm native radar cuts after the displayed scans, two requests at a time. */
export async function preloadRadarTilts(client: BackendClient, queryClient: QueryClient, state: MapState, site: string | null, time: string, stopped: () => boolean): Promise<void> {
  const sites = queryClient.getQueryData<RadarSiteCatalog>(mapKeys.radarSites)?.sites ?? [];
  const current = liveRequests(client, state, site, null, time, sites).filter(({ queryKey }) => queryKey[0] === "radar-site" && !isAustralianRadarSite(String(queryKey[2])) && RADAR_TILTABLE_PRODUCTS.some((product) => product === queryKey[1]));
  if (current.length === 0 || stopped()) return;
  const fetch = (request: LiveRequest): Promise<unknown> => queryClient.query({ ...BASE_QUERY_OPTIONS, ...request, staleTime: Number.POSITIVE_INFINITY, gcTime: Number.POSITIVE_INFINITY, retry: false });
  await Promise.allSettled(current.map(fetch));
  if (stopped()) return;
  const products = [...new Set(["reflectivity", "velocity", ...current.map(({ queryKey }) => String(queryKey[1]))])];
  const inventories = [...new Map(current.flatMap(({ queryKey }) => products.filter((product) => {
    const supported = sites.find((site) => site.id === queryKey[2])?.products;
    return supported === undefined || supported.includes(product);
  }).map((product) => {
    const options = radarTiltQuery(client, String(queryKey[2]), product);
    return [JSON.stringify(options.queryKey), options] as const;
  }))).values()];
  for (let index = 0; index < inventories.length; index += 2) {
    if (stopped()) return;
    await Promise.allSettled(inventories.slice(index, index + 2).map((options) => queryClient.query(options)));
  }
  const requests: LiveRequest[] = [];
  for (let panel = 0; panel < state.panelLayout; panel++) {
    const weather = state.livePanels[panel];
    if (weather === undefined) continue;
    const { site: antenna, product: selected, tiltable, supportedProducts } = panelRadar(weather, site, sites);
    if (!tiltable || !weather.layers.radar.visible || antenna === null) continue;
    const product = selected ?? "reflectivity";
    for (const { value: tilt } of RADAR_TILTS) for (const kind of [product, ...products.filter((kind) => kind !== product)]) {
      if (supportedProducts !== undefined && !supportedProducts.includes(kind)) continue;
      const inventory = queryClient.getQueryData<RadarTilts>(mapKeys.radarTilts(kind, antenna));
      if (!inventory?.tilts.some((cut) => cut.tilt === tilt && cut.status === "available")) continue;
      requests.push({ queryKey: mapKeys.radarSite(kind, antenna, time, tilt), queryFn: () => client.getRadarSiteFrame({ site: antenna }, kind, time, tilt) });
    }
  }
  const displayed = new Set(current.map((request) => JSON.stringify(request.queryKey)));
  const unique = [...new Map(requests.map((request) => [JSON.stringify(request.queryKey), request])).values()].filter((request) => !displayed.has(JSON.stringify(request.queryKey)));
  for (let index = 0; index < unique.length; index += 2) {
    if (stopped()) return;
    await Promise.allSettled(unique.slice(index, index + 2).map(fetch));
    trimLiveCache(queryClient);
  }
}

const LIVE_PREFIXES = new Set(["radar", "radar-site", "satellite", "weather-alerts", "surface"]);
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
