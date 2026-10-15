// Label declutter (DESIGN-frontend F5). Pure: project the items, keep those
// inside the viewport, and keep each one at least a cell from every label kept
// before it, in priority order. A distance, not cell membership: two points a
// few pixels apart on either side of a cell edge would both be labelled.
// Stations go selected first, then by id, so labels do not jitter between
// moves; place names go in the order the backend gives (major cities, then
// towns largest first). Runs once per gesture, on moveend.

import type { Station } from "../../types/contracts";

/** Screen position in CSS pixels, relative to the map container. */
export interface ScreenPoint {
  x: number;
  y: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface DeclutterOptions {
  project: (station: Station) => ScreenPoint;
  viewport: Viewport;
  /** Cell size in CSS pixels. */
  cell: number;
  selectedId: string | null;
}

function byPriority(selectedId: string | null): (a: Station, b: Station) => number {
  return (a, b) => {
    if (a.id === selectedId) {
      return b.id === selectedId ? 0 : -1;
    }
    if (b.id === selectedId) {
      return 1;
    }
    if (a.id === b.id) {
      return 0;
    }
    return a.id < b.id ? -1 : 1;
  };
}

/** The cell key of a screen point. */
export function cellKey(point: ScreenPoint, cell: number): string {
  const size = Math.max(1, cell);
  return `${String(Math.floor(point.x / size))}:${String(Math.floor(point.y / size))}`;
}

/** Whether two screen points are less than `distance` apart on both axes. */
export function near(a: ScreenPoint, b: ScreenPoint, distance: number): boolean {
  return Math.abs(a.x - b.x) < distance && Math.abs(a.y - b.y) < distance;
}

/**
 * The items, in the given order, inside the viewport and at least `cell` from
 * every item kept before them on one axis or the other. A kept point owns its
 * cell (no other point in it can be that far away), so only the 3 by 3 cells
 * around a candidate need checking.
 */
export function declutterPoints<T>(
  items: readonly T[],
  project: (item: T) => ScreenPoint,
  viewport: Viewport,
  cell: number,
): T[] {
  const size = Math.max(1, cell);
  const owners = new Map<string, ScreenPoint>();
  const kept: T[] = [];
  for (const item of items) {
    const point = project(item);
    if (
      !Number.isFinite(point.x) ||
      !Number.isFinite(point.y) ||
      point.x < 0 ||
      point.y < 0 ||
      point.x > viewport.width ||
      point.y > viewport.height
    ) {
      continue;
    }
    const crowded = [-size, 0, size].some((dx) =>
      [-size, 0, size].some((dy) => {
        const owner = owners.get(cellKey({ x: point.x + dx, y: point.y + dy }, size));
        return owner !== undefined && near(owner, point, size);
      }),
    );
    if (crowded) {
      continue;
    }
    owners.set(cellKey(point, size), point);
    kept.push(item);
  }
  return kept;
}

export function declutter(stations: readonly Station[], options: DeclutterOptions): Station[] {
  const { project, viewport, cell, selectedId } = options;
  return declutterPoints([...stations].sort(byPriority(selectedId)), project, viewport, cell);
}
