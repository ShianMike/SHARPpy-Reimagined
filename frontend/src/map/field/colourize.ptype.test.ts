import catalog from "@contracts/public/map/catalog.json";
import { describe, expect, it } from "vitest";
import { parseMapCatalog } from "../../lib/contracts/map";
import type { FieldFrame, Palette } from "../../types/map";
import { cellColours, colourize, decodePtypeValue, isPtypePalette, paletteRange, ptypeGroupStride, ptypePaletteGroups, ptypePaletteTicks } from "./colourize";

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

const rgba = (pixels: Uint8ClampedArray | Uint8Array, index: number): number[] => Array.from(pixels.slice(index * 4, index * 4 + 4));

describe("precipitation-type ramps at both encoded group strides", () => {
  // HRRR composite reflectivity by dominant precipitation type: four 17-band
  // groups packed 100 dBZ apart (steps of 5 within 0-80 dBZ per group), the
  // catalogue shape refd-1km-ptype publishes.
  const REFD_PTYPE_AMOUNTS = [0.01, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80];
  const refdPtypePalette = (): Palette => ({
    display: "auto",
    stops: [0, 100, 200, 300].flatMap((offset, group) =>
      REFD_PTYPE_AMOUNTS.map((amount, band) => [Number((offset + amount).toFixed(2)), `#${String(group * 40 + band + 1).padStart(6, "0")}`] as const)),
    units: "dBZ",
    stepped: true,
    floor: 0.01,
    ceiling: null,
    ticks: [10, 20, 30, 40, 50],
    ptypeLabels: ["Rain", "Snow", "Freezing rain", "Mix"],
  });
  // The rate and QPF ramps the original catalogues publish: 10 units between groups.
  const ratePtypePalette = (): Palette => ({
    display: "auto",
    stops: [0, 10, 20, 30].flatMap((offset, group) =>
      [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.1, 0.12, 0.15, 0.17, 0.2, 0.25, 0.3, 0.4, 0.5].map((amount, band) =>
        [Number((offset + amount).toFixed(2)), `#${String(group * 40 + band + 1).padStart(6, "0")}`] as const)),
    units: "in/h",
    stepped: true,
    floor: 0.01,
    ceiling: null,
    ticks: [0.05, 0.1, 0.2, 0.5],
  });
  const refdBands = refdPtypePalette();

  it("detects the stride from the encoded group offsets and splits each ramp into four groups", () => {
    expect(ptypeGroupStride(refdBands)).toBe(100);
    expect(ptypeGroupStride(ratePtypePalette())).toBe(10);
    expect(ptypeGroupStride(palette("refc"))).toBeNull();
    expect(isPtypePalette(refdBands)).toBe(true);
    expect(isPtypePalette(palette("refc"))).toBe(false);
    const groups = ptypePaletteGroups(refdBands) ?? [];
    expect(groups.map(({ label }) => label)).toEqual(["Rain", "Snow", "Freezing rain", "Mix"]);
    for (const group of groups) {
      expect(group.stops).toHaveLength(17);
      expect(group.stops.map(([value]) => value)).toEqual(REFD_PTYPE_AMOUNTS);
    }
    // Stride-10 groups keep their historical amounts and default labels.
    const rateGroups = ptypePaletteGroups(ratePtypePalette()) ?? [];
    expect(rateGroups.map(({ label }) => label)).toEqual(["Rain", "Snow", "Freezing rain", "Mix"]);
    expect(rateGroups[0]?.stops.map(([value]) => value)).toEqual([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.1, 0.12, 0.15, 0.17, 0.2, 0.25, 0.3, 0.4, 0.5]);
  });

  it("unwraps HRRR reflectivity offsets into readable 0-80 dBZ amounts per type", () => {
    expect(decodePtypeValue(0.0104, refdBands)).toEqual({ amount: 0.01, ptypeLabel: "Rain" });
    expect(decodePtypeValue(105.4, refdBands)).toEqual({ amount: 5.4, ptypeLabel: "Snow" });
    expect(decodePtypeValue(223.456, refdBands)).toEqual({ amount: 23.46, ptypeLabel: "Freezing rain" });
    expect(decodePtypeValue(355.9, refdBands)).toEqual({ amount: 55.9, ptypeLabel: "Mix" });
    // Above 80 dBZ inside a group the amount reads honestly, without crossing to the next type.
    expect(decodePtypeValue(95.5, refdBands)).toEqual({ amount: 95.5, ptypeLabel: "Rain" });
    expect(decodePtypeValue(10.0804, ratePtypePalette())).toEqual({ amount: 0.08, ptypeLabel: "Snow" });
    // A plain reflectivity palette never decodes as a type.
    expect(decodePtypeValue(20, palette("refc"))).toEqual({ amount: 20, ptypeLabel: null });
  });

  it("places the dBZ type ticks inside one group's 17 bands", () => {
    const stops = ptypePaletteGroups(refdBands)?.[0]?.stops;
    if (stops === undefined) throw new Error("Expected reflectivity type groups");
    expect(paletteRange(refdBands)).toEqual({ low: 0.01, high: 380 });
    expect(ptypePaletteTicks(refdBands.ticks, stops)).toEqual([
      { value: 0.01, at: 0 }, { value: 10, at: 2 / 17 }, { value: 20, at: 4 / 17 }, { value: 30, at: 6 / 17 },
      { value: 40, at: 8 / 17 }, { value: 50, at: 10 / 17 }, { value: 80, at: 16 / 17 },
    ]);
  });

  it("colours the reflectivity cells through their snow and mix bands", () => {
    const bandColour = (index: number): number[] => {
      const hex = refdBands.stops[index]?.[1];
      if (hex === undefined) throw new Error("missing stop colour");
      return [Number.parseInt(hex.slice(1, 3), 16), Number.parseInt(hex.slice(3, 5), 16), Number.parseInt(hex.slice(5, 7), 16)];
    };
    // value = cell * 1: 8 dBZ rain, 5 dBZ snow (offset 100), 45 dBZ freezing rain, 80 dBZ mix, no data.
    const pixels = colourize(frame([8, 105, 245.5, 380, 65535], 5, 1, 1, 0), refdBands);
    expect(rgba(pixels, 0)).toEqual([...bandColour(1), 255]);
    expect(rgba(pixels, 1)).toEqual([...bandColour(18), 255]);
    expect(rgba(pixels, 2)).toEqual([...bandColour(43), 255]);
    expect(rgba(pixels, 3)).toEqual([...bandColour(67), 255]);
    expect(rgba(pixels, 4)).toEqual([0, 0, 0, 0]);
    // The packed per-cell table shares the same band edges as the raster path.
    const packed = new Uint8Array(cellColours({ scale: 1, offset: 0, nodata: 65535 }, refdBands).buffer);
    expect(rgba(packed, 105)).toEqual([...bandColour(18), 255]);
    expect(rgba(packed, 110)).toEqual([...bandColour(19), 255]);
  });

  it("reads the real catalog refd-1km-ptype dBZ palette accurately without shifting stops", () => {
    const refd = palette("refd-1km-ptype");
    expect(ptypeGroupStride(refd)).toBe(100);
    expect(isPtypePalette(refd)).toBe(true);
    const groups = ptypePaletteGroups(refd) ?? [];
    expect(groups.map(({ label }) => label)).toEqual(["Rain", "Snow", "Frzr", "Sleet"]);
    expect(groups[0]?.stops[0]?.[0]).toBe(5);
    expect(groups[0]?.stops.at(-1)?.[0]).toBe(50);
    const stops = groups[0]?.stops;
    if (stops === undefined) throw new Error("Expected refd-1km-ptype stops");
    expect(ptypePaletteTicks(refd.ticks, stops).map(({ value }) => value)).toEqual([5, 10, 20, 30, 40, 50]);
    expect(decodePtypeValue(5, refd)).toEqual({ amount: 5, ptypeLabel: "Rain" });
    expect(decodePtypeValue(115, refd)).toEqual({ amount: 15, ptypeLabel: "Snow" });
    expect(decodePtypeValue(225, refd)).toEqual({ amount: 25, ptypeLabel: "Frzr" });
    expect(decodePtypeValue(340, refd)).toEqual({ amount: 40, ptypeLabel: "Sleet" });
  });
});
