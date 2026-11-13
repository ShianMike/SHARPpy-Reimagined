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
  const site = nearestRadarSite(sites ?? [], camera?.center[1] ?? 39.8, camera?.center[0] ?? -98.6);
  const anchor = useSelectionAnchor();
  const { now, selectedTime, playing } = useLiveTrackingStore();
  const { speed, loop } = useUiStore();
  const panels = useMapStore((state) => state.livePanels);
  const layout = useMapStore((state) => state.panelLayout);
  const reportWindowS = useMapStore((state) => state.reportWindowS);
  const reportFilter = useMapStore((state) => state.reportFilter);
  const pending = useIsFetching({ predicate: (query) => isLiveQuery(query.queryKey) && query.getObserversCount() > 0 });

  const load = useCallback(async (time: number): Promise<void> => {
    await Promise.allSettled(liveRequests(client, useMapStore.getState(), site, anchor, liveTimestamp(time), sites).map((request) =>
      fetchLiveRequest(queryClient, request).then(async (data) => {
        const frame = data as { png?: unknown } | undefined;
        if (frame?.png instanceof Blob) await decodePng(frame.png);
      })));
    trimLiveCache(queryClient);
  }, [client, queryClient, site, anchor, sites]);

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
  }, [now, selectedTime, playing, speed, loop, panels, layout, reportWindowS, reportFilter, load]);
  useEffect(() => () => { useLiveTrackingStore.getState().setPlaying(false); }, []);

  return { waiting: playing && pending > 0 };
}
