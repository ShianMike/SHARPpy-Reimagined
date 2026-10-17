// The station catalogue query (DESIGN-frontend F3, PLAN P17). With an
// observation time chosen in the layer panel (otherwise the observed launch),
// every view sees the catalogue marked against its live UWyo list. The bundled
// list remains usable while the availability check loads or fails.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, QUERY_KEYS } from "../lib/backend/queryOptions";
import { useStationAvailability } from "../map/mapQueries";
import { useMapStore } from "../stores/mapStore";
import type { StationCatalog } from "../types/contracts";
import { withAvailability } from "./availability";

/**
 * Loaded once and kept (staleTime Infinity): it refetches only on Retry,
 * Restart backend, or the bridge returning to up after a failed load.
 */
export function useStationCatalog(): UseQueryResult<StationCatalog> {
  const client = useBackendClient();
  const report = useStationAvailability(useMapStore((state) => state.availabilityTime)).data;
  const select = useMemo(
    () => (report === undefined ? undefined : (catalog: StationCatalog) => withAvailability(catalog, report)),
    [report],
  );
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: QUERY_KEYS.catalog,
    queryFn: () => client.getStationCatalog(),
    staleTime: Number.POSITIVE_INFINITY,
    // A failed load is not repeated just because another component (the lazy
    // map) mounts an observer; only Retry, Restart backend, or the bridge
    // returning to up refetch it.
    retryOnMount: false,
    ...(select === undefined ? {} : { select }),
  });
}
