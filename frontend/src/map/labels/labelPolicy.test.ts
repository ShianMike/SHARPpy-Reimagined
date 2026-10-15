import { describe, expect, it } from "vitest";
import { declutterCellFor, equivalentSpan, labelTierFor, longitudeSpan } from "./labelPolicy";

describe("labelTierFor", () => {
  it.each([
    ["high", 55, 16],
    ["balanced", 55, 16],
    ["low", 30, 8],
  ] as const)("%s: ids at a span of %i degrees or less, names at %i or less", (profile, ids, names) => {
    expect(labelTierFor(profile, ids + 0.01)).toBeNull();
    expect(labelTierFor(profile, ids)).toBe("id");
    expect(labelTierFor(profile, names + 0.01)).toBe("id");
    expect(labelTierFor(profile, names)).toBe("name");
    expect(labelTierFor(profile, 1)).toBe("name");
  });

  it("shows no labels for the whole CONUS view on any profile", () => {
    // CONUS_BOUNDS spans 58.5 degrees of longitude.
    for (const profile of ["high", "balanced", "low"] as const) {
      expect(labelTierFor(profile, 58.5)).toBeNull();
    }
  });

  it("shows no labels for a non-finite span", () => {
    expect(labelTierFor("high", Number.NaN)).toBeNull();
  });
});

describe("declutterCellFor", () => {
  it("uses 48, 64, and 96 px cells", () => {
    expect(declutterCellFor("high")).toBe(48);
    expect(declutterCellFor("balanced")).toBe(64);
    expect(declutterCellFor("low")).toBe(96);
  });
});

describe("longitudeSpan", () => {
  it("is east minus west, at most 360 degrees", () => {
    expect(longitudeSpan(-125, -66.5)).toBe(58.5);
    expect(longitudeSpan(-400, 400)).toBe(360);
  });
});

describe("equivalentSpan", () => {
  it("is the span the window-wide map shows at a narrow panel's zoom", () => {
    // A 480 px panel showing 20 degrees: a 1440 px map at that zoom shows 60, so no labels, not names.
    expect(equivalentSpan(20, 480, 1440)).toBe(60);
    expect(labelTierFor("balanced", equivalentSpan(20, 480, 1440))).toBeNull();
    // The window-wide map itself, and a canvas not laid out yet, keep their span.
    expect(equivalentSpan(20, 1440, 1440)).toBe(20);
    expect(equivalentSpan(20, 0, 1440)).toBe(20);
  });
});
