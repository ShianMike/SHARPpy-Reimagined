import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { failureOf, overlayAvailability } from "../lib/backend/errors";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";

export function useWeatherAlerts(validTime: string | null, enabled: boolean) {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const queryKey = mapKeys.alerts(validTime ?? "");
  const previous = failureOf(queryClient.getQueryState(queryKey)?.error);
  const missingHistory = previous !== null && overlayAvailability(previous) === "historyUnavailable";
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey,
    queryFn: () => client.getWeatherAlerts(validTime ?? ""),
    enabled: enabled && validTime !== null,
    // Mounting another panel cannot make an uncached historical snapshot exist.
    retryOnMount: !missingHistory,
    staleTime: 60_000,
    gcTime: Number.POSITIVE_INFINITY,
  });
}
