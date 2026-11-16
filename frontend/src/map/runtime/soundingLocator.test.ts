// @vitest-environment jsdom
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { mapCaptureSources } from "./mapCapture";
import { captureSoundingMap, locatorCrop, mapContextMatchesSounding, registerSoundingLocatorMap, registerSoundingMapContext } from "./soundingLocator";
import type { FieldProduct } from "../../types/map";
import type { SoundingSummary } from "../../types/soundings";
import { useForecastStore } from "../../stores/forecastStore";

const request = { model: "hrrr", product: "temperature", runTime: "2026-11-15T00:00:00Z", forecastHour: 2 };
const context = { panel: 0, request, model: "HRRR", product: { key: "temperature" } as FieldProduct,
  frame: { runTime: request.runTime, forecastHour: 2, validTime: "2026-11-15T02:00:00Z", attribution: "NOAA" } };
const size = { width: 500, height: 400 };
const removed: (() => void)[] = [];
afterEach(() => { removed.splice(0).forEach((remove) => { remove(); }); mapCaptureSources.clear(); useForecastStore.setState({ exporting: false }); vi.restoreAllMocks(); });

function setup(panel = 0) {
  const mapCanvas = document.createElement("canvas"), image = document.createElement("canvas");
  Object.defineProperties(mapCanvas, { clientWidth: { value: 800 }, clientHeight: { value: 400 } });
  image.width = 500; image.height = 400;
  vi.spyOn(image, "toBlob").mockImplementation((callback) => { callback(new Blob(["png"], { type: "image/png" })); });
  const handlers = new Map<string, () => void>();
  const map = {
    getCanvas: () => mapCanvas,
    getCenter: () => ({ lng: -97, lat: 35 }),
    getZoom: vi.fn(() => 5), getBearing: () => 0, getPitch: () => 0,
    getProjection: () => ({ type: "mercator" }), isMoving: () => false,
    project: vi.fn(() => ({ x: 200, y: 100 })),
    unproject: vi.fn(() => ({ lng: -97, lat: 35 })),
    on: (name: string, callback: () => void) => { handlers.set(name, callback); },
  };
  const captureFrame = vi.fn(() => Promise.resolve(image));
  registerSoundingLocatorMap(panel, map as unknown as MapLibreMap);
  const unregisterContext = registerSoundingMapContext(context);
  removed.push(unregisterContext);
  removed.push(() => { handlers.get("remove")?.(); });
  mapCaptureSources.set(panel, { captureFrame, attribution: () => "OpenFreeMap · OpenMapTiles · OpenStreetMap" });
  return { map, image, captureFrame, remove: () => { handlers.get("remove")?.(); } };
}

it("keeps a point at the edge in a true aspect ratio crop", () => {
  const { crop, point } = locatorCrop(1600, 900, 1.5, { x: 0.98, y: 0.01 });
  expect(crop.width * 1600).toBeCloseTo(210.9375);
  expect(crop.width * 1600 / (crop.height * 900)).toBeCloseTo(1.5);
  expect(crop.left + crop.width).toBeCloseTo(1);
  expect(crop.top).toBe(0);
  expect(crop.left + point.x * crop.width).toBeCloseTo(0.98);
  expect(crop.top + point.y * crop.height).toBeCloseTo(0.01);
});

it("captures the projected location at the chosen inset resolution with basemap credits", async () => {
  const { image, captureFrame } = setup();
  const signal = new AbortController().signal;
  const snapshot = await captureSoundingMap(context, 35, -97, size, signal);
  expect(captureFrame).toHaveBeenCalledWith(request, expect.any(AbortSignal), { ...size,
    crop: { left: 0.201171875, top: 0.171875, width: 0.09765625, height: 0.15625 } });
  expect(snapshot?.image.type).toBe("image/png");
  expect(snapshot?.point).toEqual({ x: 0.5, y: 0.5 });
  expect(snapshot?.basemapAttribution).toContain("OpenStreetMap");
  expect([image.width, image.height]).toEqual([0, 0]);
});

it("does not capture a point outside the view or on the back of the globe", async () => {
  const { map, captureFrame } = setup();
  map.project.mockReturnValueOnce({ x: 900, y: 100 });
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
  map.unproject.mockReturnValueOnce({ lng: 83, lat: 35 });
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
  expect(captureFrame).not.toHaveBeenCalled();
});

it("omits non-finite projected points and inverse coordinates", async () => {
  const { map, captureFrame } = setup();
  map.project.mockReturnValueOnce({ x: Number.NaN, y: 100 });
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
  map.unproject.mockReturnValueOnce({ lng: Number.NaN, lat: 35 });
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
  expect(captureFrame).not.toHaveBeenCalled();
});

it("discards a frame if the camera changes before capture completes", async () => {
  const { map, image } = setup();
  let finish: (canvas: HTMLCanvasElement) => void = () => undefined;
  mapCaptureSources.set(0, { attribution: () => "OpenStreetMap", captureFrame: () => new Promise((resolve) => { finish = resolve; }) });
  const pending = captureSoundingMap(context, 35, -97, size, new AbortController().signal);
  map.getZoom.mockReturnValue(6);
  finish(image);
  expect(await pending).toBeNull();
  expect([image.width, image.height]).toEqual([0, 0]);
});

it("does not let an old map unregister its replacement", async () => {
  const first = setup();
  const second = setup();
  first.remove();
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).not.toBeNull();
  second.remove();
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
});

it("omits a locator without real basemap attribution", async () => {
  const { captureFrame } = setup();
  mapCaptureSources.set(0, { captureFrame, attribution: () => "  " });
  expect(await captureSoundingMap(context, 35, -97, size, new AbortController().signal)).toBeNull();
  expect(captureFrame).not.toHaveBeenCalled();
});

it("cancels locator capture before a forecast export changes map density", async () => {
  setup();
  mapCaptureSources.set(0, { attribution: () => "OpenStreetMap", captureFrame: (_request, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => { reject(new DOMException("Capture cancelled", "AbortError")); }, { once: true });
  }) });
  const pending = captureSoundingMap(context, 35, -97, size, new AbortController().signal);
  useForecastStore.setState({ exporting: true });
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});

it("can capture the visible basemap when the model field is turned off", async () => {
  const { map, image, captureFrame } = setup();
  const captureVisible = vi.fn(() => Promise.resolve(image));
  registerSoundingLocatorMap(0, map as unknown as MapLibreMap, captureVisible);
  const bare = { panel: 0, request: null, product: null, model: null, frame: null };
  const unregister = registerSoundingMapContext(bare);
  const snapshot = await captureSoundingMap(bare, 35, -97, size, new AbortController().signal);
  expect(snapshot?.basemapAttribution).toContain("OpenStreetMap");
  expect(snapshot?.product).toBeNull();
  expect(captureVisible).toHaveBeenCalledWith(expect.any(AbortSignal), expect.objectContaining(size));
  expect(captureFrame).not.toHaveBeenCalled();
  image.width = size.width; image.height = size.height;
  mapCaptureSources.set(0, { captureFrame, attribution: () => "Natural Earth" });
  const fallback = await captureSoundingMap(bare, 35, -97, size, new AbortController().signal);
  expect(fallback?.basemapAttribution).toBe("Natural Earth");
  unregister();
});

it("does not reuse a model field for a different model run or valid time", () => {
  const summary = { kind: "model", model: "HRRR", provider: "hrrr", runTime: request.runTime, validTime: context.frame.validTime } as SoundingSummary;
  expect(mapContextMatchesSounding(context, summary, 0)).toBe(true);
  expect(mapContextMatchesSounding(context, { ...summary, provider: "gfs" }, 0)).toBe(false);
  expect(mapContextMatchesSounding(context, { ...summary, validTime: "2026-11-15T03:00:00Z" }, 0)).toBe(false);
  expect(mapContextMatchesSounding(context, { ...summary, times: [summary.validTime, "2026-11-15T03:00:00Z"] }, 1)).toBe(false);
  expect(mapContextMatchesSounding(context, { ...summary, kind: "observed" }, 0)).toBe(true);
});
