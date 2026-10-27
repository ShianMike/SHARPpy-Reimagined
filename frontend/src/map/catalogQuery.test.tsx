// @vitest-environment jsdom
import models from "@contracts/public/soundings/models.catalog.json";
import modelMapCatalogs from "@contracts/public/map/catalog.models.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, QUERY_KEYS } from "../lib/backend/queryOptions";
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

it.each(["gfs", "aigfs", "openmeteo-ecmwf-ifs025", "ecmwf-ifs-9km", "hrw-wrf-arw", "hrw-wrf-nssl", "hrw-fv3"])("checks %s product availability for the selected run and hour", async (modelKey) => {
  const model = models.models.find((entry) => entry.key === modelKey);
  if (model === undefined) throw new Error("ECMWF model fixture missing");
  const raw = parseMapCatalog((modelMapCatalogs as Readonly<Record<string, unknown>>)[modelKey]);
  const hidden = modelKey === "aigfs" ? "apcp" : modelKey.startsWith("hrw-") ? "maxuvv" : "snod";
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
  act(() => { useForecastStore.getState().setHour(18); });
  await waitFor(() => { expect(resolveNext).toBeDefined(); });
  expect(hook.result.current.forecast).toMatchObject({ status: "ready", context: { hour: 18, fieldsAvailable: false } });
  expect(hook.result.current.catalog.data).toBeUndefined();
  act(() => { resolveNext?.({ ...raw, products: raw.products.filter((product) => product.key !== hidden) }); });
  await waitFor(() => { expect(hook.result.current.catalog.data?.products).toHaveLength(raw.products.length - 1); });
  expect(call).toHaveBeenLastCalledWith("get_map_catalog", { model: modelKey, runTime: model.suggestedRun, forecastHour: 18 });
  expect(hook.result.current.catalog.data?.products.some((product) => product.key === hidden)).toBe(false);
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
