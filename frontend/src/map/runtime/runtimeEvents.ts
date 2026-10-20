// The event handlers of a map runtime (DESIGN-frontend F4): MapLibre's events
// and deck.gl's hover, click, render, and error callbacks, each a no-op once the
// runtime is disposed (its live wrapper). Split from MapRuntime.ts for size.

import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import type { PickingInfo } from "@deck.gl/core";
import { MARKS, markOnce } from "../../lib/metrics";
import { useGraphicsStore } from "../../stores/graphicsStore";
import type { CanvasTokens } from "../canvasTokens";
import { useHoverStore } from "../hover/hoverStore";
import { attachMapEvents } from "./mapEvents";
import { createOverlay } from "./overlay";
import type { StartupWatch } from "./startupWatch";
import { clickFromPick, hoverFromPick } from "./stationScene";

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
}

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));

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
  return createOverlay(
    { pickingRadius: tokens.lengths.pickingRadius, useDevicePixels: pixelRatio },
    {
      onHover: live((info) => {
        if (!target.dragging) {
          hoverFromPick(info, target.panel, target.container.getBoundingClientRect());
        }
      }),
      onClick: live(clickFromPick),
      onAfterRender: live(() => {
        target.overlayReady = true;
        target.watch.afterRender();
      }),
      onError: live((error) => {
        target.errors.push(error.message);
        if (!target.watch.overlayError(error.message)) {
          target.runtimeError(`deck.gl: ${error.message}`);
        }
      }),
    },
  );
}

/** Interleaved overlays do not forward input. Pick once per animation frame, using MapLibre's coordinates. */
export function wireOverlayEvents(map: MapLibreMap, overlay: MapLibreOverlay, target: RuntimeEventTarget, radius: number): () => void {
  let pending: MapMouseEvent | null = null;
  let frame: number | null = null;
  const pick = (event: MapMouseEvent): PickingInfo => ({
    // Deck's picking resources are initialized asynchronously, before its first render.
    ...(target.overlayReady ? overlay.pickObject({ x: event.point.x, y: event.point.y, radius }) : null),
    x: event.point.x, y: event.point.y,
    coordinate: [event.lngLat.lng, event.lngLat.lat],
  }) as PickingInfo;
  const clear = (): void => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    pending = null;
    useHoverStore.getState().clear();
  };
  const move = target.live((event: MapMouseEvent) => {
    if (target.dragging || map.isMoving()) return;
    pending = event;
    if (frame !== null) return;
    frame = requestAnimationFrame(target.live(() => {
      frame = null;
      const latest = pending;
      pending = null;
      if (latest === null || target.dragging || map.isMoving()) return;
      hoverFromPick(pick(latest), target.panel, target.container.getBoundingClientRect());
    }));
  });
  const click = target.live((event: MapMouseEvent) => { clickFromPick(pick(event)); });
  map.on("mousemove", move);
  map.on("mouseout", clear);
  map.on("movestart", clear);
  map.on("click", click);
  return () => {
    clear();
    map.off("mousemove", move);
    map.off("mouseout", clear);
    map.off("movestart", clear);
    map.off("click", click);
  };
}
