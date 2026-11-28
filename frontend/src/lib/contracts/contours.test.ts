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
    expect(lines.paths).toEqual(contours.paths);
    expect(lines.paths?.length).toBeGreaterThan(0);
    expect(lines.labels).toEqual(contours.labels);
    expect(lines.wind?.data).toBeInstanceOf(Uint16Array);
    expect(lines.wind?.data.length).toBe(contours.wind.width * contours.wind.height);
    expect(lines.product).toBe("hgt-wind-500");
    expect(lines.interval).toBe(6);
  });

  it("accepts sparse numeric labels without changing legacy frames", () => {
    const labels = [{ position: [-99.5, 35.5], value: 1012 }];
    expect(parseContourLines({ ...contours, product: "mslp", unit: "hPa", interval: 4, labels }).labels).toEqual(labels);
    const segments = [[[-100, 35], [-99, 36]]];
    const legacy = edit(contours, (copy) => {
      delete copy.labels;
      delete copy.paths;
      copy.segments = segments;
    });
    const lines = parseContourLines(legacy);
    expect(lines.segments).toEqual(segments);
    expect(lines.paths).toBeUndefined();
    expect(lines.labels).toBeUndefined();
  });

  it("accepts distributed pressure anchors within the pressure-only label limit", () => {
    const frame = { ...contours, product: "mslp", unit: "hPa", interval: 4, segments: [], wind: null };
    const labels = Array.from({ length: 2_048 }, () => ({ position: [-99.5, 35.5], value: 1012 }));
    expect(parseContourLines({ ...frame, labels }).labels).toHaveLength(2_048);
    expect(() => parseContourLines({ ...frame, labels: [...labels, labels[0]] })).toThrow(ContractError);
  });

  it("accepts connected paths and rejects incomplete, off-globe or oversized paths", () => {
    const frame = { ...contours, product: "mslp", unit: "hPa", interval: 4, segments: [], wind: null, labels: undefined, paths: undefined };
    const paths = [[[-100, 35], [-99.5, 35.25], [-99, 36]]];
    expect(parseContourLines({ ...frame, paths }).paths).toEqual(paths);
    expect(parseContourLines(frame).paths).toBeUndefined();
    for (const invalid of [[[[-100, 35]]], [[[-100, 35], [-99.5, 95]]], [Array.from({ length: 200_001 }, () => [-100, 35])]]) {
      expect(() => parseContourLines({ ...frame, paths: invalid })).toThrow(ContractError);
    }
  });

  it("accepts H and L pressure centers while legacy frames parse unchanged", () => {
    const centers = [
      { lon: -97.6, lat: 36.4, kind: "H", value: 1035 },
      { lon: -94.2, lat: 33.8, kind: "L", value: 999 },
    ];
    const labels = [{ position: [-99.5, 35.5], value: 1012 }];
    const lines = parseContourLines({ ...contours, product: "mslp", unit: "hPa", interval: 4, labels, centers });
    expect(lines.centers).toEqual(centers);
    const legacy = parseContourLines(contours);
    expect(legacy.centers).toBeUndefined();
    expect(legacy.thickness).toBeUndefined();
  });

  it("accepts the 1000-500 mb thickness level set with joined paths", () => {
    const thickness = {
      unit: "dam",
      interval: 6,
      paths: [
        { value: 522, points: [[-100, 34], [-99, 34.5], [-98, 35]] },
        { value: 546, points: [[-96, 37], [-95, 37.5]] },
      ],
      labels: [{ position: [-99.5, 34.5], value: 522 }, { position: [-95.5, 37.5], value: 546 }],
    };
    const lines = parseContourLines({ ...contours, unit: "dam", thickness });
    expect(lines.thickness).toEqual(thickness);
  });

  it.each([
    { name: "an unknown center kind", change: (copy: Record<string, unknown>) => { copy.centers = [{ lon: -97, lat: 36, kind: "X", value: 1012 }]; } },
    { name: "an off-globe center", change: (copy: Record<string, unknown>) => { copy.centers = [{ lon: 200, lat: 36, kind: "H", value: 1012 }]; } },
    { name: "a fractional center pressure", change: (copy: Record<string, unknown>) => { copy.centers = [{ lon: -97, lat: 36, kind: "H", value: 1012.5 }]; } },
    { name: "an extra center key", change: (copy: Record<string, unknown>) => { copy.centers = [{ lon: -97, lat: 36, kind: "L", value: 1012, name: "low" }]; } },
    {
      name: "too many centers",
      change: (copy: Record<string, unknown>) => {
        copy.centers = Array.from({ length: 65 }, () => ({ lon: -97, lat: 36, kind: "H", value: 1012 }));
      },
    },
    { name: "a zero thickness interval", change: (copy: Record<string, unknown>) => { copy.thickness = { unit: "dam", interval: 0, paths: [], labels: [] }; } },
    {
      name: "a thickness label off the level set",
      change: (copy: Record<string, unknown>) => {
        copy.thickness = { unit: "dam", interval: 6, paths: [], labels: [{ position: [-99, 35], value: 541 }] };
      },
    },
    {
      name: "a one-point thickness path",
      change: (copy: Record<string, unknown>) => {
        copy.thickness = { unit: "dam", interval: 6, paths: [{ value: 540, points: [[-99, 35]] }], labels: [] };
      },
    },
    {
      name: "an off-globe thickness point",
      change: (copy: Record<string, unknown>) => {
        copy.thickness = { unit: "dam", interval: 6, paths: [{ value: 540, points: [[-99, 95], [-98, 35]] }], labels: [] };
      },
    },
    {
      name: "an unexpected thickness key",
      change: (copy: Record<string, unknown>) => {
        copy.thickness = { unit: "dam", interval: 6, paths: [], labels: [], contours: [] };
      },
    },
    {
      name: "too many thickness labels",
      change: (copy: Record<string, unknown>) => {
        copy.thickness = { unit: "dam", interval: 6, paths: [], labels: Array.from({ length: 257 }, () => ({ position: [-99, 35], value: 540 })) };
      },
    },
  ])("rejects $name", ({ change }) => {
    expect(() => parseContourLines(edit(contours, (copy) => {
      copy.product = "mslp";
      copy.unit = "hPa";
      copy.interval = 4;
      // Keep the fixture's own labels multi-checked against the frame's interval.
      copy.labels = [{ position: [-99.5, 35.5], value: 1012 }];
      change(copy);
    }))).toThrow(ContractError);
  });

  it.each([
    { name: "off-globe point", labels: [{ position: [-99, 95], value: 1012 }] },
    { name: "extra coordinate", labels: [{ position: [-99, 35, 0], value: 1012 }] },
    { name: "non-contour level", labels: [{ position: [-99, 35], value: 1013 }] },
    { name: "infinite value", labels: [{ position: [-99, 35], value: Number.POSITIVE_INFINITY }] },
    { name: "unexpected property", labels: [{ position: [-99, 35], value: 1012, extra: true }] },
    { name: "too many labels", labels: Array.from({ length: 257 }, () => ({ position: [-99, 35], value: 1012 })) },
  ])("rejects $name", ({ labels }) => {
    expect(() => parseContourLines({ ...contours, interval: 4, labels })).toThrow(ContractError);
  });

  it.each([
    ["a forecast beyond F384", () => parseContourLines({ ...contours, forecastHour: 385 })],
    ["a segment with three points", () =>
      parseContourLines({ ...contours, segments: [[[-100, 35], [-99, 36], [-98, 36]]] })],
    ["a point off the globe", () =>
      parseContourLines({ ...contours, segments: [[[-100, 95], [-99, 36]]] })],
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
