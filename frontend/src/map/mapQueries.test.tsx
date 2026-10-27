// @vitest-environment jsdom
import field from "@contracts/public/map/field.shrf?url&inline";
import radar from "@contracts/public/map/radar.shrf?url&inline";
import radarSite from "@contracts/public/map/radar-site.shrf?url&inline";
import satellite from "@contracts/public/map/satellite.shrf?url&inline";
import outlook from "@contracts/public/map/outlook.cat.json";
import reports from "@contracts/public/map/reports.json";
import surface from "@contracts/public/map/surface.json";
import catalog from "@contracts/public/map/catalog.json";
import modelCatalogs from "@contracts/public/map/catalog.models.json";
import contours from "@contracts/public/map/contours.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import type { Transport } from "../lib/backend/transport";
import { useFieldFrame, useRadarFrame, useRadarSiteFrame, useSatelliteFrame, useSpcOutlooks, useStormReports } from "./mapQueries";
import { useSurfacePlots } from "./surfaceQueries";
import { useFieldContours } from "./contourQueries";
import { defaultLayerStates } from "./layers/registry";
import { defaultProductFor } from "./panelField";
import { FRAME_INTERVAL_MS, usePlayback } from "./usePlayback";
import { useMapStore } from "../stores/mapStore";
import { useForecastStore } from "../stores/forecastStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useUiStore } from "../stores/uiStore";
import { useAppStore } from "../stores/appStore";
import type { FieldFrame } from "../types/map";
import * as fieldImages from "./fieldImages";

const bytes = (url: string): ArrayBuffer => Uint8Array.from(atob(url.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;
const payloads = { field: bytes(field), radar: bytes(radar), radarSite: bytes(radarSite), satellite: bytes(satellite), outlook, reports, surface };
type Kind = keyof typeof payloads;

function useLayerQuery(kind: Kind, changed: boolean) {
  const time = changed ? "2026-10-09T21:00:00Z" : "2026-10-04T21:00:00Z";
  const fieldQuery = useFieldFrame(kind === "field" ? { model: "hrrr", product: "refc", runTime: time, forecastHour: 1 } : null);
  const radarQuery = useRadarFrame(changed ? "base" : null, kind === "radar");
  const siteQuery = useRadarSiteFrame(null, changed ? "KFDR" : "KTLX", kind === "radarSite");
  const satelliteQuery = useSatelliteFrame("infrared", time, kind === "satellite");
  const outlookQuery = useSpcOutlooks([kind === "outlook", false, false, false], time)[0];
  const reportQuery = useStormReports(time, 21600, "all", kind === "reports");
  const surfaceQuery = useSurfacePlots({ latitude: changed ? 40 : 35.2, longitude: -97.4 }, time, kind === "surface");
  return { field: fieldQuery, radar: radarQuery, radarSite: siteQuery, satellite: satelliteQuery, outlook: outlookQuery, reports: reportQuery, surface: surfaceQuery }[kind];
}

const savedMap = useMapStore.getState();
const savedForecast = useForecastStore.getState();
const savedGraphics = useGraphicsStore.getState();
const savedUi = useUiStore.getState();
const savedApp = useAppStore.getState();
beforeEach(() => {
  useAppStore.setState({ activeWorkspace: "map" });
  vi.stubGlobal("ImageData", class { constructor(public data: Uint8ClampedArray, public width: number, public height: number) {} });
});
afterEach(() => {
  useAppStore.setState(savedApp);
  cleanup(); useMapStore.setState(savedMap); useForecastStore.setState(savedForecast); useGraphicsStore.setState(savedGraphics); useUiStore.setState(savedUi); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

it.each([2, 4] as const)("keeps %i panels on the loaded hour until the next frame is ready, without a second playback delay", async (panelLayout) => {
  vi.useFakeTimers();
  const mapCatalog = parseMapCatalog(catalog);
  const frame = parseFieldFrame(payloads.field);
  const runTime = "2026-10-04T12:00:00Z";
  const layers = defaultLayerStates(); layers["model-field"].visible = true;
  useMapStore.setState({ panelLayout, productKey: "hgt-wind-500", layers, panels: Array.from({ length: 3 }, () => ({ productKey: null, layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime, forecastHour: 0, playing: true });
  useUiStore.setState({ speed: 1, loop: true });
  useGraphicsStore.setState({ effective: { ...savedGraphics.effective, profile: "balanced" } });
  const pendingLines: { hour: number; resolve: (lines: unknown) => void }[] = [];
  const client = new BackendClient({
    call: (_command, args) => new Promise((resolve) => { pendingLines.push({ hour: Number(args?.forecastHour), resolve }); }),
    listen: () => Promise.resolve(() => undefined),
  });
  const pending: { hour: number; resolve: (frame: FieldFrame) => void }[] = [];
  vi.spyOn(client, "getWeatherField").mockImplementation((_product, _run, hour) => new Promise((resolve) => { pending.push({ hour, resolve }); }));
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr"), mapCatalog);
  for (let panel = 0; panel < panelLayout; panel++) {
    queryClient.setQueryData(mapKeys.field(panel === 0 ? "hgt-wind-500" : defaultProductFor(mapCatalog, panel)?.key ?? "", runTime, 0), frame);
  }
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
    const next = pending.filter(({ hour }) => hour === 1);
    expect(next).toHaveLength(panelLayout);
    await act(async () => { next.slice(0, -1).forEach(({ resolve }) => { resolve(frame); }); await vi.advanceTimersByTimeAsync(0); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
    await act(async () => { next.at(-1)?.resolve(frame); await vi.advanceTimersByTimeAsync(0); });
    expect(useForecastStore.getState().forecastHour).toBe(0);
    expect(pendingLines.filter(({ hour }) => hour === 1)).toHaveLength(1);
    await act(async () => { pendingLines.filter(({ hour }) => hour === 1).forEach(({ resolve }) => { resolve(contours); }); await vi.advanceTimersByTimeAsync(0); });
    expect(useForecastStore.getState().forecastHour).toBe(1);
    act(() => { useForecastStore.getState().setPlaying(false); });
    await act(async () => {
      pending.forEach(({ resolve }) => { resolve(frame); });
      pendingLines.forEach(({ resolve }) => { resolve(contours); });
      await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS * 2);
    });
    expect(useForecastStore.getState().forecastHour).toBe(1);
  } finally { hook.unmount(); queryClient.clear(); }
});

it.each(Object.keys(payloads) as Kind[])("clears %s data when its selection changes", async (kind) => {
  let calls = 0;
  const transport: Transport = {
    call: vi.fn(() => {
      calls += 1;
      return calls === 1 ? Promise.resolve(payloads[kind]) : new Promise<unknown>(() => undefined);
    }),
    listen: () => Promise.resolve(() => undefined),
  };
  const client = new BackendClient(transport);
  const queryClient = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(({ changed }) => useLayerQuery(kind, changed), { initialProps: { changed: false }, wrapper });
  try {
    await waitFor(() => { expect(hook.result.current?.isSuccess).toBe(true); });
    expect(hook.result.current?.data).toBeDefined();
    hook.rerender({ changed: true });
    expect(hook.result.current?.data).toBeUndefined();
    expect(hook.result.current?.isPlaceholderData).toBe(false);
    await waitFor(() => { expect(calls).toBe(2); });
  } finally {
    hook.unmount();
    queryClient.clear();
  }
});

it.each(["rrfs-a", "nam", "nam-3km-conus", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5"])(
  "keeps %s field and wind/contour requests separate from HRRR at the same run and hour", async (model) => {
    const call = vi.fn((command: string, args?: Readonly<Record<string, unknown>>) =>
      args?.model === "hrrr" ? Promise.resolve(command === "get_weather_field" ? payloads.field : contours)
        : new Promise<unknown>(() => undefined));
    const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
    const queryClient = createQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
    );
    const runTime = "2026-10-04T18:00:00Z";
    const hook = renderHook(({ selected }) => ({
      field: useFieldFrame({ model: selected, product: "hgt-wind-500", runTime, forecastHour: 1 }),
      contours: useFieldContours("hgt-wind-500", runTime, 1, true, selected),
    }), { initialProps: { selected: "hrrr" }, wrapper });
    try {
      await waitFor(() => { expect(hook.result.current.field.isSuccess && hook.result.current.contours.isSuccess).toBe(true); });
      hook.rerender({ selected: model });
      expect(hook.result.current.field.data).toBeUndefined();
      expect(hook.result.current.contours.data).toBeUndefined();
      await waitFor(() => { expect(call).toHaveBeenCalledTimes(4); });
      for (const command of ["get_weather_field", "get_field_contours"]) {
        expect(call).toHaveBeenCalledWith(command, expect.objectContaining({ model, product: "hgt-wind-500", runTime, forecastHour: 1 }));
      }
      const catalogue = parseMapCatalog((modelCatalogs as Readonly<Record<string, unknown>>)[model]);
      expect(catalogue.fieldModel).toBe(model);
      expect(catalogue.products).toHaveLength(model.startsWith("refs-") ? 22 : model === "nam" ? 34 : model === "nam-3km-conus" ? 36 : 38);
      expect(catalogue.products.some(({ key }) => key === "ltng")).toBe(!["nam", "nam-3km-conus"].includes(model));
      const secondaryProduct = defaultProductFor(parseMapCatalog(catalog), 1)?.key;
      expect(catalogue.products.some(({ key }) => key === secondaryProduct)).toBe(!model.startsWith("refs-"));
    } finally { hook.unmount(); queryClient.clear(); }
  },
);

it.each([2, 4] as const)("prefetches %i panels and the configured future hours concurrently while paused", async (panelLayout) => {
  const mapCatalog = parseMapCatalog(catalog);
  const frame = parseFieldFrame(payloads.field);
  const runTime = "2026-10-04T12:00:00Z";
  const products = Array.from({ length: panelLayout }, (_, panel) => {
    const product = defaultProductFor(mapCatalog, panel);
    if (product === undefined) throw new Error("Fixture panel product missing");
    return product.key;
  });
  const layers = defaultLayerStates(); layers["model-field"].visible = true;
  useMapStore.setState({ panelLayout, productKey: null, layers, panels: Array.from({ length: 3 }, () => ({ productKey: null, layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime, forecastHour: 0, playing: false });
  useGraphicsStore.setState({ effective: { ...savedGraphics.effective, profile: "high" } });
  const client = new BackendClient({ call: () => Promise.resolve(contours), listen: () => Promise.resolve(() => undefined) });
  const pending: { product: string; hour: number; resolve: (frame: FieldFrame) => void }[] = [];
  vi.spyOn(client, "getWeatherField").mockImplementation((product, _run, hour) => new Promise((resolve) => { pending.push({ product, hour, resolve }); }));
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr"), mapCatalog);
  products.forEach((product) => { queryClient.setQueryData(mapKeys.field(product, runTime, 0), frame); });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await waitFor(() => { expect(pending.map(({ product, hour }) => [product, hour])).toEqual([1, 2].flatMap((hour) => products.map((product) => [product, hour]))); });
    await act(async () => {
      pending.slice(0, -1).forEach(({ resolve }) => { resolve(frame); });
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2 * panelLayout);
    expect(useForecastStore.getState().forecastHour).toBe(0);
    hook.unmount();
    await act(async () => {
      pending.forEach(({ resolve }) => { resolve(frame); });
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2 * panelLayout);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("prepares both active modes once across four matching panels, waits on cached fields, and cancels the old window", async () => {
  vi.useFakeTimers();
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
  queryClient.setQueryData(mapKeys.catalog("hrrr"), parseMapCatalog(catalog));
  const frame = parseFieldFrame(payloads.field);
  for (const hour of [0, 1, 2]) queryClient.setQueryData(mapKeys.field("refc", run, hour, "hrrr"), { ...frame, forecastHour: hour });
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
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
