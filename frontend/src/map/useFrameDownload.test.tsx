// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import contours from "@contracts/public/map/contours.json";
import field from "@contracts/public/map/field.shrf?url&inline";
import models from "@contracts/public/soundings/models.catalog.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { BackendFailure } from "../lib/backend/errors";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseContourLines } from "../lib/contracts/contours";
import { parseModelCatalog } from "../lib/contracts/soundings";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { defaultLayerStates } from "./layers/registry";
import { useFrameDownload } from "./useFrameDownload";

const savedMap = useMapStore.getState();
const savedForecast = useForecastStore.getState();
afterEach(() => { cleanup(); useMapStore.setState(savedMap); useForecastStore.setState(savedForecast); vi.restoreAllMocks(); });
const frame = parseFieldFrame(Uint8Array.from(atob(field.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);

function setup() {
  const layers = defaultLayerStates(); layers["model-field"].visible = true;
  useMapStore.setState({ panelLayout: 4, productKey: "hgt-wind-500", layers,
    panels: Array.from({ length: 3 }, () => ({ productKey: "hgt-wind-500", layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 1, playing: true });
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr", "2026-10-04T12:00:00Z", 1), parseMapCatalog(catalog));
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  const storedFields = new Set<number>(), storedWinds = new Set<number>();
  const fields = vi.spyOn(client, "getWeatherField").mockImplementation((_product, _run, hour) => { storedFields.add(hour); return Promise.resolve(frame); });
  const winds = vi.spyOn(client, "getFieldContours").mockImplementation((_product, _run, hour) => { storedWinds.add(hour); return Promise.resolve(parseContourLines(contours)); });
  const saved = vi.spyOn(client, "getCachedForecastHours").mockImplementation((_model, _products, _run, hours) => Promise.resolve(hours.filter((hour) => storedFields.has(hour) && storedWinds.has(hour))));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  return { queryClient, client, fields, winds, saved, storedFields, wrapper };
}

it.each([
  ...["gfs", "aigfs", "openmeteo-ecmwf-ifs025", "ecmwf-ifs-9km", "hrw-wrf-arw", "hrw-wrf-nssl", "hrw-fv3"].map((modelKey) => ({ modelKey, individual: false })),
  { modelKey: "gfs", individual: true },
])("keeps $modelKey downloads running while another hour's catalogue loads (individual: $individual)", async ({ modelKey, individual }) => {
  const { queryClient, client, fields, storedFields, wrapper } = setup();
  const parsed = parseModelCatalog(models);
  const model = parsed.models.find((item) => item.key === modelKey);
  if (model === undefined) throw new Error("Model fixture missing");
  queryClient.setQueryData(QUERY_KEYS.models, { ...parsed, models: parsed.models.map((item) => item.key === modelKey ? { ...item, forecastHours: [0, 6, 12] } : item) });
  const available = { ...parseMapCatalog(catalog), fieldModel: modelKey };
  queryClient.setQueryData(mapKeys.catalog(modelKey, model.suggestedRun, 0), available);
  useForecastStore.setState({ modelKey, runTime: model.suggestedRun, forecastHour: 0 });
  let finishCatalog!: (value: typeof available) => void;
  const check = vi.spyOn(client, "getMapCatalog").mockImplementation(() => new Promise((resolve) => { finishCatalog = resolve; }));
  let finishFrame!: () => void;
  fields.mockImplementationOnce((_product, _run, hour) => new Promise((resolve) => { finishFrame = () => { storedFields.add(hour); resolve(frame); }; }));
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { if (individual) void hook.result.current.retryHour(0); else hook.result.current.toggle(); });
    await waitFor(() => { expect(fields).toHaveBeenCalledTimes(individual ? 1 : 3); });
    act(() => { useForecastStore.getState().setHour(6); });
    await waitFor(() => { expect(check).toHaveBeenCalledWith(modelKey, model.suggestedRun, 6); });
    expect(fields.mock.calls[0]?.[4]?.aborted).toBe(false);
    act(() => { finishCatalog(available); });
    await waitFor(() => { expect(hook.result.current.disabled).toBe(false); });
    expect(hook.result.current.running).toBe(!individual);
    act(() => { finishFrame(); });
    await waitFor(() => { expect(hook.result.current.cached).toBe(individual ? 1 : 3); expect(hook.result.current.running).toBe(false); });
  } finally { hook.unmount(); queryClient.clear(); }
});

it("downloads every hour and winds once across four matching panels, keeps the selected hour, and reuses ready queries", async () => {
  const { queryClient, fields, winds, wrapper } = setup();
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
    expect(fields).toHaveBeenCalledTimes(hook.result.current.total);
    expect(winds).toHaveBeenCalledTimes(hook.result.current.total);
    expect(new Set(fields.mock.calls.map((call) => call[2])).size).toBe(hook.result.current.total);
    expect(useForecastStore.getState()).toMatchObject({ forecastHour: 1, playing: false });
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.running).toBe(false); });
    expect(fields).toHaveBeenCalledTimes(hook.result.current.total);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("downloads only complete intervals for mixed 1-hour and 3-hour products, including during a catalogue refresh", async () => {
  const { queryClient, client, fields, winds, saved, storedFields, wrapper } = setup();
  const run = "2026-10-04T12:00:00Z", eligible = [3, 4, 5];
  const parsed = parseModelCatalog(models), available = parseMapCatalog(catalog);
  queryClient.setQueryData(QUERY_KEYS.models, { ...parsed, models: parsed.models.map((model) => model.key === "hrrr" ? { ...model, forecastHours: [0, 1, 2, 3, 4, 5] } : model) });
  queryClient.setQueryData(mapKeys.catalog("hrrr", run, 3), available);
  useForecastStore.setState({ forecastHour: 3 });
  useMapStore.setState({ productKey: "tmp-2m", panels: ["uh-2-5km-1h", "uh-2-5km-3h", "tmp-2m"].map((productKey) => ({ productKey, layers: useMapStore.getState().layers })) });
  saved.mockImplementation((_model, _products, _run, hours) => Promise.resolve(hours.filter((hour) => storedFields.has(hour))));
  let finishFrame!: () => void;
  fields.mockImplementationOnce((_product, _run, hour) => new Promise((resolve) => { finishFrame = () => { storedFields.add(hour); resolve(frame); }; }));
  let finishCatalog!: (value: typeof available) => void;
  vi.spyOn(client, "getMapCatalog").mockImplementation(() => new Promise((resolve) => { finishCatalog = resolve; }));
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    expect(hook.result.current.total).toBe(3);
    expect(hook.result.current.missing).toEqual(eligible);
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(fields).toHaveBeenCalledTimes(9); });
    act(() => { useForecastStore.getState().setHour(4); });
    await waitFor(() => { expect(finishCatalog).toBeDefined(); });
    expect(hook.result.current).toMatchObject({ total: 3, running: true });
    act(() => { hook.result.current.refresh(); finishFrame(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
    expect(saved).toHaveBeenLastCalledWith("hrrr", ["tmp-2m", "uh-2-5km-1h", "uh-2-5km-3h"], run, eligible);
    expect([...new Set(fields.mock.calls.map((call) => call[2]))]).toEqual(eligible);
    expect(winds).not.toHaveBeenCalled();
    expect(hook.result.current).toMatchObject({ total: 3, loaded: 3, cached: 3, ready: 3, missing: [], running: false });
    expect(hook.result.current.hourStates).toEqual([0, 1, 2, 3, 4, 5].map((hour) => ({ hour, state: hour < 3 ? "missing" : "cached" })));
    await act(async () => { await hook.result.current.retryHour(0); });
    expect(fields).toHaveBeenCalledTimes(9);
    act(() => { finishCatalog(available); });
  } finally { hook.unmount(); queryClient.clear(); }
});

it("keeps saved hours separate from memory readiness after eviction and reconciles a clear", async () => {
  const { queryClient, wrapper, saved } = setup();
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
    expect(hook.result.current.ready).toBe(hook.result.current.total);
    act(() => { queryClient.removeQueries({ queryKey: ["fields"] }); });
    await waitFor(() => { expect(hook.result.current.ready).toBe(0); });
    expect(hook.result.current.cached).toBe(hook.result.current.total);
    expect(hook.result.current.message).toContain("0 ready in memory");
    saved.mockResolvedValue([]);
    act(() => { hook.result.current.refresh(); });
    await waitFor(() => { expect(hook.result.current.cached).toBe(0); });
    expect(hook.result.current.complete).toBe(false);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("does not claim success for a failed hour, and retry completes the missing wind data", async () => {
  const { queryClient, winds, wrapper } = setup();
  winds.mockRejectedValueOnce(new Error("unavailable"));
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.running).toBe(false); });
    expect(hook.result.current).toMatchObject({ complete: false, failed: true });
    expect(hook.result.current.loaded).toBe(hook.result.current.total - 1);
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
  } finally { hook.unmount(); queryClient.clear(); }
});

it("reports live per-hour states without loading fields and keeps disk, model, run and product identities separate", async () => {
  const { queryClient, fields, winds, saved, wrapper } = setup();
  saved.mockResolvedValue([3]);
  const hook = renderHook(useFrameDownload, { wrapper });
  const state = (hour: number) => hook.result.current.hourStates.find((item) => item.hour === hour)?.state;
  const run = "2026-10-04T12:00:00Z";
  try {
    await waitFor(() => { expect(state(0)).toBe("missing"); expect(state(3)).toBe("cached"); });
    expect(fields).not.toHaveBeenCalled();
    expect(winds).not.toHaveBeenCalled();
    act(() => {
      queryClient.setQueryData(mapKeys.field("hgt-wind-500", run, 0, "hrrr"), frame);
      queryClient.setQueryData(mapKeys.contours("hgt-wind-500", run, 0, "rap"), parseContourLines(contours));
      queryClient.setQueryData(mapKeys.contours("hgt-wind-500", "another-run", 0, "hrrr"), parseContourLines(contours));
      queryClient.setQueryData(mapKeys.contours("refc", run, 0, "hrrr"), parseContourLines(contours));
    });
    expect(state(0)).toBe("missing");
    act(() => { queryClient.setQueryData(mapKeys.contours("hgt-wind-500", run, 0, "hrrr"), parseContourLines(contours)); });
    expect(state(0)).toBe("cached");
    expect(hook.result.current.ready).toBe(1);
    let finish!: (value: typeof frame) => void;
    let pending!: Promise<unknown>;
    act(() => { pending = queryClient.query({ queryKey: mapKeys.field("hgt-wind-500", run, 1, "hrrr"), queryFn: () => new Promise<typeof frame>((resolve) => { finish = resolve; }) }); });
    expect(state(1)).toBe("loading");
    await act(async () => { finish(frame); await pending; });
    await act(async () => {
      await queryClient.query({ queryKey: mapKeys.contours("hgt-wind-500", run, 1, "hrrr"), queryFn: () => Promise.reject(new Error("wind failed")) }).catch(() => undefined);
    });
    expect(state(1)).toBe("failed");
    await act(async () => {
      await queryClient.query({ queryKey: mapKeys.field("hgt-wind-500", run, 2, "hrrr"), queryFn: () => Promise.reject(new BackendFailure({
        code: "FIELD_UNAVAILABLE", message: "Hour not published", retryable: false,
      })) }).catch(() => undefined);
    });
    expect(state(2)).toBe("missing");
    act(() => { queryClient.removeQueries({ queryKey: mapKeys.field("hgt-wind-500", run, 0, "hrrr"), exact: true }); });
    expect(state(0)).toBe("missing");
    saved.mockRejectedValue(new Error("cache unavailable"));
    act(() => { hook.result.current.refresh(); });
    await waitFor(() => { expect(hook.result.current.cacheError).toBe(true); });
    expect(state(4)).toBe("unknown");
  } finally { hook.unmount(); queryClient.clear(); }
});

it("retries only the failed hour's wind data, deduplicates clicks, and clears the failed download", async () => {
  const { queryClient, fields, winds, wrapper } = setup();
  winds.mockRejectedValueOnce(new Error("unavailable"));
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.running).toBe(false); });
    const hour = hook.result.current.failedHours[0];
    expect(hour).toBeDefined();
    if (hour === undefined) throw new Error("Expected a failed hour");
    fields.mockClear(); winds.mockClear();
    let finish!: () => void;
    winds.mockImplementationOnce(() => new Promise((resolve) => { finish = () => { resolve(parseContourLines(contours)); }; }));
    let retry!: Promise<void>;
    act(() => { retry = hook.result.current.retryHour(hour); void hook.result.current.retryHour(hour); });
    await waitFor(() => { expect(hook.result.current.hourStates.find((item) => item.hour === hour)?.state).toBe("loading"); });
    expect(fields).not.toHaveBeenCalled();
    expect(winds).toHaveBeenCalledExactlyOnceWith("hgt-wind-500", "2026-10-04T12:00:00Z", hour, "hrrr", expect.any(AbortSignal));
    await act(async () => { finish(); await retry; });
    expect(hook.result.current.hourStates.find((item) => item.hour === hour)?.state).toBe("cached");
    expect(hook.result.current.failedHours).toEqual([]);
    expect(useForecastStore.getState()).toMatchObject({ forecastHour: 1, playing: false });
    await act(async () => { await hook.result.current.retryHour(-1); });
    expect(winds).toHaveBeenCalledTimes(1);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("retries a failed field with retained data and cancels an individual retry when the run changes", async () => {
  const { queryClient, fields, winds, wrapper } = setup();
  const run = "2026-10-04T12:00:00Z";
  const key = mapKeys.field("hgt-wind-500", run, 2, "hrrr");
  queryClient.setQueryData(key, frame);
  await queryClient.query({ queryKey: key, staleTime: 0, queryFn: () => Promise.reject(new Error("refresh failed")) }).catch(() => undefined);
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    await waitFor(() => { expect(hook.result.current.hourStates.find((item) => item.hour === 2)?.state).toBe("failed"); });
    let finish!: () => void;
    fields.mockImplementationOnce(() => new Promise((resolve) => { finish = () => { resolve(frame); }; }));
    let retry!: Promise<void>;
    act(() => { retry = hook.result.current.retryHour(2); });
    await waitFor(() => { expect(fields).toHaveBeenCalledTimes(1); });
    expect(fields.mock.calls[0]?.[4]?.aborted).toBe(false);
    act(() => { useForecastStore.getState().setRun("2026-10-04T16:00:00Z"); });
    await act(async () => { await retry; });
    expect(fields.mock.calls[0]?.[4]?.aborted).toBe(true);
    expect(hook.result.current.hourStates.find((item) => item.hour === 2)?.state).not.toBe("failed");
    finish();
    expect(winds.mock.calls.every((call) => call[1] === run)).toBe(true);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("restores a missing disk frame even while its successful query remains in memory", async () => {
  const { queryClient, fields, storedFields, wrapper } = setup();
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
    storedFields.delete(0);
    act(() => { hook.result.current.refresh(); });
    await waitFor(() => { expect(hook.result.current.cached).toBe(hook.result.current.total - 1); });
    expect(hook.result.current.ready).toBe(hook.result.current.total);
    fields.mockClear();
    act(() => { hook.result.current.toggle(); });
    await waitFor(() => { expect(hook.result.current.complete).toBe(true); });
    expect(fields).toHaveBeenCalledTimes(1);
    expect(storedFields.has(0)).toBe(true);
  } finally { hook.unmount(); queryClient.clear(); }
});

it.each(["stop", "change model"])("bounds the queue to two hours and cancels queued work on %s", async (action) => {
  const { queryClient, fields, wrapper } = setup();
  const pending: (() => void)[] = [];
  fields.mockImplementation(() => new Promise((resolve) => { pending.push(() => { resolve(frame); }); }));
  const hook = renderHook(useFrameDownload, { wrapper });
  try {
    act(() => { hook.result.current.toggle(); });
    expect(pending).toHaveLength(2);
    if (action === "stop") act(() => { hook.result.current.toggle(); });
    else act(() => { useForecastStore.getState().setModel("rap"); });
    await act(async () => { pending.forEach((resolve) => { resolve(); }); await Promise.resolve(); });
    expect(fields).toHaveBeenCalledTimes(2);
    expect(hook.result.current.running).toBe(false);
    expect(hook.result.current.complete).toBe(false);
    act(() => { useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 1 }); });
    expect(hook.result.current.running).toBe(false);
  } finally { hook.unmount(); queryClient.clear(); }
});
