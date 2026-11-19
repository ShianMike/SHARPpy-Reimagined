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
import { createTransport } from "../lib/backend/fixtureTransport";
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
import { trimFieldCache } from "./mapQueries";
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

it.each([1, 3, 4])("waits for both render modes across four panels at %s times speed and cancels the old window", async (speed) => {
  vi.useFakeTimers();
  useAppStore.setState({ activeWorkspace: "map" });
  vi.stubGlobal("ImageData", class { constructor(public data: Uint8ClampedArray, public width: number, public height: number) {} });
  const run = "2026-10-04T12:00:00Z";
  const sharp = defaultLayerStates(), smooth = defaultLayerStates();
  sharp["model-field"] = { visible: true, opacity: 1 };
  smooth["model-field"] = { visible: true, opacity: 1, smoothing: true };
  useMapStore.setState({ panelLayout: 4, productKey: "refc", layers: sharp,
    panels: [smooth, sharp, smooth].map((layers, index) => ({ ...useMapStore.getInitialState().panels[index], reportFilter: "all", reportWindowS: 21600, productKey: "refc", layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0, playing: true });
  useUiStore.setState({ speed, loop: true });
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
    await act(async () => { await vi.advanceTimersByTimeAsync(Math.ceil(FRAME_INTERVAL_MS / speed)); });
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

it("keeps the current hour until the next hour's enabled overlays settle for all visible panels", async () => {
  vi.useFakeTimers();
  const run = "2026-10-04T12:00:00Z", validTime = "2026-10-04T13:00:00Z";
  useAppStore.getState().setActiveWorkspace("map");
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0, playing: true });
  useUiStore.setState({ speed: 4, loop: true });
  const map = useMapStore.getState();
  map.setPanelLayout(4);
  const filters = ["tornado", "hail", "wind", "significant"];
  const windows = [7200, 21600, 43200, 86400];
  for (const [panel, filter] of filters.entries()) {
    map.setActivePanel(panel);
    map.setLayerVisible("model-field", false);
    map.setLayerVisible("storm-reports", true);
    map.setLayerVisible("spc-cat", true);
    map.setLayerVisible("isobars", true);
    map.setReportFilter(filter);
    map.setReportWindowS(windows[panel] ?? 21600);
  }
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  for (const hour of [0, 1]) queryClient.setQueryData(mapKeys.catalog("hrrr", run, hour), parseMapCatalog(catalog));
  const client = new BackendClient(createTransport());
  const fetchReports = client.getStormReports.bind(client);
  const pendingReports: (() => void)[] = [];
  const reports = vi.spyOn(client, "getStormReports").mockImplementation((...args) =>
    args[0] === validTime ? new Promise((resolve, reject) => {
      pendingReports.push(() => { void fetchReports(...args).then(resolve, reject); });
    }) : fetchReports(...args));
  const outlooks = vi.spyOn(client, "getSpcOutlook");
  const pressure = vi.spyOn(client, "getFieldContours").mockRejectedValueOnce(new Error("Pressure layer temporarily unavailable"));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(Math.ceil(FRAME_INTERVAL_MS / 4)); });
    expect(reports).toHaveBeenCalledTimes(4);
    filters.forEach((filter, panel) => {
      expect(reports).toHaveBeenCalledWith(validTime, windows[panel], filter, expect.any(AbortSignal));
    });
    expect(outlooks).toHaveBeenCalledExactlyOnceWith("cat", validTime, expect.any(AbortSignal));
    expect(pressure).toHaveBeenCalledExactlyOnceWith("mslp", run, 1, "hrrr", expect.any(AbortSignal));
    expect(useForecastStore.getState().forecastHour).toBe(0);
    await act(async () => { pendingReports.slice(0, 3).forEach((finish) => { finish(); }); await vi.advanceTimersByTimeAsync(1); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
    await act(async () => { pendingReports[3]?.(); await vi.advanceTimersByTimeAsync(1); });
    expect(useForecastStore.getState().forecastHour).toBe(1);
  } finally {
    hook.unmount();
    pendingReports.forEach((finish) => { finish(); });
    queryClient.clear();
  }
});

it("prepares and retains the next hour's actual products and isobars when a run wraps", async () => {
  vi.useFakeTimers();
  const run = "2026-10-04T16:00:00Z";
  useAppStore.getState().setActiveWorkspace("map");
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 18, playing: true });
  useUiStore.setState({ speed: 4, loop: true });
  const layers = defaultLayerStates();
  layers["model-field"].visible = true;
  layers.isobars.visible = true;
  useMapStore.setState({ panelLayout: 1, productKey: "uh-2-5km-3h", layers });
  useGraphicsStore.setState({ effective: { ...savedGraphics.effective, profile: "low" } });
  const client = new BackendClient(createTransport());
  const queryClient = createQueryClient();
  const inventory = parseMapCatalog(catalog);
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr", run, 18), { ...inventory, products: inventory.products.filter((product) => product.key !== "mslp") });
  const frame = parseFieldFrame(Uint8Array.from(atob(field.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  queryClient.setQueryData(mapKeys.field("uh-2-5km-3h", run, 18), { ...frame, forecastHour: 18, product: "uh-2-5km-3h" });
  const first = mapKeys.field(inventory.defaultProduct, run, 0);
  queryClient.setQueryData(first, { ...frame, forecastHour: 0, product: inventory.defaultProduct });
  const fetch = vi.spyOn(client, "getWeatherField");
  let showInventory: () => void = () => undefined;
  vi.spyOn(client, "getMapCatalog").mockImplementation(() => new Promise((resolve) => { showInventory = () => {
    resolve({ ...inventory, products: inventory.products.filter((product) => product.key !== "uh-2-5km-3h") });
  }; }));
  let showPressure: () => void = () => undefined;
  const contours = client.getFieldContours.bind(client);
  const fetchPressure = vi.spyOn(client, "getFieldContours").mockImplementation((...args) => new Promise((resolve, reject) => {
    showPressure = () => { void contours(...args).then(resolve, reject); };
  }));
  const prepare = vi.spyOn(fieldImages, "prepareFieldImage").mockResolvedValue(undefined);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(Math.ceil(FRAME_INTERVAL_MS / 4)); });
    expect(useForecastStore.getState().forecastHour).toBe(18);
    expect(prepare).not.toHaveBeenCalled();
    await act(async () => { showInventory(); await vi.advanceTimersByTimeAsync(0); });
    expect(prepare).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ product: inventory.defaultProduct, forecastHour: 0 }),
      expect.anything(), expect.any(Boolean), expect.any(AbortSignal));
    expect(fetchPressure).toHaveBeenCalledExactlyOnceWith("mslp", run, 0, "hrrr", expect.any(AbortSignal));
    expect(fetch).not.toHaveBeenCalled();
    expect(useForecastStore.getState().forecastHour).toBe(18);
    trimFieldCache(queryClient, 0);
    expect(queryClient.getQueryData(first)).toBeDefined();
    await act(async () => { showPressure(); await vi.advanceTimersByTimeAsync(0); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
  } finally {
    hook.unmount();
    showInventory(); showPressure();
    queryClient.clear();
  }
});
