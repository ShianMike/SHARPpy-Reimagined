// The deck.gl attachment (DESIGN-frontend F4). MapRuntime adds it right after
// the Map is constructed, without waiting for load.
//
// Flat live maps share MapLibre's canvas. Globe uses a separate canvas because
// the two renderers' globe depth buffers disagree and hide weather meshes.
import type { PickingInfo } from "@deck.gl/core";
import { MapLibreOverlay } from "@deck.gl/maplibre";
import type { CanvasContext } from "@luma.gl/core";
import { mapProjection, useMapStore } from "../../stores/mapStore";

export interface OverlayHandlers {
  onHover: (info: PickingInfo) => void;
  onClick: (info: PickingInfo) => void;
  onAfterRender: () => void;
  onError: (error: Error) => void;
}

export interface OverlayOptions {
  /** CSS pixels (--map-picking-radius). */
  pickingRadius: number;
  /** The profile's pixel ratio; used only if the overlay is not interleaved. */
  useDevicePixels: number;
}

function cursor({ isDragging, isHovering }: { isDragging: boolean; isHovering: boolean }): string {
  if (isDragging) {
    return "grabbing";
  }
  return isHovering ? "pointer" : "grab";
}

export function createOverlay(options: OverlayOptions, handlers: OverlayHandlers): MapLibreOverlay {
  let canvasContext: CanvasContext | null = null;
  const overlay = new MapLibreOverlay({
    interleaved: import.meta.env.MODE !== "fixture" && mapProjection(useMapStore.getState()) === "mercator",
    layers: [],
    pickingRadius: options.pickingRadius,
    useDevicePixels: options.useDevicePixels,
    onDeviceInitialized: (device) => { canvasContext = device.getDefaultCanvasContext(); },
    getCursor: cursor,
    onHover: (info) => {
      handlers.onHover(info);
    },
    onClick: (info) => {
      handlers.onClick(info);
    },
    onAfterRender: () => {
      handlers.onAfterRender();
    },
    onError: (error) => {
      handlers.onError(error);
    },
  });
  const onRemove = overlay.onRemove.bind(overlay);
  overlay.onRemove = (map) => {
    try {
      onRemove(map);
    } finally {
      // deck.gl 9.4 finalizes layers but leaves the default canvas's DPR listener alive.
      canvasContext?.destroy();
      canvasContext = null;
    }
  };
  return overlay;
}
