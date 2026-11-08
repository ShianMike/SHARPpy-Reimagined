import type { Bounds } from "../../types/soundings";

export type Position = readonly [longitude: number, latitude: number];
export const wrapLongitude = (longitude: number): number => ((longitude + 180) % 360 + 360) % 360 - 180;
export const boxWidth = (box: Bounds): number => box.east > box.west ? box.east - box.west : 360 + box.east - box.west;
export const boxCentre = (box: Bounds): Position => [wrapLongitude(box.west + boxWidth(box) / 2), (box.south + box.north) / 2];
export function boxDimensions(box: Bounds): readonly [number, number] {
  const radians = Math.PI / 180;
  return [6371.0088 * boxWidth(box) * radians * Math.cos((box.south + box.north) / 2 * radians),
    6371.0088 * (box.north - box.south) * radians];
}

/** The shorter longitude arc lets a gesture cross the antimeridian. */
export function boxBetween(a: Position, b: Position): Bounds | null {
  const span = wrapLongitude(b[0] - a[0]);
  const south = Math.max(-89.99, Math.min(a[1], b[1]));
  const north = Math.min(89.99, Math.max(a[1], b[1]));
  if (Math.abs(span) < 1e-7 || north - south < 1e-7) return null;
  return { west: wrapLongitude(span > 0 ? a[0] : b[0]), east: wrapLongitude(span > 0 ? b[0] : a[0]), south, north };
}

export function boxContains(box: Bounds, point: Position): boolean {
  const delta = ((point[0] - box.west) % 360 + 360) % 360;
  return point[1] >= box.south && point[1] <= box.north && delta <= boxWidth(box);
}

export function moveBox(box: Bounds, a: Position, b: Position): Bounds {
  const dy = Math.max(-89.99 - box.south, Math.min(89.99 - box.north, b[1] - a[1]));
  const dx = wrapLongitude(b[0] - a[0]);
  return { west: wrapLongitude(box.west + dx), east: wrapLongitude(box.east + dx), south: box.south + dy, north: box.north + dy };
}

/** Keep the opposite corner fixed and prevent the dragged handle flipping roles. */
export function resizeBox(anchor: Position, corner: number, point: Position): Bounds | null {
  const west = corner === 0 || corner === 3;
  const south = corner === 0 || corner === 1;
  const delta = wrapLongitude(point[0] - anchor[0]);
  const longitude = anchor[0] + (west ? Math.min(-1e-6, delta) : Math.max(1e-6, delta));
  const latitude = south ? Math.min(anchor[1] - 1e-6, point[1]) : Math.max(anchor[1] + 1e-6, point[1]);
  return boxBetween(anchor, [longitude, latitude]);
}

/** Densify geographic parallels so Mercator and globe show the same region. */
export function boxRing(box: Bounds): number[][] {
  const corners: Position[] = [[box.west, box.south], [box.west + boxWidth(box), box.south],
    [box.west + boxWidth(box), box.north], [box.west, box.north]];
  const ring: number[][] = [];
  corners.forEach((a, i) => {
    const b = corners[(i + 1) % 4];
    if (b === undefined) return;
    for (let step = 0; step < 32; step++) ring.push([a[0] + (b[0] - a[0]) * step / 32, a[1] + (b[1] - a[1]) * step / 32]);
  });
  const first = ring[0];
  if (first !== undefined) ring.push(first);
  return ring;
}
