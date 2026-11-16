// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import { afterEach, expect, it, vi } from "vitest";
import { parseMapCatalog } from "../../lib/contracts/map";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { composeForecast } from "./forecastComposition";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("composes the captured map with the parameter, color range, time and credits", () => {
  const product = parseMapCatalog(catalog).products.find((item) => item.key === "tmp-2m");
  if (product === undefined) throw new Error("Forecast fixture missing");
  const context = { fillRect: vi.fn(), drawImage: vi.fn(), fillText: vi.fn(), strokeText: vi.fn(),
    strokeRect: vi.fn(), putImageData: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class { constructor(readonly pixels: Uint8ClampedArray, readonly width: number, readonly height: number) {} });
  const canvas = document.createElement("canvas"), map = document.createElement("canvas");
  map.width = 3840; map.height = 2160;
  composeForecast(canvas, { map, frame: { forecastHour: 2, validTime: "2026-10-04T14:00:00Z",
    attribution: "NOAA", basemapAttribution: "OpenStreetMap" } }, { width: 3840, height: 2160,
    model: "HRRR", run: "2026-10-04T12:00:00Z", product, units: DEFAULT_UNITS });
  expect([canvas.width, canvas.height]).toEqual([3840, 2160]);
  expect(context.drawImage.mock.calls[0]?.[0]).toBe(map);
  expect(context.drawImage).toHaveBeenCalledTimes(2); // map and color ramp
  const labels = context.fillText.mock.calls.map(([label]) => String(label));
  expect(labels).toContain(`HRRR · ${product.label}`);
  expect(labels.some((label) => label.includes("F002") && label.includes("10-04 14Z"))).toBe(true);
  expect(labels).toContain("NOAA · OpenStreetMap");
});

it("keeps fractional and small scientific values distinct on the exported scale", () => {
  const source = parseMapCatalog(catalog).products[0];
  if (source === undefined) throw new Error("Forecast fixture missing");
  const context = { fillRect: vi.fn(), drawImage: vi.fn(), fillText: vi.fn(), strokeText: vi.fn(), strokeRect: vi.fn(), putImageData: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class { constructor(readonly pixels: Uint8ClampedArray, readonly width: number, readonly height: number) {} });
  const map = document.createElement("canvas"); map.width = 1280; map.height = 720;
  for (const [low, high] of [[0, 0.4], [-0.0004, 0.0004], [100, 100.4]]) {
    context.strokeText.mockClear();
    const product = { ...source, palette: { ...source.palette, units: "", display: "fixed" as const, stops: [[low, "#000000"], [high, "#ffffff"]] as [number, string][] } };
    composeForecast(document.createElement("canvas"), { map, frame: { forecastHour: 0, validTime: "2026-11-16T00:00:00Z", attribution: "test" } },
      { width: 1280, height: 720, model: "Test", run: "2026-11-16T00:00:00Z", product, units: DEFAULT_UNITS });
    const ticks = context.strokeText.mock.calls.slice(1).map(([label]) => String(label));
    expect(new Set(ticks).size).toBe(5);
    expect(ticks[0]).toBe(String(high)); expect(ticks.at(-1)).toBe(String(low));
  }
});
