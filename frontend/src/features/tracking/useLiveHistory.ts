import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS } from "../../lib/backend/queryOptions";
import { useRadarSites } from "../../map/mapQueries";
import { nearestRadarSite } from "../../map/nearestSite";
import { FRAME_INTERVAL_MS } from "../../map/usePlayback";
import { useSelectionAnchor } from "../../map/surfaceQueries";
import { useMapStore } from "../../stores/mapStore";
import { liveHistoryTimes, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useUiStore } from "../../stores/uiStore";
import { decodePng } from "../../map/pngImages";
import { isLiveQuery, liveRequests, preloadRadarTilts, trimLiveCache } from "./liveRequests";

const IDLE_PROGRESS = { running: false, done: 0, total: 0, failed: 0, stopped: false };

export function useLiveHistory() {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const sites = useRadarSites(true).data?.sites;
  const camera = useMapStore((state) => state.cameraSnapshot);
  const site = nearestRadarSite(sites ?? [], camera?.center[1] ?? 39.8, camera?.center[0] ?? -98.6);
  const anchor = useSelectionAnchor();
  const { now, selectedTime, playing } = useLiveTrackingStore();
  const { speed, loop } = useUiStore();
  const panels = useMapStore((state) => state.livePanels);
  const layout = useMapStore((state) => state.panelLayout);
  const time = liveTimestamp(now);
  const selection = JSON.stringify(liveRequests(client, useMapStore.getState(), site, anchor, time)
    .map(({ queryKey }) => queryKey.filter((part) => part !== time)));
  const pending = useIsFetching({ predicate: (query) => isLiveQuery(query.queryKey) && query.getObserversCount() > 0 });
  const generation = useRef(0);
  const preloading = useRef(Promise.resolve());
  const [storedProgress, setProgress] = useState({ ...IDLE_PROGRESS, selection });
  const progress = storedProgress.selection === selection ? storedProgress : IDLE_PROGRESS;
  useEffect(() => () => {
    generation.current++;
    setProgress((state) => state.running ? { ...state, running: false, stopped: true } : state);
  }, [selection]);

  useEffect(() => {
    if (selectedTime !== null || playing || progress.running) return;
    let stopped = false;
    const warm = async (): Promise<void> => {
      await preloadRadarTilts(client, queryClient, useMapStore.getState(), site, liveTimestamp(now), () => stopped);
    };
    preloading.current = preloading.current.then(warm, warm);
    return () => { stopped = true; };
  }, [client, queryClient, panels, layout, site, now, selectedTime, playing, progress.running]);

  const load = useCallback(async (time: number, refresh = false): Promise<void> => {
    await Promise.allSettled(liveRequests(client, useMapStore.getState(), site, anchor, liveTimestamp(time)).map((request) =>
      queryClient.query({ ...BASE_QUERY_OPTIONS, ...request, staleTime: refresh ? 0 : Number.POSITIVE_INFINITY, gcTime: Number.POSITIVE_INFINITY, retry: false }).then(async (data) => {
        const frame = data as { png?: unknown } | undefined;
        if (frame?.png instanceof Blob) await decodePng(frame.png);
      })));
  }, [client, queryClient, site, anchor]);

  useEffect(() => {
    if (!playing) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const times = liveHistoryTimes(now);
    const time = selectedTime ?? times[0] ?? now;
    const next = times.find((step) => step > time) ?? (loop ? times[0] ?? now : null);
    if (next === null) { useLiveTrackingStore.getState().setPlaying(false); return; }
    const delay = new Promise<void>((resolve) => { timer = setTimeout(resolve, FRAME_INTERVAL_MS / speed); });
    void Promise.all([load(next), delay]).then(() => {
      if (stopped) return;
      useLiveTrackingStore.getState().selectTime(next);
    });
    return () => { stopped = true; clearTimeout(timer); };
  }, [now, selectedTime, playing, speed, loop, panels, layout, load]);
  useEffect(() => () => { useLiveTrackingStore.getState().setPlaying(false); }, []);

  const cacheHour = async (): Promise<void> => {
    const token = ++generation.current;
    const state = useMapStore.getState();
    const now = useLiveTrackingStore.getState().now;
    const requests = liveHistoryTimes(now).reverse().flatMap((time) => liveRequests(client, state, site, anchor, liveTimestamp(time)));
    let done = 0;
    let failed = 0;
    setProgress({ running: true, done, total: requests.length, failed, stopped: false, selection });
    let index = 0;
    // Keep two downloads busy without making a completed request wait for its slower partner.
    const download = async (): Promise<void> => {
      while (generation.current === token) {
        const request = requests[index++];
        if (request === undefined) return;
        try { await queryClient.query({ ...BASE_QUERY_OPTIONS, ...request, staleTime: Number.POSITIVE_INFINITY, gcTime: Number.POSITIVE_INFINITY, retry: false }); }
        catch { failed++; }
        if (generation.current !== token) return;
        done++;
        trimLiveCache(queryClient);
        setProgress({ running: done < requests.length, done, total: requests.length, failed, stopped: false, selection });
      }
    };
    await Promise.all([download(), download()]);
    if (requests.length === 0) setProgress({ ...IDLE_PROGRESS, selection });
  };

  const refresh = async (): Promise<void> => {
    useLiveTrackingStore.getState().tick();
    const state = useLiveTrackingStore.getState();
    await load(state.selectedTime ?? state.now, true);
    trimLiveCache(queryClient);
  };
  const stop = (): void => { generation.current++; setProgress((state) => ({ ...state, running: false, stopped: true })); };
  return { progress, waiting: playing && pending > 0, cacheHour, refresh, stop };
}
