// The last health report already in the cache, without fetching: notices
// read the developer log path from it. A disabled observer shares the health
// query's cache entry but never triggers a request (useBackendHealth() owns
// fetching and polling), so mounting a notice adds no health call.

import { useQuery } from "@tanstack/react-query";
import type { BackendHealth } from "../../types/contracts";
import { useBackendClient } from "./BackendProvider";
import { BASE_QUERY_OPTIONS, QUERY_KEYS } from "./queryOptions";

export function useKnownHealth(): BackendHealth | undefined {
  const client = useBackendClient();
  const query = useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: QUERY_KEYS.health,
    queryFn: () => client.getBackendHealth(),
    enabled: false,
  });
  return query.data;
}
