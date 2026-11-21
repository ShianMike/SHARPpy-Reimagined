import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import { useAppStore } from "../stores/appStore";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import type { SpotterPosition } from "../types/spotters";

const POLL_MS = 60_000;
const ACTIVE_MS = 10 * 60_000;

/** The public placefiles are for personal, non-commercial viewing. */
export function spotterNetworkAvailable(): boolean {
  return "__TAURI_INTERNALS__" in window || ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
}

const expiresAt = (position: SpotterPosition): number => Date.parse(position.updatedAt) + ACTIVE_MS;

/** Share one live request across panels and the legend. Cached positions still expire offline. */
export function useSpotterPositions(visible: boolean) {
  const client = useBackendClient();
  const liveView = useAppStore((state) => state.activeWorkspace === "live");
  const followingLive = useLiveTrackingStore((state) => state.selectedTime === null && !state.playing);
  const enabled = liveView && visible && followingLive && spotterNetworkAvailable();
  const query = useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.spotters,
    queryFn: () => client.getSpotterPositions(),
    enabled,
    staleTime: POLL_MS,
    gcTime: ACTIVE_MS,
    refetchInterval: POLL_MS,
    retry: false,
  });
  const [expiryClock, setExpiryClock] = useState(Date.now);
  const snapshot = enabled ? query.data?.positions : undefined;
  const now = Math.max(expiryClock, query.dataUpdatedAt);
  const positions = useMemo(() => {
    if (snapshot === undefined) return null;
    const active = snapshot.filter((position) => expiresAt(position) > now);
    return active.length === snapshot.length ? snapshot : active;
  }, [snapshot, now]);
  useEffect(() => {
    if (positions === null || positions.length === 0) return;
    const expires = positions.reduce((earliest, position) => Math.min(earliest, expiresAt(position)), Infinity);
    const timer = setTimeout(() => { setExpiryClock(Date.now()); }, Math.max(expires - Date.now(), 0));
    return () => { clearTimeout(timer); };
  }, [positions, now]);
  return { positions, query, followingLive };
}
