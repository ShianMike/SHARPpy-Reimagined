// Per-profile settings (DESIGN-frontend F6). The glass values live in
// tokens.css ([data-graphics-profile] blocks); everything else is here.
// Budgets marked "defined only" are fixed now and applied in later phases.

import type { GraphicsProfile } from "../../types/graphics";

export interface LabelSettings {
  /** Station ids are labelled when the visible longitude span is at most this (degrees). */
  idSpanDeg: number;
  /** Names join the ids when the span is at most this (degrees). */
  nameSpanDeg: number;
  /** One label per screen cell of this size (CSS pixels). */
  declutterCell: number;
}

export interface ProfileSettings {
  /** MapLibre pixelRatio, and deck.gl useDevicePixels, for a device pixel ratio. */
  pixelRatio(devicePixelRatio: number): number;
  antialias: boolean;
  labels: LabelSettings;
  /** Defined only: each panel holds one field and one radar texture, so no budget is enforced. */
  gpuTextureBudgetMb: number;
  /** The field frames the cache keeps; the oldest unused frames go first (map/mapQueries.ts). */
  cpuFieldCacheMb: number;
  /** Frames prefetched ahead: `next`, plus `prefetched` while playing (map/usePlayback.ts); `previous` is defined only. */
  frameWindow: { previous: number; next: number; prefetched: number };
  /** The pixel-ratio scale of four panels (map/runtime/mapOptions.ts), never under 1. */
  fourPanelRenderScale: number;
  /** Defined only: render scale while the map is dragged, zoomed, or animated. */
  interactionRenderScale: number;
}

export const PROFILE_SETTINGS: Readonly<Record<GraphicsProfile, ProfileSettings>> = {
  low: {
    pixelRatio: () => 1,
    antialias: false,
    labels: { idSpanDeg: 30, nameSpanDeg: 8, declutterCell: 96 },
    gpuTextureBudgetMb: 128,
    cpuFieldCacheMb: 256,
    frameWindow: { previous: 1, next: 1, prefetched: 0 },
    fourPanelRenderScale: 0.5,
    interactionRenderScale: 0.75,
  },
  balanced: {
    pixelRatio: (dpr) => Math.min(dpr, 1.5),
    antialias: false,
    labels: { idSpanDeg: 55, nameSpanDeg: 16, declutterCell: 64 },
    gpuTextureBudgetMb: 256,
    cpuFieldCacheMb: 512,
    frameWindow: { previous: 1, next: 1, prefetched: 1 },
    fourPanelRenderScale: 0.75,
    interactionRenderScale: 0.85,
  },
  high: {
    pixelRatio: (dpr) => Math.min(dpr, 2),
    antialias: true,
    labels: { idSpanDeg: 55, nameSpanDeg: 16, declutterCell: 48 },
    gpuTextureBudgetMb: 512,
    cpuFieldCacheMb: 1024,
    frameWindow: { previous: 2, next: 2, prefetched: 1 },
    fourPanelRenderScale: 1,
    interactionRenderScale: 1,
  },
};

export function profileSettings(profile: GraphicsProfile): ProfileSettings {
  return PROFILE_SETTINGS[profile];
}
