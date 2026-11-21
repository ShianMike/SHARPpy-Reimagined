import type { ForecastDrawingResult, ForecastPoint, ForecastPolygon, ForecastProduct, GeneratedForecastContour } from "../../types/forecastDrawing";
import { containsPoint } from "../layers/outlookGeometry";
import { forecastRingContains, forecastRingDistance } from "./forecastDrawingCoordinates";

/** Edit the same band that the user can see, even though all the lower fills overlap. */
export function forecastEditTargetAt(
  point: ForecastPoint,
  product: ForecastProduct,
  polygons: readonly ForecastPolygon[],
  result: ForecastDrawingResult | undefined,
): string | GeneratedForecastContour | undefined {
  if (result === undefined) return;
  const visible = result.areas.findLast((area) => containsPoint(area.rings, point));
  if (visible === undefined) return;
  const level = visible.hatch ? visible.hatchLevel
    : product === "cat" ? visible.rank : Number.parseInt(visible.label, 10);
  const authored = polygons.filter((polygon) => polygon.hatch === visible.hatch && polygon.level === level);
  const direct = authored.findLast((polygon) => forecastRingContains(polygon.points, point));
  if (direct !== undefined) return direct.id;
  const generated = visible.hatch ? [] : result.generatedContours.filter((contour) => contour.level === level);
  const derived = generated.findLast((contour) => forecastRingContains(contour.points, point));
  if (derived !== undefined) return derived;

  // Rounding and nesting can add coverage outside any editable ring at this level.
  let closest: string | GeneratedForecastContour | undefined;
  let nearest = Number.POSITIVE_INFINITY;
  for (const contour of [...authored, ...generated]) {
    const distance = forecastRingDistance(contour.points, point);
    if (distance < nearest) {
      closest = "id" in contour ? contour.id : contour;
      nearest = distance;
    }
  }
  return closest;
}
