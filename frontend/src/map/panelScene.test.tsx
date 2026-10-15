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
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import type { CommandName, Transport } from "../lib/backend/transport";
import type { FieldFrame } from "../types/map";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { defaultLayerStates, type SceneData } from "./layers/registry";
import { useFieldImage, usePanelScene } from "./panelScene";
import type { MapRuntime } from "./runtime/MapRuntime";

const savedMap = useMapStore.getState();
const savedForecast = useForecastStore.getState();
afterEach(() => {
  cleanup();
  useMapStore.setState(savedMap);
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

it("keeps a decoded radar frame on refresh failure, and clears it on product change", async () => {
  const layers = defaultLayerStates(); layers.radar.visible = true;
  useMapStore.setState({ layers, radarProduct: null, radarScope: "mosaic" });
  useForecastStore.setState({ modelKey: "hrrr", runTime: null, forecastHour: 5 });
  let failed = false;
  vi.stubGlobal("createImageBitmap", vi.fn(() => Promise.resolve({ width: 2, height: 2 })));
  const replies: Partial<Record<CommandName, unknown>> = { get_map_catalog: catalog, get_model_catalog: models, get_station_catalog: stations, get_radar_sites: sites, get_place_labels: places };
  const transport: Transport = {
    call: (command) => command === "get_radar_frame"
      ? failed ? Promise.reject(new Error("Refresh failed")) : Promise.resolve(bytes(radarPng))
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
    const image = scene.radar;
    failed = true;
    await act(async () => { await queryClient.refetchQueries({ queryKey: mapKeys.radar(null), exact: true }); });
    await waitFor(() => { expect(queryClient.getQueryState(mapKeys.radar(null))?.status).toBe("error"); });
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
  useMapStore.setState({ layers });
  useForecastStore.setState({ modelKey: "hrrr", runTime: null, forecastHour: 5 });
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
