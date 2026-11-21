import { FORECAST_LEVELS, MAX_FORECAST_VERTICES, type ForecastDrawingResult, type ForecastPoint, type ForecastProduct } from "../../types/forecastDrawing";
import { at, ContractError, expectArray, expectExactKeys, expectFiniteNumber, expectObject, expectOneOf, expectString } from "./guards";
import { parseOutlookArea } from "./map";

export function parseForecastVertices(value: unknown, path: string): readonly ForecastPoint[] {
  if (!Array.isArray(value) || value.length < 3 || value.length > MAX_FORECAST_VERTICES) {
    throw new ContractError(path, "expected 3 to 256 map vertices");
  }
  return expectArray(value, path, (point, pointPath): ForecastPoint => {
    if (!Array.isArray(point) || point.length !== 2) throw new ContractError(pointPath, "expected a map vertex");
    return [
      expectFiniteNumber(point[0], at(pointPath, 0), { min: -180, max: 180 }),
      expectFiniteNumber(point[1], at(pointPath, 1), { min: -90, max: 90 }),
    ];
  });
}

export function parseForecastDrawing(value: unknown, product: ForecastProduct, path = ""): ForecastDrawingResult {
  const object = expectObject(value, path);
  expectExactKeys(object, path, ["areas", "clippedHatches", "hiddenHatches", "generatedContours"]);
  return {
    areas: expectArray(object.areas, at(path, "areas"), parseOutlookArea),
    clippedHatches: expectArray(object.clippedHatches, at(path, "clippedHatches"), expectString),
    hiddenHatches: expectArray(object.hiddenHatches, at(path, "hiddenHatches"), expectString),
    generatedContours: expectArray(object.generatedContours, at(path, "generatedContours"), (value, contourPath) => {
      const contour = expectObject(value, contourPath);
      expectExactKeys(contour, contourPath, ["sourceId", "level", "points"]);
      return {
        sourceId: expectString(contour.sourceId, at(contourPath, "sourceId"), { minLength: 1, maxLength: 64 }),
        level: expectOneOf(contour.level, at(contourPath, "level"), FORECAST_LEVELS[product]),
        points: parseForecastVertices(contour.points, at(contourPath, "points")),
      };
    }),
  };
}
