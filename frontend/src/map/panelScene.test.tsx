// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import stations from "@contracts/public/stations.catalog.full.json";
import sites from "@contracts/public/map/radar-sites.json";
import places from "@contracts/public/map/places.json";
import outlook from "@contracts/public/map/outlook.cat.json";
import contourFixture from "@contracts/public/map/contours.json";
import fieldPng from "@contracts/public/map/field.shrf?url&inline";
import radarPng from "@contracts/public/map/radar.shrf?url&inline";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createTransport } from "../lib/backend/fixtureTransport";
import { createQueryClient, mapKeys } from "../lib/backend/queryOptions";
import { encodeShrf, parseShrf } from "../lib/contracts/frames";
import type { CommandName, Transport } from "../lib/backend/transport";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import { defaultLayerStates, type SceneData } from "./layers/registry";
import { usePanelScene } from "./panelScene";
import type { MapRuntime } from "./runtime/MapRuntime";

const savedMap = useMapStore.getState();
const savedLive = useLiveTrackingStore.getState();
const savedForecast = useForecastStore.getState();

afterEach(() => {
  cleanup();
  useMapStore.setState(savedMap);
  useLiveTrackingStore.setState(savedLive);
  useForecastStore.setState(savedForecast);
  vi.unstubAllGlobals();
});

const bytes = (url: string): ArrayBuffer => Uint8Array.from(atob(url.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;

it.each([false, true])("loads selected-time pressure lines for a standalone or pressure/wind product (wind=%s)", async (wind) => {
  const layers = defaultLayerStates();
  layers["model-field"].visible = wind;
  useMapStore.setState({ view: "map", activePanel: 0, layers, productKey: wind ? "mslp" : null });
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T18:00:00Z", forecastHour: 3 });
  const template = catalog.products[0];
  if (template === undefined) throw new Error("Missing catalogue fixture");
  const pressureProduct = { ...template,
    key: "mslp", label: "Mean Sea-level Pressure", shortLabel: "MSLP", category: "Surface", wind,
    palette: { ...template.palette, units: "hPa" }, contour: { interval: 4, label: "hPa", colour: "#101418", width: 1.2 },
  };
  const pressureCatalog = { ...catalog, products: [...catalog.products.filter((product) => product.key !== "mslp"), pressureProduct] };
  const raw = parseShrf(bytes(fieldPng));
  const field = encodeShrf({ ...raw.header, product: "mslp", forecastHour: 3, validTime: "2026-10-04T21:00:00Z", units: "hPa" }, raw.body);
  const fallback = createTransport();
  const calls = vi.fn((command: CommandName, params?: Record<string, unknown>) => {
    if (command === "get_field_contours") return Promise.resolve({ ...contourFixture, product: "mslp", interval: 4, unit: "hPa", wind: wind ? contourFixture.wind : null,
      forecastHour: params?.forecastHour, labels: [{ position: [-99.5, 35.5], value: 1012 }] });
    const replies: Partial<Record<CommandName, unknown>> = { get_map_catalog: pressureCatalog, get_model_catalog: models,
      get_station_catalog: stations, get_place_labels: places, get_weather_field: field };
    return command in replies ? Promise.resolve(replies[command]) : fallback.call(command, params);
  });
  const transport: Transport = { call: calls, listen: () => Promise.resolve(() => undefined) };
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  await waitFor(() => { expect(hook.result.current.catalog.data).toBeDefined(); });
  if (wind) await waitFor(() => { expect(scene.contours?.forecastHour).toBe(3); });
  expect(calls.mock.calls.filter(([method]) => method === "get_field_contours")).toHaveLength(wind ? 1 : 0);
  act(() => {
    if (wind) useMapStore.getState().setLayerOpacity("isobars", 0);
    useMapStore.getState().setLayerVisible("isobars", true);
  });
  await waitFor(() => { expect(scene.isobars?.forecastHour).toBe(3); });
  expect(scene.isobars?.labels?.[0]?.value).toBe(1012);
  expect(calls).toHaveBeenCalledWith("get_field_contours", expect.objectContaining({ product: "mslp", model: "hrrr", runTime: "2026-10-04T18:00:00Z", forecastHour: 3 }));
  if (wind) {
    expect(scene.contours).toBe(scene.isobars);
    expect(scene.contourSpec).toEqual(pressureProduct.contour);
    expect(calls.mock.calls.filter(([method]) => method === "get_field_contours")).toHaveLength(1);
  } else {
    expect(calls.mock.calls.filter(([method]) => method === "get_weather_field")).toHaveLength(0);
    act(() => { useForecastStore.getState().setHour(4); });
    await waitFor(() => { expect(scene.isobars?.forecastHour).toBe(4); });
    act(() => { useMapStore.getState().setLayerVisible("isobars", false); });
    await waitFor(() => { expect(scene.isobars).toBeNull(); });
    const completed = calls.mock.calls.filter(([method]) => method === "get_field_contours").length;
    act(() => { useForecastStore.getState().setHour(5); });
    await waitFor(() => { expect(hook.result.current.catalog.data).toBeDefined(); });
    expect(calls.mock.calls.filter(([method]) => method === "get_field_contours")).toHaveLength(completed);
  }
  hook.unmount(); queryClient.clear();
});

it.each(["map", "live"] as const)("renders storm reports at the %s clock with the selected hazard and event window", async (view) => {
  const layers = defaultLayerStates();
  layers["storm-reports"].visible = true;
  useMapStore.setState((state) => ({ view, activePanel: 0, layers, reportFilter: "all", reportWindowS: 21600,
    livePanels: state.livePanels.map((panel, index) => index === 0 ? { ...panel, layers } : panel) }));
  const liveTime = Date.parse("2026-10-17T08:00:00Z");
  useLiveTrackingStore.setState({ now: liveTime, selectedTime: null });
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T18:00:00Z", forecastHour: 3 });
  const transport = createTransport(), calls = vi.spyOn(transport, "call");
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  const time = view === "live" ? "2026-10-17T08:00:00Z" : "2026-10-04T21:00:00Z";
  await waitFor(() => { expect(scene.reports?.length).toBeGreaterThan(0); });
  expect(calls).toHaveBeenCalledWith("get_storm_reports", expect.objectContaining({ validTime: time, windowS: 21600, hazardFilter: "all" }));
  const previous = scene.reports;
  act(() => {
    if (view === "live") useLiveTrackingStore.getState().selectTime(liveTime - 30 * 60_000);
    else useForecastStore.getState().setHour(4);
    useMapStore.getState().setReportFilter("hail");
    useMapStore.getState().setReportWindowS(7200);
  });
  const next = view === "live" ? "2026-10-17T07:30:00Z" : "2026-10-04T22:00:00Z";
  await waitFor(() => {
    expect(scene.reports?.length).toBeGreaterThan(0);
    expect(scene.reports).not.toBe(previous);
  });
  expect(calls).toHaveBeenCalledWith("get_storm_reports", expect.objectContaining({ validTime: next, windowS: 7200, hazardFilter: "hail" }));
  expect(scene.reports?.every((report) => report.category === "h" || report.category === "significant" && report.symbol === "diamond")).toBe(true);
  act(() => { useMapStore.getState().setLayerVisible("storm-reports", false); });
  await waitFor(() => { expect(scene.reports).toBeNull(); });
  hook.unmount(); queryClient.clear();
});

it("keeps radar visible during decoding and refresh failure, and clears it on product change", async () => {
  const layers = defaultLayerStates(); layers.radar.visible = true;
  useMapStore.setState((state) => ({ view: "live", activePanel: 0, livePanels: state.livePanels.map((panel, index) => index === 0 ? { ...panel, layers, radarProduct: null, radarScope: "mosaic" } : panel) }));
  useForecastStore.setState({ modelKey: "hrrr", runTime: null, forecastHour: 5 });
  let failed = false;
  let waiting = false;
  const raw = parseShrf(bytes(radarPng));
  const frame = encodeShrf({ ...raw.header, validTime: new Date(savedLive.now - 10 * 60_000).toISOString().replace(".000Z", "Z") }, raw.body);
  const decode = vi.fn(() => Promise.resolve({ width: 2, height: 2 }));
  vi.stubGlobal("createImageBitmap", decode);
  const replies: Partial<Record<CommandName, unknown>> = { get_map_catalog: catalog, get_model_catalog: models, get_station_catalog: stations, get_radar_sites: sites, get_place_labels: places };
  const transport: Transport = {
    call: (command) => command === "get_radar_frame"
      ? waiting ? new Promise(() => undefined) : failed ? Promise.reject(new Error("Refresh failed")) : Promise.resolve(frame)
      : Promise.resolve(replies[command]),
    listen: () => Promise.resolve(() => undefined),
  };
  const queryClient = createQueryClient();
  const client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  try {
    await waitFor(() => { expect(scene.radar).toBeTruthy(); });
    const key = mapKeys.radar(null);
    const first = scene.radar;
    let ready: (image: { width: number; height: number }) => void = () => undefined;
    decode.mockImplementationOnce(() => new Promise((resolve) => { ready = resolve; }));
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    expect(scene.radar).toBe(first);
    await act(async () => { ready({ width: 2, height: 2 }); await Promise.resolve(); });
    await waitFor(() => { expect(scene.radar !== first).toBe(true); });
    const image = scene.radar;
    failed = true;
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(queryClient.getQueryState(key)?.status).toBe("error"); });
    expect(scene.radar).toBe(image);
    waiting = true;
    act(() => { useLiveTrackingStore.getState().tick(savedLive.now + 60_000); });
    expect(scene.radar).toBe(image);
    act(() => { useMapStore.getState().setRadarProduct("base"); });
    await waitFor(() => { expect(scene.radar).toBeNull(); });
  } finally {
    hook.unmount(); queryClient.clear();
  }
});

it.each([true, false])("respects SPC coverage (%s) and accepts a valid empty outlook on refresh", async (covers) => {
  const layers = defaultLayerStates();
  layers["spc-cat"].visible = true;
  useMapStore.setState({ view: "map", activePanel: 0, layers });
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-04T21:00:00Z"), selectedTime: null });
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T18:00:00Z", forecastHour: 3 });
  const replies: Partial<Record<CommandName, unknown>> = {
    get_map_catalog: catalog, get_model_catalog: models, get_station_catalog: stations,
    get_radar_sites: sites, get_place_labels: places,
    get_spc_outlook: { ...outlook, outlook: { ...outlook.outlook, covers } },
  };
  const transport: Transport = {
    call: (command) => Promise.resolve(replies[command]),
    listen: () => Promise.resolve(() => undefined),
  };
  const queryClient = createQueryClient();
  const client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const scene: Partial<SceneData> = {};
  const setScene = vi.fn((data: Partial<SceneData>) => { Object.assign(scene, data); });
  const runtime = { setScene, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  try {
    await waitFor(() => { expect(queryClient.getQueryData(["outlooks", "cat", "2026-10-04T21:00:00Z"])).toBeDefined(); });
    await waitFor(() => { expect(scene.outlooks?.cat !== undefined).toBe(covers); });
    expect(scene.outlooks).toBeDefined();
    replies.get_spc_outlook = {
      ...outlook, outlook: { ...outlook.outlook, covers: true, areas: [], issued: "2026-10-04T20:00:00Z" },
    };
    await act(async () => { await queryClient.refetchQueries({ queryKey: mapKeys.outlook("cat", "2026-10-04T21:00:00Z"), exact: true }); });
    await waitFor(() => { expect(scene.outlooks?.cat).toMatchObject({ covers: true, areas: [], issued: "2026-10-04T20:00:00Z" }); });
  } finally {
    hook.unmount();
    queryClient.clear();
  }
});
