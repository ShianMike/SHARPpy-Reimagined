// Closed outlook paths and interior label candidates. Polygons are even-odd,
// so labels stay out of holes as well as outside concave edges.

export type LonLat = readonly [number, number];
export type Ring = readonly LonLat[];

/**
 * Where the line s(p) = 0 crosses the rings, as positions t(p) along it, sorted;
 * between the first and second crossing is inside, and so on (even-odd). s and
 * t are linear in lon and lat, so interpolating them along an edge is exact.
 */
function crossings(rings: readonly Ring[], s: (p: LonLat) => number, t: (p: LonLat) => number): number[] {
  const out: number[] = [];
  for (const ring of rings) {
    ring.forEach((a, i) => {
      const b = ring[(i + 1) % ring.length] ?? a;
      const sa = s(a);
      const sb = s(b);
      if (sa > 0 !== sb > 0) {
        out.push(t(a) + ((t(b) - t(a)) * sa) / (sa - sb));
      }
    });
  }
  return out.sort((x, y) => x - y);
}

function insideSpans(points: readonly number[]): [number, number][] {
  const spans: [number, number][] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    spans.push([points[i] ?? 0, points[i + 1] ?? 0]);
  }
  return spans;
}

interface Box {
  west: number;
  east: number;
  south: number;
  north: number;
}

function boxOf(ring: Ring): Box {
  const lons = ring.map((p) => p[0]);
  const lats = ring.map((p) => p[1]);
  return { west: Math.min(...lons), east: Math.max(...lons), south: Math.min(...lats), north: Math.max(...lats) };
}

/** Whether a point is inside the area (even-odd, so a hole is outside). */
export function containsPoint(rings: readonly Ring[], point: LonLat): boolean {
  const [lon, lat] = point;
  return crossings(rings, (p) => p[1] - lat, (p) => p[0]).filter((x) => x < lon).length % 2 === 1;
}

/** Each ring as a closed path, for an outline PathLayer. */
export function closedPaths(rings: readonly Ring[]): LonLat[][] {
  return rings.filter((ring) => ring.length > 1).map((ring) => [...ring, ring[0] ?? [0, 0]]);
}

/**
 * A point inside the area for its label: the middle of the widest inside span
 * of a horizontal line through its box, which is inside even
 * for a concave area or one with holes. The first vertex if the area is degenerate.
 */
export function labelPoint(rings: readonly Ring[], fraction = 0.5): LonLat {
  const outer = rings[0] ?? [];
  if (outer.length < 3) {
    return outer[0] ?? [0, 0];
  }
  const box = boxOf(outer);
  const lat = box.south + (box.north - box.south) * fraction;
  const spans = insideSpans(crossings(rings, (p) => p[1] - lat, (p) => p[0]));
  const widest = spans.reduce<[number, number] | null>((best, span) =>
    best === null || span[1] - span[0] > best[1] - best[0] ? span : best, null);
  return widest === null ? (outer[0] ?? [0, 0]) : [(widest[0] + widest[1]) / 2, lat];
}
