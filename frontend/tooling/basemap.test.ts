import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BasemapFormatError, convertBasemap, convertBasemapText } from "./basemap.ts";
import { cspHeaderFromObject, cspHeaderFromTauriConfig } from "./csp.ts";

const repoRoot = resolve(fileURLToPath(new URL(".", import.meta.url)), "..", "..");
const basemapPath = resolve(repoRoot, "sharpmod", "resources", "basemap.json");

function validInput(): Record<string, unknown> {
  const line = [
    [-97.5, 35.2],
    [-97.4, 35.3],
  ];
  return { coastline: [line], lakes: [line], countries: [line], states: [line] };
}

function expectFormatError(input: unknown, pathFragment: string): void {
  let caught: unknown;
  try {
    convertBasemap(input);
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(BasemapFormatError);
  expect((caught as BasemapFormatError).name).toBe("BasemapFormatError");
  expect((caught as BasemapFormatError).path).toContain(pathFragment);
}

describe("convertBasemap", () => {
  it("converts the real basemap.json into four MultiLineString families", () => {
    const geo = convertBasemapText(readFileSync(basemapPath, "utf8"));
    const counts = {
      coastline: geo.coastline.features[0].geometry.coordinates.length,
      lakes: geo.lakes.features[0].geometry.coordinates.length,
      countries: geo.countries.features[0].geometry.coordinates.length,
      states: geo.states.features[0].geometry.coordinates.length,
    };
    expect(counts).toEqual({ coastline: 1429, lakes: 370, countries: 422, states: 612 });
    for (const family of ["coastline", "lakes", "countries", "states"] as const) {
      expect(geo[family].type).toBe("FeatureCollection");
      expect(geo[family].features).toHaveLength(1);
      expect(geo[family].features[0].geometry.type).toBe("MultiLineString");
      expect(geo[family].features[0].properties.family).toBe(family);
    }
  });

  it("accepts a minimal valid input and copies the coordinates", () => {
    const geo = convertBasemap(validInput());
    expect(geo.states.features[0].geometry.coordinates).toEqual([
      [
        [-97.5, 35.2],
        [-97.4, 35.3],
      ],
    ]);
  });

  it("rejects malformed input with a named error", () => {
    expectFormatError(null, "(root)");
    expectFormatError([], "(root)");
    const missing = validInput();
    delete missing.lakes;
    expectFormatError(missing, "(root)");
    expectFormatError({ ...validInput(), rivers: [] }, "(root)");
    expectFormatError({ ...validInput(), states: {} }, "states");
    expectFormatError({ ...validInput(), lakes: [[[1, 2]]] }, "lakes[0]");
    expectFormatError({ ...validInput(), coastline: [[[1, 2], [3]]] }, "coastline[0][1]");
    expectFormatError({ ...validInput(), countries: [[[1, 2], [181, 0]]] }, "countries[0][1]");
    expectFormatError({ ...validInput(), countries: [[[1, 2], [0, -90.5]]] }, "countries[0][1]");
    expectFormatError({ ...validInput(), states: [[[1, 2], [Number.NaN, 0]]] }, "states[0][1]");
    expectFormatError({ ...validInput(), states: [[[1, 2], ["1", 0]]] }, "states[0][1]");
    expect(() => convertBasemapText("{not json")).toThrow(BasemapFormatError);
  });
});

describe("csp", () => {
  it("builds the preview header from tauri.conf.json", () => {
    const header = cspHeaderFromTauriConfig(resolve(repoRoot, "src-tauri", "tauri.conf.json"));
    expect(header).toContain("default-src 'self'");
    expect(header).toContain("worker-src 'self' blob:");
    expect(header).toContain("connect-src 'self' ipc: http://ipc.localhost");
    expect(header.split("; ")).toHaveLength(12);
  });

  it("rejects an empty or malformed policy", () => {
    expect(() => cspHeaderFromObject({})).toThrow();
    expect(() => cspHeaderFromObject({ "default-src": "" })).toThrow();
    expect(() => cspHeaderFromObject({ "default-src": 1 })).toThrow();
  });
});
