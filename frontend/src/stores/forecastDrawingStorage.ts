import { parseForecastVertices } from "../lib/contracts/forecastDrawing";
import { at, ContractError, expectArray, expectBoolean, expectObject, expectOneOf, expectString } from "../lib/contracts/guards";
import { FORECAST_HATCH_LEVELS, FORECAST_LEVELS, MAX_FORECAST_POLYGONS, type ForecastPolygon, type ForecastProduct } from "../types/forecastDrawing";

const storageKey = (product: ForecastProduct): string => `sharppy-forecast-drawing-v1-${product}`;

export function readForecastDrawing(product: ForecastProduct): readonly ForecastPolygon[] {
  try {
    const text = localStorage.getItem(storageKey(product));
    if (text === null) return [];
    const saved: unknown = JSON.parse(text);
    const ids = new Set<string>();
    const polygons = expectArray(saved, "", (value, path) => {
      const item = expectObject(value, path);
      const id = expectString(item.id, at(path, "id"), { minLength: 1, maxLength: 64 });
      if (ids.has(id)) throw new ContractError(at(path, "id"), "duplicate drawing ID");
      ids.add(id);
      const hatch = expectBoolean(item.hatch, at(path, "hatch"));
      const levels = hatch ? FORECAST_HATCH_LEVELS[product] : FORECAST_LEVELS[product];
      const level = expectOneOf(item.level, at(path, "level"), levels);
      const points = parseForecastVertices(item.points, at(path, "points"));
      if (item.sourceId === undefined) return { id, level, hatch, points };
      const sourceId = expectString(item.sourceId, at(path, "sourceId"), { minLength: 1, maxLength: 64 });
      return { id, level, hatch, points, sourceId };
    });
    return polygons.length > MAX_FORECAST_POLYGONS ? [] : polygons;
  } catch {
    return [];
  }
}

export function writeForecastDrawing(product: ForecastProduct, polygons: readonly ForecastPolygon[]): boolean {
  try {
    localStorage.setItem(storageKey(product), JSON.stringify(polygons));
    return true;
  } catch {
    return false;
  }
}
