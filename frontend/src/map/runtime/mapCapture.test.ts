// @vitest-environment jsdom
import type { Map as MapLibreMap } from "maplibre-gl";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { UNOFFICIAL_FORECAST_TITLE } from "../../types/forecastDrawing";
import { FORECAST_EXPORT_DISCLAIMER } from "../forecastDrawing/forecastDrawingExportFooter";
import { exportForecastDrawingPng } from "../forecastDrawing/exportForecastDrawing";
import { captureMapFrame, mapCaptureSources, registerMapCapture } from "./mapCapture";

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", () => 1); vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage: vi.fn(), clearRect: vi.fn() } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  mapCaptureSources.clear();
  useAppStore.setState(useAppStore.getInitialState(), true);
  useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
  useMapStore.setState(useMapStore.getInitialState(), true);
  useUiStore.getState().setForecastOutlinesOnly(false);
  useUiStore.getState().setForecastDrawingDetails(useUiStore.getInitialState().forecastDrawingDetails);
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

function setup(interleaved = false) {
  const canvas = document.createElement("canvas"), weather = interleaved ? canvas : document.createElement("canvas");
  Object.defineProperties(canvas, { clientWidth: { value: 500 }, clientHeight: { value: 400 } });
  canvas.width = 500; canvas.height = 400; weather.width = 500; weather.height = 400;
  const handlers = new Map<string, () => void>();
  const map = { getCanvas: () => canvas, getPixelRatio: () => 1, triggerRepaint: vi.fn(), isMoving: vi.fn(() => false),
    areTilesLoaded: vi.fn(() => true),
    getStyle: () => ({ sources: { basemap: {} } }), getSource: () => ({ attribution: "<a>OpenStreetMap</a>" }),
    setPixelRatio: vi.fn((ratio: number) => { canvas.width = 500 * ratio; canvas.height = 400 * ratio; }),
    on: (name: string, callback: () => void) => { handlers.set(name, callback); }, off: (name: string) => { handlers.delete(name); } };
  const overlay = { getCanvas: () => weather, setProps: vi.fn((props: { useDevicePixels?: number }) => {
    if (props.useDevicePixels !== undefined) { weather.width = 500 * props.useDevicePixels; weather.height = 400 * props.useDevicePixels; }
  }) };
  return { map: map as unknown as MapLibreMap, overlay: overlay as unknown as MapLibreOverlay, handlers, state: map, props: overlay.setProps };
}

it.each([false, true])("waits for a complete field and fresh map/overlay renders at export density (shared canvas=%s)", async (interleaved) => {
  const { map, overlay, handlers, state, props } = setup(interleaved);
  let ready = false;
  let overlayRendered: () => void = () => undefined;
  const unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => ready, (callback) => { overlayRendered = callback; return unsubscribe; }, new AbortController().signal, { width: 960, height: 540 });
  expect(state.setPixelRatio).toHaveBeenCalledWith(1.92);
  handlers.get("render")?.(); overlayRendered();
  expect(unsubscribe).not.toHaveBeenCalled();
  ready = true; state.areTilesLoaded.mockReturnValue(false);
  handlers.get("render")?.(); expect(unsubscribe).not.toHaveBeenCalled();
  state.areTilesLoaded.mockReturnValue(true); handlers.get("render")?.(); overlayRendered();
  const captured = await job;
  expect([captured.width, captured.height]).toEqual([960, 768]);
  expect(state.setPixelRatio).toHaveBeenLastCalledWith(1);
  expect(props).toHaveBeenCalledWith({ _animate: false });
  expect(unsubscribe).toHaveBeenCalledOnce(); expect(handlers.has("render")).toBe(false);
});

it("renders both map layers at the density needed by the inset and copies only its pixels", async () => {
  const { map, overlay, state, handlers } = setup();
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
  let overlayRendered = (): void => undefined;
  const job = captureMapFrame(map, overlay, () => true, (render) => { overlayRendered = render; return () => undefined; },
    new AbortController().signal, { width: 400, height: 320, crop: { left: 0.5, top: 0.25, width: 0.25, height: 0.25 } });
  expect(state.setPixelRatio).toHaveBeenCalledWith(3.2);
  handlers.get("render")?.(); overlayRendered();
  const captured = await job;
  expect([captured.width, captured.height]).toEqual([400, 320]);
  expect(drawImage).toHaveBeenCalledWith(map.getCanvas(), 800, 320, 400, 320, 0, 0, 400, 320);
  expect(drawImage).toHaveBeenCalledWith(overlay.getCanvas(), 800, 320, 400, 320, 0, 0, 400, 320);
  expect(state.setPixelRatio).toHaveBeenLastCalledWith(1);
});

it("waits for a new overlay render after the camera stops moving", async () => {
  const { map, overlay, handlers, state } = setup();
  let overlayRendered: () => void = () => undefined;
  const unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => true, (callback) => { overlayRendered = callback; return unsubscribe; },
    new AbortController().signal, { width: 960, height: 540 });
  state.isMoving.mockReturnValue(true);
  overlayRendered();
  state.isMoving.mockReturnValue(false);
  handlers.get("render")?.();
  expect(unsubscribe).not.toHaveBeenCalled();
  overlayRendered();
  await job;
  expect(unsubscribe).toHaveBeenCalledOnce();
});

it.each([["movestart", "weather"], ["movestart", "base"], ["resize", "weather"], ["resize", "base"]])(
  "discards both captured buffers after %s with the old %s buffer ready", async (event, first) => {
  const { map, overlay, handlers } = setup();
  let overlayRendered: () => void = () => undefined;
  const unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => true, (callback) => { overlayRendered = callback; return unsubscribe; },
    new AbortController().signal, { width: 960, height: 540 });
  if (first === "weather") overlayRendered(); else handlers.get("render")?.();
  handlers.get(event)?.();
  if (first === "weather") handlers.get("render")?.(); else overlayRendered();
  expect(unsubscribe).not.toHaveBeenCalled();
  if (first === "weather") overlayRendered(); else handlers.get("render")?.();
  await job;
  expect(unsubscribe).toHaveBeenCalledOnce(); expect(handlers.size).toBe(0);
});

it("aborts a pending capture and restores render density", async () => {
  const { map, overlay, state, handlers } = setup();
  const controller = new AbortController(), unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => false, () => unsubscribe, controller.signal, { width: 1920, height: 1080 });
  const rejected = expect(job).rejects.toMatchObject({ name: "AbortError" });
  controller.abort(); await rejected;
  expect(state.setPixelRatio).toHaveBeenLastCalledWith(1); expect(unsubscribe).toHaveBeenCalledOnce(); expect(handlers.has("render")).toBe(false);
});

it("rejects a hidden map before requesting an infinite pixel ratio", async () => {
  const { map, overlay, state, props, handlers } = setup();
  vi.spyOn(map, "getCanvas").mockReturnValue(document.createElement("canvas"));
  const controller = new AbortController(), unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => false, () => unsubscribe, controller.signal, { width: 1920, height: 1080 });
  const rejected = expect(job).rejects.toThrow("visible size");
  controller.abort(); await rejected;
  expect(state.setPixelRatio).not.toHaveBeenCalled();
  expect(props).not.toHaveBeenCalled(); expect(handlers.size).toBe(0);
});

it("rejects draw errors immediately and releases render listeners", async () => {
  const { map, overlay, handlers, state } = setup();
  const context = { drawImage: () => { throw new Error("Drawing buffer lost"); }, clearRect: () => undefined };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const controller = new AbortController(), unsubscribe = vi.fn();
  const job = captureMapFrame(map, overlay, () => true, () => unsubscribe, controller.signal, { width: 1920, height: 1080 });
  const rejected = expect(job).rejects.toThrow("Drawing buffer lost");
  try { expect(() => { handlers.get("render")?.(); }).not.toThrow(); }
  finally { controller.abort(); }
  await rejected;
  expect(state.setPixelRatio).toHaveBeenLastCalledWith(1);
  expect(unsubscribe).toHaveBeenCalledOnce(); expect(handlers.size).toBe(0);
});

it("does not let removal of an old map unregister its replacement", () => {
  const old = setup(), current = setup();
  registerMapCapture(0, old.map, old.overlay, () => true, () => () => undefined);
  registerMapCapture(0, current.map, current.overlay, () => true, () => () => undefined);
  const source = mapCaptureSources.get(0);
  old.handlers.get("remove")?.(); expect(mapCaptureSources.get(0)).toBe(source);
  current.handlers.get("remove")?.(); expect(mapCaptureSources.has(0)).toBe(false);
});

function showForecastArea(): void {
  useAppStore.getState().setActiveWorkspace("map");
  useForecastDrawingStore.setState({ enabled: true, results: { cat: {
    areas: [{ label: "Marginal", description: "", fill: null, stroke: "green", rank: 1,
      hatch: false, hatchLevel: 0, rings: [[[-98, 34], [-95, 35], [-96, 37]]] }],
    clippedHatches: [], hiddenHatches: [], generatedContours: [],
  } } });
}

it("cancels a drawing export if the display style changes during capture", async () => {
  showForecastArea();
  const { map, overlay, handlers } = setup();
  const unsubscribe = vi.fn();
  registerMapCapture(0, map, overlay, () => false, () => unsubscribe, () => false);
  const job = exportForecastDrawingPng(0, new AbortController().signal);
  useUiStore.getState().setForecastOutlinesOnly(true);
  await expect(job).rejects.toThrow("The forecast display changed during export.");
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(handlers.has("render")).toBe(false);
  expect(useForecastStore.getState().exporting).toBe(false);
});

it("captures the visible drawing with the model field off and downloads a PNG with the outlook details and notice", async () => {
  vi.useFakeTimers();
  showForecastArea();
  useUiStore.getState().setForecastDrawingDetails({ author: "Casey Nguyen", day: 2 });
  useMapStore.getState().setLayerVisible("model-field", false);
  const { map, overlay, handlers } = setup();
  const fieldReady = vi.fn(() => false), currentReady = vi.fn(() => false);
  let overlayRendered = (): void => undefined;
  registerMapCapture(0, map, overlay, fieldReady, (listener) => { overlayRendered = listener; return () => undefined; }, currentReady);
  const render = { save: vi.fn(), restore: vi.fn(), drawImage: vi.fn(), fillRect: vi.fn(),
    fillText: vi.fn<CanvasRenderingContext2D["fillText"]>(), measureText: (line: string) => ({ width: line.length * 8 }) };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(render as unknown as CanvasRenderingContext2D);
  const png = new Blob(["png"], { type: "image/png" });
  const encode = vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done) => { done(png); });
  const link = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  const createObjectURL = vi.fn(() => "blob:forecast-drawing"), revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }));

  const job = exportForecastDrawingPng(0, new AbortController().signal);
  handlers.get("render")?.(); overlayRendered();
  expect(encode).not.toHaveBeenCalled();
  currentReady.mockReturnValue(true);
  handlers.get("render")?.(); overlayRendered();
  await job;

  expect(fieldReady).not.toHaveBeenCalled();
  expect(render.drawImage.mock.calls[0]?.[0]).toBe(map.getCanvas());
  expect(render.drawImage.mock.calls[1]?.[0]).toBe(overlay.getCanvas());
  const labels = render.fillText.mock.calls.map(([label]) => label);
  expect(labels[0]).toBe(UNOFFICIAL_FORECAST_TITLE);
  expect(labels.slice(1, -1).join(" ")).toBe(`Categorical Risk · Day 2 · By Casey Nguyen ${FORECAST_EXPORT_DISCLAIMER}`);
  expect(labels.at(-1)).toBe("OpenStreetMap");
  expect(render.fillText.mock.invocationCallOrder[0]).toBeGreaterThan(render.drawImage.mock.invocationCallOrder.at(-1) ?? 0);
  expect(encode).toHaveBeenCalledWith(expect.any(Function), "image/png");
  expect(createObjectURL).toHaveBeenCalledWith(png);
  const downloaded = link.mock.instances[0] as HTMLAnchorElement | undefined;
  expect(downloaded?.download).toMatch(/^sharppy-unofficial-forecast-cat-.*\.png$/);
  expect(useForecastStore.getState().exporting).toBe(false);
  vi.runOnlyPendingTimers();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:forecast-drawing");
});

it("discards a plain map capture if a sketch starts between its base and Deck renders", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  const { map, overlay, handlers } = setup();
  let overlayRendered = (): void => undefined;
  const job = captureMapFrame(map, overlay, () => true, (listener) => { overlayRendered = listener; return () => undefined; },
    new AbortController().signal, { width: 400, height: 320 });
  handlers.get("render")?.();
  useForecastDrawingStore.setState({ enabled: true, mode: "draw" });
  overlayRendered();
  await expect(job).rejects.toThrow("Turn off Draw Forecast");
  expect(handlers.has("render")).toBe(false);
});
