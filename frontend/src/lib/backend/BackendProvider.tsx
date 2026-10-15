// BackendProvider (DESIGN-frontend F2) provides the client and owns the bridge
// status subscription. Events are not replayed, so the order is fixed:
// 1. subscribe; only then enable the health query (nothing can fall between);
// 2. on each event, patch the cached health and invalidate it, and on a
//    return to "up" also refetch a catalogue whose last load failed;
// 3. backstop: poll every second while the bridge is starting or restarting;
// 4. if subscribing fails, warn once and poll every 5 s for the session;
// 5. unlisten on unmount.

import { hashKey, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { BackendHealth, BridgeStatus } from "../../types/contracts";
import { isTransitional } from "../contracts/health";
import type { BackendClient } from "./client";
import { recordLog } from "../../stores/logStore";
import { useSoundingStore } from "../../stores/soundingStore";
import {
  BASE_QUERY_OPTIONS,
  FALLBACK_POLL_MS,
  QUERY_KEYS,
  TRANSITIONAL_POLL_MS,
} from "./queryOptions";

interface SubscriptionState {
  subscribed: boolean;
  eventsAvailable: boolean;
}

const ClientContext = createContext<BackendClient | null>(null);
const SubscriptionContext = createContext<SubscriptionState>({ subscribed: false, eventsAvailable: true });

function applyBridgeStatus(queryClient: QueryClient, status: BridgeStatus): void {
  queryClient.setQueryData<BackendHealth>(QUERY_KEYS.health, (old) =>
    old === undefined ? old : { ...old, bridge: status },
  );
  void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.health });
  if (status.status === "up" && queryClient.getQueryState(QUERY_KEYS.catalog)?.status === "error") {
    void queryClient.invalidateQueries({ queryKey: QUERY_KEYS.catalog });
  }
}

export interface BackendProviderProps {
  client: BackendClient;
  children: ReactNode;
}

export function BackendProvider({ client, children }: BackendProviderProps): ReactNode {
  const queryClient = useQueryClient();
  const [subscription, setSubscription] = useState<SubscriptionState>({ subscribed: false, eventsAvailable: true });

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | null = null;
    let instance = queryClient.getQueryData<BackendHealth>(QUERY_KEYS.health)?.bridge;
    const healthHash = hashKey(QUERY_KEYS.health);
    // Health updates cover status events, polling, and explicit restarts alike.
    const unwatch = queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.query.queryHash !== healthHash) return;
      const next = queryClient.getQueryData<BackendHealth>(QUERY_KEYS.health)?.bridge;
      if (next?.status !== "up") return;
      const previous = instance;
      instance = next;
      if (previous?.status !== "up" || (previous.pid === next.pid
        && previous.since === next.since && previous.restarts === next.restarts)) return;
      const hadSoundings = useSoundingStore.getState().open.length > 0;
      useSoundingStore.setState({ open: [], activeId: null, views: {} });
      void queryClient.cancelQueries({ queryKey: ["soundings"] });
      queryClient.removeQueries({ queryKey: ["soundings"] });
      if (hadSoundings) {
        recordLog("warning", "Backend", "Soundings closed after backend restart", "Reopen the soundings to continue.");
      }
    });
    client
      .onBridgeStatus((status) => {
        if (!disposed) {
          applyBridgeStatus(queryClient, status);
        }
      })
      .then(
        (release) => {
          if (disposed) {
            release();
            return;
          }
          unlisten = release;
          setSubscription({ subscribed: true, eventsAvailable: true });
        },
        (reason: unknown) => {
          if (disposed) {
            return;
          }
          console.warn(
            "[backend] bridge status events are unavailable; backend status is polled every 5 s instead",
            reason,
          );
          recordLog("info", "Backend", "Connection monitoring uses polling", "Backend status is checked every 5 seconds.");
          setSubscription({ subscribed: true, eventsAvailable: false });
        },
      );
    return () => {
      disposed = true;
      unlisten?.();
      unwatch();
    };
  }, [client, queryClient]);

  return (
    <ClientContext value={client}>
      <SubscriptionContext value={subscription}>{children}</SubscriptionContext>
    </ClientContext>
  );
}

export function useBackendClient(): BackendClient {
  const client = useContext(ClientContext);
  if (client === null) {
    throw new Error("useBackendClient() needs a <BackendProvider> above it");
  }
  return client;
}

/** The backend health query (PLAN P17): enabled once the status subscription resolved. */
export function useBackendHealth(): UseQueryResult<BackendHealth> {
  const client = useBackendClient();
  const { subscribed, eventsAvailable } = useContext(SubscriptionContext);
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: QUERY_KEYS.health,
    queryFn: () => client.getBackendHealth(),
    enabled: subscribed,
    staleTime: 0,
    refetchInterval: (query) => {
      if (!eventsAvailable) {
        return FALLBACK_POLL_MS;
      }
      return isTransitional(query.state.data?.bridge) ? TRANSITIONAL_POLL_MS : false;
    },
  });
}
