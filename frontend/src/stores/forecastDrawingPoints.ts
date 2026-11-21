import type { ForecastPoint } from "../types/forecastDrawing";

export type ForecastPoints = readonly ForecastPoint[];

export function copyForecastPoints(points: ForecastPoints): ForecastPoints {
  return points.map(([longitude, latitude]) => [longitude, latitude]);
}

export function samePoint(first: ForecastPoint, second: ForecastPoint | undefined): boolean {
  return first[0] === second?.[0] && first[1] === second[1];
}

export function samePoints(first: ForecastPoints, second: ForecastPoints): boolean {
  return first === second || (first.length === second.length && first.every((point, index) => samePoint(point, second[index])));
}

/** The same vertices can start at any point and run in either direction around the outline. */
export function sameOutline(first: ForecastPoints, second: ForecastPoints): boolean {
  if (first.length !== second.length) return false;
  const start = second.findIndex((point) => samePoint(point, first[0]));
  return start !== -1 && [1, -1].some((direction) => first.every((point, index) =>
    samePoint(point, second[(start + second.length + index * direction) % second.length])));
}
