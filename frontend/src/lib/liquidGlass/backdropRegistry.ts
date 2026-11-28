// Registry of live MapLibre and deck.gl backdrop canvases for Liquid Glass capture.

import type { Map as MapLibreMap } from "maplibre-gl";
import type { MapLibreOverlay } from "@deck.gl/maplibre";

export type BackdropLayerKind = "base" | "overlay";

export interface BackdropFrameEvent {
  panel: number;
  kind: BackdropLayerKind;
  canvas: HTMLCanvasElement;
  rect: DOMRect;
  singleInterleaved: boolean;
}

export interface BackdropEntry {
  panel: number;
  map: MapLibreMap;
  overlay: MapLibreOverlay;
}

interface FrameSubscription {
  onFrame: (event: BackdropFrameEvent) => void;
  onTopologyChange: (() => void) | undefined;
}

interface BackdropRecord {
  entry: BackdropEntry;
  dispose: () => void;
  invalidateRects: () => void;
}

// Partial map mocks in unit tests may not implement the evented API surface.
interface OptionalMapEvents {
  on?: (event: string, listener: () => void) => void;
  off?: (event: string, listener: () => void) => void;
  isMoving?: () => boolean;
  triggerRepaint?: () => void;
}

function addResizeInvalidation(map: MapLibreMap, listener: () => void): () => void {
  const events: OptionalMapEvents = map;
  if (typeof events.on === "function") {
    events.on("resize", listener);
  }
  window.addEventListener("resize", listener);
  return () => {
    if (typeof events.off === "function") {
      events.off("resize", listener);
    }
    window.removeEventListener("resize", listener);
  };
}

const records = new Map<number, BackdropRecord>();
const frameSubscriptions = new Set<FrameSubscription>();

function notifyTopology(): void {
  for (const record of records.values()) {
    record.invalidateRects();
  }
  for (const sub of frameSubscriptions) {
    sub.onTopologyChange?.();
  }
}

function emitFrame(event: BackdropFrameEvent): void {
  for (const sub of frameSubscriptions) {
    sub.onFrame(event);
  }
}

/**
 * Registers a panel's MapLibre map and deck.gl overlay for synchronous WebGL
 * backdrop capture inside their respective render callbacks.
 */
export function registerLiquidGlassBackdrop(
  panel: number,
  map: MapLibreMap,
  overlay: MapLibreOverlay,
  onOverlayRender: (listener: () => void) => () => void,
): void {
  const existing = records.get(panel);
  if (existing !== undefined) {
    existing.dispose();
    records.delete(panel);
  }

  let disposed = false;
  let lastHighResCaptureMs = 0;
  let baseRect: DOMRect | null = null;
  let baseCanvasW = -1;
  let baseCanvasH = -1;
  let overlayRect: DOMRect | null = null;
  let overlayCanvasRef: HTMLCanvasElement | null = null;
  let overlayCanvasW = -1;
  let overlayCanvasH = -1;
  const mapEvents: OptionalMapEvents = map;

  const invalidateRects = (): void => {
    baseRect = null;
    overlayRect = null;
  };

  const resizeObserver =
    typeof ResizeObserver === "function" ? new ResizeObserver(() => { invalidateRects(); }) : null;

  const handleBaseRender = (): void => {
    if (disposed || frameSubscriptions.size === 0) {
      return;
    }
    const canvas = map.getCanvas();
    if (canvas.width * canvas.height > 2_600_000 && typeof mapEvents.isMoving === "function" && mapEvents.isMoving()) {
      const now = performance.now();
      if (now - lastHighResCaptureMs < 32) {
        return;
      }
      lastHighResCaptureMs = now;
    } else {
      lastHighResCaptureMs = 0;
    }
    if (baseRect === null || canvas.width !== baseCanvasW || canvas.height !== baseCanvasH) {
      const measured = canvas.getBoundingClientRect();
      if (measured.width < 1 || measured.height < 1) {
        baseRect = null;
        return;
      }
      baseRect = measured;
      baseCanvasW = canvas.width;
      baseCanvasH = canvas.height;
    }
    const rect = baseRect;
    const overlayCanvas = overlay.getCanvas();
    const singleInterleaved = records.size === 1 && (overlayCanvas === null || overlayCanvas === canvas);
    emitFrame({ panel, kind: "base", canvas, rect, singleInterleaved });
  };

  const handleOverlayRender = (): void => {
    if (disposed || frameSubscriptions.size === 0) {
      return;
    }
    const overlayCanvas = overlay.getCanvas();
    const mapCanvas = map.getCanvas();
    if (overlayCanvas === null || overlayCanvas === mapCanvas) {
      return;
    }
    if (
      overlayRect === null ||
      overlayCanvas !== overlayCanvasRef ||
      overlayCanvas.width !== overlayCanvasW ||
      overlayCanvas.height !== overlayCanvasH
    ) {
      const measured = overlayCanvas.getBoundingClientRect();
      if (measured.width < 1 || measured.height < 1) {
        overlayRect = null;
        return;
      }
      overlayRect = measured;
      overlayCanvasRef = overlayCanvas;
      overlayCanvasW = overlayCanvas.width;
      overlayCanvasH = overlayCanvas.height;
      resizeObserver?.observe(overlayCanvas);
    }
    const rect = overlayRect;
    emitFrame({ panel, kind: "overlay", canvas: overlayCanvas, rect, singleInterleaved: false });
  };

  const unsubscribeOverlay = onOverlayRender(handleOverlayRender);

  const handleRemove = (): void => {
    dispose();
    if (records.get(panel) === record) {
      records.delete(panel);
      notifyTopology();
    }
  };

  const handleMoveEnd = (): void => {
    if (disposed || lastHighResCaptureMs === 0) {
      return;
    }
    lastHighResCaptureMs = 0;
    if (typeof mapEvents.triggerRepaint === "function") {
      mapEvents.triggerRepaint();
    }
  };

  const removeResizeInvalidation = addResizeInvalidation(map, invalidateRects);

  const dispose = (): void => {
    if (disposed) {
      return;
    }
    disposed = true;
    map.off("render", handleBaseRender);
    map.off("remove", handleRemove);
    if (typeof mapEvents.off === "function") {
      mapEvents.off("moveend", handleMoveEnd);
    }
    removeResizeInvalidation();
    resizeObserver?.disconnect();
    unsubscribeOverlay();
  };

  const record: BackdropRecord = {
    entry: { panel, map, overlay },
    dispose,
    invalidateRects,
  };

  records.set(panel, record);
  map.on("render", handleBaseRender);
  map.on("remove", handleRemove);
  if (typeof mapEvents.on === "function") {
    mapEvents.on("moveend", handleMoveEnd);
  }
  if (resizeObserver !== null) {
    const mapCanvas = map.getCanvas();
    resizeObserver.observe(mapCanvas);
    const initialOverlayCanvas = overlay.getCanvas();
    if (initialOverlayCanvas !== null && initialOverlayCanvas !== mapCanvas) {
      resizeObserver.observe(initialOverlayCanvas);
    }
  }
  notifyTopology();
}

/** Subscribes to synchronous backdrop render events and optional panel mount/unmount notifications. */
export function subscribeBackdropFrames(
  onFrame: (event: BackdropFrameEvent) => void,
  onTopologyChange?: () => void,
): () => void {
  const sub: FrameSubscription = { onFrame, onTopologyChange };
  frameSubscriptions.add(sub);
  return () => {
    frameSubscriptions.delete(sub);
  };
}

/** Returns all currently mounted backdrop entries. */
export function getBackdropEntries(): readonly BackdropEntry[] {
  return Array.from(records.values(), (record) => record.entry);
}

/** Detaches all registered backdrops and clears subscribers for unit tests. */
export function clearBackdropRegistryForTest(): void {
  for (const record of records.values()) {
    record.dispose();
  }
  records.clear();
  frameSubscriptions.clear();
}
