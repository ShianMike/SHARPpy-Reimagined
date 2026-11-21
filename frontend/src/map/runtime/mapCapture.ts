import type { Map as MapLibreMap } from "maplibre-gl";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { FrameRequest } from "../mapQueries";
import { assertPlainMapCapture, type UnofficialForecastDrawingCapture } from "../forecastDrawing/forecastDrawingCapture";

export interface CaptureSize { width: number; height: number }
export interface MapCaptureResolution extends CaptureSize {
  /** Crop relative to the full, unchanged map viewport. Width and height above apply to this area. */
  crop?: { left: number; top: number; width: number; height: number };
}

export interface MapCaptureSource {
  captureFrame(request: FrameRequest, signal: AbortSignal, resolution?: MapCaptureResolution, drawing?: UnofficialForecastDrawingCapture): Promise<HTMLCanvasElement>;
  captureCurrent?(drawing: UnofficialForecastDrawingCapture): Promise<HTMLCanvasElement>;
  attribution?(): string;
}

/** Only mounted maps are capture targets; disposal cannot remove a replacement. */
export const mapCaptureSources = new Map<number, MapCaptureSource>();

export function registerMapCapture(panel: number, map: MapLibreMap, overlay: MapLibreOverlay, ready: (request: FrameRequest) => boolean,
  onOverlayRender: (listener: () => void) => () => void, isReadyForCurrent?: () => boolean): void {
  const lifetime = new AbortController();
  const source: MapCaptureSource = {
    captureFrame: (request, signal, resolution, drawing) => captureMapFrame(map, overlay,
      () => ready(request), onOverlayRender, AbortSignal.any([signal, lifetime.signal]), resolution, drawing),
    ...(isReadyForCurrent === undefined ? {} : { captureCurrent: (drawing: UnofficialForecastDrawingCapture) => captureMapFrame(map, overlay,
      isReadyForCurrent, onOverlayRender, lifetime.signal, undefined, drawing) }),
    attribution: () => {
      const credits = Object.keys(map.getStyle().sources).flatMap((id) => {
        const source = map.getSource(id);
        return source?.attribution ? [source.attribution] : [];
      });
      return new DOMParser().parseFromString([...new Set(credits)].join(" · "), "text/html").body.textContent;
    },
  };
  mapCaptureSources.set(panel, source);
  map.on("remove", () => {
    lifetime.abort();
    if (mapCaptureSources.get(panel) === source) mapCaptureSources.delete(panel);
  });
}

/** Copy WebGL buffers inside their render callbacks, without retaining drawing buffers during normal use. */
export async function captureMapFrame(map: MapLibreMap, overlay: MapLibreOverlay, ready: () => boolean,
  onOverlayRender: (listener: () => void) => () => void, signal: AbortSignal, resolution?: MapCaptureResolution,
  drawing?: UnofficialForecastDrawingCapture): Promise<HTMLCanvasElement> {
  if (drawing !== undefined) signal = AbortSignal.any([signal, drawing.signal]);
  signal.throwIfAborted();
  if (drawing === undefined) assertPlainMapCapture();
  const crop = resolution?.crop;
  const canvas = map.getCanvas();
  if (!(canvas.clientWidth > 0 && canvas.clientHeight > 0)) throw new Error("The map has no visible size. Open the map and try again.");
  return new Promise((resolve, reject) => {
    const base = document.createElement("canvas"), weather = document.createElement("canvas");
    const baseContext = base.getContext("2d"), weatherContext = weather.getContext("2d");
    if (baseContext === null || weatherContext === null) { reject(new Error("Map capture is unavailable.")); return; }
    let baseReady = false, weatherReady = false;
    let tick = 0, stopped = false;
    let unsubscribe = (): void => undefined;
    const originalRatio = map.getPixelRatio();
    const invalidate = (): void => { baseReady = false; weatherReady = false; };
    const stop = (): void => {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(tick);
      clearTimeout(timeout);
      map.off("render", copyBase);
      map.off("movestart", invalidate); map.off("resize", invalidate);
      unsubscribe();
      signal.removeEventListener("abort", abort);
      overlay.setProps({ _animate: false });
      if (resolution !== undefined) {
        map.setPixelRatio(originalRatio);
        overlay.setProps({ useDevicePixels: originalRatio });
      }
    };
    const fail = (error: unknown): void => {
      try { stop(); } finally { reject(error instanceof Error || error instanceof DOMException ? error : new Error(String(error))); }
    };
    const finish = (): void => {
      if (stopped || !baseReady || !weatherReady) return;
      if (overlay.getCanvas() !== map.getCanvas()) baseContext.drawImage(weather, 0, 0, base.width, base.height);
      stop();
      weather.width = 0; weather.height = 0;
      resolve(base);
    };
    const canCopy = (): boolean => {
      if (drawing === undefined) assertPlainMapCapture();
      if (ready() && map.areTilesLoaded() && !map.isMoving()) return true;
      invalidate();
      return false;
    };
    const copy = (source: HTMLCanvasElement, target: HTMLCanvasElement, context: CanvasRenderingContext2D): void => {
      if (crop === undefined) {
        target.width = source.width; target.height = source.height;
        context.drawImage(source, 0, 0);
        return;
      }
      const width = source.width * crop.width, height = source.height * crop.height;
      target.width = Math.max(1, Math.round(width)); target.height = Math.max(1, Math.round(height));
      context.drawImage(source, crop.left * source.width, crop.top * source.height, width, height,
        0, 0, target.width, target.height);
    };
    const copyBase = (): void => {
      if (stopped) return;
      try {
        if (!canCopy()) return;
        const canvas = map.getCanvas();
        copy(canvas, base, baseContext);
        baseReady = true;
        if (overlay.getCanvas() === canvas) weatherReady = true;
        finish();
      } catch (error) { fail(error); }
    };
    const copyWeather = (): void => {
      if (stopped) return;
      try {
        if (!canCopy()) return;
        const canvas = overlay.getCanvas();
        if (canvas === null || canvas === map.getCanvas()) return; // MapLibre's render event includes its final boundaries and labels.
        copy(canvas, weather, weatherContext);
        weatherReady = true;
        finish();
      } catch (error) { fail(error); }
    };
    const abort = (): void => { fail(signal.reason); };
    const timeout = setTimeout(() => { fail(new Error("The map did not finish drawing. Retry the frame or reload maps.")); }, 30_000);
    unsubscribe = onOverlayRender(copyWeather);
    signal.addEventListener("abort", abort, { once: true });
    map.on("render", copyBase);
    map.on("movestart", invalidate); map.on("resize", invalidate);
    const draw = (): void => {
      if (stopped) return;
      try {
        tick = requestAnimationFrame(draw);
        map.triggerRepaint();
      } catch (error) { fail(error); }
    };
    // Request fresh renders even when the camera is stationary.
    try {
      if (resolution !== undefined) {
        const canvas = map.getCanvas();
        const ratio = Math.max(resolution.width / (canvas.clientWidth * (crop?.width ?? 1)),
          resolution.height / (canvas.clientHeight * (crop?.height ?? 1)));
        map.setPixelRatio(ratio);
        // MapLibre may limit the drawing buffer on smaller GPUs. Keep the overlay at the actual density.
        overlay.setProps({ useDevicePixels: Math.min(canvas.width / canvas.clientWidth, canvas.height / canvas.clientHeight) });
      }
      overlay.setProps({ _animate: true });
      tick = requestAnimationFrame(draw);
    } catch (error) { fail(error); }
  });
}
