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
import { useDrawnFields, usePanelField } from "./panelField";
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

it.each(["hrrr", "rap", "rdps", "hrdps", "rrfs-a", "refs", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5", "gfs", "aigfs", "openmeteo-ecmwf-ifs025", "ecmwf-ifs-9km"])("checks %s product availability for the selected run and hour", async (modelKey) => {
  const model = models.models.find((entry) => entry.key === modelKey);
  if (model === undefined) throw new Error("Model fixture missing");
  const raw = parseMapCatalog(modelKey === "hrrr" ? mapCatalog : (modelMapCatalogs as Readonly<Record<string, unknown>>)[modelKey]);
  const hidden = modelKey === "refs" ? "hgt-wind-850" : modelKey === "aigfs" ? "apcp" : modelKey === "hrdps" ? "snow-10-1"
    : raw.products.some((product) => product.key === "uh-2-5km-3h") ? "uh-2-5km-3h" : "snod";
  const firstHour = modelKey === "refs" ? 1 : 12;
  let resolveNext: ((value: unknown) => void) | undefined;
  const call = vi.fn((_command: string, args?: Record<string, unknown>) => Number(args?.forecastHour) === 18
    ? new Promise<unknown>((resolve) => { resolveNext = resolve; }) : Promise.resolve(raw));
  const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  useForecastStore.setState({ modelKey, runTime: null, forecastHour: modelKey === "refs" ? 0 : firstHour });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(() => ({ catalog: useMapCatalog(), forecast: useForecast() }), { wrapper });
  await waitFor(() => { expect(hook.result.current.catalog.data?.products).toHaveLength(raw.products.length); });
  expect(call).toHaveBeenLastCalledWith("get_map_catalog", { model: modelKey, runTime: model.suggestedRun, forecastHour: firstHour });
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, firstHour))).toEqual(raw);
  act(() => { useForecastStore.getState().setHour(18); });
  await waitFor(() => { expect(resolveNext).toBeDefined(); });
  expect(hook.result.current.forecast).toMatchObject({ status: "ready", context: { hour: 18, fieldsAvailable: true } });
  expect(hook.result.current.catalog.data).toEqual(raw);
  expect(hook.result.current.catalog.isPlaceholderData).toBe(true);
  act(() => { resolveNext?.({ ...raw, products: raw.products.filter((product) => product.key !== hidden) }); });
  await waitFor(() => { expect(hook.result.current.catalog.data?.products).toHaveLength(raw.products.length - 1); });
  expect(call).toHaveBeenLastCalledWith("get_map_catalog", { model: modelKey, runTime: model.suggestedRun, forecastHour: 18 });
  expect(hook.result.current.catalog.data?.products.some((product) => product.key === hidden)).toBe(false);
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, 18))).toEqual(hook.result.current.catalog.data);
  expect(queryClient.getQueryData(mapKeys.catalog(modelKey, model.suggestedRun, firstHour))).toEqual(raw);
});

it.each(["hrrr", "rrfs-a", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5"])("only offers complete trailing UH intervals for %s", async (modelKey) => {
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
    await waitFor(() => { expect(hook.result.current.isSuccess && !hook.result.current.isPlaceholderData).toBe(true); });
    expect(getCatalog).toHaveBeenLastCalledWith(modelKey, expect.any(String), hour);
    expect(hook.result.current.data?.products.filter((product) => product.key.startsWith("uh-")).map((product) => product.key)).toEqual(
      hour === 0 ? [] : hour < 3 ? ["uh-0-3km", "uh-0-3km-run", "uh-2-5km-1h", "uh-2-5km-run"] : ["uh-0-3km", "uh-0-3km-run", "uh-2-5km-1h", "uh-2-5km-3h", "uh-2-5km-run"],
    );
  }
});

it("keeps the palette but waits for HRDPS inventory before loading the previous hour", async () => {
  const modelCatalog = parseModelCatalog(models);
  const model = modelCatalog.models.find((entry) => entry.key === "hrdps");
  if (model === undefined) throw new Error("HRDPS fixture missing");
  const catalog = parseMapCatalog(modelMapCatalogs.hrdps);
  const run = model.suggestedRun;
  const client = new BackendClient(createTransport());
  const getField = vi.spyOn(client, "getWeatherField").mockImplementation(() => new Promise(() => undefined));
  let finishInventory!: (value: typeof catalog) => void;
  const getCatalog = vi.spyOn(client, "getMapCatalog").mockImplementation(
    () => new Promise((resolve) => { finishInventory = resolve; }),
  );
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, modelCatalog);
  queryClient.setQueryData(mapKeys.catalog(model.key, run, 24), catalog);
  useForecastStore.setState({ modelKey: model.key, forecastHour: 24, runTime: run });
  useMapStore.getState().setProduct("qpf-24h");
  useMapStore.getState().setLayerVisible("model-field", true);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(() => ({ field: usePanelField(0), drawn: useDrawnFields() }), { wrapper });
  await waitFor(() => { expect(getField).toHaveBeenCalledWith("qpf-24h", run, 24, model.key, expect.anything()); });

  act(() => { useForecastStore.getState().setHour(23); });
  await waitFor(() => { expect(getCatalog).toHaveBeenCalledWith(model.key, run, 23); });
  expect(hook.result.current.field.product?.key).toBe("qpf-24h");
  expect(hook.result.current.field.request).toBeNull();
  expect(hook.result.current.drawn).toEqual([]);
  expect(getField).toHaveBeenCalledTimes(1);

  act(() => { finishInventory({ ...catalog, products: catalog.products.filter((product) => product.minimumForecastHour <= 23) }); });
  await waitFor(() => { expect(getField).toHaveBeenCalledWith("tmp-2m", run, 23, model.key, expect.anything()); });
});

it.each(["hrw-wrf-arw", "hrw-wrf-nssl", "hrw-fv3", "sref", "href"])("falls back from hidden %s before requesting map products", async (modelKey) => {
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  const client = new BackendClient(createTransport());
  const getCatalog = vi.spyOn(client, "getMapCatalog");
  useForecastStore.setState({ modelKey, runTime: "2000-01-01T00:00:00Z", forecastHour: 0 });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(() => ({ catalog: useMapCatalog(), forecast: useForecast() }), { wrapper });
  await waitFor(() => { expect(hook.result.current.catalog.isSuccess).toBe(true); });
  expect(getCatalog).toHaveBeenCalledExactlyOnceWith("hrrr", models.models[0]?.suggestedRun, 0);
  expect(hook.result.current.forecast).toMatchObject({ status: "ready", context: { model: { key: "hrrr" }, fieldsAvailable: true } });
  expect(queryClient.getQueryData<ReturnType<typeof parseModelCatalog>>(QUERY_KEYS.models)?.models.some((model) => model.key === "nam")).toBe(true);
});

it.each(["nam", "nam-3km-conus"])("defaults %s to its last archived run and accepts an earlier run", async (modelKey) => {
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  const client = new BackendClient(createTransport());
  useForecastStore.setState({ modelKey, runTime: "2026-11-03T12:00:00Z", forecastHour: 12 });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(useForecast, { wrapper });
  await waitFor(() => {
    expect(hook.result.current).toMatchObject({ status: "ready", context: {
      model: { key: modelKey }, run: "2026-11-03T06:00:00Z", hour: 12, fieldsAvailable: true,
    } });
  });
  act(() => { useForecastStore.getState().setRun("2022-05-15T12:00:00Z"); });
  await waitFor(() => {
    expect(hook.result.current).toMatchObject({ status: "ready", context: { model: { key: modelKey }, run: "2022-05-15T12:00:00Z" } });
  });
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
