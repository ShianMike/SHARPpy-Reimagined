import type { QueryClient } from "@tanstack/react-query";
import { addHours } from "../features/sounding/soundingTime";
import type { BackendClient } from "../lib/backend/client";
import { panelLayers, panelReports, type MapState } from "../stores/mapStore";
import { OUTLOOK_KEYS } from "../types/map";
import { contourQueryOptions } from "./contourQueries";
import { outlookQueryOptions, reportQueryOptions, type FrameRequest } from "./mapQueries";
import { queryFrame } from "./queryFrame";

/** Load the exact overlays drawn at this time with the panel's own layer settings. */
export function forecastOverlayLoads(client: BackendClient, queryClient: QueryClient, map: MapState,
  panel: number, frame: Omit<FrameRequest, "product">, pressureAvailable: boolean, signal: AbortSignal): Promise<unknown>[] {
  const layers = panelLayers(map, panel);
  const validTime = addHours(frame.runTime, frame.forecastHour);
  const loads: Promise<unknown>[] = OUTLOOK_KEYS.filter((key) => layers[`spc-${key}`].visible)
    .map((key) => queryFrame(queryClient, outlookQueryOptions(client, key, validTime), signal));
  if (layers["storm-reports"].visible) {
    const { reportWindowS, reportFilter } = panelReports(map, panel);
    loads.push(queryFrame(queryClient, reportQueryOptions(client, validTime, reportWindowS, reportFilter), signal));
  }
  if (pressureAvailable && layers.isobars.visible) {
    loads.push(queryFrame(queryClient, contourQueryOptions(client, queryClient, { ...frame, product: "mslp" }), signal));
  }
  return loads;
}
