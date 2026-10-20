// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import stations from "@contracts/public/stations.catalog.full.json";
import sites from "@contracts/public/map/radar-sites.json";
import places from "@contracts/public/map/places.json";
import outlook from "@contracts/public/map/outlook.cat.json";
import fieldPng from "@contracts/public/map/field.shrf?url&inline";
import radarPng from "@contracts/public/map/radar.shrf?url&inline";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys } from "../lib/backend/queryOptions";
import { encodeShrf, parseFieldFrame, parseShrf } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import type { CommandName, Transport } from "../lib/backend/transport";
import type { FieldFrame } from "../types/map";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import { defaultLayerStates, type SceneData } from "./layers/registry";
import { domainPath, useFieldImage, usePanelScene, usePngImage } from "./panelScene";
import type { MapRuntime } from "./runtime/MapRuntime";

const savedMap = useMapStore.getState();
const savedLive = useLiveTrackingStore.getState();
const savedForecast = useForecastStore.getState();

it("exposes image decode failure, ignores an obsolete rejection, and retries the displayed frame", async () => {
  const refetch = vi.fn();
  let rejectOld: (error: Error) => void = () => undefined;
  const decode = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject; }))
    .mockRejectedValueOnce(new Error("corrupt PNG")).mockResolvedValue({ width: 2, height: 2 });
  vi.stubGlobal("createImageBitmap", decode);
  const bounds = { west: -100, east: -90, south: 30, north: 40 };
  const older = { png: new Blob(["old"]), bounds, validTime: "2026-10-04T18:00:00Z" };
  const newer = { png: new Blob(["new"]), bounds, validTime: "2026-10-04T19:00:00Z" };
  const hook = renderHook(({ data, enabled }) => usePngImage({ data, isError: false, refetch }, enabled, "radar"),
    { initialProps: { data: older, enabled: true } });
  hook.rerender({ data: newer, enabled: true });
  await waitFor(() => { expect(hook.result.current.issue).toContain("could not be decoded"); });
  await act(async () => { rejectOld(new Error("obsolete")); await Promise.resolve(); });
  expect(hook.result.current.issue).toContain("could not be decoded");
  act(() => { hook.result.current.retry(); });
  await waitFor(() => { expect(hook.result.current.image).not.toBeNull(); });
  expect(hook.result.current.issue).toBeNull();
  expect(refetch).toHaveBeenCalledTimes(1);
  hook.rerender({ data: newer, enabled: false });
  expect(hook.result.current.image).toBeNull();
  act(() => { hook.result.current.retry(); });
  expect(refetch).toHaveBeenCalledTimes(1);
});

it("uses the same closed grid perimeter without adding a zero-length segment", () => {
  const catalogue = parseModelCatalog(models);
  const reference = catalogue.models.find((model) => model.key === "hrrr");
  if (reference === undefined) throw new Error("Missing model fixture");
  for (const model of catalogue.models.filter((item) => item.key === "hrrr" || item.key === "nam-3km-conus" || item.key === "rrfs-a" || item.key.startsWith("refs-m"))) {
    const path = domainPath(model);
    expect(path).toBe(model.outline);
    expect(path).toEqual(reference.outline);
    expect(path?.length).toBeGreaterThan(100);
    expect(path?.[0]).toEqual(path?.at(-1));
    expect(domainPath(model)).toBe(path);
  }
});
afterEach(() => {
  cleanup();
  useMapStore.setState(savedMap);
  useLiveTrackingStore.setState(savedLive);
  useForecastStore.setState(savedForecast);
  vi.unstubAllGlobals();
});

const bytes = (url: string): ArrayBuffer => Uint8Array.from(atob(url.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;

it("shares a frame across panels and reopens, colouring only when its Sharp image is read", () => {
  const construct = vi.fn(function (data: Uint8ClampedArray, width: number, height: number) { return { data, width, height }; });
  vi.stubGlobal("ImageData", construct);
  const frame = parseFieldFrame(bytes(fieldPng));
  const palette = parseMapCatalog(catalog).products[0]?.palette;
  if (palette === undefined) throw new Error("Fixture palette missing");
  const first = renderHook(() => useFieldImage(frame, palette));
  const second = renderHook(() => useFieldImage(frame, palette));
  expect(second.result.current).toBe(first.result.current);
  expect(construct).not.toHaveBeenCalled();
  expect(second.result.current?.image).toBe(first.result.current?.image);
  expect(construct).toHaveBeenCalledTimes(1);
  const image = first.result.current;
  first.unmount(); second.unmount();
  const initialProps: { selected: FieldFrame | undefined } = { selected: frame };
  const reopened = renderHook(({ selected }) => useFieldImage(selected, palette), { initialProps });
  expect(reopened.result.current).toBe(image);
  reopened.rerender({ selected: undefined });
  expect(reopened.result.current).toBeNull();
  reopened.rerender({ selected: { ...frame, cells: frame.cells.slice() } });
  expect(reopened.result.current === image).toBe(false);
  expect(construct).toHaveBeenCalledTimes(1);
  expect(reopened.result.current?.image).not.toBe(image?.image);
  expect(construct).toHaveBeenCalledTimes(2);
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
    const key = mapKeys.radar(null, new Date(useLiveTrackingStore.getState().now).toISOString().replace(".000Z", "Z"));
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

it.each([true, false])("draws the SPC outlook only when it covers the selected time (%s)", async (covers) => {
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
  } finally {
    hook.unmount();
    queryClient.clear();
  }
});
