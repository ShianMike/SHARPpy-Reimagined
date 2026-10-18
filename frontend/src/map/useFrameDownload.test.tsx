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
  queryClient.setQueryData(mapKeys.catalog("hrrr"), parseMapCatalog(catalog));
  const client = new BackendClient({ call: () => new Promise(() => undefined), listen: () => Promise.resolve(() => undefined) });
  const storedFields = new Set<number>(), storedWinds = new Set<number>();
  const fields = vi.spyOn(client, "getWeatherField").mockImplementation((_product, _run, hour) => { storedFields.add(hour); return Promise.resolve(frame); });
  const winds = vi.spyOn(client, "getFieldContours").mockImplementation((_product, _run, hour) => { storedWinds.add(hour); return Promise.resolve(parseContourLines(contours)); });
  const saved = vi.spyOn(client, "getCachedForecastHours").mockImplementation((_model, _products, _run, hours) => Promise.resolve(hours.filter((hour) => storedFields.has(hour) && storedWinds.has(hour))));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  return { queryClient, fields, winds, saved, storedFields, wrapper };
}

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
  } finally { hook.unmount(); queryClient.clear(); }
});
