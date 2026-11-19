// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import type { SoundingMapSnapshot } from "../../map/runtime/soundingLocator";
import type { SoundingView } from "../../types/soundingView";
import { composeSoundingLocator } from "./soundingLocatorImage";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("draws the map with an outside frame, marker and place name", async () => {
  const sounding = { width: 2000, height: 1400, close: vi.fn() };
  const map = { width: 500, height: 252, close: vi.fn() };
  const bitmap = vi.fn().mockResolvedValueOnce(sounding).mockResolvedValueOnce(map);
  vi.stubGlobal("createImageBitmap", bitmap);
  const context = { drawImage: vi.fn(), fillText: vi.fn<CanvasRenderingContext2D["fillText"]>(), strokeText: vi.fn(),
    beginPath: vi.fn(), arc: vi.fn(), stroke: vi.fn(), fill: vi.fn(), strokeRect: vi.fn(),
    getImageData: vi.fn(() => ({ data: [0, 0, 0, 255] })) };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => { callback(new Blob(["composed"], { type: "image/png" })); });
  const bounds = { left: 0.55, top: 0.01, right: 0.8, bottom: 0.19 };
  const view = { image: new Blob(["sounding"]), width: 2000, height: 1400, locatorBounds: bounds } as SoundingView;
  const snapshot = { image: new Blob(["map"]), point: { x: 0.4, y: 0.6 }, model: "HRRR",
    product: { key: "tmp", label: "Temperature", shortLabel: "Temp", category: "Surface", description: "", caveat: "", minimumForecastHour: 0,
      contour: null, wind: false, palette: { units: "°C", display: "auto", stepped: false, floor: null, ceiling: null, ticks: [], stops: [[-20, "#0000ff"], [20, "#ff0000"]] } },
    frame: { runTime: "2026-11-15T00:00:00Z", forecastHour: 2, validTime: "2026-11-15T02:00:00Z", attribution: "NOAA" },
    basemapAttribution: "OpenFreeMap · OpenMapTiles · OpenStreetMap" } satisfies SoundingMapSnapshot;
  const result = await composeSoundingLocator(view, snapshot, "Nowata, Oklahoma");
  expect(result.image.type).toBe("image/png");
  expect(context.drawImage).toHaveBeenCalledTimes(2);
  expect(context.drawImage).toHaveBeenCalledWith(sounding, 0, 0);
  expect(context.drawImage).toHaveBeenCalledWith(map, bounds.left * view.width, bounds.top * view.height,
    (bounds.right - bounds.left) * view.width, (bounds.bottom - bounds.top) * view.height);
  const [markerX, markerY] = context.arc.mock.calls[0] as number[];
  expect(markerX).toBeCloseTo(1300);
  expect(markerY).toBeCloseTo(165.2);
  expect(context.strokeRect).toHaveBeenCalledExactlyOnceWith(1098, 12, 504, 256);
  expect(context.strokeText).not.toHaveBeenCalled();
  expect(context.fillText.mock.calls).toHaveLength(1);
  const titleCall = context.fillText.mock.calls[0];
  if (titleCall === undefined) throw new Error("The locator header was not drawn.");
  const [label, labelX, labelY, labelWidth] = titleCall;
  expect(label).toBe("Nowata, Oklahoma");
  expect(labelX).toBeCloseTo(1350);
  expect(labelY).toBeGreaterThan(0);
  expect(labelY).toBeLessThan(bounds.top * view.height);
  expect(labelWidth).toBeLessThan((bounds.right - bounds.left) * view.width);
  expect(sounding.close).toHaveBeenCalledOnce();
  expect(map.close).toHaveBeenCalledOnce();
  context.fillText.mockClear();
  bitmap.mockResolvedValueOnce(sounding).mockResolvedValueOnce(map);
  const bare = await composeSoundingLocator(view, { ...snapshot, model: null, product: null, frame: null }, "Nowata, Oklahoma");
  expect(bare.image.type).toBe("image/png");
  expect(context.fillText.mock.calls.map(([label]) => label)).toEqual(["Nowata, Oklahoma"]);
});
