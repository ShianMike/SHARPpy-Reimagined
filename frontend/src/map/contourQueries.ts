// Contour-line queries (DESIGN-frontend F3). A level set never changes once
// published, so a product's lines at a run and hour are cached like a field
// frame and split out of mapQueries to keep each module within the size rule.

import { useQuery, useQueryClient, type QueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import type { BackendClient } from "../lib/backend/client";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import type { ContourLines } from "../types/map";
import { profileSettings } from "../lib/graphics/profileSettings";
import { useGraphicsStore } from "../stores/graphicsStore";
import { trimFieldCache, type FrameRequest } from "./mapQueries";

export function contourQueryOptions(client: BackendClient, queryClient: QueryClient, request: FrameRequest) {
  return {
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.contours(request.product, request.runTime, request.forecastHour, request.model),
    queryFn: async () => {
      const lines = await client.getFieldContours(request.product, request.runTime, request.forecastHour, request.model);
      const budget = profileSettings(useGraphicsStore.getState().effective.profile).cpuFieldCacheMb;
      trimFieldCache(queryClient, budget * 1024 * 1024, lines);
      return lines;
    },
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    structuralSharing: false,
  } as const;
}

/**
 * The product's contour lines at one run and hour. A level set never changes
 * once published, so it is cached for the session like a field frame.
 */
export function useFieldContours(
  product: string | null,
  runTime: string | null,
  forecastHour: number | null,
  enabled: boolean,
  model = "hrrr",
): UseQueryResult<ContourLines> {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  return useQuery({
    ...contourQueryOptions(client, queryClient, { product: product ?? "", runTime: runTime ?? "", forecastHour: forecastHour ?? 0, model }),
    enabled: enabled && product !== null && runTime !== null && forecastHour !== null,
  });
}
