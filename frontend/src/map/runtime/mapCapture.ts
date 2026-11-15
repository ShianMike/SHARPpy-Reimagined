import type { Map as MapLibreMap } from "maplibre-gl";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { FrameRequest } from "../mapQueries";

export interface MapCaptureSource {
  captureFrame(request: FrameRequest, signal: AbortSignal, resolution?: { width: number; height: number }): Promise<HTMLCanvasElement>;
  attribution?(): string;
}

/** Only mounted maps are capture targets; disposal cannot remove a replacement. */
export const mapCaptureSources = new Map<number, MapCaptureSource>();

export function registerMapCapture(panel: number, map: MapLibreMap, overlay: MapLibreOverlay, ready: (request: FrameRequest) => boolean,
  onOverlayRender: (listener: () => void) => () => void): void {
  const lifetime = new AbortController();
  const source: MapCaptureSource = {
    captureFrame: (request, signal, resolution) => captureMapFrame(map, overlay,
      () => ready(request), onOverlayRender, AbortSignal.any([signal, lifetime.signal]), resolution),
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
export function captureMapFrame(map: MapLibreMap, overlay: MapLibreOverlay, ready: () => boolean,
  onOverlayRender: (listener: () => void) => () => void, signal: AbortSignal, resolution?: { width: number; height: number }): Promise<HTMLCanvasElement> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const base = document.createElement("canvas"), weather = document.createElement("canvas");
    const baseContext = base.getContext("2d"), weatherContext = weather.getContext("2d");
    if (baseContext === null || weatherContext === null) { reject(new Error("Map capture is unavailable.")); return; }
    let baseReady = false, weatherReady = false;
    let tick: number;
    const originalRatio = map.getPixelRatio();
    const stop = (): void => {
      cancelAnimationFrame(tick);
      clearTimeout(timeout);
      overlay.setProps({ _animate: false });
      if (resolution !== undefined) {
        map.setPixelRatio(originalRatio);
        overlay.setProps({ useDevicePixels: originalRatio });
      }
      map.off("render", copyBase);
      unsubscribe();
      signal.removeEventListener("abort", abort);
    };
    const finish = (): void => {
      if (!baseReady || !weatherReady) return;
      stop();
      if (overlay.getCanvas() !== map.getCanvas()) baseContext.drawImage(weather, 0, 0, base.width, base.height);
      resolve(base);
    };
    const canCopy = (): boolean => {
      if (ready() && map.areTilesLoaded() && !map.isMoving()) return true;
      baseReady = false; weatherReady = false;
      return false;
    };
    const copyBase = (): void => {
      if (!canCopy()) return;
      const canvas = map.getCanvas();
      base.width = canvas.width; base.height = canvas.height;
      baseContext.drawImage(canvas, 0, 0);
      baseReady = true;
      if (overlay.getCanvas() === canvas) weatherReady = true;
      finish();
    };
    const unsubscribe = onOverlayRender(() => {
      if (!canCopy()) return;
      const canvas = overlay.getCanvas();
      if (canvas === null || canvas === map.getCanvas()) return; // MapLibre's render event includes its final boundaries and labels.
      weather.width = canvas.width; weather.height = canvas.height;
      weatherContext.clearRect(0, 0, weather.width, weather.height);
      weatherContext.drawImage(canvas, 0, 0);
      weatherReady = true;
      finish();
    });
    const abort = (): void => { stop(); reject(new DOMException("Map capture cancelled.", "AbortError")); };
    const timeout = setTimeout(() => { stop(); reject(new Error("The map did not finish drawing. Retry the frame or reload maps.")); }, 30_000);
    signal.addEventListener("abort", abort, { once: true });
    map.on("render", copyBase);
    const draw = (): void => {
      map.triggerRepaint();
      tick = requestAnimationFrame(draw);
    };
    // Request fresh renders even when the camera is stationary.
    if (resolution !== undefined) {
      const canvas = map.getCanvas();
      const ratio = Math.max(resolution.width / canvas.clientWidth, resolution.height / canvas.clientHeight);
      map.setPixelRatio(ratio);
      overlay.setProps({ useDevicePixels: ratio });
    }
    overlay.setProps({ _animate: true });
    tick = requestAnimationFrame(draw);
  });
}
