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
  let lastWidth = -1;
  let lastHeight = -1;
  const interleaved = import.meta.env.MODE !== "fixture" && mapProjection(useMapStore.getState()) === "mercator";
  const overlay = new MapLibreOverlay({
    interleaved,
    _pickable: !interleaved,
    layers: [],
    pickingRadius: options.pickingRadius,
    useDevicePixels: options.useDevicePixels,
    onDeviceInitialized: (device) => {
      canvasContext = device.getDefaultCanvasContext();
      lastWidth = -1;
      lastHeight = -1;
    },
    onBeforeRender: () => {
      // MapLibre owns this buffer. A pixel-ratio change leaves its CSS size unchanged,
      // so luma's ResizeObserver cannot update the framebuffer and clipping rectangle.
      if (interleaved && canvasContext !== null) {
        const { width, height } = canvasContext.canvas;
        if (width !== lastWidth || height !== lastHeight) {
          canvasContext.setDrawingBufferSize(width, height);
          lastWidth = width;
          lastHeight = height;
        }
      }
    },
    getCursor: cursor,
    ...(interleaved
      ? {}
      : {
          onHover: (info: PickingInfo) => {
            handlers.onHover(info);
          },
          onClick: (info: PickingInfo) => {
            handlers.onClick(info);
          },
        }),
    onAfterRender: () => {
      handlers.onAfterRender();
    },
    onError: (error) => {
      handlers.onError(error);
    },
  });
  if (interleaved && typeof overlay.pickObject === "function") {
    const pickObject = overlay.pickObject.bind(overlay);
    overlay.pickObject = (params) => {
      overlay.setProps({ _pickable: true });
      try {
        return pickObject(params);
      } finally {
        overlay.setProps({ _pickable: false });
      }
    };
  }
  const onRemove = overlay.onRemove.bind(overlay);
  overlay.onRemove = (map) => {
    try {
      onRemove(map);
    } finally {
      // deck.gl 9.4 finalizes layers but leaves the default canvas's DPR listener alive.
      canvasContext?.destroy();
      canvasContext = null;
      lastWidth = -1;
      lastHeight = -1;
    }
  };
  return overlay;
}
