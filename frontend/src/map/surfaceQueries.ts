// Model Viewer and Live Tracking share the latest worldwide METAR snapshot.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback } from "react";
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

export function useSurfaceLayer(enabled: boolean): UseQueryResult<SurfacePlots> {
  return useSurfacePlots(null, null, enabled, "latest");
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
