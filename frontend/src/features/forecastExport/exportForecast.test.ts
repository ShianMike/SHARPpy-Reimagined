// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { map, state, setup as setupExport } from "../../test/forecastExportHarness";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { trimFieldCache } from "../../map/mapQueries";
import { mapKeys } from "../../lib/backend/queryOptions";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { useForecastStore } from "../../stores/forecastStore";
import { exportForecast, forecastExportFilename } from "./exportForecast";

const setup = () => setupExport(createTransport());

it("loads all selected hours before capture and saves every PNG before the first GIF frame", async () => {
  const { client, queryClient, options, field, captureFrame } = setup();
  let release!: () => void;
  const original = field.getMockImplementation();
  field.mockImplementation((...args) => args[2] === 3 ? new Promise((resolve) => {
    release = () => { if (original !== undefined) void original(...args).then(resolve); };
  }) : original?.(...args) ?? Promise.reject(new Error("Missing fixture")));
  const job = exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined);
  await vi.waitFor(() => { expect(field).toHaveBeenCalledTimes(3); });
  expect(captureFrame).not.toHaveBeenCalled(); expect(state.writes).toEqual([]);
  release(); await job;
  expect(state.events).toEqual(["load:1", "load:2", "load:3", "capture:1", "png", "capture:2", "png", "capture:3", "png", "encoder:start", "encode", "encode", "encode"]);
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, playing: false, exporting: false });
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].smoothing).toBe(map.layers["model-field"].smoothing);
  queryClient.clear();
});

it("fails before capture when any selected hour is unavailable and restores the map", async () => {
  const { client, queryClient, options, field, captureFrame } = setup();
  field.mockRejectedValueOnce(new Error("Hour unavailable"));
  await expect(exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined)).rejects.toThrow("Hour unavailable");
  expect(captureFrame).not.toHaveBeenCalled(); expect(state.writes).toEqual([]);
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, exporting: false });
  expect(state.events).not.toContain("encoder:start"); expect(state.closes).toBe(0); queryClient.clear();
});

it("protects a prepared field until capture takes ownership", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  captureFrame.mockImplementation((request) => {
    trimFieldCache(queryClient, 0);
    expect(queryClient.getQueryData(mapKeys.field(options.product.key, options.run, request.forecastHour))).toBeDefined();
    return Promise.resolve(document.createElement("canvas"));
  });
  await exportForecast({ ...options, format: "png", hours: [2] }, client, queryClient,
    new AbortController().signal, document.createElement("canvas"), () => undefined);
  trimFieldCache(queryClient, 0);
  expect(queryClient.getQueryData(mapKeys.field(options.product.key, options.run, 2))).toBeUndefined();
  queryClient.clear();
});

it("discards a capture if its layer settings change during rendering", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  captureFrame.mockImplementation(() => {
    useMapStore.getState().setLayerVisible("storm-reports", true);
    return Promise.resolve(document.createElement("canvas"));
  });
  await expect(exportForecast({ ...options, format: "png", hours: [2] }, client, queryClient,
    new AbortController().signal, document.createElement("canvas"), () => undefined)).rejects.toThrow("changed during export");
  expect(state.events).not.toContain("png");
  expect(panelLayers(useMapStore.getState(), 0)["storm-reports"].visible).toBe(true);
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, exporting: false });
  queryClient.clear();
});

it("restores export controls and map settings when the GIF worker cannot start", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  state.startFailure = new Error("Worker blocked");
  await expect(exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined)).rejects.toThrow("Worker blocked");
  expect(captureFrame).toHaveBeenCalledTimes(3);
  expect(state.writes).toEqual([]);
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, playing: false, exporting: false });
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].smoothing).toBe(map.layers["model-field"].smoothing);
  queryClient.clear();
});

it("stops the other preload owner immediately when a selected hour fails", async () => {
  const { client, queryClient, options, field, captureFrame } = setup();
  field.mockImplementation((_product, _run, hour) => hour === 1 ? Promise.reject(new Error("Hour unavailable")) : new Promise(() => undefined));
  let failure: unknown;
  void exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined)
    .catch((error: unknown) => { failure = error; });
  await vi.waitFor(() => { expect(failure).toBeInstanceOf(Error); }, { timeout: 500 });
  expect((failure as Error).message).toBe("Hour unavailable");
  expect(captureFrame).not.toHaveBeenCalled();
  expect(useForecastStore.getState().exporting).toBe(false);
  queryClient.clear();
});

it("cancels during preload without capturing or publishing a partial GIF", async () => {
  const { client, queryClient, options, field, captureFrame } = setup();
  field.mockImplementation(() => new Promise(() => undefined));
  const controller = new AbortController();
  const job = exportForecast(options, client, queryClient, controller.signal, document.createElement("canvas"), () => undefined);
  const failed = expect(job).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => { expect(field).toHaveBeenCalledTimes(2); });
  controller.abort(); await failed;
  expect(captureFrame).not.toHaveBeenCalled(); expect(state.writes).toEqual([]);
  expect(useForecastStore.getState().exporting).toBe(false); queryClient.clear();
  expect(state.events).not.toContain("encoder:start");
});

it("restores the exported panel without changing another panel when the layout shrinks during cancellation", async () => {
  const { client, queryClient, options } = setup();
  const store = useMapStore.getState();
  store.setPanelLayout(2);
  store.setFieldSmoothing(true);
  store.setActivePanel(1);
  store.setFieldSmoothing(false);
  options.panel = 1;
  const captureFrame = vi.fn((_request: unknown, signal: AbortSignal) => new Promise<HTMLCanvasElement>((_resolve, reject) => {
    signal.addEventListener("abort", () => { reject(new DOMException("Cancelled", "AbortError")); }, { once: true });
  }));
  mapCaptureSources.set(1, { captureFrame });
  const controller = new AbortController();
  const job = exportForecast(options, client, queryClient, controller.signal, document.createElement("canvas"), () => undefined);
  const rejected = expect(job).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => { expect(captureFrame).toHaveBeenCalledOnce(); });
  expect(panelLayers(useMapStore.getState(), 1)["model-field"].smoothing).toBe(true);
  store.setPanelLayout(1);
  controller.abort();
  await rejected;
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].smoothing).toBe(true);
  expect(panelLayers(useMapStore.getState(), 1)["model-field"].smoothing).toBe(false);
  queryClient.clear();
});

it("restores the original map view when cancellation follows a workspace change", async () => {
  const { client, queryClient, options } = setup();
  const captureFrame = vi.fn((_request: unknown, signal: AbortSignal) => new Promise<HTMLCanvasElement>((_resolve, reject) => {
    signal.addEventListener("abort", () => { reject(new DOMException("Cancelled", "AbortError")); }, { once: true });
  }));
  mapCaptureSources.set(0, { captureFrame });
  const controller = new AbortController();
  const job = exportForecast(options, client, queryClient, controller.signal, document.createElement("canvas"), () => undefined);
  const rejected = expect(job).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => { expect(captureFrame).toHaveBeenCalledOnce(); });
  useMapStore.setState({ view: "live" });
  useMapStore.getState().setFieldSmoothing(true);
  controller.abort();
  await rejected;
  const state = useMapStore.getState();
  expect(state.layers["model-field"].smoothing).toBe(map.layers["model-field"].smoothing);
  expect(state.livePanels[0]?.layers["model-field"].smoothing).toBe(true);
  queryClient.clear();
});

it("exports only real-hour frames with smooth pixels and the requested duration", async () => {
  const { client, queryClient, options } = setup();
  await exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined);
  expect(state.writes).toHaveLength(3);
  expect(state.writes.reduce((sum, delay) => sum + delay, 0)).toBe(2250);
  expect(forecastExportFilename(options)).toBe("sharppy-hrrr-tmp-2m-20261004T120000Z-f001-f003.gif");
  queryClient.clear();
});

it("exports the composed current-hour image as PNG at the requested resolution", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  const preview = document.createElement("canvas");
  const save = vi.spyOn(preview, "toBlob");
  options.hours = [5]; options.width = 3840; options.height = 2160; options.format = "png";
  const blob = await exportForecast(options, client, queryClient, new AbortController().signal, preview, () => undefined);
  expect(captureFrame).toHaveBeenCalledOnce();
  expect(captureFrame).toHaveBeenCalledWith(expect.objectContaining({ forecastHour: 5 }), expect.any(AbortSignal), { width: 3840, height: 2160 });
  expect(save).toHaveBeenCalledOnce();
  expect(blob.type).toBe("image/png");
  expect(state.writes).toEqual([]);
  expect(forecastExportFilename(options)).toBe("sharppy-hrrr-tmp-2m-20261004T120000Z-f005.png");
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, exporting: false });
  queryClient.clear();
});

it("discards a PNG and restores the map if cancelled while the image is being saved", async () => {
  const { client, queryClient, options } = setup();
  options.hours = [5]; options.format = "png";
  let complete!: (blob: Blob) => void;
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => { complete = callback; });
  const controller = new AbortController();
  const job = exportForecast(options, client, queryClient, controller.signal, document.createElement("canvas"), () => undefined);
  const rejected = expect(job).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => { expect(complete).toBeTypeOf("function"); });
  controller.abort();
  complete(new Blob(["png"], { type: "image/png" }));
  await rejected;
  expect(useForecastStore.getState()).toMatchObject({ forecastHour: 5, exporting: false });
  expect(panelLayers(useMapStore.getState(), 0)["model-field"].smoothing).toBe(map.layers["model-field"].smoothing);
  queryClient.clear();
});

it("builds one bounded palette sample from every captured hour and the full ramp", async () => {
  const { client, queryClient, options } = setup();
  let color = 10;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({
    getImageData: () => { color += 10; return { data: new Uint8ClampedArray([color, 0, 0, 255]) }; },
    drawImage: () => { /* Palette sampling advances only when reading pixels. */ },
  }) as unknown as CanvasRenderingContext2D);
  await exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined);
  const sample = state.samples[0];
  expect(state.samples).toHaveLength(1);
  expect(sample?.[0]).toBe(20);
  expect(sample?.[4096 * 4]).toBe(30);
  expect(sample?.[8192 * 4]).toBe(40);
  expect(sample?.length).toBeLessThanOrEqual((131_072 + 32_768) * 4);
  expect(sample?.subarray(3 * 4096 * 4).some((value) => value !== 0)).toBe(true);
  queryClient.clear();
});

it.each([2, 4] as const)("captures every panel of a %i-panel layout in the same PNG", async (count) => {
  const { client, queryClient, options } = setup();
  useMapStore.getState().setPanelLayout(count);
  const parts = Array.from({ length: count }, (_, panel) => ({ panel, product: options.product }));
  const captures = parts.map(({ panel }) => {
    const captureFrame = vi.fn(() => Promise.resolve(document.createElement("canvas")));
    mapCaptureSources.set(panel, { captureFrame }); return captureFrame;
  });
  const result = await exportForecast({ ...options, panels: parts, format: "png", hours: [2] },
    client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined);
  expect(result.type).toBe("image/png");
  for (const capture of captures) {
    expect(capture).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ forecastHour: 2 }),
      expect.any(AbortSignal), { width: options.width / 2, height: options.height / (count === 4 ? 2 : 1) });
  }
  expect(state.events).not.toContain("encoder:start");
  expect(useForecastStore.getState().forecastHour).toBe(5);
  queryClient.clear();
});

it("fails a whole-layout export before capture when any selected panel is missing", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  await expect(exportForecast({ ...options, panels: [{ panel: 0, product: options.product }, { panel: 1, product: options.product }] },
    client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined)).rejects.toThrow("selected map is unavailable");
  expect(captureFrame).not.toHaveBeenCalled();
  expect(useForecastStore.getState().exporting).toBe(false);
  queryClient.clear();
});
