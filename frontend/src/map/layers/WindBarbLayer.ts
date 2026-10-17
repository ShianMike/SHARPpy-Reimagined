import { IconLayer } from "@deck.gl/layers";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { WindBarbGrid } from "../../types/map";
import { WIND_BARB_ICONS } from "./windBarbIcons";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";

export const WIND_BARBS_LAYER_ID = "model-wind-barbs";
export interface WindBarb {
  position: [number, number];
  direction: number;
  speed: number;
}
/** Visit only visible stride-aligned cells; powers of two keep barbs anchored during panning. */
export function selectWindBarbs(grid: WindBarbGrid, map: Pick<MapLibreMap, "getBounds" | "getCanvas" | "project">, spacing = 64): WindBarb[] {
  const bounds = map.getBounds();
  const { width, height, data } = grid;
  const { west, east, south, north } = grid.bounds;
  const dx = (east - west) / width, dy = (north - south) / height;
  const left = Math.max(0, Math.ceil((bounds.getWest() - west) / dx - 0.5));
  const right = Math.min(width - 1, Math.floor((bounds.getEast() - west) / dx - 0.5));
  const top = Math.max(0, Math.ceil((north - bounds.getNorth()) / dy - 0.5));
  const bottom = Math.min(height - 1, Math.floor((north - bounds.getSouth()) / dy - 0.5));
  if (left > right || top > bottom) return [];
  // Sample inside the visible grid; its western edge can be behind a globe's horizon.
  const lon = west + (left + right + 1) * dx / 2, lat = north - (top + bottom + 1) * dy / 2;
  const a = map.project([lon, lat]), b = map.project([lon + dx, lat]), c = map.project([lon, lat + dy]);
  const xStep = 2 ** Math.max(0, Math.ceil(Math.log2(spacing / Math.hypot(b.x - a.x, b.y - a.y))));
  const yStep = 2 ** Math.max(0, Math.ceil(Math.log2(spacing / Math.hypot(c.x - a.x, c.y - a.y))));
  const canvas = map.getCanvas();
  const selected: WindBarb[] = [];
  for (let row = Math.ceil(top / yStep) * yStep; row <= bottom; row += yStep) {
    for (let col = Math.ceil(left / xStep) * xStep; col <= right; col += xStep) {
      const cell = data[row * width + col];
      if (cell === undefined || cell === 65535) continue;
      const position: [number, number] = [west + (col + 0.5) * dx, north - (row + 0.5) * dy];
      const point = map.project(position);
      if (point.x < 0 || point.y < 0 || point.x >= canvas.clientWidth || point.y >= canvas.clientHeight) continue;
      selected.push({ position, direction: cell & 511, speed: (cell >> 9) * 5 });
    }
  }
  return selected;
}

export function buildWindBarbLayers(ctx: LayerBuildContext, state: LayerState): IconLayer<WindBarb>[] {
  const barbs = ctx.windBarbs ?? [];
  if (barbs.length === 0) return [];
  return [true, false].map((halo) => new IconLayer<WindBarb>({
    ...WIND_BARB_ICONS,
    id: `${WIND_BARBS_LAYER_ID}${halo ? "-halo" : ""}`,
    data: barbs,
    getPosition: (barb) => barb.position,
    getIcon: (barb) => `${String(barb.speed)}${halo ? "-halo" : ""}`,
    getAngle: (barb) => 90 - barb.direction,
    getColor: deckColour(halo ? ctx.tokens.colours.labelHalo : ctx.tokens.colours.labelText),
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  }));
}
