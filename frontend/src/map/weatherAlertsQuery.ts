import { useQuery } from "@tanstack/react-query";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";

export function useWeatherAlerts(validTime: string | null, enabled: boolean) {
  const client = useBackendClient();
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.alerts(validTime ?? ""),
    queryFn: () => client.getWeatherAlerts(validTime ?? ""),
    enabled: enabled && validTime !== null,
    staleTime: 60_000,
    gcTime: Number.POSITIVE_INFINITY,
  });
}
