// @vitest-environment jsdom
import models from "@contracts/public/soundings/models.catalog.json";
import mapCatalog from "@contracts/public/map/catalog.json";
import modelMapCatalogs from "@contracts/public/map/catalog.models.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { createTransport } from "../lib/backend/fixtureTransport";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import { useMapCatalog } from "./catalogQuery";
import { usePlayback } from "./usePlayback";
import { useForecast } from "../features/forecast/useForecast";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { useAppStore } from "../stores/appStore";

const savedForecast = useForecastStore.getState();
const savedMap = useMapStore.getState();
const savedApp = useAppStore.getState();
beforeEach(() => { useAppStore.setState({ activeWorkspace: "map" }); });
afterEach(() => {
  cleanup(); useForecastStore.setState(savedForecast); useMapStore.setState(savedMap); useAppStore.setState(savedApp);
  vi.useRealTimers(); vi.restoreAllMocks();
});

it.each(["hrrr", "rrfs-a", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5", "nam-3km-conus", "gfs", "aigfs", "openmeteo-ecmwf-ifs025", "ecmwf-ifs-9km", "hrw-wrf-arw", "hrw-wrf-nssl", "hrw-fv3"])("checks %s product availability for the selected run and hour", async (modelKey) => {
  const model = models.models.find((entry) => entry.key === modelKey);
  if (model === undefined) throw new Error("Model fixture missing");
  const raw = parseMapCatalog(modelKey === "hrrr" ? mapCatalog : (modelMapCatalogs as Readonly<Record<string, unknown>>)[modelKey]);
  const hidden = modelKey === "aigfs" ? "apcp" : raw.products.some((product) => product.key === "uh-2-5km-3h") ? "uh-2-5km-3h" : "snod";
  let resolveNext: ((value: unknown) => void) | undefined;
  const call = vi.fn((_command: string, args?: Record<string, unknown>) => Number(args?.forecastHour) === 18
    ? new Promise<unknown>((resolve) => { resolveNext = resolve; }) : Promise.resolve(raw));
  const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  useForecastStore.setState({ modelKey, runTime: null, forecastHour: 12 });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(() => ({ catalog: useMapCatalog(), forecast: useForecast() }), { wrapper });
  await waitFor(() => { expect(hook.result.current.catalog.data?.products).toHaveLength(raw.products.length); });
  expect(call).toHaveBeenLastCalledWith("get_map_catalog", { model: modelKey, runTime: model.suggestedRun, forecastHour: 12 });
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, 12))).toEqual(raw);
  act(() => { useForecastStore.getState().setHour(18); });
  await waitFor(() => { expect(resolveNext).toBeDefined(); });
  expect(hook.result.current.forecast).toMatchObject({ status: "ready", context: { hour: 18, fieldsAvailable: false } });
  expect(hook.result.current.catalog.data).toBeUndefined();
  act(() => { resolveNext?.({ ...raw, products: raw.products.filter((product) => product.key !== hidden) }); });
  await waitFor(() => { expect(hook.result.current.catalog.data?.products).toHaveLength(raw.products.length - 1); });
  expect(call).toHaveBeenLastCalledWith("get_map_catalog", { model: modelKey, runTime: model.suggestedRun, forecastHour: 18 });
  expect(hook.result.current.catalog.data?.products.some((product) => product.key === hidden)).toBe(false);
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, 18))).toEqual(hook.result.current.catalog.data);
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, 12))).toEqual(raw);
});

it.each(["hrrr", "rrfs-a", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5", "nam-3km-conus", "hrw-wrf-arw", "hrw-wrf-nssl", "hrw-fv3"])("only offers complete trailing UH intervals for %s", async (modelKey) => {
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  const client = new BackendClient(createTransport());
  const getCatalog = vi.spyOn(client, "getMapCatalog");
  useForecastStore.setState({ modelKey, runTime: null, forecastHour: 0 });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(useMapCatalog, { wrapper });
  for (const hour of [0, 1, 2, 3]) {
    act(() => { useForecastStore.getState().setHour(hour); });
    await waitFor(() => { expect(hook.result.current.isSuccess).toBe(true); });
    expect(getCatalog).toHaveBeenLastCalledWith(modelKey, expect.any(String), hour);
    expect(hook.result.current.data?.products.filter((product) => product.key.startsWith("uh-")).map((product) => product.key)).toEqual(
      hour === 0 ? [] : hour < 3 ? ["uh-0-3km", "uh-2-5km-1h"] : ["uh-0-3km", "uh-2-5km-1h", "uh-2-5km-3h"],
    );
  }
});

it("keeps normal NAM's catalogue static without UH products", async () => {
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  const client = new BackendClient(createTransport());
  const getCatalog = vi.spyOn(client, "getMapCatalog");
  useForecastStore.setState({ modelKey: "nam", runTime: null, forecastHour: 0 });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(useMapCatalog, { wrapper });
  await waitFor(() => { expect(hook.result.current.isSuccess).toBe(true); });
  act(() => { useForecastStore.getState().setHour(3); });
  expect(getCatalog).toHaveBeenCalledExactlyOnceWith("nam", undefined, undefined);
  expect(hook.result.current.data?.products.some((product) => product.key.startsWith("uh-"))).toBe(false);
});

it("waits for ECMWF product availability before advancing playback", async () => {
  vi.useFakeTimers();
  const modelKey = "ecmwf-ifs-9km";
  const model = models.models.find((entry) => entry.key === modelKey);
  if (model === undefined) throw new Error("ECMWF model fixture missing");
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  useForecastStore.setState({ modelKey, runTime: model.suggestedRun, forecastHour: 18, playing: true });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  renderHook(usePlayback, { wrapper });
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(useForecastStore.getState().forecastHour).toBe(18);
  expect(useForecastStore.getState().playing).toBe(true);
});
