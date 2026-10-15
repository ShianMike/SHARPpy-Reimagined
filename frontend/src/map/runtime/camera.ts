// Camera constants and helpers (DESIGN-frontend F4). No maplibre-gl import.

import type { CanvasLayout } from "../canvasTokens";

/** The initial view when no valid camera was saved: [[west, south], [east, north]]. */
export const CONUS_BOUNDS: [[number, number], [number, number]] = [
  [-125.0, 24.0],
  [-66.5, 50.0],
];

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 9;
/** A focus request zooms in to at least this level. */
export const FOCUS_ZOOM = 5;
/** moveend writes the camera snapshot after this quiet period. */
export const CAMERA_SNAPSHOT_DEBOUNCE_MS = 500;

/** The persisted camera: written on moveend, read when a map is built. */
export interface CameraSnapshot {
  center: [number, number];
  zoom: number;
}

export function isValidCamera(value: CameraSnapshot | null | undefined): value is CameraSnapshot {
  if (value === null || value === undefined) {
    return false;
  }
  const [lng, lat] = value.center;
  return (
    Number.isFinite(lng) &&
    Number.isFinite(lat) &&
    lng >= -180 &&
    lng <= 180 &&
    lat >= -85 &&
    lat <= 85 &&
    Number.isFinite(value.zoom) &&
    value.zoom >= MIN_ZOOM &&
    value.zoom <= MAX_ZOOM
  );
}

export interface Padding {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

/**
 * The map overlay's insets (DESIGN-frontend F8): fitBounds and centring avoid
 * the toolbar, the rail, and the timeline. Floating panels do not count.
 */
export function overlayPadding(layout: CanvasLayout): Padding {
  return {
    top: layout.toolbarHeight + layout.chromeGap,
    left: layout.railWidth + layout.chromeGap,
    bottom: layout.timelineHeight + 2 * layout.chromeGap,
    right: layout.chromeGap,
  };
}
