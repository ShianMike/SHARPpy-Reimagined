import catalog from "@contracts/public/map/catalog.json";
import { describe, expect, it } from "vitest";
import { parseMapCatalog } from "../../lib/contracts/map";
import type { FieldFrame, Palette } from "../../types/map";
import { buildLut, colourize, drawnRangeNote, formatValue, LUT_SIZE, sampleAt } from "./colourize";

const products = parseMapCatalog(catalog).products;
const palette = (key: string): Palette => {
  const product = products.find((item) => item.key === key);
  if (product === undefined) {
    throw new Error(`no ${key} in the catalogue fixture`);
  }
  return product.palette;
};

function frame(cells: number[], width = cells.length, height = 1, scale = 0.5, offset = 4): FieldFrame {
  return {
    product: "refc",
    runTime: "2026-10-04T18:00:00Z",
    forecastHour: 1,
    validTime: "2026-10-04T19:00:00Z",
    width,
    height,
    bounds: { west: -100, east: -90, south: 30, north: 40 },
    units: "dBZ",
    scale,
    offset,
    nodata: 65535,
    valueMin: 0,
    valueMax: 1,
    sourceUrl: "",
    attribution: "",
    cells: Uint16Array.from(cells),
  };
}

const rgba = (pixels: Uint8ClampedArray, index: number): number[] => Array.from(pixels.slice(index * 4, index * 4 + 4));

describe("field colouring", () => {
  it("builds the legacy lookup table (hrrr_field_images.build_lut, refc)", () => {
    const lut = buildLut(palette("refc"));
    expect(lut).toHaveLength(LUT_SIZE * 4);
    // Read from sharpmod with the same indices.
    const expected: Record<number, number[]> = {
      0: [255, 255, 255], 1: [255, 255, 255], 100: [177, 197, 230], 511: [246, 211, 90],
      512: [237, 179, 68], 777: [118, 3, 10], 1022: [230, 230, 230], 1023: [230, 230, 230],
    };
    for (const [index, colour] of Object.entries(expected)) {
      expect(Array.from(lut.slice(Number(index) * 4, Number(index) * 4 + 3)), `entry ${index}`).toEqual(colour);
    }
  });

  it("colours like hrrr_field_images.colourize: hidden below the floor and with no data, clipped above", () => {
    // value = cell * 0.5 + 4: 4 (below the floor of 7.5), 7.5, 40, 72.5, 80, no data.
    const pixels = colourize(frame([0, 7, 72, 137, 152, 65535]), palette("refc"));
    expect(rgba(pixels, 0)[3]).toBe(0);
    expect(rgba(pixels, 1)).toEqual([255, 255, 255, 255]);
    expect(rgba(pixels, 2)).toEqual([237, 179, 68, 255]);
    expect(rgba(pixels, 3)).toEqual([230, 230, 230, 255]);
    expect(rgba(pixels, 4)).toEqual([230, 230, 230, 255]);
    expect(rgba(pixels, 5)).toEqual([0, 0, 0, 0]);
  });

  it("interpolates a continuous palette and hides values above a ceiling", () => {
    const ramp: Palette = { stops: [[0, "#000000"], [20, "#ff0000"]], units: "", stepped: false, floor: null, ceiling: 15, ticks: [] };
    // Values 0, 5, 10, and 16; table entries 0, 255, and 511 hold 0, 4.985, and 9.990.
    const pixels = colourize(frame([0, 10, 20, 32], 4, 1, 0.5, 0), ramp);
    expect(rgba(pixels, 0)).toEqual([0, 0, 0, 255]);
    expect(rgba(pixels, 1)).toEqual([63, 0, 0, 255]);
    expect(rgba(pixels, 2)).toEqual([127, 0, 0, 255]);
    expect(rgba(pixels, 3)[3]).toBe(0);
  });

  it("preserves narrow lightning classes and the exact reflectivity boundary", () => {
    const pixels = colourize(frame([0, 9, 15, 55, 100, 299, 300, 15000, 19280, 65535], 10, 1, 0.001, 0), palette("ltng"));
    expect(rgba(pixels, 0)[3]).toBe(0);
    expect(rgba(pixels, 1)[3]).toBe(0);
    expect(rgba(pixels, 2)).toEqual([240, 240, 240, 255]);
    expect(rgba(pixels, 3)).toEqual([165, 165, 165, 255]);
    expect(rgba(pixels, 4)).toEqual([55, 83, 106, 255]);
    expect(rgba(pixels, 5)).toEqual([167, 200, 206, 255]);
    expect(rgba(pixels, 6)).toEqual([233, 221, 150, 255]);
    expect(rgba(pixels, 7)).toEqual([137, 61, 72, 255]);
    expect(rgba(pixels, 8)).toEqual([137, 61, 72, 255]);
    expect(rgba(pixels, 9)[3]).toBe(0);
    const boundary = colourize(frame([39999, 40000], 2, 1, 0.001, 0), palette("refc"));
    expect(rgba(boundary, 0)).toEqual([246, 211, 90, 255]);
    expect(rgba(boundary, 1)).toEqual([237, 179, 68, 255]);
  });

  it("samples the cell under a point, row 0 at the north edge", () => {
    // 2 by 2 over 10 degrees: north-west, north-east, south-west, south-east.
    const grid = frame([10, 20, 30, 65535], 2, 2, 1, 0);
    expect(sampleAt(grid, -99, 39)).toBe(10);
    expect(sampleAt(grid, -91, 39)).toBe(20);
    expect(sampleAt(grid, -99, 31)).toBe(30);
    expect(sampleAt(grid, -91, 31)).toBeNull();
    expect(sampleAt(grid, -90, 30)).toBeNull();
    expect(sampleAt(grid, -101, 35)).toBeNull();
    expect(sampleAt(grid, -100, 40)).toBe(10);
  });

  it("explains values the map leaves uncoloured, and formats readouts", () => {
    expect(drawnRangeNote(4, palette("refc"))).toBe("");
    expect(drawnRangeNote(40, palette("refc"))).toBe("");
    expect(drawnRangeNote(16, { ...palette("refc"), ceiling: 15 })).toBe("above the drawn range");
    expect([formatValue(1234.5), formatValue(42.25), formatValue(0.257), formatValue(-3.14)]).toEqual(["1235", "42.3", "0.26", "-3.1"]);
    // Quantization can put a zero a hair below it: no "-0.00".
    expect(formatValue(-0.0003)).toBe("0.00");
    expect(formatValue(Number.NaN)).toBe("\u2014");
    expect(formatValue(Number.POSITIVE_INFINITY)).toBe("\u2014");
  });
});
