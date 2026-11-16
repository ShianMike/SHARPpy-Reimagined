// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { createQueryClient } from "../../lib/backend/queryOptions";
import { parseMapCatalog } from "../../lib/contracts/map";
import { parseModelCatalog } from "../../lib/contracts/soundings";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { useForecastStore } from "../../stores/forecastStore";
import type { FieldFrame } from "../../types/map";
import { resolveForecast } from "../forecast/useForecast";
import { exportForecast, forecastExportFilename, type ForecastExportOptions } from "./exportForecast";

const state = vi.hoisted(() => ({ events: [] as string[], writes: [] as number[], samples: [] as Uint8Array[], closes: 0, startFailure: null as Error | null }));
vi.mock("./GifWriter", () => ({ GifWriter: class {
  constructor() { state.events.push("encoder:start"); if (state.startFailure !== null) throw state.startFailure; }
  write(frame: { delay: number; paletteSample?: Uint8Array }) {
    state.events.push("encode"); state.writes.push(frame.delay);
    if (frame.paletteSample !== undefined) state.samples.push(frame.paletteSample);
    return Promise.resolve();
  }
  finish() { return new Blob(["GIF89a"], { type: "image/gif" }); }
  close() { state.closes++; }
} }));
vi.mock("./forecastComposition", () => ({ composeForecast: () => undefined }));
vi.mock("../../map/fieldImages", () => ({ prepareFieldImage: () => Promise.resolve() }));
const map = useMapStore.getInitialState(), forecast = useForecastStore.getInitialState();
beforeEach(() => {
  state.events.length = 0; state.writes.length = 0; state.samples.length = 0; state.closes = 0;
  state.startFailure = null;
  useMapStore.setState(map, true); useForecastStore.setState({ ...forecast, forecastHour: 5, playing: true }, true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ getImageData: () => ({ data: new Uint8ClampedArray(4) }), drawImage: () => undefined } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => { state.events.push("png"); callback(new Blob(["png"], { type: "image/png" })); });
  vi.stubGlobal("createImageBitmap", () => Promise.resolve({ width: 1, height: 1, close: () => undefined }));
});
afterEach(() => { mapCaptureSources.clear(); useMapStore.setState(map, true); useForecastStore.setState(forecast, true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup() {
  const inventory = parseMapCatalog(catalog), product = inventory.products.find((product) => product.key === "tmp-2m");
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 5 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const client = new BackendClient(createTransport()), queryClient = createQueryClient();
  vi.spyOn(client, "getMapCatalog").mockResolvedValue(inventory);
  const field = vi.spyOn(client, "getWeatherField").mockImplementation((_product, runTime, forecastHour) => {
    state.events.push(`load:${String(forecastHour)}`);
    return Promise.resolve({ product: product.key, runTime, forecastHour, validTime: runTime, attribution: "fixture",
      width: 1, height: 1, cells: new Uint16Array([1]), bounds: inventory.fieldBounds, units: product.palette.units,
      scale: 1, offset: 0, nodata: 65535, valueMin: 1, valueMax: 1, sourceUrl: "" } satisfies FieldFrame);
  });
  const captureFrame = vi.fn((request: { forecastHour: number }) => {
    state.events.push(`capture:${String(request.forecastHour)}`); return Promise.resolve(document.createElement("canvas"));
  });
  mapCaptureSources.set(0, { captureFrame });
  const options: ForecastExportOptions = { context, product, model: context.model.label, run: context.run, panel: 0,
    hours: [1, 2, 3], width: 960, height: 540, delay: 750, units: DEFAULT_UNITS };
  return { client, queryClient, field, captureFrame, options };
}

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
    getImageData: () => ({ data: new Uint8ClampedArray([color, 0, 0, 255]) }),
    drawImage: () => { color += 10; },
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
