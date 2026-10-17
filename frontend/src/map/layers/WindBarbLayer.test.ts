import { describe, expect, it, vi } from "vitest";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { WindBarbGrid } from "../../types/map";
import { selectWindBarbs } from "./WindBarbLayer";
import { windBarbPaths, WIND_BARB_ICONS } from "./windBarbIcons";

const grid: WindBarbGrid = {
  width: 256, height: 128,
  bounds: { west: -130, east: -60, south: 20, north: 55 },
  data: new Uint16Array(256 * 128).fill((13 << 9) | 270), // 65 kt from west
};

function view(scale: number, center = [-95, 37.5], width = 1000, height = 600) {
  const west = (center[0] ?? 0) - width / scale / 2, east = west + width / scale;
  const south = (center[1] ?? 0) - height / scale / 2, north = south + height / scale;
  return {
    getBounds: () => ({ getWest: () => west, getEast: () => east, getNorth: () => north, getSouth: () => south }),
    getCanvas: () => ({ clientWidth: width, clientHeight: height }),
    project: vi.fn(([lon, lat]: number[]) => ({ x: ((lon ?? 0) - west) * scale, y: (north - (lat ?? 0)) * scale })),
  } as unknown as Pick<MapLibreMap, "getBounds" | "getCanvas" | "project">;
}

describe("zoom-aware model wind barbs", () => {
  it("draws actual 5/10 kt feathers and filled 50 kt flags, including multiple flags", () => {
    for (const [speed, flags, full, half] of [[5, 0, 0, 1], [10, 0, 1, 0], [65, 1, 1, 1], [85, 1, 3, 1], [100, 2, 0, 0], [150, 3, 0, 0], [635, 12, 3, 1]]) {
      const shape = windBarbPaths(speed ?? 0);
      expect(shape.flags.match(/Z/g) ?? []).toHaveLength(flags ?? 0);
      expect(shape.lines.match(/L\d+ 10/g) ?? []).toHaveLength(full ?? 0);
      expect(shape.lines.match(/L\d+ 5(?=M|$)/g) ?? []).toHaveLength(half ?? 0);
      expect([...shape.lines.matchAll(/M(\d+) 0L\d+ (?:5|10)/g)].every((match) => Number(match[1]) > 0)).toBe(true);
      expect(WIND_BARB_ICONS.iconMapping[String(speed)]).toBeDefined();
    }
    expect(windBarbPaths(0).lines).toContain("A4 4");
    // A lone half-feather sits behind the tip so it cannot read as a full feather.
    expect(windBarbPaths(5).lines).toBe("M0 0L24 0M20 0L20 5");
    expect(windBarbPaths(12).lines).toBe(windBarbPaths(10).lines);
    expect(windBarbPaths(13).lines).toBe(windBarbPaths(15).lines);
  });

  it("reveals finer cells when zoomed while keeping fixed screen spacing and a bounded pass", () => {
    const wideMap = view(12), zoomMap = view(50);
    const wide = selectWindBarbs(grid, wideMap), zoom = selectWindBarbs(grid, zoomMap);
    expect(wide.length).toBeGreaterThan(20);
    const colSpacing = (barbs: typeof wide) => Math.min(...barbs.filter((b) => b.position[1] === barbs[0]?.position[1])
      .slice(1).map((b, i) => b.position[0] - (barbs[i]?.position[0] ?? 0)));
    expect(colSpacing(zoom)).toBeLessThan(colSpacing(wide));
    for (const [map, barbs] of [[wideMap, wide], [zoomMap, zoom]] as const) {
      expect(barbs.length).toBeLessThan(1000 * 600 / (64 * 64));
      expect(map.project).toHaveBeenCalledTimes(barbs.length + 3);
      expect(barbs.every((b) => b.speed === 65 && b.direction === 270)).toBe(true);
    }
  });

  it("keeps overlapping cells anchored when panned and thins by each panel's viewport", () => {
    const original = selectWindBarbs(grid, view(24)), panned = selectWindBarbs(grid, view(24, [-94, 37.5]));
    const positions = new Set(original.map((b) => b.position.join()));
    expect(panned.filter((b) => positions.has(b.position.join())).length).toBeGreaterThan(original.length * 0.8);
    const quarter = selectWindBarbs(grid, view(24, [-95, 37.5], 500, 300));
    expect(quarter.length).toBeLessThan(original.length * 0.35);
  });

  it("measures spacing inside the visible grid when the western edge is behind the globe", () => {
    const map = view(24);
    const project = map.project.bind(map);
    map.project = vi.fn((position: Parameters<MapLibreMap["project"]>[0]) => {
      const point = project(position);
      return point.x < 0 ? { x: NaN, y: NaN } as ReturnType<typeof project> : point;
    });
    expect(selectWindBarbs(grid, map).length).toBeGreaterThan(20);
  });

  it("retains calm and unshaded winds, skips missing cells, and does no work off-domain", () => {
    const low = { ...grid, data: new Uint16Array(grid.data.length).fill((3 << 9) | 180) };
    expect(selectWindBarbs(low, view(24)).every((b) => b.speed === 15)).toBe(true);
    expect(selectWindBarbs({ ...low, data: new Uint16Array(grid.data.length) }, view(24)).every((b) => b.speed === 0)).toBe(true);
    expect(selectWindBarbs({ ...low, data: new Uint16Array(grid.data.length).fill(65535) }, view(24))).toEqual([]);
    const offDomain = view(24, [20, 40]);
    expect(selectWindBarbs(grid, offDomain)).toEqual([]);
    expect(offDomain.project).not.toHaveBeenCalled();
  });
});
