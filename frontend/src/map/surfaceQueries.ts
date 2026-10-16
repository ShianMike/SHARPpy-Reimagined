// Surface station-plot queries (DESIGN-frontend F3), split out of mapQueries to
// keep each module within the size rule. The plots answer "what did the nearby
// stations report" around one anchor at one valid time, so they are keyed by
// all three and disabled without them.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback } from "react";
import { useStationCatalog } from "../stations/stationQueries";
import { useAppStore } from "../stores/appStore";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { BASE_QUERY_OPTIONS, mapKeys } from "../lib/backend/queryOptions";
import type { SurfacePlots } from "../types/surface";
import { useUiStore } from "../stores/uiStore";
import { surfaceDisplay } from "./surfaceDisplay";

const PLOTS_STALE_MS = 10 * 60_000;

export interface SurfaceAnchor {
  latitude: number;
  longitude: number;
}

/** The map's own selection as a point: a station's spot or the picked point. */
export function useSelectionAnchor(): SurfaceAnchor | null {
  const point = useAppStore((state) => state.selectedPoint);
  const stationId = useAppStore((state) => state.selectedStationId);
  const stations = useStationCatalog().data?.stations;
  if (point !== null) {
    return point;
  }
  const station = stationId === null ? undefined : stations?.find((item) => item.id === stationId);
  return station === undefined
    ? null
    : { latitude: station.latitude, longitude: station.longitude };
}

/** The station plots around an anchor at a valid time. */
export function useSurfacePlots(
  anchor: SurfaceAnchor | null,
  validTime: string | null,
  enabled: boolean,
): UseQueryResult<SurfacePlots> {
  const client = useBackendClient();
  const temperature = useUiStore((state) => state.units.temperature);
  const select = useCallback((data: SurfacePlots) => surfaceDisplay(data, temperature), [temperature]);
  return useQuery({
    ...BASE_QUERY_OPTIONS,
    queryKey: mapKeys.surface(anchor?.latitude ?? 0, anchor?.longitude ?? 0, validTime ?? ""),
    queryFn: () =>
      client.getSurfacePlots(anchor?.latitude ?? 0, anchor?.longitude ?? 0, validTime ?? ""),
    enabled: enabled && anchor !== null && validTime !== null,
    staleTime: PLOTS_STALE_MS,
    select,
  });
}
