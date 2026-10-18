import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import type { BackendClient } from "../lib/backend/client";

/** Metadata only: share checks across panels, menus and the tilt preloader. */
export function radarTiltQuery(client: BackendClient, site: string, product: string, time: string | null = null) {
  return { ...BASE_QUERY_OPTIONS, queryKey: mapKeys.radarTilts(product, site, time), queryFn: () => client.getRadarTilts(site, product, time), staleTime: 45_000, gcTime: 5 * 60_000, retry: false as const };
}
