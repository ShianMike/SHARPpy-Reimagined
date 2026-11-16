import type { Map as MapLibreMap } from "maplibre-gl";
import type { FrameRequest } from "../mapQueries";
import type { FieldFrame, FieldProduct } from "../../types/map";
import type { SoundingSummary } from "../../types/soundings";
import { useForecastStore } from "../../stores/forecastStore";
import { mapCaptureSources, type CaptureSize, type MapCaptureResolution } from "./mapCapture";

/** The mounted maps stay owned by their runtimes; this registry never moves them. */
type VisibleMapCapture = (signal: AbortSignal, resolution: MapCaptureResolution) => Promise<HTMLCanvasElement>;
const maps = new Map<number, { map: MapLibreMap; captureVisible?: VisibleMapCapture }>();
const contexts = new Map<number, SoundingMapContext>();
const listeners = new Set<() => void>();
const revisions = new WeakMap<SoundingMapContext, number>();
let nextRevision = 0;

export interface SoundingMapSnapshot {
  image: Blob;
  /** Normalized coordinates in the captured crop, suitable for placing a marker. */
  point: { x: number; y: number };
  basemapAttribution: string;
  model: string | null;
  product: FieldProduct | null;
  frame: Pick<FieldFrame, "runTime" | "forecastHour" | "validTime" | "attribution"> | null;
}

/** A displayed field is labelled with its own time; source changes never rewrite the snapshot. */
export interface SoundingMapContext {
  panel: number;
  request: FrameRequest | null;
  model: string | null;
  product: FieldProduct | null;
  frame: Pick<FieldFrame, "runTime" | "forecastHour" | "validTime" | "attribution"> | null;
}

/** A panel publishes only the field it already draws; this never initiates a fetch. */
export function registerSoundingMapContext(context: SoundingMapContext): () => void {
  revisions.set(context, ++nextRevision);
  contexts.set(context.panel, context);
  listeners.forEach((listener) => { listener(); });
  return () => {
    if (contexts.get(context.panel) !== context) return;
    contexts.delete(context.panel);
    listeners.forEach((listener) => { listener(); });
  };
}

export function subscribeSoundingMapContext(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function soundingMapContext(panel: number): SoundingMapContext | null {
  return contexts.get(panel) ?? null;
}

export function soundingMapRevision(context: SoundingMapContext | null): number {
  return context === null ? 0 : revisions.get(context) ?? 0;
}

export function mapContextMatchesSounding(context: SoundingMapContext, summary: SoundingSummary, timeIndex: number): boolean {
  if (context.request === null || context.frame === null || summary.kind !== "model") return true;
  const time = summary.times?.[timeIndex] ?? summary.validTime;
  const valid = time === null ? timeIndex === 0 && summary.forecastHour === context.request.forecastHour
    : Date.parse(time) === Date.parse(context.frame.validTime);
  return summary.provider === context.request.model && summary.runTime !== null
    && Date.parse(summary.runTime) === Date.parse(context.request.runTime) && valid;
}

export function registerSoundingLocatorMap(panel: number, map: MapLibreMap,
  captureVisible?: VisibleMapCapture): void {
  const entry = { map, ...(captureVisible === undefined ? {} : { captureVisible }) };
  maps.set(panel, entry);
  map.on("remove", () => { if (maps.get(panel) === entry) maps.delete(panel); });
}

function cameraKey(map: MapLibreMap): string {
  const center = map.getCenter();
  const canvas = map.getCanvas();
  return [center.lng, center.lat, map.getZoom(), map.getBearing(), map.getPitch(),
    JSON.stringify(map.getProjection()), canvas.clientWidth, canvas.clientHeight].join(",");
}

/** Check both screen bounds and the inverse projection, including the back of a globe. */
function projectedPoint(map: MapLibreMap, latitude: number, longitude: number): { x: number; y: number } | null {
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90) return null;
  const canvas = map.getCanvas();
  if (canvas.clientWidth <= 0 || canvas.clientHeight <= 0) return null;
  const point = map.project([longitude, latitude]);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.x > canvas.clientWidth || point.y < 0 || point.y > canvas.clientHeight) return null;
  const inverse = map.unproject(point);
  const longitudeError = ((inverse.lng - longitude + 540) % 360 + 360) % 360 - 180;
  if (!Number.isFinite(inverse.lat) || !Number.isFinite(longitudeError) || Math.abs(inverse.lat - latitude) > 0.01 || Math.abs(longitudeError) > 0.01) return null;
  return { x: point.x / canvas.clientWidth, y: point.y / canvas.clientHeight };
}

/** The 6.4× crop stays on the map and keeps the projected point inside it. */
export function locatorCrop(width: number, height: number, aspect: number, point: { x: number; y: number }) {
  const cropWidth = Math.min(width, height * aspect) / 6.4, cropHeight = cropWidth / aspect;
  const left = Math.max(0, Math.min(width - cropWidth, point.x * width - cropWidth / 2));
  const top = Math.max(0, Math.min(height - cropHeight, point.y * height - cropHeight / 2));
  return {
    crop: { left: left / width, top: top / height, width: cropWidth / width, height: cropHeight / height },
    point: { x: (point.x * width - left) / cropWidth, y: (point.y * height - top) / cropHeight },
  };
}

/**
 * Render a dense capture of the locator area without moving the visible map.
 * The caller supplies the panel's actual field request and waits until the field
 * is available. A point outside the view, missing attribution, or a changed map
 * yields no snapshot rather than a misleading locator.
 */
export async function captureSoundingMap(context: SoundingMapContext,
  latitude: number, longitude: number, size: CaptureSize, signal: AbortSignal): Promise<SoundingMapSnapshot | null> {
  signal.throwIfAborted();
  const { panel, request } = context;
  const entry = maps.get(panel);
  const map = entry?.map;
  const source = mapCaptureSources.get(panel);
  if (entry === undefined || map === undefined || source === undefined || map.isMoving() || useForecastStore.getState().exporting
    || request === null && entry.captureVisible === undefined) return null;
  const point = projectedPoint(map, latitude, longitude);
  const attribution = source.attribution?.().trim() ?? "";
  if (point === null || attribution === "") return null;
  const { clientWidth, clientHeight } = map.getCanvas();
  const locator = locatorCrop(clientWidth, clientHeight, size.width / size.height, point);
  const resolution = { ...size, crop: locator.crop };
  const camera = cameraKey(map);
  const interrupted = new AbortController();
  const unsubscribe = useForecastStore.subscribe((state) => { if (state.exporting) interrupted.abort(); });
  let canvas: HTMLCanvasElement | undefined;
  try {
    const captureSignal = AbortSignal.any([signal, interrupted.signal]);
    if (request === null) {
      if (entry.captureVisible === undefined) return null;
      canvas = await entry.captureVisible(captureSignal, resolution);
    } else canvas = await source.captureFrame(request, captureSignal, resolution);
    signal.throwIfAborted();
    interrupted.signal.throwIfAborted();
    if (maps.get(panel) !== entry || mapCaptureSources.get(panel) !== source || contexts.get(panel) !== context || map.isMoving() || cameraKey(map) !== camera) return null;
    const captured = canvas;
    const image = await new Promise<Blob>((resolve, reject) => {
      captured.toBlob((blob) => { if (blob === null) reject(new Error("The locator map could not be saved.")); else resolve(blob); }, "image/png");
    });
    signal.throwIfAborted();
    interrupted.signal.throwIfAborted();
    if (maps.get(panel) !== entry || mapCaptureSources.get(panel) !== source || contexts.get(panel) !== context || map.isMoving() || cameraKey(map) !== camera) return null;
    return { image, point: locator.point, basemapAttribution: attribution, model: context.model, product: context.product, frame: context.frame };
  } finally {
    unsubscribe();
    if (canvas !== undefined) { canvas.width = 0; canvas.height = 0; }
  }
}
