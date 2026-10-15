// The deck.gl attachment (DESIGN-frontend F4). MapRuntime adds it right after
// the Map is constructed, without waiting for load.
//
// Live maps share MapLibre's canvas so its original outlines can draw over weather.
// Offline fixtures retain their local boundary pass and separate canvas.
import type { PickingInfo } from "@deck.gl/core";
import { MapLibreOverlay } from "@deck.gl/maplibre";

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
  return new MapLibreOverlay({
    interleaved: import.meta.env.MODE !== "fixture",
    layers: [],
    pickingRadius: options.pickingRadius,
    useDevicePixels: options.useDevicePixels,
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
}
