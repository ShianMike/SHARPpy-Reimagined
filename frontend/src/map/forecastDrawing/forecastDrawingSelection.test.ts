import { expect, it } from "vitest";
import type { ForecastDrawingResult, ForecastPoint } from "../../types/forecastDrawing";
import type { OutlookArea } from "../../types/map";
import { forecastEditTargetAt } from "./forecastDrawingSelection";

const box = (radius: number, center = 0): readonly ForecastPoint[] => [
  [center - radius, -radius], [center + radius, -radius],
  [center + radius, radius], [center - radius, radius],
];

const area = (rank: number, rings: OutlookArea["rings"], label = ""): OutlookArea => ({
  label, rank, rings, description: "", fill: "#EFBBEF", stroke: "#AA00AA", hatch: false, hatchLevel: 0,
});

it("selects each visible generated band below a hand-drawn High Risk", () => {
  const high = { id: "high", level: 5, hatch: false, points: box(1) };
  const generatedContours = Array.from({ length: 5 }, (_, level) => ({
    level, sourceId: high.id, points: box(6 - level),
  }));
  const result = {
    areas: [...generatedContours.map((contour) => area(contour.level, [contour.points])), area(high.level, [high.points])],
    generatedContours, clippedHatches: [], hiddenHatches: [],
  };
  expect(forecastEditTargetAt([0, 0], "cat", [high], result)).toBe(high.id);
  for (const contour of generatedContours) {
    expect(forecastEditTargetAt([5.5 - contour.level, 0], "cat", [high], result)).toBe(contour);
  }
  expect(forecastEditTargetAt([6.5, 0], "cat", [high], result)).toBeUndefined();
});

it("selects the visible hatch or probability band at the clicked location", () => {
  const tornado = { id: "tornado", hatch: false, level: 10, points: box(1) };
  const hatch = { id: "hatch", hatch: true, level: 2, points: box(0.5) };
  const generated = { sourceId: tornado.id, level: 5, points: box(2) };
  const result = {
    areas: [area(50, [box(2)], "5%"), area(100, [box(1)], "10%"), {
      ...area(10_002, [box(0.5)], "CIG2"), hatch: true, hatchLevel: 2,
    }],
    generatedContours: [generated], clippedHatches: [], hiddenHatches: [],
  };
  expect(forecastEditTargetAt([0.25, 0], "torn", [tornado, hatch], result)).toBe(hatch.id);
  expect(forecastEditTargetAt([0.75, 0], "torn", [tornado, hatch], result)).toBe(tornado.id);
  expect(forecastEditTargetAt([1.5, 0], "torn", [tornado, hatch], result)).toBe(generated);
});

it("finds the local editable contour when its control ring has a simplified outline", () => {
  const west = { sourceId: "west", level: 0, points: box(1.9, -5) };
  const east = { sourceId: "east", level: 0, points: box(1.9, 5) };
  const result: ForecastDrawingResult = {
    areas: [area(0, [box(2, -5)]), area(0, [box(2, 5)])],
    generatedContours: [west, east], clippedHatches: [], hiddenHatches: [],
  };
  expect(forecastEditTargetAt([-6.99, 0], "cat", [], result)).toBe(west);
  expect(forecastEditTargetAt([6.99, 0], "cat", [], result)).toBe(east);
});
