// The MapLibre Map options (DESIGN-frontend F4), and the browser visibility
// source and clock the start-up watch takes. MapLibre types only.

import type { MapOptions } from "maplibre-gl";
import { profileSettings } from "../../lib/graphics/profileSettings";
import type { GraphicsProfile } from "../../types/graphics";
import { basemapStyle } from "../basemap/basemapStyle";
import type { CanvasTokens } from "../canvasTokens";
import { MAX_ZOOM, MIN_ZOOM } from "./camera";
import type { Clock, VisibilitySource } from "./startupWatch";

/** The pixel ratio for a profile at the current device pixel ratio. */
export function profilePixelRatio(profile: GraphicsProfile): number {
  return profileSettings(profile).pixelRatio(window.devicePixelRatio);
}

/**
 * A panel's pixel ratio: the profile's, scaled by fourPanelRenderScale when
 * four panels draw at once, but never under one device pixel per CSS pixel
 * (unless the profile's own ratio is): below it labels blur past reading.
 */
export function panelPixelRatio(profile: GraphicsProfile, panelLayout: number): number {
  const base = profilePixelRatio(profile);
  if (panelLayout !== 4) {
    return base;
  }
  return Math.max(Math.min(1, base), base * profileSettings(profile).fourPanelRenderScale);
}

/**
 * A north-up 2D map with no world copies (deck.gl layers are not repeated).
 * powerPreference "default" matches the graphics probe, so the probed
 * renderer is the one the map gets.
 */
export function mapOptions(container: HTMLElement, tokens: CanvasTokens, profile: GraphicsProfile): MapOptions {
  return {
    container,
    style: basemapStyle(tokens),
    minZoom: MIN_ZOOM,
    maxZoom: MAX_ZOOM,
    renderWorldCopies: false,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    maxPitch: 0,
    keyboard: true,
    fadeDuration: 0,
    attributionControl: { compact: true, customAttribution: "Natural Earth" },
    pixelRatio: profilePixelRatio(profile),
    canvasContextAttributes: {
      antialias: profileSettings(profile).antialias,
      preserveDrawingBuffer: false,
      powerPreference: "default",
    },
  };
}

export const documentVisibility: VisibilitySource = {
  isVisible: () => document.visibilityState === "visible",
  onChange: (listener) => {
    document.addEventListener("visibilitychange", listener);
    return () => {
      document.removeEventListener("visibilitychange", listener);
    };
  },
};

export const performanceClock: Clock = { now: () => performance.now() };
