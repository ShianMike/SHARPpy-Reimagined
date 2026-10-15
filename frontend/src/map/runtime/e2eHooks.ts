// Test hooks (DESIGN-frontend F4), fixture builds only: MapRuntime loads this
// module through a dynamic import behind import.meta.env.MODE === "fixture",
// so desktop builds contain neither the chunk nor the hook name. The hooks
// always read the newest runtime, so a remount (Reload map) keeps them valid.

import { useGraphicsStore } from "../../stores/graphicsStore";
import type { GraphicsSelection } from "../../types/graphics";

export interface ProjectedPoint {
  /** Viewport CSS pixels, ready for a pointer move. */
  x: number;
  y: number;
}

export interface CameraState {
  center: [number, number];
  zoom: number;
}

/** What the hooks need from a runtime. */
export interface E2eTarget {
  projectStation: (id: string) => ProjectedPoint | null;
  labelCount: () => number;
  /** "id", "name", or null when no labels are shown. */
  labelTier: () => string | null;
  camera: () => CameraState | null;
  basemapFeatureCounts: () => Record<string, number>;
  mapErrors: () => string[];
  /** The deck.gl layers last drawn visible on the primary map, bottom first. */
  deckLayers: () => string[];
  /** Viewport pixels of a lon/lat on the primary map. */
  projectPoint: (longitude: number, latitude: number) => ProjectedPoint | null;
}

export interface SharppyE2eHooks {
  projectStation: (id: string) => ProjectedPoint | null;
  labelCount: () => number;
  labelTier: () => string | null;
  camera: () => CameraState | null;
  profile: () => GraphicsSelection;
  basemapFeatureCounts: () => Record<string, number>;
  mapErrors: () => string[];
  deckLayers: () => string[];
  projectPoint: (longitude: number, latitude: number) => ProjectedPoint | null;
}

declare global {
  interface Window {
    __SHARPPY_E2E__?: SharppyE2eHooks;
  }
}

let current: E2eTarget | null = null;

export function install(target: E2eTarget): void {
  current = target;
  if (Object.prototype.hasOwnProperty.call(window, "__SHARPPY_E2E__")) {
    return;
  }
  const hooks: SharppyE2eHooks = Object.freeze({
    projectStation: (id: string) => current?.projectStation(id) ?? null,
    labelCount: () => current?.labelCount() ?? 0,
    labelTier: () => current?.labelTier() ?? null,
    camera: () => current?.camera() ?? null,
    profile: () => structuredClone(useGraphicsStore.getState().effective),
    basemapFeatureCounts: () => current?.basemapFeatureCounts() ?? {},
    mapErrors: () => current?.mapErrors() ?? [],
    deckLayers: () => current?.deckLayers() ?? [],
    projectPoint: (longitude: number, latitude: number) => current?.projectPoint(longitude, latitude) ?? null,
  });
  Object.defineProperty(window, "__SHARPPY_E2E__", {
    value: hooks,
    writable: false,
    enumerable: false,
    configurable: false,
  });
}

/** A disposed runtime stops answering; the next one installs itself. */
export function uninstall(target: E2eTarget): void {
  if (current === target) {
    current = null;
  }
}
