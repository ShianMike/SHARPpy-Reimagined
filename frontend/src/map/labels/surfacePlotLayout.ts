import type { LngLatBounds, Map as MapLibreMap } from "maplibre-gl";
import type { SurfacePlot } from "../../types/surface";
import { wrapLongitude } from "../box/boxGeometry";
import { declutterPoints, type ScreenPoint, type Viewport } from "./declutter";

export const SURFACE_DETAIL_MIN_ZOOM = 5;

interface SurfaceDetail {
  plot: SurfacePlot;
  point: ScreenPoint;
}

const surfacePriorities = new WeakMap<readonly SurfacePlot[], { ordered: SurfacePlot[]; longestId: number }>();

/**
 * Run on moveend. Cache priority per observation array, then project only the
 * stations within the geographic bounds. The screen grid checks neighboring
 * cells and bounds the number of detailed plots by the viewport size.
 */
export function selectSurfaceDetails(
  plots: readonly SurfacePlot[], map: MapLibreMap, bounds: LngLatBounds,
  viewport: Viewport, labelSize: number, globe: boolean,
): SurfaceDetail[] {
  let priorities = surfacePriorities.get(plots);
  if (priorities === undefined) {
    const ordered = [...plots].sort((a, b) =>
      Number(b.availability === "available") - Number(a.availability === "available") || Number(a.stale) - Number(b.stale) || a.id.localeCompare(b.id),
    );
    const longestId = plots.reduce((length, plot) => Math.max(length, plot.id.length), 0);
    priorities = { ordered, longestId };
    surfacePriorities.set(plots, priorities);
  }
  const west = bounds.getWest(), east = bounds.getEast();
  const south = bounds.getSouth(), north = bounds.getNorth();
  const width = east >= west ? east - west : east + 360 - west;
  const candidates: SurfaceDetail[] = [];
  for (const plot of priorities.ordered) {
    const { longitude, latitude } = plot;
    const delta = longitude - west;
    // Bounds can straddle the date line, with the east or west edge outside ±180°.
    if (latitude < south || latitude > north || delta - Math.floor(delta / 360) * 360 > width) continue;
    const point = map.project([longitude, latitude]);
    if (!(point.x >= 0 && point.x <= viewport.width && point.y >= 0 && point.y <= viewport.height)) continue;
    if (globe) {
      // Projecting alone can place a far-side station on the visible disk.
      const { lng, lat } = map.unproject(point);
      if (!(Math.abs(lat - latitude) <= 0.01 && Math.abs(wrapLongitude(lng - longitude)) <= 0.01)) continue;
    }
    candidates.push({ plot, point });
  }
  return declutterPoints(candidates, ({ point }) => point, viewport, Math.max(84, priorities.longestId * labelSize + 28));
}
