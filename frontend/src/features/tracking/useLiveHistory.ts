import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { useRadarSites } from "../../map/mapQueries";
import { nearestRadarSite } from "../../map/nearestSite";
import { FRAME_INTERVAL_MS } from "../../map/usePlayback";
import { useSelectionAnchor } from "../../map/surfaceQueries";
import { useMapStore } from "../../stores/mapStore";
import { liveHistoryTimes, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useUiStore } from "../../stores/uiStore";
import { decodePng } from "../../map/pngImages";
import { fetchLiveRequest, isLiveQuery, liveRequests, trimLiveCache } from "./liveRequests";

export function useLiveHistory() {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const sites = useRadarSites(true).data?.sites;
  const camera = useMapStore((state) => state.cameraSnapshot);
  const site = nearestRadarSite(sites ?? [], camera?.center[1] ?? 0, camera?.center[0] ?? 0);
  const anchor = useSelectionAnchor();
  const { now, selectedTime, playing } = useLiveTrackingStore();
  const { speed, loop } = useUiStore();
  const panels = useMapStore((state) => state.livePanels);
  const layout = useMapStore((state) => state.panelLayout);
  const pending = useIsFetching({ predicate: (query) => isLiveQuery(query.queryKey) });

  const load = useCallback(async (time: number, followingLive: boolean) => {
    const requests = liveRequests(client, useMapStore.getState(), site, anchor, liveTimestamp(time), sites, followingLive);
    await Promise.allSettled(requests.map((request) =>
      fetchLiveRequest(queryClient, request).then(async (data) => {
        const frame = data as { png?: unknown } | undefined;
        if (frame?.png instanceof Blob) await decodePng(frame.png);
      })));
    return requests.map(({ queryKey }) => queryKey);
  }, [client, queryClient, site, anchor, sites]);

  useEffect(() => {
    if (!playing) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const times = liveHistoryTimes(now);
    const next = selectedTime === null || selectedTime >= now ? times[0] : times.find((step) => step > selectedTime);
    if (next === undefined) { useLiveTrackingStore.getState().setPlaying(false); return; }
    const followingLive = !loop && next === now;
    const delay = selectedTime === null ? Promise.resolve() : new Promise<void>((resolve) => { timer = setTimeout(resolve, FRAME_INTERVAL_MS / speed); });
    void Promise.all([load(next, followingLive), delay]).then(([prepared]) => {
      if (stopped) return;
      // Reused entries can be the oldest in the cache, even though they draw next.
      trimLiveCache(queryClient, undefined, prepared);
      if (followingLive) useLiveTrackingStore.setState({ playing: false, selectedTime: null });
      else useLiveTrackingStore.getState().selectTime(next);
    });
    return () => { stopped = true; clearTimeout(timer); };
  }, [now, selectedTime, playing, speed, loop, panels, layout, load, queryClient]);
  useEffect(() => () => { useLiveTrackingStore.getState().setPlaying(false); }, []);

  return { waiting: playing && pending > 0 };
}
