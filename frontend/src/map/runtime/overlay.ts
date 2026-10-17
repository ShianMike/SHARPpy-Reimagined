// The deck.gl attachment (DESIGN-frontend F4). MapRuntime adds it right after
// the Map is constructed, without waiting for load.
//
// Flat live maps share MapLibre's canvas. Globe uses a separate canvas because
// the two renderers' globe depth buffers disagree and hide weather meshes.
import type { PickingInfo } from "@deck.gl/core";
import { MapLibreOverlay } from "@deck.gl/maplibre";
import { useMapStore } from "../../stores/mapStore";

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
    interleaved: import.meta.env.MODE !== "fixture" && useMapStore.getState().projection === "mercator",
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
