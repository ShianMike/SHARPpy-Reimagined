import contours from "@contracts/public/map/contours.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseContourLines } from "./contours";

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

describe("contour contracts", () => {
  it.each([0, 84, 144, 360, 384])("accepts global contour and wind frames at F%s", (forecastHour) => {
    expect(parseContourLines({ ...contours, forecastHour }).forecastHour).toBe(forecastHour);
  });

  it("parses the exported fixture unchanged", () => {
    const lines = parseContourLines(contours);
    expect(lines.segments).toEqual(contours.segments);
    expect(lines.wind?.data).toBeInstanceOf(Uint16Array);
    expect(lines.wind?.data.length).toBe(contours.wind.width * contours.wind.height);
    expect(lines.product).toBe("hgt-wind-500");
    expect(lines.interval).toBe(6);
    // Each segment is exactly two [lon, lat] endpoints.
    for (const segment of lines.segments) {
      expect(segment).toHaveLength(2);
    }
  });

  it.each([
    ["a forecast beyond F384", () => parseContourLines({ ...contours, forecastHour: 385 })],
    ["a segment with three points", () =>
      parseContourLines(edit(contours, (c) => (c.segments as unknown[][][])[0]?.push([1, 2])))],
    ["a point off the globe", () =>
      parseContourLines(edit(contours, (c) => (((c.segments as unknown[][][])[0] as number[][])[0] as number[])[1] = 95))],
    ["an extra key", () => parseContourLines(edit(contours, (c) => (c.levels = 12)))],
    ["a zero interval", () => parseContourLines(edit(contours, (c) => (c.interval = 0)))],
    ["an incomplete wind grid", () => parseContourLines(edit(contours, (c) => (c.wind as Record<string, unknown>).dataBase64 = "AAAA"))],
    ["an invalid wind direction", () => parseContourLines(edit(contours, (c) => {
      c.wind = { width: 1, height: 1, bounds: { west: -100, east: -90, south: 30, north: 40 }, dataBase64: btoa(String.fromCharCode(255, 1)) };
    }))],
    ["an empty wind extent", () => parseContourLines(edit(contours, (c) => ((c.wind as { bounds: { east: number; west: number } }).bounds.east = (c.wind as { bounds: { west: number } }).bounds.west)))],
  ])("rejects %s", (_name, parse) => {
    expect(parse).toThrow(ContractError);
  });
});
