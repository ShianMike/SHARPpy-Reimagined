// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import field from "@contracts/public/map/field.shrf?url&inline";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import { useAppStore } from "../stores/appStore";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { defaultLayerStates } from "./layers/registry";
import { usePlayback } from "./usePlayback";

const savedApp = useAppStore.getState(), savedMap = useMapStore.getState(), savedForecast = useForecastStore.getState();
afterEach(() => { cleanup(); useAppStore.setState(savedApp); useMapStore.setState(savedMap); useForecastStore.setState(savedForecast); vi.useRealTimers(); });

it.each(["overview", "live", "sounding", "status", "settings"] as const)("does not fetch or advance forecast frames while %s is active", async (workspace) => {
  vi.useFakeTimers();
  const run = "2026-10-04T12:00:00Z";
  const layers = defaultLayerStates(); layers["model-field"].visible = true;
  useMapStore.setState({ panelLayout: 1, productKey: "refc", layers });
  useAppStore.setState({ activeWorkspace: workspace });
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0, playing: true });
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  const fetch = vi.spyOn(client, "getWeatherField");
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr"), parseMapCatalog(catalog));
  queryClient.setQueryData(mapKeys.field("refc", run, 0), parseFieldFrame(Uint8Array.from(atob(field.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(fetch).not.toHaveBeenCalled();
    expect(useForecastStore.getState().forecastHour).toBe(0);
  } finally { hook.unmount(); queryClient.clear(); }
});
