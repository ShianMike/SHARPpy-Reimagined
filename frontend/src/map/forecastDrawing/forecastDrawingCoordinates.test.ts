import { expect, it } from "vitest";
import { forecastPreviewPath, forecastRingContains, translateForecastRing } from "./forecastDrawingCoordinates";

it("selects the short side of a ring across the antimeridian", () => {
  const ring = [[179, 30], [-178, 30], [-178, 33], [179, 33]] as const;
  expect(forecastRingContains(ring, [179.5, 31])).toBe(true);
  expect(forecastRingContains(ring, [-179.5, 31])).toBe(true);
  expect(forecastRingContains(ring, [0, 31])).toBe(false);
});

it("applies a common displacement across the antimeridian and at the pole", () => {
  const ring = [[179, 83], [-178, 83], [-178, 87], [179, 87]] as const;
  const moved = translateForecastRing(ring, [179, 85], [-177, 89]);
  expect(moved).toEqual([[-177, 86], [-174, 86], [-174, 90], [-177, 90]]);
  expect(translateForecastRing(ring, [179, 85], [179, 85])).toBe(ring);
});

it("rounds the preview corners without closing an unfinished sketch", () => {
  const points = [[-99, 35], [-97, 35], [-97, 36]] as const;
  const sketch = forecastPreviewPath(points, false);
  expect(sketch[0]).toEqual(points[0]);
  expect(sketch.at(-1)).toEqual(points[2]);
  expect(sketch).not.toContainEqual(points[1]);
  expect(sketch.length).toBeGreaterThan(points.length);
  const closed = forecastPreviewPath(points, true);
  expect(closed.at(-1)).toEqual(closed[0]);
  expect(closed).not.toContainEqual(points[0]);
});
