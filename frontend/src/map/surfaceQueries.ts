// Model Viewer shares the latest worldwide snapshot. Live Tracking can also
// request nearby observations at a specific playback time.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useStationCatalog } from "../stations/stationQueries";
import { useAppStore } from "../stores/appStore";
import { useMapStore } from "../stores/mapStore";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import type { SurfacePlots } from "../types/surface";
import { useUiStore } from "../stores/uiStore";
import { surfaceDisplay } from "./surfaceDisplay";

const PLOTS_STALE_MS = 10 * 60_000;
const SNAPSHOT_REFRESH_MS = 2 * 60_000;

export interface SurfaceAnchor {
  latitude: number;
  longitude: number;
}

/** The map's own selection as a point: a station's spot or the picked point. */
export function useSelectionAnchor(enabled = true): SurfaceAnchor | null {
  const point = useAppStore((state) => enabled ? state.selectedPoint : null);
  const stationId = useAppStore((state) => enabled ? state.selectedStationId : null);
  const stations = useStationCatalog(enabled).data?.stations;
  const camera = useMapStore((state) => enabled ? state.cameraSnapshot : null);
  const live = useMapStore((state) => state.view === "live");
  return useMemo(() => {
    if (!enabled) return null;
    if (point !== null && !live) return point;
    const station = stationId === null ? undefined : stations?.find((item) => item.id === stationId);
    return station === undefined
      ? live ? { latitude: camera?.center[1] ?? 39.8, longitude: camera?.center[0] ?? -98.6 } : null
      : { latitude: station.latitude, longitude: station.longitude };
  }, [enabled, point, stationId, stations, camera, live]);
}

export function useSurfaceLayer(live: boolean, validTime: string | null, enabled: boolean): UseQueryResult<SurfacePlots> {
  const anchor = useSelectionAnchor(live && enabled);
  return useSurfacePlots(anchor, live && enabled ? validTime : null, enabled, live ? "nearby" : "latest");
}

/** The current global snapshot or historical observations near an anchor. */
export function useSurfacePlots(
  anchor: SurfaceAnchor | null,
  validTime: string | null,
  enabled: boolean,
  scope: "nearby" | "latest" = "nearby",
): UseQueryResult<SurfacePlots> {
  const client = useBackendClient();
  const temperature = useUiStore((state) => state.units.temperature);
  const select = useCallback((data: SurfacePlots) => surfaceDisplay(data, temperature), [temperature]);
  const latest = scope === "latest";
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: latest ? mapKeys.latestSurface : mapKeys.surface(anchor?.latitude ?? 0, anchor?.longitude ?? 0, validTime ?? ""),
    queryFn: () => latest ? client.getLatestSurfacePlots() : client.getSurfacePlots(anchor?.latitude ?? 0, anchor?.longitude ?? 0, validTime ?? ""),
    enabled: enabled && (latest || anchor !== null && validTime !== null),
    staleTime: latest ? SNAPSHOT_REFRESH_MS : PLOTS_STALE_MS,
    gcTime: latest ? 10 * 60_000 : Number.POSITIVE_INFINITY,
    refetchInterval: latest ? SNAPSHOT_REFRESH_MS : false,
    retry: latest ? false : BASE_QUERY_OPTIONS.retry,
    select,
  });
}
