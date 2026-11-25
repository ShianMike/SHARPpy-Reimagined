// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import modelCatalogs from "@contracts/public/map/catalog.models.json";
import { afterEach, expect, it, vi } from "vitest";
import { version } from "../../../package.json";
import { parseMapCatalog } from "../../lib/contracts/map";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { PRECIPITATION_ICON_PATHS } from "../../map/field/precipitationIcons";
import { composeForecast } from "./forecastComposition";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([
  { source: catalog, key: "tmp-2m", model: "HRRR" },
  { source: modelCatalogs.refs, key: "qpf-3h", model: "REFS Mean" },
])("composes $key with its unit, color range, time, app version and credits", ({ source, key, model }) => {
  const product = parseMapCatalog(source).products.find((item) => item.key === key);
  if (product === undefined) throw new Error("Forecast fixture missing");
  const context = { fillRect: vi.fn(), drawImage: vi.fn(), fillText: vi.fn(), strokeText: vi.fn(),
    strokeRect: vi.fn(), putImageData: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("ImageData", class { constructor(readonly pixels: Uint8ClampedArray, readonly width: number, readonly height: number) {} });
  const canvas = document.createElement("canvas"), map = document.createElement("canvas");
  map.width = 3840; map.height = 2160;
  composeForecast(canvas, { map, frame: { forecastHour: 2, validTime: "2026-10-04T14:00:00Z",
    attribution: "NOAA", basemapAttribution: "OpenStreetMap" } }, { width: 3840, height: 2160,
    model, run: "2026-10-04T12:00:00Z", product, units: DEFAULT_UNITS });
  expect([canvas.width, canvas.height]).toEqual([3840, 2160]);
  expect(context.drawImage.mock.calls[0]?.[0]).toBe(map);
  expect(context.drawImage).toHaveBeenCalledTimes(2); // map and color ramp
  const labels = context.fillText.mock.calls.map(([label]) => String(label));
  expect(labels).toContain(`${model} · ${product.label}`);
  expect(labels).toContain(`SHARPpy Reimagined v${version}`);
  expect(labels[4]).toBe(product.palette.units);
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

it.each([[1920, 1080], [640, 360]])("fits one stacked REFS QPF legend inside a %i by %i export tile", (width, height) => {
  const product = parseMapCatalog(modelCatalogs.refs).products.find((item) => item.key === "qpf-1h-ptype");
  if (product === undefined) throw new Error("REFS QPF fixture missing");
  vi.stubGlobal("Path2D", class { constructor(readonly data: string) {} });
  const theme = document.createElement("div").style;
  const background = "#202124", foreground = "#f4f4f4";
  theme.setProperty("--surface-1", background);
  theme.setProperty("--text-primary", foreground);
  vi.spyOn(window, "getComputedStyle").mockReturnValue(theme);
  const fills: string[] = [];
  const strokes: { colour: string; width: number }[] = [];
  const tickHeights: number[] = [];
  const context = {
    fillStyle: "", strokeStyle: "", lineWidth: 0, font: "",
    fillRect: vi.fn<CanvasRenderingContext2D["fillRect"]>(() => { fills.push(context.fillStyle); }),
    drawImage: vi.fn(), fillText: vi.fn<CanvasRenderingContext2D["fillText"]>(),
    strokeText: vi.fn<CanvasRenderingContext2D["strokeText"]>(() => {
      tickHeights.push(Number.parseFloat(context.font) + context.lineWidth);
    }),
    strokeRect: vi.fn<CanvasRenderingContext2D["strokeRect"]>(),
    stroke: vi.fn<(path: { data: string }) => void>(() => { strokes.push({ colour: context.strokeStyle, width: context.lineWidth }); }),
    save: vi.fn(), restore: vi.fn(), rotate: vi.fn(),
    translate: vi.fn<CanvasRenderingContext2D["translate"]>(), scale: vi.fn<CanvasRenderingContext2D["scale"]>(),
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const map = document.createElement("canvas");
  map.width = width; map.height = height;
  const captured = { map, frame: { forecastHour: 1, validTime: "2026-11-25T01:00:00Z", attribution: "NOAA" } };
  const options = { width, height, model: "REFS Mean", run: "2026-11-25T00:00:00Z", product, units: DEFAULT_UNITS };
  composeForecast(document.createElement("canvas"), captured, options);

  expect(context.drawImage).toHaveBeenCalledTimes(1);
  expect(context.strokeRect).toHaveBeenCalledTimes(4);
  expect(fills.slice(3)).toEqual(product.palette.stops.map(([, colour]) => colour));
  expect(context.rotate).not.toHaveBeenCalled();
  const ramps = context.strokeRect.mock.calls;
  expect(new Set(ramps.map(([left]) => left)).size).toBe(1);
  const ticks = context.fillText.mock.calls.slice(4, -1);
  const labels = ["0.01", "0.05", "0.1", "0.2", "0.5+"];
  expect(ticks).toHaveLength(4 * labels.length);
  expect(context.strokeText.mock.calls).toEqual(ticks);
  expect(context.translate).toHaveBeenCalledTimes(4);
  const paths = context.stroke.mock.calls.map(([path]) => path);
  expect(paths.map(({ data }) => data)).toEqual(PRECIPITATION_ICON_PATHS.flatMap((path) => [path, path]));
  const [, , , headerHeight = 0] = context.fillRect.mock.calls[1] ?? [];
  const [, footerTop = height] = context.fillRect.mock.calls[2] ?? [];
  let previousBottom = headerHeight;
  for (const [index, [left, top, columnWidth, rampHeight]] of ramps.entries()) {
    const [x, y] = context.translate.mock.calls[index] ?? [0, 0];
    const [scaleX, scaleY] = context.scale.mock.calls[index] ?? [0, 0];
    expect(scaleX).toBeGreaterThan(0);
    expect(scaleY).toBe(scaleX);
    expect(x + 12 * scaleX).toBeCloseTo(left + columnWidth / 2);
    const iconGap = top - (y + 24 * scaleY);
    expect(iconGap).toBeGreaterThan(0);
    expect(iconGap).toBeLessThan(12 * scaleY);
    expect(y).toBeGreaterThan(previousBottom);
    previousBottom = top + rampHeight;
    expect(previousBottom).toBeLessThan(footerTop);
    const outline = strokes[index * 2], symbol = strokes[index * 2 + 1];
    expect(outline?.colour).toBe(background);
    expect(symbol?.colour).toBe(foreground);
    expect(outline?.width).toBeGreaterThan(symbol?.width ?? 0);

    const bands = context.fillRect.mock.calls.slice(3 + index * 17, 3 + (index + 1) * 17);
    expect(bands).toHaveLength(17);
    expect(bands.every(([bandLeft, , bandWidth, bandHeight]) => bandLeft === left && bandWidth === columnWidth && bandHeight > 0)).toBe(true);
    const firstTick = index * labels.length;
    const blockTicks = ticks.slice(firstTick, firstTick + labels.length);
    expect(blockTicks.map(([text]) => text)).toEqual(labels);
    for (const [tickIndex, [, tickX, tickY, maxWidth]] of blockTicks.entries()) {
      const [, bandTop = 0, , bandHeight = 0] = bands[tickIndex * 4] ?? [];
      expect(tickY).toBe(bandTop + bandHeight);
      expect(tickX).toBeLessThan(left);
      expect(tickX - (maxWidth ?? 0)).toBeGreaterThan(width * 0.9);
      const nextTick = blockTicks[tickIndex + 1];
      if (nextTick !== undefined) {
        expect(tickY - nextTick[2]).toBeGreaterThan(tickHeights[firstTick + tickIndex] ?? Infinity);
      }
    }
  }

  context.stroke.mockClear();
  composeForecast(document.createElement("canvas"), captured, options);
  expect(context.stroke).toHaveBeenCalledTimes(paths.length);
  for (const [index, [path]] of context.stroke.mock.calls.entries()) expect(path).toBe(paths[index]);
});

it("draws the rate unit and rotated captions on grouped HRDPS precipitation exports", () => {
  const product = parseMapCatalog(modelCatalogs.hrdps).products.find((item) => item.key === "prate-ptype");
  if (product === undefined) throw new Error("HRDPS precipitation rate fixture missing");
  const context = {
    drawImage: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn<CanvasRenderingContext2D["strokeRect"]>(), strokeText: vi.fn(),
    fillText: vi.fn<CanvasRenderingContext2D["fillText"]>(), stroke: vi.fn(),
    measureText: (label: string) => ({ width: label.length * 7 }),
    save: vi.fn(), restore: vi.fn(), translate: vi.fn(), rotate: vi.fn(),
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const map = document.createElement("canvas"); map.width = 1280; map.height = 720;
  composeForecast(document.createElement("canvas"), { map, frame: { forecastHour: 1, validTime: "2026-11-25T01:00:00Z", attribution: "ECCC" } },
    { width: 1280, height: 720, model: "HRDPS", run: "2026-11-25T00:00:00Z", product, units: DEFAULT_UNITS });

  expect(context.fillText.mock.calls.slice(4, -1).map(([text]) => text))
    .toEqual(["in/h", "0.01", "0.05", "0.1", "0.2", "0.5+", "Rain", "Snow", "Freezing rain", "Mix"]);
  expect(context.strokeRect.mock.calls).toEqual([1198, 1214, 1230, 1246].map((left) => [left, 132, 12, 401]));
  expect(context.rotate.mock.calls).toEqual(Array.from({ length: 4 }, () => [Math.PI / 2]));
  expect(context.stroke).not.toHaveBeenCalled();
});
