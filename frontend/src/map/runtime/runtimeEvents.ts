// The event handlers of a map runtime (DESIGN-frontend F4): MapLibre's events
// and deck.gl's hover, click, render, and error callbacks, each a no-op once the
// runtime is disposed (its live wrapper). Split from MapRuntime.ts for size.

import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import type { PickingInfo } from "@deck.gl/core";
import { MARKS, markOnce } from "../../lib/metrics";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import type { CanvasTokens } from "../canvasTokens";
import { useHoverStore } from "../hover/hoverStore";
import { attachMapEvents } from "./mapEvents";
import { createOverlay } from "./overlay";
import type { StartupWatch } from "./startupWatch";
import { clickFromPick, hoverFromPick } from "./stationScene";
import type { StationScene } from "./stationScene";
import { captureMapFrame, registerMapCapture } from "./mapCapture";
import { registerLiquidGlassBackdrop } from "../../lib/liquidGlass/backdropRegistry";
import { registerSoundingLocatorMap } from "./soundingLocator";

/** What the handlers need from their runtime. */
export interface RuntimeEventTarget {
  readonly panel: number;
  readonly container: HTMLElement;
  readonly primary: boolean;
  readonly watch: StartupWatch;
  readonly errors: string[];
  overlayReady: boolean;
  dragging: boolean;
  live<T extends unknown[]>(handler: (...args: T) => void): (...args: T) => void;
  styleLoaded(): void;
  runtimeError(message: string): void;
  readonly map?: MapLibreMap | null;
  readonly scene?: (Pick<StationScene, "matchesField" | "readyForCapture"> & {
    readonly needsGpuHoverPick?: StationScene["needsGpuHoverPick"];
  }) | null;
}

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));

/** The container's viewport offset, read lazily and dropped when the layout may move it. */
function createOriginCache(container: HTMLElement): { current(): { left: number; top: number }; dispose(): void } {
  let origin: { left: number; top: number } | null = null;
  const invalidate = (): void => { origin = null; };
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(invalidate) : null;
  observer?.observe(container);
  window.addEventListener("resize", invalidate);
  return {
    current: () => {
      if (origin === null) {
        const box = container.getBoundingClientRect();
        origin = { left: box.left, top: box.top };
      }
      return origin;
    },
    dispose: () => { observer?.disconnect(); window.removeEventListener("resize", invalidate); },
  };
}

export function wireMapEvents(map: MapLibreMap, target: RuntimeEventTarget): void {
  const live = target.live.bind(target);
  attachMapEvents(map, {
    error: live((event, sourceId) => {
      const message = messageOf(event.error);
      target.errors.push(message);
      if (sourceId === null || !target.watch.sourceError(sourceId)) {
        target.runtimeError(`MapLibre: ${message}`);
      }
    }),
    loaded: live(() => {
      if (target.primary) {
        markOnce(MARKS.mapLoaded);
      }
    }),
    styleLoaded: live(() => {
      target.styleLoaded();
    }),
    sourceLoaded: live((sourceId) => {
      target.watch.sourceLoaded(sourceId);
    }),
    contextLost: live(() => {
      target.watch.overlayError("The graphics context was lost.");
      if (target.primary) useGraphicsStore.getState().setContextLost(true);
    }),
    dragStart: live(() => {
      target.dragging = true;
      useHoverStore.getState().clear();
    }),
    dragEnd: live(() => {
      target.dragging = false;
    }),
  });
}

export function createPanelOverlay(tokens: CanvasTokens, pixelRatio: number, target: RuntimeEventTarget): MapLibreOverlay {
  const live = target.live.bind(target);
  const captures = new Set<() => void>();
  const origin = createOriginCache(target.container);
  const overlay = createOverlay(
    { pickingRadius: tokens.lengths.pickingRadius, useDevicePixels: pixelRatio },
    {
      onHover: live((info) => {
        if (!target.dragging && (useMapStore.getState().view === "live" || useBoxSoundingStore.getState().mode === "point")) {
          hoverFromPick(info, target.panel, origin.current());
        }
      }),
      onClick: live((info) => { clickFromPick(info, target.panel, origin.current()); }),
      onAfterRender: live(() => {
        target.overlayReady = true;
        target.watch.afterRender();
        captures.forEach((listener) => { listener(); });
      }),
      onError: live((error) => {
        target.errors.push(error.message);
        if (!target.watch.overlayError(error.message)) {
          target.runtimeError(`deck.gl: ${error.message}`);
        }
      }),
    },
  );
  if (target.map !== undefined && target.map !== null) {
    const map = target.map;
    const onOverlayRender = (listener: () => void): (() => void) => { captures.add(listener); return () => { captures.delete(listener); }; };
    registerMapCapture(target.panel, map, overlay,
      (request) => target.scene?.matchesField(request) === true,
      onOverlayRender, () => target.overlayReady && map.isStyleLoaded() === true);
    registerLiquidGlassBackdrop(target.panel, map, overlay, onOverlayRender);
    registerSoundingLocatorMap(target.panel, map, (signal, resolution) => captureMapFrame(map, overlay,
      () => target.overlayReady && target.scene?.readyForCapture() === true
        && !panelLayers(useMapStore.getState(), target.panel)["model-field"].visible,
      onOverlayRender, signal, resolution));
  }
  const removeOverlay = (overlay as { onRemove?: (map: MapLibreMap) => void }).onRemove?.bind(overlay);
  overlay.onRemove = (map) => {
    origin.dispose();
    removeOverlay?.(map);
  };
  return overlay;
}

/** Interleaved overlays do not forward input. Pick once per animation frame, using MapLibre's coordinates. */
export function wireOverlayEvents(map: MapLibreMap, overlay: MapLibreOverlay, target: RuntimeEventTarget, radius: number): () => void {
  let pending: MapMouseEvent | null = null;
  let frame: number | null = null;
  let hadGpuHit = false;
  const origin = createOriginCache(target.container);
  const pick = (event: MapMouseEvent, gpu: boolean): PickingInfo => ({
    // Deck's picking resources are initialized asynchronously, before its first render.
    ...(target.overlayReady && gpu ? overlay.pickObject({ x: event.point.x, y: event.point.y, radius }) : null),
    x: event.point.x, y: event.point.y,
    coordinate: [event.lngLat.lng, event.lngLat.lat],
  }) as PickingInfo;
  const clear = (keepPinnedSpotter = false): void => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    pending = null;
    hadGpuHit = false;
    useHoverStore.getState().clear(keepPinnedSpotter);
  };
  const leave = (): void => { clear(true); };
  const moveStart = (): void => { clear(); };
  const move = target.live((event: MapMouseEvent) => {
    if (target.dragging || map.isMoving() || !useHoverStore.getState().inspecting
      || (useMapStore.getState().view === "map" && useBoxSoundingStore.getState().mode === "box")) return;
    pending = event;
    if (frame !== null) return;
    frame = requestAnimationFrame(target.live(() => {
      frame = null;
      const latest = pending;
      pending = null;
      if (latest === null || target.dragging || map.isMoving() || !useHoverStore.getState().inspecting) return;
      const scene = target.scene;
      const gpu = target.overlayReady
        && (scene?.needsGpuHoverPick === undefined || hadGpuHit
          || scene.needsGpuHoverPick(map, latest.point.x, latest.point.y, radius));
      const info = pick(latest, gpu);
      hadGpuHit = gpu && info.object != null;
      hoverFromPick(info, target.panel, origin.current());
    }));
  });
  const click = target.live((event: MapMouseEvent) => {
    clickFromPick(pick(event, true), target.panel, origin.current());
  });
  map.on("mousemove", move);
  map.on("mouseout", leave);
  map.on("movestart", moveStart);
  map.on("click", click);
  return () => {
    clear();
    origin.dispose();
    map.off("mousemove", move);
    map.off("mouseout", leave);
    map.off("movestart", moveStart);
    map.off("click", click);
  };
}
