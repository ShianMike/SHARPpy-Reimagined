import { hashKey, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { useRadarSites } from "../../map/mapQueries";
import { nearestRadarSite } from "../../map/nearestSite";
import { FRAME_INTERVAL_MS } from "../../map/usePlayback";
import { useSelectionAnchor } from "../../map/surfaceQueries";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { liveHistoryTimes, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useUiStore } from "../../stores/uiStore";
import { decodePng } from "../../map/pngImages";
import { fetchLiveRequest, liveRequests, trimLiveCache } from "./liveRequests";

interface LivePreparation {
  now: number;
  selectedTime: number | null;
  followingLive: boolean;
  owner: AbortController;
  requests: Map<string, Promise<void>>;
}

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
  const [preparing, setPreparing] = useState(false);
  const cycle = useRef<LivePreparation | null>(null);

  const load = useCallback(async (time: number, followingLive: boolean, next: LivePreparation) => {
    const requests = liveRequests(client, useMapStore.getState(), site, anchor, liveTimestamp(time), sites, followingLive);
    await Promise.allSettled(requests.map((request) => {
      const key = hashKey(request.queryKey);
      let preparation = next.requests.get(key);
      if (preparation === undefined) {
        preparation = fetchLiveRequest(queryClient, request).then(async (data) => {
          const frame = data as { png?: unknown } | undefined;
          if (!next.owner.signal.aborted && frame?.png instanceof Blob) await decodePng(frame.png, next.owner.signal);
        });
        next.requests.set(key, preparation);
      }
      return preparation;
    }));
    const prepared = requests.map(({ queryKey }) => queryKey);
    if (!useLiveTrackingStore.getState().playing) trimLiveCache(queryClient);
    return prepared;
  }, [client, queryClient, site, anchor, sites]);

  useEffect(() => {
    if (!playing) {
      cycle.current?.owner.abort();
      cycle.current = null;
      return;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const times = liveHistoryTimes(now);
    const next = selectedTime === null || selectedTime >= now ? times[0] : times.find((step) => step > selectedTime);
    if (next === undefined) { useLiveTrackingStore.getState().setPlaying(false); return; }
    const followingLive = !loop && next === now;
    if (cycle.current?.now !== now || cycle.current.selectedTime !== selectedTime || cycle.current.followingLive !== followingLive) {
      cycle.current?.owner.abort();
      cycle.current = { now, selectedTime, followingLive, owner: new AbortController(), requests: new Map() };
    }
    const delay = selectedTime === null ? Promise.resolve() : new Promise<void>((resolve) => { timer = setTimeout(resolve, FRAME_INTERVAL_MS / speed); });
    const preparation = load(next, followingLive, cycle.current);
    void Promise.resolve().then(() => { if (!stopped) setPreparing(true); });
    void preparation.then(() => { if (!stopped) setPreparing(false); });
    void Promise.all([preparation, delay]).then(([prepared]) => {
      const current = useLiveTrackingStore.getState();
      const map = useMapStore.getState(), settings = useUiStore.getState();
      if (stopped || !current.playing || current.now !== now || current.selectedTime !== selectedTime
        || map.livePanels !== panels || map.panelLayout !== layout || settings.loop !== loop || settings.speed !== speed
        || useAppStore.getState().activeWorkspace !== "live") return;
      // Reused entries can be the oldest in the cache, even though they draw next.
      trimLiveCache(queryClient, undefined, prepared);
      if (followingLive) useLiveTrackingStore.setState({ playing: false, selectedTime: null });
      else useLiveTrackingStore.getState().selectTime(next);
    });
    return () => { stopped = true; clearTimeout(timer); };
  }, [now, selectedTime, playing, speed, loop, panels, layout, load, queryClient]);
  useEffect(() => () => {
    cycle.current?.owner.abort();
    cycle.current = null;
    useLiveTrackingStore.getState().setPlaying(false);
  }, []);

  return { waiting: playing && preparing };
}
