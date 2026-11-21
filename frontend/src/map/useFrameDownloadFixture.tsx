import catalog from "@contracts/public/map/catalog.json";
import contours from "@contracts/public/map/contours.json";
import field from "@contracts/public/map/field.shrf?url&inline";
import models from "@contracts/public/soundings/models.catalog.json";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { parseContourLines } from "../lib/contracts/contours";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { defaultLayerStates } from "./layers/registry";

export const frame = parseFieldFrame(Uint8Array.from(atob(field.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);

export function setupFrameDownload() {
  const layers = defaultLayerStates();
  layers["model-field"].visible = true;
  const run = "2026-10-04T12:00:00Z";
  const productKey = "hgt-wind-500";
  useMapStore.setState({ panelLayout: 4, productKey, layers,
    panels: useMapStore.getInitialState().panels.map(panel => ({ ...panel, productKey, layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 1, playing: true });
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr", run, 1), parseMapCatalog(catalog));
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  const storedFields = new Set<number>();
  const storedWinds = new Set<number>();
  const fields = vi.spyOn(client, "getWeatherField").mockImplementation((_product, _run, hour) => {
    storedFields.add(hour);
    return Promise.resolve(frame);
  });
  const winds = vi.spyOn(client, "getFieldContours").mockImplementation((_product, _run, hour) => {
    storedWinds.add(hour);
    return Promise.resolve(parseContourLines(contours));
  });
  const saved = vi.spyOn(client, "getCachedForecastHours").mockImplementation(
    (_model, _products, _run, hours) => Promise.resolve(hours.filter((hour) => storedFields.has(hour) && storedWinds.has(hour))),
  );
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  return { queryClient, client, fields, winds, saved, storedFields, wrapper };
}
