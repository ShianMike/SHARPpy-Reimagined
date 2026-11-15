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

const state = vi.hoisted(() => ({ events: [] as string[], writes: [] as number[], closes: 0 }));
vi.mock("./GifWriter", () => ({ GifWriter: class {
  write(frame: { delay: number }) { state.events.push("encode"); state.writes.push(frame.delay); return Promise.resolve(); }
  finish() { return new Blob(["GIF89a"], { type: "image/gif" }); }
  close() { state.closes++; }
} }));
vi.mock("./forecastComposition", () => ({ composeForecast: () => undefined }));
vi.mock("../../map/fieldImages", () => ({ prepareFieldImage: () => Promise.resolve() }));
const map = useMapStore.getInitialState(), forecast = useForecastStore.getInitialState();
beforeEach(() => {
  state.events.length = 0; state.writes.length = 0; state.closes = 0;
  useMapStore.setState(map, true); useForecastStore.setState({ ...forecast, forecastHour: 5, playing: true }, true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ getImageData: () => ({ data: new Uint8ClampedArray(4) }), drawImage: () => undefined } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => { state.events.push("png"); callback(new Blob(["png"])); });
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
  expect(state.events).toEqual(["load:1", "load:2", "load:3", "capture:1", "png", "capture:2", "png", "capture:3", "png", "encode", "encode", "encode"]);
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
  expect(state.closes).toBe(1); queryClient.clear();
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
