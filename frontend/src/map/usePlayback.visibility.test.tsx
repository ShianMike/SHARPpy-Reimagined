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
import { useGraphicsStore } from "../stores/graphicsStore";
import { useUiStore } from "../stores/uiStore";
import * as fieldImages from "./fieldImages";
import { FRAME_INTERVAL_MS, usePlayback } from "./usePlayback";

const savedApp = useAppStore.getState(), savedMap = useMapStore.getState(), savedForecast = useForecastStore.getState();
const savedGraphics = useGraphicsStore.getState(), savedUi = useUiStore.getState();
afterEach(() => { cleanup(); useGraphicsStore.setState(savedGraphics); useUiStore.setState(savedUi); vi.restoreAllMocks(); vi.unstubAllGlobals(); useAppStore.setState(savedApp); useMapStore.setState(savedMap); useForecastStore.setState(savedForecast); vi.useRealTimers(); });

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

it("prepares both active modes once across four matching panels, waits on cached fields, and cancels the old window", async () => {
  vi.useFakeTimers();
  useAppStore.setState({ activeWorkspace: "map" });
  vi.stubGlobal("ImageData", class { constructor(public data: Uint8ClampedArray, public width: number, public height: number) {} });
  const run = "2026-10-04T12:00:00Z";
  const sharp = defaultLayerStates(), smooth = defaultLayerStates();
  sharp["model-field"] = { visible: true, opacity: 1 };
  smooth["model-field"] = { visible: true, opacity: 1, smoothing: true };
  useMapStore.setState({ panelLayout: 4, productKey: "refc", layers: sharp,
    panels: [smooth, sharp, smooth].map((layers) => ({ productKey: "refc", layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0, playing: true });
  useUiStore.setState({ speed: 1, loop: true });
  useGraphicsStore.setState({ effective: { ...savedGraphics.effective, profile: "balanced" } });
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr", run, 0), parseMapCatalog(catalog));
  const frame = parseFieldFrame(Uint8Array.from(atob(field.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  for (const hour of [0, 1, 2]) queryClient.setQueryData(mapKeys.field("refc", run, hour, "hrrr"), { ...frame, forecastHour: hour });
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  vi.spyOn(client, "getMapCatalog").mockResolvedValue(parseMapCatalog(catalog));
  const fetch = vi.spyOn(client, "getWeatherField");
  const prepare = fieldImages.prepareFieldImage;
  const pending: { hour: number; smooth: boolean; signal: AbortSignal; finish: () => void }[] = [];
  vi.spyOn(fieldImages, "prepareFieldImage").mockImplementation((field, palette, smoothing, signal) =>
    new Promise<void>((resolve) => { pending.push({ hour: field.forecastHour, smooth: smoothing, signal, finish: resolve }); })
      .then(() => prepare(field, palette, smoothing, signal)));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
    expect(fetch).not.toHaveBeenCalled();
    expect(pending.map(({ hour, smooth }) => [hour, smooth])).toEqual([[1, false], [1, true], [2, false], [2, true]]);
    expect(useForecastStore.getState().forecastHour).toBe(0);
    await act(async () => { pending[0]?.finish(); await vi.advanceTimersByTimeAsync(1); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
    await act(async () => { pending[1]?.finish(); await vi.advanceTimersByTimeAsync(1); });
    expect(useForecastStore.getState().forecastHour).toBe(1);
    expect(pending.slice(2, 4).every(({ signal }) => signal.aborted)).toBe(true);
  } finally {
    hook.unmount();
    pending.forEach(({ finish }) => { finish(); });
    await act(async () => { await vi.runAllTimersAsync(); });
    queryClient.clear();
  }
});
