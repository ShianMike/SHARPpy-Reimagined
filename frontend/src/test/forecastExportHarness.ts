// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { afterEach, beforeEach, vi } from "vitest";
import { BackendClient } from "../lib/backend/client";
import type { Transport } from "../lib/backend/transport";
import { createQueryClient } from "../lib/backend/queryOptions";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import { DEFAULT_UNITS } from "../lib/displayUnits";
import { mapCaptureSources } from "../map/runtime/mapCapture";
import { useMapStore } from "../stores/mapStore";
import { useForecastStore } from "../stores/forecastStore";
import type { FieldFrame } from "../types/map";
import { resolveForecast } from "../features/forecast/useForecast";
import type { ForecastExportOptions } from "../features/forecastExport/exportForecast";

export const state = { events: [] as string[], writes: [] as number[], samples: [] as Uint8Array[], closes: 0, startFailure: null as Error | null };
vi.mock("../features/forecastExport/GifWriter", () => ({ GifWriter: class {
  constructor() { state.events.push("encoder:start"); if (state.startFailure !== null) throw state.startFailure; }
  write(frame: { delay: number; paletteSample?: Uint8Array }) {
    state.events.push("encode"); state.writes.push(frame.delay);
    if (frame.paletteSample !== undefined) state.samples.push(frame.paletteSample);
    return Promise.resolve();
  }
  finish() { return new Blob(["GIF89a"], { type: "image/gif" }); }
  close() { state.closes++; }
} }));
vi.mock("../features/forecastExport/forecastComposition", () => ({ composeForecast: vi.fn() }));
vi.mock("../map/fieldImages", () => ({ prepareFieldImage: () => Promise.resolve() }));
export const map = useMapStore.getInitialState(), forecast = useForecastStore.getInitialState();
beforeEach(() => {
  state.events.length = 0; state.writes.length = 0; state.samples.length = 0; state.closes = 0;
  state.startFailure = null;
  useMapStore.setState(map, true); useForecastStore.setState({ ...forecast, forecastHour: 5, playing: true }, true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ getImageData: () => ({ data: new Uint8ClampedArray(4) }), drawImage: () => undefined } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => { state.events.push("png"); callback(new Blob(["png"], { type: "image/png" })); });
  vi.stubGlobal("createImageBitmap", () => Promise.resolve({ width: 1, height: 1, close: () => undefined }));
});
afterEach(() => { mapCaptureSources.clear(); useMapStore.setState(map, true); useForecastStore.setState(forecast, true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

export function setup(transport: Transport) {
  const inventory = parseMapCatalog(catalog), product = inventory.products.find((product) => product.key === "tmp-2m");
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 5 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const client = new BackendClient(transport), queryClient = createQueryClient();
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
