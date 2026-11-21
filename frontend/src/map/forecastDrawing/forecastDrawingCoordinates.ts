import type { LngLat, Map as MapLibreMap, Point } from "maplibre-gl";
import type { ForecastPoint } from "../../types/forecastDrawing";
import { wrapLongitude } from "../box/boxGeometry";
import { containsPoint } from "../layers/outlookGeometry";

export function forecastMapPoint(map: MapLibreMap, screen: Pick<Point, "x" | "y">, position: LngLat): ForecastPoint | null {
  const { lng, lat } = position.wrap();
  const projected = map.project(position);
  // A position off the globe can unproject to its horizon instead.
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || Math.abs(lat) >= 90
    || Math.hypot(projected.x - screen.x, projected.y - screen.y) > 2) return null;
  return [lng, lat];
}

export function forecastRingContains(ring: readonly ForecastPoint[], point: ForecastPoint): boolean {
  const anchor = ring[0]?.[0];
  if (anchor === undefined) return false;
  return containsPoint([ring.map(([lon, lat]) => [wrapLongitude(lon - anchor), lat])],
    [wrapLongitude(point[0] - anchor), point[1]]);
}

/** Resolve clicks near the rounded outline when it differs from the edit vertices. */
export function forecastRingDistance(ring: readonly ForecastPoint[], point: ForecastPoint): number {
  const longitudeScale = Math.cos(Math.min(85, Math.abs(point[1])) * Math.PI / 180);
  let nearest = Number.POSITIVE_INFINITY;
  for (const [index, a] of ring.entries()) {
    const b = ring[(index + 1) % ring.length] ?? a;
    const x = wrapLongitude(a[0] - point[0]) * longitudeScale;
    const y = a[1] - point[1];
    const dx = wrapLongitude(b[0] - a[0]) * longitudeScale;
    const dy = b[1] - a[1];
    const length = dx * dx + dy * dy;
    const t = length === 0 ? 0 : Math.max(0, Math.min(1, -(x * dx + y * dy) / length));
    nearest = Math.min(nearest, Math.hypot(x + t * dx, y + t * dy));
  }
  return nearest;
}

/** Shift all the original vertices by one offset, including at the date line and poles. */
export function translateForecastRing(ring: readonly ForecastPoint[], from: ForecastPoint, to: ForecastPoint): readonly ForecastPoint[] {
  const latitudes = ring.map(([, lat]) => lat);
  const dx = wrapLongitude(to[0] - from[0]);
  const dy = Math.max(-90 - Math.min(...latitudes), Math.min(90 - Math.max(...latitudes), to[1] - from[1]));
  if (dx === 0 && dy === 0) return ring;
  return ring.map(([lon, lat]) => [wrapLongitude(lon + dx), lat + dy]);
}

/** Match the rounded contour during a gesture. The backend validates the saved area. */
export function forecastPreviewPath(points: readonly ForecastPoint[], closed: boolean): [number, number][] {
  if (points.length < 3) return points.map(([lon, lat]) => [lon, lat]);
  const latitudes = points.map(([, lat]) => lat);
  const centerLat = (Math.min(...latitudes) + Math.max(...latitudes)) / 2;
  const kmPerDegree = 111.195;
  const longitudeScale = kmPerDegree * Math.cos(Math.min(85, Math.abs(centerLat)) * Math.PI / 180);
  const trim = (longitude: number, latitude: number): number =>
    Math.min(0.2, 32 / Math.hypot(longitude * longitudeScale, latitude * kmPerDegree));
  const path: [number, number][] = [];
  points.forEach(([lon, lat], index) => {
    const previous = closed ? points.at(index - 1) : points[index - 1];
    const next = closed ? points[(index + 1) % points.length] : points[index + 1];
    if (previous === undefined || next === undefined) {
      path.push([lon, lat]);
      return;
    }
    const previousLon = wrapLongitude(previous[0] - lon), previousLat = previous[1] - lat;
    const nextLon = wrapLongitude(next[0] - lon), nextLat = next[1] - lat;
    if (Math.abs(previousLon * nextLat - nextLon * previousLat) < 1e-12) {
      path.push([lon, lat]);
      return;
    }
    const start = trim(previousLon, previousLat), end = trim(nextLon, nextLat);
    for (let step = 0; step <= 6; step++) {
      const t = step / 6;
      const a = (1 - t) ** 2 * start, b = t ** 2 * end;
      path.push([wrapLongitude(lon + a * previousLon + b * nextLon), lat + a * previousLat + b * nextLat]);
    }
  });
  const first = path[0];
  if (closed && first !== undefined) path.push(first);
  return path;
}
