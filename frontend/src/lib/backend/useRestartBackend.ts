// Restart backend (DESIGN-contracts C5): an explicit user action. Once the
// command settles, the health and catalogue queries are invalidated, so both
// reload against the new bridge. The client logs a failure once (C6).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useBackendClient } from "./BackendProvider";
import { QUERY_KEYS } from "./queryOptions";

export interface RestartBackend {
  restart: () => void;
  pending: boolean;
}

export function useRestartBackend(): RestartBackend {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => client.restartBackend(),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.health }),
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog }),
      ]);
    },
  });
  return {
    restart: () => {
      mutation.mutate();
    },
    pending: mutation.isPending,
  };
}
